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

    accepted_terms = Column(Boolean, default=False, nullable=False)
    accepted_privacy_policy = Column(Boolean, default=False, nullable=False)
    accepted_digital_policy = Column(Boolean, default=False, nullable=False)

    accepted_terms_at = Column(DateTime, nullable=True)
    accepted_privacy_policy_at = Column(DateTime, nullable=True)
    accepted_digital_policy_at = Column(DateTime, nullable=True)

    status = Column(String, default="registered", nullable=False)

    membership_level = Column(Integer, nullable=True)
    membership_active = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    role = Column(String, default="member", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    ambassador_profile = relationship(
        "Ambassador",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    referrals_as_user = relationship(
        "AmbassadorReferral",
        foreign_keys="AmbassadorReferral.user_id",
        back_populates="referred_user",
    )

    monthly_selections = relationship(
        "MonthlySelection",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    plan_change_requests = relationship(
        "PlanChangeRequest",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    member_card = relationship(
        "MemberCard",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    commissions_as_referred = relationship(
        "Commission",
        foreign_keys="Commission.referred_user_id",
        back_populates="referred_user",
    )

    orders = relationship(
        "Order",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    payments = relationship(
        "MembershipPayment",
        foreign_keys="MembershipPayment.user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    marketing_campaigns_created = relationship(
        "MarketingCampaign",
        foreign_keys="MarketingCampaign.created_by",
        back_populates="creator",
    )

    marketing_recipients = relationship(
        "MarketingCampaignRecipient",
        foreign_keys="MarketingCampaignRecipient.user_id",
        back_populates="user",
    )

    push_tokens = relationship(
        "PushNotificationToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Ambassador(Base):
    __tablename__ = "ambassadors"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)

    ambassador_code = Column(String, unique=True, index=True, nullable=False)
    ambassador_token = Column(String, unique=True, index=True, nullable=False)

    national_id = Column(String, nullable=False)
    address = Column(String, nullable=False)

    bank_name = Column(String, nullable=True)
    bank_account_type = Column(String, nullable=True)
    bank_account_number = Column(String, nullable=True)
    bank_account_holder = Column(String, nullable=True)
    bank_identification = Column(String, nullable=True)
    payment_notes = Column(Text, nullable=True)

    status = Column(String, default="active", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="ambassador_profile")

    referrals = relationship(
        "AmbassadorReferral",
        back_populates="ambassador",
        cascade="all, delete-orphan",
    )

    commissions = relationship(
        "Commission",
        back_populates="ambassador",
        cascade="all, delete-orphan",
    )


class AmbassadorReferral(Base):
    __tablename__ = "ambassador_referrals"

    id = Column(Integer, primary_key=True, index=True)
    ambassador_id = Column(Integer, ForeignKey("ambassadors.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)

    referral_code = Column(String, nullable=False)
    status = Column(String, default="active", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    ambassador = relationship("Ambassador", back_populates="referrals")

    referred_user = relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="referrals_as_user",
    )


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
        cascade="all, delete-orphan",
    )

    monthly_selections = relationship("MonthlySelection", back_populates="plan")

    change_requests_current = relationship(
        "PlanChangeRequest",
        foreign_keys="PlanChangeRequest.current_plan_id",
        back_populates="current_plan",
    )

    change_requests_requested = relationship(
        "PlanChangeRequest",
        foreign_keys="PlanChangeRequest.requested_plan_id",
        back_populates="requested_plan",
    )

    commissions = relationship("Commission", back_populates="plan")


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True, index=True)
    price = Column(Float, nullable=False)
    description = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    category = Column(String, nullable=True, index=True)
    active = Column(Boolean, default=True, nullable=False)

    plan_products = relationship(
        "PlanProduct",
        back_populates="product",
        cascade="all, delete-orphan",
    )

    monthly_selection_items = relationship(
        "MonthlySelectionItem",
        back_populates="product",
    )

    order_items = relationship("OrderItem", back_populates="product")


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
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "month",
            "year",
            name="uq_monthly_selection_user_cycle",
        ),
    )


class MonthlySelectionItem(Base):
    __tablename__ = "monthly_selection_items"

    id = Column(Integer, primary_key=True, index=True)
    monthly_selection_id = Column(
        Integer,
        ForeignKey("monthly_selections.id"),
        nullable=False,
    )
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, default=1, nullable=False)

    monthly_selection = relationship(
        "MonthlySelection",
        back_populates="items",
    )

    product = relationship(
        "Product",
        back_populates="monthly_selection_items",
    )

    __table_args__ = (
        UniqueConstraint(
            "monthly_selection_id",
            "product_id",
            name="uq_monthly_selection_item_product",
        ),
    )


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
        back_populates="change_requests_current",
    )

    requested_plan = relationship(
        "Plan",
        foreign_keys=[requested_plan_id],
        back_populates="change_requests_requested",
    )


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


class Commission(Base):
    __tablename__ = "commissions"

    id = Column(Integer, primary_key=True, index=True)

    ambassador_id = Column(Integer, ForeignKey("ambassadors.id"), nullable=False)
    referred_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)

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

    ambassador = relationship("Ambassador", back_populates="commissions")

    referred_user = relationship(
        "User",
        foreign_keys=[referred_user_id],
        back_populates="commissions_as_referred",
    )

    plan = relationship("Plan", back_populates="commissions")

    __table_args__ = (
        UniqueConstraint(
            "ambassador_id",
            "referred_user_id",
            "month",
            "year",
            name="uq_commission_monthly",
        ),
    )


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

    carrier = Column(String, nullable=True)
    tracking_number = Column(String, nullable=True, index=True)
    tracking_url = Column(String, nullable=True)
    shipping_notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    prepared_at = Column(DateTime, nullable=True)
    shipped_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    shipping_batch_date = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="orders")

    items = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
    )

    payments = relationship("MembershipPayment", back_populates="order")

    tracking_history = relationship(
        "OrderTrackingHistory",
        back_populates="order",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "month", "year", name="uq_order_user_cycle"),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)

    product_name_snapshot = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False, default=1)

    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")


class OrderTrackingHistory(Base):
    __tablename__ = "order_tracking_history"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)

    status = Column(String, nullable=False)
    note = Column(Text, nullable=True)

    carrier = Column(String, nullable=True)
    tracking_number = Column(String, nullable=True)
    tracking_url = Column(String, nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order", back_populates="tracking_history")
    creator = relationship("User", foreign_keys=[created_by])


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
        back_populates="payments",
    )

    order = relationship("Order", back_populates="payments")

    admin_verifier = relationship(
        "User",
        foreign_keys=[admin_verified_by],
    )


class PasswordResetCode(Base):
    __tablename__ = "password_reset_codes"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    email = Column(String, nullable=False, index=True)

    code = Column(String, nullable=False)
    used = Column(Boolean, default=False, nullable=False)

    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", foreign_keys=[user_id])


class MarketingCampaign(Base):
    __tablename__ = "marketing_campaigns"

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String, nullable=False)
    subject = Column(String, nullable=True)
    message = Column(Text, nullable=False)
    image_url = Column(Text, nullable=True)

    channel = Column(String, nullable=False, default="email")
    target_group = Column(String, nullable=False, default="members")

    status = Column(String, nullable=False, default="draft")

    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    scheduled_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)

    creator = relationship(
        "User",
        foreign_keys=[created_by],
        back_populates="marketing_campaigns_created",
    )

    recipients = relationship(
        "MarketingCampaignRecipient",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )

    events = relationship(
        "MarketingEvent",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )


class MarketingCampaignRecipient(Base):
    __tablename__ = "marketing_campaign_recipients"

    id = Column(Integer, primary_key=True, index=True)

    campaign_id = Column(
        Integer,
        ForeignKey("marketing_campaigns.id"),
        nullable=False,
    )

    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    name_snapshot = Column(String, nullable=True)
    email_snapshot = Column(String, nullable=True)
    phone_snapshot = Column(String, nullable=True)
    role_snapshot = Column(String, nullable=True)

    delivery_status = Column(String, nullable=False, default="pending")

    sent_at = Column(DateTime, nullable=True)
    opened_at = Column(DateTime, nullable=True)
    clicked_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True)

    error_message = Column(Text, nullable=True)

    campaign = relationship(
        "MarketingCampaign",
        back_populates="recipients",
    )

    user = relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="marketing_recipients",
    )

    events = relationship(
        "MarketingEvent",
        back_populates="recipient",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "campaign_id",
            "user_id",
            name="uq_marketing_campaign_user",
        ),
    )


class MarketingEvent(Base):
    __tablename__ = "marketing_events"

    id = Column(Integer, primary_key=True, index=True)

    campaign_id = Column(
        Integer,
        ForeignKey("marketing_campaigns.id"),
        nullable=False,
    )

    recipient_id = Column(
        Integer,
        ForeignKey("marketing_campaign_recipients.id"),
        nullable=True,
    )

    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    event_type = Column(String, nullable=False)
    channel = Column(String, nullable=False)

    event_metadata = Column("metadata", Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    campaign = relationship(
        "MarketingCampaign",
        back_populates="events",
    )

    recipient = relationship(
        "MarketingCampaignRecipient",
        foreign_keys=[recipient_id],
        back_populates="events",
    )

    user = relationship(
        "User",
        foreign_keys=[user_id],
    )


class PushNotificationToken(Base):
    __tablename__ = "push_notification_tokens"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )

    token = Column(Text, nullable=False, unique=True)

    platform = Column(String, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    user = relationship(
        "User",
        back_populates="push_tokens",
    )
