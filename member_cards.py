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
        raise HTTPException(
            status_code=400,
            detail="Solo socios o embajadores pueden tener tarjeta",
        )

    if user.role == "member" and not user.membership_level:
        raise HTTPException(
            status_code=400,
            detail="El socio no tiene membresía asignada",
        )

    level_snapshot = get_card_level_snapshot(user)
    member_code = get_card_code(db, user)
    card_status = get_card_status(user)

    card = db.query(models.MemberCard).filter(
        models.MemberCard.user_id == user.id
    ).first()

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

    return {
        "message": "Tarjeta generada correctamente",
        "card": card_response(user, card),
    }


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
    card = db.query(models.MemberCard).filter(
        models.MemberCard.qr_token == qr_token
    ).first()

    if not card:
        return HTMLResponse("<h1>Tarjeta inválida</h1>", status_code=404)

    user = db.query(models.User).filter(
        models.User.id == card.user_id
    ).first()

    if not user:
        return HTMLResponse("<h1>Usuario no encontrado</h1>", status_code=404)

    card.status = get_card_status(user)

    db.commit()
    db.refresh(card)

    status_text = "ACTIVA" if card.status == "active" else "INACTIVA"

    status_color = "#22c55e" if card.status == "active" else "#ef4444"

    html_content = f"""
    <html>
        <head>
            <title>Validación MAYU</title>
        </head>

        <body style="background:#0f172a; color:white; font-family:Arial; padding:40px;">

            <div style="max-width:520px; margin:auto; background:#1e293b; border-radius:24px; padding:32px;">

                <h1 style="text-align:center;">MAYU WELLNESS CLUB</h1>

                <div style="text-align:center; margin:20px 0;">
                    <span style="padding:12px 24px; border-radius:999px; background:{status_color};">
                        {status_text}
                    </span>
                </div>

                <p><strong>Nombre:</strong> {user.name}</p>
                <p><strong>Email:</strong> {user.email}</p>
                <p><strong>Tipo:</strong> {level_text(user, card)}</p>
                <p><strong>Código:</strong> {card.member_code}</p>

            </div>

        </body>
    </html>
    """

    return HTMLResponse(content=html_content)


@router.get("/user/{user_id}/image")
def generate_card_image(user_id: int, db: Session = Depends(get_db)):
    user, card = get_or_create_card(db, user_id)

    width = 950
    height = 560

    image = Image.new("RGB", (width, height), (13, 148, 136))

    draw = ImageDraw.Draw(image)

    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 58)
        info_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 34)
    except Exception:
        title_font = ImageFont.load_default()
        info_font = ImageFont.load_default()

    draw.text((80, 80), "MAYU WELLNESS CLUB", fill="white", font=title_font)

    draw.text((80, 220), user.name, fill="white", font=info_font)

    draw.text(
        (80, 280),
        level_text(user, card),
        fill="white",
        font=info_font,
    )

    draw.text(
        (80, 340),
        f"Codigo: {card.member_code}",
        fill="white",
        font=info_font,
    )

    validation_url = f"{BASE_PUBLIC_URL}/member-cards/validate/{card.qr_token}"

    qr = qrcode.make(validation_url)
    qr = qr.resize((180, 180))

    image.paste(qr, (700, 300))

    file_path = f"/tmp/card_{user_id}.png"

    image.save(file_path)

    return FileResponse(
        path=file_path,
        media_type="image/png",
        content_disposition_type="inline",
    )


def build_manifest(pass_dir: str):
    manifest = {}

    for filename in os.listdir(pass_dir):

        if filename in ["manifest.json", "signature"]:
            continue

        file_path = os.path.join(pass_dir, filename)

        if os.path.isfile(file_path):

            with open(file_path, "rb") as f:
                manifest[filename] = hashlib.sha1(
                    f.read()
                ).hexdigest()

    manifest_path = os.path.join(pass_dir, "manifest.json")

    with open(manifest_path, "w") as f:
        json.dump(manifest, f)


def sign_manifest(pass_dir: str, certs_dir: str):

    p12_path = os.path.join(certs_dir, "mayu_wallet.p12")

    password = os.getenv("APPLE_WALLET_P12_PASSWORD")

    if not os.path.exists(p12_path):
        raise HTTPException(
            status_code=500,
            detail="No existe mayu_wallet.p12",
        )

    with open(p12_path, "rb") as f:

        private_key, certificate, additional_certs = (
            pkcs12.load_key_and_certificates(
                f.read(),
                password.encode(),
            )
        )

    manifest_path = os.path.join(pass_dir, "manifest.json")

    with open(manifest_path, "rb") as f:
        manifest_data = f.read()

    builder = PKCS7SignatureBuilder().set_data(manifest_data)

    builder = builder.add_signer(
        certificate,
        private_key,
        hashes.SHA256(),
    )

    signature = builder.sign(
        Encoding.DER,
        [PKCS7Options.DetachedSignature],
    )

    with open(os.path.join(pass_dir, "signature"), "wb") as f:
        f.write(signature)


def zip_pkpass(pass_dir: str, output_path: str):

    with zipfile.ZipFile(
        output_path,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as z:

        for filename in os.listdir(pass_dir):

            file_path = os.path.join(pass_dir, filename)

            if os.path.isfile(file_path):
                z.write(file_path, filename)


@router.get("/apple-wallet/{user_id}")
def generate_apple_wallet_pass(
    user_id: int,
    db: Session = Depends(get_db),
):

    user, card = get_or_create_card(db, user_id)

    pass_type_id = os.getenv("APPLE_PASS_TYPE_ID")
    team_id = os.getenv("APPLE_TEAM_ID")

    if not pass_type_id:
        raise HTTPException(
            status_code=500,
            detail="Falta APPLE_PASS_TYPE_ID",
        )

    if not team_id:
        raise HTTPException(
            status_code=500,
            detail="Falta APPLE_TEAM_ID",
        )

    base_dir = os.path.dirname(os.path.abspath(__file__))

    certs_dir = os.path.join(base_dir, "certs")

    if not os.path.exists(certs_dir):
        certs_dir = os.path.join(os.getcwd(), "certs")

    temp_dir = tempfile.mkdtemp()

    pass_dir = os.path.join(temp_dir, "pass")

    os.makedirs(pass_dir, exist_ok=True)

    try:

        validate_url = (
            f"{BASE_PUBLIC_URL}/member-cards/validate/{card.qr_token}"
        )

        pass_json = {
            "formatVersion": 1,
            "passTypeIdentifier": pass_type_id,
            "serialNumber": card.member_code,
            "teamIdentifier": team_id,
            "organizationName": "Mayu Wellness Club",
            "description": "Tarjeta Mayu Wellness Club",
            "logoText": "MAYU",
            "foregroundColor": "rgb(255,255,255)",
            "backgroundColor": "rgb(13,148,136)",
            "labelColor": "rgb(255,255,255)",
            "generic": {
                "primaryFields": [
                    {
                        "key": "name",
                        "label": "SOCIO",
                        "value": user.name,
                    }
                ],
                "secondaryFields": [
                    {
                        "key": "level",
                        "label": "TIPO",
                        "value": level_text(user, card),
                    }
                ],
                "auxiliaryFields": [
                    {
                        "key": "code",
                        "label": "CODIGO",
                        "value": card.member_code,
                    }
                ],
            },
            "barcode": {
                "format": "PKBarcodeFormatQR",
                "message": validate_url,
                "messageEncoding": "iso-8859-1",
            },
        }

        with open(
            os.path.join(pass_dir, "pass.json"),
            "w",
        ) as f:
            json.dump(pass_json, f)

        icon_path = os.path.join(pass_dir, "icon.png")

        img = Image.new("RGB", (180, 180), (13, 148, 136))
        draw = ImageDraw.Draw(img)

        draw.text((40, 70), "MAYU", fill="white")

        img.save(icon_path)

        build_manifest(pass_dir)

        sign_manifest(pass_dir, certs_dir)

        output_path = os.path.join(
            temp_dir,
            f"mayu_wallet_{user_id}.pkpass",
        )

        zip_pkpass(pass_dir, output_path)

        return FileResponse(
            path=output_path,
            media_type="application/vnd.apple.pkpass",
            filename=f"mayu_wallet_{user_id}.pkpass",
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"Error generando Apple Wallet: {str(e)}",
        )


@router.get("/google-wallet/{user_id}")
def google_wallet_placeholder(
    user_id: int,
    db: Session = Depends(get_db),
):

    user, card = get_or_create_card(db, user_id)

    web_url = f"{BASE_PUBLIC_URL}/member-cards/user/{user.id}/web"

    return RedirectResponse(url=web_url)
