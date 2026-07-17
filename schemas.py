from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime


# =========================
# 👤 REGISTRO USUARIO NORMAL
# =========================
class UserRegister(BaseModel):
    name: str
    email: EmailStr
    password: str

    phone: str
    cedula: str
    city: str
    address: str
    reference: str
    delivery_notes: str

    phone_secondary: Optional[str] = None

    ambassador_code: Optional[str] = None

    # Políticas obligatorias
    accepted_terms: bool = False
    accepted_privacy_policy: bool = False
    accepted_digital_policy: bool = False


# =========================
# 🔐 LOGIN USUARIO NORMAL
# =========================
class UserLogin(BaseModel):
    email: EmailStr
    password: str


# =========================
# 🤝 REGISTRO DE EMBAJADOR
# =========================
class AmbassadorRegister(BaseModel):
    name: str
    email: EmailStr
    password: str
    phone: str
    birth_date: Optional[datetime] = None

    national_id: str
    address: str
    bank_name: str
    account_type: str
    bank_account_number: str


# =========================
# 🔐 LOGIN DE EMBAJADOR
# =========================
class AmbassadorLogin(BaseModel):
    email: EmailStr
    password: str


# =========================
# 👤 RESPUESTA USUARIO (ME)
# =========================
class UserResponse(BaseModel):
    id: int
    name: str
    email: EmailStr

    phone: Optional[str]
    phone_secondary: Optional[str]

    cedula: str
    city: str
    address: str
    reference: str
    delivery_notes: str

    membership_level: Optional[int]
    membership_active: bool

    accepted_terms: bool
    accepted_privacy_policy: bool
    accepted_digital_policy: bool

    accepted_terms_at: Optional[datetime]
    accepted_privacy_policy_at: Optional[datetime]
    accepted_digital_policy_at: Optional[datetime]

    class Config:
        from_attributes = True
