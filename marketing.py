from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from typing import Optional, List

from database import SessionLocal
from dependencies import get_current_user
from models import User, MarketingCampaign, MarketingMessageLog


router = APIRouter(prefix="/marketing", tags=["marketing"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_marketing_admin_or_superadmin(current_user: User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {"superadmin", "admin", "marketing"}:
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para marketing, admin o superadmin",
        )


class MarketingCampaignCreateRequest(BaseModel):
    title: str
    message: str
    channel: str = "push"
    audience: str = "all"
    status: str = "draft"


class MarketingCampaignUpdateRequest(BaseModel):
    title: Optional[str] = None
    message: Optional[str] = None
    channel: Optional[str] = None
    audience: Optional[str] = None
    status: Optional[str] = None


class MarketingSendRequest(BaseModel):
    campaign_id: int


def campaign_to_dict(campaign: MarketingCampaign):
    return {
        "id": campaign.id,
        "title": campaign.title,
        "message": campaign.message,
        "channel": campaign.channel,
        "audience": campaign.audience,
        "status": campaign.status,
        "created_by": campaign.created_by,
        "created_at": campaign.created_at,
        "sent_at": campaign.sent_at,
    }


def log_to_dict(log: MarketingMessageLog):
    return {
        "id": log.id,
        "campaign_id": log.campaign_id,
        "user_id": log.user_id,
        "user_name": log.user.name if log.user else None,
        "user_email": log.user.email if log.user else None,
        "user_phone": log.user.phone if log.user else None,
        "channel": log.channel,
        "status": log.status,
        "opened": log.opened,
        "opened_at": log.opened_at,
        "clicked": log.clicked,
        "clicked_at": log.clicked_at,
        "sent_at": log.sent_at,
        "error_message": log.error_message,
    }


def get_audience_users(db: Session, audience: str):
    query = db.query(User).filter(User.is_active == True)

    if audience == "members":
        query = query.filter(User.role == "member")

    elif audience == "active_members":
        query = query.filter(
            User.role == "member",
            User.membership_active == True,
        )

    elif audience == "inactive_members":
        query = query.filter(
            User.role == "member",
            User.membership_active == False,
        )

    elif audience == "ambassadors":
        query = query.filter(User.role == "ambassador")

    elif audience == "all":
        query = query.filter(User.role.in_(["member", "ambassador"]))

    else:
        raise HTTPException(status_code=400, detail="Audiencia inválida")

    return query.all()


@router.get("/test")
def marketing_test():
    return {
        "message": "marketing router ok",
        "available_channels": ["push", "email", "whatsapp"],
        "available_audiences": [
            "all",
            "members",
            "active_members",
            "inactive_members",
            "ambassadors",
        ],
    }


@router.post("/campaigns")
def create_campaign(
    payload: MarketingCampaignCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_admin_or_superadmin(current_user)

    if payload.channel not in {"push", "email", "whatsapp"}:
        raise HTTPException(status_code=400, detail="Canal inválido")

    if payload.audience not in {
        "all",
        "members",
        "active_members",
        "inactive_members",
        "ambassadors",
    }:
        raise HTTPException(status_code=400, detail="Audiencia inválida")

    campaign = MarketingCampaign(
        title=payload.title.strip(),
        message=payload.message.strip(),
        channel=payload.channel,
        audience=payload.audience,
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_admin_or_superadmin(current_user)

    campaigns = (
        db.query(MarketingCampaign)
        .order_by(MarketingCampaign.created_at.desc())
        .all()
    )

    return {
        "items": [campaign_to_dict(c) for c in campaigns],
    }


@router.get("/campaigns/{campaign_id}")
def get_campaign_detail(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_admin_or_superadmin(current_user)

    campaign = (
        db.query(MarketingCampaign)
        .filter(MarketingCampaign.id == campaign_id)
        .first()
    )

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")

    logs = (
        db.query(MarketingMessageLog)
        .filter(MarketingMessageLog.campaign_id == campaign.id)
        .order_by(MarketingMessageLog.sent_at.desc())
        .all()
    )

    return {
        "campaign": campaign_to_dict(campaign),
        "total_sent": len(logs),
        "total_opened": len([l for l in logs if l.opened]),
        "total_clicked": len([l for l in logs if l.clicked]),
        "logs": [log_to_dict(l) for l in logs],
    }


@router.put("/campaigns/{campaign_id}")
def update_campaign(
    campaign_id: int,
    payload: MarketingCampaignUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_marketing_admin_or_superadmin(current_user)

    campaign = (
        db.query(MarketingCampaign)
        .filter(MarketingCampaign.id == campaign_id)
        .first()
    )

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")

    if payload.title is not None:
        campaign.title = payload.title.strip()

    if payload.message is not None:
        campaign.message = payload.message.strip()

    if payload.channel is not None:
        if payload.channel not in {"push", "email", "whatsapp"}:
            raise HTTPException(status_code=400, detail="Canal inválido")
        campaign.channel = payload.channel

    if payload.audience is not None:
        if payload.audience not in {
            "all",
            "members",
            "active_members",
            "inactive_members",
            "ambassadors",
        }:
            raise HTTPException(status_code=400, detail="Audiencia inválida")
        campaign.audience = payload.audience

    if payload.status is not None:
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
    require_marketing_admin_or_superadmin(current_user)

    campaign = (
        db.query(MarketingCampaign)
        .filter(MarketingCampaign.id == payload.campaign_id)
        .first()
    )

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")

    users = get_audience_users(db, campaign.audience)

    created_logs = 0

    for user in users:
        existing_log = (
            db.query(MarketingMessageLog)
            .filter(
                MarketingMessageLog.campaign_id == campaign.id,
                MarketingMessageLog.user_id == user.id,
            )
            .first()
        )

        if existing_log:
            continue

        log = MarketingMessageLog(
            campaign_id=campaign.id,
            user_id=user.id,
            channel=campaign.channel,
            status="sent",
            sent_at=datetime.utcnow(),
        )

        db.add(log)
        created_logs += 1

    campaign.status = "sent"
    campaign.sent_at = datetime.utcnow()

    db.commit()
    db.refresh(campaign)

    return {
        "message": "Campaña enviada registrada correctamente",
        "campaign_id": campaign.id,
        "channel": campaign.channel,
        "audience": campaign.audience,
        "total_recipients": len(users),
        "new_logs_created": created_logs,
        "note": "Por ahora registra el envío. Luego conectamos envío real por push, email y WhatsApp.",
    }


@router.put("/logs/{log_id}/opened")
def mark_log_opened(
    log_id: int,
    db: Session = Depends(get_db),
):
    log = db.query(MarketingMessageLog).filter(MarketingMessageLog.id == log_id).first()

    if not log:
        raise HTTPException(status_code=404, detail="Registro no encontrado")

    log.opened = True
    log.opened_at = datetime.utcnow()

    db.commit()
    db.refresh(log)

    return {
        "message": "Apertura registrada",
        "log": log_to_dict(log),
    }


@router.put("/logs/{log_id}/clicked")
def mark_log_clicked(
    log_id: int,
    db: Session = Depends(get_db),
):
    log = db.query(MarketingMessageLog).filter(MarketingMessageLog.id == log_id).first()

    if not log:
        raise HTTPException(status_code=404, detail="Registro no encontrado")

    log.clicked = True
    log.clicked_at = datetime.utcnow()

    db.commit()
    db.refresh(log)

    return {
        "message": "Click registrado",
        "log": log_to_dict(log),
    }
