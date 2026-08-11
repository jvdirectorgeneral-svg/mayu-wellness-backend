import json
import os
import tempfile
import uuid
from typing import Optional

import jwt as pyjwt
import qrcode
import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models
from database import get_db
from dependencies import get_current_user
from member_cards import (
    BASE_PUBLIC_URL,
    build_manifest,
    clean_google_private_key,
    cover_image_to_canvas,
    create_wallet_icon,
    fit_image_to_canvas,
    get_google_wallet_service_account,
    sign_manifest,
    zip_pkpass,
)


router = APIRouter(prefix="/luxury-cards", tags=["Mayu Luxury Cards"])
LUXURY_CLASS_SUFFIX = "mayu_luxury_owners_v1"


class LuxuryCardCreate(BaseModel):
    holder_name: str
    email: str
    phone: Optional[str] = None
    company: Optional[str] = "Mayu"
    position: Optional[str] = None
    alert_region: Optional[str] = "Ecuador"


class LuxuryCardStatus(BaseModel):
    is_active: bool


def require_superadmin(user: models.User):
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Solo Control Maestro puede gestionar Mayu Luxury")


def _next_code(db: Session) -> str:
    last = db.query(models.LuxuryCard).order_by(models.LuxuryCard.id.desc()).first()
    return f"LUX-MAYU-{((last.id + 1) if last else 1):06d}"


def _find_card(db: Session, identifier: str):
    value = identifier.strip()
    return db.query(models.LuxuryCard).filter(
        (models.LuxuryCard.card_code == value)
        | (models.LuxuryCard.qr_token == value)
        | (models.LuxuryCard.email == value.lower())
    ).first()


def card_dict(card: models.LuxuryCard):
    root = f"{BASE_PUBLIC_URL}/luxury-cards"
    return {
        "id": card.id,
        "holder_name": card.holder_name,
        "email": card.email,
        "phone": card.phone,
        "company": card.company,
        "position": card.position,
        "alert_region": card.alert_region,
        "card_code": card.card_code,
        "qr_token": card.qr_token,
        "is_active": card.is_active,
        "public_card_url": f"{root}/public/{card.qr_token}",
        "qr_image_url": f"{root}/qr/{card.qr_token}/image",
        "card_background_url": f"{root}/assets/luxury_card_bg.png",
        "apple_wallet_url": f"{root}/wallet/apple/{card.qr_token}",
        "google_wallet_url": f"{root}/wallet/google/{card.qr_token}",
        "created_at": card.created_at,
        "updated_at": card.updated_at,
    }


@router.post("/admin")
def create_luxury_card(
    payload: LuxuryCardCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_superadmin(current_user)
    email = payload.email.strip().lower()
    if not payload.holder_name.strip() or not email:
        raise HTTPException(status_code=400, detail="Nombre y correo son obligatorios")
    if db.query(models.LuxuryCard).filter(models.LuxuryCard.email == email).first():
        raise HTTPException(status_code=409, detail="Ya existe una tarjeta Mayu Luxury para este correo")
    card = models.LuxuryCard(
        holder_name=payload.holder_name.strip(),
        email=email,
        phone=(payload.phone or "").strip() or None,
        company=(payload.company or "Mayu").strip() or "Mayu",
        position=(payload.position or "").strip() or None,
        alert_region=(payload.alert_region or "Ecuador").strip() or "Ecuador",
        card_code=_next_code(db),
        qr_token=uuid.uuid4().hex,
        created_by=current_user.id,
    )
    db.add(card)
    db.commit()
    db.refresh(card)
    return card_dict(card)


@router.get("/admin")
def list_luxury_cards(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_superadmin(current_user)
    cards = db.query(models.LuxuryCard).order_by(models.LuxuryCard.created_at.desc()).all()
    return {"items": [card_dict(card) for card in cards], "total": len(cards)}


@router.put("/admin/{card_id}/status")
def update_luxury_card_status(
    card_id: int,
    payload: LuxuryCardStatus,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_superadmin(current_user)
    card = db.query(models.LuxuryCard).filter(models.LuxuryCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Tarjeta Mayu Luxury no encontrada")
    card.is_active = payload.is_active
    db.commit()
    db.refresh(card)
    return card_dict(card)


@router.get("/assets/luxury_card_bg.png")
def luxury_card_background():
    path = os.path.join(os.path.dirname(__file__), "assets", "luxury_card_bg.png")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No existe fondo Mayu Luxury")
    return FileResponse(path, media_type="image/png")


@router.get("/qr/{qr_token}/image")
def luxury_qr(qr_token: str, db: Session = Depends(get_db)):
    card = _find_card(db, qr_token)
    if not card:
        raise HTTPException(status_code=404, detail="Tarjeta Mayu Luxury no encontrada")
    path = f"/tmp/luxury_qr_{card.id}.png"
    qrcode.make(f"{BASE_PUBLIC_URL}/luxury-cards/public/{card.qr_token}").save(path)
    return FileResponse(path, media_type="image/png")


@router.get("/public/{identifier}")
def public_luxury_card(identifier: str, db: Session = Depends(get_db)):
    card = _find_card(db, identifier)
    if not card:
        raise HTTPException(status_code=404, detail="Tarjeta Mayu Luxury no encontrada")
    status = "ACTIVA" if card.is_active else "INACTIVA"
    data = card_dict(card)
    html = f"""<!doctype html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>
    <title>Mayu Luxury · {card.holder_name}</title><style>body{{margin:0;background:#080808;color:#f5d57a;font-family:Arial;display:grid;place-items:center;min-height:100vh}}.card{{width:min(90vw,720px);border:1px solid #cda84d;border-radius:28px;overflow:hidden;background:#111;box-shadow:0 22px 70px #000}}.hero{{height:280px;background:url('{data['card_background_url']}') center/cover}}.info{{padding:28px}}h1{{margin:0 0 8px}}p{{color:#eee}}.code{{letter-spacing:2px}}a{{display:inline-block;margin:8px 8px 0 0;padding:12px 18px;border-radius:24px;background:#d6b65a;color:#111;text-decoration:none;font-weight:bold}}</style></head>
    <body><main class='card'><div class='hero'></div><div class='info'><h1>MAYU LUXURY</h1><h2>{card.holder_name}</h2><p>{card.position or 'Socio propietario'} · {card.company or 'Mayu'}</p><p class='code'>{card.card_code} · {status}</p><a href='{data['apple_wallet_url']}'>Apple Wallet</a><a href='{data['google_wallet_url']}'>Google Wallet</a></div></main></body></html>"""
    return HTMLResponse(html)


def _build_apple_pass(card: models.LuxuryCard) -> str:
    pass_type_id = os.getenv("APPLE_PASS_TYPE_ID")
    team_id = os.getenv("APPLE_TEAM_ID")
    if not pass_type_id or not team_id:
        raise HTTPException(status_code=500, detail="Falta configuración Apple Wallet en Render")
    base = os.path.dirname(__file__)
    certs = os.path.join(base, "certs")
    temp = tempfile.mkdtemp(prefix=f"mayu_luxury_{card.id}_")
    pass_dir = os.path.join(temp, "pass")
    os.makedirs(pass_dir, exist_ok=True)
    public_url = f"{BASE_PUBLIC_URL}/luxury-cards/public/{card.qr_token}"
    payload = {
        "formatVersion": 1, "passTypeIdentifier": pass_type_id,
        "serialNumber": f"mayu-luxury-{card.id}", "teamIdentifier": team_id,
        "organizationName": "Mayu", "description": "Tarjeta Mayu Luxury",
        "logoText": "MAYU LUXURY", "foregroundColor": "rgb(247,215,126)",
        "backgroundColor": "rgb(8,8,8)", "labelColor": "rgb(247,215,126)",
        "storeCard": {
            "primaryFields": [{"key": "holder", "label": "SOCIO PROPIETARIO", "value": card.holder_name}],
            "secondaryFields": [{"key": "position", "label": "CARGO", "value": card.position or "Socio propietario"}],
            "auxiliaryFields": [{"key": "code", "label": "CÓDIGO", "value": card.card_code}],
            "backFields": [{"key": "email", "label": "Correo", "value": card.email}, {"key": "region", "label": "Región de alertas", "value": card.alert_region}, {"key": "web", "label": "Tarjeta web", "value": public_url}],
        },
        "barcode": {"format": "PKBarcodeFormatQR", "message": public_url, "messageEncoding": "iso-8859-1", "altText": card.card_code},
    }
    with open(os.path.join(pass_dir, "pass.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    logo = os.path.join(base, "assets", "logo_mayu.png")
    bg = os.path.join(base, "assets", "luxury_card_bg.png")
    for filename, size in [("icon.png", (29, 29)), ("icon@2x.png", (58, 58)), ("logo.png", (70, 26)), ("logo@2x.png", (140, 52))]:
        if os.path.exists(logo): fit_image_to_canvas(logo, os.path.join(pass_dir, filename), size, (8, 8, 8))
        else: create_wallet_icon(os.path.join(pass_dir, filename))
    cover_image_to_canvas(bg, os.path.join(pass_dir, "strip.png"), (375, 123), (8, 8, 8))
    cover_image_to_canvas(bg, os.path.join(pass_dir, "strip@2x.png"), (750, 246), (8, 8, 8))
    build_manifest(pass_dir)
    sign_manifest(pass_dir, certs)
    output = os.path.join(temp, f"mayu_luxury_{card.id}.pkpass")
    zip_pkpass(pass_dir, output)
    return output


@router.get("/wallet/apple/{qr_token}")
def apple_wallet(qr_token: str, db: Session = Depends(get_db)):
    card = _find_card(db, qr_token)
    if not card or not card.is_active:
        raise HTTPException(status_code=404, detail="Tarjeta Mayu Luxury no válida")
    return FileResponse(_build_apple_pass(card), media_type="application/vnd.apple.pkpass", filename=f"mayu_luxury_{card.card_code}.pkpass")


def _google_save_url(card: models.LuxuryCard) -> str:
    issuer = os.getenv("GOOGLE_WALLET_ISSUER_ID")
    if not issuer:
        raise HTTPException(status_code=500, detail="Falta GOOGLE_WALLET_ISSUER_ID en Render")
    info = get_google_wallet_service_account()
    credentials = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/wallet_object.issuer"])
    credentials.refresh(GoogleAuthRequest())
    class_id = f"{issuer}.{LUXURY_CLASS_SUFFIX}"
    headers = {"Authorization": f"Bearer {credentials.token}", "Content-Type": "application/json"}
    existing = requests.get(f"https://walletobjects.googleapis.com/walletobjects/v1/genericClass/{class_id}", headers=headers, timeout=20)
    if existing.status_code == 404:
        body = {"id": class_id, "issuerName": "Mayu Luxury", "reviewStatus": "UNDER_REVIEW", "hexBackgroundColor": "#080808"}
        created = requests.post("https://walletobjects.googleapis.com/walletobjects/v1/genericClass", headers=headers, json=body, timeout=20)
        if created.status_code not in {200, 201}:
            raise HTTPException(status_code=500, detail="No se pudo crear la clase Mayu Luxury en Google Wallet")
    elif existing.status_code != 200:
        raise HTTPException(status_code=500, detail="No se pudo consultar Google Wallet")
    public_url = f"{BASE_PUBLIC_URL}/luxury-cards/public/{card.qr_token}"
    bg_url = f"{BASE_PUBLIC_URL}/luxury-cards/assets/luxury_card_bg.png"
    obj = {
        "id": f"{issuer}.mayu_luxury_{card.id}", "classId": class_id,
        "state": "ACTIVE", "hexBackgroundColor": "#080808",
        "heroImage": {"sourceUri": {"uri": bg_url}, "contentDescription": {"defaultValue": {"language": "es", "value": "Mayu Luxury"}}},
        "cardTitle": {"defaultValue": {"language": "es", "value": "Mayu Luxury"}},
        "header": {"defaultValue": {"language": "es", "value": card.holder_name}},
        "subheader": {"defaultValue": {"language": "es", "value": card.position or "Socio propietario"}},
        "barcode": {"type": "QR_CODE", "value": public_url, "alternateText": card.card_code},
        "textModulesData": [{"id": "company", "header": "Empresa", "body": card.company or "Mayu"}, {"id": "region", "header": "Región de alertas", "body": card.alert_region}],
    }
    claims = {"iss": info["client_email"], "aud": "google", "typ": "savetowallet", "payload": {"genericObjects": [obj]}}
    token = pyjwt.encode(claims, clean_google_private_key(info["private_key"]), algorithm="RS256")
    return f"https://pay.google.com/gp/v/save/{token}"


@router.get("/wallet/google/{qr_token}")
def google_wallet(qr_token: str, db: Session = Depends(get_db)):
    card = _find_card(db, qr_token)
    if not card or not card.is_active:
        raise HTTPException(status_code=404, detail="Tarjeta Mayu Luxury no válida")
    return RedirectResponse(_google_save_url(card))
