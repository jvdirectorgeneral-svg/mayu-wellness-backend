from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Any
from html import escape
from jose import jwt
from datetime import datetime
import secrets
import string
import os
import re
import unicodedata
from urllib.parse import urlencode, urlparse

import requests

import cloudinary
import cloudinary.uploader

from database import SessionLocal
from dependencies import get_current_user, SECRET_KEY, ALGORITHM
import models

router = APIRouter(prefix="/education", tags=["education"])


class EducationCategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None
    active: bool = True


class EducationCategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None


class EducationResourceCreate(BaseModel):
    title: str
    category_id: Optional[int] = None
    resource_type: str = "pdf"
    file_url: Optional[str] = None
    external_url: Optional[str] = None
    cover_image_url: Optional[str] = None
    price: float = 0
    description: Optional[str] = None
    content_text: Optional[str] = None
    plant_common_name: Optional[str] = None
    plant_scientific_name: Optional[str] = None
    plant_family: Optional[str] = None
    plant_origin: Optional[str] = None
    plant_uses: Optional[str] = None
    plant_parts_used: Optional[str] = None
    plant_preparation: Optional[str] = None
    plant_warnings: Optional[str] = None
    active: bool = True
    free_for_members: bool = True

    marketplace_only: bool = False
    language: str = "es"
    content_type: str = "general"
    video_urls: Optional[List[str]] = None
    online_files: Optional[List[Any]] = None
    download_pdf_url: Optional[str] = None


class EducationResourceUpdate(BaseModel):
    title: Optional[str] = None
    category_id: Optional[int] = None
    resource_type: Optional[str] = None
    file_url: Optional[str] = None
    external_url: Optional[str] = None
    cover_image_url: Optional[str] = None
    price: Optional[float] = None
    description: Optional[str] = None
    content_text: Optional[str] = None
    plant_common_name: Optional[str] = None
    plant_scientific_name: Optional[str] = None
    plant_family: Optional[str] = None
    plant_origin: Optional[str] = None
    plant_uses: Optional[str] = None
    plant_parts_used: Optional[str] = None
    plant_preparation: Optional[str] = None
    plant_warnings: Optional[str] = None
    active: Optional[bool] = None
    free_for_members: Optional[bool] = None

    marketplace_only: Optional[bool] = None
    language: Optional[str] = None
    content_type: Optional[str] = None
    video_urls: Optional[List[str]] = None
    online_files: Optional[List[Any]] = None
    download_pdf_url: Optional[str] = None


class EducationAccessCodeCreate(BaseModel):
    resource_id: int
    buyer_name: Optional[str] = None
    buyer_email: Optional[str] = None
    buyer_phone: Optional[str] = None
    max_uses: int = 30


class EducationAccessCodeValidate(BaseModel):
    access_code: str


class EducationCartItemCreate(BaseModel):
    resource_id: int
    quantity: int = 1


class EducationOrderCreate(BaseModel):
    buyer_name: str
    buyer_email: str
    buyer_phone: str
    items: List[EducationCartItemCreate]
    payment_method: str = "paypal"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def configure_cloudinary():
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    api_key = os.getenv("CLOUDINARY_API_KEY")
    api_secret = os.getenv("CLOUDINARY_API_SECRET")

    if not cloud_name or not api_key or not api_secret:
        raise HTTPException(status_code=500, detail="Faltan variables CLOUDINARY en Render")

    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,
    )


def generate_access_code():
    alphabet = string.ascii_uppercase + string.digits
    return "MAYU-EDU-" + "".join(secrets.choice(alphabet) for _ in range(10))


def generate_education_order_code():
    now = datetime.utcnow()
    random_code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    return f"EDU-MAYU-{now.strftime('%Y%m%d%H%M%S')}-{random_code}"


def get_user_from_token_param(token: Optional[str], db: Session):
    if not token:
        return None

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("email")
        sub = payload.get("sub")
        user_id = payload.get("user_id") or payload.get("id")

        if email:
            user = db.query(models.User).filter(models.User.email == email).first()
            if user:
                return user

        if user_id:
            user = db.query(models.User).filter(models.User.id == int(user_id)).first()
            if user:
                return user

        if sub:
            if str(sub).isdigit():
                user = db.query(models.User).filter(models.User.id == int(sub)).first()
                if user:
                    return user

            user = db.query(models.User).filter(models.User.email == sub).first()
            if user:
                return user

    except Exception:
        return None

    return None


def is_team_user(current_user: models.User):
    return current_user.role in {"superadmin", "admin", "education_admin"}


def require_education_admin(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {"superadmin", "admin", "education_admin"}:
        raise HTTPException(status_code=403, detail="Acceso solo para Mayu Educación")


def require_active_member_or_team(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if is_team_user(current_user):
        return

    if not getattr(current_user, "membership_active", False):
        raise HTTPException(status_code=403, detail="Tu membresía no está activa")


def category_to_dict(category):
    return {
        "id": category.id,
        "name": category.name,
        "description": category.description,
        "active": category.active,
        "created_at": category.created_at,
    }


def resource_to_dict(resource, public: bool = False):
    price = getattr(resource, "price", 0) or 0

    return {
        "id": resource.id,
        "title": resource.title,
        "category_id": resource.category_id,
        "category_name": resource.category_rel.name if resource.category_rel else None,
        "resource_type": resource.resource_type,
        "cover_image_url": resource.cover_image_url,
        "price": price,
        "is_paid": price > 0,
        "description": resource.description,
        "content_text": resource.content_text,
        "plant_common_name": resource.plant_common_name,
        "plant_scientific_name": resource.plant_scientific_name,
        "plant_family": resource.plant_family,
        "plant_origin": resource.plant_origin,
        "plant_uses": resource.plant_uses,
        "plant_parts_used": resource.plant_parts_used,
        "plant_preparation": resource.plant_preparation,
        "plant_warnings": resource.plant_warnings,
        "active": resource.active,
        "free_for_members": resource.free_for_members,
        "marketplace_only": getattr(resource, "marketplace_only", False),
        "language": getattr(resource, "language", "es"),
        "content_type": getattr(resource, "content_type", "general"),
        "video_urls": getattr(resource, "video_urls", None) or [],
        "online_files": getattr(resource, "online_files", None) or [],
        "download_pdf_url": getattr(resource, "download_pdf_url", None),
        "file_url": None if public else resource.file_url,
        "external_url": resource.external_url if resource.resource_type == "link" else (None if public else resource.external_url),
        "protected_view_url": f"/education/resources/{resource.id}/view",
        "created_by": resource.created_by,
        "created_at": resource.created_at,
        "updated_at": resource.updated_at,
    }


def get_valid_access_code(db: Session, resource_id: int, access_code: Optional[str]):
    if not access_code:
        return None

    code = (
        db.query(models.EducationAccessCode)
        .filter(models.EducationAccessCode.code == access_code.strip())
        .filter(models.EducationAccessCode.resource_id == resource_id)
        .first()
    )

    if not code or code.status != "active":
        return None

    if code.uses_count >= code.max_uses:
        code.status = "expired"
        db.commit()
        return None

    return code


def consume_access_code(db: Session, code):
    code.uses_count = (code.uses_count or 0) + 1
    code.last_used_at = datetime.utcnow()

    if code.uses_count >= code.max_uses:
        code.status = "expired"

    db.commit()
    db.refresh(code)


def cloudinary_pdf_page_url(url: str, page: int):
    if "/upload/" not in url:
        return url

    return url.replace("/upload/", f"/upload/f_jpg,pg_{page},q_auto,w_1400/")


def cloudinary_video_mp4_url(url: str):
    if not url:
        return url

    if "/upload/" not in url:
        return url

    return url.replace("/upload/", "/upload/f_mp4,q_auto/")


def education_pdf_filename(title: Optional[str]):
    normalized = unicodedata.normalize("NFKD", title or "contenido-mayu")
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii")
    safe_title = re.sub(r"[^A-Za-z0-9_-]+", "-", ascii_title).strip("-_")
    return f"{safe_title or 'contenido-mayu'}.pdf"


@router.get("/resources/{resource_id}/download-pdf")
def download_protected_pdf(
    resource_id: int,
    token: Optional[str] = Query(default=None),
    access_code: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    current_user = get_user_from_token_param(token, db)

    if current_user:
        require_active_member_or_team(current_user)
    elif not get_valid_access_code(db, resource_id, access_code):
        raise HTTPException(status_code=401, detail="Acceso no autorizado o código caducado")

    resource = (
        db.query(models.EducationResource)
        .filter(models.EducationResource.id == resource_id)
        .filter(models.EducationResource.active == True)
        .first()
    )

    if not resource or not resource.download_pdf_url:
        raise HTTPException(status_code=404, detail="PDF complementario no encontrado")

    parsed_url = urlparse(resource.download_pdf_url)
    if parsed_url.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Enlace de PDF no válido")

    try:
        upstream = requests.get(resource.download_pdf_url, stream=True, timeout=(10, 90))
        upstream.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="No se pudo descargar el PDF almacenado") from exc

    filename = education_pdf_filename(resource.title)

    def stream_pdf():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        stream_pdf(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/categories")
def get_public_categories(db: Session = Depends(get_db)):
    categories = (
        db.query(models.EducationCategory)
        .filter(models.EducationCategory.active == True)
        .order_by(models.EducationCategory.name.asc())
        .all()
    )

    return {"items": [category_to_dict(c) for c in categories]}


@router.get("/resources")
def get_public_resources(
    category_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_active_member_or_team(current_user)

    query = (
        db.query(models.EducationResource)
        .filter(models.EducationResource.active == True)
        .filter(models.EducationResource.free_for_members == True)
        .filter(models.EducationResource.marketplace_only == False)
    )

    if category_id:
        query = query.filter(models.EducationResource.category_id == category_id)

    if resource_type and resource_type.strip():
        query = query.filter(models.EducationResource.resource_type == resource_type.strip())

    if search and search.strip():
        query = query.filter(models.EducationResource.title.ilike(f"%{search.strip()}%"))

    resources = query.order_by(models.EducationResource.id.desc()).all()
    return {"items": [resource_to_dict(r, public=True) for r in resources]}


@router.get("/store/resources")
def get_store_resources(
    category_id: Optional[int] = None,
    search: Optional[str] = None,
    language: Optional[str] = "es",
    db: Session = Depends(get_db),
):
    query = db.query(models.EducationResource).filter(models.EducationResource.active == True)

    if category_id:
        query = query.filter(models.EducationResource.category_id == category_id)

    if search and search.strip():
        query = query.filter(models.EducationResource.title.ilike(f"%{search.strip()}%"))

    if language and language.strip():
        query = query.filter(models.EducationResource.language == language.strip())

    resources = query.order_by(models.EducationResource.id.desc()).all()
    return {"items": [resource_to_dict(r, public=True) for r in resources]}


@router.post("/store/orders")
def create_education_store_order(payload: EducationOrderCreate, db: Session = Depends(get_db)):
    if not payload.items:
        raise HTTPException(status_code=400, detail="El carrito está vacío")

    buyer_name = payload.buyer_name.strip()
    buyer_phone = payload.buyer_phone.strip()
    buyer_email = payload.buyer_email.strip()

    if not buyer_name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")

    if not buyer_phone:
        raise HTTPException(status_code=400, detail="El teléfono es obligatorio")

    if not buyer_email:
        raise HTTPException(status_code=400, detail="El email es obligatorio")

    order_code = generate_education_order_code()
    subtotal = 0.0
    items_data = []

    for item in payload.items:
        resource = (
            db.query(models.EducationResource)
            .filter(models.EducationResource.id == item.resource_id)
            .filter(models.EducationResource.active == True)
            .first()
        )

        if not resource:
            raise HTTPException(status_code=404, detail="Contenido no encontrado")

        unit_price = float(resource.price or 0)
        line_total = unit_price * item.quantity
        subtotal += line_total

        items_data.append({
            "resource_id": resource.id,
            "title": resource.title,
            "unit_price": round(unit_price, 2),
            "quantity": item.quantity,
            "total": round(line_total, 2),
        })

    total = round(subtotal, 2)

    whatsapp_lines = [
        f"Hola Mayu Educación, deseo confirmar mi pedido {order_code}.",
        f"Cliente: {buyer_name}",
        f"Teléfono: {buyer_phone}",
        f"Email: {buyer_email}",
        "",
        "Contenidos solicitados:",
    ]

    for item in items_data:
        whatsapp_lines.append(f"- {item['title']} | Cantidad: {item['quantity']} | Subtotal: ${item['total']:.2f}")

    whatsapp_lines.append("")
    whatsapp_lines.append(f"Total: ${total:.2f} USD")

    return {
        "message": "Orden educativa creada correctamente",
        "order": {
            "order_code": order_code,
            "buyer_name": buyer_name,
            "buyer_email": buyer_email,
            "buyer_phone": buyer_phone,
            "subtotal": round(subtotal, 2),
            "total": total,
            "currency": "USD",
            "payment_method": payload.payment_method,
            "payment_status": "pending",
            "status": "created",
            "whatsapp_message": "\n".join(whatsapp_lines),
            "items": items_data,
            "created_at": datetime.utcnow(),
        },
    }


@router.post("/store/access-codes")
def create_access_code(
    payload: EducationAccessCodeCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)

    resource = db.query(models.EducationResource).filter(models.EducationResource.id == payload.resource_id).first()

    if not resource:
        raise HTTPException(status_code=404, detail="Contenido no encontrado")

    access = models.EducationAccessCode(
        resource_id=payload.resource_id,
        code=generate_access_code(),
        buyer_name=payload.buyer_name,
        buyer_email=payload.buyer_email,
        buyer_phone=payload.buyer_phone,
        max_uses=payload.max_uses,
        uses_count=0,
        status="active",
        created_at=datetime.utcnow(),
    )

    db.add(access)
    db.commit()
    db.refresh(access)

    return {
        "message": "Código de acceso creado correctamente",
        "code": access.code,
        "resource_id": access.resource_id,
        "max_uses": access.max_uses,
        "uses_count": access.uses_count,
        "status": access.status,
        "view_url": f"/education/resources/{access.resource_id}/view?access_code={access.code}",
    }


@router.post("/store/access-codes/validate")
def validate_access_code(payload: EducationAccessCodeValidate, db: Session = Depends(get_db)):
    access = (
        db.query(models.EducationAccessCode)
        .filter(models.EducationAccessCode.code == payload.access_code.strip())
        .first()
    )

    if not access:
        raise HTTPException(status_code=401, detail="Código inválido")

    if access.status != "active":
        raise HTTPException(status_code=401, detail="Código inactivo o caducado")

    if access.uses_count >= access.max_uses:
        access.status = "expired"
        db.commit()
        raise HTTPException(status_code=401, detail="Código caducado")

    resource = (
        db.query(models.EducationResource)
        .filter(models.EducationResource.id == access.resource_id)
        .filter(models.EducationResource.active == True)
        .first()
    )

    if not resource:
        raise HTTPException(status_code=404, detail="Contenido no encontrado")

    return {
        "message": "Código válido",
        "resource_id": access.resource_id,
        "title": resource.title,
        "code": access.code,
        "uses_count": access.uses_count,
        "max_uses": access.max_uses,
        "remaining_uses": access.max_uses - access.uses_count,
        "view_url": f"/education/resources/{access.resource_id}/view?access_code={access.code}",
    }


@router.get("/resources/{resource_id}/view", response_class=HTMLResponse)
def view_protected_resource(
    resource_id: int,
    token: Optional[str] = Query(default=None),
    access_code: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    current_user = get_user_from_token_param(token, db)
    access = None

    if current_user:
        require_active_member_or_team(current_user)
        viewer_name = current_user.name or "Usuario Mayu"
        viewer_email = current_user.email or ""
    else:
        access = get_valid_access_code(db, resource_id, access_code)

        if not access:
            raise HTTPException(status_code=401, detail="Acceso no autorizado o código caducado")

        viewer_name = access.buyer_name or "Comprador Mayu Educación"
        viewer_email = access.buyer_email or access.code

    resource = (
        db.query(models.EducationResource)
        .filter(models.EducationResource.id == resource_id)
        .filter(models.EducationResource.active == True)
        .first()
    )

    if not resource:
        raise HTTPException(status_code=404, detail="Contenido no encontrado")

    if access:
        consume_access_code(db, access)
        remaining_after_use = max(access.max_uses - access.uses_count, 0)
    else:
        remaining_after_use = "Acceso ilimitado para socio activo"

    file_url = resource.file_url or ""
    external_url = resource.external_url or ""
    resource_type = resource.resource_type or ""
    video_urls = resource.video_urls if isinstance(resource.video_urls, list) else []
    online_files = resource.online_files if isinstance(resource.online_files, list) else []

    main_video_url = ""
    if resource_type == "video":
        if file_url:
            main_video_url = file_url
        elif external_url:
            main_video_url = external_url
        elif video_urls:
            main_video_url = video_urls[0]

    safe_url = escape(file_url or external_url or main_video_url)
    safe_video_url = escape(cloudinary_video_mp4_url(main_video_url))
    title = escape(resource.title or "Contenido Mayu")
    user_name = escape(viewer_name)
    user_email = escape(viewer_email)

    extra_content_html = ""

    if resource.description:
        extra_content_html += f"""
        <div class="box">
          <h3>Resumen</h3>
          <p>{escape(resource.description)}</p>
        </div>
        """

    if resource.download_pdf_url:
        download_query = urlencode(
            {key: value for key, value in {"token": token, "access_code": access_code}.items() if value}
        )
        download_url = f"/education/resources/{resource_id}/download-pdf"
        if download_query:
            download_url += f"?{download_query}"
        extra_content_html += f"""
        <div class="box">
          <h3>PDF descargable</h3>
          <p><a href="{escape(download_url)}">Descargar PDF complementario</a></p>
        </div>
        """

    if online_files:
        files_html = ""
        for f in online_files:
            if isinstance(f, dict):
                name = escape(str(f.get("name", "Archivo online")))
                url = escape(str(f.get("url", "")))
                if url:
                    files_html += f'<li><a href="{url}" target="_blank">{name}</a></li>'

        if files_html:
            extra_content_html += f"""
            <div class="box">
              <h3>Archivos online</h3>
              <ul>{files_html}</ul>
            </div>
            """

    if resource_type in {"plant_card", "plant_registry"} and not safe_url:
        viewer = f"""
        <div class="box">
          <h3>{escape(resource.plant_common_name or resource.title or "Ficha Mayu")}</h3>
          <p><strong>Nombre científico:</strong> {escape(resource.plant_scientific_name or "-")}</p>
          <p><strong>Familia:</strong> {escape(resource.plant_family or "-")}</p>
          <p><strong>Origen:</strong> {escape(resource.plant_origin or "-")}</p>
          <p><strong>Partes usadas:</strong> {escape(resource.plant_parts_used or "-")}</p>
          <p><strong>Usos:</strong> {escape(resource.plant_uses or "-")}</p>
          <p><strong>Preparación:</strong> {escape(resource.plant_preparation or "-")}</p>
          <p><strong>Advertencias:</strong> {escape(resource.plant_warnings or "-")}</p>
          <p>{escape(resource.content_text or "")}</p>
        </div>
        """
    elif resource_type == "video":
        if not safe_video_url:
            raise HTTPException(status_code=404, detail="Este video no tiene enlace disponible")

        viewer = f"""
        <div class="video-wrap">
          <video id="mayuProtectedVideo" controls playsinline webkit-playsinline
            controlsList="nodownload nofullscreen noremoteplayback noplaybackrate" disablePictureInPicture
            oncontextmenu="return false;"
            style="width:100%;max-height:80vh;border-radius:16px;background:#000;display:block;">
            <source src="{safe_video_url}" type="video/mp4">
            Tu navegador no puede reproducir este video.
          </video>
          <div class="page-watermark video-watermark">MAYU EDUCACIÓN<br>{user_name}<br>{user_email}</div>
        </div>
        """
    elif resource_type in {"pdf", "document"}:
        if not safe_url:
            raise HTTPException(status_code=404, detail="Este contenido no tiene archivo o enlace disponible")

        pages_html = ""

        for page in range(1, 81):
            page_url = escape(cloudinary_pdf_page_url(file_url or external_url, page))
            pages_html += f"""
            <div class="page-wrap">
              <img src="{page_url}" draggable="false"
                onerror="this.parentElement.style.display='none';"
                style="width:100%;margin-bottom:18px;border-radius:14px;user-select:none;" />
              <div class="page-watermark">MAYU EDUCACIÓN<br>{user_name}<br>{user_email}</div>
            </div>
            """

        viewer = pages_html
    else:
        if not safe_url:
            raise HTTPException(status_code=404, detail="Este contenido no tiene archivo o enlace disponible")

        viewer = f"""
        <div class="page-wrap">
          <img src="{safe_url}" draggable="false"
            style="width:100%;max-height:80vh;object-fit:contain;border-radius:16px;user-select:none;" />
          <div class="page-watermark">MAYU EDUCACIÓN<br>{user_name}<br>{user_email}</div>
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title>{title}</title>
      <style>
        body {{
          margin: 0;
          padding: 18px;
          background: #f4f4f1;
          font-family: Arial, sans-serif;
          color: #1e1e1e;
          user-select: none;
          -webkit-user-select: none;
        }}
        .viewer {{
          max-width: 1100px;
          margin: 0 auto;
          background: white;
          border-radius: 18px;
          padding: 18px;
          box-shadow: 0 4px 18px rgba(0,0,0,0.08);
        }}
        .page-wrap, .video-wrap {{
          position: relative;
        }}
        .video-wrap {{
          overflow: hidden;
          border-radius: 16px;
          background: #000;
        }}
        .page-watermark {{
          position: absolute;
          top: 40%;
          left: 50%;
          transform: translate(-50%, -50%) rotate(-25deg);
          color: rgba(0,128,128,0.26);
          font-weight: bold;
          font-size: 30px;
          text-align: center;
          pointer-events: none;
        }}
        .video-watermark {{
          position: absolute;
          z-index: 5;
          top: 50%;
          left: 50%;
          width: 80%;
          color: rgba(0,128,128,0.30);
          text-shadow: 0 1px 8px rgba(255,255,255,0.16);
        }}
        .box {{
          margin-top: 18px;
          padding: 20px;
          border-radius: 16px;
          background: #f8f8f6;
          line-height: 1.55;
        }}
        a {{
          color: #008080;
          font-weight: bold;
        }}
      </style>
    </head>
    <body oncontextmenu="return false;">
      <div class="viewer">
        <h2>{title}</h2>
        <p>Acceso protegido Mayu Educación. Usos restantes: {remaining_after_use}</p>
        {viewer}
        {extra_content_html}
      </div>
      <script>
        const video = document.getElementById('mayuProtectedVideo');
        if (video) {{
          video.addEventListener('webkitbeginfullscreen', function () {{
            try {{ video.webkitExitFullscreen(); }} catch (e) {{}}
          }});
          video.addEventListener('enterpictureinpicture', function () {{
            try {{ document.exitPictureInPicture(); }} catch (e) {{}}
          }});
        }}
      </script>
    </body>
    </html>
    """

    return HTMLResponse(content=html)


@router.get("/admin/categories")
def get_admin_categories(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)
    categories = db.query(models.EducationCategory).order_by(models.EducationCategory.id.desc()).all()
    return {"items": [category_to_dict(c) for c in categories]}


@router.get("/admin/resources")
def get_admin_resources(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)
    resources = db.query(models.EducationResource).order_by(models.EducationResource.id.desc()).all()
    return {"items": [resource_to_dict(r, public=False) for r in resources]}


@router.post("/categories")
def create_category(
    payload: EducationCategoryCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)

    name = payload.name.strip()

    if not name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")

    category = models.EducationCategory(name=name, description=payload.description, active=payload.active)
    db.add(category)
    db.commit()
    db.refresh(category)

    return {"message": "Categoría educativa creada correctamente", "category": category_to_dict(category)}


@router.put("/categories/{category_id}")
def update_category(
    category_id: int,
    payload: EducationCategoryUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)

    category = db.query(models.EducationCategory).filter(models.EducationCategory.id == category_id).first()

    if not category:
        raise HTTPException(status_code=404, detail="Categoría no encontrada")

    for field, value in payload.dict(exclude_unset=True).items():
        setattr(category, field, value.strip() if isinstance(value, str) else value)

    db.commit()
    db.refresh(category)

    return {"message": "Categoría educativa actualizada correctamente", "category": category_to_dict(category)}


@router.delete("/categories/{category_id}")
def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)

    category = db.query(models.EducationCategory).filter(models.EducationCategory.id == category_id).first()

    if not category:
        raise HTTPException(status_code=404, detail="Categoría no encontrada")

    db.delete(category)
    db.commit()

    return {"message": "Categoría educativa eliminada correctamente", "category_id": category_id}


@router.post("/resources")
def create_resource(
    payload: EducationResourceCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)

    title = payload.title.strip()

    if not title:
        raise HTTPException(status_code=400, detail="El título es obligatorio")

    if payload.marketplace_only:
        payload.free_for_members = False

    if payload.marketplace_only and float(payload.price or 0) <= 0:
        raise HTTPException(status_code=400, detail="El contenido solo Marketplace debe tener precio mayor a 0")

    resource = models.EducationResource(
        title=title,
        category_id=payload.category_id,
        resource_type=payload.resource_type.strip(),
        file_url=payload.file_url,
        external_url=payload.external_url,
        cover_image_url=payload.cover_image_url,
        price=payload.price,
        description=payload.description,
        content_text=payload.content_text,
        plant_common_name=payload.plant_common_name,
        plant_scientific_name=payload.plant_scientific_name,
        plant_family=payload.plant_family,
        plant_origin=payload.plant_origin,
        plant_uses=payload.plant_uses,
        plant_parts_used=payload.plant_parts_used,
        plant_preparation=payload.plant_preparation,
        plant_warnings=payload.plant_warnings,
        active=payload.active,
        free_for_members=payload.free_for_members,
        marketplace_only=payload.marketplace_only,
        language=(payload.language or "es").strip(),
        content_type=(payload.content_type or "general").strip(),
        video_urls=payload.video_urls or [],
        online_files=payload.online_files or [],
        download_pdf_url=payload.download_pdf_url,
        created_by=current_user.id,
    )

    db.add(resource)
    db.commit()
    db.refresh(resource)

    return {"message": "Contenido educativo creado correctamente", "resource": resource_to_dict(resource, public=False)}


@router.put("/resources/{resource_id}")
def update_resource(
    resource_id: int,
    payload: EducationResourceUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)

    resource = db.query(models.EducationResource).filter(models.EducationResource.id == resource_id).first()

    if not resource:
        raise HTTPException(status_code=404, detail="Contenido no encontrado")

    if payload.category_id == 0:
        resource.category_id = None

    data = payload.dict(exclude_unset=True)

    if data.get("marketplace_only") is True:
        data["free_for_members"] = False

    if data.get("marketplace_only") is True:
        price_to_validate = data.get("price", resource.price)
        if float(price_to_validate or 0) <= 0:
            raise HTTPException(status_code=400, detail="El contenido solo Marketplace debe tener precio mayor a 0")

    for field, value in data.items():
        if field == "category_id" and value == 0:
            continue
        setattr(resource, field, value.strip() if isinstance(value, str) else value)

    db.commit()
    db.refresh(resource)

    return {"message": "Contenido educativo actualizado correctamente", "resource": resource_to_dict(resource, public=False)}


@router.delete("/resources/{resource_id}")
def delete_resource(
    resource_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)

    resource = db.query(models.EducationResource).filter(models.EducationResource.id == resource_id).first()

    if not resource:
        raise HTTPException(status_code=404, detail="Contenido no encontrado")

    db.delete(resource)
    db.commit()

    return {"message": "Contenido educativo eliminado correctamente", "resource_id": resource_id}
@router.post("/upload-file")
async def upload_education_file(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)
    configure_cloudinary()

    try:
        content_type = file.content_type or ""
        filename = file.filename or "archivo"
        lower_filename = filename.lower()

        if lower_filename.endswith(".doc") or lower_filename.endswith(".docx"):
            raise HTTPException(
                status_code=400,
                detail="Para proteger documentos, conviértelos primero a PDF y súbelos como PDF."
            )

        is_video = (
            content_type.startswith("video/")
            or lower_filename.endswith((".mp4", ".mov", ".m4v"))
        )

        is_pdf = (
            lower_filename.endswith(".pdf")
            or content_type == "application/pdf"
        )

        if is_video:
            upload_resource_type = "video"
            returned_resource_type = "video"
        elif is_pdf:
            upload_resource_type = "raw"
            returned_resource_type = "pdf"
        else:
            upload_resource_type = "image"
            returned_resource_type = "image"

        result = cloudinary.uploader.upload(
            file.file,
            folder="mayu_education",
            resource_type=upload_resource_type,
            use_filename=True,
            unique_filename=True,
            overwrite=False,
        )

        return {
            "message": "Archivo subido correctamente",
            "file_url": result.get("secure_url"),
            "public_id": result.get("public_id"),
            "resource_type": returned_resource_type,
            "filename": filename,
            "content_type": content_type,
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo subir el archivo: {str(e)}"
        )
