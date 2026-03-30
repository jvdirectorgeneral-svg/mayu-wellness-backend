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
    image_url = Column(String, nullable=True)
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


# =========================
# 📅 SELECCIÓN MENSUAL
# =========================
class MonthlySelection(Base):
    __tablename__ = "monthly_selections"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)

    month = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)

    status = Column(String, default="draft")  # draft / confirmed / locked
    editable = Column(Boolean, default=True)


# =========================
# 📦 ITEMS DE LA SELECCIÓN MENSUAL
# =========================
class MonthlySelectionItem(Base):
    __tablename__ = "monthly_selection_items"

    id = Column(Integer, primary_key=True, index=True)
    monthly_selection_id = Column(Integer, ForeignKey("monthly_selections.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, default=1)


# =========================
# 🔁 SOLICITUD DE CAMBIO DE PLAN
# =========================
class PlanChangeRequest(Base):
    __tablename__ = "plan_change_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    current_plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)
    requested_plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)

    status = Column(String, default="pending")  # pending / approved / rejected / applied
    effective_month = Column(Integer, nullable=True)
    effective_year = Column(Integer, nullable=True)
