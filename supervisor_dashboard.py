from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, extract

from database import SessionLocal
from dependencies import get_current_user
from models import (
    User,
    Ambassador,
    Commission,
    MembershipPayment,
    Order,
    MarketplaceOrder,
    EducationOrder,
)


router = APIRouter(prefix="/supervisor-dashboard", tags=["supervisor-dashboard"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_supervisor_admin_or_superadmin(current_user: User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {"supervisor", "admin", "superadmin"}:
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para supervisor, admin o superadmin",
        )


def payment_to_supervisor_dict(payment: MembershipPayment):
    return {
        "id": payment.id,
        "user_id": payment.user_id,
        "user_name": payment.user.name if payment.user else None,
        "order_id": payment.order_id,
        "payment_type": getattr(payment, "payment_type", None),
        "provider": getattr(payment, "provider", None),
        "amount": payment.amount,
        "currency": payment.currency,
        "status": payment.status,
        "payer_email": payment.payer_email,
        "admin_verified": payment.admin_verified,
        "admin_verified_at": payment.admin_verified_at,
        "created_at": payment.created_at,
        "paid_at": payment.paid_at,
    }


def order_to_supervisor_dict(order: Order):
    return {
        "id": order.id,
        "user_id": order.user_id,
        "user_name": order.user.name if order.user else None,
        "status": order.status,
        "membership_level_snapshot": order.membership_level_snapshot,
        "total_amount_snapshot": order.total_amount_snapshot,
        "created_at": order.created_at,
        "approved_at": order.approved_at,
        "shipped_at": order.shipped_at,
        "delivered_at": order.delivered_at,
    }


# =========================
# KPI GENERALES
# =========================
@router.get("/kpis")
def get_supervisor_kpis(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_supervisor_admin_or_superadmin(current_user)

    total_users = db.query(User).filter(
        User.role == "member"
    ).count()

    active_users = db.query(User).filter(
        User.role == "member",
        User.membership_active == True,
    ).count()

    inactive_users = db.query(User).filter(
        User.role == "member",
        User.membership_active == False,
    ).count()

    total_ambassadors = db.query(Ambassador).count()

    active_ambassadors = db.query(Ambassador).filter(
        Ambassador.is_active == True
    ).count()

    total_payments = db.query(MembershipPayment).count()

    verified_payments = db.query(MembershipPayment).filter(
        MembershipPayment.status.in_([
            "verified",
            "paid",
            "subscription_active",
            "subscription_paid",
        ])
    ).count()

    pending_payments = db.query(MembershipPayment).filter(
        MembershipPayment.status.in_([
            "created",
            "pending",
            "subscription_created",
        ])
    ).count()

    total_paid_amount = db.query(
        func.coalesce(func.sum(MembershipPayment.amount), 0)
    ).filter(
        MembershipPayment.status.in_([
            "verified",
            "paid",
            "subscription_active",
            "subscription_paid",
        ])
    ).scalar()

    total_pending_payment_amount = db.query(
        func.coalesce(func.sum(MembershipPayment.amount), 0)
    ).filter(
        MembershipPayment.status.in_([
            "created",
            "pending",
            "subscription_created",
        ])
    ).scalar()

    total_orders = db.query(Order).count()

    pending_review_orders = db.query(Order).filter(
        Order.status == "pending_payment_review"
    ).count()

    ready_for_logistics = db.query(Order).filter(
        Order.status == "approved_for_logistics"
    ).count()

    shipped_orders = db.query(Order).filter(
        Order.status == "shipped"
    ).count()

    delivered_orders = db.query(Order).filter(
        Order.status == "delivered"
    ).count()

    total_generated = db.query(
        func.coalesce(func.sum(Commission.commission_amount), 0)
    ).scalar()

    total_pending = db.query(
        func.coalesce(func.sum(Commission.commission_amount), 0)
    ).filter(
        Commission.status == "pending"
    ).scalar()

    total_paid = db.query(
        func.coalesce(func.sum(Commission.commission_amount), 0)
    ).filter(
        Commission.status == "paid"
    ).scalar()

    total_commission_records = db.query(Commission).count()

    marketplace_pharmacy_orders = db.query(MarketplaceOrder).count()
    marketplace_pharmacy_paid_orders = db.query(MarketplaceOrder).filter(
        MarketplaceOrder.payment_status.in_(["paid", "verified"])
    ).count()
    marketplace_pharmacy_paid_amount = db.query(
        func.coalesce(func.sum(MarketplaceOrder.total), 0)
    ).filter(
        MarketplaceOrder.payment_status.in_(["paid", "verified"])
    ).scalar()

    education_orders = db.query(EducationOrder).count()
    education_paid_orders = db.query(EducationOrder).filter(
        EducationOrder.payment_status.in_(["paid", "verified"])
    ).count()
    education_paid_amount = db.query(
        func.coalesce(func.sum(EducationOrder.total), 0)
    ).filter(
        EducationOrder.payment_status.in_(["paid", "verified"])
    ).scalar()

    return {
        "total_users": total_users,
        "active_users": active_users,
        "inactive_users": inactive_users,
        "total_ambassadors": total_ambassadors,
        "active_ambassadors": active_ambassadors,

        "total_payments": total_payments,
        "verified_payments": verified_payments,
        "pending_payments": pending_payments,
        "total_paid_amount": float(total_paid_amount or 0),
        "total_pending_payment_amount": float(total_pending_payment_amount or 0),

        "total_orders": total_orders,
        "pending_review_orders": pending_review_orders,
        "ready_for_logistics": ready_for_logistics,
        "shipped_orders": shipped_orders,
        "delivered_orders": delivered_orders,

        "total_commission_records": total_commission_records,
        "total_generated": float(total_generated or 0),
        "total_pending": float(total_pending or 0),
        "total_paid": float(total_paid or 0),

        "marketplace_pharmacy_orders": marketplace_pharmacy_orders,
        "marketplace_pharmacy_paid_orders": marketplace_pharmacy_paid_orders,
        "marketplace_pharmacy_paid_amount": float(marketplace_pharmacy_paid_amount or 0),
        "education_orders": education_orders,
        "education_paid_orders": education_paid_orders,
        "education_paid_amount": float(education_paid_amount or 0),
    }


@router.get("/summary")
def get_supervisor_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_supervisor_admin_or_superadmin(current_user)

    kpis = get_supervisor_kpis(db=db, current_user=current_user)

    initial_membership_payments = (
        db.query(MembershipPayment)
        .filter(MembershipPayment.payment_type.in_(["signup", "membership_initial", "subscription"]))
        .count()
    )
    renewal_membership_payments = (
        db.query(MembershipPayment)
        .filter(MembershipPayment.payment_type == "subscription_renewal")
        .count()
    )

    return {
        "total_socios": kpis["total_users"],
        "active_socios": kpis["active_users"],
        "inactive_socios": kpis["inactive_users"],
        "initial_membership_payments": initial_membership_payments,
        "renewal_membership_payments": renewal_membership_payments,
        "total_ambassadors": kpis["total_ambassadors"],
        "active_ambassadors": kpis["active_ambassadors"],
        "pending_review_orders": kpis["pending_review_orders"],
        "ready_for_logistics": kpis["ready_for_logistics"],
        "shipped_orders": kpis["shipped_orders"],
        "delivered_orders": kpis["delivered_orders"],
        "total_generated": kpis["total_generated"],
        "total_pending": kpis["total_pending"],
        "total_paid": kpis["total_paid"],
        "total_payments": kpis["total_payments"],
        "verified_payments": kpis["verified_payments"],
        "pending_payments": kpis["pending_payments"],
        "total_paid_amount": kpis["total_paid_amount"],
        "total_pending_payment_amount": kpis["total_pending_payment_amount"],
        "marketplace_pharmacy_orders": kpis["marketplace_pharmacy_orders"],
        "marketplace_pharmacy_paid_orders": kpis["marketplace_pharmacy_paid_orders"],
        "marketplace_pharmacy_paid_amount": kpis["marketplace_pharmacy_paid_amount"],
        "education_orders": kpis["education_orders"],
        "education_paid_orders": kpis["education_paid_orders"],
        "education_paid_amount": kpis["education_paid_amount"],
        "commission_rule": "Nivel 1: $5, Nivel 2: $6, Nivel 3: $7. Pago administrativo del día 8 al 10 con corte al mes anterior.",
        "admin_review_window": "Afiliaciones y renovaciones en Admin Mayu",
        "shipping_window": "Logística despacha órdenes aprobadas",
    }


@router.get("/payments")
def get_supervisor_payments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_supervisor_admin_or_superadmin(current_user)

    payments = (
        db.query(MembershipPayment)
        .order_by(MembershipPayment.created_at.desc())
        .limit(300)
        .all()
    )

    return {"items": [payment_to_supervisor_dict(payment) for payment in payments]}


@router.get("/orders")
def get_supervisor_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_supervisor_admin_or_superadmin(current_user)

    orders = db.query(Order).order_by(Order.created_at.desc()).limit(300).all()
    return {"items": [order_to_supervisor_dict(order) for order in orders]}


# =========================
# DISTRIBUCIÓN DE PLANES
# =========================
@router.get("/plan-distribution")
def get_plan_distribution(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_supervisor_admin_or_superadmin(current_user)

    cobre = db.query(User).filter(
        User.role == "member",
        User.membership_level == 1,
        User.membership_active == True,
    ).count()

    plata = db.query(User).filter(
        User.role == "member",
        User.membership_level == 2,
        User.membership_active == True,
    ).count()

    oro = db.query(User).filter(
        User.role == "member",
        User.membership_level == 3,
        User.membership_active == True,
    ).count()

    total = cobre + plata + oro

    return {
        "total_active_members": total,
        "cobre": cobre,
        "plata": plata,
        "oro": oro,
    }


# =========================
# RANKING DE EMBAJADORES
# =========================
@router.get("/ambassador-ranking")
def get_supervisor_ambassador_ranking(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_supervisor_admin_or_superadmin(current_user)

    rows = (
        db.query(
            Commission.ambassador_id,
            func.count(Commission.id).label("total_records"),
            func.coalesce(func.sum(Commission.commission_amount), 0).label("total_generated"),
        )
        .group_by(Commission.ambassador_id)
        .order_by(func.coalesce(func.sum(Commission.commission_amount), 0).desc())
        .all()
    )

    items = []

    for row in rows:
        ambassador = db.query(Ambassador).filter(
            Ambassador.id == row.ambassador_id
        ).first()

        ambassador_name = None
        ambassador_code = None

        if ambassador:
            ambassador_code = ambassador.ambassador_code
            ambassador_user = db.query(User).filter(
                User.id == ambassador.user_id
            ).first()

            if ambassador_user:
                ambassador_name = ambassador_user.name

        items.append({
            "ambassador_id": row.ambassador_id,
            "ambassador_name": ambassador_name,
            "ambassador_code": ambassador_code,
            "total_records": row.total_records,
            "total_generated": float(row.total_generated or 0),
        })

    return {
        "total_items": len(items),
        "items": items,
    }


# =========================
# CRECIMIENTO MENSUAL DE SOCIOS
# =========================
@router.get("/monthly-users-growth")
def get_monthly_users_growth(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_supervisor_admin_or_superadmin(current_user)

    rows = (
        db.query(
            extract("year", User.created_at).label("year"),
            extract("month", User.created_at).label("month"),
            func.count(User.id).label("total_users"),
        )
        .filter(User.role == "member")
        .group_by(
            extract("year", User.created_at),
            extract("month", User.created_at),
        )
        .order_by(
            extract("year", User.created_at),
            extract("month", User.created_at),
        )
        .all()
    )

    return [
        {
            "year": int(row.year),
            "month": int(row.month),
            "total_users": row.total_users,
        }
        for row in rows
    ]


# =========================
# CRECIMIENTO MENSUAL DE COMISIONES
# =========================
@router.get("/monthly-commissions-growth")
def get_monthly_commissions_growth(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_supervisor_admin_or_superadmin(current_user)

    rows = (
        db.query(
            Commission.year,
            Commission.month,
            func.count(Commission.id).label("total_records"),
            func.coalesce(func.sum(Commission.commission_amount), 0).label("total_generated"),
        )
        .group_by(Commission.year, Commission.month)
        .order_by(Commission.year, Commission.month)
        .all()
    )

    return [
        {
            "year": row.year,
            "month": row.month,
            "total_records": row.total_records,
            "total_generated": float(row.total_generated or 0),
        }
        for row in rows
    ]


# =========================
# CRECIMIENTO MENSUAL DE PAGOS REALES
# =========================
@router.get("/monthly-payments-growth")
def get_monthly_payments_growth(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_supervisor_admin_or_superadmin(current_user)

    rows = (
        db.query(
            extract("year", MembershipPayment.created_at).label("year"),
            extract("month", MembershipPayment.created_at).label("month"),
            func.count(MembershipPayment.id).label("total_records"),
            func.coalesce(func.sum(MembershipPayment.amount), 0).label("total_paid"),
        )
        .filter(
            MembershipPayment.status.in_([
                "verified",
                "paid",
                "subscription_active",
                "subscription_paid",
            ])
        )
        .group_by(
            extract("year", MembershipPayment.created_at),
            extract("month", MembershipPayment.created_at),
        )
        .order_by(
            extract("year", MembershipPayment.created_at),
            extract("month", MembershipPayment.created_at),
        )
        .all()
    )

    return [
        {
            "year": int(row.year),
            "month": int(row.month),
            "total_records": row.total_records,
            "total_paid": float(row.total_paid or 0),
        }
        for row in rows
    ]
