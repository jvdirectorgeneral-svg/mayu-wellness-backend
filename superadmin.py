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


class PharmacyAccessCodesRequest(BaseModel):
    pharmacy_points: Optional[str] = None
    doctor_prescriber: Optional[str] = None
    pharmacy_admin: Optional[str] = None
    pharmacy_logistics: Optional[str] = None


PHARMACY_ACCESS_CODES = {
    "pharmacy_points": {
        "label": "Puntos Farmacia",
        "default": "0001",
    },
    "doctor_prescriber": {
        "label": "Doctor Prescriptor",
        "default": "0001",
    },
    "pharmacy_admin": {
        "label": "Administrador Farmacia",
        "default": "4444",
    },
    "pharmacy_logistics": {
        "label": "Logística Farmacia",
        "default": "8888",
    },
}


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


def require_pharmacy_access_code_reader(current_user: User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {"superadmin", "admin", "pharmacy_admin"}:
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para Control Maestro o Farmacia Mayu",
        )


def ensure_app_settings_table(db: Session):
    db.execute(
        text("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key VARCHAR PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
    )


def get_app_setting(db: Session, key: str, default: str):
    ensure_app_settings_table(db)

    value = db.execute(
        text("SELECT value FROM app_settings WHERE key = :key"),
        {"key": key},
    ).scalar()

    return value if value is not None else default


def set_app_setting(db: Session, key: str, value: str):
    ensure_app_settings_table(db)

    db.execute(
        text("""
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (:key, :value, NOW())
            ON CONFLICT (key)
            DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """),
        {"key": key, "value": value},
    )


def pharmacy_access_codes_payload(db: Session):
    return {
        key: {
            "label": config["label"],
            "code": get_app_setting(
                db,
                f"pharmacy_access_code_{key}",
                config["default"],
            ),
        }
        for key, config in PHARMACY_ACCESS_CODES.items()
    }


def clean_access_code(value: Optional[str], label: str):
    if value is None:
        return None

    cleaned = value.strip()

    if not cleaned:
        raise HTTPException(
            status_code=400,
            detail=f"El código de {label} es obligatorio",
        )

    if len(cleaned) > 20:
        raise HTTPException(
            status_code=400,
            detail=f"El código de {label} es demasiado largo",
        )

    return cleaned


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

    user = db.query(User).filter(User.id == current_user.id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    email = payload.email.strip().lower()
    cedula = payload.cedula.strip()

    if db.query(User).filter(User.email == email, User.id != user.id).first():
        raise HTTPException(
            status_code=400,
            detail="El correo ya está registrado por otro usuario",
        )

    if db.query(User).filter(User.cedula == cedula, User.id != user.id).first():
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

    user = db.query(User).filter(User.id == current_user.id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

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


@router.get("/pharmacy-access-codes")
def get_pharmacy_access_codes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_superadmin(current_user)

    return {
        "items": pharmacy_access_codes_payload(db),
    }


@router.get("/pharmacy-access-codes/farmacia")
def get_pharmacy_access_codes_for_farmacia(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_pharmacy_access_code_reader(current_user)

    return {
        "items": pharmacy_access_codes_payload(db),
    }


@router.put("/pharmacy-access-codes")
def update_pharmacy_access_codes(
    payload: PharmacyAccessCodesRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_superadmin(current_user)

    updates = {
        "pharmacy_points": clean_access_code(
            payload.pharmacy_points,
            PHARMACY_ACCESS_CODES["pharmacy_points"]["label"],
        ),
        "doctor_prescriber": clean_access_code(
            payload.doctor_prescriber,
            PHARMACY_ACCESS_CODES["doctor_prescriber"]["label"],
        ),
        "pharmacy_admin": clean_access_code(
            payload.pharmacy_admin,
            PHARMACY_ACCESS_CODES["pharmacy_admin"]["label"],
        ),
        "pharmacy_logistics": clean_access_code(
            payload.pharmacy_logistics,
            PHARMACY_ACCESS_CODES["pharmacy_logistics"]["label"],
        ),
    }

    for key, value in updates.items():
        if value is None:
            continue

        set_app_setting(db, f"pharmacy_access_code_{key}", value)

    db.commit()

    return {
        "message": "Códigos de acceso Farmacia Mayu actualizados",
        "items": pharmacy_access_codes_payload(db),
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
        raise HTTPException(status_code=400, detail="El correo ya está registrado")

    if db.query(User).filter(User.cedula == cedula).first():
        raise HTTPException(status_code=400, detail="La cédula ya está registrada")

    if not payload.password or not payload.password.strip():
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

        if db.query(User).filter(User.email == email, User.id != user.id).first():
            raise HTTPException(
                status_code=400,
                detail="El correo ya está registrado por otro usuario",
            )

        user.email = email

    if payload.cedula is not None:
        cedula = payload.cedula.strip()

        if db.query(User).filter(User.cedula == cedula, User.id != user.id).first():
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


def column_exists(db: Session, table_name: str, column_name: str) -> bool:
    if not table_exists(db, table_name):
        return False

    result = db.execute(
        text("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
            AND table_name = :table_name
            AND column_name = :column_name
            LIMIT 1
        """),
        {
            "table_name": table_name,
            "column_name": column_name,
        },
    ).first()

    return result is not None


def delete_if_table_exists(
    db: Session,
    table_name: str,
    column_name: str,
    value: int,
):
    if table_exists(db, table_name) and column_exists(db, table_name, column_name):
        db.execute(
            text(f"DELETE FROM {table_name} WHERE {column_name} = :value"),
            {"value": value},
        )


def set_null_if_table_exists(
    db: Session,
    table_name: str,
    column_name: str,
    value: int,
):
    if table_exists(db, table_name) and column_exists(db, table_name, column_name):
        db.execute(
            text(f"UPDATE {table_name} SET {column_name} = NULL WHERE {column_name} = :value"),
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
    if (
        table_exists(db, table_name)
        and table_exists(db, parent_table)
        and column_exists(db, table_name, column_name)
        and column_exists(db, parent_table, parent_column)
    ):
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
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if (user.role or "").strip().lower() != "member":
        raise HTTPException(
            status_code=403,
            detail="Por seguridad, este borrado completo solo permite eliminar socios member",
        )

    try:
        # 1. Marketing: primero eventos hijos de recipients
        if table_exists(db, "marketing_events") and table_exists(db, "marketing_campaign_recipients"):
            if column_exists(db, "marketing_events", "recipient_id"):
                db.execute(
                    text("""
                        DELETE FROM marketing_events
                        WHERE recipient_id IN (
                            SELECT id
                            FROM marketing_campaign_recipients
                            WHERE user_id = :user_id
                        )
                    """),
                    {"user_id": user_id},
                )

        delete_if_table_exists(db, "marketing_events", "user_id", user_id)
        delete_if_table_exists(db, "marketing_campaign_recipients", "user_id", user_id)
        delete_if_table_exists(db, "marketing_contacts", "user_id", user_id)

        # 2. Push / recuperación / notificaciones
        delete_if_table_exists(db, "push_notification_tokens", "user_id", user_id)
        delete_if_table_exists(db, "marketing_push_tokens", "user_id", user_id)
        delete_if_table_exists(db, "password_reset_codes", "user_id", user_id)
        delete_if_table_exists(db, "password_recovery_codes", "user_id", user_id)
        delete_if_table_exists(db, "notifications", "user_id", user_id)

        # 3. Órdenes normales: hijos antes de orders
        delete_by_subquery_if_table_exists(
            db, "order_items", "order_id", "orders", "user_id", user_id
        )
        delete_by_subquery_if_table_exists(
            db, "order_tracking_history", "order_id", "orders", "user_id", user_id
        )
        delete_by_subquery_if_table_exists(
            db, "order_delivery_history", "order_id", "orders", "user_id", user_id
        )

        # 4. Pagos asociados a órdenes y usuario
        if table_exists(db, "membership_payments") and column_exists(db, "membership_payments", "order_id"):
            if table_exists(db, "orders") and column_exists(db, "orders", "user_id"):
                db.execute(
                    text("""
                        DELETE FROM membership_payments
                        WHERE order_id IN (
                            SELECT id
                            FROM orders
                            WHERE user_id = :user_id
                        )
                    """),
                    {"user_id": user_id},
                )

        delete_if_table_exists(db, "membership_payments", "user_id", user_id)

        # 5. Selección mensual: hijos antes de monthly_selections
        delete_by_subquery_if_table_exists(
            db,
            "monthly_selection_items",
            "selection_id",
            "monthly_selections",
            "user_id",
            user_id,
        )
        delete_if_table_exists(db, "monthly_selections", "user_id", user_id)

        # 6. Marketplace farmacia: hijos antes de marketplace_orders
        delete_by_subquery_if_table_exists(
            db,
            "marketplace_order_items",
            "order_id",
            "marketplace_orders",
            "user_id",
            user_id,
        )
        delete_if_table_exists(db, "marketplace_orders", "user_id", user_id)

        # 7. Educación: hijos antes de education_orders
        delete_by_subquery_if_table_exists(
            db,
            "education_order_items",
            "order_id",
            "education_orders",
            "user_id",
            user_id,
        )
        delete_if_table_exists(db, "education_orders", "user_id", user_id)
        delete_if_table_exists(db, "education_access_logs", "user_id", user_id)

        # 8. Solicitudes, tarjetas y otros registros directos
        delete_if_table_exists(db, "plan_change_requests", "user_id", user_id)
        delete_by_subquery_if_table_exists(
            db,
            "member_apple_wallet_registrations",
            "card_id",
            "member_cards",
            "user_id",
            user_id,
        )
        delete_if_table_exists(db, "member_cards", "user_id", user_id)

        # 9. Referidos y comisiones del socio antes de eliminar su usuario.
        delete_if_table_exists(db, "commissions", "referred_user_id", user_id)
        delete_if_table_exists(db, "ambassador_referrals", "user_id", user_id)

        # Embajador y comisiones si el socio también fue embajador
        ambassador = (
            db.query(Ambassador)
            .filter(Ambassador.user_id == user_id)
            .first()
        )

        if ambassador:
            if table_exists(db, "commissions"):
                if column_exists(db, "commissions", "ambassador_id"):
                    db.execute(
                        text("DELETE FROM commissions WHERE ambassador_id = :ambassador_id"),
                        {"ambassador_id": ambassador.id},
                    )

            db.delete(ambassador)

        delete_if_table_exists(db, "commissions", "user_id", user_id)

        # 10. Campos created_by / admin_verified_by no deben borrar registros administrativos.
        # Solo se limpian referencias al usuario member para evitar bloqueo FK.
        nullable_reference_tables = [
            ("membership_payments", "admin_verified_by"),
            ("marketplace_orders", "admin_verified_by"),
            ("order_tracking_history", "created_by"),
            ("order_delivery_history", "created_by"),
            ("marketing_campaigns", "created_by"),
            ("marketplace_products", "created_by"),
            ("education_resources", "created_by"),
        ]

        for table_name, column_name in nullable_reference_tables:
            set_null_if_table_exists(db, table_name, column_name, user_id)

        # 11. Finalmente órdenes y usuario
        delete_if_table_exists(db, "orders", "user_id", user_id)

        db.delete(user)
        db.commit()

        return {
            "message": "Socio member eliminado completamente",
            "user_id": user_id,
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo eliminar completamente el socio: {str(e)}",
        )
