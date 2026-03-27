from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import SessionLocal
from auth import hash_password, verify_password, create_access_token
import models

router = APIRouter()

class UserCreate(BaseModel):
    name: str
    email: str
    password: str

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
    return {"users": users}

@router.post("/users")
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    existing_user = db.query(models.User).filter(models.User.email == user.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")

    new_user = models.User(
        name=user.name,
        email=user.email,
        password=hash_password(user.password)
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "id": new_user.id,
        "name": new_user.name,
        "email": new_user.email,
        "status": new_user.status,
        "membership_level": new_user.membership_level,
        "membership_active": new_user.membership_active
    }

@router.put("/users/{user_id}/membership")
def update_membership(user_id: int, membership: MembershipUpdate, db: Session = Depends(get_db)):
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
        "status": user.status,
        "membership_level": user.membership_level,
        "membership_active": user.membership_active
    }

@router.post("/login")
def login(user: LoginRequest, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == user.email).first()

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
            "membership_level": db_user.membership_level,
            "membership_active": db_user.membership_active
        }
    }
