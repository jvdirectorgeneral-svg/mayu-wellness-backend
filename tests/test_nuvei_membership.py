import base64
import hashlib
import os
import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

import nuvei_membership


class FakeCard:
    raw_payload = None
    next_debit_at = None
    last_debit_at = None
    failed_attempts = 0


class NuveiMembershipTests(unittest.TestCase):
    def test_auth_token_uses_server_credentials(self):
        with patch.dict(
            os.environ,
            {
                "NUVEI_SERVER_APP_CODE": "SERVER-CODE",
                "NUVEI_SERVER_APP_KEY": "server-secret",
            },
            clear=False,
        ), patch.object(nuvei_membership.time, "time", return_value=1700000000):
            decoded = base64.b64decode(nuvei_membership.auth_token()).decode("utf-8")
        digest = hashlib.sha256(b"server-secret1700000000").hexdigest()
        self.assertEqual(decoded, f"SERVER-CODE;1700000000;{digest}")

    def test_success_requires_nuvei_status_and_detail_three(self):
        self.assertTrue(
            nuvei_membership.is_nuvei_success(
                {"transaction": {"status": "success", "status_detail": 3}}
            )
        )
        self.assertFalse(
            nuvei_membership.is_nuvei_success(
                {"transaction": {"status": "success", "status_detail": 35}}
            )
        )
        self.assertFalse(
            nuvei_membership.is_nuvei_success(
                {"transaction": {"status": "failure", "status_detail": 3}}
            )
        )

    def test_webhook_signature_is_validated_and_fails_closed(self):
        user_id = "42"
        transaction_id = "NUVEI-TX-1"
        app_code = "SERVER-CODE"
        app_key = "server-secret"
        stoken = hashlib.md5(
            f"{transaction_id}_{app_code}_{user_id}_{app_key}".encode("utf-8")
        ).hexdigest()
        transaction = {
            "id": transaction_id,
            "application_code": app_code,
            "stoken": stoken,
        }
        with patch.dict(
            os.environ,
            {
                "NUVEI_SERVER_APP_CODE": app_code,
                "NUVEI_SERVER_APP_KEY": app_key,
            },
            clear=False,
        ):
            self.assertIsNone(
                nuvei_membership.validate_webhook_signature(transaction, user_id)
            )
            with self.assertRaises(HTTPException) as raised:
                nuvei_membership.validate_webhook_signature(
                    {**transaction, "stoken": "incorrect"}, user_id
                )
        self.assertEqual(raised.exception.status_code, 203)

    def test_success_advances_schedule_and_failure_retries_tomorrow(self):
        card = FakeCard()
        charged_at = datetime(2026, 8, 19, 12, 0, 0)
        with patch.dict(os.environ, {"NUVEI_MODE": "sandbox"}, clear=False), patch.dict(
            os.environ, {"NUVEI_SANDBOX_RENEWAL_INTERVAL_DAYS": ""}, clear=False
        ):
            nuvei_membership.advance_card_after_success(card, charged_at)
        self.assertEqual(card.last_debit_at, charged_at)
        self.assertEqual(card.next_debit_at.date().isoformat(), "2026-09-19")
        self.assertEqual(card.failed_attempts, 0)

        nuvei_membership.schedule_card_retry(card)
        self.assertEqual(card.failed_attempts, 1)
        self.assertGreater(card.next_debit_at, charged_at)

    def test_all_three_membership_plan_prices(self):
        self.assertEqual(nuvei_membership.MONTHLY_PRICES, {1: 40.0, 2: 50.0, 3: 60.0})
        for level, expected in ((1, 40.0), (2, 50.0), (3, 60.0)):
            user = type("FakeUser", (), {"membership_level": level})()
            self.assertEqual(nuvei_membership.monthly_amount_for_user(user), expected)

    def test_membership_debit_rejects_surcharges(self):
        for level, amount in ((1, 44.80), (2, 56.00), (3, 67.20), (3, 62.00)):
            user = type("FakeUser", (), {"id": 1, "membership_level": level})()
            with self.assertRaises(HTTPException) as raised:
                nuvei_membership.run_nuvei_debit(
                    MagicMock(), user, FakeCard(), 9, 2026, amount=amount, force=True
                )
            self.assertEqual(raised.exception.status_code, 400)

    def test_old_accelerated_date_cannot_charge_before_next_month(self):
        card = FakeCard()
        card.last_debit_at = datetime(2026, 8, 31, 12, 0, 0)
        self.assertEqual(
            nuvei_membership.monthly_due_date(card, date(2026, 9, 2)),
            date(2026, 9, 30),
        )
        self.assertEqual(
            nuvei_membership.monthly_due_date(card, date(2026, 10, 1)),
            date(2026, 10, 1),
        )

    def test_sandbox_and_live_renew_monthly_despite_old_two_day_setting(self):
        charged_at = datetime(2026, 8, 20, 15, 0, 0)
        sandbox_card = FakeCard()
        with patch.dict(
            os.environ,
            {
                "NUVEI_MODE": "sandbox",
                "NUVEI_SANDBOX_RENEWAL_INTERVAL_DAYS": "2",
            },
            clear=False,
        ):
            nuvei_membership.advance_card_after_success(sandbox_card, charged_at)
        self.assertEqual(sandbox_card.next_debit_at.date().isoformat(), "2026-09-20")

        live_card = FakeCard()
        with patch.dict(
            os.environ,
            {
                "NUVEI_MODE": "live",
                "NUVEI_SANDBOX_RENEWAL_INTERVAL_DAYS": "2",
            },
            clear=False,
        ):
            nuvei_membership.advance_card_after_success(live_card, charged_at)
        self.assertEqual(live_card.next_debit_at.date().isoformat(), "2026-09-20")

    def test_successful_renewal_uses_shared_operational_circuit(self):
        db = MagicMock()
        user = type(
            "FakeUser",
            (),
            {
                "id": 42,
                "email": "socio@example.com",
                "phone": "0999999999",
                "membership_level": 1,
            },
        )()
        card = type(
            "FakeCard",
            (),
            {
                "id": 7,
                "token": "card-token",
                "raw_payload": None,
                "next_debit_at": None,
                "last_debit_at": None,
                "failed_attempts": 0,
            },
        )()
        payment = type(
            "FakePayment",
            (),
            {
                "id": 99,
                "payment_type": "subscription_renewal",
                "order_id": 123,
            },
        )()
        response = {
            "transaction": {
                "id": "NUVEI-TX-99",
                "status": "success",
                "status_detail": 3,
                "amount": 42.0,
            }
        }

        with patch.object(nuvei_membership, "nuvei_request", return_value=response), patch.object(
            nuvei_membership, "create_payment_from_success", return_value=payment
        ), patch.object(
            nuvei_membership,
            "reconcile_subscription_renewal",
            return_value={"processed": True, "order_id": 123},
        ) as reconcile, patch.object(
            nuvei_membership,
            "notify_admin_member_payment_event",
            return_value={"sent": True},
        ):
            result = nuvei_membership.run_nuvei_debit(
                db=db,
                user=user,
                card=card,
                month=8,
                year=2026,
                force=True,
            )

        reconcile.assert_called_once_with(db=db, payment=payment, sync_wallet=True)
        self.assertTrue(result["success"])
        self.assertTrue(result["renewal_processing"]["processed"])


if __name__ == "__main__":
    unittest.main()
