from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import SessionLocal
from dependencies import get_current_user
from models import (
    User,
    Ambassador,
    Commission,
    MembershipPayment,
    Order,
    MonthlySelection,
    MonthlySelectionItem,
    Product,
)

router = APIRouter(prefix="/superadmin", tags=["superadmin"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_superadmin(current_user: User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if current_user.role != "superadmin":
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para superadmin",
        )


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

    # 🔒 PROTEGER USUARIOS DEL SISTEMA
    if user.role in ["admin", "superadmin", "supervisor", "logistics"]:
        raise HTTPException(
            status_code=403,
            detail="No puedes eliminar usuarios del sistema",
        )

    # 🔥 BORRADO SEGURO EN ORDEN CORRECTO

    # 1. Order Items
    orders = db.query(Order).filter(Order.user_id == user_id).all()
    for order in orders:
        db.execute(
            f"DELETE FROM order_items WHERE order_id = {order.id}"
        )

    # 2. Orders
    db.query(Order).filter(Order.user_id == user_id).delete()

    # 3. Payments
    db.query(MembershipPayment).filter(
        MembershipPayment.user_id == user_id
    ).delete()

    # 4. Monthly selection items
    selections = db.query(MonthlySelection).filter(
        MonthlySelection.user_id == user_id
    ).all()

    for sel in selections:
        db.query(MonthlySelectionItem).filter(
            MonthlySelectionItem.monthly_selection_id == sel.id
        ).delete()

    # 5. Monthly selections
    db.query(MonthlySelection).filter(
        MonthlySelection.user_id == user_id
    ).delete()

    # 6. Member cards
    db.execute(
        f"DELETE FROM member_cards WHERE user_id = {user_id}"
    )

    # 7. Ambassador referrals
    db.execute(
        f"DELETE FROM ambassador_referrals WHERE referred_user_id = {user_id}"
    )

    # 8. Commissions
    db.query(Commission).filter(
        Commission.user_id == user_id
    ).delete()

    # 9. Ambassador (si existe)
    ambassador = db.query(Ambassador).filter(
        Ambassador.user_id == user_id
    ).first()

    if ambassador:
        db.execute(
            f"DELETE FROM ambassador_referrals WHERE ambassador_id = {ambassador.id}"
        )
        db.query(Ambassador).filter(
            Ambassador.id == ambassador.id
        ).delete()

    # 10. Finalmente el usuario
    db.query(User).filter(User.id == user_id).delete()

    db.commit()

    return {
        "message": f"Usuario {user_id} eliminado completamente del sistema"
    }
