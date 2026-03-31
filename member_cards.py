from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
import models
import uuid
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import qrcode
import os

router = APIRouter(prefix="/member-cards", tags=["Member Cards"])


def generate_member_code(user_id: int, level: int):
    return f"MAYU-{level}-{user_id:06d}"


def draw_text_with_shadow(draw, position, text, font, text_color=(255, 255, 255), shadow_color=(0, 0, 0)):
    x, y = position
    draw.text((x + 2, y + 2), text, fill=shadow_color, font=font)
    draw.text((x, y), text, fill=text_color, font=font)


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

    width, height = 950, 560
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # =========================
    # FONDO POR NIVEL
    # =========================
    if card.level_snapshot == 1:
        level_text = "Nivel 1 - Cobre"
        bg_path = os.path.join(base_dir, "assets", "card_cobre.jpg")
        accent_color = (236, 210, 190)
    elif card.level_snapshot == 2:
        level_text = "Nivel 2 - Plata"
        bg_path = os.path.join(base_dir, "assets", "card_plata.jpg")
        accent_color = (245, 245, 245)
    else:
        level_text = "Nivel 3 - Oro"
        bg_path = os.path.join(base_dir, "assets", "card_oro.jpg")
        accent_color = (255, 236, 170)

    # Todo el texto en blanco
    text_color = (255, 255, 255)
    shadow_color = (0, 0, 0)

    # Fondo con imagen o fallback
    if os.path.exists(bg_path):
        image = Image.open(bg_path).convert("RGB")
        image = image.resize((width, height))
    else:
        fallback_colors = {
            1: (176, 111, 79),
            2: (205, 210, 214),
            3: (212, 175, 55),
        }
        image = Image.new(
            "RGB",
            (width, height),
            fallback_colors.get(card.level_snapshot, (220, 220, 220))
        )

    draw = ImageDraw.Draw(image)

    # =========================
    # FUENTES
    # =========================
    font_path = os.path.join(base_dir, "assets", "PlayfairDisplay-Bold.ttf")

    try:
        title_font = ImageFont.truetype(font_path, 54)
        name_font = ImageFont.truetype(font_path, 40)
        info_font = ImageFont.truetype(font_path, 28)
    except Exception:
        try:
            title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 54)
            name_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 40)
            info_font = ImageFont.truetype("DejaVuSans.ttf", 28)
        except Exception:
            title_font = ImageFont.load_default()
            name_font = ImageFont.load_default()
            info_font = ImageFont.load_default()

    # =========================
    # MARCO
    # =========================
    draw.rounded_rectangle(
        [(18, 18), (width - 18, height - 18)],
        radius=30,
        outline=accent_color,
        width=4
    )

    # =========================
    # LOGO CENTRADO Y GRANDE
    # =========================
    logo_path = os.path.join(base_dir, "assets", "logo_mayu.png")
    if os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo = logo.resize((170, 170))
            logo_x = (width - 170) // 2
            logo_y = 25
            image.paste(logo, (logo_x, logo_y), logo)
        except Exception:
            pass

    # =========================
    # TÍTULO GRANDE, CENTRADO
    # =========================
    title = "MAYU WELLNESS CLUB"
    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    title_width = title_bbox[2] - title_bbox[0]
    title_x = (width - title_width) // 2
    title_y = 205

    # Negrita simulada + sombra
    for dx, dy in [(0, 0), (1, 0), (0, 1), (1, 1)]:
        draw.text(
            (title_x + dx + 2, title_y + dy + 2),
            title,
            fill=shadow_color,
            font=title_font
        )
        draw.text(
            (title_x + dx, title_y + dy),
            title,
            fill=text_color,
            font=title_font
        )

    # =========================
    # DATOS GRANDES EN BLANCO
    # =========================
    draw_text_with_shadow(draw, (60, 300), user.name, name_font, text_color, shadow_color)
    draw_text_with_shadow(draw, (60, 355), level_text, info_font, text_color, shadow_color)
    draw_text_with_shadow(draw, (60, 400), f"Código: {card.member_code}", info_font, text_color, shadow_color)
    draw_text_with_shadow(draw, (60, 440), f"Válido hasta: {card.expires_at}", info_font, text_color, shadow_color)
    draw_text_with_shadow(draw, (60, 480), f"Estado: {card.status}", info_font, text_color, shadow_color)

    # =========================
    # QR
    # =========================
    qr = qrcode.make(card.qr_token)
    qr = qr.resize((170, 170))
    image.paste(qr, (735, 340))

    # =========================
    # GUARDAR
    # =========================
    file_path = f"card_{user_id}.png"
    image.save(file_path)

    return FileResponse(
        file_path,
        media_type="image/png",
        filename=f"mayu_card_{user_id}.png"
    )
