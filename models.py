from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from database import Base

# =========================
# 👤 USUARIO
# =========================
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)

    status = Column(String, default="registered")

    membership_level = Column(Integer, nullable=True)
    membership_active = Column(Boolean, default=False)


# =========================
# 💎 PLANES
# =========================
class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    level = Column(Integer, nullable=False)
    price = Column(Integer, nullable=False)
    description = Column(String, nullable=True)
    active = Column(Boolean, default=True)


# =========================
# 🧪 PRODUCTOS
# =========================
class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    price = Column(Integer, nullable=False)
    description = Column(String, nullable=True)
    image_url = Column(String, nullable=True)  # 🔥 NUEVO
    active = Column(Boolean, default=True)


# =========================
# 🔗 RELACIÓN PLAN - PRODUCTO
# =========================
class PlanProduct(Base):
    __tablename__ = "plan_products"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("plans.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    is_required = Column(Boolean, default=False)
    max_quantity = Column(Integer, default=1)
