from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    ForeignKey,
    DateTime,
    Float,
    UniqueConstraint,
    Text,
)
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


# =========================
# 👤 USUARIO
# =========================
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    password = Column(String, nullable=False)

    phone = Column(String, nullable=False)
    cedula = Column(String, unique=True, nullable=False, index=True)
    city = Column(String, nullable=False)
    address = Column(String, nullable=False)
    reference = Column(String, nullable=False)
    delivery_notes = Column(Text, nullable=False)
    phone_secondary = Column(String, nullable=True)

    status = Column(String, default="registered", nullable=False)

    membership_level = Column(Integer, nullable=True)
    membership_active = Column(Boolean, default=False, nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)

    # member / ambassador / admin / supervisor / logistics / superadmin
    role = Column(String, default="member", nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    ambassador_profile = relationship(
        "Ambassador",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )

    referrals_as_user = relationship(
        "AmbassadorReferral",
        foreign_keys="AmbassadorReferral.user_id",
        back_populates="referred_user"
    )

    monthly_selections = relationship(
        "MonthlySelection",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    plan_change_requests = relationship(
        "PlanChangeRequest",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    member_card = relationship(
        "MemberCard",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )

    commissions_as_referred = relationship(
        "Commission",
        foreign_keys="Commission.referred_user_id",
        back_populates="referred_user"
    )

    orders = relationship(
        "Order",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    payments = relationship(
        "MembershipPayment",
        foreign_keys="MembershipPayment.user_id",
        back_populates="user",
        cascade="all, delete-orphan"
    )


# =========================
# 🤝 EMBAJADOR
# =========================
class Ambassador(Base):
    __tablename__ = "ambassadors"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)

    ambassador_code = Column(String, unique=True, index=True, nullable=False)
    ambassador_token = Column(String, unique=True, index=True, nullable=False)

    national_id = Column(String, nullable=False)
    address = Column(String, nullable=False)
    bank_name = Column(String, nullable=False)
    account_type = Column(String, nullable=False)
    bank_account_number = Column(String, nullable=False)

    status = Column(String, default="active", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="ambassador_profile")

    referrals = relationship(
        "AmbassadorReferral",
        back_populates="ambassador",
        cascade="all, delete-orphan"
    )

    commissions = relationship(
        "Commission",
        back_populates="ambassador",
        cascade="all, delete-orphan"
    )


# =========================
# 🔗 REFERIDOS DE EMBAJADOR
# =========================
class AmbassadorReferral(Base):
    __tablename__ = "ambassador_referrals"

    id = Column(Integer, primary_key=True, index=True)

    ambassador_id = Column(
        Integer,
        ForeignKey("ambassadors.id"),
        nullable=False
    )

    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        unique=True
    )

    referral_code = Column(String, nullable=False)
    status = Column(String, default="active", nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    ambassador = relationship(
        "Ambassador",
        back_populates="referrals"
    )

    referred_user = relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="referrals_as_user"
    )


# =========================
# 💎 PLANES
# =========================
class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    level = Column(Integer, nullable=False, unique=True, index=True)
    price = Column(Float, nullable=False)
    description = Column(String, nullable=True)
    active = Column(Boolean, default=True, nullable=False)

    plan_products = relationship(
        "PlanProduct",
        back_populates="plan",
        cascade="all, delete-orphan"
    )

    monthly_selections = relationship(
        "MonthlySelection",
        back_populates="plan"
    )

    change_requests_current = relationship(
        "PlanChangeRequest",
        foreign_keys="PlanChangeRequest.current_plan_id",
        back_populates="current_plan"
    )

    change_requests_requested = relationship(
        "PlanChangeRequest",
        foreign_keys="PlanChangeRequest.requested_plan_id",
        back_populates="requested_plan"
    )

    commissions = relationship(
        "Commission",
        back_populates="plan"
    )


# =========================
# 🧪 PRODUCTOS
# =========================
class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True, index=True)
    price = Column(Float, nullable=False)
    description = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    active = Column(Boolean, default=True, nullable=False)

    plan_products = relationship(
        "PlanProduct",
        back_populates="product",
        cascade="all, delete-orphan"
    )

    monthly_selection_items = relationship(
        "MonthlySelectionItem",
        back_populates="product"
    )

    order_items = relationship(
        "OrderItem",
        back_populates="product"
    )


# =========================
# 🔗 RELACIÓN PLAN - PRODUCTO
# =========================
class PlanProduct(Base):
    __tablename__ = "plan_products"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    is_required = Column(Boolean, default=False, nullable=False)
    max_quantity = Column(Integer, default=1, nullable=False)

    plan = relationship("Plan", back_populates="plan_products")
    product = relationship("Product", back_populates="plan_products")

    __table_args__ = (
        UniqueConstraint("plan_id", "product_id", name="uq_plan_product"),
    )


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

    status = Column(String, default="draft", nullable=False)
    editable = Column(Boolean, default=True, nullable=False)

    user = relationship("User", back_populates="monthly_selections")
    plan = relationship("Plan", back_populates="monthly_selections")

    items = relationship(
        "MonthlySelectionItem",
        back_populates="monthly_selection",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "month",
            "year",
            name="uq_monthly_selection_user_cycle"
        ),
    )


# =========================
# 📦 ITEMS DE LA SELECCIÓN MENSUAL
# =========================
class MonthlySelectionItem(Base):
    __tablename__ = "monthly_selection_items"

    id = Column(Integer, primary_key=True, index=True)
    monthly_selection_id = Column(
        Integer,
        ForeignKey("monthly_selections.id"),
        nullable=False
    )
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, default=1, nullable=False)

    monthly_selection = relationship(
        "MonthlySelection",
        back_populates="items"
    )

    product = relationship(
        "Product",
        back_populates="monthly_selection_items"
    )

    __table_args__ = (
        UniqueConstraint(
            "monthly_selection_id",
            "product_id",
            name="uq_monthly_selection_item_product"
        ),
    )


# =========================
# 🔁 SOLICITUD DE CAMBIO DE PLAN
# =========================
class PlanChangeRequest(Base):
    __tablename__ = "plan_change_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    current_plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)
    requested_plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)

    status = Column(String, default="pending", nullable=False)
    effective_month = Column(Integer, nullable=True)
    effective_year = Column(Integer, nullable=True)

    user = relationship("User", back_populates="plan_change_requests")

    current_plan = relationship(
        "Plan",
        foreign_keys=[current_plan_id],
        back_populates="change_requests_current"
    )

    requested_plan = relationship(
        "Plan",
        foreign_keys=[requested_plan_id],
        back_populates="change_requests_requested"
    )


# =========================
# 💳 TARJETA DIGITAL DEL SOCIO
# =========================
class MemberCard(Base):
    __tablename__ = "member_cards"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)

    member_code = Column(String, unique=True, nullable=False)
    qr_token = Column(String, unique=True, nullable=False)

    level_snapshot = Column(Integer, nullable=False)
    status = Column(String, default="active", nullable=False)
    expires_at = Column(String, nullable=True)

    user = relationship("User", back_populates="member_card")


# =========================
# 💰 COMISIONES MENSUALES
# =========================
class Commission(Base):
    __tablename__ = "commissions"

    id = Column(Integer, primary_key=True, index=True)

    ambassador_id = Column(
        Integer,
        ForeignKey("ambassadors.id"),
        nullable=False
    )

    referred_user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False
    )

    plan_id = Column(
        Integer,
        ForeignKey("plans.id"),
        nullable=False
    )

    month = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)

    base_amount = Column(Float, nullable=False)
    commission_percent = Column(Float, nullable=False, default=14.5)
    commission_amount = Column(Float, nullable=False)

    member_status = Column(String, nullable=False, default="active")
    payment_status = Column(String, nullable=False, default="paid")
    eligibility_status = Column(String, nullable=False, default="eligible")
    status = Column(String, nullable=False, default="pending")

    generated_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)
    notes = Column(String, nullable=True)

    ambassador = relationship(
        "Ambassador",
        back_populates="commissions"
    )

    referred_user = relationship(
        "User",
        foreign_keys=[referred_user_id],
        back_populates="commissions_as_referred"
    )

    plan = relationship(
        "Plan",
        back_populates="commissions"
    )

    __table_args__ = (
        UniqueConstraint(
            "ambassador_id",
            "referred_user_id",
            "month",
            "year",
            name="uq_commission_monthly"
        ),
    )


# =========================
# 📦 ÓRDENES
# =========================
class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    order_code = Column(String, unique=True, nullable=False, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    month = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)

    membership_level_snapshot = Column(Integer, nullable=True)
    user_status_snapshot = Column(String, nullable=False, default="inactive")

    city_snapshot = Column(String, nullable=False)
    address_snapshot = Column(String, nullable=False)
    reference_snapshot = Column(String, nullable=False)
    delivery_notes_snapshot = Column(Text, nullable=False)

    status = Column(String, nullable=False, default="pending")
    logistics_notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    prepared_at = Column(DateTime, nullable=True)
    shipped_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)

    # NUEVO: agrupa órdenes que salen en el despacho semanal de logística.
    # Ejemplo: todos los viernes se marca al enviar.
    shipping_batch_date = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="orders")

    items = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan"
    )

    payments = relationship(
        "MembershipPayment",
        back_populates="order"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "month", "year", name="uq_order_user_cycle"),
    )


# =========================
# 📦 ITEMS DE ORDEN
# =========================
class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)

    product_name_snapshot = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False, default=1)

    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")


# =========================
# 💸 PAGOS DE MEMBRESÍA
# =========================
class MembershipPayment(Base):
    __tablename__ = "membership_payments"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)

    payment_type = Column(String, nullable=False, default="signup")
    provider = Column(String, nullable=False, default="paypal")

    paypal_order_id = Column(String, unique=True, nullable=True, index=True)
    paypal_capture_id = Column(String, unique=True, nullable=True, index=True)

    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False, default="USD")

    status = Column(String, nullable=False, default="created")
    payer_email = Column(String, nullable=True)
    payment_reference = Column(String, nullable=True)
    receipt_url = Column(String, nullable=True)

    raw_payload = Column(Text, nullable=True)

    admin_verified = Column(Boolean, default=False, nullable=False)
    admin_verified_at = Column(DateTime, nullable=True)
    admin_verified_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)

    user = relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="payments"
    )

    order = relationship(
        "Order",
        back_populates="payments"
    )

    admin_verifier = relationship(
        "User",
        foreign_keys=[admin_verified_by]
    )
