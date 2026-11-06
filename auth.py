import os
from datetime import datetime, timedelta, UTC

from jose import jwt
from passlib.context import CryptContext


def _load_secret_key() -> str:
    value = (os.getenv("SECRET_KEY") or "").strip()
    production = bool(os.getenv("RENDER")) or os.getenv("APP_ENV", "").lower() in {
        "production",
        "live",
    }
    if value:
        if production and len(value) < 48:
            raise RuntimeError("SECRET_KEY debe tener al menos 48 caracteres en producción")
        return value
    if production:
        raise RuntimeError("Falta SECRET_KEY en producción")
    return "development-only-secret-key-change-before-production"


SECRET_KEY = _load_secret_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24


pwd_context = CryptContext(
    schemes=["pbkdf2_sha256"],
    deprecated="auto",
)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
