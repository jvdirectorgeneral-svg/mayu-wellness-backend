from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from database import SessionLocal
from dependencies import get_current_user
import models

import os
import cloudinary
import cloudinary.uploader

router = APIRouter(prefix="/education", tags=["education"])


# =========================
# SCHEMAS
# =========================

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


class EducationResourceUpdate(BaseModel):
    title: Optional[str] = None
    category_id: Optional[int] = None
    resource_type: Optional[str] = None

    file_url: Optional[str] = None
    external_url: Optional[str] = None
    cover_image_url: Optional[str] = None

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


# =========================
# DB
# =========================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =========================
# PERMISOS
# =========================

def require_education_admin(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {
        "superadmin",
        "admin",
        "education_admin",
    }:
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para Mayu Educación",
        )


def require_member_or_team(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {
        "superadmin",
        "admin",
        "education_admin",
        "member",
        "ambassador",
    }:
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para socios Mayu",
        )


# =========================
# CLOUDINARY
# =========================

def configure_cloudinary():
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    api_key = os.getenv("CLOUDINARY_API_KEY")
    api_secret = os.getenv("CLOUDINARY_API_SECRET")

    if not cloud_name or not api_key or not api_secret:
        raise HTTPException(
            status_code=500,
            detail="Faltan variables CLOUDINARY en Render",
        )

    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,
    )


# =========================
# SERIALIZADORES
# =========================

def category_to_dict(category: models.EducationCategory):
    return {
        "id": category.id,
        "name": category.name,
        "description": category.description,
        "active": category.active,
        "created_at": category.created_at,
    }


def resource_to_dict(resource: models.EducationResource):
    return {
        "id": resource.id,
        "title": resource.title,
        "category_id": resource.category_id,
        "category_name": resource.category_rel.name if resource.category_rel else None,
        "resource_type": resource.resource_type,
        "file_url": resource.file_url,
        "external_url": resource.external_url,
        "cover_image_url": resource.cover_image_url,
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
        "created_by": resource.created_by,
        "created_at": resource.created_at,
        "updated_at": resource.updated_at,
    }


# =========================
# CATEGORÍAS PÚBLICAS / SOCIOS
# =========================

@router.get("/categories")
def get_public_categories(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_member_or_team(current_user)

    categories = (
        db.query(models.EducationCategory)
        .filter(models.EducationCategory.active == True)
        .order_by(models.EducationCategory.name.asc())
        .all()
    )

    return {"items": [category_to_dict(c) for c in categories]}


# =========================
# CATEGORÍAS ADMIN
# =========================

@router.get("/admin/categories")
def get_admin_categories(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)

    categories = (
        db.query(models.EducationCategory)
        .order_by(models.EducationCategory.id.desc())
        .all()
    )

    return {"items": [category_to_dict(c) for c in categories]}


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

    existing = (
        db.query(models.EducationCategory)
        .filter(models.EducationCategory.name == name)
        .first()
    )

    if existing:
        raise HTTPException(status_code=400, detail="La categoría ya existe")

    category = models.EducationCategory(
        name=name,
        description=payload.description,
        active=payload.active,
    )

    db.add(category)
    db.commit()
    db.refresh(category)

    return {
        "message": "Categoría educativa creada correctamente",
        "category": category_to_dict(category),
    }


@router.put("/categories/{category_id}")
def update_category(
    category_id: int,
    payload: EducationCategoryUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)

    category = (
        db.query(models.EducationCategory)
        .filter(models.EducationCategory.id == category_id)
        .first()
    )

    if not category:
        raise HTTPException(status_code=404, detail="Categoría no encontrada")

    if payload.name is not None:
        name = payload.name.strip()

        if not name:
            raise HTTPException(status_code=400, detail="El nombre es obligatorio")

        duplicate = (
            db.query(models.EducationCategory)
            .filter(
                models.EducationCategory.name == name,
                models.EducationCategory.id != category_id,
            )
            .first()
        )

        if duplicate:
            raise HTTPException(
                status_code=400,
                detail="Ya existe otra categoría con ese nombre",
            )

        category.name = name

    if payload.description is not None:
        category.description = payload.description

    if payload.active is not None:
        category.active = payload.active

    db.commit()
    db.refresh(category)

    return {
        "message": "Categoría educativa actualizada correctamente",
        "category": category_to_dict(category),
    }


@router.delete("/categories/{category_id}")
def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)

    category = (
        db.query(models.EducationCategory)
        .filter(models.EducationCategory.id == category_id)
        .first()
    )

    if not category:
        raise HTTPException(status_code=404, detail="Categoría no encontrada")

    has_resources = (
        db.query(models.EducationResource)
        .filter(models.EducationResource.category_id == category_id)
        .first()
    )

    if has_resources:
        raise HTTPException(
            status_code=400,
            detail="No puedes borrar una categoría que tiene contenidos. Primero mueve o elimina esos contenidos.",
        )

    db.delete(category)
    db.commit()

    return {
        "message": "Categoría educativa eliminada correctamente",
        "category_id": category_id,
    }


# =========================
# RECURSOS PÚBLICOS / SOCIOS
# =========================

@router.get("/resources")
def get_public_resources(
    category_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_member_or_team(current_user)

    query = (
        db.query(models.EducationResource)
        .filter(models.EducationResource.active == True)
        .filter(models.EducationResource.free_for_members == True)
    )

    if category_id:
        query = query.filter(models.EducationResource.category_id == category_id)

    if resource_type and resource_type.strip():
        query = query.filter(
            models.EducationResource.resource_type == resource_type.strip()
        )

    if search and search.strip():
        clean_search = f"%{search.strip()}%"
        query = query.filter(models.EducationResource.title.ilike(clean_search))

    resources = query.order_by(models.EducationResource.id.desc()).all()

    return {"items": [resource_to_dict(r) for r in resources]}


@router.get("/resources/{resource_id}")
def get_public_resource_detail(
    resource_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_member_or_team(current_user)

    resource = (
        db.query(models.EducationResource)
        .filter(models.EducationResource.id == resource_id)
        .filter(models.EducationResource.active == True)
        .filter(models.EducationResource.free_for_members == True)
        .first()
    )

    if not resource:
        raise HTTPException(status_code=404, detail="Contenido no encontrado")

    return resource_to_dict(resource)


# =========================
# RECURSOS ADMIN
# =========================

@router.get("/admin/resources")
def get_admin_resources(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)

    resources = (
        db.query(models.EducationResource)
        .order_by(models.EducationResource.id.desc())
        .all()
    )

    return {"items": [resource_to_dict(r) for r in resources]}


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

    if payload.category_id:
        category = (
            db.query(models.EducationCategory)
            .filter(models.EducationCategory.id == payload.category_id)
            .first()
        )

        if not category:
            raise HTTPException(status_code=404, detail="Categoría no encontrada")

    resource = models.EducationResource(
        title=title,
        category_id=payload.category_id,
        resource_type=payload.resource_type.strip(),
        file_url=payload.file_url,
        external_url=payload.external_url,
        cover_image_url=payload.cover_image_url,
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
        created_by=current_user.id,
    )

    db.add(resource)
    db.commit()
    db.refresh(resource)

    return {
        "message": "Contenido educativo creado correctamente",
        "resource": resource_to_dict(resource),
    }


@router.put("/resources/{resource_id}")
def update_resource(
    resource_id: int,
    payload: EducationResourceUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)

    resource = (
        db.query(models.EducationResource)
        .filter(models.EducationResource.id == resource_id)
        .first()
    )

    if not resource:
        raise HTTPException(status_code=404, detail="Contenido no encontrado")

    if payload.category_id is not None:
        if payload.category_id != 0:
            category = (
                db.query(models.EducationCategory)
                .filter(models.EducationCategory.id == payload.category_id)
                .first()
            )

            if not category:
                raise HTTPException(status_code=404, detail="Categoría no encontrada")

            resource.category_id = payload.category_id
        else:
            resource.category_id = None

    for field, value in payload.dict(exclude_unset=True).items():
        if field == "category_id":
            continue

        if field == "title" and value is not None:
            value = value.strip()

            if not value:
                raise HTTPException(
                    status_code=400,
                    detail="El título es obligatorio",
                )

        if field == "resource_type" and value is not None:
            value = value.strip()

        setattr(resource, field, value)

    db.commit()
    db.refresh(resource)

    return {
        "message": "Contenido educativo actualizado correctamente",
        "resource": resource_to_dict(resource),
    }


@router.delete("/resources/{resource_id}")
def delete_resource(
    resource_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)

    resource = (
        db.query(models.EducationResource)
        .filter(models.EducationResource.id == resource_id)
        .first()
    )

    if not resource:
        raise HTTPException(status_code=404, detail="Contenido no encontrado")

    db.delete(resource)
    db.commit()

    return {
        "message": "Contenido educativo eliminado correctamente",
        "resource_id": resource_id,
    }


# =========================
# SUBIDA DE ARCHIVOS
# =========================

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

        resource_type = "raw"

        if content_type.startswith("image/"):
            resource_type = "image"
        elif content_type.startswith("video/"):
            resource_type = "video"
        else:
            resource_type = "raw"

        result = cloudinary.uploader.upload(
            file.file,
            folder="mayu_education",
            resource_type=resource_type,
            public_id=None,
            overwrite=False,
        )

        return {
            "message": "Archivo subido correctamente",
            "file_url": result.get("secure_url"),
            "public_id": result.get("public_id"),
            "resource_type": resource_type,
            "filename": filename,
            "content_type": content_type,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo subir el archivo: {str(e)}",
        )
