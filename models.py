from sqlalchemy import Column, Integer, String, Boolean
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)  # 👈 NUEVO
    status = Column(String, default="registered")
    membership_level = Column(Integer, nullable=True)
    membership_active = Column(Boolean, default=False)
