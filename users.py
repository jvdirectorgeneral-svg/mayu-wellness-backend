from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from database import SessionLocal
from auth import hash_password, verify_password, create_access_token
import models

router = APIRouter()


class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    phone: str
    delivery_address: str
    ambassador_code: Optional[str] = None


class MembershipUpdate(BaseModel):
    level: int
    active: bool


class LoginRequest(BaseModel):
    email: str
    password: str


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/users")
def get_users(db: Session = Depends(get_db)):
    users = db.query(models.User).all()
    return {
        "users": [
            {
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "phone": u.phone,
                "delivery_address": u.delivery_address,
                "status": u.status,
                "membership_level": u.membership_level,
                "membership_active": u.membership_active,
                "role": u.role
            }
            for u in users
        ]
    }


@router.post("/users")
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    existing_user = db.query(models.User).filter(
        models.User.email == user.email
    ).first()

    if existing_user:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")

    ambassador = None
    cleaned_ambassador_code = None

    if user.ambassador_code is not None and user.ambassador_code.strip() != "":
        cleaned_ambassador_code = user.ambassador_code.strip()

        ambassador = db.query(models.Ambassador).filter(
            models.Ambassador.ambassador_code == cleaned_ambassador_code
        ).first()

        if not ambassador:
            raise HTTPException(status_code=400, detail="Código de embajador inválido")

    new_user = models.User(
        name=user.name,
        email=user.email,
        password=hash_password(user.password),
        phone=user.phone,
        delivery_address=user.delivery_address,
        role="member"
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    if ambassador:
        referral = models.AmbassadorReferral(
            ambassador_id=ambassador.id,
            user_id=new_user.id,
            referral_code=cleaned_ambassador_code,
            status="active"
        )
        db.add(referral)
        db.commit()

    return {
        "id": new_user.id,
        "name": new_user.name,
        "email": new_user.email,
        "phone": new_user.phone,
        "delivery_address": new_user.delivery_address,
        "status": new_user.status,
        "membership_level": new_user.membership_level,
        "membership_active": new_user.membership_active,
        "role": new_user.role,
        "ambassador_code": cleaned_ambassador_code
    }


@router.put("/users/{user_id}/membership")
def update_membership(
    user_id: int,
    membership: MembershipUpdate,
    db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user.membership_level = membership.level
    user.membership_active = membership.active

    db.commit()
    db.refresh(user)

    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "delivery_address": user.delivery_address,
        "status": user.status,
        "membership_level": user.membership_level,
        "membership_active": user.membership_active,
        "role": user.role
    }


@router.post("/login")
def login(user: LoginRequest, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(
        models.User.email == user.email
    ).first()

    if not db_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not verify_password(user.password, db_user.password):
        raise HTTPException(status_code=401, detail="Contraseña incorrecta")

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
            "delivery_address": db_user.delivery_address,
            "membership_level": db_user.membership_level,
            "membership_active": db_user.membership_active,
            "role": db_user.role
        }
    }
