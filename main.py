from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from database import engine, Base, SessionLocal
from sqlalchemy import text
from renewal_processing import reconcile_all_paid_subscription_renewals

from users import router as users_router
from products import router as products_router
from plans import router as plans_router
from plan_products import router as plan_products_router
from plan_selection import router as plan_selection_router
from monthly_selection import router as monthly_selection_router
from plan_change import router as plan_change_router
from member_cards import router as member_cards_router
from ambassadors import router as ambassadors_router
from commissions import router as commissions_router
from admin_dashboard import router as admin_dashboard_router
from supervisor_dashboard import router as supervisor_dashboard_router
from orders import router as orders_router
from payments_paypal import router as payments_paypal_router
from paypal_subscriptions import router as paypal_subscriptions_router
from superadmin import router as superadmin_router
from marketing import router as marketing_router
from marketing_uploads import router as marketing_uploads_router
from marketplace import router as marketplace_router
from education import router as education_router
from education_orders import router as education_orders_router
from routers.payphone import router as payphone_router
from marketplace_paypal import router as marketplace_paypal_router
from pharmacy_loyalty import router as pharmacy_loyalty_router
from doctor_prescribers import router as doctor_prescribers_router

from dependencies import get_current_user
import models


Base.metadata.create_all(bind=engine)

# Small, idempotent compatibility migration for installations that created the
# marketing tables before CRM fields were added.
with engine.begin() as connection:
    for statement in (
        "ALTER TABLE marketing_contacts ADD COLUMN IF NOT EXISTS city VARCHAR",
        "ALTER TABLE marketing_contacts ADD COLUMN IF NOT EXISTS birth_date TIMESTAMP",
        "ALTER TABLE marketing_contacts ADD COLUMN IF NOT EXISTS email_status VARCHAR NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE marketing_contacts ADD COLUMN IF NOT EXISTS bounce_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE marketing_contacts ADD COLUMN IF NOT EXISTS complaint_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE marketing_contacts ADD COLUMN IF NOT EXISTS last_email_event_at TIMESTAMP",
        "ALTER TABLE marketing_contacts ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP",
        "ALTER TABLE marketing_campaigns ADD COLUMN IF NOT EXISTS audience_tag VARCHAR",
    ):
        connection.execute(text(statement))


app = FastAPI(
    title="Mayu Wellness API",
    version="1.0.0",
)


@app.on_event("startup")
def reconcile_existing_subscription_renewals_on_startup():
    """Repair paid renewals received before automatic downstream processing existed."""
    db = SessionLocal()
    try:
        result = reconcile_all_paid_subscription_renewals(db)
        print(
            "subscription renewal reconciliation:",
            {
                "checked": result["checked"],
                "processed": result["processed"],
                # Contains only provider status/counts (never wallet tokens).
                # This makes missing device registrations and APNs failures
                # visible in Render without exposing private identifiers.
                "wallet_sync": result.get("wallet_sync", {}),
            },
            flush=True,
        )
    except Exception as exc:
        db.rollback()
        print("subscription renewal reconciliation failed:", str(exc))
    finally:
        db.close()


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://mayuclub.com",
        "https://www.mayuclub.com",
        "https://mayuwellnesclub.com",
        "https://www.mayuwellnesclub.com",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(users_router)
app.include_router(products_router)
app.include_router(plans_router)
app.include_router(plan_products_router)
app.include_router(plan_selection_router)
app.include_router(monthly_selection_router)
app.include_router(plan_change_router)
app.include_router(member_cards_router)
app.include_router(ambassadors_router)
app.include_router(commissions_router)
app.include_router(admin_dashboard_router)
app.include_router(supervisor_dashboard_router)
app.include_router(orders_router)
app.include_router(payments_paypal_router)
app.include_router(paypal_subscriptions_router)
app.include_router(superadmin_router)
app.include_router(marketing_router)
app.include_router(marketing_uploads_router)
app.include_router(marketplace_router)
app.include_router(education_router)
app.include_router(education_orders_router)
app.include_router(payphone_router)
app.include_router(marketplace_paypal_router)
app.include_router(pharmacy_loyalty_router)
app.include_router(doctor_prescribers_router)


@app.get("/")
def read_root():
    return {"message": "Mayu Wellness Backend funcionando 🚀"}


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/me")
def get_me(current_user: models.User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "phone": current_user.phone,
        "cedula": current_user.cedula,
        "city": current_user.city,
        "address": current_user.address,
        "reference": current_user.reference,
        "delivery_notes": current_user.delivery_notes,
        "phone_secondary": current_user.phone_secondary,
        "membership_level": current_user.membership_level,
        "membership_active": current_user.membership_active,
        "role": current_user.role,
        "is_active": current_user.is_active,
        "status": current_user.status,
    }
