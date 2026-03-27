from fastapi import APIRouter

router = APIRouter()

@router.get("/users")
def get_users():
    return {
        "users": [
            {
                "id": 1,
                "name": "Julio Vicencio",
                "email": "julio@mayu.com",
                "status": "active"
            }
        ]
    }
