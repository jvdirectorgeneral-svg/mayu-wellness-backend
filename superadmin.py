from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel, EmailStr
from typing import Optional

from database import SessionLocal
from dependencies import get_current_user
from auth import hash_password
from models import (
    User,
    Ambassador,
    Commission,
    MembershipPayment,
    Order,
    OrderItem,
    MonthlySelection,
    MonthlySelectionItem,
)

router = APIRouter(prefix="/superadmin", tags=["superadmin"])


INTERNAL_ROLES = [
    "admin",
    "superadmin",
    "supervisor",
    "logistics",
    "marketing",
]

CREATABLE_INTERNAL_ROLES = [
    "admin",
    "supervisor",
    "logistics",
    "marketing",
]


class SuperAdminProfileUpdate(BaseModel):
    name: str
    email: EmailStr
    phone: str
    cedula: str


class SuperAdminPasswordUpdate(BaseModel):
    new_password: str


class CreateInternalUserRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    cedula: str
    phone: str
    role: str


class UpdateInternalUserRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    cedula: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None


class UpdateInternalUserStatusRequest(BaseModel):
    is_active: bool


class ResetAnyUserPasswordRequest(BaseModel):
    new_password: str


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_superadmin(current_user: User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Acceso solo para superadmin")


def user_to_dict(db: Session, user: User):
    ambassador = db.query(Ambassador).filter(Ambassador.user_id == user.id).first()

    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "cedula": user.cedula,
        "role": user.role,
        "is_active": user.is_active,
        "membership_level": user.membership_level,
        "membership_active": user.membership_active,
        "ambassador_code": ambassador.ambassador_code if ambassador else None,
        "created_at": user.created_at,
    }


@router.get("/me")
def get_superadmin_profile(current_user: User = Depends(get_current_user)):
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


@router.put("/me")
def update_superadmin_profile(
    payload: SuperAdminProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_superadmin(current_user)

    user = db.query(User).filter(User.id == current_user.id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    email = payload.email.strip().lower()
    cedula = payload.cedula.strip()

    existing_email = db.query(User).filter(User.email == email, User.id != user.id).first()
    if existing_email:
        raise HTTPException(status_code=400, detail="El correo ya está registrado por otro usuario")

    existing_cedula = db.query(User).filter(User.cedula == cedula, User.id != user.id).first()
    if existing_cedula:
        raise HTTPException(status_code=400, detail="La cédula ya está registrada por otro usuario")

    user.name = payload.name.strip()
    user.email = email
    user.phone = payload.phone.strip()
    user.cedula = cedula

    db.commit()
    db.refresh(user)

    return {
        "message": "Perfil superadmin actualizado correctamente",
        "user": user_to_dict(db, user),
    }


@router.put("/me/password")
def update_superadmin_password(
    payload: SuperAdminPasswordUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_superadmin(current_user)

    user = db.query(User).filter(User.id == current_user.id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not payload.new_password or payload.new_password.strip() == "":
        raise HTTPException(status_code=400, detail="La nueva contraseña es obligatoria")

    user.password = hash_password(payload.new_password.strip())

    db.commit()
    db.refresh(user)

    return {"message": "Contraseña superadmin actualizada correctamente", "user_id": user.id}


@router.get("/internal-users")
def get_internal_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_superadmin(current_user)

    users = db.query(User).order_by(User.id.asc()).all()

    return {
        "items": [user_to_dict(db, user) for user in users],
        "internal_roles": INTERNAL_ROLES,
    }


@router.post("/internal-users")
def create_internal_user(
    payload: CreateInternalUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_superadmin(current_user)

    role = payload.role.strip().lower()

    if role not in CREATABLE_INTERNAL_ROLES:
        raise HTTPException(
            status_code=400,
            detail="Rol interno inválido. Roles permitidos: admin, supervisor, logistics, marketing",
        )

    email = payload.email.strip().lower()
    cedula = payload.cedula.strip()

    existing_email = db.query(User).filter(User.email == email).first()
    if existing_email:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")

    existing_cedula = db.query(User).filter(User.cedula == cedula).first()
    if existing_cedula:
        raise HTTPException(status_code=400, detail="La cédula ya está registrada")

    if not payload.password or payload.password.strip() == "":
        raise HTTPException(status_code=400, detail="La contraseña es obligatoria")

    user = User(
        name=payload.name.strip(),
        email=email,
        password=hash_password(payload.password.strip()),
        phone=payload.phone.strip(),
        cedula=cedula,
        city="Sistema",
        address="Usuario interno Mayu",
        reference="Usuario interno Mayu",
        delivery_notes="Usuario interno Mayu",
        phone_secondary=None,
        status="registered",
        membership_level=None,
        membership_active=False,
        is_active=True,
        role=role,
        accepted_terms=True,
        accepted_privacy_policy=True,
        accepted_digital_policy=True,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "message": "Usuario interno creado correctamente",
        "user": user_to_dict(db, user),
    }


@router.put("/internal-users/{user_id}")
def update_internal_user(
    user_id: int,
    payload: UpdateInternalUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_superadmin(current_user)

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role == "superadmin":
        raise HTTPException(status_code=403, detail="No puedes editar otro superadmin desde aquí")

    if user.role not in CREATABLE_INTERNAL_ROLES:
        raise HTTPException(status_code=403, detail="Este endpoint solo edita usuarios internos")

    if payload.role is not None:
        role = payload.role.strip().lower()
        if role not in CREATABLE_INTERNAL_ROLES:
            raise HTTPException(status_code=400, detail="Rol interno inválido")
        user.role = role

    if payload.email is not None:
        email = payload.email.strip().lower()
        existing_email = db.query(User).filter(User.email == email, User.id != user.id).first()
        if existing_email:
            raise HTTPException(status_code=400, detail="El correo ya está registrado por otro usuario")
        user.email = email

    if payload.cedula is not None:
        cedula = payload.cedula.strip()
        existing_cedula = db.query(User).filter(User.cedula == cedula, User.id != user.id).first()
        if existing_cedula:
            raise HTTPException(status_code=400, detail="La cédula ya está registrada por otro usuario")
        user.cedula = cedula

    if payload.name is not None:
        user.name = payload.name.strip()

    if payload.phone is not None:
        user.phone = payload.phone.strip()

    db.commit()
    db.refresh(user)

    return {
        "message": "Usuario interno actualizado correctamente",
        "user": user_to_dict(db, user),
    }


@router.put("/internal-users/{user_id}/status")
def update_internal_user_status(
    user_id: int,
    payload: UpdateInternalUserStatusRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_superadmin(current_user)

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role == "superadmin":
        raise HTTPException(status_code=403, detail="No puedes desactivar el superadmin")

    user.is_active = payload.is_active

    db.commit()
    db.refresh(user)

    return {
        "message": "Estado actualizado correctamente",
        "user": user_to_dict(db, user),
    }


@router.put("/users/{user_id}/reset-password")
def reset_any_user_password(
    user_id: int,
    payload: ResetAnyUserPasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_superadmin(current_user)

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not payload.new_password or payload.new_password.strip() == "":
        raise HTTPException(status_code=400, detail="La nueva contraseña es obligatoria")

    user.password = hash_password(payload.new_password.strip())

    db.commit()
    db.refresh(user)

    return {
        "message": "Contraseña actualizada correctamente",
        "user_id": user.id,
        "name": user.name,
        "role": user.role,
    }


@router.delete("/users/{user_id}/full-delete")
def delete_user_full(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_superadmin(current_user)

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role in INTERNAL_ROLES:
        raise HTTPException(
            status_code=403,
            detail="No puedes eliminar usuarios internos del sistema",
        )

    try:
        orders = db.query(Order).filter(Order.user_id == user_id).all()

        for order in orders:
            db.query(OrderItem).filter(OrderItem.order_id == order.id).delete(
                synchronize_session=False
            )

        db.query(MembershipPayment).filter(
            MembershipPayment.order_id.in_(
                db.query(Order.id).filter(Order.user_id == user_id)
            )
        ).delete(synchronize_session=False)

        db.query(Order).filter(Order.user_id == user_id).delete(
            synchronize_session=False
        )

        db.query(MembershipPayment).filter(
            MembershipPayment.user_id == user_id
        ).delete(synchronize_session=False)

        db.query(MembershipPayment).filter(
            MembershipPayment.admin_verified_by == user_id
        ).update({"admin_verified_by": None}, synchronize_session=False)

        selections = db.query(MonthlySelection).filter(
            MonthlySelection.user_id == user_id
        ).all()

        for selection in selections:
            db.query(MonthlySelectionItem).filter(
                MonthlySelectionItem.monthly_selection_id == selection.id
            ).delete(synchronize_session=False)

        db.query(MonthlySelection).filter(
            MonthlySelection.user_id == user_id
        ).delete(synchronize_session=False)

        db.execute(
            text("DELETE FROM member_cards WHERE user_id = :user_id"),
            {"user_id": user_id},
        )

        db.execute(
            text("DELETE FROM ambassador_referrals WHERE user_id = :user_id"),
            {"user_id": user_id},
        )

        ambassador = db.query(Ambassador).filter(Ambassador.user_id == user_id).first()

        if ambassador:
            db.execute(
                text("DELETE FROM ambassador_referrals WHERE ambassador_id = :ambassador_id"),
                {"ambassador_id": ambassador.id},
            )

            db.query(Commission).filter(
                Commission.ambassador_id == ambassador.id
            ).delete(synchronize_session=False)

            db.query(Ambassador).filter(Ambassador.id == ambassador.id).delete(
                synchronize_session=False
            )

        db.query(Commission).filter(
            Commission.referred_user_id == user_id
        ).delete(synchronize_session=False)

        db.query(User).filter(User.id == user_id).delete(synchronize_session=False)

        db.commit()

        return {"message": f"Usuario {user_id} eliminado completamente del sistema"}

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Error eliminando usuario: {str(e)}",
        )
