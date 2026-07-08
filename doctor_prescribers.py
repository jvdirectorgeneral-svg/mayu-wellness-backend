import uuid
import io
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import (
    ALGORITHM,
    SECRET_KEY,
    create_access_token,
    hash_password,
    verify_password,
)
from database import get_db
from dependencies import get_current_user
from member_cards import BASE_PUBLIC_URL
import models
import qrcode


router = APIRouter(prefix="/doctor-prescribers", tags=["Doctor Prescribers"])
security = HTTPBearer()
COMMISSION_RATE_BPS = 3000


class DoctorRegisterRequest(BaseModel):
    name: str
    email: str
    phone: str
    password: Optional[str] = None
    birth_date: Optional[str] = None
    cedula: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account_type: str
    bank_account_number: str
    bank_account_number_confirm: str
    accepted_terms: bool = True
    accepted_privacy_policy: bool = True
    accepted_digital_policy: bool = True


class DoctorLoginRequest(BaseModel):
    email: str
    password: str


class DoctorRecoverRequest(BaseModel):
    email: str
    phone: str


class DoctorSaleCreditRequest(BaseModel):
    amount: float
    reference: str
    note: Optional[str] = None
    deduction_percent: Optional[float] = 0


class DoctorPayoutRequest(BaseModel):
    note: Optional[str] = None


def require_pharmacy_admin(user: models.User):
    if user.role not in {"superadmin", "admin", "pharmacy_admin"}:
        raise HTTPException(
            status_code=403,
            detail="Solo Farmacia Administrador puede cargar ventas de doctor",
        )


def parse_birth_date(value: Optional[str]):
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    raise HTTPException(
        status_code=400,
        detail="Fecha de nacimiento inválida. Usa YYYY-MM-DD",
    )


def _money_to_cents(amount: float) -> int:
    cents = int(round(float(amount) * 100))
    if cents <= 0:
        raise HTTPException(status_code=400, detail="El monto debe ser mayor a cero")
    return cents


def _percent_to_bps(value: Optional[float]) -> int:
    percent = float(value or 0)
    if percent < 0:
        raise HTTPException(status_code=400, detail="El descuento no puede ser negativo")
    if percent > 100:
        raise HTTPException(status_code=400, detail="El descuento no puede superar 100%")
    return int(round(percent * 100))


def normalize_phone(value: Optional[str]) -> str:
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isdigit())


def _next_doctor_code(db: Session) -> str:
    last = (
        db.query(models.DoctorPrescriber)
        .order_by(models.DoctorPrescriber.id.desc())
        .first()
    )
    next_number = (last.id + 1) if last else 1
    return f"DOC-MAYU-{next_number:06d}"


def _find_doctor(db: Session, identifier: str):
    cleaned = identifier.strip()
    return (
        db.query(models.DoctorPrescriber)
        .filter(
            (models.DoctorPrescriber.doctor_code == cleaned)
            | (models.DoctorPrescriber.qr_token == cleaned)
            | (models.DoctorPrescriber.email == cleaned.lower())
        )
        .first()
    )


def doctor_to_dict(doctor: models.DoctorPrescriber, include_transactions: bool = True):
    public_card_url = f"{BASE_PUBLIC_URL}/doctor-prescribers/qr/{doctor.qr_token}"
    pending_commission_cents = sum(
        tx.commission_cents
        for tx in (doctor.transactions or [])
        if getattr(tx, "payout_status", "pending") != "paid"
    )
    paid_commission_cents = sum(
        tx.commission_cents
        for tx in (doctor.transactions or [])
        if getattr(tx, "payout_status", "pending") == "paid"
    )
    data = {
        "id": doctor.id,
        "name": doctor.name,
        "email": doctor.email,
        "phone": doctor.phone,
        "cedula": doctor.cedula,
        "birth_date": doctor.birth_date,
        "city": doctor.city,
        "address": doctor.address,
        "bank_name": doctor.bank_name,
        "bank_account_type": doctor.bank_account_type,
        "bank_account_last4": (doctor.bank_account_number or "")[-4:],
        "doctor_code": doctor.doctor_code,
        "qr_token": doctor.qr_token,
        "public_card_url": public_card_url,
        "qr_image_url": f"{BASE_PUBLIC_URL}/doctor-prescribers/qr/{doctor.qr_token}/image",
        "card_background_url": f"{BASE_PUBLIC_URL}/doctor-prescribers/assets/doctor_prescriber_card_bg.png",
        "apple_wallet_url": f"{BASE_PUBLIC_URL}/doctor-prescribers/wallet/apple/{doctor.qr_token}",
        "google_wallet_url": f"{BASE_PUBLIC_URL}/doctor-prescribers/wallet/google/{doctor.qr_token}",
        "commission_rate_percent": doctor.commission_rate_bps / 100,
        "total_sales_cents": doctor.total_sales_cents,
        "commission_balance_cents": doctor.commission_balance_cents,
        "lifetime_commission_cents": doctor.lifetime_commission_cents,
        "commission_balance": round(doctor.commission_balance_cents / 100, 2),
        "lifetime_commission": round(doctor.lifetime_commission_cents / 100, 2),
        "total_sales": round(doctor.total_sales_cents / 100, 2),
        "pending_commission_cents": pending_commission_cents,
        "paid_commission_cents": paid_commission_cents,
        "pending_commission": round(pending_commission_cents / 100, 2),
        "paid_commission": round(paid_commission_cents / 100, 2),
        "is_active": doctor.is_active,
        "created_at": doctor.created_at,
        "updated_at": doctor.updated_at,
    }
    if include_transactions:
        data["transactions"] = [
            {
                "id": tx.id,
                "sale_amount_cents": tx.sale_amount_cents,
                "sale_amount": round(tx.sale_amount_cents / 100, 2),
                "gross_commission_cents": getattr(tx, "gross_commission_cents", None) or tx.commission_cents,
                "gross_commission": round(((getattr(tx, "gross_commission_cents", None) or tx.commission_cents) / 100), 2),
                "deduction_bps": getattr(tx, "deduction_bps", 0),
                "deduction_percent": round((getattr(tx, "deduction_bps", 0) or 0) / 100, 2),
                "deduction_cents": getattr(tx, "deduction_cents", 0),
                "deduction": round((getattr(tx, "deduction_cents", 0) or 0) / 100, 2),
                "commission_cents": tx.commission_cents,
                "commission": round(tx.commission_cents / 100, 2),
                "commission_rate_percent": tx.commission_rate_bps / 100,
                "source": tx.source,
                "reference": tx.reference,
                "note": tx.note,
                "payout_status": getattr(tx, "payout_status", "pending"),
                "paid_at": getattr(tx, "paid_at", None),
                "payout_note": getattr(tx, "payout_note", None),
                "created_at": tx.created_at,
            }
            for tx in (doctor.transactions or [])[:20]
        ]
    return data


@router.get("/assets/doctor_prescriber_card_bg.png")
def get_doctor_prescriber_card_background():
    base_dir = os.path.dirname(__file__)
    file_path = os.path.join(base_dir, "assets", "doctor_prescriber_card_bg.png")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="No existe fondo Doctor Prescriptor")
    return FileResponse(file_path, media_type="image/png")


@router.get("/qr/{qr_token}/image")
def public_doctor_qr_image(qr_token: str, db: Session = Depends(get_db)):
    doctor = (
        db.query(models.DoctorPrescriber)
        .filter(models.DoctorPrescriber.qr_token == qr_token)
        .first()
    )
    if not doctor or not doctor.is_active:
        raise HTTPException(status_code=404, detail="Doctor Prescriptor no válido")

    url = f"{BASE_PUBLIC_URL}/doctor-prescribers/qr/{doctor.qr_token}"
    image = qrcode.make(url)
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return StreamingResponse(output, media_type="image/png")


@router.get("/qr/{qr_token}")
def public_doctor_card(qr_token: str, db: Session = Depends(get_db)):
    doctor = (
        db.query(models.DoctorPrescriber)
        .filter(models.DoctorPrescriber.qr_token == qr_token)
        .first()
    )
    if not doctor or not doctor.is_active:
        raise HTTPException(status_code=404, detail="Doctor Prescriptor no válido")
    return {
        "doctor": doctor_to_dict(doctor, include_transactions=False),
        "message": "Doctor Prescriptor Mayu válido",
    }


@router.get("/wallet/apple/{qr_token}", response_class=HTMLResponse)
def doctor_apple_wallet_placeholder(qr_token: str, db: Session = Depends(get_db)):
    doctor = (
        db.query(models.DoctorPrescriber)
        .filter(models.DoctorPrescriber.qr_token == qr_token)
        .first()
    )
    if not doctor or not doctor.is_active:
        raise HTTPException(status_code=404, detail="Doctor Prescriptor no válido")
    return """
    <html>
      <body style="font-family:Arial;background:#f4f4f1;padding:24px">
        <div style="max-width:520px;margin:auto;background:white;padding:28px;border-radius:24px">
          <h2>Apple Wallet Doctor Prescriptor Mayu</h2>
          <p>La tarjeta Doctor Prescriptor ya está activa. La generación Wallet real se conectará en el siguiente bloque, separada de Mayu Wellness Club y Mayu Magistral.</p>
        </div>
      </body>
    </html>
    """


@router.get("/wallet/google/{qr_token}", response_class=HTMLResponse)
def doctor_google_wallet_placeholder(qr_token: str, db: Session = Depends(get_db)):
    doctor = (
        db.query(models.DoctorPrescriber)
        .filter(models.DoctorPrescriber.qr_token == qr_token)
        .first()
    )
    if not doctor or not doctor.is_active:
        raise HTTPException(status_code=404, detail="Doctor Prescriptor no válido")
    return """
    <html>
      <body style="font-family:Arial;background:#f4f4f1;padding:24px">
        <div style="max-width:520px;margin:auto;background:white;padding:28px;border-radius:24px">
          <h2>Google Wallet Doctor Prescriptor Mayu</h2>
          <p>La tarjeta Doctor Prescriptor ya está activa. La conexión Google Wallet real se hará en el siguiente bloque, separada de las tarjetas existentes.</p>
        </div>
      </body>
    </html>
    """


def get_current_doctor(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    try:
        payload = jwt.decode(
            credentials.credentials,
            SECRET_KEY,
            algorithms=[ALGORITHM],
        )
        token_type = payload.get("type")
        subject = payload.get("sub")
        prefix = "doctor_prescriber:"
        if token_type != "doctor_prescriber" or not subject:
            raise HTTPException(status_code=401, detail="Token doctor inválido")
        doctor_id = int(str(subject).replace(prefix, ""))
    except (JWTError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Token doctor inválido")

    doctor = db.query(models.DoctorPrescriber).filter_by(id=doctor_id).first()
    if not doctor or not doctor.is_active:
        raise HTTPException(status_code=404, detail="Doctor prescriptor no válido")
    return doctor


@router.post("/register")
def register_doctor_prescriber(
    payload: DoctorRegisterRequest,
    db: Session = Depends(get_db),
):
    email = payload.email.strip().lower()
    cedula = payload.cedula.strip() if payload.cedula else None
    account = payload.bank_account_number.strip().replace(" ", "")
    account_confirm = payload.bank_account_number_confirm.strip().replace(" ", "")

    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")
    if not payload.phone.strip():
        raise HTTPException(status_code=400, detail="El teléfono es obligatorio")
    if not email:
        raise HTTPException(status_code=400, detail="El correo es obligatorio")
    if not payload.bank_account_type.strip():
        raise HTTPException(status_code=400, detail="El tipo de cuenta es obligatorio")
    if not account:
        raise HTTPException(status_code=400, detail="El número de cuenta es obligatorio")
    if account != account_confirm:
        raise HTTPException(status_code=400, detail="El número de cuenta no coincide")
    if not payload.accepted_terms or not payload.accepted_privacy_policy:
        raise HTTPException(status_code=400, detail="Debes aceptar términos y privacidad")

    if db.query(models.DoctorPrescriber).filter_by(email=email).first():
        raise HTTPException(status_code=400, detail="Ese correo ya está registrado")
    if cedula and db.query(models.DoctorPrescriber).filter_by(cedula=cedula).first():
        raise HTTPException(status_code=400, detail="Esa cédula ya está registrada")

    password = payload.password or f"doctor-{email}-{uuid.uuid4().hex[:10]}"
    doctor = models.DoctorPrescriber(
        name=payload.name.strip(),
        email=email,
        password=hash_password(password.strip()),
        phone=payload.phone.strip(),
        cedula=cedula,
        birth_date=parse_birth_date(payload.birth_date),
        city=payload.city.strip() if payload.city else None,
        address=payload.address.strip() if payload.address else None,
        bank_name=payload.bank_name.strip() if payload.bank_name else None,
        bank_account_type=payload.bank_account_type.strip(),
        bank_account_number=account,
        doctor_code=_next_doctor_code(db),
        qr_token=str(uuid.uuid4()),
        commission_rate_bps=COMMISSION_RATE_BPS,
        is_active=True,
    )
    db.add(doctor)
    db.commit()
    db.refresh(doctor)

    access_token = create_access_token(
        {"sub": f"doctor_prescriber:{doctor.id}", "type": "doctor_prescriber"}
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "doctor": doctor_to_dict(doctor),
    }


@router.post("/login")
def login_doctor_prescriber(
    payload: DoctorLoginRequest,
    db: Session = Depends(get_db),
):
    doctor = (
        db.query(models.DoctorPrescriber)
        .filter(models.DoctorPrescriber.email == payload.email.strip().lower())
        .first()
    )
    if not doctor or not verify_password(payload.password, doctor.password):
        raise HTTPException(status_code=401, detail="Credenciales Doctor inválidas")
    if not doctor.is_active:
        raise HTTPException(status_code=403, detail="Doctor prescriptor desactivado")
    access_token = create_access_token(
        {"sub": f"doctor_prescriber:{doctor.id}", "type": "doctor_prescriber"}
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "doctor": doctor_to_dict(doctor),
    }


@router.post("/recover-card")
def recover_doctor_card(
    payload: DoctorRecoverRequest,
    db: Session = Depends(get_db),
):
    email = payload.email.strip().lower()
    phone = normalize_phone(payload.phone)
    if not email or not phone:
        raise HTTPException(status_code=400, detail="Correo y teléfono son obligatorios")

    doctor = (
        db.query(models.DoctorPrescriber)
        .filter(models.DoctorPrescriber.email == email)
        .first()
    )
    if not doctor or not doctor.is_active or normalize_phone(doctor.phone) != phone:
        raise HTTPException(
            status_code=404,
            detail="No encontramos una tarjeta Doctor Prescriptor con ese correo y teléfono",
        )

    access_token = create_access_token(
        {"sub": f"doctor_prescriber:{doctor.id}", "type": "doctor_prescriber"}
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "doctor": doctor_to_dict(doctor),
        "message": "Tarjeta Doctor Prescriptor recuperada",
    }


@router.get("/me")
def get_my_doctor_card(
    current_doctor: models.DoctorPrescriber = Depends(get_current_doctor),
):
    return doctor_to_dict(current_doctor)


@router.get("/resolve/{identifier}")
def resolve_doctor_prescriber(
    identifier: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)
    doctor = _find_doctor(db, identifier)
    if not doctor or not doctor.is_active:
        raise HTTPException(status_code=404, detail="Doctor Prescriptor no válido")
    return {"doctor": doctor_to_dict(doctor, include_transactions=False)}


@router.get("/admin/doctors")
def list_doctor_prescribers(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)
    doctors = (
        db.query(models.DoctorPrescriber)
        .order_by(models.DoctorPrescriber.created_at.desc())
        .all()
    )
    return [doctor_to_dict(doctor, include_transactions=False) for doctor in doctors]


@router.get("/admin/transactions")
def list_doctor_commission_transactions(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)
    transactions = (
        db.query(models.DoctorCommissionTransaction)
        .order_by(models.DoctorCommissionTransaction.created_at.desc())
        .limit(300)
        .all()
    )
    return [
        {
            "id": tx.id,
            "doctor_prescriber_id": tx.doctor_prescriber_id,
            "doctor_name": tx.doctor.name if tx.doctor else None,
            "doctor_code": tx.doctor.doctor_code if tx.doctor else None,
            "bank_name": tx.doctor.bank_name if tx.doctor else None,
            "bank_account_type": tx.doctor.bank_account_type if tx.doctor else None,
            "bank_account_last4": (tx.doctor.bank_account_number or "")[-4:] if tx.doctor else None,
            "sale_amount_cents": tx.sale_amount_cents,
            "sale_amount": round(tx.sale_amount_cents / 100, 2),
            "gross_commission_cents": getattr(tx, "gross_commission_cents", None) or tx.commission_cents,
            "gross_commission": round(((getattr(tx, "gross_commission_cents", None) or tx.commission_cents) / 100), 2),
            "deduction_bps": getattr(tx, "deduction_bps", 0),
            "deduction_percent": round((getattr(tx, "deduction_bps", 0) or 0) / 100, 2),
            "deduction_cents": getattr(tx, "deduction_cents", 0),
            "deduction": round((getattr(tx, "deduction_cents", 0) or 0) / 100, 2),
            "commission_cents": tx.commission_cents,
            "commission": round(tx.commission_cents / 100, 2),
            "source": tx.source,
            "reference": tx.reference,
            "note": tx.note,
            "payout_status": getattr(tx, "payout_status", "pending"),
            "paid_at": getattr(tx, "paid_at", None),
            "payout_note": getattr(tx, "payout_note", None),
            "created_at": tx.created_at,
        }
        for tx in transactions
    ]


@router.post("/admin/transactions/{transaction_id}/mark-paid")
def mark_doctor_commission_paid(
    transaction_id: int,
    payload: DoctorPayoutRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)
    tx = (
        db.query(models.DoctorCommissionTransaction)
        .filter(models.DoctorCommissionTransaction.id == transaction_id)
        .first()
    )
    if not tx:
        raise HTTPException(status_code=404, detail="Comisión doctor no encontrada")
    if getattr(tx, "payout_status", "pending") == "paid":
        return {"paid": True, "message": "Esta comisión ya estaba pagada"}

    tx.payout_status = "paid"
    tx.paid_at = datetime.utcnow()
    tx.paid_by = current_user.id
    tx.payout_note = payload.note.strip() if payload.note else None
    if tx.doctor:
        tx.doctor.commission_balance_cents = max(
            0,
            tx.doctor.commission_balance_cents - tx.commission_cents,
        )
    db.commit()
    db.refresh(tx)
    return {
        "paid": True,
        "message": "OK pagado a Doctor Prescriptor",
        "transaction": {
            "id": tx.id,
            "payout_status": tx.payout_status,
            "paid_at": tx.paid_at,
            "commission": round(tx.commission_cents / 100, 2),
        },
    }


@router.post("/admin/credit/{identifier}")
def credit_doctor_sale(
    identifier: str,
    payload: DoctorSaleCreditRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)
    doctor = _find_doctor(db, identifier)
    if not doctor or not doctor.is_active:
        raise HTTPException(status_code=404, detail="Doctor Prescriptor no válido")

    reference = payload.reference.strip()
    if not reference:
        raise HTTPException(status_code=400, detail="La factura es obligatoria")
    if (
        db.query(models.DoctorCommissionTransaction)
        .filter_by(reference=reference)
        .first()
    ):
        raise HTTPException(status_code=400, detail="Esa factura ya fue registrada")

    sale_cents = _money_to_cents(payload.amount)
    gross_commission_cents = int(round(sale_cents * doctor.commission_rate_bps / 10000))
    deduction_bps = _percent_to_bps(payload.deduction_percent)
    deduction_cents = int(round(gross_commission_cents * deduction_bps / 10000))
    commission_cents = max(0, gross_commission_cents - deduction_cents)
    transaction = models.DoctorCommissionTransaction(
        doctor_prescriber_id=doctor.id,
        sale_amount_cents=sale_cents,
        gross_commission_cents=gross_commission_cents,
        deduction_bps=deduction_bps,
        deduction_cents=deduction_cents,
        commission_cents=commission_cents,
        commission_rate_bps=doctor.commission_rate_bps,
        source="pharmacy_admin",
        reference=reference,
        note=payload.note.strip() if payload.note else None,
        created_by=current_user.id,
    )
    doctor.total_sales_cents += sale_cents
    doctor.commission_balance_cents += commission_cents
    doctor.lifetime_commission_cents += commission_cents
    db.add(transaction)
    db.commit()
    db.refresh(doctor)
    db.refresh(transaction)

    return {
        "created": True,
        "commission_rate_percent": doctor.commission_rate_bps / 100,
        "sale_amount": round(sale_cents / 100, 2),
        "gross_commission_earned": round(gross_commission_cents / 100, 2),
        "deduction_percent": round(deduction_bps / 100, 2),
        "deduction_amount": round(deduction_cents / 100, 2),
        "commission_earned": round(commission_cents / 100, 2),
        "doctor": doctor_to_dict(doctor),
    }
