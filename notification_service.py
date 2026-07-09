import html
import os
from typing import Iterable, Optional

import resend
from sqlalchemy.orm import Session

import models
from marketing import send_push_to_latest_user_token


def safe_send_email(to_email: Optional[str], subject: str, html_body: str) -> bool:
    if not to_email:
        return False

    try:
        api_key = os.getenv("RESEND_API_KEY")
        if not api_key:
            raise Exception("Falta RESEND_API_KEY")

        resend.api_key = api_key
        resend.Emails.send(
            {
                "from": os.getenv("FROM_EMAIL", "onboarding@resend.dev"),
                "to": [to_email],
                "subject": subject,
                "html": html_body,
            }
        )
        return True
    except Exception as exc:
        print(f"[marketplace-notification] email error: {exc}")
        return False


def safe_send_push_to_user(
    db: Session,
    user_id: Optional[int],
    title: str,
    message: str,
) -> bool:
    if not user_id:
        return False

    try:
        send_push_to_latest_user_token(
            db=db,
            user_id=user_id,
            title=title,
            message=message,
        )
        return True
    except Exception as exc:
        print(f"[marketplace-notification] push user {user_id} error: {exc}")
        return False


def safe_send_push_to_roles(
    db: Session,
    roles: Iterable[str],
    title: str,
    message: str,
) -> int:
    users = (
        db.query(models.User)
        .filter(
            models.User.role.in_(set(roles)),
            models.User.is_active == True,
        )
        .all()
    )

    sent = 0
    for user in users:
        if safe_send_push_to_user(db, user.id, title, message):
            sent += 1
    return sent


def _order_items_html(order: models.MarketplaceOrder) -> str:
    rows = []
    for item in order.items:
        rows.append(
            "<tr>"
            f"<td>{html.escape(item.product_name_snapshot)}</td>"
            f"<td>{item.quantity}</td>"
            f"<td>${float(item.unit_price_snapshot or 0):.2f}</td>"
            f"<td>${float(item.total_snapshot or 0):.2f}</td>"
            "</tr>"
        )
    return "".join(rows)


def notify_customer_order(
    db: Session,
    order: models.MarketplaceOrder,
    subject: str,
    message: str,
    include_summary: bool = False,
) -> None:
    email = order.customer_email or order.billing_email
    detail = ""
    doctor_notice = ""
    if include_summary:
        if getattr(order, "doctor_prescriber_identifier", None):
            doctor_notice = f"""
            <div style="margin:16px 0;padding:14px 16px;border-radius:14px;background:#f4f4f1;border:1px solid #d8e6e2">
              <p style="margin:0 0 8px 0"><strong>Doctor afiliado:</strong> {html.escape(order.doctor_prescriber_identifier or "")}</p>
              <p style="margin:0 0 6px 0"><strong>Comisión registrada:</strong> 30% de la compra.</p>
              <p style="margin:0"><strong>Pago administrativo:</strong> mensual, cada día 21.</p>
            </div>
            """
        detail = f"""
        <table style="width:100%;border-collapse:collapse">
          <thead><tr><th>Producto</th><th>Cant.</th><th>Precio</th><th>Total</th></tr></thead>
          <tbody>{_order_items_html(order)}</tbody>
        </table>
        <p>Subtotal: ${float(order.subtotal or 0):.2f}</p>
        <p>Descuento: ${float(order.discount_amount or 0):.2f}</p>
        <p><strong>Total: ${float(order.total or 0):.2f} {order.currency}</strong></p>
        {doctor_notice}
        """

    tracking = ""
    if order.tracking_number or order.tracking_url:
        tracking = f"""
        <p>Transportista: {html.escape(order.carrier or "-")}</p>
        <p>Guía: {html.escape(order.tracking_number or "-")}</p>
        <p><a href="{html.escape(order.tracking_url or "#")}">Ver seguimiento</a></p>
        """

    safe_send_email(
        email,
        subject,
        f"""
        <div style="font-family:Arial,sans-serif;max-width:680px;margin:auto;padding:24px">
          <h2>Farmacia Mayu</h2>
          <p>Hola {html.escape(order.customer_name or "cliente Mayu")},</p>
          <p>{html.escape(message)}</p>
          <p><strong>Pedido: {html.escape(order.order_code)}</strong></p>
          {detail}
          {tracking}
          <p>Gracias por confiar en Mayu Wellness Club.</p>
        </div>
        """,
    )
    safe_send_push_to_user(db, order.user_id, subject, message)


def add_tracking_history(
    db: Session,
    order: models.MarketplaceOrder,
    status: str,
    note: Optional[str] = None,
    created_by: Optional[int] = None,
) -> models.MarketplaceOrderTrackingHistory:
    history = models.MarketplaceOrderTrackingHistory(
        marketplace_order_id=order.id,
        status=status,
        note=note,
        carrier=order.carrier,
        tracking_number=order.tracking_number,
        tracking_url=order.tracking_url,
        created_by=created_by,
    )
    db.add(history)
    return history
