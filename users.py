from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

users_db = []

class UserCreate(BaseModel):
    name: str
    email: str

class MembershipUpdate(BaseModel):
    level: int
    active: bool

@router.get("/users")
def get_users():
    return {"users": users_db}

@router.post("/users")
def create_user(user: UserCreate):
    new_user = {
        "id": len(users_db) + 1,
        "name": user.name,
        "email": user.email,
        "status": "registered",
        "membership": {
            "level": None,
            "active": False
        }
    }
    users_db.append(new_user)
    return {
        "message": "Usuario creado correctamente",
        "user": new_user
    }

@router.put("/users/{user_id}/membership")
def update_membership(user_id: int, membership: MembershipUpdate):
    for user in users_db:
        if user["id"] == user_id:
            user["membership"]["level"] = membership.level
            user["membership"]["active"] = membership.active

            return {
                "message": "Membresía actualizada",
                "user": user
            }

    return {"error": "Usuario no encontrado"}
