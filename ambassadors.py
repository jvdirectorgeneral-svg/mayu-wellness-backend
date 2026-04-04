from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
import models
import uuid
from schemas import AmbassadorRegister, AmbassadorLogin

router = APIRouter(prefix="/ambassadors", tags=["Ambassadors"])


def generate_ambassador_code(ambassador_id: int):
    return f"EMB-{ambassador_id:06d}"


# =========================
# 🤝 REGISTRO DE EMBAJADOR
# =========================
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

    user = models.User(
        name=data.name,
        email=data.email,
        password=data.password,
        status="registered",
        membership_level=None,
        membership_active=False,
        role="ambassador"
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
            "role": user.role
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


# =========================
# 🔐 LOGIN DE EMBAJADOR
# =========================
@router.post("/login")
def login_ambassador(
    data: AmbassadorLogin,
    db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(
        models.User.email == data.email,
        models.User.password == data.password,
        models.User.role == "ambassador"
    ).first()

    if not user:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    ambassador = db.query(models.Ambassador).filter(
        models.Ambassador.user_id == user.id
    ).first()

    if not ambassador:
        raise HTTPException(status_code=404, detail="Perfil de embajador no encontrado")

    return {
        "message": "Login de embajador exitoso",
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role
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


# =========================
# 👤 PERFIL DEL EMBAJADOR
# =========================
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
from fastapi import HTTPException
from sqlalchemy.orm import Session
from dependencies import get_db
import models
from fastapi import Depends

# =========================
# 💳 TARJETA DE EMBAJADOR
# =========================
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

    return {
        "name": user.name,
        "type": "Embajador Mayu",
        "code": ambassador.ambassador_code,
        "valid_until": "Indefinido",
        "status": ambassador.status,
        "qr_token": ambassador.ambassador_token
    }
