import os
import json
import resend
import requests
import cloudinary
import cloudinary.uploader
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from typing import Optional

from google.oauth2 import service_account
from google.auth.transport.requests import Request

from database import SessionLocal
from dependencies import get_current_user
from models import (
    User,
    MarketingCampaign,
    MarketingCampaignRecipient,
    MarketingEvent,
    PushNotificationToken,
    MarketplaceOrder,
    EducationOrder,
)

router = APIRouter(prefix="/marketing", tags=["marketing"])

CRON_SECRET = os.getenv("MARKETING_CRON_SECRET")

VALID_CHANNELS = {"push", "email", "whatsapp"}
VALID_TARGET_GROUPS = {
    "members",
    "active_members",
    "inactive_members",
    "ambassadors",
    "pharmacy_marketplace",
    "education_marketplace",
}
VALID_CAMPAIGN_STATUS = {"draft", "scheduled", "sent"}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_marketing_user(current_user: User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role != "marketing":
        raise HTTPException(status_code=403, detail="Acceso solo para marketing")


def configure_cloudinary():
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    api_key = os.getenv("CLOUDINARY_API_KEY")
    api_secret = os.getenv("CLOUDINARY_API_SECRET")

    if not cloud_name or not api_key or not api_secret:
        raise Exception(
            "Faltan CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY o CLOUDINARY_API_SECRET en Render"
        )

    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,
    )


def send_marketing_email(
    to_email: str,
    subject: str,
    message: str,
    image_url: Optional[str] = None,
):
    resend_api_key = os.getenv("RESEND_API_KEY")
    from_email = os.getenv("FROM_EMAIL", "onboarding@resend.dev")

    if not resend_api_key:
        raise Exception("Falta RESEND_API_KEY en Render")

    if not to_email:
        raise Exception("Usuario sin email")

    resend.api_key = resend_api_key

    image_html = ""
    if image_url:
        image_html = f"""
        <div style="margin:24px 0;">
            <img
                src="{image_url}"
                alt="Flyer Mayu Wellness Club"
                style="display:block; width:100%; max-width:620px; height:auto; border-radius:16px; margin:auto;"
            />
        </div>
        """

    resend.Emails.send({
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "html": f"""
        <div style="font-family:Arial,sans-serif; max-width:620px; margin:auto; padding:24px;">
            <h2 style="margin-bottom:16px;">Mayu Wellness Club</h2>
            {image_html}
            <div style="font-size:16px; line-height:1.7; white-space:pre-line;">
                {message}
            </div>
            <br>
            <p>Equipo Mayu Wellness Club</p>
        </div>
        """,
    })


def get_firebase_access_token():
    firebase_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")

    if not firebase_json:
        raise Exception("Falta FIREBASE_SERVICE_ACCOUNT_JSON en Render")

    try:
        service_account_info = json.loads(firebase_json)
    except Exception as e:
        raise Exception(f"FIREBASE_SERVICE_ACCOUNT_JSON inválido: {str(e)}")

    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/firebase.messaging"],
    )

    credentials.refresh(Request())

    if not credentials.token:
        raise Exception("No se pudo generar access token de Firebase")

    return credentials.token


def send_push_notification(
    token: str,
    title: str,
    message: str,
    image_url: Optional[str] = None,
):
    project_id = os.getenv("FIREBASE_PROJECT_ID")
    bundle_id = os.getenv("APPLE_BUNDLE_ID", "com.mayu.wellnessclub")

    if not project_id:
        raise Exception("Falta FIREBASE_PROJECT_ID en Render")

    if not token:
        raise Exception("Token push vacío")

    access_token = get_firebase_access_token()

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

    notification = {
        "title": title,
        "body": message,
    }

    if image_url:
        notification["image"] = image_url

    payload = {
        "message": {
            "token": token,
            "notification": notification,
            "data": {
                "title": title,
                "message": message,
                "image_url": image_url or "",
                "source": "mayu_marketing",
                "click_action": "FLUTTER_NOTIFICATION_CLICK",
            },
            "apns": {
                "headers": {
                    "apns-priority": "10",
                    "apns-topic": bundle_id,
                },
                "payload": {
                    "aps": {
                        "alert": {
                            "title": title,
                            "body": message,
                        },
                        "sound": "default",
                        "badge": 1,
                    }
                },
            },
        }
    }

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=20,
    )

    if response.status_code >= 400:
        raise Exception(
            f"Firebase push error | project_id={project_id} | bundle_id={bundle_id} | response={response.text}"
        )

    return response.json()


class MarketingCampaignCreateRequest(BaseModel):
    title: str
    message: str
    subject: Optional[str] = None
    image_url: Optional[str] = None
    channel: str = "push"
    target_group: str = "members"
    status: str = "draft"
    scheduled_at: Optional[datetime] = None


class MarketingCampaignUpdateRequest(BaseModel):
    title: Optional[str] = None
    message: Optional[str] = None
    subject: Optional[str] = None
    image_url: Optional[str] = None
    channel: Optional[str] = None
    target_group: Optional[str] = None
    status: Optional[str] = None
    scheduled_at: Optional[datetime] = None


class MarketingSendRequest(BaseModel):
    campaign_id: int


class PushTokenRequest(BaseModel):
    token: str
    platform: Optional[str] = None


class PushTestRequest(BaseModel):
    title: str = "Prueba Push Mayu"
    message: str = "Notificación push de prueba funcionando."
    image_url: Optional[str] = None


def campaign_to_dict(campaign: MarketingCampaign):
    return {
        "id": campaign.id,
        "title": campaign.title,
        "subject": campaign.subject,
        "message": campaign.message,
        "image_url": getattr(campaign, "image_url", None),
        "channel": campaign.channel,
        "target_group": campaign.target_group,
        "audience": campaign.target_group,
        "status": campaign.status,
        "created_by": campaign.created_by,
        "created_at": campaign.created_at,
        "scheduled_at": campaign.scheduled_at,
        "sent_at": campaign.sent_at,
    }


def recipient_to_dict(recipient: MarketingCampaignRecipient):
    return {
        "id": recipient.id,
        "campaign_id": recipient.campaign_id,
        "user_id": recipient.user_id,
        "user_name": recipient.name_snapshot,
        "user_email": recipient.email_snapshot,
        "user_phone": recipient.phone_snapshot,
        "role": recipient.role_snapshot,
        "channel_status": recipient.delivery_status,
        "status": recipient.delivery_status,
        "sent_at": recipient.sent_at,
        "opened_at": recipient.opened_at,
        "clicked_at": recipient.clicked_at,
        "read_at": recipient.read_at,
        "opened": recipient.opened_at is not None,
        "clicked": recipient.clicked_at is not None,
        "read": recipient.read_at is not None,
        "error_message": recipient.error_message,
        "whatsapp_url": build_marketing_whatsapp_url(
            recipient.phone_snapshot,
            recipient.campaign.message if recipient.campaign else None,
        )
        if recipient.campaign and recipient.campaign.channel == "whatsapp"
        else None,
    }


def normalize_whatsapp_phone(phone: Optional[str]):
    if not phone:
        return None

    digits = "".join(ch for ch in str(phone) if ch.isdigit())

    if not digits:
        return None

    if digits.startswith("00"):
        digits = digits[2:]

    if digits.startswith("593"):
        return digits

    if digits.startswith("0") and len(digits) >= 9:
        return "593" + digits[1:]

    if len(digits) == 9:
        return "593" + digits

    return digits


def build_marketing_whatsapp_url(phone: Optional[str], message: Optional[str] = None):
    normalized = normalize_whatsapp_phone(phone)
    if not normalized:
        return None

    url = f"https://wa.me/{normalized}"
    if message:
        url += f"?text={quote(message)}"
    return url


def make_user_contact(user: User, source: str = "mayu_wellness"):
    return {
        "id": user.id,
        "user_id": user.id,
        "name": user.name,
        "role": user.role,
        "membership_active": user.membership_active,
        "email": user.email,
        "phone": user.phone,
        "source": source,
    }


def make_order_contact(
    *,
    source: str,
    order_id: int,
    order_code: str,
    name: str,
    email: Optional[str],
    phone: Optional[str],
    total: Optional[float],
    paid_at: Optional[datetime],
    user_id: Optional[int] = None,
):
    return {
        "id": f"{source}:{order_id}",
        "user_id": user_id,
        "name": name or "Cliente Mayu",
        "role": source,
        "membership_active": None,
        "email": email,
        "phone": phone,
        "source": source,
        "order_id": order_id,
        "order_code": order_code,
        "total": total,
        "paid_at": paid_at,
    }


def dedupe_contacts(contacts):
    seen = set()
    unique = []

    for contact in contacts:
        email = (contact.get("email") or "").strip().lower()
        phone = normalize_whatsapp_phone(contact.get("phone")) or ""
        key = email or phone or str(contact.get("id"))

        if key in seen:
            continue

        seen.add(key)
        unique.append(contact)

    return unique


def contact_to_dict(contact, channel: str, message: Optional[str] = None):
    if isinstance(contact, User):
        contact = make_user_contact(contact)

    email = contact.get("email")
    phone = contact.get("phone")

    return {
        "id": contact.get("id"),
        "user_id": contact.get("user_id"),
        "name": contact.get("name"),
        "role": contact.get("role"),
        "membership_active": contact.get("membership_active"),
        "email": email,
        "phone": phone,
        "contact": email if channel == "email" else phone,
        "channel": channel,
        "source": contact.get("source"),
        "order_id": contact.get("order_id"),
        "order_code": contact.get("order_code"),
        "total": contact.get("total"),
        "paid_at": contact.get("paid_at"),
        "whatsapp_url": build_marketing_whatsapp_url(phone, message)
        if channel == "whatsapp"
        else None,
    }


def get_audience_users(db: Session, target_group: str):
    if target_group in {"members", "active_members", "inactive_members", "ambassadors"}:
        query = db.query(User).filter(User.is_active == True)

        if target_group == "members":
            query = query.filter(User.role == "member")
        elif target_group == "active_members":
            query = query.filter(User.role == "member", User.membership_active == True)
        elif target_group == "inactive_members":
            query = query.filter(User.role == "member", User.membership_active == False)
        elif target_group == "ambassadors":
            query = query.filter(User.role == "ambassador")

        return [make_user_contact(user) for user in query.order_by(User.name.asc()).all()]

    if target_group == "pharmacy_marketplace":
        orders = (
            db.query(MarketplaceOrder)
            .filter(MarketplaceOrder.payment_status == "paid")
            .order_by(MarketplaceOrder.created_at.desc())
            .all()
        )

        return dedupe_contacts([
            make_order_contact(
                source="pharmacy_marketplace",
                order_id=order.id,
                order_code=order.order_code,
                name=order.customer_name,
                email=order.customer_email or order.billing_email,
                phone=order.customer_phone or order.billing_phone,
                total=order.total,
                paid_at=order.paid_at,
                user_id=order.user_id,
            )
            for order in orders
        ])

    if target_group == "education_marketplace":
        orders = (
            db.query(EducationOrder)
            .filter(EducationOrder.payment_status == "paid")
            .order_by(EducationOrder.created_at.desc())
            .all()
        )

        return dedupe_contacts([
            make_order_contact(
                source="education_marketplace",
                order_id=order.id,
                order_code=order.order_code,
                name=order.buyer_name,
                email=order.buyer_email,
                phone=order.buyer_phone,
                total=order.total,
                paid_at=order.paid_at,
                user_id=order.user_id,
            )
            for order in orders
        ])

    raise HTTPException(status_code=400, detail="Audiencia inválida")


def add_marketing_event(
    db: Session,
    campaign_id: int,
    event_type: str,
    channel: str,
    recipient_id: Optional[int] = None,
    user_id: Optional[int] = None,
    metadata: Optional[str] = None,
):
    event = MarketingEvent(
        campaign_id=campaign_id,
        recipient_id=recipient_id,
        user_id=user_id,
        event_type=event_type,
        channel=channel,
        event_metadata=metadata,
        created_at=datetime.utcnow(),
    )
    db.add(event)


def deactivate_invalid_token_if_needed(push_token: PushNotificationToken, error_text: str):
    lowered = error_text.lower()

    if (
        "not found" in lowered
        or "unregistered" in lowered
        or "registration-token-not-registered" in lowered
        or "requested entity was not found" in lowered
        or "invalid registration" in lowered
    ):
        push_token.is_active = False
        push_token.updated_at = datetime.utcnow()


def send_push_to_latest_user_token(
    db: Session,
    user_id: int,
    title: str,
    message: str,
    image_url: Optional[str] = None,
):
    push_token = (
        db.query(PushNotificationToken)
        .filter(
            PushNotificationToken.user_id == user_id,
            PushNotificationToken.is_active == True,
        )
        .order_by(PushNotificationToken.updated_at.desc())
        .first()
    )

    if not push_token:
        raise Exception("Usuario sin token push registrado")

    try:
        result = send_push_notification(
            token=push_token.token,
            title=title,
            message=message,
            image_url=image_url,
        )

        return {
            "token_id": push_token.id,
            "firebase_result": result,
        }

    except Exception as e:
        deactivate_invalid_token_if_needed(push_token, str(e))
        raise


def send_campaign_now(db: Session, campaign: MarketingCampaign):
    contacts = get_audience_users(db, campaign.target_group)

    created_recipients = 0
    total_success = 0
    total_errors = 0

    for contact in contacts:
        user_id = contact.get("user_id")
        email = contact.get("email")
        phone = contact.get("phone")
        name = contact.get("name") or "Cliente Mayu"
        role = contact.get("role") or contact.get("source") or campaign.target_group

        existing_query = db.query(MarketingCampaignRecipient).filter(
            MarketingCampaignRecipient.campaign_id == campaign.id,
        )

        if user_id:
            existing_query = existing_query.filter(
                MarketingCampaignRecipient.user_id == user_id,
            )
        elif email:
            existing_query = existing_query.filter(
                MarketingCampaignRecipient.email_snapshot == email,
            )
        elif phone:
            existing_query = existing_query.filter(
                MarketingCampaignRecipient.phone_snapshot == phone,
            )

        existing_recipient = existing_query.first()

        if existing_recipient:
            continue

        delivery_status = "sent"
        error_message = None
        sent_at = datetime.utcnow()

        try:
            if campaign.channel == "email":
                send_marketing_email(
                    to_email=email,
                    subject=campaign.subject or campaign.title,
                    message=campaign.message,
                    image_url=getattr(campaign, "image_url", None),
                )
                total_success += 1

            elif campaign.channel == "push":
                if not user_id:
                    raise Exception(
                        "Este contacto no está enlazado a usuario de app con token push"
                    )

                send_push_to_latest_user_token(
                    db=db,
                    user_id=user_id,
                    title=campaign.title,
                    message=campaign.message,
                    image_url=getattr(campaign, "image_url", None),
                )
                total_success += 1

            elif campaign.channel == "whatsapp":
                if not build_marketing_whatsapp_url(phone, campaign.message):
                    raise Exception("Contacto sin teléfono válido para WhatsApp")
                delivery_status = "ready"
                total_success += 1

        except Exception as e:
            delivery_status = "error"
            error_message = str(e)
            sent_at = None
            total_errors += 1

        recipient = MarketingCampaignRecipient(
            campaign_id=campaign.id,
            user_id=user_id,
            name_snapshot=name,
            email_snapshot=email,
            phone_snapshot=phone,
            role_snapshot=role,
            delivery_status=delivery_status,
            sent_at=sent_at,
            error_message=error_message,
        )

        db.add(recipient)
        db.flush()

        add_marketing_event(
            db=db,
            campaign_id=campaign.id,
            recipient_id=recipient.id,
            user_id=user_id,
            event_type=delivery_status,
            channel=campaign.channel,
            metadata=error_message,
        )

        created_recipients += 1

    campaign.status = "sent"
    campaign.sent_at = datetime.utcnow()

    return {
        "campaign_id": campaign.id,
        "channel": campaign.channel,
        "target_group": campaign.target_group,
        "total_recipients": len(contacts),
        "new_recipients_created": created_recipients,
        "total_success": total_success,
        "total_errors": total_errors,
    }


def process_scheduled_campaigns(db: Session):
    now = datetime.utcnow()

    campaigns = (
        db.query(MarketingCampaign)
        .filter(
            MarketingCampaign.status == "scheduled",
            MarketingCampaign.scheduled_at.isnot(None),
            MarketingCampaign.scheduled_at <= now,
        )
        .order_by(MarketingCampaign.scheduled_at.asc())
        .all()
    )

    results = []

    for campaign in campaigns:
        result = send_campaign_now(db, campaign)
        results.append(result)

    return results


def send_birthday_email(to_email: str, user_name: str):
    subject = "🎉 Feliz cumpleaños de parte de Mayu Wellness Club"

    message = f"""
Hola {user_name},

Hoy queremos desearte un muy feliz cumpleaños 🎉

Gracias por ser parte de Mayu Wellness Club.
Que este nuevo ciclo esté lleno de salud, bienestar, energía y conexión con la vida.

Con cariño,
Equipo Mayu Wellness Club
"""

    send_marketing_email(
        to_email=to_email,
        subject=subject,
        message=message,
        image_url=None,
    )


def member_level_info(level: int | None):
    plans = {
        1: {
            "name": "Nivel 1 - Cobre",
            "price": 40.00,
            "benefits": [
                "Selección mensual de productos Mayu Wellness Club.",
                "Tarjeta digital Mayu con acceso a tu membresía.",
                "Contenido educativo para crear hábitos de salud diarios.",
                "Despacho mensual según el calendario operativo del club.",
            ],
        },
        2: {
            "name": "Nivel 2 - Plata",
            "price": 50.00,
            "benefits": [
                "Selección mensual ampliada de productos Mayu Wellness Club.",
                "Tarjeta digital Mayu con beneficios activos.",
                "Contenido educativo y acompañamiento para sostener hábitos saludables.",
                "Despacho mensual según el calendario operativo del club.",
            ],
        },
        3: {
            "name": "Nivel 3 - Oro",
            "price": 60.00,
            "benefits": [
                "Selección mensual premium de productos Mayu Wellness Club.",
                "Tarjeta digital Mayu con beneficios activos.",
                "Contenido educativo y bienestar continuo para tu rutina diaria.",
                "Despacho mensual según el calendario operativo del club.",
            ],
        },
    }

    return plans.get(level) or {
        "name": "Mayu Wellness Club",
        "price": 0.00,
        "benefits": [
            "Membresía activa Mayu Wellness Club.",
            "Acceso a beneficios digitales y comunicación del club.",
        ],
    }


def build_welcome_email_message(user: User):
    level_info = member_level_info(user.membership_level)
    benefits = "\n".join(f"- {benefit}" for benefit in level_info["benefits"])

    return f"""
Hola {user.name},

Bienvenido a Mayu Wellness Club.

Tu membresía quedó activa en {level_info["name"]}.
Valor mensual del plan: ${level_info["price"]:.2f}.

La salud es nuestros hábitos de todos los días. Por eso, tu club está pensado para acompañarte mes a mes con productos, educación y una rutina más consciente.

Beneficios de tu plan:
{benefits}

Cada mes podrás revisar o cambiar tu selección de productos según las reglas del club. Cuando tu pago mensual esté confirmado, se genera tu orden y el equipo Mayu la prepara para despacho.

Gracias por ser parte de Mayu Wellness Club.

Equipo Mayu Wellness Club
"""


def send_welcome_email(to_email: str, user: User):
    level_info = member_level_info(user.membership_level)
    send_marketing_email(
        to_email=to_email,
        subject=f"Bienvenido a Mayu Wellness Club - {level_info['name']}",
        message=build_welcome_email_message(user),
        image_url=None,
    )


def send_welcome_member_notifications(db: Session, user: User, trigger: str = "membership_active"):
    if not user or user.role != "member" or not user.membership_active:
        return {
            "sent": False,
            "reason": "Usuario no elegible para bienvenida",
        }

    level_info = member_level_info(user.membership_level)
    campaign_title = f"Bienvenida Mayu user:{user.id} level:{user.membership_level}"
    campaign = (
        db.query(MarketingCampaign)
        .filter(MarketingCampaign.title == campaign_title)
        .first()
    )

    if campaign:
        existing_recipient = (
            db.query(MarketingCampaignRecipient)
            .filter(
                MarketingCampaignRecipient.campaign_id == campaign.id,
                MarketingCampaignRecipient.user_id == user.id,
            )
            .first()
        )
        if existing_recipient:
            return {
                "sent": False,
                "reason": "Bienvenida ya enviada",
                "campaign_id": campaign.id,
                "recipient_id": existing_recipient.id,
            }

    if not campaign:
        campaign = MarketingCampaign(
            title=campaign_title,
            subject=f"Bienvenido a Mayu Wellness Club - {level_info['name']}",
            message=build_welcome_email_message(user),
            image_url=None,
            channel="email",
            target_group="active_members",
            status="sent",
            created_by=None,
            created_at=datetime.utcnow(),
            sent_at=datetime.utcnow(),
        )
        db.add(campaign)
        db.flush()

    recipient = MarketingCampaignRecipient(
        campaign_id=campaign.id,
        user_id=user.id,
        name_snapshot=user.name,
        email_snapshot=user.email,
        phone_snapshot=user.phone,
        role_snapshot=user.role,
        delivery_status="pending",
        sent_at=None,
        error_message=None,
    )
    db.add(recipient)
    db.flush()

    errors = []
    email_sent = False
    push_sent = False

    try:
        send_welcome_email(user.email, user)
        email_sent = True
        add_marketing_event(
            db=db,
            campaign_id=campaign.id,
            recipient_id=recipient.id,
            user_id=user.id,
            event_type="welcome_email_sent",
            channel="email",
            metadata=trigger,
        )
    except Exception as exc:
        errors.append(f"email: {str(exc)}")
        add_marketing_event(
            db=db,
            campaign_id=campaign.id,
            recipient_id=recipient.id,
            user_id=user.id,
            event_type="welcome_email_error",
            channel="email",
            metadata=str(exc),
        )

    try:
        send_push_to_latest_user_token(
            db=db,
            user_id=user.id,
            title="Bienvenido a Mayu Wellness Club",
            message=(
                f"Tu {level_info['name']} está activo. "
                "La salud es nuestros hábitos de todos los días."
            ),
            image_url=None,
        )
        push_sent = True
        add_marketing_event(
            db=db,
            campaign_id=campaign.id,
            recipient_id=recipient.id,
            user_id=user.id,
            event_type="welcome_push_sent",
            channel="push",
            metadata=trigger,
        )
    except Exception as exc:
        errors.append(f"push: {str(exc)}")
        add_marketing_event(
            db=db,
            campaign_id=campaign.id,
            recipient_id=recipient.id,
            user_id=user.id,
            event_type="welcome_push_error",
            channel="push",
            metadata=str(exc),
        )

    if errors and (email_sent or push_sent):
        recipient.delivery_status = "partial_error"
    elif errors:
        recipient.delivery_status = "error"
    else:
        recipient.delivery_status = "sent"

    recipient.error_message = " | ".join(errors) if errors else None
    recipient.sent_at = datetime.utcnow()
    campaign.status = "sent"
    campaign.sent_at = datetime.utcnow()

    return {
        "sent": email_sent or push_sent,
        "email_sent": email_sent,
        "push_sent": push_sent,
        "errors": errors,
        "campaign_id": campaign.id,
        "recipient_id": recipient.id,
    }


def process_welcome_notifications(db: Session):
    users = (
        db.query(User)
        .filter(
            User.role == "member",
            User.is_active == True,
            User.membership_active == True,
            User.membership_level.in_([1, 2, 3]),
        )
        .order_by(User.id.asc())
        .all()
    )

    processed = []
    sent_count = 0
    skipped_count = 0

    for user in users:
        result = send_welcome_member_notifications(
            db=db,
            user=user,
            trigger="welcome_cron",
        )
        processed.append({"user_id": user.id, **result})
        if result.get("sent"):
            sent_count += 1
        else:
            skipped_count += 1

    return {
        "eligible_users": len(users),
        "sent": sent_count,
        "skipped": skipped_count,
        "items": processed,
    }


def process_birthday_notifications(db: Session):
    today = datetime.utcnow().date()

    users = (
        db.query(User)
        .filter(
            User.is_active == True,
            User.birth_date.isnot(None),
        )
        .all()
    )

    birthday_users = []

    for user in users:
        if user.birth_date.month == today.month and user.birth_date.day == today.day:
            birthday_users.append(user)

    campaign_title = f"Cumpleaños automático {today.isoformat()}"

    campaign = (
        db.query(MarketingCampaign)
        .filter(MarketingCampaign.title == campaign_title)
        .first()
    )

    if not campaign:
        campaign = MarketingCampaign(
            title=campaign_title,
            subject="🎉 Feliz cumpleaños de parte de Mayu Wellness Club",
            message="Mensaje automático de cumpleaños para socios Mayu Wellness Club.",
            image_url=None,
            channel="email",
            target_group="members",
            status="sent",
            created_by=None,
            created_at=datetime.utcnow(),
            sent_at=datetime.utcnow(),
        )

        db.add(campaign)
        db.flush()

    total_email_success = 0
    total_push_success = 0
    total_errors = 0
    skipped_existing = 0

    for user in birthday_users:
        existing_recipient = (
            db.query(MarketingCampaignRecipient)
            .filter(
                MarketingCampaignRecipient.campaign_id == campaign.id,
                MarketingCampaignRecipient.user_id == user.id,
            )
            .first()
        )

        if existing_recipient:
            skipped_existing += 1
            continue

        recipient = MarketingCampaignRecipient(
            campaign_id=campaign.id,
            user_id=user.id,
            name_snapshot=user.name,
            email_snapshot=user.email,
            phone_snapshot=user.phone,
            role_snapshot=user.role,
            delivery_status="pending",
            sent_at=None,
            error_message=None,
        )

        db.add(recipient)
        db.flush()

        errors = []

        try:
            send_birthday_email(
                to_email=user.email,
                user_name=user.name,
            )

            total_email_success += 1

            add_marketing_event(
                db=db,
                campaign_id=campaign.id,
                recipient_id=recipient.id,
                user_id=user.id,
                event_type="birthday_email_sent",
                channel="email",
                metadata=None,
            )

        except Exception as e:
            errors.append(f"email: {str(e)}")

            add_marketing_event(
                db=db,
                campaign_id=campaign.id,
                recipient_id=recipient.id,
                user_id=user.id,
                event_type="birthday_email_error",
                channel="email",
                metadata=str(e),
            )

        try:
            send_push_to_latest_user_token(
                db=db,
                user_id=user.id,
                title="🎉 ¡Feliz cumpleaños!",
                message=f"Hola {user.name}, Mayu Wellness Club te desea un día lleno de salud y bienestar.",
                image_url=None,
            )

            total_push_success += 1

            add_marketing_event(
                db=db,
                campaign_id=campaign.id,
                recipient_id=recipient.id,
                user_id=user.id,
                event_type="birthday_push_sent",
                channel="push",
                metadata=None,
            )

        except Exception as e:
            errors.append(f"push: {str(e)}")

            add_marketing_event(
                db=db,
                campaign_id=campaign.id,
                recipient_id=recipient.id,
                user_id=user.id,
                event_type="birthday_push_error",
                channel="push",
                metadata=str(e),
            )

        if errors:
            total_errors += 1
            recipient.delivery_status = "partial_error"
            recipient.error_message = " | ".join(errors)
        else:
            recipient.delivery_status = "sent"

        recipient.sent_at = datetime.utcnow()

    campaign.status = "sent"
    campaign.sent_at = datetime.utcnow()

    return {
        "birthday_users": len(birthday_users),
        "email_success": total_email_success,
        "push_success": total_push_success,
        "errors": total_errors,
        "skipped_existing": skipped_existing,
        "campaign_id": campaign.id,
    }


@router.post("/birthday/cron/run")
def run_birthday_cron(
    secret: str,
    db: Session = Depends(get_db),
):
    cron_secret = os.getenv("MARKETING_CRON_SECRET")

    if not cron_secret:
        raise HTTPException(
            status_code=500,
            detail="Falta MARKETING_CRON_SECRET en Render",
        )

    if secret != cron_secret:
        raise HTTPException(
            status_code=401,
            detail="No autorizado",
        )

    result = process_birthday_notifications(db)

    db.commit()

    return {
        "message": "Cumpleaños procesados correctamente",
        **result,
    }


@router.post("/welcome/cron/run")
def run_welcome_cron(
    secret: str,
    db: Session = Depends(get_db),
):
    cron_secret = os.getenv("MARKETING_CRON_SECRET")

    if not cron_secret:
        raise HTTPException(
            status_code=500,
            detail="Falta MARKETING_CRON_SECRET en Render",
        )

    if secret != cron_secret:
        raise HTTPException(
            status_code=401,
            detail="No autorizado",
        )

    result = process_welcome_notifications(db)

    db.commit()

    return {
        "message": "Bienvenidas procesadas correctamente",
        **result,
    }


@router.post("/upload-image")
def upload_marketing_image(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_user(current_user)

    if not file:
        raise HTTPException(status_code=400, detail="Archivo requerido")

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=400,
            detail=f"Solo se permiten imágenes. Tipo recibido: {file.content_type}",
        )

    try:
        configure_cloudinary()

        result = cloudinary.uploader.upload(
            file.file,
            folder="mayu/marketing",
            resource_type="image",
            overwrite=True,
        )

        image_url = result.get("secure_url")

        if not image_url:
            raise Exception("Cloudinary no devolvió secure_url")

        return {
            "message": "Imagen subida correctamente",
            "image_url": image_url,
            "public_id": result.get("public_id"),
        }

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo subir la imagen: {str(e)}",
        )


@router.post("/push-token")
def save_push_token(
    payload: PushTokenRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not payload.token or not payload.token.strip():
        raise HTTPException(status_code=400, detail="Token push requerido")

    clean_token = payload.token.strip()

    old_tokens = (
        db.query(PushNotificationToken)
        .filter(
            PushNotificationToken.user_id == current_user.id,
            PushNotificationToken.token != clean_token,
        )
        .all()
    )

    for item in old_tokens:
        item.is_active = False
        item.updated_at = datetime.utcnow()

    existing = (
        db.query(PushNotificationToken)
        .filter(PushNotificationToken.token == clean_token)
        .first()
    )

    if existing:
        existing.user_id = current_user.id
        existing.platform = payload.platform
        existing.is_active = True
        existing.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(existing)

        return {
            "message": "Token push actualizado",
            "token_id": existing.id,
        }

    push_token = PushNotificationToken(
        user_id=current_user.id,
        token=clean_token,
        platform=payload.platform,
        is_active=True,
    )

    db.add(push_token)
    db.commit()
    db.refresh(push_token)

    return {
        "message": "Token push guardado",
        "token_id": push_token.id,
    }


@router.post("/push-test-me")
def push_test_me(
    payload: PushTestRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    active_tokens = (
        db.query(PushNotificationToken)
        .filter(
            PushNotificationToken.user_id == current_user.id,
            PushNotificationToken.is_active == True,
        )
        .order_by(PushNotificationToken.updated_at.desc())
        .all()
    )

    if not active_tokens:
        raise HTTPException(
            status_code=404,
            detail="Este usuario no tiene token push activo",
        )

    latest_token = active_tokens[0]

    disabled_old_tokens = 0
    for old_token in active_tokens[1:]:
        old_token.is_active = False
        old_token.updated_at = datetime.utcnow()
        disabled_old_tokens += 1

    try:
        firebase_result = send_push_notification(
            token=latest_token.token,
            title=payload.title,
            message=payload.message,
            image_url=payload.image_url,
        )

        db.commit()

        return {
            "message": "Prueba push ejecutada",
            "user_id": current_user.id,
            "latest_token_id": latest_token.id,
            "tokens_found": len(active_tokens),
            "old_tokens_disabled": disabled_old_tokens,
            "sent": 1,
            "errors": [],
            "firebase_result": firebase_result,
        }

    except Exception as e:
        error_message = str(e)
        deactivate_invalid_token_if_needed(latest_token, error_message)
        db.commit()

        return {
            "message": "Prueba push ejecutada",
            "user_id": current_user.id,
            "latest_token_id": latest_token.id,
            "tokens_found": len(active_tokens),
            "old_tokens_disabled": disabled_old_tokens,
            "sent": 0,
            "errors": [error_message],
        }


@router.post("/push-tokens/clear-me")
def clear_my_push_tokens(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tokens = (
        db.query(PushNotificationToken)
        .filter(PushNotificationToken.user_id == current_user.id)
        .all()
    )

    for item in tokens:
        item.is_active = False
        item.updated_at = datetime.utcnow()

    db.commit()

    return {
        "message": "Tokens push desactivados",
        "user_id": current_user.id,
        "tokens_disabled": len(tokens),
    }


@router.get("/test")
def marketing_test():
    return {
        "message": "marketing router ok",
        "available_channels": list(VALID_CHANNELS),
        "available_audiences": list(VALID_TARGET_GROUPS),
        "available_status": list(VALID_CAMPAIGN_STATUS),
        "note": "Módulo marketing con campañas, mailing, push, flyer, prueba directa y limpieza de tokens.",
    }


@router.get("/dashboard")
def marketing_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_user(current_user)

    total_campaigns = db.query(MarketingCampaign).count()
    total_scheduled = db.query(MarketingCampaign).filter(
        MarketingCampaign.status == "scheduled"
    ).count()

    total_push = db.query(MarketingCampaign).filter(MarketingCampaign.channel == "push").count()
    total_email = db.query(MarketingCampaign).filter(MarketingCampaign.channel == "email").count()
    total_whatsapp = db.query(MarketingCampaign).filter(MarketingCampaign.channel == "whatsapp").count()

    total_sent = db.query(MarketingCampaignRecipient).filter(MarketingCampaignRecipient.sent_at.isnot(None)).count()
    total_opened = db.query(MarketingCampaignRecipient).filter(MarketingCampaignRecipient.opened_at.isnot(None)).count()
    total_clicked = db.query(MarketingCampaignRecipient).filter(MarketingCampaignRecipient.clicked_at.isnot(None)).count()
    total_read = db.query(MarketingCampaignRecipient).filter(MarketingCampaignRecipient.read_at.isnot(None)).count()

    total_push_tokens = db.query(PushNotificationToken).filter(
        PushNotificationToken.is_active == True
    ).count()

    return {
        "total_campaigns": total_campaigns,
        "total_scheduled": total_scheduled,
        "total_push": total_push,
        "total_email": total_email,
        "total_whatsapp": total_whatsapp,
        "total_sent": total_sent,
        "total_opened": total_opened,
        "total_clicked": total_clicked,
        "total_read": total_read,
        "total_push_tokens": total_push_tokens,
        "open_rate": round((total_opened / total_sent) * 100, 2) if total_sent else 0,
        "click_rate": round((total_clicked / total_sent) * 100, 2) if total_sent else 0,
        "read_rate": round((total_read / total_sent) * 100, 2) if total_sent else 0,
    }


@router.get("/audience-preview")
def audience_preview(
    channel: str = "email",
    target_group: str = "members",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_user(current_user)

    if channel not in VALID_CHANNELS:
        raise HTTPException(status_code=400, detail="Canal inválido")

    if target_group not in VALID_TARGET_GROUPS:
        raise HTTPException(status_code=400, detail="Audiencia inválida")

    users = get_audience_users(db, target_group)

    return {
        "channel": channel,
        "target_group": target_group,
        "total_contacts": len(users),
        "items": [contact_to_dict(user, channel, message=None) for user in users],
    }


@router.post("/campaigns")
def create_campaign(
    payload: MarketingCampaignCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_user(current_user)

    if payload.channel not in VALID_CHANNELS:
        raise HTTPException(status_code=400, detail="Canal inválido")

    if payload.target_group not in VALID_TARGET_GROUPS:
        raise HTTPException(status_code=400, detail="Audiencia inválida")

    if payload.status not in VALID_CAMPAIGN_STATUS:
        raise HTTPException(status_code=400, detail="Estado inválido")

    final_status = "scheduled" if payload.scheduled_at is not None else payload.status

    campaign = MarketingCampaign(
        title=payload.title.strip(),
        subject=payload.subject.strip() if payload.subject else None,
        message=payload.message.strip(),
        image_url=payload.image_url.strip() if payload.image_url else None,
        channel=payload.channel,
        target_group=payload.target_group,
        status=final_status,
        scheduled_at=payload.scheduled_at,
        created_by=current_user.id,
    )

    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    return {
        "message": "Campaña creada correctamente",
        "campaign": campaign_to_dict(campaign),
    }


@router.get("/campaigns")
def get_campaigns(
    channel: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_user(current_user)

    query = db.query(MarketingCampaign)

    if channel:
        if channel not in VALID_CHANNELS:
            raise HTTPException(status_code=400, detail="Canal inválido")
        query = query.filter(MarketingCampaign.channel == channel)

    if status:
        if status not in VALID_CAMPAIGN_STATUS:
            raise HTTPException(status_code=400, detail="Estado inválido")
        query = query.filter(MarketingCampaign.status == status)

    campaigns = query.order_by(MarketingCampaign.created_at.desc()).all()

    return {"items": [campaign_to_dict(c) for c in campaigns]}


@router.get("/campaigns/{campaign_id}")
def get_campaign_detail(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_user(current_user)

    campaign = db.query(MarketingCampaign).filter(MarketingCampaign.id == campaign_id).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")

    recipients = (
        db.query(MarketingCampaignRecipient)
        .filter(MarketingCampaignRecipient.campaign_id == campaign.id)
        .order_by(MarketingCampaignRecipient.id.desc())
        .all()
    )

    total_sent = len([r for r in recipients if r.sent_at is not None])
    total_opened = len([r for r in recipients if r.opened_at is not None])
    total_clicked = len([r for r in recipients if r.clicked_at is not None])
    total_read = len([r for r in recipients if r.read_at is not None])

    return {
        "campaign": campaign_to_dict(campaign),
        "total_recipients": len(recipients),
        "total_sent": total_sent,
        "total_opened": total_opened,
        "total_clicked": total_clicked,
        "total_read": total_read,
        "open_rate": round((total_opened / total_sent) * 100, 2) if total_sent else 0,
        "click_rate": round((total_clicked / total_sent) * 100, 2) if total_sent else 0,
        "read_rate": round((total_read / total_sent) * 100, 2) if total_sent else 0,
        "recipients": [recipient_to_dict(r) for r in recipients],
        "logs": [recipient_to_dict(r) for r in recipients],
    }


@router.put("/campaigns/{campaign_id}")
def update_campaign(
    campaign_id: int,
    payload: MarketingCampaignUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_user(current_user)

    campaign = db.query(MarketingCampaign).filter(MarketingCampaign.id == campaign_id).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")

    if campaign.status == "sent":
        raise HTTPException(status_code=400, detail="No se puede editar una campaña ya enviada")

    if payload.title is not None:
        campaign.title = payload.title.strip()

    if payload.subject is not None:
        campaign.subject = payload.subject.strip() if payload.subject.strip() else None

    if payload.message is not None:
        campaign.message = payload.message.strip()

    if payload.image_url is not None:
        campaign.image_url = payload.image_url.strip() if payload.image_url.strip() else None

    if payload.channel is not None:
        if payload.channel not in VALID_CHANNELS:
            raise HTTPException(status_code=400, detail="Canal inválido")
        campaign.channel = payload.channel

    if payload.target_group is not None:
        if payload.target_group not in VALID_TARGET_GROUPS:
            raise HTTPException(status_code=400, detail="Audiencia inválida")
        campaign.target_group = payload.target_group

    if payload.scheduled_at is not None:
        campaign.scheduled_at = payload.scheduled_at
        campaign.status = "scheduled"

    if payload.status is not None:
        if payload.status not in VALID_CAMPAIGN_STATUS:
            raise HTTPException(status_code=400, detail="Estado inválido")
        campaign.status = payload.status

    db.commit()
    db.refresh(campaign)

    return {
        "message": "Campaña actualizada correctamente",
        "campaign": campaign_to_dict(campaign),
    }


@router.post("/campaigns/send")
def send_campaign(
    payload: MarketingSendRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_user(current_user)

    campaign = db.query(MarketingCampaign).filter(MarketingCampaign.id == payload.campaign_id).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")

    result = send_campaign_now(db, campaign)

    db.commit()
    db.refresh(campaign)

    return {
        "message": "Campaña enviada correctamente",
        **result,
    }


@router.post("/campaigns/run-scheduled")
def run_scheduled_campaigns(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_user(current_user)

    results = process_scheduled_campaigns(db)
    db.commit()

    return {
        "message": "Campañas programadas procesadas",
        "processed": len(results),
        "items": results,
    }


@router.post("/campaigns/cron/run-scheduled")
def run_scheduled_campaigns_cron(
    secret: str,
    db: Session = Depends(get_db),
):
    cron_secret = os.getenv("MARKETING_CRON_SECRET")

    if not cron_secret:
        raise HTTPException(
            status_code=500,
            detail="Falta MARKETING_CRON_SECRET en Render",
        )

    if secret != cron_secret:
        raise HTTPException(
            status_code=401,
            detail="No autorizado",
        )

    results = process_scheduled_campaigns(db)
    db.commit()

    return {
        "message": "Campañas programadas procesadas por cron",
        "processed": len(results),
        "items": results,
    }


@router.put("/recipients/{recipient_id}/opened")
def mark_recipient_opened(
    recipient_id: int,
    db: Session = Depends(get_db),
):
    recipient = db.query(MarketingCampaignRecipient).filter(
        MarketingCampaignRecipient.id == recipient_id
    ).first()

    if not recipient:
        raise HTTPException(status_code=404, detail="Registro no encontrado")

    recipient.opened_at = datetime.utcnow()
    recipient.delivery_status = "opened"

    add_marketing_event(
        db=db,
        campaign_id=recipient.campaign_id,
        recipient_id=recipient.id,
        user_id=recipient.user_id,
        event_type="opened",
        channel=recipient.campaign.channel,
    )

    db.commit()
    db.refresh(recipient)

    return {
        "message": "Apertura registrada",
        "recipient": recipient_to_dict(recipient),
    }


@router.put("/recipients/{recipient_id}/clicked")
def mark_recipient_clicked(
    recipient_id: int,
    db: Session = Depends(get_db),
):
    recipient = db.query(MarketingCampaignRecipient).filter(
        MarketingCampaignRecipient.id == recipient_id
    ).first()

    if not recipient:
        raise HTTPException(status_code=404, detail="Registro no encontrado")

    recipient.clicked_at = datetime.utcnow()
    recipient.delivery_status = "clicked"

    add_marketing_event(
        db=db,
        campaign_id=recipient.campaign_id,
        recipient_id=recipient.id,
        user_id=recipient.user_id,
        event_type="clicked",
        channel=recipient.campaign.channel,
    )

    db.commit()
    db.refresh(recipient)

    return {
        "message": "Click registrado",
        "recipient": recipient_to_dict(recipient),
    }


@router.put("/recipients/{recipient_id}/read")
def mark_recipient_read(
    recipient_id: int,
    db: Session = Depends(get_db),
):
    recipient = db.query(MarketingCampaignRecipient).filter(
        MarketingCampaignRecipient.id == recipient_id
    ).first()

    if not recipient:
        raise HTTPException(status_code=404, detail="Registro no encontrado")

    recipient.read_at = datetime.utcnow()
    recipient.delivery_status = "read"

    add_marketing_event(
        db=db,
        campaign_id=recipient.campaign_id,
        recipient_id=recipient.id,
        user_id=recipient.user_id,
        event_type="read",
        channel=recipient.campaign.channel,
    )

    db.commit()
    db.refresh(recipient)

    return {
        "message": "Lectura registrada",
        "recipient": recipient_to_dict(recipient),
    }


@router.put("/logs/{log_id}/opened")
def mark_log_opened(
    log_id: int,
    db: Session = Depends(get_db),
):
    return mark_recipient_opened(log_id, db)


@router.put("/logs/{log_id}/clicked")
def mark_log_clicked(
    log_id: int,
    db: Session = Depends(get_db),
):
    return mark_recipient_clicked(log_id, db)
