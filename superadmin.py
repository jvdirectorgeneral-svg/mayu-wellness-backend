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
    "pharmacy_admin",
    "education_admin",
]

CREATABLE_INTERNAL_ROLES = [
    "admin",
    "supervisor",
    "logistics",
    "marketing",
    "pharmacy_admin",
    "education_admin",
]

ROLE_ALIASES = {
    "admin": "admin",
    "administrador": "admin",

    "supervisor": "supervisor",

    "logistics": "logistics",
    "logistica": "logistics",
    "logística": "logistics",

    "marketing": "marketing",
    "mercadeo": "marketing",

    "pharmacy_admin": "pharmacy_admin",
    "farmacia": "pharmacy_admin",
    "farmaciamayu": "pharmacy_admin",
    "farmacia_mayu": "pharmacy_admin",

    "education_admin": "education_admin",
    "educacion": "education_admin",
    "educación": "education_admin",
    "educacion_mayu": "education_admin",
    "mayu_educacion": "education_admin",
}


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
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para superadmin",
        )


def user_to_dict(db: Session, user: User):
    ambassador = (
        db.query(Ambassador)
        .filter(Ambassador.user_id == user.id)
        .first()
    )

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
        "ambassador_code": (
            ambassador.ambassador_code if ambassador else None
        ),
        "created_at": user.created_at,
    }


@router.get("/me")
def get_superadmin_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_superadmin(current_user)

    return user_to_dict(db, current_user)


@router.put("/me")
def update_superadmin_profile(
    payload: SuperAdminProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_superadmin(current_user)

    user = (
        db.query(User)
        .filter(User.id == current_user.id)
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=404,
            detail="Usuario no encontrado",
        )

    email = payload.email.strip().lower()
    cedula = payload.cedula.strip()

    if (
        db.query(User)
        .filter(User.email == email, User.id != user.id)
        .first()
    ):
        raise HTTPException(
            status_code=400,
            detail="El correo ya está registrado por otro usuario",
        )

    if (
        db.query(User)
        .filter(User.cedula == cedula, User.id != user.id)
        .first()
    ):
        raise HTTPException(
            status_code=400,
            detail="La cédula ya está registrada por otro usuario",
        )

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

    user = (
        db.query(User)
        .filter(User.id == current_user.id)
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=404,
            detail="Usuario no encontrado",
        )

    if not payload.new_password or not payload.new_password.strip():
        raise HTTPException(
            status_code=400,
            detail="La nueva contraseña es obligatoria",
        )

    user.password = hash_password(payload.new_password.strip())

    db.commit()
    db.refresh(user)

    return {
        "message": "Contraseña superadmin actualizada correctamente",
        "user_id": user.id,
    }


@router.get("/internal-users")
def get_internal_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_superadmin(current_user)

    users = (
        db.query(User)
        .filter(User.role.in_(INTERNAL_ROLES))
        .order_by(User.id.asc())
        .all()
    )

    return {
        "items": [user_to_dict(db, user) for user in users],
        "internal_roles": INTERNAL_ROLES,
        "creatable_roles": CREATABLE_INTERNAL_ROLES,
    }


@router.post("/internal-users")
def create_internal_user(
    payload: CreateInternalUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_superadmin(current_user)

    raw_role = payload.role.strip().lower()

    role = ROLE_ALIASES.get(raw_role)

    print("ROL RECIBIDO:", payload.role)
    print("ROL NORMALIZADO:", role)

    if role not in CREATABLE_INTERNAL_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Rol interno inválido recibido: {payload.role}",
        )

    email = payload.email.strip().lower()
    cedula = payload.cedula.strip()

    if db.query(User).filter(User.email == email).first():
        raise HTTPException(
            status_code=400,
            detail="El correo ya está registrado",
        )

    if db.query(User).filter(User.cedula == cedula).first():
        raise HTTPException(
            status_code=400,
            detail="La cédula ya está registrada",
        )

    if not payload.password or not payload.password.strip():
        raise HTTPException(
            status_code=400,
            detail="La contraseña es obligatoria",
        )

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

    user = (
        db.query(User)
        .filter(User.id == user_id)
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=404,
            detail="Usuario no encontrado",
        )

    if user.role == "superadmin":
        raise HTTPException(
            status_code=403,
            detail="No puedes editar otro superadmin desde aquí",
        )

    if user.role not in CREATABLE_INTERNAL_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Este endpoint solo edita usuarios internos",
        )

    if payload.role is not None:
        raw_role = payload.role.strip().lower()

        role = ROLE_ALIASES.get(raw_role)

        print("ROL UPDATE RECIBIDO:", payload.role)
        print("ROL UPDATE NORMALIZADO:", role)

        if role not in CREATABLE_INTERNAL_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"Rol interno inválido recibido: {payload.role}",
            )

        user.role = role

    if payload.email is not None:
        email = payload.email.strip().lower()

        if (
            db.query(User)
            .filter(User.email == email, User.id != user.id)
            .first()
        ):
            raise HTTPException(
                status_code=400,
                detail="El correo ya está registrado por otro usuario",
            )

        user.email = email

    if payload.cedula is not None:
        cedula = payload.cedula.strip()

        if (
            db.query(User)
            .filter(User.cedula == cedula, User.id != user.id)
            .first()
        ):
            raise HTTPException(
                status_code=400,
                detail="La cédula ya está registrada por otro usuario",
            )

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

def table_exists(db: Session, table_name: str) -> bool:
    result = db.execute(
        text("SELECT to_regclass(:table_name)"),
        {"table_name": f"public.{table_name}"},
    ).scalar()

    return result is not None


def delete_if_table_exists(db: Session, table_name: str, column_name: str, value: int):
    if table_exists(db, table_name):
        db.execute(
            text(f"DELETE FROM {table_name} WHERE {column_name} = :value"),
            {"value": value},
        )


def delete_by_subquery_if_table_exists(
    db: Session,
    table_name: str,
    column_name: str,
    parent_table: str,
    parent_column: str,
    user_id: int,
):
    if table_exists(db, table_name) and table_exists(db, parent_table):
        db.execute(
            text(
                f"""
                DELETE FROM {table_name}
                WHERE {column_name} IN (
                    SELECT id FROM {parent_table}
                    WHERE {parent_column} = :user_id
                )
                """
            ),
            {"user_id": user_id},
        )


@router.delete("/users/{user_id}/full-delete")
def full_delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_superadmin(current_user)

    if user_id == current_user.id:
        raise HTTPException(
            status_code=403,
            detail="No puedes eliminar tu propio usuario superadmin",
        )

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="Usuario no encontrado",
        )

    if user.role == "superadmin":
        raise HTTPException(
            status_code=403,
            detail="No puedes eliminar otro superadmin desde aquí",
        )

    try:
        # Hijos de órdenes
        delete_by_subquery_if_table_exists(
            db, "order_items", "order_id", "orders", "user_id", user_id
        )
        delete_by_subquery_if_table_exists(
            db, "order_tracking_history", "order_id", "orders", "user_id", user_id
        )

        # Hijos de selección mensual
        delete_by_subquery_if_table_exists(
            db,
            "monthly_selection_items",
            "selection_id",
            "monthly_selections",
            "user_id",
            user_id,
        )

        # Hijos de órdenes marketplace farmacia
        delete_by_subquery_if_table_exists(
            db,
            "marketplace_order_items",
            "order_id",
            "marketplace_orders",
            "user_id",
            user_id,
        )

        # Hijos de órdenes educación
        delete_by_subquery_if_table_exists(
            db,
            "education_order_items",
            "order_id",
            "education_orders",
            "user_id",
            user_id,
        )

        # Dependencias directas por user_id
        direct_tables = [
            "membership_payments",
            "monthly_selections",
            "orders",
            "member_cards",
            "plan_change_requests",
            "marketplace_orders",
            "education_orders",
            "education_access_logs",
            "marketing_push_tokens",
            "marketing_campaign_recipients",
            "password_recovery_codes",
            "notifications",
        ]

        for table in direct_tables:
            delete_if_table_exists(db, table, "user_id", user_id)

        # Embajador y comisiones
        ambassador = (
            db.query(Ambassador)
            .filter(Ambassador.user_id == user_id)
            .first()
        )

        if ambassador:
            if table_exists(db, "commissions"):
                db.execute(
                    text("DELETE FROM commissions WHERE ambassador_id = :ambassador_id"),
                    {"ambassador_id": ambassador.id},
                )

            db.delete(ambassador)

        # Comisiones directas si existiera user_id
        delete_if_table_exists(db, "commissions", "user_id", user_id)

        # Finalmente usuario
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
            detail=f"No se pudo eliminar completamente el usuario: {str(e)}",
        )
