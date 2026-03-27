from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import SessionLocal
import models

router = APIRouter()

class UserCreate(BaseModel):
    name: str
    email: str
    password: str

class MembershipUpdate(BaseModel):
    level: int
    active: bool

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
    new_user = models.User(
        name=user.name,
        email=user.email,
        password=user.password
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@router.put("/users/{user_id}/membership")
def update_membership(user_id: int, membership: MembershipUpdate, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        return {"error": "Usuario no encontrado"}

    user.membership_level = membership.level
    user.membership_active = membership.active

    db.commit()
    db.refresh(user)

    return user
