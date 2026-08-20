from datetime import datetime

from sqlalchemy.orm import Session

import models
from commissions import (
    ensure_current_month_pending_commissions_for_ambassador,
    safe_send_ambassador_push,
    sync_ambassador_wallets,
)


PAID_RENEWAL_STATUSES = {"subscription_paid", "verified", "paid", "completed"}
PAID_MEMBERSHIP_TYPES = {
    "signup",
    "membership_initial",
    "subscription",
    "subscription_renewal",
}


def _next_month(month: int, year: int) -> tuple[int, int]:
    return (1, year + 1) if month == 12 else (month + 1, year)


def _selection_has_another_paid_cycle(
    db: Session,
    selection: models.MonthlySelection,
    payment: models.MembershipPayment,
) -> bool:
    return (
        db.query(models.MembershipPayment)
        .filter(
            models.MembershipPayment.monthly_selection_id == selection.id,
            models.MembershipPayment.id != payment.id,
            models.MembershipPayment.payment_type.in_(PAID_MEMBERSHIP_TYPES),
            models.MembershipPayment.status.in_(PAID_RENEWAL_STATUSES | {"subscription_active"}),
        )
        .first()
        is not None
    )


def _clone_selection_for_cycle(
    db: Session,
    user: models.User,
    source: models.MonthlySelection,
    month: int,
    year: int,
) -> models.MonthlySelection:
    existing = (
        db.query(models.MonthlySelection)
        .filter(
            models.MonthlySelection.user_id == user.id,
            models.MonthlySelection.month == month,
            models.MonthlySelection.year == year,
        )
        .first()
    )
    if existing:
        return existing

    selection = models.MonthlySelection(
        user_id=user.id,
        plan_id=source.plan_id,
        month=month,
        year=year,
        status="confirmed",
        editable=True,
    )
    db.add(selection)
    db.flush()

    for item in source.items:
        db.add(
            models.MonthlySelectionItem(
                monthly_selection_id=selection.id,
                product_id=item.product_id,
                quantity=item.quantity or 1,
            )
        )
    db.flush()
    return selection


def _selection_for_renewal(
    db: Session,
    user: models.User,
    payment: models.MembershipPayment,
) -> models.MonthlySelection | None:
    selection = payment.monthly_selection

    if selection and not _selection_has_another_paid_cycle(db, selection, payment):
        return selection

    selections = (
        db.query(models.MonthlySelection)
        .filter(models.MonthlySelection.user_id == user.id)
        .order_by(
            models.MonthlySelection.year.asc(),
            models.MonthlySelection.month.asc(),
            models.MonthlySelection.id.asc(),
        )
        .all()
    )
    for candidate in selections:
        has_order = (
            db.query(models.Order)
            .filter(
                models.Order.user_id == user.id,
                models.Order.month == candidate.month,
                models.Order.year == candidate.year,
            )
            .first()
            is not None
        )
        if has_order or _selection_has_another_paid_cycle(db, candidate, payment):
            continue
        payment.monthly_selection_id = candidate.id
        payment.monthly_selection = candidate
        db.flush()
        return candidate

    latest = (
        db.query(models.MonthlySelection)
        .filter(models.MonthlySelection.user_id == user.id)
        .order_by(
            models.MonthlySelection.year.desc(),
            models.MonthlySelection.month.desc(),
            models.MonthlySelection.id.desc(),
        )
        .first()
    )
    if not latest:
        return None

    month, year = _next_month(latest.month, latest.year)
    selection = _clone_selection_for_cycle(db, user, latest, month, year)
    payment.monthly_selection_id = selection.id
    payment.monthly_selection = selection
    db.flush()
    return selection


def _ensure_logistics_order(
    db: Session,
    user: models.User,
    payment: models.MembershipPayment,
    selection: models.MonthlySelection,
) -> tuple[models.Order | None, str | None]:
    if payment.order_id:
        existing = db.query(models.Order).filter(models.Order.id == payment.order_id).first()
        if existing:
            return existing, None

    existing = (
        db.query(models.Order)
        .filter(
            models.Order.user_id == user.id,
            models.Order.month == selection.month,
            models.Order.year == selection.year,
        )
        .first()
    )
    if existing:
        payment.order_id = existing.id
        return existing, None

    if not selection.items:
        return None, "Selección mensual sin productos: revisar antes de despacho"

    paid_at = payment.paid_at or payment.created_at or datetime.utcnow()
    provider = (getattr(payment, "provider", None) or "pago recurrente").strip().upper()
    order = models.Order(
        order_code=(
            f"MWC-{selection.year}{selection.month:02d}-U{user.id}-"
            f"R{payment.id or int(paid_at.timestamp())}"
        ),
        user_id=user.id,
        monthly_selection_id=selection.id,
        month=selection.month,
        year=selection.year,
        membership_level_snapshot=user.membership_level,
        user_status_snapshot="active",
        city_snapshot=getattr(user, "city", None) or "",
        address_snapshot=getattr(user, "address", None) or "",
        reference_snapshot=getattr(user, "reference", None) or "",
        delivery_notes_snapshot=getattr(user, "delivery_notes", None) or "",
        status="approved_for_logistics",
        logistics_notes=(
            f"RENOVACIÓN {provider} CONFIRMADA | Pago #{payment.id} | "
            f"Ciclo {selection.month:02d}/{selection.year} | "
            "Crear un solo despacho; no duplicar esta orden."
        ),
    )
    db.add(order)
    db.flush()

    for item in selection.items:
        product = item.product
        if not product:
            product = db.query(models.Product).filter(models.Product.id == item.product_id).first()
        db.add(
            models.OrderItem(
                order_id=order.id,
                product_id=item.product_id,
                product_name_snapshot=product.name if product else "Producto por revisar",
                quantity=item.quantity or 1,
            )
        )

    db.add(
        models.OrderTrackingHistory(
            order_id=order.id,
            status="approved_for_logistics",
            note=(
                f"Pago recurrente {provider} #{payment.id} confirmado automáticamente. "
                f"Despacho correspondiente únicamente al ciclo "
                f"{selection.month:02d}/{selection.year}."
            ),
            created_by=None,
        )
    )
    selection.status = "confirmed"
    selection.editable = False
    payment.order_id = order.id
    db.flush()
    return order, None


def reconcile_subscription_renewal(
    db: Session,
    payment: models.MembershipPayment,
    sync_wallet: bool = False,
) -> dict:
    if payment.payment_type != "subscription_renewal":
        return {"processed": False, "reason": "not_subscription_renewal"}
    if payment.status not in PAID_RENEWAL_STATUSES:
        return {"processed": False, "reason": "renewal_not_paid"}

    user = db.query(models.User).filter(models.User.id == payment.user_id).first()
    if not user:
        return {"processed": False, "reason": "user_not_found"}

    user.membership_active = True
    user.is_active = True
    user.status = "active"
    payment.admin_verified = True
    payment.admin_verified_at = payment.admin_verified_at or payment.paid_at or datetime.utcnow()

    selection = _selection_for_renewal(db, user, payment)
    if not selection:
        return {"processed": False, "reason": "monthly_selection_not_found"}

    order, logistics_issue = _ensure_logistics_order(db, user, payment, selection)

    referral = (
        db.query(models.AmbassadorReferral)
        .filter(
            models.AmbassadorReferral.user_id == user.id,
            models.AmbassadorReferral.status == "active",
        )
        .first()
    )
    created_commissions = []
    ambassador_id = None
    if referral:
        ambassador = (
            db.query(models.Ambassador)
            .filter(models.Ambassador.id == referral.ambassador_id)
            .first()
        )
        if ambassador and ambassador.is_active and ambassador.status == "active":
            ambassador_id = ambassador.id
            created_commissions = ensure_current_month_pending_commissions_for_ambassador(
                db,
                ambassador,
                month=selection.month,
                year=selection.year,
            )

    next_month, next_year = _next_month(selection.month, selection.year)
    next_selection = _clone_selection_for_cycle(
        db,
        user,
        selection,
        next_month,
        next_year,
    )

    db.commit()
    db.refresh(payment)

    wallet_sync = None
    ambassador_push = None
    if sync_wallet and ambassador_id:
        wallet_sync = sync_ambassador_wallets(db, ambassador_id)
    if ambassador_id and created_commissions:
        commission_total = sum(
            float(item.commission_amount or 0) for item in created_commissions
        )
        ambassador_push = safe_send_ambassador_push(
            db,
            ambassador_id,
            "Nueva renovación mensual Mayu",
            (
                f"El pago mensual de {user.name} fue confirmado. "
                f"Se acreditó una comisión pendiente de ${commission_total:.2f} USD. "
                "Tu tarjeta digital y saldo ya fueron actualizados."
            ),
        )

    return {
        "processed": True,
        "payment_id": payment.id,
        "cycle_month": selection.month,
        "cycle_year": selection.year,
        "order_id": order.id if order else None,
        "order_status": order.status if order else None,
        "logistics_ready": order is not None,
        "logistics_issue": logistics_issue,
        "commission_created": len(created_commissions),
        "ambassador_id": ambassador_id,
        "next_selection_id": next_selection.id,
        "wallet_sync": wallet_sync,
        "push_notification": ambassador_push,
    }


def reconcile_all_paid_subscription_renewals(db: Session) -> dict:
    payments = (
        db.query(models.MembershipPayment)
        .filter(
            models.MembershipPayment.payment_type == "subscription_renewal",
            models.MembershipPayment.status.in_(PAID_RENEWAL_STATUSES),
        )
        .order_by(models.MembershipPayment.paid_at.asc(), models.MembershipPayment.id.asc())
        .all()
    )

    results = []
    ambassador_ids_to_sync = set()
    for payment in payments:
        try:
            result = reconcile_subscription_renewal(db, payment, sync_wallet=False)
            results.append(result)
            if result.get("processed") and result.get("ambassador_id"):
                ambassador_ids_to_sync.add(result["ambassador_id"])
        except Exception as exc:
            db.rollback()
            results.append(
                {
                    "processed": False,
                    "payment_id": payment.id,
                    "reason": str(exc),
                }
            )

    # Reconciliation can create commissions that predate this deployment.  The
    # ambassador pass is dynamic, so notify every affected Apple/Google Wallet
    # after all database commits have completed.  Sync once per ambassador to
    # avoid duplicate APNs notifications when several renewals are repaired.
    wallet_sync = {}
    for ambassador_id in sorted(ambassador_ids_to_sync):
        wallet_sync[str(ambassador_id)] = sync_ambassador_wallets(db, ambassador_id)

    return {
        "checked": len(payments),
        "processed": sum(1 for item in results if item.get("processed")),
        "items": results,
        "wallet_sync": wallet_sync,
    }
