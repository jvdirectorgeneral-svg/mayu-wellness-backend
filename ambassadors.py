from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session
from database import get_db
from dependencies import get_current_user
from auth import hash_password, verify_password, create_access_token
import models
import uuid
from schemas import AmbassadorRegister, AmbassadorLogin
from PIL import Image, ImageDraw, ImageFont
import qrcode
import os
from datetime import datetime

router = APIRouter(prefix="/ambassadors", tags=["Ambassadors"])

BASE_PUBLIC_URL = "https://mayu-wellness-backend-v1.onrender.com"


def send_ambassador_welcome_safely(db: Session, user: models.User):
    try:
        from marketing import send_welcome_ambassador_notifications

        result = send_welcome_ambassador_notifications(
            db=db,
            user=user,
            trigger="ambassador_register",
        )
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        return {
            "sent": False,
            "error": str(exc),
        }


def generate_ambassador_code(ambassador_id: int):
    return f"EMB-{ambassador_id:06d}"


def require_ambassador_access(ambassador: models.Ambassador, current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role in {"admin", "superadmin", "supervisor"}:
        return

    if current_user.role != "ambassador":
        raise HTTPException(status_code=403, detail="Acceso no autorizado")

    if ambassador.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="No puedes acceder al panel de otro embajador",
        )


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


def level_name(level):
    try:
        parsed = int(level or 0)
    except Exception:
        parsed = 0

    if parsed == 1:
        return "Nivel 1 - Cobre"
    if parsed == 2:
        return "Nivel 2 - Plata"
    if parsed == 3:
        return "Nivel 3 - Oro"
    return "Sin nivel"


def commission_amount_by_level(level):
    try:
        parsed = int(level or 0)
    except Exception:
        parsed = 0

    if parsed == 1:
        return 5.00
    if parsed == 2:
        return 6.00
    if parsed == 3:
        return 7.00
    return 0.00


def get_next_payment_day():
    today = datetime.utcnow()

    if today.day <= 5:
        return 5

    if today.day <= 21:
        return 21

    return 5


def format_order_products(order):
    if not order:
        return []

    return [
        {
            "id": item.id,
            "product_id": item.product_id,
            "name": item.product_name_snapshot,
            "product_name_snapshot": item.product_name_snapshot,
            "quantity": item.quantity,
        }
        for item in order.items
    ]


def order_tracking_data(order):
    if not order:
        return {
            "last_order_id": None,
            "last_order_code": None,
            "last_order_status": None,
            "carrier": None,
            "tracking_number": None,
            "tracking_url": None,
            "shipping_notes": None,
            "shipping_batch_date": None,
            "prepared_at": None,
            "shipped_at": None,
            "delivered_at": None,
            "delivery_products": [],
        }

    return {
        "last_order_id": order.id,
        "last_order_code": order.order_code,
        "last_order_status": order.status,
        "carrier": getattr(order, "carrier", None),
        "tracking_number": getattr(order, "tracking_number", None),
        "tracking_url": getattr(order, "tracking_url", None),
        "shipping_notes": getattr(order, "shipping_notes", None),
        "shipping_batch_date": getattr(order, "shipping_batch_date", None),
        "prepared_at": getattr(order, "prepared_at", None),
        "shipped_at": getattr(order, "shipped_at", None),
        "delivered_at": getattr(order, "delivered_at", None),
        "delivery_products": format_order_products(order),
    }


def get_last_order_for_user(db: Session, user_id: int):
    return (
        db.query(models.Order)
        .filter(models.Order.user_id == user_id)
        .order_by(
            models.Order.year.desc(),
            models.Order.month.desc(),
            models.Order.created_at.desc(),
        )
        .first()
    )


def get_delivery_history_for_user(db: Session, user_id: int):
    orders = (
        db.query(models.Order)
        .filter(models.Order.user_id == user_id)
        .order_by(
            models.Order.year.desc(),
            models.Order.month.desc(),
            models.Order.created_at.desc(),
        )
        .all()
    )

    return [
        {
            "order_id": order.id,
            "order_code": order.order_code,
            "month": order.month,
            "year": order.year,
            "status": order.status,
            "carrier": getattr(order, "carrier", None),
            "tracking_number": getattr(order, "tracking_number", None),
            "tracking_url": getattr(order, "tracking_url", None),
            "shipping_notes": getattr(order, "shipping_notes", None),
            "shipping_batch_date": getattr(order, "shipping_batch_date", None),
            "prepared_at": getattr(order, "prepared_at", None),
            "shipped_at": getattr(order, "shipped_at", None),
            "delivered_at": getattr(order, "delivered_at", None),
            "products": format_order_products(order),
        }
        for order in orders
    ]


def ambassador_card_payload(user: models.User, ambassador: models.Ambassador):
    status = "active" if getattr(ambassador, "is_active", True) else "inactive"

    return {
        "id": ambassador.id,
        "ambassador_id": ambassador.id,
        "user_id": user.id,
        "name": user.name,
        "user_name": user.name,
        "email": user.email,
        "phone": user.phone,
        "type": "Embajador Mayu",
        "card_type": "ambassador",
        "code": ambassador.ambassador_code,
        "member_code": ambassador.ambassador_code,
        "valid_until": "Indefinido",
        "expires_at": "Indefinido",
        "status": status,
        "qr_token": ambassador.ambassador_token,
        "image_url": f"{BASE_PUBLIC_URL}/ambassadors/{ambassador.id}/image",
        "web_url": f"{BASE_PUBLIC_URL}/member-cards/user/{user.id}/web",
        "apple_wallet_url": f"{BASE_PUBLIC_URL}/member-cards/apple-wallet/{user.id}",
        "google_wallet_url": f"{BASE_PUBLIC_URL}/member-cards/google-wallet/{user.id}",
    }


def draw_text_with_shadow(draw, position, text, font, text_color=(255, 255, 255)):
    draw.text(position, text, fill=text_color, font=font)


def draw_spaced_text_with_shadow(draw, position, text, font, spacing=1, text_color=(255, 255, 255)):
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
def register_ambassador(data: AmbassadorRegister, db: Session = Depends(get_db)):
    existing_user = db.query(models.User).filter(models.User.email == data.email).first()

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
        birth_date=data.birth_date,
        status="registered",
        membership_level=None,
        membership_active=False,
        role="ambassador",
        is_active=True,
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
        bank_account_type=data.account_type,
        bank_account_number=data.bank_account_number,
        status="active",
        is_active=True,
    )

    db.add(ambassador)
    db.commit()
    db.refresh(ambassador)

    ambassador.ambassador_code = generate_ambassador_code(ambassador.id)

    db.commit()
    db.refresh(ambassador)

    welcome_notifications = send_ambassador_welcome_safely(db, user)

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
            "is_active": user.is_active,
        },
        "ambassador": {
            "id": ambassador.id,
            "user_id": ambassador.user_id,
            "ambassador_code": ambassador.ambassador_code,
            "ambassador_token": ambassador.ambassador_token,
            "national_id": ambassador.national_id,
            "address": ambassador.address,
            "bank_name": ambassador.bank_name,
            "account_type": ambassador.bank_account_type,
            "bank_account_type": ambassador.bank_account_type,
            "bank_account_number": ambassador.bank_account_number,
            "status": ambassador.status,
            "is_active": ambassador.is_active,
        },
        "card": ambassador_card_payload(user, ambassador),
        "welcome_notifications": welcome_notifications,
    }


@router.post("/login")
def login_ambassador(payload: AmbassadorLogin, db: Session = Depends(get_db)):
    db_user = (
        db.query(models.User)
        .filter(models.User.email == payload.email, models.User.role == "ambassador")
        .first()
    )

    if not db_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not getattr(db_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    try:
        password_ok = verify_password(payload.password, db_user.password)
    except Exception:
        raise HTTPException(status_code=401, detail="Contraseña inválida o hash dañado")

    if not password_ok:
        raise HTTPException(status_code=401, detail="Contraseña incorrecta")

    ambassador = (
        db.query(models.Ambassador)
        .filter(models.Ambassador.user_id == db_user.id)
        .first()
    )

    if not ambassador:
        raise HTTPException(status_code=404, detail="Perfil de embajador no encontrado")

    token = create_access_token({"sub": str(db_user.id), "email": db_user.email})

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
            "role": db_user.role,
        },
        "ambassador": {
            "id": ambassador.id,
            "user_id": ambassador.user_id,
            "ambassador_code": ambassador.ambassador_code,
            "ambassador_token": ambassador.ambassador_token,
            "national_id": ambassador.national_id,
            "address": ambassador.address,
            "bank_name": ambassador.bank_name,
            "account_type": ambassador.bank_account_type,
            "bank_account_type": ambassador.bank_account_type,
            "bank_account_number": ambassador.bank_account_number,
            "status": ambassador.status,
            "is_active": ambassador.is_active,
        },
        "card": ambassador_card_payload(db_user, ambassador),
    }


@router.get("/{ambassador_id}")
def get_ambassador_profile(
    ambassador_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    ambassador = db.query(models.Ambassador).filter(models.Ambassador.id == ambassador_id).first()

    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    require_ambassador_access(ambassador, current_user)

    user = db.query(models.User).filter(models.User.id == ambassador.user_id).first()

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
            "account_type": ambassador.bank_account_type,
            "bank_account_type": ambassador.bank_account_type,
            "bank_account_number": ambassador.bank_account_number,
            "status": ambassador.status,
            "is_active": ambassador.is_active,
        },
        "card": ambassador_card_payload(user, ambassador),
    }


@router.get("/{ambassador_id}/card")
def get_ambassador_card(
    ambassador_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    ambassador = db.query(models.Ambassador).filter(models.Ambassador.id == ambassador_id).first()

    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    require_ambassador_access(ambassador, current_user)

    user = db.query(models.User).filter(models.User.id == ambassador.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario del embajador no encontrado")

    return ambassador_card_payload(user, ambassador)


@router.get("/validate/{ambassador_token}", response_class=HTMLResponse)
def validate_ambassador_card(ambassador_token: str, db: Session = Depends(get_db)):
    ambassador = (
        db.query(models.Ambassador)
        .filter(models.Ambassador.ambassador_token == ambassador_token)
        .first()
    )

    if not ambassador:
        return HTMLResponse("<h1>Tarjeta inválida</h1>", status_code=404)

    user = db.query(models.User).filter(models.User.id == ambassador.user_id).first()

    if not user:
        return HTMLResponse("<h1>Usuario no encontrado</h1>", status_code=404)

    status_text = "ACTIVO" if ambassador.is_active else "INACTIVO"
    status_color = "#22c55e" if ambassador.is_active else "#ef4444"

    html_content = f"""
    <html>
        <head>
            <title>Validación de tarjeta Embajador MAYU</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="margin:0; font-family: Arial, sans-serif; background:#0f172a; color:white; display:flex; align-items:center; justify-content:center; min-height:100vh;">
            <div style="max-width:520px; width:90%; background:#1e293b; border-radius:24px; padding:32px;">
                <h1 style="text-align:center; margin-top:0;">MAYU WELLNESS CLUB</h1>
                <div style="text-align:center; margin:20px 0;">
                    <span style="display:inline-block; padding:12px 24px; border-radius:999px; background:{status_color}; color:white; font-weight:bold; font-size:22px;">
                        {status_text}
                    </span>
                </div>
                <p style="font-size:20px;"><strong>Nombre:</strong> {user.name}</p>
                <p style="font-size:20px;"><strong>Email:</strong> {user.email}</p>
                <p style="font-size:20px;"><strong>Celular:</strong> {user.phone or ''}</p>
                <p style="font-size:20px;"><strong>Tipo:</strong> Embajador Mayu</p>
                <p style="font-size:20px;"><strong>Código:</strong> {ambassador.ambassador_code}</p>
                <p style="font-size:20px;"><strong>Vigencia:</strong> Indefinido</p>
            </div>
        </body>
    </html>
    """

    return HTMLResponse(content=html_content, status_code=200)


@router.get("/{ambassador_id}/image")
def generate_ambassador_card_image(ambassador_id: int, db: Session = Depends(get_db)):
    ambassador = db.query(models.Ambassador).filter(models.Ambassador.id == ambassador_id).first()

    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    user = db.query(models.User).filter(models.User.id == ambassador.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario del embajador no encontrado")

    width, height = 950, 560
    base_dir = os.path.dirname(os.path.abspath(__file__))

    bg_path = os.path.join(base_dir, "assets", "embajador_pic.png")
    accent_color = (236, 210, 190)
    text_color = (255, 255, 255)

    if os.path.exists(bg_path):
        image = Image.open(bg_path).convert("RGB").resize((width, height))
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
            logo = Image.open(logo_path).convert("RGBA").resize((170, 170))
            image.paste(logo, ((width - 170) // 2, 25), logo)
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
    )

    draw_text_with_shadow(draw, (60, 305), user.name, name_font, text_color)
    draw_text_with_shadow(draw, (60, 355), "Embajador Mayu", info_font, text_color)
    draw_text_with_shadow(draw, (60, 400), f"Código: {ambassador.ambassador_code}", info_font, text_color)
    draw_text_with_shadow(draw, (60, 440), "Válido: Indefinido", info_font, text_color)
    draw_text_with_shadow(draw, (60, 480), f"Estado: {ambassador.status}", info_font, text_color)

    validation_url = f"{BASE_PUBLIC_URL}/ambassadors/validate/{ambassador.ambassador_token}"

    qr = qrcode.make(validation_url).resize((170, 170))
    image.paste(qr, (735, 340))

    file_path = f"/tmp/ambassador_card_{ambassador_id}.png"
    image.save(file_path)

    return FileResponse(
        path=file_path,
        media_type="image/png",
        content_disposition_type="inline",
    )


@router.get("/{ambassador_id}/dashboard")
def get_ambassador_dashboard(
    ambassador_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    ambassador = db.query(models.Ambassador).filter(models.Ambassador.id == ambassador_id).first()

    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    require_ambassador_access(ambassador, current_user)

    user = db.query(models.User).filter(models.User.id == ambassador.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario del embajador no encontrado")

    referrals = (
        db.query(models.AmbassadorReferral)
        .filter(models.AmbassadorReferral.ambassador_id == ambassador.id)
        .all()
    )

    affiliates = []
    active_referrals = 0
    inactive_referrals = 0
    projected_monthly_commission = 0.00

    for referral in referrals:
        referred_user = db.query(models.User).filter(models.User.id == referral.user_id).first()

        if referred_user:
            affiliate_commission_amount = commission_amount_by_level(referred_user.membership_level)

            if referred_user.membership_active:
                active_referrals += 1
                projected_monthly_commission += affiliate_commission_amount
            else:
                inactive_referrals += 1

            last_order = get_last_order_for_user(db, referred_user.id)

            affiliates.append(
                {
                    "id": referred_user.id,
                    "name": referred_user.name,
                    "email": referred_user.email,
                    "phone": referred_user.phone,
                    "whatsapp_url": build_whatsapp_url(referred_user.phone),
                    "membership_level": referred_user.membership_level,
                    "membership_level_name": level_name(referred_user.membership_level),
                    "membership_active": referred_user.membership_active,
                    "is_active": referred_user.is_active,
                    "city": referred_user.city,
                    "address": referred_user.address,
                    "reference": referred_user.reference,
                    "delivery_notes": referred_user.delivery_notes,
                    "commission_amount": affiliate_commission_amount,
                    "monthly_commission_amount": affiliate_commission_amount if referred_user.membership_active else 0,
                    "commission_rule": f"{level_name(referred_user.membership_level)}: ${affiliate_commission_amount:.2f} mensual por socio activo pagado",
                    **order_tracking_data(last_order),
                    "delivery_history": get_delivery_history_for_user(db, referred_user.id),
                }
            )

    total_referrals = len(affiliates)
    total_payments = 0
    monthly_commission = projected_monthly_commission
    projected_yearly_commission = projected_monthly_commission * 12
    next_payment_day = get_next_payment_day()
    payment_frequency = "Pagos administrativos los días 5 y 21 de cada mes"
    goal = 100

    return {
        "ambassador_id": ambassador.id,
        "user_id": user.id,
        "name": user.name,
        "ambassador_name": user.name,
        "card": ambassador_card_payload(user, ambassador),
        "stats": {
            "total_referrals": total_referrals,
            "active_referrals": active_referrals,
            "inactive_referrals": inactive_referrals,
            "total_payments": total_payments,
            "monthly_commission": monthly_commission,
            "projected_monthly_commission": projected_monthly_commission,
            "projected_yearly_commission": projected_yearly_commission,
            "next_payment_day": next_payment_day,
            "payment_frequency": payment_frequency,
            "goal": goal,
        },
        "commission_projection": {
            "current_active_members": active_referrals,
            "monthly_income": projected_monthly_commission,
            "yearly_income": projected_yearly_commission,
            "next_payment_day": next_payment_day,
            "payment_frequency": payment_frequency,
            "status": "Pendiente de pago administrativo",
            "rule": {
                "Nivel 1 - Cobre": 5,
                "Nivel 2 - Plata": 6,
                "Nivel 3 - Oro": 7,
            },
            "message": f"Con tus socios activos actuales, tu proyección mensual es de ${projected_monthly_commission:.2f}. Se paga administrativamente los días 5 y 21 de cada mes.",
        },
        "reward_progress": {
            "goal": goal,
            "current": active_referrals,
            "reward": "Viaje a la playa",
        },
        "affiliates": affiliates,
    }


@router.get("/{ambassador_id}/affiliates/{user_id}/history")
def get_affiliate_delivery_history(
    ambassador_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    ambassador = db.query(models.Ambassador).filter(models.Ambassador.id == ambassador_id).first()

    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    require_ambassador_access(ambassador, current_user)

    referral = (
        db.query(models.AmbassadorReferral)
        .filter(
            models.AmbassadorReferral.ambassador_id == ambassador.id,
            models.AmbassadorReferral.user_id == user_id,
        )
        .first()
    )

    if not referral:
        raise HTTPException(status_code=403, detail="Este socio no pertenece a este embajador")

    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Socio no encontrado")

    return {
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "phone": user.phone,
            "membership_level": user.membership_level,
            "membership_level_name": level_name(user.membership_level),
            "membership_active": user.membership_active,
        },
        "history": get_delivery_history_for_user(db, user.id),
    }
