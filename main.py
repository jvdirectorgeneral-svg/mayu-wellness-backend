from fastapi import FastAPI, Depends
from database import engine, Base
from users import router as users_router
from dependencies import get_current_user
import models

# Crear tablas
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Mayu Wellness API",
    version="1.0.0"
)

# Rutas existentes
app.include_router(users_router)

# ROOT
@app.get("/")
def read_root():
    return {"message": "Mayu Wellness Backend funcionando 🚀"}

# HEALTH CHECK (Render lo usa)
@app.get("/health")
def health_check():
    return {"status": "ok"}

# 🔐 ENDPOINT PROTEGIDO
@app.get("/me")
def get_me(current_user: models.User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "membership_level": current_user.membership_level,
        "membership_active": current_user.membership_active
    }
