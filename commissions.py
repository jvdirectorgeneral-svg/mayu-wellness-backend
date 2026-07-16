from datetime import datetime
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, case

from database import SessionLocal
from dependencies import get_current_user
from member_cards import get_or_create_card, safe_update_member_wallets
from models import (
    Commission,
    Ambassador,
    AmbassadorReferral,
    User,
    Plan,
    MembershipPayment,
)

router = APIRouter(prefix="/commissions", tags=["commissions"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class GenerateMonthlyCommissionsRequest(BaseModel):
    month: Optional[int] = None
    year: Optional[int] = None


def require_admin_supervisor_or_superadmin(current_user: User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {"superadmin", "admin", "supervisor"}:
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para administración o supervisión",
        )


def get_commission_amount_by_level(level: int | None) -> float:
    if level == 1:
        return 5.00
    if level == 2:
        return 6.00
    if level == 3:
        return 7.00
    return 0.00


def get_commission_rule_label(level: int | None) -> str:
    if level == 1:
        return "Nivel 1 - Cobre: $5 mensual por socio activo pagado"
    if level == 2:
        return "Nivel 2 - Plata: $6 mensual por socio activo pagado"
    if level == 3:
        return "Nivel 3 - Oro: $7 mensual por socio activo pagado"
    return "Sin regla de comisión"


def sync_ambassador_wallets(db: Session, ambassador_id: int):
    ambassador = db.query(Ambassador).filter(Ambassador.id == ambassador_id).first()
    if not ambassador:
        return {"google": {"updated": False, "detail": "Embajador no encontrado"}, "apple": {"sent": 0, "errors": []}}

    try:
        user, card = get_or_create_card(db, ambassador.user_id)
        first_sync = safe_update_member_wallets(db, user, card)

        # Second wallet sync after a short delay helps Apple/Google pick up
        # the already-committed ambassador state more consistently.
        time.sleep(2)
        db.expire_all()
        fresh_ambassador = db.query(Ambassador).filter(Ambassador.id == ambassador_id).first()
        if fresh_ambassador:
            user, card = get_or_create_card(db, fresh_ambassador.user_id)
        second_sync = safe_update_member_wallets(db, user, card)

        return {
            "initial": first_sync,
            "delayed_retry": second_sync,
        }
    except Exception as exc:
        return {
            "google": {"updated": False, "detail": str(exc)},
            "apple": {"sent": 0, "errors": [{"detail": str(exc)}]},
        }


def get_plan_by_user_level(db: Session, user: User) -> Plan | None:
    if user.membership_level is None:
        return None

    return db.query(Plan).filter(Plan.level == user.membership_level).first()


def has_valid_monthly_payment(db: Session, user_id: int, month: int, year: int) -> bool:
    payments = (
        db.query(MembershipPayment)
        .filter(
            MembershipPayment.user_id == user_id,
            MembershipPayment.status.in_(
                [
                    "verified",
                    "subscription_paid",
                    "subscription_active",
                ]
            ),
        )
        .order_by(MembershipPayment.created_at.desc())
        .all()
    )

    for payment in payments:
        if getattr(payment, "monthly_selection", None):
            if (
                payment.monthly_selection.month == month
                and payment.monthly_selection.year == year
            ):
                return True

        if getattr(payment, "order", None):
            if payment.order.month == month and payment.order.year == year:
                return True

        if (
            payment.created_at
            and payment.created_at.month == month
            and payment.created_at.year == year
        ):
            return True

    return False


def get_payment_status(db: Session, user: User, month: int, year: int) -> str:
    if has_valid_monthly_payment(db, user.id, month, year):
        return "paid"

    if user.membership_active:
        return "active_subscription"

    return "pending"


def get_member_status(user: User) -> str:
    return "active" if user.membership_active else "inactive"


def get_eligibility_status(
    db: Session,
    user: User,
    plan: Plan | None,
    month: int,
    year: int,
) -> str:
    if not user:
        return "cancelled"

    if not user.is_active:
        return "ineligible"

    if not user.membership_active:
        return "ineligible"

    if user.membership_level is None:
        return "ineligible"

    if user.membership_level not in {1, 2, 3}:
        return "ineligible"

    if not plan:
        return "ineligible"

    if not plan.active:
        return "ineligible"

    if not has_valid_monthly_payment(db, user.id, month, year):
        return "ineligible"

    return "eligible"


def is_user_eligible(
    db: Session,
    user: User,
    plan: Plan | None,
    month: int,
    year: int,
) -> bool:
    return get_eligibility_status(db, user, plan, month, year) == "eligible"


def ensure_current_month_pending_commissions_for_ambassador(
    db: Session,
    ambassador: Ambassador,
    month: int | None = None,
    year: int | None = None,
):
    now = datetime.utcnow()
    month = month or now.month
    year = year or now.year

    created_items = []

    referrals = (
        db.query(AmbassadorReferral)
        .filter(
            AmbassadorReferral.ambassador_id == ambassador.id,
            AmbassadorReferral.status == "active",
        )
        .all()
    )

    for referral in referrals:
        referred_user = db.query(User).filter(User.id == referral.user_id).first()
        if not referred_user:
            continue

        plan = get_plan_by_user_level(db, referred_user)
        if not is_user_eligible(db, referred_user, plan, month, year):
            continue

        existing_commission = (
            db.query(Commission)
            .filter(
                Commission.ambassador_id == ambassador.id,
                Commission.referred_user_id == referred_user.id,
                Commission.month == month,
                Commission.year == year,
            )
            .first()
        )
        if existing_commission:
            continue

        commission_amount = get_commission_amount_by_level(referred_user.membership_level)
        if commission_amount <= 0 or not plan:
            continue

        commission = Commission(
            ambassador_id=ambassador.id,
            referred_user_id=referred_user.id,
            plan_id=plan.id,
            month=month,
            year=year,
            base_amount=float(plan.price),
            commission_percent=0,
            commission_amount=commission_amount,
            member_status=get_member_status(referred_user),
            payment_status=get_payment_status(db, referred_user, month, year),
            eligibility_status=get_eligibility_status(
                db,
                referred_user,
                plan,
                month,
                year,
            ),
            status="pending",
            generated_at=datetime.utcnow(),
            notes=(
                f"Comisión mensual generada automáticamente para {month}/{year} "
                f"antes de pago administrativo."
            ),
        )
        db.add(commission)
        created_items.append(commission)

    return created_items


def commission_to_dict(db: Session, c: Commission):
    ambassador = db.query(Ambassador).filter(Ambassador.id == c.ambassador_id).first()
    ambassador_user = (
        db.query(User).filter(User.id == ambassador.user_id).first()
        if ambassador
        else None
    )
    referred_user = db.query(User).filter(User.id == c.referred_user_id).first()
    plan = db.query(Plan).filter(Plan.id == c.plan_id).first()

    return {
        "commission_id": c.id,
        "id": c.id,
        "ambassador_id": c.ambassador_id,
        "ambassador_user_id": ambassador.user_id if ambassador else None,
        "ambassador_name": ambassador_user.name if ambassador_user else None,
        "ambassador_email": ambassador_user.email if ambassador_user else None,
        "ambassador_phone": ambassador_user.phone if ambassador_user else None,
        "ambassador_code": ambassador.ambassador_code if ambassador else None,
        "bank_name": getattr(ambassador, "bank_name", None) if ambassador else None,
        "bank_account_type": getattr(ambassador, "bank_account_type", None) if ambassador else None,
        "bank_account_number": getattr(ambassador, "bank_account_number", None) if ambassador else None,
        "bank_account_holder": getattr(ambassador, "bank_account_holder", None) if ambassador else None,
        "bank_identification": getattr(ambassador, "bank_identification", None) if ambassador else None,
        "payment_notes": getattr(ambassador, "payment_notes", None) if ambassador else None,
        "referred_user_id": c.referred_user_id,
        "referred_user_name": referred_user.name if referred_user else None,
        "referred_user_email": referred_user.email if referred_user else None,
        "referred_membership_level": referred_user.membership_level if referred_user else None,
        "commission_rule": get_commission_rule_label(
            referred_user.membership_level if referred_user else None
        ),
        "plan_id": c.plan_id,
        "plan_name": plan.name if plan else None,
        "month": c.month,
        "year": c.year,
        "base_amount": c.base_amount,
        "commission_percent": c.commission_percent,
        "commission_amount": c.commission_amount,
        "amount": c.commission_amount,
        "member_status": c.member_status,
        "payment_status": c.payment_status,
        "eligibility_status": c.eligibility_status,
        "status": c.status,
        "generated_at": c.generated_at,
        "paid_at": c.paid_at,
        "notes": c.notes,
    }


@router.get("/test")
def commissions_test(db: Session = Depends(get_db)):
    total = db.query(Commission).count()
    pending = db.query(Commission).filter(Commission.status == "pending").count()
    paid = db.query(Commission).filter(Commission.status == "paid").count()
    cancelled = db.query(Commission).filter(Commission.status == "cancelled").count()

    return {
        "message": "commissions router ok",
        "total_commissions": total,
        "pending": pending,
        "paid": paid,
        "cancelled": cancelled,
        "business_rule": "Nivel 1: $5, Nivel 2: $6, Nivel 3: $7 por socio activo pagado mensualmente.",
    }


@router.post("/generate-monthly")
def generate_monthly_commissions(
    payload: GenerateMonthlyCommissionsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_supervisor_or_superadmin(current_user)

    now = datetime.utcnow()
    month = payload.month or now.month
    year = payload.year or now.year

    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Mes inválido")

    if year < 2024 or year > 2100:
        raise HTTPException(status_code=400, detail="Año inválido")

    referrals = db.query(AmbassadorReferral).filter(
        AmbassadorReferral.status == "active"
    ).all()

    created_count = 0
    skipped_existing = 0
    skipped_not_eligible = 0
    skipped_missing_data = 0
    created_items = []
    skipped_items = []

    for referral in referrals:
        ambassador = db.query(Ambassador).filter(
            Ambassador.id == referral.ambassador_id
        ).first()

        if not ambassador:
            skipped_missing_data += 1
            skipped_items.append({
                "referral_id": referral.id,
                "reason": "Embajador no encontrado",
            })
            continue

        if not ambassador.is_active or ambassador.status != "active":
            skipped_not_eligible += 1
            skipped_items.append({
                "ambassador_id": ambassador.id,
                "reason": "Embajador inactivo",
            })
            continue

        referred_user = db.query(User).filter(User.id == referral.user_id).first()

        if not referred_user:
            skipped_missing_data += 1
            skipped_items.append({
                "referral_id": referral.id,
                "reason": "Usuario referido no encontrado",
            })
            continue

        plan = get_plan_by_user_level(db, referred_user)

        member_status = get_member_status(referred_user)
        payment_status = get_payment_status(db, referred_user, month, year)
        eligibility_status = get_eligibility_status(
            db,
            referred_user,
            plan,
            month,
            year,
        )

        existing_commission = db.query(Commission).filter(
            Commission.ambassador_id == ambassador.id,
            Commission.referred_user_id == referred_user.id,
            Commission.month == month,
            Commission.year == year,
        ).first()

        if existing_commission:
            skipped_existing += 1
            skipped_items.append({
                "ambassador_id": ambassador.id,
                "referred_user_id": referred_user.id,
                "reason": "Comisión ya existe para este mes",
            })
            continue

        if not is_user_eligible(db, referred_user, plan, month, year):
            skipped_not_eligible += 1
            skipped_items.append({
                "ambassador_id": ambassador.id,
                "referred_user_id": referred_user.id,
                "reason": "Usuario no elegible",
                "member_status": member_status,
                "payment_status": payment_status,
                "eligibility_status": eligibility_status,
            })
            continue

        commission_amount = get_commission_amount_by_level(referred_user.membership_level)

        if commission_amount <= 0:
            skipped_not_eligible += 1
            skipped_items.append({
                "ambassador_id": ambassador.id,
                "referred_user_id": referred_user.id,
                "reason": "Nivel sin regla de comisión",
                "membership_level": referred_user.membership_level,
            })
            continue

        commission = Commission(
            ambassador_id=ambassador.id,
            referred_user_id=referred_user.id,
            plan_id=plan.id,
            month=month,
            year=year,
            base_amount=float(plan.price),
            commission_percent=0,
            commission_amount=commission_amount,
            member_status=member_status,
            payment_status=payment_status,
            eligibility_status=eligibility_status,
            status="pending",
            generated_at=datetime.utcnow(),
            notes=(
                f"Comisión mensual generada para {month}/{year}. "
                f"Regla fija: {get_commission_rule_label(referred_user.membership_level)}."
            ),
        )

        db.add(commission)
        created_count += 1

        created_items.append({
            "ambassador_id": ambassador.id,
            "ambassador_code": ambassador.ambassador_code,
            "referred_user_id": referred_user.id,
            "referred_user_name": referred_user.name,
            "membership_level": referred_user.membership_level,
            "plan_id": plan.id,
            "plan_name": plan.name,
            "base_amount": float(plan.price),
            "commission_percent": 0,
            "commission_amount": commission_amount,
            "commission_rule": get_commission_rule_label(referred_user.membership_level),
            "month": month,
            "year": year,
        })

    db.commit()
    wallet_sync = {}
    affected_ambassadors = sorted({item["ambassador_id"] for item in created_items if item.get("ambassador_id")})
    for ambassador_id in affected_ambassadors:
        wallet_sync[str(ambassador_id)] = sync_ambassador_wallets(db, ambassador_id)

    return {
        "message": "Generación mensual de comisiones completada",
        "business_rule": "Nivel 1: $5, Nivel 2: $6, Nivel 3: $7 por socio activo pagado mensualmente.",
        "month": month,
        "year": year,
        "created_count": created_count,
        "skipped_existing": skipped_existing,
        "skipped_not_eligible": skipped_not_eligible,
        "skipped_missing_data": skipped_missing_data,
        "created_items": created_items,
        "skipped_items": skipped_items,
        "wallet_sync": wallet_sync,
    }


@router.get("/ambassador/{ambassador_id}")
def get_commissions_by_ambassador(
    ambassador_id: int,
    db: Session = Depends(get_db),
):
    ambassador = db.query(Ambassador).filter(Ambassador.id == ambassador_id).first()

    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    commissions = (
        db.query(Commission)
        .filter(Commission.ambassador_id == ambassador_id)
        .order_by(Commission.year.desc(), Commission.month.desc(), Commission.id.desc())
        .all()
    )

    return {
        "ambassador_id": ambassador.id,
        "ambassador_code": ambassador.ambassador_code,
        "bank_name": getattr(ambassador, "bank_name", None),
        "bank_account_type": getattr(ambassador, "bank_account_type", None),
        "bank_account_number": getattr(ambassador, "bank_account_number", None),
        "bank_account_holder": getattr(ambassador, "bank_account_holder", None),
        "bank_identification": getattr(ambassador, "bank_identification", None),
        "total_items": len(commissions),
        "items": [commission_to_dict(db, c) for c in commissions],
    }


@router.get("/ambassador/{ambassador_id}/summary")
def get_commissions_summary_by_ambassador(
    ambassador_id: int,
    db: Session = Depends(get_db),
):
    ambassador = db.query(Ambassador).filter(Ambassador.id == ambassador_id).first()

    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    commissions = db.query(Commission).filter(
        Commission.ambassador_id == ambassador_id
    ).all()

    total_generated = round(sum(c.commission_amount for c in commissions), 2)
    total_pending = round(sum(c.commission_amount for c in commissions if c.status == "pending"), 2)
    total_paid = round(sum(c.commission_amount for c in commissions if c.status == "paid"), 2)
    total_cancelled = round(sum(c.commission_amount for c in commissions if c.status == "cancelled"), 2)

    active_members = len([c for c in commissions if c.member_status == "active"])
    eligible_members = len([c for c in commissions if c.eligibility_status == "eligible"])

    return {
        "ambassador_id": ambassador.id,
        "ambassador_code": ambassador.ambassador_code,
        "bank_name": getattr(ambassador, "bank_name", None),
        "bank_account_type": getattr(ambassador, "bank_account_type", None),
        "bank_account_number": getattr(ambassador, "bank_account_number", None),
        "bank_account_holder": getattr(ambassador, "bank_account_holder", None),
        "bank_identification": getattr(ambassador, "bank_identification", None),
        "total_generated": total_generated,
        "total_pending": total_pending,
        "total_paid": total_paid,
        "total_cancelled": total_cancelled,
        "active_members_count": active_members,
        "eligible_members_count": eligible_members,
        "total_commission_records": len(commissions),
        "business_rule": "Nivel 1: $5, Nivel 2: $6, Nivel 3: $7.",
    }


@router.get("/summary/general")
def get_general_commissions_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_supervisor_or_superadmin(current_user)

    commissions = db.query(Commission).all()

    total_generated = round(sum(c.commission_amount for c in commissions), 2)
    total_pending = round(sum(c.commission_amount for c in commissions if c.status == "pending"), 2)
    total_paid = round(sum(c.commission_amount for c in commissions if c.status == "paid"), 2)
    total_cancelled = round(sum(c.commission_amount for c in commissions if c.status == "cancelled"), 2)

    total_eligible = len([c for c in commissions if c.eligibility_status == "eligible"])
    total_not_eligible = len([c for c in commissions if c.eligibility_status != "eligible"])

    return {
        "total_commissions": len(commissions),
        "total_generated": total_generated,
        "total_pending": total_pending,
        "total_paid": total_paid,
        "total_cancelled": total_cancelled,
        "total_eligible_records": total_eligible,
        "total_not_eligible_records": total_not_eligible,
        "business_rule": "Nivel 1: $5, Nivel 2: $6, Nivel 3: $7.",
    }


@router.get("/ranking/ambassadors")
def get_ambassadors_ranking(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_supervisor_or_superadmin(current_user)

    rows = (
        db.query(
            Commission.ambassador_id,
            func.count(Commission.id).label("total_records"),
            func.coalesce(func.sum(Commission.commission_amount), 0).label("total_generated"),
            func.coalesce(
                func.sum(
                    case(
                        (Commission.status == "pending", Commission.commission_amount),
                        else_=0,
                    )
                ),
                0,
            ).label("total_pending"),
            func.coalesce(
                func.sum(
                    case(
                        (Commission.status == "paid", Commission.commission_amount),
                        else_=0,
                    )
                ),
                0,
            ).label("total_paid"),
        )
        .group_by(Commission.ambassador_id)
        .order_by(func.coalesce(func.sum(Commission.commission_amount), 0).desc())
        .all()
    )

    ranking = []

    for row in rows:
        ambassador = db.query(Ambassador).filter(
            Ambassador.id == row.ambassador_id
        ).first()

        ambassador_user = (
            db.query(User).filter(User.id == ambassador.user_id).first()
            if ambassador
            else None
        )

        ranking.append({
            "ambassador_id": row.ambassador_id,
            "ambassador_user_id": ambassador.user_id if ambassador else None,
            "ambassador_code": ambassador.ambassador_code if ambassador else None,
            "ambassador_name": ambassador_user.name if ambassador_user else None,
            "ambassador_email": ambassador_user.email if ambassador_user else None,
            "ambassador_phone": ambassador_user.phone if ambassador_user else None,
            "bank_name": getattr(ambassador, "bank_name", None) if ambassador else None,
            "bank_account_type": getattr(ambassador, "bank_account_type", None) if ambassador else None,
            "bank_account_number": getattr(ambassador, "bank_account_number", None) if ambassador else None,
            "bank_account_holder": getattr(ambassador, "bank_account_holder", None) if ambassador else None,
            "bank_identification": getattr(ambassador, "bank_identification", None) if ambassador else None,
            "total_records": row.total_records,
            "total_generated": round(float(row.total_generated or 0), 2),
            "total_pending": round(float(row.total_pending or 0), 2),
            "total_paid": round(float(row.total_paid or 0), 2),
        })

    return {
        "total_ambassadors_in_ranking": len(ranking),
        "business_rule": "Nivel 1: $5, Nivel 2: $6, Nivel 3: $7.",
        "items": ranking,
    }


@router.get("/pending")
def get_pending_commissions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_supervisor_or_superadmin(current_user)

    commissions = (
        db.query(Commission)
        .filter(Commission.status == "pending")
        .order_by(Commission.year.desc(), Commission.month.desc(), Commission.id.desc())
        .all()
    )

    return {
        "total_items": len(commissions),
        "items": [commission_to_dict(db, c) for c in commissions],
    }


@router.get("/paid")
def get_paid_commissions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_supervisor_or_superadmin(current_user)

    commissions = (
        db.query(Commission)
        .filter(Commission.status == "paid")
        .order_by(Commission.year.desc(), Commission.month.desc(), Commission.id.desc())
        .all()
    )

    return {
        "total_items": len(commissions),
        "items": [commission_to_dict(db, c) for c in commissions],
    }


@router.put("/{commission_id}/mark-paid")
def mark_commission_as_paid(
    commission_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_supervisor_or_superadmin(current_user)

    commission = db.query(Commission).filter(Commission.id == commission_id).first()

    if not commission:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    if commission.status == "paid":
        return {
            "message": "La comisión ya estaba pagada",
            "commission_id": commission.id,
            "status": commission.status,
            "paid_at": commission.paid_at,
        }

    commission.status = "paid"
    commission.paid_at = datetime.utcnow()
    commission.notes = (
        f"{commission.notes or ''} | Marcada como pagada manualmente por admin el {datetime.utcnow().isoformat()}"
    ).strip(" |")

    db.commit()
    db.refresh(commission)
    wallet_sync = sync_ambassador_wallets(db, commission.ambassador_id)

    return {
        "message": "Comisión marcada como pagada correctamente",
        "commission_id": commission.id,
        "status": commission.status,
        "paid_at": commission.paid_at,
        "wallet_sync": wallet_sync,
    }


@router.post("/ambassador/{ambassador_id}/pay-pending")
def pay_pending_commissions_by_ambassador(
    ambassador_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_supervisor_or_superadmin(current_user)

    ambassador = db.query(Ambassador).filter(Ambassador.id == ambassador_id).first()
    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    now = datetime.utcnow()
    pending_commissions = (
        db.query(Commission)
        .filter(
            Commission.ambassador_id == ambassador_id,
            Commission.status == "pending",
            Commission.year <= now.year,
        )
        .order_by(Commission.year.asc(), Commission.month.asc(), Commission.id.asc())
        .all()
    )
    payable_commissions = [
        commission
        for commission in pending_commissions
        if now.day >= 10 and (commission.year, commission.month) < (now.year, now.month)
    ]

    if not pending_commissions:
        created_now = ensure_current_month_pending_commissions_for_ambassador(
            db,
            ambassador,
        )
        if created_now:
            db.commit()
            pending_commissions = (
                db.query(Commission)
                .filter(
                    Commission.ambassador_id == ambassador_id,
                    Commission.status == "pending",
                )
                .order_by(Commission.year.asc(), Commission.month.asc(), Commission.id.asc())
                .all()
            )
            payable_commissions = [
                commission
                for commission in pending_commissions
                if now.day >= 10 and (commission.year, commission.month) < (now.year, now.month)
            ]

    if not payable_commissions:
        wallet_sync = sync_ambassador_wallets(db, ambassador_id)
        return {
            "paid": True,
            "message": (
                "El embajador no tiene corte pagable. "
                "La próxima comisión se paga el día 10 con corte del 1 al 30 del mes anterior."
            ),
            "paid_records": 0,
            "paid_amount": 0,
            "payout_rule": "Pago mensual el día 10. Corte del 1 al 30 del mes anterior.",
            "wallet_sync": wallet_sync,
        }

    paid_total = 0.0
    paid_now = datetime.utcnow()
    for commission in payable_commissions:
        commission.status = "paid"
        commission.paid_at = paid_now
        commission.notes = (
            f"{commission.notes or ''} | Pago administrativo masivo el {paid_now.isoformat()}"
        ).strip(" |")
        paid_total += float(commission.commission_amount or 0)

    db.commit()
    wallet_sync = sync_ambassador_wallets(db, ambassador_id)

    return {
        "paid": True,
        "message": "Corte mensual del embajador pagado correctamente",
        "paid_records": len(payable_commissions),
        "paid_amount": round(paid_total, 2),
        "payout_rule": "Pago mensual el día 10. Corte del 1 al 30 del mes anterior.",
        "wallet_sync": wallet_sync,
    }
