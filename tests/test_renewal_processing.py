import unittest
from datetime import datetime

from database import Base, engine, SessionLocal
import models
from renewal_processing import reconcile_subscription_renewal


class SubscriptionRenewalProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    def setUp(self):
        self.db = SessionLocal()
        for model in (
            models.OrderTrackingHistory,
            models.OrderItem,
            models.MembershipPayment,
            models.Order,
            models.Commission,
            models.MonthlySelectionItem,
            models.MonthlySelection,
            models.AmbassadorReferral,
            models.Ambassador,
            models.Product,
            models.Plan,
            models.User,
        ):
            self.db.query(model).delete()
        self.db.commit()

    def tearDown(self):
        self.db.close()

    @staticmethod
    def _user(email, cedula, role, level=None):
        return models.User(
            name=email.split("@")[0],
            email=email,
            password="hashed",
            phone="0999999999",
            cedula=cedula,
            city="Quito",
            address="Dirección de prueba",
            reference="Referencia",
            delivery_notes="Entregar una sola vez",
            status="active",
            membership_level=level,
            membership_active=True,
            is_active=True,
            role=role,
        )

    def test_paid_renewal_closes_order_commission_and_next_cycle_once(self):
        ambassador_user = self._user(
            "ambassador@example.com", "AMB-CED", "ambassador"
        )
        member = self._user("member@example.com", "MEM-CED", "member", 1)
        plan = models.Plan(name="Nivel 1 - Cobre", level=1, price=42.0, active=True)
        product = models.Product(name="Producto mensual", price=10.0, active=True)
        self.db.add_all([ambassador_user, member, plan, product])
        self.db.flush()

        ambassador = models.Ambassador(
            user_id=ambassador_user.id,
            ambassador_code="EMB-000002",
            ambassador_token="token-test",
            national_id="AMB-CED",
            address="Dirección embajador",
            status="active",
            is_active=True,
        )
        self.db.add(ambassador)
        self.db.flush()
        self.db.add(
            models.AmbassadorReferral(
                ambassador_id=ambassador.id,
                user_id=member.id,
                referral_code="EMB-000002",
                status="active",
            )
        )

        august = models.MonthlySelection(
            user_id=member.id,
            plan_id=plan.id,
            month=8,
            year=2026,
            status="confirmed",
            editable=False,
        )
        self.db.add(august)
        self.db.flush()
        self.db.add(
            models.MonthlySelectionItem(
                monthly_selection_id=august.id,
                product_id=product.id,
                quantity=1,
            )
        )

        initial = models.MembershipPayment(
            user_id=member.id,
            monthly_selection_id=august.id,
            payment_type="subscription",
            provider="paypal",
            paypal_order_id="I-INITIAL",
            amount=42.0,
            currency="USD",
            status="subscription_active",
        )
        renewal = models.MembershipPayment(
            user_id=member.id,
            monthly_selection_id=august.id,
            payment_type="subscription_renewal",
            provider="paypal",
            paypal_order_id="SALE-RENEWAL-1",
            amount=42.0,
            currency="USD",
            status="subscription_paid",
            paid_at=datetime(2026, 8, 5, 14, 6, 9),
        )
        self.db.add_all([initial, renewal])
        self.db.commit()

        first = reconcile_subscription_renewal(self.db, renewal)
        second = reconcile_subscription_renewal(self.db, renewal)

        self.assertTrue(first["processed"])
        self.assertEqual((first["cycle_month"], first["cycle_year"]), (9, 2026))
        self.assertTrue(first["logistics_ready"])
        self.assertEqual(first["commission_created"], 1)
        self.assertTrue(second["processed"])
        self.assertEqual(second["commission_created"], 0)
        self.assertEqual(self.db.query(models.Order).count(), 1)
        self.assertEqual(self.db.query(models.Commission).count(), 1)

        commission = self.db.query(models.Commission).one()
        self.assertEqual(commission.commission_amount, 5.0)
        self.assertEqual((commission.month, commission.year), (9, 2026))
        self.assertEqual(renewal.order.status, "approved_for_logistics")
        self.assertTrue(renewal.admin_verified)

        october = (
            self.db.query(models.MonthlySelection)
            .filter(
                models.MonthlySelection.user_id == member.id,
                models.MonthlySelection.month == 10,
                models.MonthlySelection.year == 2026,
            )
            .one()
        )
        self.assertTrue(october.editable)


if __name__ == "__main__":
    unittest.main()
