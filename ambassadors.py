from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session
from database import get_db
from auth import hash_password, verify_password, create_access_token
import models
import uuid
from schemas import AmbassadorRegister, AmbassadorLogin
from PIL import Image, ImageDraw, ImageFont
import qrcode
import os

router = APIRouter(prefix="/ambassadors", tags=["Ambassadors"])

BASE_PUBLIC_URL = "https://mayu-wellness-backend-v1.onrender.com"


def generate_ambassador_code(ambassador_id: int):
    return f"EMB-{ambassador_id:06d}"


def normalize_phone_for_whatsapp(phone: str | None):
    if not phone:
        return None

    cleaned = (
        phone.replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
        .replace("+", "")
    )

    if cleaned.startswith("0"):
        cleaned = "593" + cleaned[1:]

    return cleaned


def build_whatsapp_url(phone: str | None):
    normalized = normalize_phone_for_whatsapp(phone)
    if not normalized:
        return None
    return f"https://wa.me/{normalized}"


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


@router.post("/register")
def register_ambassador(
    data: AmbassadorRegister,
    db: Session = Depends(get_db)
):
    existing_user = db.query(models.User).filter(
        models.User.email == data.email
    ).first()

    if existing_user:
        raise HTTPException(status_code=400, detail="El email ya está registrado")

    existing_cedula = db.query(models.User).filter(
        models.User.cedula == data.national_id
    ).first()

    if existing_cedula:
        raise HTTPException(status_code=400, detail="La cédula ya está registrada")

    user = models.User(
        name=data.name,
        email=data.email,
        password=hash_password(data.password),
        phone=data.phone,
        cedula=data.national_id,
        city="Quito",
        address=data.address,
        reference="Registro embajador",
        delivery_notes="Registro embajador",
        phone_secondary=data.phone,
        status="registered",
        membership_level=None,
        membership_active=False,
        role="ambassador",
        is_active=True
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    ambassador = models.Ambassador(
        user_id=user.id,
        ambassador_code=f"TEMP-{user.id}",
        ambassador_token=str(uuid.uuid4()),
        national_id=data.national_id,
        address=data.address,
        bank_name=data.bank_name,
        account_type=data.account_type,
        bank_account_number=data.bank_account_number,
        status="active",
        is_active=True
    )

    db.add(ambassador)
    db.commit()
    db.refresh(ambassador)

    ambassador.ambassador_code = generate_ambassador_code(ambassador.id)
    db.commit()
    db.refresh(ambassador)

    return {
        "message": "Embajador registrado correctamente",
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "phone": user.phone,
            "cedula": user.cedula,
            "address": user.address,
            "role": user.role,
            "is_active": user.is_active
        },
        "ambassador": {
            "id": ambassador.id,
            "user_id": ambassador.user_id,
            "ambassador_code": ambassador.ambassador_code,
            "ambassador_token": ambassador.ambassador_token,
            "national_id": ambassador.national_id,
            "address": ambassador.address,
            "bank_name": ambassador.bank_name,
            "account_type": ambassador.account_type,
            "bank_account_number": ambassador.bank_account_number,
            "status": ambassador.status,
            "is_active": ambassador.is_active
        }
    }


@router.post("/login")
def login_ambassador(payload: AmbassadorLogin, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(
        models.User.email == payload.email,
        models.User.role == "ambassador"
    ).first()

    if not db_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not getattr(db_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    try:
        password_ok = verify_password(payload.password, db_user.password)
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Contraseña inválida o hash dañado"
        )

    if not password_ok:
        raise HTTPException(status_code=401, detail="Contraseña incorrecta")

    ambassador = db.query(models.Ambassador).filter(
        models.Ambassador.user_id == db_user.id
    ).first()

    if not ambassador:
        raise HTTPException(status_code=404, detail="Perfil de embajador no encontrado")

    token = create_access_token({
        "sub": str(db_user.id),
        "email": db_user.email
    })

    return {
        "message": "Login exitoso",
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": db_user.id,
            "name": db_user.name,
            "email": db_user.email,
            "phone": db_user.phone,
            "cedula": db_user.cedula,
            "role": db_user.role
        },
        "ambassador": {
            "id": ambassador.id,
            "user_id": ambassador.user_id,
            "ambassador_code": ambassador.ambassador_code,
            "ambassador_token": ambassador.ambassador_token,
            "national_id": ambassador.national_id,
            "address": ambassador.address,
            "bank_name": ambassador.bank_name,
            "account_type": ambassador.account_type,
            "bank_account_number": ambassador.bank_account_number,
            "status": ambassador.status,
            "is_active": ambassador.is_active
        }
    }


@router.get("/{ambassador_id}")
def get_ambassador_profile(ambassador_id: int, db: Session = Depends(get_db)):
    ambassador = db.query(models.Ambassador).filter(
        models.Ambassador.id == ambassador_id
    ).first()

    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    user = db.query(models.User).filter(
        models.User.id == ambassador.user_id
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario del embajador no encontrado")

    return {
        "ambassador": {
            "id": ambassador.id,
            "user_id": ambassador.user_id,
            "name": user.name,
            "email": user.email,
            "phone": user.phone,
            "cedula": user.cedula,
            "ambassador_code": ambassador.ambassador_code,
            "ambassador_token": ambassador.ambassador_token,
            "national_id": ambassador.national_id,
            "address": ambassador.address,
            "bank_name": ambassador.bank_name,
            "account_type": ambassador.account_type,
            "bank_account_number": ambassador.bank_account_number,
            "status": ambassador.status,
            "is_active": ambassador.is_active
        }
    }


@router.get("/{ambassador_id}/card")
def get_ambassador_card(ambassador_id: int, db: Session = Depends(get_db)):
    ambassador = db.query(models.Ambassador).filter(
        models.Ambassador.id == ambassador_id
    ).first()

    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    user = db.query(models.User).filter(
        models.User.id == ambassador.user_id
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario del embajador no encontrado")

    return {
        "name": user.name,
        "type": "Embajador Mayu",
        "code": ambassador.ambassador_code,
        "valid_until": "Indefinido",
        "status": ambassador.status,
        "qr_token": ambassador.ambassador_token
    }


@router.get("/validate/{ambassador_token}", response_class=HTMLResponse)
def validate_ambassador_card(ambassador_token: str, db: Session = Depends(get_db)):
    ambassador = db.query(models.Ambassador).filter(
        models.Ambassador.ambassador_token == ambassador_token
    ).first()

    if not ambassador:
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

    user = db.query(models.User).filter(
        models.User.id == ambassador.user_id
    ).first()

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

    status_text = "ACTIVO" if ambassador.is_active else "INACTIVO"
    status_color = "#22c55e" if ambassador.is_active else "#ef4444"

    html_content = f"""
    <html>
        <head>
            <title>Validación de tarjeta Embajador MAYU</title>
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
                <p style="font-size:20px; margin:14px 0;"><strong>Celular:</strong> {user.phone or ''}</p>
                <p style="font-size:20px; margin:14px 0;"><strong>Tipo:</strong> Embajador Mayu</p>
                <p style="font-size:20px; margin:14px 0;"><strong>Código:</strong> {ambassador.ambassador_code}</p>
                <p style="font-size:20px; margin:14px 0;"><strong>Vigencia:</strong> Indefinido</p>
            </div>
        </body>
    </html>
    """

    return HTMLResponse(content=html_content, status_code=200)


@router.get("/{ambassador_id}/image")
def generate_ambassador_card_image(ambassador_id: int, db: Session = Depends(get_db)):
    ambassador = db.query(models.Ambassador).filter(
        models.Ambassador.id == ambassador_id
    ).first()

    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    user = db.query(models.User).filter(
        models.User.id == ambassador.user_id
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario del embajador no encontrado")

    width, height = 950, 560
    base_dir = os.path.dirname(os.path.abspath(__file__))

    bg_path = os.path.join(base_dir, "assets", "embajador_pic.png")
    accent_color = (236, 210, 190)
    text_color = (255, 255, 255)
    shadow_color = (0, 0, 0)

    if os.path.exists(bg_path):
        image = Image.open(bg_path).convert("RGB")
        image = image.resize((width, height))
    else:
        image = Image.new("RGB", (width, height), (20, 20, 20))

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
    draw_text_with_shadow(draw, (60, 355), "Embajador Mayu", info_font, text_color, shadow_color)
    draw_text_with_shadow(draw, (60, 400), f"Código: {ambassador.ambassador_code}", info_font, text_color, shadow_color)
    draw_text_with_shadow(draw, (60, 440), "Válido: Indefinido", info_font, text_color, shadow_color)
    draw_text_with_shadow(draw, (60, 480), f"Estado: {ambassador.status}", info_font, text_color, shadow_color)

    validation_url = f"{BASE_PUBLIC_URL}/ambassadors/validate/{ambassador.ambassador_token}"
    qr = qrcode.make(validation_url)
    qr = qr.resize((170, 170))
    image.paste(qr, (735, 340))

    file_path = f"ambassador_card_{ambassador_id}.png"
    image.save(file_path)

    return FileResponse(
        path=file_path,
        media_type="image/png",
        content_disposition_type="inline",
    )


@router.get("/{ambassador_id}/dashboard")
def get_ambassador_dashboard(ambassador_id: int, db: Session = Depends(get_db)):
    ambassador = db.query(models.Ambassador).filter(
        models.Ambassador.id == ambassador_id
    ).first()

    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    user = db.query(models.User).filter(
        models.User.id == ambassador.user_id
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario del embajador no encontrado")

    referrals = db.query(models.AmbassadorReferral).filter(
        models.AmbassadorReferral.ambassador_id == ambassador.id
    ).all()

    affiliates = []
    active_referrals = 0
    inactive_referrals = 0

    for referral in referrals:
        referred_user = db.query(models.User).filter(
            models.User.id == referral.user_id
        ).first()

        if referred_user:
            if referred_user.membership_active:
                active_referrals += 1
            else:
                inactive_referrals += 1

            affiliates.append({
                "id": referred_user.id,
                "name": referred_user.name,
                "email": referred_user.email,
                "phone": referred_user.phone,
                "whatsapp_url": build_whatsapp_url(referred_user.phone),
                "membership_level": referred_user.membership_level,
                "membership_active": referred_user.membership_active
            })

    total_referrals = len(affiliates)
    total_payments = 0
    monthly_commission = 0

    return {
        "card": {
            "name": user.name,
            "type": "Embajador Mayu",
            "code": ambassador.ambassador_code,
            "valid_until": "Indefinido",
            "status": ambassador.status,
            "qr_token": ambassador.ambassador_token,
            "image_url": f"{BASE_PUBLIC_URL}/ambassadors/{ambassador.id}/image"
        },
        "stats": {
            "total_referrals": total_referrals,
            "active_referrals": active_referrals,
            "inactive_referrals": inactive_referrals,
            "total_payments": total_payments,
            "monthly_commission": monthly_commission,
            "goal": 100
        },
        "reward_progress": {
            "goal": 100,
            "current": active_referrals,
            "reward": "Viaje a la playa"
        },
        "affiliates": affiliates
    }
