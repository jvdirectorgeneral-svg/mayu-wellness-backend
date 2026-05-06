from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session
from database import get_db
import models
import uuid
from PIL import Image, ImageDraw, ImageFont
import qrcode
import os

router = APIRouter(prefix="/member-cards", tags=["Member Cards"])

BASE_PUBLIC_URL = "https://mayu-wellness-backend-v1.onrender.com"
CARD_VALIDITY_TEXT = "Indefinido"


def generate_member_code(user_id: int, level: int):
    return f"MAYU-{level}-{user_id:06d}"


def generate_ambassador_code(user_id: int):
    return f"MAYU-AMB-{user_id:06d}"


def draw_text_with_shadow(
    draw,
    position,
    text,
    font,
    text_color=(255, 255, 255),
    shadow_color=(0, 0, 0),
):
    x, y = position
    draw.text((x, y), text, fill=text_color, font=font)


def draw_spaced_text_with_shadow(
    draw,
    position,
    text,
    font,
    spacing=1,
    text_color=(255, 255, 255),
    shadow_color=(0, 0, 0),
):
    x, y = position
    current_x = x

    for char in text:
        bbox = draw.textbbox((0, 0), char, font=font)
        char_width = bbox[2] - bbox[0]
        draw.text((current_x, y), char, fill=text_color, font=font)
        current_x += char_width + spacing


def get_spaced_text_width(draw, text, font, spacing=1):
    total_width = 0
    for i, char in enumerate(text):
        bbox = draw.textbbox((0, 0), char, font=font)
        char_width = bbox[2] - bbox[0]
        total_width += char_width
        if i < len(text) - 1:
            total_width += spacing
    return total_width


def get_user_card_type(user):
    if user.role == "ambassador":
        return "ambassador"
    return "member"


def get_card_level_snapshot(user):
    if user.role == "ambassador":
        return 9

    if user.membership_level:
        return user.membership_level

    return None


def get_card_status(user):
    if user.role == "ambassador":
        return "active" if user.is_active else "inactive"

    return "active" if user.membership_active else "inactive"


def get_card_code(user):
    if user.role == "ambassador":
        ambassador = getattr(user, "ambassador_profile", None)
        if ambassador and ambassador.ambassador_code:
            return ambassador.ambassador_code
        return generate_ambassador_code(user.id)

    return generate_member_code(user.id, user.membership_level)


@router.post("/generate/{user_id}")
def generate_member_card(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role not in ["member", "ambassador"]:
        raise HTTPException(
            status_code=400,
            detail="Solo se puede generar tarjeta para socios o embajadores",
        )

    if user.role == "member" and not user.membership_level:
        raise HTTPException(
            status_code=400,
            detail="El socio no tiene membresía asignada",
        )

    level_snapshot = get_card_level_snapshot(user)
    member_code = get_card_code(user)
    card_status = get_card_status(user)

    existing = db.query(models.MemberCard).filter(
        models.MemberCard.user_id == user.id
    ).first()

    if existing:
        existing.member_code = member_code
        existing.level_snapshot = level_snapshot
        existing.status = card_status
        existing.expires_at = CARD_VALIDITY_TEXT

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
                "card_type": get_user_card_type(user),
            },
        }

    card = models.MemberCard(
        user_id=user.id,
        member_code=member_code,
        qr_token=str(uuid.uuid4()),
        level_snapshot=level_snapshot,
        status=card_status,
        expires_at=CARD_VALIDITY_TEXT,
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
            "card_type": get_user_card_type(user),
        },
    }


@router.get("/user/{user_id}")
def get_member_card_by_user(user_id: int, db: Session = Depends(get_db)):
    card = db.query(models.MemberCard).filter(
        models.MemberCard.user_id == user_id
    ).first()

    if not card:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

    user = db.query(models.User).filter(models.User.id == user_id).first()

    return {
        "id": card.id,
        "user_id": card.user_id,
        "member_code": card.member_code,
        "qr_token": card.qr_token,
        "level_snapshot": card.level_snapshot,
        "status": card.status,
        "expires_at": card.expires_at or CARD_VALIDITY_TEXT,
        "card_type": get_user_card_type(user) if user else "member",
    }


@router.get("/validate/{qr_token}", response_class=HTMLResponse)
def validate_member_card(qr_token: str, db: Session = Depends(get_db)):
    card = db.query(models.MemberCard).filter(
        models.MemberCard.qr_token == qr_token
    ).first()

    if not card:
        return HTMLResponse(
            content="""
            <html>
                <head>
                    <title>Tarjeta inválida</title>
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                </head>
                <body style="font-family: Arial, sans-serif; background:#111; color:white; text-align:center; padding:40px;">
                    <h1 style="color:#ff4d4f;">Tarjeta inválida</h1>
                    <p>El código QR no corresponde a una tarjeta válida.</p>
                </body>
            </html>
            """,
            status_code=404,
        )

    user = db.query(models.User).filter(models.User.id == card.user_id).first()

    if not user:
        return HTMLResponse(
            content="""
            <html>
                <head>
                    <title>Usuario no encontrado</title>
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                </head>
                <body style="font-family: Arial, sans-serif; background:#111; color:white; text-align:center; padding:40px;">
                    <h1 style="color:#ff4d4f;">Usuario no encontrado</h1>
                </body>
            </html>
            """,
            status_code=404,
        )

    if user.role == "ambassador":
        status_text = "EMBAJADOR ACTIVO" if user.is_active else "EMBAJADOR INACTIVO"
        level_text = "Embajador Mayu Wellness Club"
    else:
        status_text = "ACTIVA" if user.membership_active else "INACTIVA"
        level_map = {
            1: "Nivel 1 - Cobre",
            2: "Nivel 2 - Plata",
            3: "Nivel 3 - Oro",
        }
        level_text = level_map.get(user.membership_level, "Sin nivel")

    status_color = "#22c55e" if card.status == "active" else "#ef4444"

    html_content = f"""
    <html>
        <head>
            <title>Validación de tarjeta MAYU</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="margin:0; font-family: Arial, sans-serif; background:#0f172a; color:white; display:flex; align-items:center; justify-content:center; min-height:100vh;">
            <div style="max-width:520px; width:90%; background:#1e293b; border-radius:24px; padding:32px; box-shadow:0 10px 30px rgba(0,0,0,0.35);">
                <h1 style="text-align:center; margin-top:0;">MAYU WELLNESS CLUB</h1>
                <div style="text-align:center; margin:20px 0;">
                    <span style="display:inline-block; padding:12px 24px; border-radius:999px; background:{status_color}; color:white; font-weight:bold; font-size:22px;">
                        {status_text}
                    </span>
                </div>
                <p style="font-size:20px; margin:14px 0;"><strong>Nombre:</strong> {user.name}</p>
                <p style="font-size:20px; margin:14px 0;"><strong>Email:</strong> {user.email}</p>
                <p style="font-size:20px; margin:14px 0;"><strong>Tipo:</strong> {level_text}</p>
                <p style="font-size:20px; margin:14px 0;"><strong>Código:</strong> {card.member_code}</p>
                <p style="font-size:20px; margin:14px 0;"><strong>Válido hasta:</strong> {CARD_VALIDITY_TEXT}</p>
            </div>
        </body>
    </html>
    """

    return HTMLResponse(content=html_content, status_code=200)


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

    if user.role == "ambassador":
        level_text = "Embajador Mayu Wellness Club"
        bg_path = os.path.join(base_dir, "assets", "card_oro.jpg")
        accent_color = (255, 236, 170)
    elif card.level_snapshot == 1:
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

    text_color = (255, 255, 255)
    shadow_color = (0, 0, 0)

    if os.path.exists(bg_path):
        image = Image.open(bg_path).convert("RGB")
        image = image.resize((width, height))
    else:
        fallback_colors = {
            1: (176, 111, 79),
            2: (205, 210, 214),
            3: (212, 175, 55),
            9: (212, 175, 55),
        }
        image = Image.new(
            "RGB",
            (width, height),
            fallback_colors.get(card.level_snapshot, (212, 175, 55)),
        )

    draw = ImageDraw.Draw(image)

    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 64)
        name_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 42)
        info_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 30)
    except Exception:
        title_font = ImageFont.load_default()
        name_font = ImageFont.load_default()
        info_font = ImageFont.load_default()

    draw.rounded_rectangle(
        [(18, 18), (width - 18, height - 18)],
        radius=30,
        outline=accent_color,
        width=4,
    )

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

    title = "MAYU WELLNESS CLUB"
    spacing = 1
    title_width = get_spaced_text_width(draw, title, title_font, spacing=spacing)
    title_x = (width - title_width) // 2
    title_y = 215

    draw_spaced_text_with_shadow(
        draw,
        (title_x, title_y),
        title,
        title_font,
        spacing=spacing,
        text_color=text_color,
        shadow_color=shadow_color,
    )

    draw_text_with_shadow(draw, (60, 305), user.name, name_font, text_color, shadow_color)
    draw_text_with_shadow(draw, (60, 355), level_text, info_font, text_color, shadow_color)
    draw_text_with_shadow(draw, (60, 400), f"Código: {card.member_code}", info_font, text_color, shadow_color)
    draw_text_with_shadow(draw, (60, 440), f"Válido hasta: {CARD_VALIDITY_TEXT}", info_font, text_color, shadow_color)
    draw_text_with_shadow(draw, (60, 480), f"Estado: {card.status}", info_font, text_color, shadow_color)

    validation_url = f"{BASE_PUBLIC_URL}/member-cards/validate/{card.qr_token}"
    qr = qrcode.make(validation_url)
    qr = qr.resize((170, 170))
    image.paste(qr, (735, 340))

    file_path = f"card_{user_id}.png"
    image.save(file_path)

    return FileResponse(
        path=file_path,
        media_type="image/png",
        content_disposition_type="inline",
    )
