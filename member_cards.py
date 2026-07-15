from datetime import datetime, timezone
from email.utils import format_datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Request as FastAPIRequest
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from database import get_db
import models
import uuid
from PIL import Image, ImageDraw, ImageFont
import qrcode
import os
import json
import hashlib
import zipfile
import tempfile
import time
import jwt
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleAuthRequest

from cryptography.hazmat.primitives.serialization import pkcs12, Encoding
from cryptography.hazmat.primitives.serialization.pkcs7 import (
    PKCS7SignatureBuilder,
    PKCS7Options,
)
from cryptography.hazmat.primitives import hashes
from cryptography import x509

router = APIRouter(prefix="/member-cards", tags=["Member Cards"])
security = HTTPBearer()

BASE_PUBLIC_URL = "https://mayu-wellness-backend-v1.onrender.com"
CARD_VALIDITY_TEXT = "Indefinido"
CLUB_NAME = "Mayu Wellness Club"
MEMBER_WALLET_AUTH_PREFIX = "mayu-member-wallet"


class AppleWalletRegistrationRequest(BaseModel):
    pushToken: str


def generate_member_code(user_id: int, level: int):
    return f"MAYU-{level}-{user_id:06d}"


def generate_ambassador_code(ambassador_id: int):
    return f"EMB-{ambassador_id:06d}"


def get_ambassador_by_user(db: Session, user_id: int):
    return db.query(models.Ambassador).filter(models.Ambassador.user_id == user_id).first()


def ambassador_commission_amount_by_level(level):
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


def ambassador_commission_summary(db: Session | None, user):
    if not db or not user or user.role != "ambassador":
        return None

    ambassador = get_ambassador_by_user(db, user.id)
    if not ambassador:
        return {
            "ambassador_id": None,
            "total_pending": 0.0,
            "total_paid": 0.0,
            "total_generated": 0.0,
            "projected_monthly_commission": 0.0,
            "active_referrals": 0,
            "current_display_amount": 0.0,
            "current_display_label": "Ganancia pendiente",
        }

    commissions = (
        db.query(models.Commission)
        .filter(models.Commission.ambassador_id == ambassador.id)
        .all()
    )

    referrals = (
        db.query(models.AmbassadorReferral)
        .filter(models.AmbassadorReferral.ambassador_id == ambassador.id)
        .all()
    )

    projected_monthly_commission = 0.0
    active_referrals = 0
    latest_referral_change = None

    for referral in referrals:
        referred_user = db.query(models.User).filter(models.User.id == referral.user_id).first()
        if not referred_user:
            continue

        if referred_user.membership_active:
            active_referrals += 1
            projected_monthly_commission += ambassador_commission_amount_by_level(
                referred_user.membership_level
            )

        current_referral_dt = getattr(referred_user, "updated_at", None) or getattr(
            referred_user, "created_at", None
        )
        if current_referral_dt and (
            latest_referral_change is None or current_referral_dt > latest_referral_change
        ):
            latest_referral_change = current_referral_dt

    total_pending = round(
        sum(float(c.commission_amount or 0) for c in commissions if c.status == "pending"),
        2,
    )
    total_paid = round(
        sum(float(c.commission_amount or 0) for c in commissions if c.status == "paid"),
        2,
    )
    total_generated = round(
        sum(float(c.commission_amount or 0) for c in commissions),
        2,
    )
    latest_commission_at = None
    for item in commissions:
        current = item.paid_at or item.generated_at
        if current and (latest_commission_at is None or current > latest_commission_at):
            latest_commission_at = current

    projected_monthly_commission = round(projected_monthly_commission, 2)
    current_display_amount = total_pending
    current_display_label = "Ganancia pendiente"

    latest_activity_at = latest_commission_at
    if latest_referral_change and (
        latest_activity_at is None or latest_referral_change > latest_activity_at
    ):
        latest_activity_at = latest_referral_change

    return {
        "ambassador_id": ambassador.id,
        "total_pending": total_pending,
        "total_paid": total_paid,
        "total_generated": total_generated,
        "projected_monthly_commission": projected_monthly_commission,
        "active_referrals": active_referrals,
        "current_display_amount": round(current_display_amount, 2),
        "current_display_label": current_display_label,
        "latest_commission_at": latest_activity_at,
    }


def get_user_card_type(user):
    return "ambassador" if user and user.role == "ambassador" else "member"


def get_card_level_snapshot(user):
    return 9 if user.role == "ambassador" else user.membership_level


def get_card_status(user):
    if user.role == "ambassador":
        return "active" if getattr(user, "is_active", True) else "inactive"
    return "active" if user.membership_active else "inactive"


def get_card_code(db: Session, user):
    if user.role == "ambassador":
        ambassador = get_ambassador_by_user(db, user.id)
        if ambassador:
            correct_code = generate_ambassador_code(ambassador.id)
            if ambassador.ambassador_code != correct_code:
                ambassador.ambassador_code = correct_code
                db.commit()
                db.refresh(ambassador)
            return correct_code
        return f"EMB-{user.id:06d}"

    return generate_member_code(user.id, user.membership_level)


def get_or_create_card(db: Session, user_id: int):
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role not in ["member", "ambassador"]:
        raise HTTPException(status_code=400, detail="Solo socios o embajadores pueden tener tarjeta")

    if user.role == "member" and not user.membership_level:
        raise HTTPException(status_code=400, detail="El socio no tiene membresía asignada")

    level_snapshot = get_card_level_snapshot(user)
    member_code = get_card_code(db, user)
    card_status = get_card_status(user)

    card = db.query(models.MemberCard).filter(models.MemberCard.user_id == user.id).first()

    if card:
        card.member_code = member_code
        card.level_snapshot = level_snapshot
        card.status = card_status
        card.expires_at = CARD_VALIDITY_TEXT
        db.commit()
        db.refresh(card)
        return user, card

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

    return user, card


def level_text(user, card):
    if user.role == "ambassador":
        return "Embajador Mayu"

    levels = {
        1: "Nivel 1 - Cobre",
        2: "Nivel 2 - Plata",
        3: "Nivel 3 - Oro",
    }

    return levels.get(card.level_snapshot, "Socio Mayu")


def member_wallet_auth_token(card):
    return f"{MEMBER_WALLET_AUTH_PREFIX}-{card.qr_token}"


def member_apple_serial(card):
    return f"member-card-{card.member_code}-{card.id}".lower()


def get_member_card_by_apple_serial(db: Session, serial_number: str):
    cards = db.query(models.MemberCard).all()

    for card in cards:
        expected_serial = member_apple_serial(card)
        legacy_prefix = f"{card.member_code}-{card.id}-".lower()
        serial_lower = serial_number.lower()
        if serial_lower == expected_serial or serial_lower.startswith(legacy_prefix):
            user = db.query(models.User).filter(models.User.id == card.user_id).first()
            if user:
                return user, card

    raise HTTPException(status_code=404, detail="Tarjeta Mayu no encontrada para Apple Wallet")


def extract_wallet_auth_token(request: FastAPIRequest):
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth_header:
        return None
    if auth_header.lower().startswith("applepass "):
        return auth_header.split(" ", 1)[1].strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return auth_header.strip()


def verify_member_wallet_request(request: FastAPIRequest, card):
    token = extract_wallet_auth_token(request)
    expected = member_wallet_auth_token(card)
    if token != expected:
        raise HTTPException(status_code=401, detail="Apple Wallet token inválido")


def member_apple_last_updated(db: Session, user, card):
    summary = ambassador_commission_summary(db, user)
    latest = summary.get("latest_commission_at") if summary else None
    base_dt = latest or user.created_at or datetime.utcnow()
    return format_datetime(base_dt.replace(tzinfo=timezone.utc), usegmt=True)


def get_card_visual_data(db: Session | None, user, card):
    if user.role == "ambassador":
        ambassador = get_ambassador_by_user(db, user.id) if db else None
        ambassador_id = ambassador.id if ambassador else user.id

        return {
            "display_name": user.name,
            "level_text": "Embajador Mayu",
            "member_code": generate_ambassador_code(ambassador_id),
            "bg_file": "embajador_pic.png",
            "wallet_file": "wallet_embajador.png",
            "fallback_color": (0, 120, 110),
            "accent_color": (255, 236, 170),
            "hex_color": "#0F766E",
        }

    if card.level_snapshot == 1:
        return {
            "display_name": user.name,
            "level_text": "Nivel 1 - Cobre",
            "member_code": card.member_code,
            "bg_file": "card_cobre.jpg",
            "wallet_file": "wallet_cobre.png",
            "fallback_color": (176, 111, 79),
            "accent_color": (236, 210, 190),
            "hex_color": "#8B5A3C",
        }

    if card.level_snapshot == 2:
        return {
            "display_name": user.name,
            "level_text": "Nivel 2 - Plata",
            "member_code": card.member_code,
            "bg_file": "card_plata.jpg",
            "wallet_file": "wallet_plata.png",
            "fallback_color": (205, 210, 214),
            "accent_color": (245, 245, 245),
            "hex_color": "#6B7280",
        }

    return {
        "display_name": user.name,
        "level_text": "Nivel 3 - Oro",
        "member_code": card.member_code,
        "bg_file": "card_oro.jpg",
        "wallet_file": "wallet_oro.png",
        "fallback_color": (15, 23, 42),
        "accent_color": (255, 236, 170),
        "hex_color": "#0F172A",
    }


def card_response(db: Session, user, card):
    ambassador_summary = ambassador_commission_summary(db, user)
    return {
        "id": card.id,
        "user_id": card.user_id,
        "member_code": card.member_code,
        "qr_token": card.qr_token,
        "level_snapshot": card.level_snapshot,
        "status": card.status,
        "expires_at": card.expires_at or CARD_VALIDITY_TEXT,
        "card_type": get_user_card_type(user),
        "image_url": f"{BASE_PUBLIC_URL}/member-cards/user/{user.id}/image",
        "web_url": f"{BASE_PUBLIC_URL}/member-cards/user/{user.id}/web",
        "apple_wallet_url": f"{BASE_PUBLIC_URL}/member-cards/apple-wallet/{user.id}",
        "google_wallet_url": f"{BASE_PUBLIC_URL}/member-cards/google-wallet/{user.id}",
        "wallet_pending_label": ambassador_summary["current_display_label"] if ambassador_summary else None,
        "wallet_pending_amount": ambassador_summary["current_display_amount"] if ambassador_summary else None,
        "wallet_paid_amount": ambassador_summary["total_paid"] if ambassador_summary else None,
        "wallet_projected_amount": ambassador_summary["projected_monthly_commission"] if ambassador_summary else None,
    }


@router.get("/assets/{filename}")
def get_wallet_asset(filename: str):
    allowed_files = {
        "wallet_cobre.png",
        "wallet_plata.png",
        "wallet_oro.png",
        "wallet_embajador.png",
        "logo_mayu.png",
        "card_cobre.jpg",
        "card_plata.jpg",
        "card_oro.jpg",
        "embajador_pic.png",
    }

    if filename not in allowed_files:
        raise HTTPException(status_code=404, detail="Asset no permitido")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(base_dir, "assets", filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"No existe assets/{filename}")

    media_type = "image/png" if filename.endswith(".png") else "image/jpeg"
    return FileResponse(path=file_path, media_type=media_type)


@router.post("/generate/{user_id}")
def generate_member_card(user_id: int, db: Session = Depends(get_db)):
    user, card = get_or_create_card(db, user_id)
    return {"message": "Tarjeta generada correctamente", "card": card_response(db, user, card)}


@router.get("/user/{user_id}")
def get_member_card_by_user(user_id: int, db: Session = Depends(get_db)):
    user, card = get_or_create_card(db, user_id)
    return card_response(db, user, card)


@router.get("/user/{user_id}/web", response_class=HTMLResponse)
def get_member_card_web(user_id: int, db: Session = Depends(get_db)):
    user, card = get_or_create_card(db, user_id)

    image_url = f"{BASE_PUBLIC_URL}/member-cards/user/{user.id}/image"
    validate_url = f"{BASE_PUBLIC_URL}/member-cards/validate/{card.qr_token}"
    apple_wallet_url = f"{BASE_PUBLIC_URL}/member-cards/apple-wallet/{user.id}"
    google_wallet_url = f"{BASE_PUBLIC_URL}/member-cards/google-wallet/{user.id}"
    ambassador_summary = ambassador_commission_summary(db, user)
    ambassador_block = ""

    if ambassador_summary:
        ambassador_block = f"""
                    <div style="margin-top:18px; padding:16px; border-radius:18px; background:#0f766e;">
                        <p><strong>{ambassador_summary['current_display_label']}:</strong> ${ambassador_summary['current_display_amount']:.2f}</p>
                        <p><strong>Próxima comisión:</strong> ${ambassador_summary['projected_monthly_commission']:.2f}</p>
                        <p><strong>Pagado acumulado:</strong> ${ambassador_summary['total_paid']:.2f}</p>
                    </div>
        """

    html = f"""
    <html>
        <head>
            <title>{CLUB_NAME}</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="margin:0; font-family:Arial,sans-serif; background:#0f172a; color:white; padding:24px;">
            <div style="max-width:720px; margin:auto;">
                <h1 style="text-align:center;">{CLUB_NAME}</h1>
                <div style="background:#1e293b; border-radius:24px; padding:20px; text-align:center;">
                    <img src="{image_url}" style="max-width:100%; border-radius:18px;" />
                    <h2>{user.name}</h2>
                    <p><strong>Tipo:</strong> {level_text(user, card)}</p>
                    <p><strong>Código:</strong> {card.member_code}</p>
                    <p><strong>Estado:</strong> {card.status}</p>
                    <p><strong>Vigencia:</strong> {CARD_VALIDITY_TEXT}</p>
                    {ambassador_block}

                    <a href="{validate_url}" style="display:inline-block; margin-top:16px; padding:12px 20px; background:#14b8a6; color:white; text-decoration:none; border-radius:999px;">Validar tarjeta</a>
                    <br/>
                    <a href="{apple_wallet_url}" style="display:inline-block; margin-top:12px; padding:12px 20px; background:#000; color:white; text-decoration:none; border-radius:999px;">Agregar a Apple Wallet</a>
                    <br/>
                    <a href="{google_wallet_url}" style="display:inline-block; margin-top:12px; padding:12px 20px; background:#0f9d58; color:white; text-decoration:none; border-radius:999px;">Agregar a Google Wallet</a>
                </div>
            </div>
        </body>
    </html>
    """

    return HTMLResponse(content=html)


@router.get("/validate/{qr_token}", response_class=HTMLResponse)
def validate_member_card(qr_token: str, db: Session = Depends(get_db)):
    card = db.query(models.MemberCard).filter(models.MemberCard.qr_token == qr_token).first()

    if not card:
        return HTMLResponse("<h1>Tarjeta inválida</h1>", status_code=404)

    user = db.query(models.User).filter(models.User.id == card.user_id).first()

    if not user:
        return HTMLResponse("<h1>Usuario no encontrado</h1>", status_code=404)

    card.status = get_card_status(user)
    db.commit()
    db.refresh(card)

    status_text = "ACTIVA" if card.status == "active" else "INACTIVA"

    if user.role == "ambassador":
        status_text = "EMBAJADOR ACTIVO" if card.status == "active" else "EMBAJADOR INACTIVO"

    status_color = "#22c55e" if card.status == "active" else "#ef4444"

    html_content = f"""
    <html>
        <head>
            <title>Validación MAYU</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="background:#0f172a; color:white; font-family:Arial; padding:40px;">
            <div style="max-width:520px; margin:auto; background:#1e293b; border-radius:24px; padding:32px;">
                <h1 style="text-align:center;">{CLUB_NAME}</h1>
                <div style="text-align:center; margin:20px 0;">
                    <span style="padding:12px 24px; border-radius:999px; background:{status_color}; color:white;">{status_text}</span>
                </div>
                <p><strong>Nombre:</strong> {user.name}</p>
                <p><strong>Email:</strong> {user.email}</p>
                <p><strong>Tipo:</strong> {level_text(user, card)}</p>
                <p><strong>Código:</strong> {card.member_code}</p>
                <p><strong>Vigencia:</strong> {CARD_VALIDITY_TEXT}</p>
            </div>
        </body>
    </html>
    """

    return HTMLResponse(content=html_content)


def draw_text(draw, position, text, font):
    draw.text(position, text, fill=(255, 255, 255), font=font)


@router.get("/user/{user_id}/image")
def generate_card_image(user_id: int, db: Session = Depends(get_db)):
    user, card = get_or_create_card(db, user_id)
    visual = get_card_visual_data(db, user, card)
    ambassador_summary = ambassador_commission_summary(db, user)

    width, height = 950, 560
    base_dir = os.path.dirname(os.path.abspath(__file__))

    bg_path = os.path.join(base_dir, "assets", visual["bg_file"])
    logo_path = os.path.join(base_dir, "assets", "logo_mayu.png")

    if os.path.exists(bg_path):
        image = Image.open(bg_path).convert("RGB").resize((width, height))
    else:
        image = Image.new("RGB", (width, height), visual["fallback_color"])

    draw = ImageDraw.Draw(image)

    try:
        club_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 44)
        name_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 42)
        info_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 30)
    except Exception:
        club_font = ImageFont.load_default()
        name_font = ImageFont.load_default()
        info_font = ImageFont.load_default()

    draw.rounded_rectangle(
        [(18, 18), (width - 18, height - 18)],
        radius=30,
        outline=visual["accent_color"],
        width=4,
    )

    if os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA").resize((180, 180))
            image.paste(logo, ((width - 180) // 2, 20), logo)
        except Exception:
            pass

    bbox = draw.textbbox((0, 0), CLUB_NAME.upper(), font=club_font)
    club_width = bbox[2] - bbox[0]
    draw_text(draw, ((width - club_width) // 2, 205), CLUB_NAME.upper(), club_font)

    draw_text(draw, (60, 305), visual["display_name"], name_font)
    if ambassador_summary:
        current_label = ambassador_summary["current_display_label"]
        draw_text(
            draw,
            (60, 355),
            f"{current_label}: ${ambassador_summary['current_display_amount']:.2f}",
            info_font,
        )
        draw_text(
            draw,
            (60, 400),
            f"Proxima comision: ${ambassador_summary['projected_monthly_commission']:.2f}",
            info_font,
        )
        draw_text(
            draw,
            (60, 440),
            f"Pagado acumulado: ${ambassador_summary['total_paid']:.2f}",
            info_font,
        )
        draw_text(draw, (60, 480), f"Codigo: {visual['member_code']}", info_font)
    else:
        draw_text(draw, (60, 355), visual["level_text"], info_font)
        draw_text(draw, (60, 400), f"Código: {visual['member_code']}", info_font)
        draw_text(draw, (60, 440), f"Válido hasta: {CARD_VALIDITY_TEXT}", info_font)
        draw_text(draw, (60, 480), f"Estado: {card.status}", info_font)

    validation_url = f"{BASE_PUBLIC_URL}/member-cards/validate/{card.qr_token}"
    qr = qrcode.make(validation_url).resize((170, 170))
    image.paste(qr, (735, 340))

    file_path = f"/tmp/card_{user_id}.png"
    image.save(file_path)

    return FileResponse(path=file_path, media_type="image/png", content_disposition_type="inline")


def rgb_string(color_tuple):
    return f"rgb({color_tuple[0]},{color_tuple[1]},{color_tuple[2]})"


def create_wallet_icon(output_path: str):
    img = Image.new("RGB", (180, 180), (15, 23, 42))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 38)
    except Exception:
        font = ImageFont.load_default()

    draw.text((22, 68), "MAYU", fill=(255, 236, 170), font=font)
    img.save(output_path)


def fit_image_to_canvas(source_path: str, target_path: str, size: tuple, bg_color=(15, 23, 42)):
    img = Image.open(source_path).convert("RGBA")
    img.thumbnail(size, Image.LANCZOS)

    canvas = Image.new("RGBA", size, bg_color + (255,))
    canvas.paste(
        img,
        ((size[0] - img.size[0]) // 2, (size[1] - img.size[1]) // 2),
        img,
    )
    canvas.convert("RGB").save(target_path)


def cover_image_to_canvas(source_path: str, target_path: str, size: tuple, bg_color=(15, 23, 42)):
    img = Image.open(source_path).convert("RGB")
    img_ratio = img.width / img.height
    target_ratio = size[0] / size[1]

    if img_ratio > target_ratio:
        new_height = size[1]
        new_width = int(new_height * img_ratio)
    else:
        new_width = size[0]
        new_height = int(new_width / img_ratio)

    img = img.resize((new_width, new_height), Image.LANCZOS)

    left = (new_width - size[0]) // 2
    top = (new_height - size[1]) // 2
    img = img.crop((left, top, left + size[0], top + size[1]))

    canvas = Image.new("RGB", size, bg_color)
    canvas.paste(img, (0, 0))
    canvas.save(target_path)


def copy_or_create_wallet_images(pass_dir: str, user, card):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    logo_path = os.path.join(base_dir, "assets", "logo_mayu.png")

    visual = get_card_visual_data(None, user, card)
    wallet_image_path = os.path.join(base_dir, "assets", visual["wallet_file"])

    for filename, size in [
        ("icon.png", (29, 29)),
        ("icon@2x.png", (58, 58)),
    ]:
        target = os.path.join(pass_dir, filename)

        if os.path.exists(logo_path):
            fit_image_to_canvas(logo_path, target, size, visual["fallback_color"])
        else:
            create_wallet_icon(target)

    for filename, size in [
        ("logo.png", (70, 26)),
        ("logo@2x.png", (140, 52)),
    ]:
        target = os.path.join(pass_dir, filename)

        if os.path.exists(logo_path):
            fit_image_to_canvas(logo_path, target, size, visual["fallback_color"])
        else:
            create_wallet_icon(target)

    if os.path.exists(wallet_image_path):
        cover_image_to_canvas(
            wallet_image_path,
            os.path.join(pass_dir, "strip.png"),
            (375, 123),
            visual["fallback_color"],
        )
        cover_image_to_canvas(
            wallet_image_path,
            os.path.join(pass_dir, "strip@2x.png"),
            (750, 246),
            visual["fallback_color"],
        )


def load_wwdr_certificate(path: str):
    with open(path, "rb") as f:
        data = f.read()

    try:
        return x509.load_der_x509_certificate(data)
    except Exception:
        return x509.load_pem_x509_certificate(data)


def build_manifest(pass_dir: str):
    manifest = {}

    for filename in os.listdir(pass_dir):
        if filename in ["manifest.json", "signature"]:
            continue

        file_path = os.path.join(pass_dir, filename)

        if os.path.isfile(file_path):
            with open(file_path, "rb") as f:
                manifest[filename] = hashlib.sha1(f.read()).hexdigest()

    with open(os.path.join(pass_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, separators=(",", ":"))


def sign_manifest(pass_dir: str, certs_dir: str):
    p12_path = os.path.join(certs_dir, "mayu_wallet.p12")
    wwdr_path = os.path.join(certs_dir, "AppleWWDRCAG3.cer")

    password = os.getenv("APPLE_WALLET_P12_PASSWORD") or os.getenv("APPLE_WALLET_CERT_PASSWORD")

    if not os.path.exists(p12_path):
        raise HTTPException(status_code=500, detail="No existe certs/mayu_wallet.p12")

    if not password:
        raise HTTPException(status_code=500, detail="Falta APPLE_WALLET_P12_PASSWORD en Render")

    with open(p12_path, "rb") as f:
        private_key, certificate, _ = pkcs12.load_key_and_certificates(f.read(), password.encode())

    if private_key is None or certificate is None:
        raise HTTPException(status_code=500, detail="El .p12 no contiene certificado y clave privada")

    with open(os.path.join(pass_dir, "manifest.json"), "rb") as f:
        manifest_data = f.read()

    builder = PKCS7SignatureBuilder().set_data(manifest_data)
    builder = builder.add_signer(certificate, private_key, hashes.SHA256())

    if os.path.exists(wwdr_path):
        builder = builder.add_certificate(load_wwdr_certificate(wwdr_path))

    signature = builder.sign(Encoding.DER, [PKCS7Options.DetachedSignature, PKCS7Options.Binary])

    with open(os.path.join(pass_dir, "signature"), "wb") as f:
        f.write(signature)


def zip_pkpass(pass_dir: str, output_path: str):
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as z:
        for filename in os.listdir(pass_dir):
            file_path = os.path.join(pass_dir, filename)
            if os.path.isfile(file_path):
                z.write(file_path, filename)


def build_member_apple_wallet_file(
    db: Session,
    user,
    card,
    serial_number_override: str | None = None,
):
    pass_type_id = os.getenv("APPLE_PASS_TYPE_ID")
    team_id = os.getenv("APPLE_TEAM_ID")
    organization_name = os.getenv("APPLE_ORGANIZATION_NAME", CLUB_NAME)

    if not pass_type_id:
        raise HTTPException(status_code=500, detail="Falta APPLE_PASS_TYPE_ID")

    if not team_id:
        raise HTTPException(status_code=500, detail="Falta APPLE_TEAM_ID")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    certs_dir = os.path.join(base_dir, "certs")

    if not os.path.exists(certs_dir):
        certs_dir = os.path.join(os.getcwd(), "certs")

    temp_dir = tempfile.mkdtemp(prefix=f"mayu_pkpass_{user.id}_")
    pass_dir = os.path.join(temp_dir, "pass")
    os.makedirs(pass_dir, exist_ok=True)

    try:
        validate_url = f"{BASE_PUBLIC_URL}/member-cards/validate/{card.qr_token}"
        card_web_url = f"{BASE_PUBLIC_URL}/member-cards/user/{user.id}/web"
        visual = get_card_visual_data(db, user, card)
        ambassador_summary = ambassador_commission_summary(db, user)
        pending_value = (
            f"${ambassador_summary['current_display_amount']:.2f}" if ambassador_summary else None
        )
        projected_value = (
            f"${ambassador_summary['projected_monthly_commission']:.2f}"
            if ambassador_summary
            else None
        )
        paid_value = (
            f"${ambassador_summary['total_paid']:.2f}" if ambassador_summary else None
        )
        primary_label = (
            ambassador_summary["current_display_label"].upper()
            if ambassador_summary
            else "SOCIO MAYU"
        )
        primary_value = pending_value or user.name
        secondary_fields = [
            {
                "key": "level",
                "label": "MEMBRESÍA",
                "value": level_text(user, card),
            },
            {
                "key": "status",
                "label": "ESTADO",
                "value": "Activo" if card.status == "active" else "Inactivo",
            },
        ]
        if ambassador_summary:
            secondary_fields = [
                {
                    "key": "name",
                    "label": "EMBAJADOR",
                    "value": user.name,
                },
                {
                    "key": "paid_total",
                    "label": "PAGADO",
                    "value": paid_value,
                },
            ]

        pass_json = {
            "formatVersion": 1,
            "passTypeIdentifier": pass_type_id,
            "serialNumber": serial_number_override or member_apple_serial(card),
            "teamIdentifier": team_id,
            "organizationName": organization_name,
            "description": CLUB_NAME,
            "logoText": CLUB_NAME.upper(),
            "webServiceURL": f"{BASE_PUBLIC_URL}/member-cards/wallet/apple",
            "authenticationToken": member_wallet_auth_token(card),
            "foregroundColor": "rgb(255,255,255)",
            "backgroundColor": "rgb(15,23,42)",
            "labelColor": "rgb(255,236,170)",
            "suppressStripShine": True,
            "sharingProhibited": False,
            "storeCard": {
                "primaryFields": [
                    {
                        "key": "primary",
                        "label": primary_label,
                        "value": primary_value,
                    }
                ],
                "secondaryFields": secondary_fields,
                "auxiliaryFields": [
                    {
                        "key": "code",
                        "label": "CÓDIGO",
                        "value": card.member_code,
                    },
                    *(
                        [
                            {
                                "key": "projected_total",
                                "label": "PRÓXIMA COMISIÓN",
                                "value": projected_value,
                            }
                        ]
                        if ambassador_summary
                        else [
                            {
                                "key": "valid",
                                "label": "VIGENCIA",
                                "value": CARD_VALIDITY_TEXT,
                            }
                        ]
                    ),
                ],
                "backFields": [
                    {"key": "valid_back", "label": "Vigencia", "value": CARD_VALIDITY_TEXT},
                    {"key": "email", "label": "Email", "value": user.email},
                    {"key": "phone", "label": "Celular", "value": user.phone or "-"},
                    *(
                        [
                            {
                                "key": "pending_back",
                                "label": ambassador_summary["current_display_label"],
                                "value": pending_value,
                            },
                            {
                                "key": "paid_back",
                                "label": "Pagado acumulado",
                                "value": paid_value,
                            },
                            {
                                "key": "projected_back",
                                "label": "Próxima comisión",
                                "value": projected_value,
                            },
                        ]
                        if ambassador_summary
                        else []
                    ),
                    {"key": "web", "label": "Tarjeta web", "value": card_web_url},
                ],
            },
            "barcode": {
                "format": "PKBarcodeFormatQR",
                "message": validate_url,
                "messageEncoding": "iso-8859-1",
                "altText": card.member_code,
            },
        }

        with open(os.path.join(pass_dir, "pass.json"), "w", encoding="utf-8") as f:
            json.dump(pass_json, f, ensure_ascii=False, separators=(",", ":"))

        copy_or_create_wallet_images(pass_dir, user, card)
        build_manifest(pass_dir)
        sign_manifest(pass_dir, certs_dir)

        output_path = os.path.join(temp_dir, f"mayu_wallet_{user.id}.pkpass")
        zip_pkpass(pass_dir, output_path)
        return output_path

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generando Apple Wallet: {str(e)}")


@router.get("/apple-wallet/{user_id}")
def generate_apple_wallet_pass(user_id: int, db: Session = Depends(get_db)):
    user, card = get_or_create_card(db, user_id)
    output_path = build_member_apple_wallet_file(db, user, card)
    return FileResponse(
        path=output_path,
        media_type="application/vnd.apple.pkpass",
        filename=f"mayu_wallet_{user_id}.pkpass",
    )


def get_google_wallet_service_account():
    raw_json = os.getenv("GOOGLE_WALLET_SERVICE_ACCOUNT_JSON")

    if not raw_json:
        raise HTTPException(status_code=500, detail="Falta GOOGLE_WALLET_SERVICE_ACCOUNT_JSON en Render")

    try:
        return json.loads(raw_json)
    except Exception:
        raise HTTPException(status_code=500, detail="GOOGLE_WALLET_SERVICE_ACCOUNT_JSON no es JSON válido")


def clean_google_private_key(private_key: str):
    return private_key.replace("\\n", "\n").strip()


def member_google_class_suffix() -> str:
    configured = os.getenv("GOOGLE_WALLET_CLASS_SUFFIX", "").strip()
    if not configured or configured == "mayu_membership":
        return "mayu_membership_generic_v2"
    return configured


def ensure_member_google_wallet_class(service_account_info: dict, class_id: str):
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/wallet_object.issuer"],
    )
    credentials.refresh(GoogleAuthRequest())
    headers = {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
    }
    url = f"https://walletobjects.googleapis.com/walletobjects/v1/genericClass/{class_id}"
    existing = requests.get(url, headers=headers, timeout=20)
    if existing.status_code == 200:
        return
    if existing.status_code != 404:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo verificar clase Google Wallet socios: {existing.text[:500]}",
        )

    class_body = {
        "id": class_id,
        "issuerName": CLUB_NAME,
        "reviewStatus": "UNDER_REVIEW",
        "hexBackgroundColor": "#0F172A",
        "localizedIssuerName": {
            "defaultValue": {"language": "es", "value": CLUB_NAME}
        },
        "homepageUri": {
            "uri": BASE_PUBLIC_URL,
            "description": CLUB_NAME,
        },
    }
    created = requests.post(
        "https://walletobjects.googleapis.com/walletobjects/v1/genericClass",
        headers=headers,
        json=class_body,
        timeout=20,
    )
    if created.status_code not in {200, 201}:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo crear clase Google Wallet socios: {created.text[:500]}",
        )


def build_google_wallet_object_body(user, card, issuer_id: str, class_id: str):
    visual = get_card_visual_data(None, user, card)
    object_suffix = f"{card.member_code}_{card.id}_{card.level_snapshot}_{card.status}".replace("-", "_").lower()
    object_id = f"{issuer_id}.{object_suffix}"

    validate_url = f"{BASE_PUBLIC_URL}/member-cards/validate/{card.qr_token}"
    full_card_image_url = f"{BASE_PUBLIC_URL}/member-cards/user/{user.id}/image?v={card.id}-{card.level_snapshot}-{card.status}-{uuid.uuid4()}"
    logo_url = f"{BASE_PUBLIC_URL}/member-cards/assets/logo_mayu.png"
    card_web_url = f"{BASE_PUBLIC_URL}/member-cards/user/{user.id}/web"

    ambassador_summary = ambassador_commission_summary(None, user)
    if user.role == "ambassador":
        # Requery summary when wallet object is built from a request with DB-backed user.
        try:
            from database import SessionLocal

            temp_db = SessionLocal()
            try:
                fresh_user = temp_db.query(models.User).filter(models.User.id == user.id).first() or user
                ambassador_summary = ambassador_commission_summary(temp_db, fresh_user)
            finally:
                temp_db.close()
        except Exception:
            ambassador_summary = ambassador_summary or None

    text_modules = [
        {"id": "membership", "header": "Membresía", "body": level_text(user, card)},
        {"id": "status", "header": "Estado", "body": "Activo" if card.status == "active" else "Inactivo"},
        {"id": "code", "header": "Código", "body": card.member_code},
        {"id": "valid", "header": "Vigencia", "body": CARD_VALIDITY_TEXT},
    ]

    if ambassador_summary:
        text_modules = [
            {
                "id": "pending",
                "header": ambassador_summary["current_display_label"],
                "body": f"${ambassador_summary['current_display_amount']:.2f}",
            },
            {
                "id": "projected_total",
                "header": "Próxima comisión",
                "body": f"${ambassador_summary['projected_monthly_commission']:.2f}",
            },
            {
                "id": "paid_total",
                "header": "Pagado acumulado",
                "body": f"${ambassador_summary['total_paid']:.2f}",
            },
            {"id": "type", "header": "Tipo", "body": "Embajador Mayu"},
            {"id": "code", "header": "Código", "body": card.member_code},
        ]

    return {
        "id": object_id,
        "classId": class_id,
        "state": "ACTIVE" if card.status == "active" else "INACTIVE",
        "hexBackgroundColor": visual["hex_color"],
        "logo": {
            "sourceUri": {"uri": logo_url},
            "contentDescription": {"defaultValue": {"language": "es", "value": CLUB_NAME}},
        },
        "heroImage": {
            "sourceUri": {"uri": full_card_image_url},
            "contentDescription": {"defaultValue": {"language": "es", "value": f"Tarjeta {CLUB_NAME}"}},
        },
        "imageModulesData": [
            {
                "id": "card_design",
                "mainImage": {
                    "sourceUri": {"uri": full_card_image_url},
                    "contentDescription": {"defaultValue": {"language": "es", "value": f"Tarjeta digital {CLUB_NAME}"}},
                },
            }
        ],
        "cardTitle": {"defaultValue": {"language": "es", "value": CLUB_NAME}},
        "header": {"defaultValue": {"language": "es", "value": user.name}},
        "subheader": {"defaultValue": {"language": "es", "value": f"{level_text(user, card)} · {card.member_code}"}},
        "barcode": {
            "type": "QR_CODE",
            "value": validate_url,
            "alternateText": card.member_code,
        },
        "textModulesData": text_modules,
        "linksModuleData": {
            "uris": [
                {"id": "validate", "uri": validate_url, "description": "Validar tarjeta"},
                {"id": "web", "uri": card_web_url, "description": "Ver tarjeta web"},
            ]
        },
    }


def member_google_wallet_text_signature(wallet_object: dict):
    items = []
    for module in wallet_object.get("textModulesData", []) or []:
        items.append(
            {
                "id": module.get("id"),
                "header": module.get("header"),
                "body": module.get("body"),
            }
        )
    return items


def member_google_wallet_visual_signature(wallet_object: dict):
    hero_uri = (
        ((wallet_object.get("heroImage") or {}).get("sourceUri") or {}).get("uri")
    )
    image_uri = None
    image_modules = wallet_object.get("imageModulesData", []) or []
    if image_modules:
        image_uri = (
            (((image_modules[0] or {}).get("mainImage") or {}).get("sourceUri") or {}).get("uri")
        )

    return {
        "header": ((wallet_object.get("header") or {}).get("defaultValue") or {}).get("value"),
        "subheader": ((wallet_object.get("subheader") or {}).get("defaultValue") or {}).get("value"),
        "hero_uri": hero_uri,
        "image_uri": image_uri,
        "text_modules": member_google_wallet_text_signature(wallet_object),
    }


def build_google_wallet_save_url(user, card):
    issuer_id = os.getenv("GOOGLE_WALLET_ISSUER_ID")
    class_suffix = member_google_class_suffix()

    if not issuer_id:
        raise HTTPException(status_code=500, detail="Falta GOOGLE_WALLET_ISSUER_ID en Render")

    service_account = get_google_wallet_service_account()

    client_email = service_account.get("client_email")
    private_key = service_account.get("private_key")

    if not client_email or not private_key:
        raise HTTPException(status_code=500, detail="JSON de Google Wallet incompleto")

    private_key = clean_google_private_key(private_key)
    class_id = f"{issuer_id}.{class_suffix}"
    ensure_member_google_wallet_class(service_account, class_id)
    generic_object = upsert_member_google_wallet_object(
        service_account,
        user,
        card,
        issuer_id,
        class_id,
    )

    claims = {
        "iss": client_email,
        "aud": "google",
        "typ": "savetowallet",
        "payload": {
            "genericObjects": [generic_object],
        },
    }

    token = jwt.encode(claims, private_key, algorithm="RS256")
    return f"https://pay.google.com/gp/v/save/{token}"


@router.get("/google-wallet/{user_id}")
def generate_google_wallet_pass(user_id: int, db: Session = Depends(get_db)):
    user, card = get_or_create_card(db, user_id)
    save_url = build_google_wallet_save_url(user, card)
    return RedirectResponse(url=save_url)


def upsert_member_google_wallet_object(
    service_account_info: dict,
    user,
    card,
    issuer_id: str,
    class_id: str,
):
    object_body = build_google_wallet_object_body(user, card, issuer_id, class_id)
    object_id = object_body["id"]
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/wallet_object.issuer"],
    )
    credentials.refresh(GoogleAuthRequest())
    headers = {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
    }
    object_url = f"https://walletobjects.googleapis.com/walletobjects/v1/genericObject/{object_id}"
    existing = requests.get(object_url, headers=headers, timeout=20)

    if existing.status_code == 404:
        created = requests.post(
            "https://walletobjects.googleapis.com/walletobjects/v1/genericObject",
            headers=headers,
            json=object_body,
            timeout=20,
        )
        if created.status_code not in {200, 201}:
            raise HTTPException(
                status_code=500,
                detail=f"No se pudo crear objeto Google Wallet socios: {created.text[:500]}",
            )
        return object_body

    if existing.status_code >= 300:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo verificar objeto Google Wallet socios: {existing.text[:500]}",
        )

    replaced = requests.put(
        object_url,
        headers=headers,
        json=object_body,
        timeout=20,
    )
    if replaced.status_code >= 300:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo reemplazar objeto Google Wallet socios: {replaced.text[:500]}",
        )

    expected_signature = member_google_wallet_visual_signature(object_body)

    for attempt in range(2):
        if attempt > 0:
            patched = requests.patch(
                object_url,
                headers=headers,
                json={
                    "state": object_body["state"],
                    "hexBackgroundColor": object_body["hexBackgroundColor"],
                    "logo": object_body["logo"],
                    "heroImage": object_body["heroImage"],
                    "imageModulesData": object_body["imageModulesData"],
                    "cardTitle": object_body["cardTitle"],
                    "header": object_body["header"],
                    "subheader": object_body["subheader"],
                    "barcode": object_body["barcode"],
                    "textModulesData": object_body["textModulesData"],
                    "linksModuleData": object_body["linksModuleData"],
                },
                timeout=20,
            )
            if patched.status_code >= 300:
                raise HTTPException(
                    status_code=500,
                    detail=f"No se pudo reforzar actualización Google Wallet socios: {patched.text[:500]}",
                )

        time.sleep(1)
        verified = requests.get(object_url, headers=headers, timeout=20)
        if verified.status_code >= 300:
            raise HTTPException(
                status_code=500,
                detail=f"No se pudo verificar objeto final Google Wallet socios: {verified.text[:500]}",
            )

        verified_body = verified.json()
        current_signature = member_google_wallet_visual_signature(verified_body)
        if current_signature == expected_signature:
            return verified_body

    raise HTTPException(
        status_code=500,
        detail=(
            "Google Wallet mantuvo datos anteriores del socio. "
            f"Esperado={json.dumps(expected_signature, ensure_ascii=False)}"
        )[:500],
    )


def safe_update_member_google_wallet_object(user, card):
    issuer_id = os.getenv("GOOGLE_WALLET_ISSUER_ID")
    if not issuer_id:
        return {"updated": False, "detail": "Falta GOOGLE_WALLET_ISSUER_ID"}

    try:
        service_account_info = get_google_wallet_service_account()
        class_id = f"{issuer_id}.{member_google_class_suffix()}"
        ensure_member_google_wallet_class(service_account_info, class_id)
        object_body = upsert_member_google_wallet_object(
            service_account_info,
            user,
            card,
            issuer_id,
            class_id,
        )
        return {"updated": True, "object_id": object_body["id"]}
    except HTTPException as exc:
        return {"updated": False, "detail": str(exc.detail)}
    except Exception as exc:
        return {"updated": False, "detail": str(exc)}


def build_member_apple_wallet_push_cert_files(temp_dir: str):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    certs_dir = os.path.join(base_dir, "certs")
    if not os.path.exists(certs_dir):
        certs_dir = os.path.join(os.getcwd(), "certs")

    p12_path = os.path.join(certs_dir, "mayu_wallet.p12")
    password = os.getenv("APPLE_WALLET_P12_PASSWORD") or os.getenv(
        "APPLE_WALLET_CERT_PASSWORD"
    )

    if not os.path.exists(p12_path):
        raise Exception("No existe certs/mayu_wallet.p12")
    if not password:
        raise Exception("Falta APPLE_WALLET_P12_PASSWORD")

    with open(p12_path, "rb") as f:
        private_key, certificate, additional_certificates = pkcs12.load_key_and_certificates(
            f.read(),
            password.encode(),
        )

    if not private_key or not certificate:
        raise Exception("Certificado Apple Wallet inválido")

    from cryptography.hazmat.primitives.serialization import NoEncryption, PrivateFormat

    cert_path = os.path.join(temp_dir, "apple_wallet_push_cert.pem")
    key_path = os.path.join(temp_dir, "apple_wallet_push_key.pem")

    with open(cert_path, "wb") as f:
        f.write(certificate.public_bytes(Encoding.PEM))
        for item in additional_certificates or []:
            f.write(item.public_bytes(Encoding.PEM))

    with open(key_path, "wb") as f:
        f.write(
            private_key.private_bytes(
                Encoding.PEM,
                PrivateFormat.PKCS8,
                NoEncryption(),
            )
        )

    return cert_path, key_path


def safe_send_member_apple_wallet_update_pushes(db: Session, card):
    pass_type_id = os.getenv("APPLE_PASS_TYPE_ID")
    if not pass_type_id:
        return {"sent": 0, "errors": [{"detail": "Falta APPLE_PASS_TYPE_ID"}]}

    registrations = (
        db.query(models.MemberAppleWalletRegistration)
        .filter(models.MemberAppleWalletRegistration.card_id == card.id)
        .all()
    )
    if not registrations:
        return {"sent": 0, "errors": [], "detail": "Sin dispositivos Apple Wallet registrados"}

    try:
        import httpx

        temp_dir = tempfile.mkdtemp(prefix=f"mayu_member_apns_{card.id}_")
        cert_path, key_path = build_member_apple_wallet_push_cert_files(temp_dir)
        apns_host = os.getenv("APPLE_APNS_HOST", "https://api.push.apple.com")
        sent = 0
        errors = []

        with httpx.Client(http2=True, cert=(cert_path, key_path), timeout=20) as client:
            for registration in registrations:
                try:
                    response = client.post(
                        f"{apns_host}/3/device/{registration.push_token}",
                        headers={
                            "apns-topic": pass_type_id,
                            "apns-push-type": "background",
                            "apns-priority": "10",
                        },
                        json={},
                    )
                    if response.status_code in {200, 201}:
                        sent += 1
                    elif response.status_code == 410:
                        (
                            db.query(models.MemberAppleWalletRegistration)
                            .filter(models.MemberAppleWalletRegistration.id == registration.id)
                            .delete(synchronize_session=False)
                        )
                        db.commit()
                    else:
                        errors.append(
                            {
                                "registration_id": registration.id,
                                "status_code": response.status_code,
                                "detail": response.text[:300],
                            }
                        )
                except Exception as exc:
                    errors.append(
                        {
                            "registration_id": registration.id,
                            "detail": str(exc),
                        }
                    )

        return {
            "sent": sent,
            "registered_devices": len(registrations),
            "errors": errors,
        }
    except Exception as exc:
        return {"sent": 0, "errors": [{"detail": str(exc)}], "registered_devices": len(registrations)}


def safe_update_member_wallets(db: Session, user, card):
    return {
        "google": safe_update_member_google_wallet_object(user, card),
        "apple": safe_send_member_apple_wallet_update_pushes(db, card),
    }


@router.post(
    "/wallet/apple/v1/devices/{device_library_identifier}/registrations/{pass_type_identifier}/{serial_number}"
)
def register_member_apple_wallet_device(
    device_library_identifier: str,
    pass_type_identifier: str,
    serial_number: str,
    payload: AppleWalletRegistrationRequest,
    request: FastAPIRequest,
    db: Session = Depends(get_db),
):
    user, card = get_member_card_by_apple_serial(db, serial_number)
    verify_member_wallet_request(request, card)

    if not payload.pushToken or not payload.pushToken.strip():
        raise HTTPException(status_code=400, detail="pushToken requerido")

    existing = (
        db.query(models.MemberAppleWalletRegistration)
        .filter(
            models.MemberAppleWalletRegistration.card_id == card.id,
            models.MemberAppleWalletRegistration.device_library_identifier == device_library_identifier,
            models.MemberAppleWalletRegistration.serial_number == serial_number,
        )
        .first()
    )

    created = False
    if existing:
        existing.pass_type_identifier = pass_type_identifier
        existing.push_token = payload.pushToken.strip()
        existing.authentication_token = member_wallet_auth_token(card)
        existing.updated_at = datetime.utcnow()
    else:
        created = True
        existing = models.MemberAppleWalletRegistration(
            card_id=card.id,
            device_library_identifier=device_library_identifier,
            pass_type_identifier=pass_type_identifier,
            serial_number=serial_number,
            push_token=payload.pushToken.strip(),
            authentication_token=member_wallet_auth_token(card),
        )
        db.add(existing)

    db.commit()
    return Response(status_code=201 if created else 200)


@router.delete(
    "/wallet/apple/v1/devices/{device_library_identifier}/registrations/{pass_type_identifier}/{serial_number}"
)
def unregister_member_apple_wallet_device(
    device_library_identifier: str,
    pass_type_identifier: str,
    serial_number: str,
    request: FastAPIRequest,
    db: Session = Depends(get_db),
):
    user, card = get_member_card_by_apple_serial(db, serial_number)
    verify_member_wallet_request(request, card)
    (
        db.query(models.MemberAppleWalletRegistration)
        .filter(
            models.MemberAppleWalletRegistration.card_id == card.id,
            models.MemberAppleWalletRegistration.device_library_identifier == device_library_identifier,
            models.MemberAppleWalletRegistration.pass_type_identifier == pass_type_identifier,
            models.MemberAppleWalletRegistration.serial_number == serial_number,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return Response(status_code=200)


@router.get(
    "/wallet/apple/v1/devices/{device_library_identifier}/registrations/{pass_type_identifier}"
)
def get_member_apple_wallet_updated_serials(
    device_library_identifier: str,
    pass_type_identifier: str,
    request: FastAPIRequest,
    db: Session = Depends(get_db),
    passesUpdatedSince: str | None = None,
):
    registrations = (
        db.query(models.MemberAppleWalletRegistration)
        .filter(
            models.MemberAppleWalletRegistration.device_library_identifier == device_library_identifier,
            models.MemberAppleWalletRegistration.pass_type_identifier == pass_type_identifier,
        )
        .all()
    )

    if registrations:
        token = extract_wallet_auth_token(request)
        if token not in {item.authentication_token for item in registrations}:
            return Response(status_code=401)

    updated_items = []
    for item in registrations:
        if not item.card:
            continue
        user = item.card.user
        if not user:
            continue
        last_updated = member_apple_last_updated(db, user, item.card)
        if passesUpdatedSince and last_updated <= passesUpdatedSince:
            continue
        updated_items.append((item, last_updated))

    if not updated_items:
        return Response(status_code=204)

    return {
        "lastUpdated": max(last_updated for _, last_updated in updated_items),
        "serialNumbers": [item.serial_number for item, _ in updated_items],
    }


@router.get("/wallet/apple/v1/passes/{pass_type_identifier}/{serial_number}")
def get_updated_member_apple_wallet_pass(
    pass_type_identifier: str,
    serial_number: str,
    request: FastAPIRequest,
    db: Session = Depends(get_db),
):
    user, card = get_member_card_by_apple_serial(db, serial_number)
    verify_member_wallet_request(request, card)
    output_path = build_member_apple_wallet_file(
        db,
        user,
        card,
        serial_number_override=serial_number,
    )
    summary = ambassador_commission_summary(db, user)
    pending_value = int(round((summary["current_display_amount"] if summary else 0) * 100))
    paid_value = int(round((summary["total_paid"] if summary else 0) * 100))
    projected_value = int(round((summary["projected_monthly_commission"] if summary else 0) * 100))
    return FileResponse(
        path=output_path,
        media_type="application/vnd.apple.pkpass",
        filename=f"mayu_wallet_{user.id}.pkpass",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Last-Modified": member_apple_last_updated(db, user, card),
            "ETag": f"member-{card.id}-{card.status}-{pending_value}-{paid_value}-{projected_value}",
        },
    )


@router.post("/wallet/apple/v1/log")
def member_apple_wallet_log(payload: dict):
    return {"message": "Apple Wallet log member recibido", "payload": payload}


@router.post("/wallet/apple/v1/v1/log")
def member_apple_wallet_legacy_double_v1_log(payload: dict):
    return {"message": "Apple Wallet log member recibido", "payload": payload}
