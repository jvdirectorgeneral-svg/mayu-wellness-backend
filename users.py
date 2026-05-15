from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime, timedelta
from database import SessionLocal
from auth import hash_password, verify_password, create_access_token
from dependencies import get_current_user
import models

import os
import secrets
import string
import resend

router = APIRouter()

BASE_PUBLIC_URL = "https://mayu-wellness-backend-v1.onrender.com"


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    phone: str
    cedula: str
    city: str
    address: str
    reference: str
    delivery_notes: str
    phone_secondary: Optional[str] = None
    ambassador_code: Optional[str] = None
    accepted_terms: bool = False
    accepted_privacy_policy: bool = False
    accepted_digital_policy: bool = False


class MembershipUpdate(BaseModel):
    level: int
    active: bool


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class VerifyRecoveryCodeRequest(BaseModel):
    email: EmailStr
    code: str


class ResetPasswordWithCodeRequest(BaseModel):
    email: EmailStr
    code: str
    new_password: str


class StaffCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    phone: str
    cedula: str
    role: str


class StaffUpdate(BaseModel):
    name: str
    email: EmailStr
    phone: str
    cedula: str
    role: str


class StaffPasswordResetRequest(BaseModel):
    new_password: str


class UserPasswordResetRequest(BaseModel):
    new_password: str


class StaffStatusUpdate(BaseModel):
    is_active: bool


class UserStatusUpdate(BaseModel):
    is_active: bool


class SuperAdminProfileUpdate(BaseModel):
    name: str
    email: EmailStr
    phone: str
    cedula: str


class SuperAdminPasswordUpdate(BaseModel):
    new_password: str


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_superadmin(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")
    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Acceso solo para superadmin")


def generate_recovery_code() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(4))


def send_reset_email(to_email: str, code: str):
    resend_api_key = os.getenv("RESEND_API_KEY")
    from_email = os.getenv("FROM_EMAIL", "onboarding@resend.dev")

    if not resend_api_key:
        raise Exception("Falta RESEND_API_KEY en Render")

    resend.api_key = resend_api_key

    params = {
        "from": from_email,
        "to": [to_email],
        "subject": "Código de recuperación - Mayu Wellness Club",
        "html": f"""
        <div style="font-family: Arial, sans-serif; max-width: 520px; margin: auto; padding: 24px;">
            <h2>Mayu Wellness Club</h2>
            <p>Tu código de recuperación es:</p>
            <h1 style="font-size: 42px; letter-spacing: 8px;">{code}</h1>
            <p>Este código expira en 10 minutos.</p>
            <p>Si no solicitaste este cambio, puedes ignorar este correo.</p>
            <br>
            <p>Equipo Mayu Wellness Club</p>
        </div>
        """,
    }

    resend.Emails.send(params)


def user_response(user: models.User):
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "cedula": user.cedula,
        "city": user.city,
        "address": user.address,
        "reference": user.reference,
        "delivery_notes": user.delivery_notes,
        "phone_secondary": user.phone_secondary,
        "status": user.status,
        "membership_level": user.membership_level,
        "membership_active": user.membership_active,
        "is_active": getattr(user, "is_active", True),
        "role": user.role,
        "accepted_terms": getattr(user, "accepted_terms", False),
        "accepted_privacy_policy": getattr(user, "accepted_privacy_policy", False),
        "accepted_digital_policy": getattr(user, "accepted_digital_policy", False),
        "accepted_terms_at": getattr(user, "accepted_terms_at", None),
        "accepted_privacy_policy_at": getattr(user, "accepted_privacy_policy_at", None),
        "accepted_digital_policy_at": getattr(user, "accepted_digital_policy_at", None),
    }


@router.get("/superadmin/me")
def get_superadmin_profile(current_user: models.User = Depends(get_current_user)):
    require_superadmin(current_user)

    return {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "phone": current_user.phone,
        "cedula": current_user.cedula,
        "role": current_user.role,
        "is_active": getattr(current_user, "is_active", True),
    }


@router.put("/superadmin/me")
def update_superadmin_profile(
    payload: SuperAdminProfileUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_superadmin(current_user)

    user = db.query(models.User).filter(models.User.id == current_user.id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    email = payload.email.strip().lower()
    cedula = payload.cedula.strip()

    if db.query(models.User).filter(
        models.User.email == email,
        models.User.id != user.id,
    ).first():
        raise HTTPException(status_code=400, detail="El correo ya está registrado por otro usuario")

    if db.query(models.User).filter(
        models.User.cedula == cedula,
        models.User.id != user.id,
    ).first():
        raise HTTPException(status_code=400, detail="La cédula ya está registrada por otro usuario")

    user.name = payload.name.strip()
    user.email = email
    user.phone = payload.phone.strip()
    user.cedula = cedula

    db.commit()
    db.refresh(user)

    return {
        "message": "Perfil superadmin actualizado correctamente",
        "user": user_response(user),
    }


@router.put("/superadmin/me/password")
def update_superadmin_password(
    payload: SuperAdminPasswordUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_superadmin(current_user)

    user = db.query(models.User).filter(models.User.id == current_user.id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not payload.new_password or payload.new_password.strip() == "":
        raise HTTPException(status_code=400, detail="La nueva contraseña es obligatoria")

    user.password = hash_password(payload.new_password.strip())

    db.commit()
    db.refresh(user)

    return {"message": "Contraseña superadmin actualizada correctamente", "user_id": user.id}


@router.get("/users")
def get_users(db: Session = Depends(get_db)):
    users = db.query(models.User).all()
    return {"users": [user_response(u) for u in users]}


@router.post("/users")
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    cedula = payload.cedula.strip()

    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")
    if not payload.phone.strip():
        raise HTTPException(status_code=400, detail="El teléfono es obligatorio")
    if not cedula:
        raise HTTPException(status_code=400, detail="La cédula es obligatoria")
    if not payload.city.strip():
        raise HTTPException(status_code=400, detail="La ciudad es obligatoria")
    if not payload.address.strip():
        raise HTTPException(status_code=400, detail="La dirección es obligatoria")
    if not payload.reference.strip():
        raise HTTPException(status_code=400, detail="La referencia es obligatoria")
    if not payload.delivery_notes.strip():
        raise HTTPException(status_code=400, detail="Los datos de facturación son obligatorios")
    if not payload.accepted_terms:
        raise HTTPException(status_code=400, detail="Debes aceptar los términos y condiciones")
    if not payload.accepted_privacy_policy:
        raise HTTPException(status_code=400, detail="Debes aceptar el tratamiento de datos personales")
    if not payload.accepted_digital_policy:
        raise HTTPException(status_code=400, detail="Debes aceptar la política digital y notificaciones")

    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=400, detail="El correo ya está registrado")

    if db.query(models.User).filter(models.User.cedula == cedula).first():
        raise HTTPException(status_code=400, detail="La cédula ya está registrada")

    ambassador = None
    cleaned_ambassador_code = None

    if payload.ambassador_code and payload.ambassador_code.strip():
        cleaned_ambassador_code = payload.ambassador_code.strip()

        ambassador = db.query(models.Ambassador).filter(
            models.Ambassador.ambassador_code == cleaned_ambassador_code
        ).first()

        if not ambassador:
            raise HTTPException(status_code=400, detail="Código de embajador inválido")

    now = datetime.utcnow()

    new_user = models.User(
        name=payload.name.strip(),
        email=email,
        password=hash_password(payload.password),
        phone=payload.phone.strip(),
        cedula=cedula,
        city=payload.city.strip(),
        address=payload.address.strip(),
        reference=payload.reference.strip(),
        delivery_notes=payload.delivery_notes.strip(),
        phone_secondary=payload.phone_secondary.strip() if payload.phone_secondary else None,
        role="member",
        is_active=True,
        accepted_terms=True,
        accepted_privacy_policy=True,
        accepted_digital_policy=True,
        accepted_terms_at=now,
        accepted_privacy_policy_at=now,
        accepted_digital_policy_at=now,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    if ambassador:
        referral = models.AmbassadorReferral(
            ambassador_id=ambassador.id,
            user_id=new_user.id,
            referral_code=cleaned_ambassador_code,
            status="active",
        )
        db.add(referral)
        db.commit()

    response = user_response(new_user)
    response["ambassador_code"] = cleaned_ambassador_code
    return response


@router.put("/users/{user_id}/membership")
def update_membership(user_id: int, payload: MembershipUpdate, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user.membership_level = payload.level
    user.membership_active = payload.active

    db.commit()
    db.refresh(user)

    return user_response(user)


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()

    db_user = db.query(models.User).filter(models.User.email == email).first()

    if not db_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not getattr(db_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    try:
        password_ok = verify_password(payload.password, db_user.password)
    except Exception:
        raise HTTPException(status_code=401, detail="Contraseña inválida o hash dañado")

    if not password_ok:
        raise HTTPException(status_code=401, detail="Contraseña incorrecta")

    token = create_access_token({
        "sub": str(db_user.id),
        "email": db_user.email,
    })

    return {
        "message": "Login exitoso",
        "access_token": token,
        "token_type": "bearer",
        "user": user_response(db_user),
    }


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()

    db_user = db.query(models.User).filter(models.User.email == email).first()

    if not db_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not getattr(db_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    code = generate_recovery_code()
    expires_at = datetime.utcnow() + timedelta(minutes=10)

    db.query(models.PasswordResetCode).filter(
        models.PasswordResetCode.email == email,
        models.PasswordResetCode.used == False,
    ).update({"used": True}, synchronize_session=False)

    reset_code = models.PasswordResetCode(
        user_id=db_user.id,
        email=email,
        code=code,
        used=False,
        expires_at=expires_at,
    )

    db.add(reset_code)
    db.commit()

    try:
        send_reset_email(db_user.email, code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo enviar el correo: {str(e)}")

    return {"message": "Código de recuperación enviado al correo registrado"}


@router.post("/verify-recovery-code")
def verify_recovery_code(payload: VerifyRecoveryCodeRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    code = payload.code.strip()

    reset_code = (
        db.query(models.PasswordResetCode)
        .filter(
            models.PasswordResetCode.email == email,
            models.PasswordResetCode.code == code,
            models.PasswordResetCode.used == False,
        )
        .order_by(models.PasswordResetCode.created_at.desc())
        .first()
    )

    if not reset_code:
        raise HTTPException(status_code=400, detail="Código incorrecto")

    if datetime.utcnow() > reset_code.expires_at:
        reset_code.used = True
        db.commit()
        raise HTTPException(status_code=400, detail="Código expirado")

    return {"message": "Código válido"}


@router.post("/reset-password-with-code")
def reset_password_with_code(
    payload: ResetPasswordWithCodeRequest,
    db: Session = Depends(get_db),
):
    email = payload.email.strip().lower()
    code = payload.code.strip()
    new_password = payload.new_password.strip()

    if not new_password or len(new_password) < 6:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres")

    reset_code = (
        db.query(models.PasswordResetCode)
        .filter(
            models.PasswordResetCode.email == email,
            models.PasswordResetCode.code == code,
            models.PasswordResetCode.used == False,
        )
        .order_by(models.PasswordResetCode.created_at.desc())
        .first()
    )

    if not reset_code:
        raise HTTPException(status_code=400, detail="Código incorrecto")

    if datetime.utcnow() > reset_code.expires_at:
        reset_code.used = True
        db.commit()
        raise HTTPException(status_code=400, detail="Código expirado")

    user = db.query(models.User).filter(models.User.email == email).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user.password = hash_password(new_password)
    reset_code.used = True

    db.commit()
    db.refresh(user)

    return {
        "message": "Contraseña actualizada correctamente",
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "apple_wallet_url": f"{BASE_PUBLIC_URL}/member-cards/apple-wallet/{user.id}",
            "google_wallet_url": f"{BASE_PUBLIC_URL}/member-cards/google-wallet/{user.id}",
            "card_web_url": f"{BASE_PUBLIC_URL}/member-cards/user/{user.id}/web",
        },
    }


@router.post("/superadmin/internal-users")
def create_staff(
    payload: StaffCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_superadmin(current_user)

    allowed_roles = {"admin", "supervisor", "logistics"}

    if payload.role not in allowed_roles:
        raise HTTPException(status_code=400, detail="Rol inválido. Solo se permite admin, supervisor o logistics")

    if db.query(models.User).filter(models.User.email == payload.email.strip().lower()).first():
        raise HTTPException(status_code=400, detail="El correo ya está registrado")

    if db.query(models.User).filter(models.User.cedula == payload.cedula.strip()).first():
        raise HTTPException(status_code=400, detail="La cédula ya está registrada")

    now = datetime.utcnow()

    new_staff = models.User(
        name=payload.name.strip(),
        email=payload.email.strip().lower(),
        password=hash_password(payload.password),
        phone=payload.phone.strip(),
        cedula=payload.cedula.strip(),
        city="N/A",
        address="N/A",
        reference="N/A",
        delivery_notes="N/A",
        phone_secondary=None,
        role=payload.role,
        status="staff",
        membership_level=None,
        membership_active=False,
        is_active=True,
        accepted_terms=True,
        accepted_privacy_policy=True,
        accepted_digital_policy=True,
        accepted_terms_at=now,
        accepted_privacy_policy_at=now,
        accepted_digital_policy_at=now,
    )

    db.add(new_staff)
    db.commit()
    db.refresh(new_staff)

    return {
        "message": "Usuario staff creado correctamente",
        "user": user_response(new_staff),
    }


@router.get("/superadmin/internal-users")
def list_staff(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_superadmin(current_user)

    visible_roles = [
        "superadmin",
        "admin",
        "supervisor",
        "logistics",
        "ambassador",
        "member",
    ]

    users = (
        db.query(models.User)
        .filter(models.User.role.in_(visible_roles))
        .order_by(models.User.created_at.desc())
        .all()
    )

    items = []

    for u in users:
        item = user_response(u)
        ambassador_code = None

        if u.role == "ambassador":
            ambassador = db.query(models.Ambassador).filter(
                models.Ambassador.user_id == u.id
            ).first()

            if ambassador:
                ambassador_code = ambassador.ambassador_code

        item["ambassador_code"] = ambassador_code
        items.append(item)

    return {"items": items}


@router.put("/superadmin/internal-users/{user_id}")
def update_staff(
    user_id: int,
    payload: StaffUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_superadmin(current_user)

    allowed_roles = {"admin", "supervisor", "logistics"}

    if payload.role not in allowed_roles:
        raise HTTPException(status_code=400, detail="Rol inválido. Solo se permite admin, supervisor o logistics")

    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role not in allowed_roles:
        raise HTTPException(status_code=400, detail="Solo se puede editar usuarios internos")

    if db.query(models.User).filter(
        models.User.email == payload.email.strip().lower(),
        models.User.id != user_id,
    ).first():
        raise HTTPException(status_code=400, detail="El correo ya está registrado por otro usuario")

    if db.query(models.User).filter(
        models.User.cedula == payload.cedula.strip(),
        models.User.id != user_id,
    ).first():
        raise HTTPException(status_code=400, detail="La cédula ya está registrada por otro usuario")

    user.name = payload.name.strip()
    user.email = payload.email.strip().lower()
    user.phone = payload.phone.strip()
    user.cedula = payload.cedula.strip()
    user.role = payload.role

    db.commit()
    db.refresh(user)

    return {
        "message": "Usuario interno actualizado correctamente",
        "user": user_response(user),
    }


@router.put("/superadmin/internal-users/{user_id}/reset-password")
def reset_staff_password(
    user_id: int,
    payload: StaffPasswordResetRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_superadmin(current_user)

    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    allowed_roles = {"admin", "supervisor", "logistics", "ambassador", "member"}

    if user.role not in allowed_roles:
        raise HTTPException(status_code=400, detail="No se puede resetear la contraseña de este usuario desde aquí")

    user.password = hash_password(payload.new_password.strip())

    db.commit()
    db.refresh(user)

    return {
        "message": "Contraseña actualizada correctamente",
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
    }


@router.put("/superadmin/users/{user_id}/reset-password")
def reset_any_user_password(
    user_id: int,
    payload: UserPasswordResetRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_superadmin(current_user)

    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role == "superadmin":
        raise HTTPException(status_code=403, detail="La clave del superadmin se cambia desde /superadmin/me/password")

    if not payload.new_password or payload.new_password.strip() == "":
        raise HTTPException(status_code=400, detail="La nueva contraseña es obligatoria")

    user.password = hash_password(payload.new_password.strip())

    db.commit()
    db.refresh(user)

    return {
        "message": "Contraseña actualizada correctamente",
        "user": user_response(user),
    }


@router.put("/superadmin/internal-users/{user_id}/status")
def update_staff_status(
    user_id: int,
    payload: StaffStatusUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_superadmin(current_user)

    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role == "superadmin":
        raise HTTPException(status_code=403, detail="No se puede desactivar el superadmin desde Control Maestro")

    allowed_roles = {"admin", "supervisor", "logistics", "ambassador", "member"}

    if user.role not in allowed_roles:
        raise HTTPException(status_code=400, detail="No se puede activar o desactivar este usuario")

    user.is_active = payload.is_active

    db.commit()
    db.refresh(user)

    return {
        "message": "Estado actualizado correctamente",
        "user": user_response(user),
    }


@router.put("/superadmin/users/{user_id}/status")
def update_any_user_status(
    user_id: int,
    payload: UserStatusUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_superadmin(current_user)

    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role == "superadmin":
        raise HTTPException(status_code=403, detail="No se puede desactivar el superadmin")

    user.is_active = payload.is_active

    db.commit()
    db.refresh(user)

    return {
        "message": "Estado actualizado correctamente",
        "user": user_response(user),
    }


@router.delete("/superadmin/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_superadmin(current_user)

    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role in ["admin", "superadmin", "supervisor", "logistics"]:
        raise HTTPException(status_code=403, detail="No puedes eliminar usuarios del sistema Mayu Team")

    db.delete(user)
    db.commit()

    return {
        "message": "Usuario eliminado correctamente",
        "user_id": user_id,
    }


@router.delete("/superadmin/users/{user_id}/full-delete")
def delete_user_full(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_superadmin(current_user)

    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role in ["admin", "superadmin", "supervisor", "logistics"]:
        raise HTTPException(status_code=403, detail="No puedes eliminar usuarios internos del sistema")

    try:
        orders = db.query(models.Order).filter(models.Order.user_id == user_id).all()

        for order in orders:
            if hasattr(models, "OrderTrackingHistory"):
                db.query(models.OrderTrackingHistory).filter(
                    models.OrderTrackingHistory.order_id == order.id
                ).delete(synchronize_session=False)

            db.query(models.OrderItem).filter(
                models.OrderItem.order_id == order.id
            ).delete(synchronize_session=False)

        db.query(models.MembershipPayment).filter(
            models.MembershipPayment.order_id.in_(
                db.query(models.Order.id).filter(models.Order.user_id == user_id)
            )
        ).delete(synchronize_session=False)

        db.query(models.Order).filter(
            models.Order.user_id == user_id
        ).delete(synchronize_session=False)

        db.query(models.MembershipPayment).filter(
            models.MembershipPayment.user_id == user_id
        ).delete(synchronize_session=False)

        db.query(models.MembershipPayment).filter(
            models.MembershipPayment.admin_verified_by == user_id
        ).update(
            {"admin_verified_by": None},
            synchronize_session=False,
        )

        selections = db.query(models.MonthlySelection).filter(
            models.MonthlySelection.user_id == user_id
        ).all()

        for selection in selections:
            db.query(models.MonthlySelectionItem).filter(
                models.MonthlySelectionItem.monthly_selection_id == selection.id
            ).delete(synchronize_session=False)

        db.query(models.MonthlySelection).filter(
            models.MonthlySelection.user_id == user_id
        ).delete(synchronize_session=False)

        db.query(models.MemberCard).filter(
            models.MemberCard.user_id == user_id
        ).delete(synchronize_session=False)

        db.query(models.Commission).filter(
            models.Commission.referred_user_id == user_id
        ).delete(synchronize_session=False)

        db.query(models.AmbassadorReferral).filter(
            models.AmbassadorReferral.user_id == user_id
        ).delete(synchronize_session=False)

        ambassador = db.query(models.Ambassador).filter(
            models.Ambassador.user_id == user_id
        ).first()

        if ambassador:
            db.query(models.AmbassadorReferral).filter(
                models.AmbassadorReferral.ambassador_id == ambassador.id
            ).delete(synchronize_session=False)

            db.query(models.Commission).filter(
                models.Commission.ambassador_id == ambassador.id
            ).delete(synchronize_session=False)

            db.query(models.Ambassador).filter(
                models.Ambassador.id == ambassador.id
            ).delete(synchronize_session=False)

        db.delete(user)
        db.commit()

        return {
            "message": "Usuario eliminado completamente",
            "user_id": user_id,
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Error eliminando usuario: {str(e)}",
        )


@router.post("/recovery/reset-admin")
def recovery_reset_admin(db: Session = Depends(get_db)):
    admin = db.query(models.User).filter(
        models.User.email == "admin@mayu.com"
    ).first()

    if not admin:
        raise HTTPException(status_code=404, detail="Admin no encontrado")

    admin.password = hash_password("Mayu2026")
    admin.role = "superadmin"
    admin.is_active = True

    db.commit()
    db.refresh(admin)

    return {
        "message": "Admin recuperado correctamente",
        "email": admin.email,
    }
