from pydantic import BaseModel


# =========================
# 👤 REGISTRO USUARIO NORMAL
# =========================
class UserRegister(BaseModel):
    name: str
    email: str
    password: str
    phone: str
    delivery_address: str  # 🆕 domicilio obligatorio

    ambassador_code: str | None = None


# =========================
# 🔐 LOGIN USUARIO NORMAL
# =========================
class UserLogin(BaseModel):
    email: str
    password: str


# =========================
# 🤝 REGISTRO DE EMBAJADOR
# =========================
class AmbassadorRegister(BaseModel):
    name: str
    email: str
    password: str
    phone: str

    national_id: str
    address: str
    bank_name: str
    account_type: str
    bank_account_number: str


# =========================
# 🔐 LOGIN DE EMBAJADOR
# =========================
class AmbassadorLogin(BaseModel):
    email: str
    password: str


# =========================
# 👤 RESPUESTA USUARIO (ME)
# =========================
class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    phone: str | None
    delivery_address: str | None  # 🆕 devolver dirección

    membership_level: int | None
    membership_active: bool

    class Config:
        from_attributes = True
