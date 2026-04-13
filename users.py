from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from database import SessionLocal
from auth import hash_password, verify_password, create_access_token
from dependencies import get_current_user
import models

import os
import smtplib
import ssl
import secrets
import string
from email.message import EmailMessage

router = APIRouter()


# =========================
# SCHEMAS
# =========================
class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    phone: str
    delivery_address: str
    ambassador_code: Optional[str] = None


class MembershipUpdate(BaseModel):
    level: int
    active: bool


class LoginRequest(BaseModel):
    email: str
    password: str


class ForgotPasswordRequest(BaseModel):
    email: str


class StaffCreate(BaseModel):
    name: str
    email: str
    password: str
    phone: Optional[str] = None
    role: str


class StaffPasswordResetRequest(BaseModel):
    new_password: str


class StaffStatusUpdate(BaseModel):
    is_active: bool


# =========================
# DB
# =========================
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =========================
# HELPERS
# =========================
def require_superadmin(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Acceso solo para superadmin")


def generate_temporary_password(length: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def send_reset_email(to_email: str, temporary_password: str):
    smtp_email = os.getenv("SMTP_EMAIL")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))

    if not smtp_email or not smtp_password:
        raise Exception("Faltan variables SMTP_EMAIL o SMTP_PASSWORD en el servidor")

    msg = EmailMessage()
    msg["Subject"] = "Recuperación de contraseña - Mayu Wellness Club"
    msg["From"] = smtp_email
    msg["To"] = to_email
    msg.set_content(
        f"""
Hola,

Hemos generado una contraseña temporal para tu cuenta de Mayu Wellness Club.

Tu nueva contraseña temporal es:
{temporary_password}

Te recomendamos iniciar sesión y cambiarla lo antes posible.

Equipo Mayu Wellness Club
""".strip()
    )

    context = ssl.create_default_context()

    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
        server.login(smtp_email, smtp_password)
        server.send_message(msg)


# =========================
# USUARIOS GENERALES
# =========================
@router.get("/users")
def get_users(db: Session = Depends(get_db)):
    users = db.query(models.User).all()
    return {
        "users": [
            {
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "phone": u.phone,
                "delivery_address": u.delivery_address,
                "status": u.status,
                "membership_level": u.membership_level,
                "membership_active": u.membership_active,
                "is_active": getattr(u, "is_active", True),
                "role": u.role
            }
            for u in users
        ]
    }


@router.post("/users")
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    existing_user = db.query(models.User).filter(
        models.User.email == payload.email
    ).first()

    if existing_user:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")

    ambassador = None
    cleaned_ambassador_code = None

    if payload.ambassador_code is not None and payload.ambassador_code.strip() != "":
        cleaned_ambassador_code = payload.ambassador_code.strip()

        ambassador = db.query(models.Ambassador).filter(
            models.Ambassador.ambassador_code == cleaned_ambassador_code
        ).first()

        if not ambassador:
            raise HTTPException(status_code=400, detail="Código de embajador inválido")

    new_user = models.User(
        name=payload.name,
        email=payload.email,
        password=hash_password(payload.password),
        phone=payload.phone,
        delivery_address=payload.delivery_address,
        role="member",
        is_active=True
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    if ambassador:
        referral = models.AmbassadorReferral(
            ambassador_id=ambassador.id,
            user_id=new_user.id,
            referral_code=cleaned_ambassador_code,
            status="active"
        )
        db.add(referral)
        db.commit()

    return {
        "id": new_user.id,
        "name": new_user.name,
        "email": new_user.email,
        "phone": new_user.phone,
        "delivery_address": new_user.delivery_address,
        "status": new_user.status,
        "membership_level": new_user.membership_level,
        "membership_active": new_user.membership_active,
        "is_active": new_user.is_active,
        "role": new_user.role,
        "ambassador_code": cleaned_ambassador_code
    }


@router.put("/users/{user_id}/membership")
def update_membership(
    user_id: int,
    payload: MembershipUpdate,
    db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user.membership_level = payload.level
    user.membership_active = payload.active

    db.commit()
    db.refresh(user)

    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "delivery_address": user.delivery_address,
        "status": user.status,
        "membership_level": user.membership_level,
        "membership_active": user.membership_active,
        "is_active": getattr(user, "is_active", True),
        "role": user.role
    }


# =========================
# LOGIN
# =========================
@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(
        models.User.email == payload.email
    ).first()

    if not db_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not getattr(db_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    try:
        password_ok = verify_password(payload.password, db_user.password)
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Contraseña inválida o hash dañado"
        )

    if not password_ok:
        raise HTTPException(status_code=401, detail="Contraseña incorrecta")

    token = create_access_token({
        "sub": str(db_user.id),
        "email": db_user.email
    })

    return {
        "message": "Login exitoso",
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": db_user.id,
            "name": db_user.name,
            "email": db_user.email,
            "phone": db_user.phone,
            "delivery_address": db_user.delivery_address,
            "membership_level": db_user.membership_level,
            "membership_active": db_user.membership_active,
            "is_active": getattr(db_user, "is_active", True),
            "role": db_user.role
        }
    }


# =========================
# FORGOT PASSWORD GENERAL
# =========================
@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(
        models.User.email == payload.email
    ).first()

    if not db_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not getattr(db_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    temporary_password = generate_temporary_password()
    db_user.password = hash_password(temporary_password)

    db.commit()
    db.refresh(db_user)

    try:
        send_reset_email(db_user.email, temporary_password)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo enviar el correo: {str(e)}"
        )

    return {
        "message": "Se envió una contraseña temporal al correo registrado"
    }


# =========================
# SUPERADMIN - CREAR USUARIOS INTERNOS
# =========================
@router.post("/superadmin/internal-users")
def create_staff(
    payload: StaffCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    require_superadmin(current_user)

    allowed_roles = {"admin", "supervisor", "logistics"}
    if payload.role not in allowed_roles:
        raise HTTPException(
            status_code=400,
            detail="Rol inválido. Solo se permite admin, supervisor o logistics"
        )

    existing_user = db.query(models.User).filter(
        models.User.email == payload.email
    ).first()

    if existing_user:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")

    new_staff = models.User(
        name=payload.name,
        email=payload.email,
        password=hash_password(payload.password),
        phone=payload.phone,
        delivery_address=None,
        role=payload.role,
        status="staff",
        membership_level=None,
        membership_active=False,
        is_active=True
    )

    db.add(new_staff)
    db.commit()
    db.refresh(new_staff)

    return {
        "message": "Usuario staff creado correctamente",
        "user": {
            "id": new_staff.id,
            "name": new_staff.name,
            "email": new_staff.email,
            "phone": new_staff.phone,
            "role": new_staff.role,
            "status": new_staff.status,
            "is_active": new_staff.is_active
        }
    }


# =========================
# SUPERADMIN - LISTAR USUARIOS INTERNOS
# =========================
@router.get("/superadmin/internal-users")
def list_staff(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    require_superadmin(current_user)

    staff_roles = ["superadmin", "admin", "supervisor", "logistics"]

    users = (
        db.query(models.User)
        .filter(models.User.role.in_(staff_roles))
        .order_by(models.User.created_at.desc())
        .all()
    )

    return {
        "items": [
            {
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "phone": u.phone,
                "role": u.role,
                "status": u.status,
                "is_active": getattr(u, "is_active", True),
                "created_at": u.created_at
            }
            for u in users
        ]
    }


# =========================
# SUPERADMIN - RESETEAR CLAVE USUARIO INTERNO
# =========================
@router.put("/superadmin/internal-users/{user_id}/reset-password")
def reset_staff_password(
    user_id: int,
    payload: StaffPasswordResetRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    require_superadmin(current_user)

    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role not in {"admin", "supervisor", "logistics", "superadmin"}:
        raise HTTPException(
            status_code=400,
            detail="Solo se puede resetear password de staff interno"
        )

    user.password = hash_password(payload.new_password)
    db.commit()
    db.refresh(user)

    return {
        "message": "Contraseña actualizada correctamente",
        "user_id": user.id,
        "email": user.email,
        "role": user.role
    }


# =========================
# SUPERADMIN - ACTIVAR / DESACTIVAR USUARIO INTERNO
# =========================
@router.put("/superadmin/internal-users/{user_id}/status")
def update_staff_status(
    user_id: int,
    payload: StaffStatusUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    require_superadmin(current_user)

    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role not in {"admin", "supervisor", "logistics", "superadmin"}:
        raise HTTPException(
            status_code=400,
            detail="Solo se puede activar o desactivar staff interno"
        )

    user.is_active = payload.is_active
    db.commit()
    db.refresh(user)

    return {
        "message": "Estado actualizado correctamente",
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "is_active": user.is_active
        }
    }
