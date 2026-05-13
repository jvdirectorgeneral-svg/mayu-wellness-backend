from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
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

from cryptography.hazmat.primitives.serialization import pkcs12, Encoding
from cryptography.hazmat.primitives.serialization.pkcs7 import (
    PKCS7SignatureBuilder,
    PKCS7Options,
)
from cryptography.hazmat.primitives import hashes
from cryptography import x509

router = APIRouter(prefix="/member-cards", tags=["Member Cards"])

BASE_PUBLIC_URL = "https://mayu-wellness-backend-v1.onrender.com"
CARD_VALIDITY_TEXT = "Indefinido"


def generate_member_code(user_id: int, level: int):
    return f"MAYU-{level}-{user_id:06d}"


def generate_ambassador_code(ambassador_id: int):
    return f"EMB-{ambassador_id:06d}"


def get_ambassador_by_user(db: Session, user_id: int):
    return db.query(models.Ambassador).filter(
        models.Ambassador.user_id == user_id
    ).first()


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


def get_card_visual_data(db: Session, user, card):
    if user.role == "ambassador":
        ambassador = get_ambassador_by_user(db, user.id) if db else None
        ambassador_id = ambassador.id if ambassador else user.id
        return {
            "display_name": user.name,
            "level_text": "Embajador Mayu",
            "member_code": generate_ambassador_code(ambassador_id),
            "bg_file": "embajador_pic.png",
            "fallback_color": (0, 120, 110),
            "accent_color": (255, 236, 170),
        }

    if card.level_snapshot == 1:
        return {
            "display_name": user.name,
            "level_text": "Nivel 1 - Cobre",
            "member_code": card.member_code,
            "bg_file": "card_cobre.jpg",
            "fallback_color": (176, 111, 79),
            "accent_color": (236, 210, 190),
        }

    if card.level_snapshot == 2:
        return {
            "display_name": user.name,
            "level_text": "Nivel 2 - Plata",
            "member_code": card.member_code,
            "bg_file": "card_plata.jpg",
            "fallback_color": (205, 210, 214),
            "accent_color": (245, 245, 245),
        }

    return {
        "display_name": user.name,
        "level_text": "Nivel 3 - Oro",
        "member_code": card.member_code,
        "bg_file": "card_oro.jpg",
        "fallback_color": (212, 175, 55),
        "accent_color": (255, 236, 170),
    }


def card_response(user, card):
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
    }


@router.post("/generate/{user_id}")
def generate_member_card(user_id: int, db: Session = Depends(get_db)):
    user, card = get_or_create_card(db, user_id)
    return {"message": "Tarjeta generada correctamente", "card": card_response(user, card)}


@router.get("/user/{user_id}")
def get_member_card_by_user(user_id: int, db: Session = Depends(get_db)):
    user, card = get_or_create_card(db, user_id)
    return card_response(user, card)


@router.get("/user/{user_id}/web", response_class=HTMLResponse)
def get_member_card_web(user_id: int, db: Session = Depends(get_db)):
    user, card = get_or_create_card(db, user_id)

    image_url = f"{BASE_PUBLIC_URL}/member-cards/user/{user.id}/image"
    validate_url = f"{BASE_PUBLIC_URL}/member-cards/validate/{card.qr_token}"
    apple_wallet_url = f"{BASE_PUBLIC_URL}/member-cards/apple-wallet/{user.id}"

    html = f"""
    <html>
        <head>
            <title>Tarjeta Mayu Wellness Club</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="margin:0; font-family:Arial,sans-serif; background:#0f172a; color:white; padding:24px;">
            <div style="max-width:720px; margin:auto;">
                <h1 style="text-align:center;">MAYU WELLNESS CLUB</h1>
                <div style="background:#1e293b; border-radius:24px; padding:20px; text-align:center;">
                    <img src="{image_url}" style="max-width:100%; border-radius:18px;" />
                    <h2>{user.name}</h2>
                    <p><strong>Tipo:</strong> {level_text(user, card)}</p>
                    <p><strong>Código:</strong> {card.member_code}</p>
                    <p><strong>Estado:</strong> {card.status}</p>
                    <p><strong>Vigencia:</strong> {CARD_VALIDITY_TEXT}</p>
                    <a href="{validate_url}" style="display:inline-block; margin-top:16px; padding:12px 20px; background:#14b8a6; color:white; text-decoration:none; border-radius:999px;">
                        Validar tarjeta
                    </a>
                    <br/>
                    <a href="{apple_wallet_url}" style="display:inline-block; margin-top:12px; padding:12px 20px; background:#000; color:white; text-decoration:none; border-radius:999px;">
                        Agregar a Apple Wallet
                    </a>
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
                <h1 style="text-align:center;">MAYU WELLNESS CLUB</h1>
                <div style="text-align:center; margin:20px 0;">
                    <span style="padding:12px 24px; border-radius:999px; background:{status_color}; color:white;">
                        {status_text}
                    </span>
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
        outline=visual["accent_color"],
        width=4,
    )

    if os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA").resize((170, 170))
            image.paste(logo, ((width - 170) // 2, 25), logo)
        except Exception:
            pass

    draw_text(draw, (60, 305), visual["display_name"], name_font)
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


def create_wallet_icon(output_path: str):
    img = Image.new("RGB", (180, 180), (13, 148, 136))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 56)
    except Exception:
        font = ImageFont.load_default()

    draw.text((35, 58), "MAYU", fill=(255, 255, 255), font=font)
    img.save(output_path)


def copy_or_create_wallet_images(pass_dir: str, user, card):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    logo_path = os.path.join(base_dir, "assets", "logo_mayu.png")

    if user.role == "ambassador":
        strip_source = os.path.join(base_dir, "assets", "wallet_embajador.png")
    elif card.level_snapshot == 1:
        strip_source = os.path.join(base_dir, "assets", "wallet_cobre.png")
    elif card.level_snapshot == 2:
        strip_source = os.path.join(base_dir, "assets", "wallet_plata.png")
    else:
        strip_source = os.path.join(base_dir, "assets", "wallet_oro.png")

    for filename, size in [
        ("icon.png", (29, 29)),
        ("icon@2x.png", (58, 58)),
        ("logo.png", (160, 50)),
        ("logo@2x.png", (320, 100)),
    ]:
        target = os.path.join(pass_dir, filename)

        if os.path.exists(logo_path):
            try:
                img = Image.open(logo_path).convert("RGBA")
                img.thumbnail(size)
                canvas = Image.new("RGBA", size, (0, 0, 0, 0))
                canvas.paste(img, ((size[0] - img.size[0]) // 2, (size[1] - img.size[1]) // 2), img)
                canvas.save(target)
            except Exception:
                create_wallet_icon(target)
        else:
            create_wallet_icon(target)

    if os.path.exists(strip_source):
        strip = Image.open(strip_source).convert("RGB")
        strip = strip.resize((375, 123))
        strip.save(os.path.join(pass_dir, "strip.png"))

        strip_2x = strip.resize((750, 246))
        strip_2x.save(os.path.join(pass_dir, "strip@2x.png"))
    else:
        print("NO EXISTE STRIP WALLET:", strip_source)


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


@router.get("/apple-wallet/{user_id}")
def generate_apple_wallet_pass(user_id: int, db: Session = Depends(get_db)):
    user, card = get_or_create_card(db, user_id)

    pass_type_id = os.getenv("APPLE_PASS_TYPE_ID")
    team_id = os.getenv("APPLE_TEAM_ID")
    organization_name = os.getenv("APPLE_ORGANIZATION_NAME", "Mayu Wellness Club")

    if not pass_type_id:
        raise HTTPException(status_code=500, detail="Falta APPLE_PASS_TYPE_ID")

    if not team_id:
        raise HTTPException(status_code=500, detail="Falta APPLE_TEAM_ID")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    certs_dir = os.path.join(base_dir, "certs")

    if not os.path.exists(certs_dir):
        certs_dir = os.path.join(os.getcwd(), "certs")

    temp_dir = tempfile.mkdtemp(prefix=f"mayu_pkpass_{user_id}_")
    pass_dir = os.path.join(temp_dir, "pass")
    os.makedirs(pass_dir, exist_ok=True)

    try:
        validate_url = f"{BASE_PUBLIC_URL}/member-cards/validate/{card.qr_token}"
        card_web_url = f"{BASE_PUBLIC_URL}/member-cards/user/{user.id}/web"

        pass_json = {
            "formatVersion": 1,
            "passTypeIdentifier": pass_type_id,
            "serialNumber": card.member_code,
            "teamIdentifier": team_id,
            "organizationName": organization_name,
            "description": "Tarjeta Mayu Wellness Club",
            "logoText": "MAYU",
            "foregroundColor": "rgb(255,255,255)",
            "backgroundColor": "rgb(0,0,0)",
            "labelColor": "rgb(255,255,255)",
            "storeCard": {
                "primaryFields": [
                    {"key": "name", "label": "SOCIO", "value": user.name}
                ],
                "secondaryFields": [
                    {"key": "level", "label": "TIPO", "value": level_text(user, card)},
                    {"key": "status", "label": "ESTADO", "value": "Activo" if card.status == "active" else "Inactivo"},
                ],
                "auxiliaryFields": [
                    {"key": "code", "label": "CÓDIGO", "value": card.member_code},
                    {"key": "valid", "label": "VIGENCIA", "value": CARD_VALIDITY_TEXT},
                ],
                "backFields": [
                    {"key": "email", "label": "Email", "value": user.email},
                    {"key": "phone", "label": "Celular", "value": user.phone or "-"},
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

        output_path = os.path.join(temp_dir, f"mayu_wallet_{user_id}.pkpass")
        zip_pkpass(pass_dir, output_path)

        return FileResponse(
            path=output_path,
            media_type="application/vnd.apple.pkpass",
            filename=f"mayu_wallet_{user_id}.pkpass",
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generando Apple Wallet: {str(e)}")


@router.get("/google-wallet/{user_id}")
def google_wallet_placeholder(user_id: int, db: Session = Depends(get_db)):
    user, card = get_or_create_card(db, user_id)
    web_url = f"{BASE_PUBLIC_URL}/member-cards/user/{user.id}/web"
    return RedirectResponse(url=web_url)
