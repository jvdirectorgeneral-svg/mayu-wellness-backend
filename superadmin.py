from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel, EmailStr

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


# =========================
# SCHEMAS
# =========================
class SuperAdminProfileUpdate(BaseModel):
    name: str
    email: EmailStr
    phone: str
    cedula: str


class SuperAdminPasswordUpdate(BaseModel):
    new_password: str


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
# SECURITY
# =========================
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


# =========================
# SUPERADMIN PROFILE
# =========================
@router.get("/me")
def get_superadmin_profile(
    current_user: User = Depends(get_current_user),
):
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

    existing_email = db.query(User).filter(
        User.email == email,
        User.id != user.id,
    ).first()

    if existing_email:
        raise HTTPException(
            status_code=400,
            detail="El correo ya está registrado por otro usuario",
        )

    existing_cedula = db.query(User).filter(
        User.cedula == cedula,
        User.id != user.id,
    ).first()

    if existing_cedula:
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
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "phone": user.phone,
            "cedula": user.cedula,
            "role": user.role,
            "is_active": getattr(user, "is_active", True),
        },
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


# =========================
# FULL DELETE USER
# =========================
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

    if user.role in ["admin", "superadmin", "supervisor", "logistics"]:
        raise HTTPException(
            status_code=403,
            detail="No puedes eliminar usuarios del sistema",
        )

    try:
        # =========================
        # ÓRDENES + ITEMS
        # =========================
        orders = db.query(Order).filter(Order.user_id == user_id).all()

        for order in orders:
            db.query(OrderItem).filter(
                OrderItem.order_id == order.id
            ).delete(synchronize_session=False)

        db.query(MembershipPayment).filter(
            MembershipPayment.order_id.in_(
                db.query(Order.id).filter(Order.user_id == user_id)
            )
        ).delete(synchronize_session=False)

        db.query(Order).filter(
            Order.user_id == user_id
        ).delete(synchronize_session=False)

        # =========================
        # PAGOS
        # =========================
        db.query(MembershipPayment).filter(
            MembershipPayment.user_id == user_id
        ).delete(synchronize_session=False)

        db.query(MembershipPayment).filter(
            MembershipPayment.admin_verified_by == user_id
        ).update(
            {"admin_verified_by": None},
            synchronize_session=False,
        )

        # =========================
        # SELECCIÓN MENSUAL
        # =========================
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

        # =========================
        # TARJETAS
        # =========================
        db.execute(
            text("DELETE FROM member_cards WHERE user_id = :user_id"),
            {"user_id": user_id},
        )

        # =========================
        # REFERIDOS DONDE ES SOCIO
        # =========================
        db.execute(
            text("""
                DELETE FROM ambassador_referrals
                WHERE user_id = :user_id
                   OR referred_user_id = :user_id
            """),
            {"user_id": user_id},
        )

        # =========================
        # SI ES EMBAJADOR
        # =========================
        ambassador = db.query(Ambassador).filter(
            Ambassador.user_id == user_id
        ).first()

        if ambassador:
            db.execute(
                text("""
                    DELETE FROM ambassador_referrals
                    WHERE ambassador_id = :ambassador_id
                """),
                {"ambassador_id": ambassador.id},
            )

            db.query(Commission).filter(
                Commission.ambassador_id == ambassador.id
            ).delete(synchronize_session=False)

            db.query(Ambassador).filter(
                Ambassador.id == ambassador.id
            ).delete(synchronize_session=False)

        # =========================
        # COMISIONES SI EXISTE user_id
        # =========================
        if hasattr(Commission, "user_id"):
            db.query(Commission).filter(
                Commission.user_id == user_id
            ).delete(synchronize_session=False)

        # =========================
        # USUARIO
        # =========================
        db.query(User).filter(
            User.id == user_id
        ).delete(synchronize_session=False)

        db.commit()

        return {
            "message": f"Usuario {user_id} eliminado completamente del sistema"
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Error eliminando usuario: {str(e)}",
        )
