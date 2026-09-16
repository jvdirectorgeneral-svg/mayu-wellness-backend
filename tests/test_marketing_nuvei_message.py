import unittest
from datetime import datetime
from types import SimpleNamespace

import marketing
from models import AmbassadorReferral, NuveiMembershipCard


class _Query:
    def __init__(self, result):
        self.result = result

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.result


class _Db:
    def query(self, model):
        if model is AmbassadorReferral:
            return _Query(None)
        if model is NuveiMembershipCard:
            return _Query(SimpleNamespace(next_debit_at=datetime(2026, 9, 18)))
        raise AssertionError(f"Consulta inesperada: {model}")


class MarketingNuveiMessageTests(unittest.TestCase):
    def test_nuvei_renewal_uses_real_provider_and_next_date(self):
        user = SimpleNamespace(
            id=51,
            name="Socio prueba",
            email="socio@example.com",
            phone="0999999999",
            membership_level=1,
            membership_active=True,
        )
        payment = SimpleNamespace(
            id=127,
            payment_type="subscription_renewal",
            provider="nuvei",
            status="subscription_paid",
            amount=40,
            currency="USD",
            paypal_order_id="NUVEI-TX-127",
            payment_reference="MWC-NUVEI-51",
            paid_at=datetime(2026, 9, 16),
            created_at=datetime(2026, 9, 16),
        )

        message = marketing.build_admin_member_payment_message(
            _Db(), user, payment=payment, event_label="Débito mensual confirmado por Nuvei"
        )

        self.assertIn("Transacción Nuvei: NUVEI-TX-127", message)
        self.assertIn("Detalle Nuvei mensual:", message)
        self.assertIn("Próximo débito programado: $40.00 el 2026-09-18", message)
        self.assertIn("Estado operativo: Nuvei confirmado, socio activo", message)
        self.assertNotIn("PayPal", message)


if __name__ == "__main__":
    unittest.main()
