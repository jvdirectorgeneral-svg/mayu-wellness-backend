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
    JSON,
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
    birth_date = Column(DateTime, nullable=True)
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

    marketplace_products_created = relationship(
        "MarketplaceProduct",
        foreign_keys="MarketplaceProduct.created_by",
        back_populates="creator",
    )

    marketplace_orders = relationship(
        "MarketplaceOrder",
        foreign_keys="MarketplaceOrder.user_id",
        back_populates="user",
    )

    education_resources_created = relationship(
        "EducationResource",
        foreign_keys="EducationResource.created_by",
        back_populates="creator",
    )

    education_orders = relationship(
        "EducationOrder",
        back_populates="user",
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

    payments = relationship(
        "MembershipPayment",
        back_populates="monthly_selection",
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


class PharmacyCustomer(Base):
    __tablename__ = "pharmacy_customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    password = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    cedula = Column(String, unique=True, nullable=True, index=True)
    birth_date = Column(DateTime, nullable=True)
    city = Column(String, nullable=True)
    address = Column(String, nullable=True)
    reference = Column(String, nullable=True)
    delivery_notes = Column(Text, nullable=True)
    accepted_terms = Column(Boolean, nullable=False, default=False)
    accepted_privacy_policy = Column(Boolean, nullable=False, default=False)
    accepted_digital_policy = Column(Boolean, nullable=False, default=False)
    accepted_terms_at = Column(DateTime, nullable=True)
    accepted_privacy_policy_at = Column(DateTime, nullable=True)
    accepted_digital_policy_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    loyalty_card = relationship(
        "PharmacyLoyaltyCard",
        back_populates="pharmacy_customer",
        uselist=False,
        cascade="all, delete-orphan",
    )
    push_tokens = relationship(
        "PharmacyPushNotificationToken",
        back_populates="pharmacy_customer",
        cascade="all, delete-orphan",
    )


class PharmacyPushNotificationToken(Base):
    __tablename__ = "pharmacy_push_notification_tokens"

    id = Column(Integer, primary_key=True, index=True)
    pharmacy_customer_id = Column(
        Integer,
        ForeignKey("pharmacy_customers.id"),
        nullable=False,
        index=True,
    )
    token = Column(Text, nullable=False, unique=True)
    platform = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    birthday_last_sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    pharmacy_customer = relationship(
        "PharmacyCustomer",
        back_populates="push_tokens",
    )


class PharmacyLoyaltyCard(Base):
    __tablename__ = "pharmacy_loyalty_cards"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, unique=True)
    pharmacy_customer_id = Column(
        Integer,
        ForeignKey("pharmacy_customers.id"),
        nullable=True,
        unique=True,
    )
    card_code = Column(String, unique=True, nullable=False, index=True)
    qr_token = Column(String, unique=True, nullable=False, index=True)
    points_balance = Column(Integer, nullable=False, default=0)
    accumulated_cents = Column(Integer, nullable=False, default=0)
    lifetime_points = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    user = relationship("User")
    pharmacy_customer = relationship(
        "PharmacyCustomer",
        back_populates="loyalty_card",
    )
    transactions = relationship(
        "PharmacyPointsTransaction",
        back_populates="card",
        cascade="all, delete-orphan",
        order_by="PharmacyPointsTransaction.created_at.desc()",
    )
    apple_wallet_registrations = relationship(
        "PharmacyAppleWalletRegistration",
        back_populates="card",
        cascade="all, delete-orphan",
    )


class PharmacyAppleWalletRegistration(Base):
    __tablename__ = "pharmacy_apple_wallet_registrations"

    id = Column(Integer, primary_key=True, index=True)
    card_id = Column(
        Integer,
        ForeignKey("pharmacy_loyalty_cards.id"),
        nullable=False,
        index=True,
    )
    device_library_identifier = Column(String, nullable=False, index=True)
    pass_type_identifier = Column(String, nullable=False, index=True)
    serial_number = Column(String, nullable=False, index=True)
    push_token = Column(Text, nullable=False)
    authentication_token = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    card = relationship(
        "PharmacyLoyaltyCard",
        back_populates="apple_wallet_registrations",
    )


class PharmacyPointsTransaction(Base):
    __tablename__ = "pharmacy_points_transactions"

    id = Column(Integer, primary_key=True, index=True)
    card_id = Column(
        Integer,
        ForeignKey("pharmacy_loyalty_cards.id"),
        nullable=False,
        index=True,
    )
    marketplace_order_id = Column(
        Integer,
        ForeignKey("marketplace_orders.id"),
        nullable=True,
        unique=True,
    )
    purchase_amount_cents = Column(Integer, nullable=False)
    points_delta = Column(Integer, nullable=False)
    remainder_after_cents = Column(Integer, nullable=False)
    source = Column(String, nullable=False)
    reference = Column(String, nullable=True, unique=True)
    note = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    card = relationship("PharmacyLoyaltyCard", back_populates="transactions")
    creator = relationship("User", foreign_keys=[created_by])
    marketplace_order = relationship("MarketplaceOrder")


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

    monthly_selection_id = Column(
        Integer,
        ForeignKey("monthly_selections.id"),
        nullable=True,
        index=True,
    )

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

    monthly_selection = relationship("MonthlySelection")

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

    monthly_selection_id = Column(
        Integer,
        ForeignKey("monthly_selections.id"),
        nullable=True,
        index=True,
    )

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

    monthly_selection = relationship(
        "MonthlySelection",
        back_populates="payments",
    )

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


class MarketplaceCategory(Base):
    __tablename__ = "marketplace_categories"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String, nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)

    active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    products = relationship(
        "MarketplaceProduct",
        back_populates="category_rel",
    )


class MarketplaceProduct(Base):
    __tablename__ = "marketplace_products"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String, nullable=False, index=True)
    category_id = Column(
        Integer,
        ForeignKey("marketplace_categories.id"),
        nullable=True,
    )

    price = Column(Float, nullable=False)
    stock = Column(Integer, default=0, nullable=False)

    image_url = Column(Text, nullable=True)
    video_url = Column(Text, nullable=True)

    short_description = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    benefits = Column(Text, nullable=True)
    ingredients = Column(Text, nullable=True)
    suggested_dose = Column(Text, nullable=True)
    usage_instructions = Column(Text, nullable=True)
    warnings = Column(Text, nullable=True)

    presentation = Column(String, nullable=True)

    active = Column(Boolean, default=True, nullable=False)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    category_rel = relationship(
        "MarketplaceCategory",
        back_populates="products",
    )

    creator = relationship(
        "User",
        foreign_keys=[created_by],
        back_populates="marketplace_products_created",
    )

    order_items = relationship(
        "MarketplaceOrderItem",
        back_populates="product",
    )


class MarketplaceOrder(Base):
    __tablename__ = "marketplace_orders"

    id = Column(Integer, primary_key=True, index=True)

    order_code = Column(String, unique=True, nullable=False, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    customer_name = Column(String, nullable=False)
    customer_phone = Column(String, nullable=False)
    customer_email = Column(String, nullable=True)

    city = Column(String, nullable=True)
    address = Column(Text, nullable=True)
    delivery_notes = Column(Text, nullable=True)

    billing_name = Column(String, nullable=True)
    billing_identification = Column(String, nullable=True)
    billing_email = Column(String, nullable=True)
    billing_phone = Column(String, nullable=True)
    billing_address = Column(Text, nullable=True)

    subtotal = Column(Float, nullable=False, default=0)

    discount_code = Column(String, nullable=True)
    pharmacy_loyalty_identifier = Column(String, nullable=True, index=True)
    discount_percent = Column(Float, nullable=False, default=0)
    discount_amount = Column(Float, nullable=False, default=0)

    total = Column(Float, nullable=False, default=0)
    currency = Column(String, nullable=False, default="USD")

    payment_method = Column(String, nullable=False, default="whatsapp")
    payment_status = Column(String, nullable=False, default="pending")
    status = Column(String, nullable=False, default="created")

    whatsapp_message = Column(Text, nullable=True)

    payphone_transaction_id = Column(String, nullable=True, index=True)
    payphone_payment_url = Column(Text, nullable=True)
    raw_payment_payload = Column(Text, nullable=True)

    admin_verified = Column(Boolean, default=False, nullable=False)
    admin_verified_at = Column(DateTime, nullable=True)
    admin_verified_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    carrier = Column(String, nullable=True)
    tracking_number = Column(String, nullable=True, index=True)
    tracking_url = Column(Text, nullable=True)
    shipping_notes = Column(Text, nullable=True)

    approved_at = Column(DateTime, nullable=True)
    prepared_at = Column(DateTime, nullable=True)
    shipped_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)

    user = relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="marketplace_orders",
    )

    admin_verifier = relationship(
        "User",
        foreign_keys=[admin_verified_by],
    )

    items = relationship(
        "MarketplaceOrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
    )

    tracking_history = relationship(
        "MarketplaceOrderTrackingHistory",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="MarketplaceOrderTrackingHistory.created_at",
    )


class MarketplaceOrderItem(Base):
    __tablename__ = "marketplace_order_items"

    id = Column(Integer, primary_key=True, index=True)

    order_id = Column(
        Integer,
        ForeignKey("marketplace_orders.id"),
        nullable=False,
    )

    product_id = Column(
        Integer,
        ForeignKey("marketplace_products.id"),
        nullable=False,
    )

    product_name_snapshot = Column(String, nullable=False)
    unit_price_snapshot = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    total_snapshot = Column(Float, nullable=False)

    order = relationship(
        "MarketplaceOrder",
        back_populates="items",
    )

    product = relationship(
        "MarketplaceProduct",
        back_populates="order_items",
    )


class MarketplaceOrderTrackingHistory(Base):
    __tablename__ = "marketplace_order_tracking_history"

    id = Column(Integer, primary_key=True, index=True)
    marketplace_order_id = Column(
        Integer,
        ForeignKey("marketplace_orders.id"),
        nullable=False,
        index=True,
    )
    status = Column(String, nullable=False, index=True)
    note = Column(Text, nullable=True)
    carrier = Column(String, nullable=True)
    tracking_number = Column(String, nullable=True)
    tracking_url = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    order = relationship(
        "MarketplaceOrder",
        back_populates="tracking_history",
    )
    creator = relationship(
        "User",
        foreign_keys=[created_by],
    )


class EducationCategory(Base):
    __tablename__ = "education_categories"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String, nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)

    active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    resources = relationship(
        "EducationResource",
        back_populates="category_rel",
    )


class EducationResource(Base):
    __tablename__ = "education_resources"

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String, nullable=False, index=True)

    category_id = Column(
        Integer,
        ForeignKey("education_categories.id"),
        nullable=True,
    )

    resource_type = Column(String, nullable=False, default="pdf")

    file_url = Column(Text, nullable=True)
    external_url = Column(Text, nullable=True)
    cover_image_url = Column(Text, nullable=True)

    price = Column(Float, default=0, nullable=False)

    description = Column(Text, nullable=True)
    content_text = Column(Text, nullable=True)

    plant_common_name = Column(String, nullable=True)
    plant_scientific_name = Column(String, nullable=True)
    plant_family = Column(String, nullable=True)
    plant_origin = Column(String, nullable=True)
    plant_uses = Column(Text, nullable=True)
    plant_parts_used = Column(Text, nullable=True)
    plant_preparation = Column(Text, nullable=True)
    plant_warnings = Column(Text, nullable=True)

    active = Column(Boolean, default=True, nullable=False)
    free_for_members = Column(Boolean, default=True, nullable=False)

    # V7.4 Mayu Educación
    marketplace_only = Column(Boolean, default=False, nullable=False)
    language = Column(String, default="es", nullable=False)
    content_type = Column(String, default="general", nullable=False)

    video_urls = Column(JSON, nullable=True)
    online_files = Column(JSON, nullable=True)
    download_pdf_url = Column(Text, nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    category_rel = relationship(
        "EducationCategory",
        back_populates="resources",
    )

    creator = relationship(
        "User",
        foreign_keys=[created_by],
        back_populates="education_resources_created",
    )

    order_items = relationship(
        "EducationOrderItem",
        back_populates="resource",
    )


class EducationAccessCode(Base):
    __tablename__ = "education_access_codes"

    id = Column(Integer, primary_key=True, index=True)

    resource_id = Column(
        Integer,
        ForeignKey("education_resources.id"),
        nullable=False,
    )

    code = Column(String, unique=True, index=True, nullable=False)

    buyer_name = Column(String, nullable=True)
    buyer_email = Column(String, nullable=True)
    buyer_phone = Column(String, nullable=True)

    max_uses = Column(Integer, default=30, nullable=False)
    uses_count = Column(Integer, default=0, nullable=False)

    status = Column(String, default="active", nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)

    resource = relationship("EducationResource")


class EducationOrder(Base):
    __tablename__ = "education_orders"

    id = Column(Integer, primary_key=True, index=True)

    order_code = Column(String, unique=True, nullable=False, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    buyer_name = Column(String, nullable=False)
    buyer_phone = Column(String, nullable=False)
    buyer_email = Column(String, nullable=False)

    subtotal = Column(Float, nullable=False, default=0)
    total = Column(Float, nullable=False, default=0)
    currency = Column(String, nullable=False, default="USD")

    payment_method = Column(String, nullable=False, default="paypal")
    payment_status = Column(String, nullable=False, default="pending")
    status = Column(String, nullable=False, default="created")

    whatsapp_message = Column(Text, nullable=True)

    payphone_transaction_id = Column(String, nullable=True, index=True)
    payphone_payment_url = Column(Text, nullable=True)
    raw_payment_payload = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)

    user = relationship(
        "User",
        back_populates="education_orders",
    )

    items = relationship(
        "EducationOrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
    )


class EducationOrderItem(Base):
    __tablename__ = "education_order_items"

    id = Column(Integer, primary_key=True, index=True)

    order_id = Column(
        Integer,
        ForeignKey("education_orders.id"),
        nullable=False,
    )

    resource_id = Column(
        Integer,
        ForeignKey("education_resources.id"),
        nullable=False,
    )

    resource_title_snapshot = Column(String, nullable=False)
    resource_type_snapshot = Column(String, nullable=True)

    unit_price_snapshot = Column(Float, nullable=False, default=0)
    quantity = Column(Integer, nullable=False, default=1)
    total_snapshot = Column(Float, nullable=False, default=0)

    access_code = Column(String, nullable=True)

    order = relationship(
        "EducationOrder",
        back_populates="items",
    )

    resource = relationship(
        "EducationResource",
        back_populates="order_items",
    )
