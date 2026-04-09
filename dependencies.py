from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from database import SessionLocal
from auth import SECRET_KEY, ALGORITHM
import models

security = HTTPBearer()


# =========================
# DB
# =========================
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =========================
# 🔐 USUARIO ACTUAL
# =========================
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    token = credentials.credentials

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")

        if user_id is None:
            raise HTTPException(status_code=401, detail="Token inválido")

    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido")

    user = db.query(models.User).filter(
        models.User.id == int(user_id)
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # 🔥 NUEVO — CONTROL GLOBAL DE ACCESO
    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario desactivado")

    return user
