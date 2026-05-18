import os
import cloudinary
import cloudinary.uploader

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException

from dependencies import get_current_user
from models import User


router = APIRouter(prefix="/marketing", tags=["marketing-uploads"])


def require_marketing_user(current_user: User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role != "marketing":
        raise HTTPException(status_code=403, detail="Acceso solo para marketing")


cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True,
)


@router.post("/upload-image")
async def upload_marketing_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    require_marketing_user(current_user)

    if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(
            status_code=400,
            detail="Solo se permiten imágenes JPG, PNG o WEBP",
        )

    try:
        result = cloudinary.uploader.upload(
            file.file,
            folder="mayu_marketing",
            resource_type="image",
        )

        return {
            "message": "Imagen subida correctamente",
            "image_url": result.get("secure_url"),
            "public_id": result.get("public_id"),
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo subir la imagen: {str(e)}",
        )
