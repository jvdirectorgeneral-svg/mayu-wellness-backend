from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
import models
import uuid
from datetime import datetime, timedelta
from PIL import Image, ImageDraw
import qrcode
import os

router = APIRouter(prefix="/member-cards", tags=["Member Cards"])


def generate_member_code(user_id: int, level: int):
    return f"MAYU-{level}-{user_id:06d}"


@router.post("/generate/{user_id}")
def generate_member_card(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not user.membership_level:
        raise HTTPException(status_code=400, detail="El usuario no tiene membresía asignada")

    existing = db.query(models.MemberCard).filter(
        models.MemberCard.user_id == user.id
    ).first()

    expires_at = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")
    member_code = generate_member_code(user.id, user.membership_level)

    if existing:
        existing.level_snapshot = user.membership_level
        existing.status = "active" if user.membership_active else "inactive"
        existing.expires_at = expires_at
        db.commit()
        db.refresh(existing)

        return {
            "message": "Tarjeta actualizada correctamente",
            "card": {
                "id": existing.id,
                "user_id": existing.user_id,
                "member_code": existing.member_code,
                "qr_token": existing.qr_token,
                "level_snapshot": existing.level_snapshot,
                "status": existing.status,
                "expires_at": existing.expires_at,
            }
        }

    card = models.MemberCard(
        user_id=user.id,
        member_code=member_code,
        qr_token=str(uuid.uuid4()),
        level_snapshot=user.membership_level,
        status="active" if user.membership_active else "inactive",
        expires_at=expires_at
    )

    db.add(card)
    db.commit()
    db.refresh(card)

    return {
        "message": "Tarjeta generada correctamente",
        "card": {
            "id": card.id,
            "user_id": card.user_id,
            "member_code": card.member_code,
            "qr_token": card.qr_token,
            "level_snapshot": card.level_snapshot,
            "status": card.status,
            "expires_at": card.expires_at,
        }
    }


@router.get("/user/{user_id}")
def get_member_card_by_user(user_id: int, db: Session = Depends(get_db)):
    card = db.query(models.MemberCard).filter(
        models.MemberCard.user_id == user_id
    ).first()

    if not card:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

    return {
        "id": card.id,
        "user_id": card.user_id,
        "member_code": card.member_code,
        "qr_token": card.qr_token,
        "level_snapshot": card.level_snapshot,
        "status": card.status,
        "expires_at": card.expires_at,
    }


@router.get("/validate/{qr_token}")
def validate_member_card(qr_token: str, db: Session = Depends(get_db)):
    card = db.query(models.MemberCard).filter(
        models.MemberCard.qr_token == qr_token
    ).first()

    if not card:
        raise HTTPException(status_code=404, detail="Tarjeta inválida")

    user = db.query(models.User).filter(models.User.id == card.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    return {
        "valid": True,
        "user_id": user.id,
        "name": user.name,
        "email": user.email,
        "membership_level": user.membership_level,
        "membership_active": user.membership_active,
        "member_code": card.member_code,
        "card_status": card.status,
        "expires_at": card.expires_at,
    }


@router.get("/user/{user_id}/image")
def generate_card_image(user_id: int, db: Session = Depends(get_db)):
    card = db.query(models.MemberCard).filter(
        models.MemberCard.user_id == user_id
    ).first()

    if not card:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # =========================
    # COLORES ELEGANTES POR NIVEL
    # =========================
    if card.level_snapshot == 1:
        bg_color = (176, 111, 79)        # cobre elegante
        accent_color = (230, 190, 160)   # brillo cobre
        level_text = "Nivel 1 - Cobre"
    elif card.level_snapshot == 2:
        bg_color = (205, 210, 214)       # plata premium
        accent_color = (245, 245, 245)   # brillo plata
        level_text = "Nivel 2 - Plata"
    else:
        bg_color = (212, 175, 55)        # oro brillante
        accent_color = (255, 233, 140)   # brillo oro
        level_text = "Nivel 3 - Oro"

    # =========================
    # CREAR IMAGEN BASE
    # =========================
    width, height = 950, 560
    image = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(image)

    # Marco interior elegante
    draw.rounded_rectangle(
        [(20, 20), (width - 20, height - 20)],
        radius=28,
        outline=accent_color,
        width=4
    )

    # Línea decorativa superior
    draw.rounded_rectangle(
        [(35, 35), (width - 35, 95)],
        radius=18,
        fill=accent_color
    )

    # =========================
    # LOGO
    # =========================
    base_dir = os.path.dirname(os.path.abspath(__file__))
    logo_path = os.path.join(base_dir, "assets", "logo_mayu.png")

    if os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo = logo.resize((120, 120))
            image.paste(logo, (780, 120), logo)
        except Exception:
            pass

    # =========================
    # TEXTOS
    # =========================
    text_color = (20, 20, 20)

    draw.text((55, 48), "MAYU WELLNESS CLUB", fill=text_color)
    draw.text((55, 145), user.name, fill=text_color)
    draw.text((55, 205), level_text, fill=text_color)
    draw.text((55, 275), f"Codigo: {card.member_code}", fill=text_color)
    draw.text((55, 330), f"Valido hasta: {card.expires_at}", fill=text_color)
    draw.text((55, 385), f"Estado: {card.status}", fill=text_color)

    # Franja de beneficio
    draw.rounded_rectangle(
        [(50, 450), (600, 505)],
        radius=16,
        fill=accent_color
    )
    draw.text((70, 468), "Tarjeta digital de beneficios MAYU", fill=text_color)

    # =========================
    # QR
    # =========================
    qr = qrcode.make(card.qr_token)
    qr = qr.resize((170, 170))
    image.paste(qr, (730, 330))

    # =========================
    # GUARDAR IMAGEN TEMPORAL
    # =========================
    file_path = f"card_{user_id}.png"
    image.save(file_path)

    return FileResponse(
        file_path,
        media_type="image/png",
        filename=f"mayu_card_{user_id}.png"
    )
