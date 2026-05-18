from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from typing import Optional

from database import SessionLocal
from dependencies import get_current_user
from models import User, MarketingCampaign, MarketingCampaignRecipient, MarketingEvent


router = APIRouter(prefix="/marketing", tags=["marketing"])


VALID_CHANNELS = {"push", "email", "whatsapp"}

VALID_TARGET_GROUPS = {
    "members",
    "active_members",
    "inactive_members",
    "ambassadors",
}

VALID_CAMPAIGN_STATUS = {
    "draft",
    "scheduled",
    "sent",
}


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


class MarketingCampaignCreateRequest(BaseModel):
    title: str
    message: str
    subject: Optional[str] = None
    image_url: Optional[str] = None
    channel: str = "push"
    target_group: str = "members"
    status: str = "draft"


class MarketingCampaignUpdateRequest(BaseModel):
    title: Optional[str] = None
    message: Optional[str] = None
    subject: Optional[str] = None
    image_url: Optional[str] = None
    channel: Optional[str] = None
    target_group: Optional[str] = None
    status: Optional[str] = None


class MarketingSendRequest(BaseModel):
    campaign_id: int


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
    }


def contact_to_dict(user: User, channel: str):
    return {
        "id": user.id,
        "name": user.name,
        "role": user.role,
        "membership_active": user.membership_active,
        "email": user.email,
        "phone": user.phone,
        "contact": user.email if channel == "email" else user.phone,
        "channel": channel,
    }


def get_audience_users(db: Session, target_group: str):
    query = db.query(User).filter(User.is_active == True)

    if target_group == "members":
        query = query.filter(User.role == "member")

    elif target_group == "active_members":
        query = query.filter(
            User.role == "member",
            User.membership_active == True,
        )

    elif target_group == "inactive_members":
        query = query.filter(
            User.role == "member",
            User.membership_active == False,
        )

    elif target_group == "ambassadors":
        query = query.filter(User.role == "ambassador")

    else:
        raise HTTPException(status_code=400, detail="Audiencia inválida")

    return query.order_by(User.name.asc()).all()


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


@router.get("/test")
def marketing_test():
    return {
        "message": "marketing router ok",
        "available_channels": list(VALID_CHANNELS),
        "available_audiences": list(VALID_TARGET_GROUPS),
        "available_status": list(VALID_CAMPAIGN_STATUS),
        "note": "Módulo marketing separado para push, email y WhatsApp.",
    }


@router.get("/dashboard")
def marketing_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_user(current_user)

    total_campaigns = db.query(MarketingCampaign).count()

    total_push = db.query(MarketingCampaign).filter(
        MarketingCampaign.channel == "push"
    ).count()

    total_email = db.query(MarketingCampaign).filter(
        MarketingCampaign.channel == "email"
    ).count()

    total_whatsapp = db.query(MarketingCampaign).filter(
        MarketingCampaign.channel == "whatsapp"
    ).count()

    total_sent = db.query(MarketingCampaignRecipient).filter(
        MarketingCampaignRecipient.sent_at.isnot(None)
    ).count()

    total_opened = db.query(MarketingCampaignRecipient).filter(
        MarketingCampaignRecipient.opened_at.isnot(None)
    ).count()

    total_clicked = db.query(MarketingCampaignRecipient).filter(
        MarketingCampaignRecipient.clicked_at.isnot(None)
    ).count()

    total_read = db.query(MarketingCampaignRecipient).filter(
        MarketingCampaignRecipient.read_at.isnot(None)
    ).count()

    return {
        "total_campaigns": total_campaigns,
        "total_push": total_push,
        "total_email": total_email,
        "total_whatsapp": total_whatsapp,
        "total_sent": total_sent,
        "total_opened": total_opened,
        "total_clicked": total_clicked,
        "total_read": total_read,
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
        "items": [contact_to_dict(user, channel) for user in users],
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

    campaign = MarketingCampaign(
        title=payload.title.strip(),
        subject=payload.subject.strip() if payload.subject else None,
        message=payload.message.strip(),
        image_url=payload.image_url.strip() if payload.image_url else None,
        channel=payload.channel,
        target_group=payload.target_group,
        status=payload.status,
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_user(current_user)

    query = db.query(MarketingCampaign)

    if channel:
        if channel not in VALID_CHANNELS:
            raise HTTPException(status_code=400, detail="Canal inválido")
        query = query.filter(MarketingCampaign.channel == channel)

    campaigns = query.order_by(MarketingCampaign.created_at.desc()).all()

    return {
        "items": [campaign_to_dict(c) for c in campaigns],
    }


@router.get("/campaigns/{campaign_id}")
def get_campaign_detail(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_user(current_user)

    campaign = (
        db.query(MarketingCampaign)
        .filter(MarketingCampaign.id == campaign_id)
        .first()
    )

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

    campaign = (
        db.query(MarketingCampaign)
        .filter(MarketingCampaign.id == campaign_id)
        .first()
    )

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")

    if payload.title is not None:
        campaign.title = payload.title.strip()

    if payload.subject is not None:
        campaign.subject = payload.subject.strip()

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

    campaign = (
        db.query(MarketingCampaign)
        .filter(MarketingCampaign.id == payload.campaign_id)
        .first()
    )

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")

    users = get_audience_users(db, campaign.target_group)

    created_recipients = 0

    for user in users:
        existing_recipient = (
            db.query(MarketingCampaignRecipient)
            .filter(
                MarketingCampaignRecipient.campaign_id == campaign.id,
                MarketingCampaignRecipient.user_id == user.id,
            )
            .first()
        )

        if existing_recipient:
            continue

        recipient = MarketingCampaignRecipient(
            campaign_id=campaign.id,
            user_id=user.id,
            name_snapshot=user.name,
            email_snapshot=user.email,
            phone_snapshot=user.phone,
            role_snapshot=user.role,
            delivery_status="sent",
            sent_at=datetime.utcnow(),
        )

        db.add(recipient)
        db.flush()

        add_marketing_event(
            db=db,
            campaign_id=campaign.id,
            recipient_id=recipient.id,
            user_id=user.id,
            event_type="sent",
            channel=campaign.channel,
        )

        created_recipients += 1

    campaign.status = "sent"
    campaign.sent_at = datetime.utcnow()

    db.commit()
    db.refresh(campaign)

    return {
        "message": "Campaña enviada registrada correctamente",
        "campaign_id": campaign.id,
        "channel": campaign.channel,
        "target_group": campaign.target_group,
        "total_recipients": len(users),
        "new_recipients_created": created_recipients,
        "note": "Envío registrado. Aquí se conectará el proveedor real de push, email o WhatsApp.",
    }


@router.put("/recipients/{recipient_id}/opened")
def mark_recipient_opened(
    recipient_id: int,
    db: Session = Depends(get_db),
):
    recipient = (
        db.query(MarketingCampaignRecipient)
        .filter(MarketingCampaignRecipient.id == recipient_id)
        .first()
    )

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
    recipient = (
        db.query(MarketingCampaignRecipient)
        .filter(MarketingCampaignRecipient.id == recipient_id)
        .first()
    )

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
    recipient = (
        db.query(MarketingCampaignRecipient)
        .filter(MarketingCampaignRecipient.id == recipient_id)
        .first()
    )

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
