import uuid
import io
import os
import json
import tempfile
import requests
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Request as FastAPIRequest
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleAuthRequest
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    pkcs12,
)
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from auth import (
    ALGORITHM,
    SECRET_KEY,
    create_access_token,
    hash_password,
    verify_password,
)
from database import get_db
from dependencies import get_current_user
from member_cards import (
    BASE_PUBLIC_URL,
    build_manifest,
    clean_google_private_key,
    cover_image_to_canvas,
    create_wallet_icon,
    fit_image_to_canvas,
    get_google_wallet_service_account,
    sign_manifest,
    zip_pkpass,
)
from notification_service import safe_send_email
import models
import qrcode
import jwt as pyjwt


router = APIRouter(prefix="/doctor-prescribers", tags=["Doctor Prescribers"])
security = HTTPBearer()
COMMISSION_RATE_BPS = 3000
DOCTOR_WALLET_CLASS_SUFFIX = "doctor_prescriptor_mayu"
DOCTOR_WALLET_AUTH_PREFIX = "mayu-doctor-wallet"


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


class AppleWalletRegistrationRequest(BaseModel):
    pushToken: str


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


def phone_variants(value: Optional[str]) -> set[str]:
    digits = normalize_phone(value)
    if not digits:
        return set()

    variants = {digits}
    if digits.startswith("593") and len(digits) >= 11:
        variants.add("0" + digits[3:])
        variants.add(digits[3:])
    elif digits.startswith("0") and len(digits) >= 10:
        variants.add("593" + digits[1:])
        variants.add(digits[1:])
    elif len(digits) == 9:
        variants.add("0" + digits)
        variants.add("593" + digits)
    return {variant for variant in variants if variant}


def phones_match(left: Optional[str], right: Optional[str]) -> bool:
    return bool(phone_variants(left) & phone_variants(right))


def doctor_apple_serial(doctor: models.DoctorPrescriber) -> str:
    return f"mayu-doctor-{doctor.id}"


def doctor_wallet_auth_token(doctor: models.DoctorPrescriber) -> str:
    return f"{DOCTOR_WALLET_AUTH_PREFIX}-{doctor.qr_token}"


def doctor_apple_last_updated(doctor: models.DoctorPrescriber) -> str:
    updated_at = doctor.updated_at or doctor.created_at or datetime.utcnow()
    return updated_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def doctor_apple_last_modified(doctor: models.DoctorPrescriber) -> str:
    updated_at = doctor.updated_at or doctor.created_at or datetime.utcnow()
    return format_datetime(updated_at.replace(tzinfo=timezone.utc), usegmt=True)


def extract_wallet_auth_token(request: FastAPIRequest) -> str:
    authorization = request.headers.get("authorization") or ""
    prefix = "ApplePass "
    if authorization.startswith(prefix):
        return authorization.replace(prefix, "", 1).strip()
    return ""


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


def build_doctor_recovery_email_message(doctor: models.DoctorPrescriber) -> str:
    doctor_data = doctor_to_dict(doctor, include_transactions=False)
    name = doctor_data.get("name") or "Doctor Prescriptor Mayu"
    code = doctor_data.get("doctor_code") or ""
    commission = doctor_data.get("commission_balance") or 0
    public_url = doctor_data.get("public_card_url") or ""
    qr_url = doctor_data.get("qr_image_url") or ""
    apple_url = doctor_data.get("apple_wallet_url") or ""
    google_url = doctor_data.get("google_wallet_url") or ""
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:680px;margin:auto;padding:24px;color:#1d2525">
      <h2 style="color:#006054">Doctor Prescriptor Mayu</h2>
      <p>Hola {name},</p>
      <p>Estos son los datos para recuperar tu tarjeta Doctor Prescriptor Mayu.</p>
      <div style="background:#f4f4f1;border-radius:18px;padding:18px;margin:18px 0">
        <p><strong>Código:</strong> {code}</p>
        <p><strong>Comisión acumulada:</strong> ${float(commission):.2f}</p>
        <p><strong>Comisión:</strong> 30% médico prescriptor</p>
      </div>
      <p style="text-align:center">
        <img src="{qr_url}" alt="QR Doctor Prescriptor Mayu" style="max-width:220px;width:100%;border-radius:16px" />
      </p>
      <p><a href="{public_url}">Ver tarjeta Doctor Prescriptor</a></p>
      <p><a href="{apple_url}">Descargar Wallet iOS</a></p>
      <p><a href="{google_url}">Descargar Wallet Android</a></p>
      <p style="font-size:13px;color:#6b6b6b">Si todavía no tienes Wallet emitida, este enlace igualmente te permite recuperar tu código y QR.</p>
    </div>
    """

def copy_or_create_doctor_wallet_images(pass_dir: str):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    logo_path = os.path.join(base_dir, "assets", "logo_mayu.png")
    background_path = os.path.join(base_dir, "assets", "doctor_prescriber_card_bg.png")
    bg_color = (0, 96, 84)

    for filename, size in [
        ("icon.png", (29, 29)),
        ("icon@2x.png", (58, 58)),
    ]:
        target = os.path.join(pass_dir, filename)
        if os.path.exists(logo_path):
            fit_image_to_canvas(logo_path, target, size, bg_color)
        else:
            create_wallet_icon(target)

    for filename, size in [
        ("logo.png", (70, 26)),
        ("logo@2x.png", (140, 52)),
    ]:
        target = os.path.join(pass_dir, filename)
        if os.path.exists(logo_path):
            fit_image_to_canvas(logo_path, target, size, bg_color)
        else:
            create_wallet_icon(target)

    if os.path.exists(background_path):
        cover_image_to_canvas(
            background_path,
            os.path.join(pass_dir, "strip.png"),
            (375, 123),
            bg_color,
        )
        cover_image_to_canvas(
            background_path,
            os.path.join(pass_dir, "strip@2x.png"),
            (750, 246),
            bg_color,
        )


def build_doctor_apple_wallet_file(doctor: models.DoctorPrescriber) -> str:
    pass_type_id = os.getenv("APPLE_PASS_TYPE_ID")
    team_id = os.getenv("APPLE_TEAM_ID")
    organization_name = os.getenv("APPLE_ORGANIZATION_NAME", "Doctor Prescriptor Mayu")

    if not pass_type_id:
        raise HTTPException(status_code=500, detail="Falta APPLE_PASS_TYPE_ID")
    if not team_id:
        raise HTTPException(status_code=500, detail="Falta APPLE_TEAM_ID")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    certs_dir = os.path.join(base_dir, "certs")
    if not os.path.exists(certs_dir):
        certs_dir = os.path.join(os.getcwd(), "certs")

    temp_dir = tempfile.mkdtemp(prefix=f"mayu_doctor_pkpass_{doctor.id}_")
    pass_dir = os.path.join(temp_dir, "pass")
    os.makedirs(pass_dir, exist_ok=True)

    try:
        public_url = f"{BASE_PUBLIC_URL}/doctor-prescribers/qr/{doctor.qr_token}"
        commission_value = f"${round((doctor.commission_balance_cents or 0) / 100, 2):.2f}"
        sales_value = f"${round((doctor.total_sales_cents or 0) / 100, 2):.2f}"
        pass_json = {
            "formatVersion": 1,
            "passTypeIdentifier": pass_type_id,
            "serialNumber": doctor_apple_serial(doctor),
            "teamIdentifier": team_id,
            "organizationName": organization_name,
            "description": "Tarjeta Doctor Prescriptor Mayu",
            "logoText": "DOCTOR PRESCRIPTOR MAYU",
            "webServiceURL": f"{BASE_PUBLIC_URL}/doctor-prescribers/wallet/apple",
            "authenticationToken": doctor_wallet_auth_token(doctor),
            "foregroundColor": "rgb(255,255,255)",
            "backgroundColor": "rgb(0,96,84)",
            "labelColor": "rgb(210,245,238)",
            "suppressStripShine": True,
            "sharingProhibited": False,
            "storeCard": {
                "primaryFields": [
                    {
                        "key": "commission",
                        "label": "COMISION ACUMULADA",
                        "value": commission_value,
                    }
                ],
                "secondaryFields": [
                    {
                        "key": "name",
                        "label": "DOCTOR",
                        "value": doctor.name,
                    },
                    {
                        "key": "rate",
                        "label": "COMISION",
                        "value": "30% medico prescriptor",
                    },
                ],
                "auxiliaryFields": [
                    {
                        "key": "code",
                        "label": "CODIGO",
                        "value": doctor.doctor_code,
                    }
                ],
                "backFields": [
                    {"key": "sales", "label": "Ventas acumuladas", "value": sales_value},
                    {"key": "email", "label": "Correo", "value": doctor.email},
                    {"key": "phone", "label": "Telefono", "value": doctor.phone},
                    {"key": "web", "label": "Tarjeta web", "value": public_url},
                ],
            },
            "barcode": {
                "format": "PKBarcodeFormatQR",
                "message": public_url,
                "messageEncoding": "iso-8859-1",
                "altText": doctor.doctor_code,
            },
        }

        with open(os.path.join(pass_dir, "pass.json"), "w", encoding="utf-8") as f:
            json.dump(pass_json, f, ensure_ascii=False, separators=(",", ":"))

        copy_or_create_doctor_wallet_images(pass_dir)
        build_manifest(pass_dir)
        sign_manifest(pass_dir, certs_dir)

        output_path = os.path.join(temp_dir, f"doctor_prescriptor_mayu_{doctor.id}.pkpass")
        zip_pkpass(pass_dir, output_path)
        return output_path
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error generando Apple Wallet Doctor: {str(exc)}",
        )


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


@router.get("/wallet/apple/{qr_token}")
def doctor_apple_wallet_placeholder(qr_token: str, db: Session = Depends(get_db)):
    doctor = (
        db.query(models.DoctorPrescriber)
        .filter(models.DoctorPrescriber.qr_token == qr_token)
        .first()
    )
    if not doctor or not doctor.is_active:
        raise HTTPException(status_code=404, detail="Doctor Prescriptor no válido")
    output_path = build_doctor_apple_wallet_file(doctor)
    return FileResponse(
        path=output_path,
        media_type="application/vnd.apple.pkpass",
        filename=f"doctor_prescriptor_mayu_{doctor.id}.pkpass",
    )


def get_doctor_by_apple_serial(db: Session, serial_number: str):
    prefix = "mayu-doctor-"
    if not serial_number.startswith(prefix):
        raise HTTPException(status_code=404, detail="Pase Doctor Prescriptor no válido")
    try:
        doctor_id = int(serial_number.replace(prefix, "", 1))
    except ValueError:
        raise HTTPException(status_code=404, detail="Pase Doctor Prescriptor no válido")

    doctor = (
        db.query(models.DoctorPrescriber)
        .filter(models.DoctorPrescriber.id == doctor_id)
        .first()
    )
    if not doctor or not doctor.is_active:
        raise HTTPException(status_code=404, detail="Pase Doctor Prescriptor no válido")
    return doctor


def verify_doctor_wallet_request(request: FastAPIRequest, doctor: models.DoctorPrescriber):
    if extract_wallet_auth_token(request) != doctor_wallet_auth_token(doctor):
        raise HTTPException(status_code=401, detail="No autorizado")


def get_wallet_certs_dir():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    certs_dir = os.path.join(base_dir, "certs")
    if not os.path.exists(certs_dir):
        certs_dir = os.path.join(os.getcwd(), "certs")
    return certs_dir


def build_apple_wallet_push_cert_files(temp_dir: str):
    certs_dir = get_wallet_certs_dir()
    p12_path = os.path.join(certs_dir, "mayu_wallet.p12")
    password = os.getenv("APPLE_WALLET_P12_PASSWORD") or os.getenv(
        "APPLE_WALLET_CERT_PASSWORD"
    )
    if not os.path.exists(p12_path):
        raise Exception("No existe certs/mayu_wallet.p12")
    if not password:
        raise Exception("Falta APPLE_WALLET_P12_PASSWORD")

    with open(p12_path, "rb") as f:
        private_key, certificate, additional_certificates = pkcs12.load_key_and_certificates(
            f.read(),
            password.encode(),
        )
    if not private_key or not certificate:
        raise Exception("Certificado Apple Wallet inválido")

    cert_path = os.path.join(temp_dir, "apple_wallet_push_cert.pem")
    key_path = os.path.join(temp_dir, "apple_wallet_push_key.pem")

    with open(cert_path, "wb") as f:
        f.write(certificate.public_bytes(Encoding.PEM))
        for item in additional_certificates or []:
            f.write(item.public_bytes(Encoding.PEM))

    with open(key_path, "wb") as f:
        f.write(
            private_key.private_bytes(
                Encoding.PEM,
                PrivateFormat.PKCS8,
                NoEncryption(),
            )
        )

    return cert_path, key_path


def safe_get_doctor_apple_wallet_registration_count(db: Session, doctor_id: int):
    try:
        return (
            db.query(models.DoctorAppleWalletRegistration)
            .filter(models.DoctorAppleWalletRegistration.doctor_prescriber_id == doctor_id)
            .count()
        )
    except SQLAlchemyError as exc:
        return {"error": str(exc)}


def safe_send_doctor_apple_wallet_update_pushes(db: Session, doctor: models.DoctorPrescriber):
    pass_type_id = os.getenv("APPLE_PASS_TYPE_ID")
    if not pass_type_id:
        return {"sent": 0, "errors": [{"detail": "Falta APPLE_PASS_TYPE_ID"}]}

    registrations = (
        db.query(models.DoctorAppleWalletRegistration)
        .filter(models.DoctorAppleWalletRegistration.doctor_prescriber_id == doctor.id)
        .all()
    )
    if not registrations:
        return {"sent": 0, "errors": [], "detail": "Sin dispositivos Apple Wallet registrados"}

    try:
        import httpx

        temp_dir = tempfile.mkdtemp(prefix=f"mayu_doctor_apns_{doctor.id}_")
        cert_path, key_path = build_apple_wallet_push_cert_files(temp_dir)
        apns_host = os.getenv("APPLE_APNS_HOST", "https://api.push.apple.com")
        sent = 0
        errors = []
        with httpx.Client(http2=True, cert=(cert_path, key_path), timeout=20) as client:
            for registration in registrations:
                try:
                    response = client.post(
                        f"{apns_host}/3/device/{registration.push_token}",
                        headers={
                            "apns-topic": pass_type_id,
                            "apns-push-type": "background",
                            "apns-priority": "10",
                        },
                        json={},
                    )
                    if response.status_code in {200, 201}:
                        sent += 1
                    else:
                        errors.append(
                            {
                                "registration_id": registration.id,
                                "status_code": response.status_code,
                                "detail": response.text[:300],
                            }
                        )
                except Exception as exc:
                    errors.append(
                        {
                            "registration_id": registration.id,
                            "detail": str(exc),
                        }
                    )
        return {
            "sent": sent,
            "registered_devices": len(registrations),
            "errors": errors,
        }
    except Exception as exc:
        return {"sent": 0, "errors": [{"detail": str(exc)}]}


@router.post(
    "/wallet/apple/v1/devices/{device_library_identifier}/registrations/{pass_type_identifier}/{serial_number}"
)
def register_doctor_apple_wallet_device(
    device_library_identifier: str,
    pass_type_identifier: str,
    serial_number: str,
    payload: AppleWalletRegistrationRequest,
    request: FastAPIRequest,
    db: Session = Depends(get_db),
):
    doctor = get_doctor_by_apple_serial(db, serial_number)
    verify_doctor_wallet_request(request, doctor)

    if not payload.pushToken or not payload.pushToken.strip():
        raise HTTPException(status_code=400, detail="pushToken requerido")

    existing = (
        db.query(models.DoctorAppleWalletRegistration)
        .filter(
            models.DoctorAppleWalletRegistration.doctor_prescriber_id == doctor.id,
            models.DoctorAppleWalletRegistration.device_library_identifier
            == device_library_identifier,
            models.DoctorAppleWalletRegistration.serial_number == serial_number,
        )
        .first()
    )

    created = False
    if existing:
        existing.pass_type_identifier = pass_type_identifier
        existing.push_token = payload.pushToken.strip()
        existing.authentication_token = doctor_wallet_auth_token(doctor)
        existing.updated_at = datetime.utcnow()
    else:
        created = True
        existing = models.DoctorAppleWalletRegistration(
            doctor_prescriber_id=doctor.id,
            device_library_identifier=device_library_identifier,
            pass_type_identifier=pass_type_identifier,
            serial_number=serial_number,
            push_token=payload.pushToken.strip(),
            authentication_token=doctor_wallet_auth_token(doctor),
        )
        db.add(existing)

    db.commit()
    return Response(status_code=201 if created else 200)


@router.delete(
    "/wallet/apple/v1/devices/{device_library_identifier}/registrations/{pass_type_identifier}/{serial_number}"
)
def unregister_doctor_apple_wallet_device(
    device_library_identifier: str,
    pass_type_identifier: str,
    serial_number: str,
    request: FastAPIRequest,
    db: Session = Depends(get_db),
):
    doctor = get_doctor_by_apple_serial(db, serial_number)
    verify_doctor_wallet_request(request, doctor)
    (
        db.query(models.DoctorAppleWalletRegistration)
        .filter(
            models.DoctorAppleWalletRegistration.doctor_prescriber_id == doctor.id,
            models.DoctorAppleWalletRegistration.device_library_identifier
            == device_library_identifier,
            models.DoctorAppleWalletRegistration.pass_type_identifier
            == pass_type_identifier,
            models.DoctorAppleWalletRegistration.serial_number == serial_number,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return Response(status_code=200)


@router.get(
    "/wallet/apple/v1/devices/{device_library_identifier}/registrations/{pass_type_identifier}"
)
def get_doctor_apple_wallet_updated_serials(
    device_library_identifier: str,
    pass_type_identifier: str,
    request: FastAPIRequest,
    db: Session = Depends(get_db),
    passesUpdatedSince: Optional[str] = None,
):
    registrations = (
        db.query(models.DoctorAppleWalletRegistration)
        .filter(
            models.DoctorAppleWalletRegistration.device_library_identifier
            == device_library_identifier,
            models.DoctorAppleWalletRegistration.pass_type_identifier
            == pass_type_identifier,
        )
        .all()
    )

    if registrations:
        token = extract_wallet_auth_token(request)
        if token not in {item.authentication_token for item in registrations}:
            raise HTTPException(status_code=401, detail="No autorizado")

    updated_items = []
    for item in registrations:
        if not item.doctor:
            continue
        last_updated = doctor_apple_last_updated(item.doctor)
        if passesUpdatedSince and last_updated <= passesUpdatedSince:
            continue
        updated_items.append((item, last_updated))

    if not updated_items:
        return Response(status_code=204)

    return {
        "lastUpdated": max(last_updated for _, last_updated in updated_items),
        "serialNumbers": [item.serial_number for item, _ in updated_items],
    }


@router.get("/wallet/apple/v1/passes/{pass_type_identifier}/{serial_number}")
def get_updated_doctor_apple_wallet_pass(
    pass_type_identifier: str,
    serial_number: str,
    request: FastAPIRequest,
    db: Session = Depends(get_db),
):
    doctor = get_doctor_by_apple_serial(db, serial_number)
    verify_doctor_wallet_request(request, doctor)
    output_path = build_doctor_apple_wallet_file(doctor)
    return FileResponse(
        path=output_path,
        media_type="application/vnd.apple.pkpass",
        filename=f"doctor_prescriptor_mayu_{doctor.id}.pkpass",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Last-Modified": doctor_apple_last_modified(doctor),
        },
    )


@router.post("/wallet/apple/v1/log")
def doctor_apple_wallet_log(payload: dict):
    return {"message": "Apple Wallet log doctor recibido", "payload": payload}


@router.get("/wallet/google/{qr_token}")
def doctor_google_wallet(qr_token: str, db: Session = Depends(get_db)):
    doctor = (
        db.query(models.DoctorPrescriber)
        .filter(models.DoctorPrescriber.qr_token == qr_token)
        .first()
    )
    if not doctor or not doctor.is_active:
        raise HTTPException(status_code=404, detail="Doctor Prescriptor no válido")
    return RedirectResponse(url=build_doctor_google_wallet_save_url(doctor))


def doctor_google_class_suffix() -> str:
    return os.getenv(
        "GOOGLE_WALLET_DOCTOR_CLASS_SUFFIX",
        DOCTOR_WALLET_CLASS_SUFFIX,
    )


def doctor_google_class_id(issuer_id: str) -> str:
    return f"{issuer_id}.{doctor_google_class_suffix()}"


def doctor_google_object_id(doctor: models.DoctorPrescriber, issuer_id: Optional[str] = None) -> str:
    issuer = issuer_id or os.getenv("GOOGLE_WALLET_ISSUER_ID")
    if not issuer:
        raise HTTPException(status_code=500, detail="Falta GOOGLE_WALLET_ISSUER_ID en Render")
    object_suffix = f"doctor_prescriptor_{doctor.doctor_code}_{doctor.id}".replace("-", "_").lower()
    return f"{issuer}.{object_suffix}"


def ensure_doctor_google_wallet_class(service_account_info: dict, class_id: str):
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/wallet_object.issuer"],
    )
    credentials.refresh(GoogleAuthRequest())
    headers = {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
    }
    existing = requests.get(
        f"https://walletobjects.googleapis.com/walletobjects/v1/genericClass/{class_id}",
        headers=headers,
        timeout=20,
    )
    if existing.status_code == 200:
        return
    if existing.status_code != 404:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo verificar clase Doctor Google Wallet: {existing.text[:500]}",
        )

    class_body = {
        "id": class_id,
        "issuerName": "Mayu Doctor Prescriptor",
        "reviewStatus": "UNDER_REVIEW",
        "hexBackgroundColor": "#006054",
        "localizedIssuerName": {
            "defaultValue": {"language": "es", "value": "Mayu Doctor Prescriptor"}
        },
        "homepageUri": {
            "uri": BASE_PUBLIC_URL,
            "description": "Mayu Doctor Prescriptor",
        },
    }
    created = requests.post(
        "https://walletobjects.googleapis.com/walletobjects/v1/genericClass",
        headers=headers,
        json=class_body,
        timeout=20,
    )
    if created.status_code not in {200, 201}:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo crear clase Doctor Google Wallet: {created.text[:500]}",
        )


def build_doctor_google_wallet_object(doctor: models.DoctorPrescriber, issuer_id: str, class_id: str):
    public_url = f"{BASE_PUBLIC_URL}/doctor-prescribers/qr/{doctor.qr_token}"
    qr_image_url = f"{BASE_PUBLIC_URL}/doctor-prescribers/qr/{doctor.qr_token}/image"
    bg_url = f"{BASE_PUBLIC_URL}/doctor-prescribers/assets/doctor_prescriber_card_bg.png"
    logo_url = f"{BASE_PUBLIC_URL}/doctor-prescribers/assets/doctor_prescriber_card_bg.png"
    commission_value = f"${round((doctor.commission_balance_cents or 0) / 100, 2)}"
    sales_value = f"${round((doctor.total_sales_cents or 0) / 100, 2)}"

    return {
        "id": doctor_google_object_id(doctor, issuer_id),
        "classId": class_id,
        "state": "ACTIVE" if doctor.is_active else "INACTIVE",
        "hexBackgroundColor": "#006054",
        "logo": {
            "sourceUri": {"uri": logo_url},
            "contentDescription": {
                "defaultValue": {"language": "es", "value": "Doctor Prescriptor Mayu"}
            },
        },
        "heroImage": {
            "sourceUri": {"uri": bg_url},
            "contentDescription": {
                "defaultValue": {"language": "es", "value": "Tarjeta Doctor Prescriptor Mayu"}
            },
        },
        "imageModulesData": [
            {
                "id": "doctor_card_design",
                "mainImage": {
                    "sourceUri": {"uri": bg_url},
                    "contentDescription": {
                        "defaultValue": {"language": "es", "value": "Fondo Doctor Prescriptor Mayu"}
                    },
                },
            }
        ],
        "cardTitle": {
            "defaultValue": {"language": "es", "value": "Doctor Prescriptor Mayu"}
        },
        "header": {"defaultValue": {"language": "es", "value": doctor.name}},
        "subheader": {
            "defaultValue": {
                "language": "es",
                "value": f"{commission_value} comisión acumulada · {doctor.doctor_code}",
            }
        },
        "barcode": {
            "type": "QR_CODE",
            "value": public_url,
            "alternateText": doctor.doctor_code,
        },
        "textModulesData": [
            {"id": "commission", "header": "Ganancias pendientes", "body": commission_value},
            {"id": "sales", "header": "Ventas acumuladas", "body": sales_value},
            {"id": "rate", "header": "Comisión", "body": "30% médico prescriptor"},
            {"id": "code", "header": "Código", "body": doctor.doctor_code},
        ],
        "linksModuleData": {
            "uris": [
                {"id": "web", "uri": public_url, "description": "Ver tarjeta Doctor"},
                {"id": "qr", "uri": qr_image_url, "description": "Ver QR Doctor"},
            ]
        },
    }


def build_doctor_google_wallet_save_url(doctor: models.DoctorPrescriber) -> str:
    issuer_id = os.getenv("GOOGLE_WALLET_ISSUER_ID")
    if not issuer_id:
        raise HTTPException(status_code=500, detail="Falta GOOGLE_WALLET_ISSUER_ID en Render")

    service_account_info = get_google_wallet_service_account()
    client_email = service_account_info.get("client_email")
    private_key = service_account_info.get("private_key")
    if not client_email or not private_key:
        raise HTTPException(status_code=500, detail="JSON de Google Wallet incompleto")

    class_id = doctor_google_class_id(issuer_id)
    ensure_doctor_google_wallet_class(service_account_info, class_id)
    generic_object = build_doctor_google_wallet_object(doctor, issuer_id, class_id)
    claims = {
        "iss": client_email,
        "aud": "google",
        "typ": "savetowallet",
        "payload": {"genericObjects": [generic_object]},
    }
    token = pyjwt.encode(claims, clean_google_private_key(private_key), algorithm="RS256")
    return f"https://pay.google.com/gp/v/save/{token}"


def safe_update_doctor_google_wallet_object(doctor: models.DoctorPrescriber):
    issuer_id = os.getenv("GOOGLE_WALLET_ISSUER_ID")
    if not issuer_id:
        return {"updated": False, "detail": "Falta GOOGLE_WALLET_ISSUER_ID"}
    try:
        service_account_info = get_google_wallet_service_account()
        class_id = doctor_google_class_id(issuer_id)
        ensure_doctor_google_wallet_class(service_account_info, class_id)
        object_body = build_doctor_google_wallet_object(doctor, issuer_id, class_id)
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=["https://www.googleapis.com/auth/wallet_object.issuer"],
        )
        credentials.refresh(GoogleAuthRequest())
        response = requests.patch(
            f"https://walletobjects.googleapis.com/walletobjects/v1/genericObject/{object_body['id']}",
            headers={
                "Authorization": f"Bearer {credentials.token}",
                "Content-Type": "application/json",
            },
            json=object_body,
            timeout=20,
        )
        if response.status_code == 404:
            return {"updated": False, "detail": "El doctor aún no ha guardado la tarjeta Google Wallet"}
        if response.status_code >= 300:
            return {
                "updated": False,
                "status_code": response.status_code,
                "detail": response.text[:500],
            }
        return {"updated": True, "object_id": object_body["id"]}
    except Exception as exc:
        return {"updated": False, "detail": str(exc)}


def safe_update_doctor_wallets(db: Session, doctor: models.DoctorPrescriber):
    return {
        "google": safe_update_doctor_google_wallet_object(doctor),
        "apple": safe_send_doctor_apple_wallet_update_pushes(db, doctor),
    }


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
    phone = payload.phone.strip()
    if not email or not phone:
        raise HTTPException(status_code=400, detail="Correo y teléfono son obligatorios")

    doctor = (
        db.query(models.DoctorPrescriber)
        .filter(models.DoctorPrescriber.email == email)
        .first()
    )
    if not doctor or not doctor.is_active or not phones_match(doctor.phone, phone):
        raise HTTPException(
            status_code=404,
            detail="No encontramos una tarjeta Doctor Prescriptor con ese correo y teléfono",
        )

    access_token = create_access_token(
        {"sub": f"doctor_prescriber:{doctor.id}", "type": "doctor_prescriber"}
    )
    email_sent = safe_send_email(
        doctor.email,
        "Recupera tu Doctor Prescriptor Mayu",
        build_doctor_recovery_email_message(doctor),
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "doctor": doctor_to_dict(doctor),
        "email_sent": email_sent,
        "email": doctor.email,
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
    wallet_sync = safe_update_doctor_wallets(db, doctor)

    return {
        "created": True,
        "commission_rate_percent": doctor.commission_rate_bps / 100,
        "sale_amount": round(sale_cents / 100, 2),
        "gross_commission_earned": round(gross_commission_cents / 100, 2),
        "deduction_percent": round(deduction_bps / 100, 2),
        "deduction_amount": round(deduction_cents / 100, 2),
        "commission_earned": round(commission_cents / 100, 2),
        "wallet_sync": wallet_sync,
        "doctor": doctor_to_dict(doctor),
    }
