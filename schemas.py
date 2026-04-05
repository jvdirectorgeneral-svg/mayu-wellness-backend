from pydantic import BaseModel


# =========================
# 👤 REGISTRO USUARIO NORMAL
# =========================
class UserRegister(BaseModel):
    name: str
    email: str
    password: str
    phone: str
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
