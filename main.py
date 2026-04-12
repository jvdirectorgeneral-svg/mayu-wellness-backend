from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from database import engine, Base
from users import router as users_router
from products import router as products_router
from plans import router as plans_router
from plan_products import router as plan_products_router
from plan_selection import router as plan_selection_router
from monthly_selection import router as monthly_selection_router
from plan_change import router as plan_change_router
from member_cards import router as member_cards_router
from ambassadors import router as ambassadors_router
from commissions import router as commissions_router
from admin_dashboard import router as admin_dashboard_router
from supervisor_dashboard import router as supervisor_dashboard_router
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
# 🌐 CORS
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://mayuclub.com",
        "https://www.mayuclub.com",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# 📡 ROUTERS
# =========================
app.include_router(users_router)
app.include_router(products_router)
app.include_router(plans_router)
app.include_router(plan_products_router)
app.include_router(plan_selection_router)
app.include_router(monthly_selection_router)
app.include_router(plan_change_router)
app.include_router(member_cards_router)
app.include_router(ambassadors_router)
app.include_router(commissions_router)
app.include_router(admin_dashboard_router)
app.include_router(supervisor_dashboard_router)

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
        "membership_active": current_user.membership_active,
        "role": current_user.role
    }
