from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
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


class MembershipUpdate(BaseModel):
    level: int
    active: bool


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


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
    return "".join(secrets.choice(alphabet) for _ in range(length))


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
                "cedula": u.cedula,
                "city": u.city,
                "address": u.address,
                "reference": u.reference,
                "delivery_notes": u.delivery_notes,
                "phone_secondary": u.phone_secondary,
                "status": u.status,
                "membership_level": u.membership_level,
                "membership_active": u.membership_active,
                "is_active": getattr(u, "is_active", True),
                "role": u.role,
            }
            for u in users
        ]
    }


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
        raise HTTPException(status_code=400, detail="Las notas de entrega son obligatorias")

    existing_user = db.query(models.User).filter(
        models.User.email == email
    ).first()

    if existing_user:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")

    existing_cedula = db.query(models.User).filter(
        models.User.cedula == cedula
    ).first()

    if existing_cedula:
        raise HTTPException(status_code=400, detail="La cédula ya está registrada")

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

    return {
        "id": new_user.id,
        "name": new_user.name,
        "email": new_user.email,
        "phone": new_user.phone,
        "cedula": new_user.cedula,
        "city": new_user.city,
        "address": new_user.address,
        "reference": new_user.reference,
        "delivery_notes": new_user.delivery_notes,
        "phone_secondary": new_user.phone_secondary,
        "status": new_user.status,
        "membership_level": new_user.membership_level,
        "membership_active": new_user.membership_active,
        "is_active": new_user.is_active,
        "role": new_user.role,
        "ambassador_code": cleaned_ambassador_code,
    }


@router.put("/users/{user_id}/membership")
def update_membership(
    user_id: int,
    payload: MembershipUpdate,
    db: Session = Depends(get_db),
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
    }


# =========================
# LOGIN
# =========================
@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()

    db_user = db.query(models.User).filter(
        models.User.email == email
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
            detail="Contraseña inválida o hash dañado",
        )

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
        "user": {
            "id": db_user.id,
            "name": db_user.name,
            "email": db_user.email,
            "phone": db_user.phone,
            "cedula": db_user.cedula,
            "city": db_user.city,
            "address": db_user.address,
            "reference": db_user.reference,
            "delivery_notes": db_user.delivery_notes,
            "phone_secondary": db_user.phone_secondary,
            "membership_level": db_user.membership_level,
            "membership_active": db_user.membership_active,
            "is_active": getattr(db_user, "is_active", True),
            "role": db_user.role,
        },
    }


# =========================
# FORGOT PASSWORD GENERAL
# =========================
@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()

    db_user = db.query(models.User).filter(
        models.User.email == email
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
            detail=f"No se pudo enviar el correo: {str(e)}",
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
    current_user: models.User = Depends(get_current_user),
):
    require_superadmin(current_user)

    allowed_roles = {"admin", "supervisor", "logistics"}
    if payload.role not in allowed_roles:
        raise HTTPException(
            status_code=400,
            detail="Rol inválido. Solo se permite admin, supervisor o logistics",
        )

    existing_user = db.query(models.User).filter(
        models.User.email == payload.email.strip().lower()
    ).first()

    if existing_user:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")

    existing_cedula = db.query(models.User).filter(
        models.User.cedula == payload.cedula.strip()
    ).first()

    if existing_cedula:
        raise HTTPException(status_code=400, detail="La cédula ya está registrada")

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
            "cedula": new_staff.cedula,
            "role": new_staff.role,
            "status": new_staff.status,
            "is_active": new_staff.is_active,
        },
    }


# =========================
# SUPERADMIN - LISTAR USUARIOS INTERNOS
# =========================
@router.get("/superadmin/internal-users")
def list_staff(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
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
                "cedula": u.cedula,
                "role": u.role,
                "status": u.status,
                "is_active": getattr(u, "is_active", True),
                "created_at": u.created_at,
            }
            for u in users
        ]
    }


# =========================
# SUPERADMIN - ACTUALIZAR USUARIO INTERNO
# =========================
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
        raise HTTPException(
            status_code=400,
            detail="Rol inválido. Solo se permite admin, supervisor o logistics",
        )

    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role not in {"admin", "supervisor", "logistics", "superadmin"}:
        raise HTTPException(
            status_code=400,
            detail="Solo se puede editar usuarios internos",
        )

    existing_user = db.query(models.User).filter(
        models.User.email == payload.email.strip().lower(),
        models.User.id != user_id,
    ).first()

    if existing_user:
        raise HTTPException(status_code=400, detail="El correo ya está registrado por otro usuario")

    existing_cedula = db.query(models.User).filter(
        models.User.cedula == payload.cedula.strip(),
        models.User.id != user_id,
    ).first()

    if existing_cedula:
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
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "phone": user.phone,
            "cedula": user.cedula,
            "role": user.role,
            "status": user.status,
            "is_active": getattr(user, "is_active", True),
        },
    }


# =========================
# SUPERADMIN - RESETEAR CLAVE USUARIO INTERNO
# =========================
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

    if user.role not in {"admin", "supervisor", "logistics", "superadmin"}:
        raise HTTPException(
            status_code=400,
            detail="Solo se puede resetear password de staff interno",
        )

    user.password = hash_password(payload.new_password)
    db.commit()
    db.refresh(user)

    return {
        "message": "Contraseña actualizada correctamente",
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
    }


# =========================
# SUPERADMIN - RESETEAR CLAVE DE CUALQUIER USUARIO
# =========================
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

    if not payload.new_password or payload.new_password.strip() == "":
        raise HTTPException(status_code=400, detail="La nueva contraseña es obligatoria")

    user.password = hash_password(payload.new_password.strip())
    db.commit()
    db.refresh(user)

    return {
        "message": "Contraseña actualizada correctamente",
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "is_active": getattr(user, "is_active", True),
        },
    }


# =========================
# SUPERADMIN - ACTIVAR / DESACTIVAR USUARIO INTERNO
# =========================
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

    if user.role not in {"admin", "supervisor", "logistics", "superadmin"}:
        raise HTTPException(
            status_code=400,
            detail="Solo se puede activar o desactivar staff interno",
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
            "is_active": user.is_active,
        },
    }


# =========================
# SUPERADMIN - ACTIVAR / DESACTIVAR CUALQUIER USUARIO
# =========================
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
            "is_active": user.is_active,
        },
    }


# =========================
# SUPERADMIN - ELIMINAR USUARIO
# =========================
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

    db.delete(user)
    db.commit()

    return {
        "message": "Usuario eliminado correctamente",
        "user_id": user_id,
    }
