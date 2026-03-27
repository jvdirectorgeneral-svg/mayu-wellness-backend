from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

users_db = []

class UserCreate(BaseModel):
    name: str
    email: str
    status: str = "registered"

@router.get("/users")
def get_users():
    return {"users": users_db}

@router.post("/users")
def create_user(user: UserCreate):
    new_user = {
        "id": len(users_db) + 1,
        "name": user.name,
        "email": user.email,
        "status": user.status
    }
    users_db.append(new_user)
    return {
        "message": "Usuario creado correctamente",
        "user": new_user
    }
