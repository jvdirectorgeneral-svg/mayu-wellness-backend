from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from database import engine, Base
from users import router as users_router
from products import router as products_router  # 🔥 NUEVO
from dependencies import get_current_user
import models

# =========================
# 🔧 CREAR TABLAS
# =========================
Base.metadata.create_all(bind=engine)

# =========================
# 🚀 APP
# =========================
app = FastAPI(
    title="Mayu Wellness API",
    version="1.0.0"
)

# =========================
# 🔥 CORS (NECESARIO PARA FLUTTER WEB)
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # luego en producción se restringe
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# 📡 ROUTERS
# =========================
app.include_router(users_router)
app.include_router(products_router)  # 🔥 NUEVO

# =========================
# ROOT
# =========================
@app.get("/")
def read_root():
    return {"message": "Mayu Wellness Backend funcionando 🚀"}

# =========================
# HEALTH CHECK
# =========================
@app.get("/health")
def health_check():
    return {"status": "ok"}

# =========================
# 🔐 USUARIO ACTUAL
# =========================
@app.get("/me")
def get_me(current_user: models.User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "membership_level": current_user.membership_level,
        "membership_active": current_user.membership_active
    }
