import base64
import hashlib
import os
import unittest
from datetime import datetime
from unittest.mock import patch

from fastapi import HTTPException

import nuvei_membership


class FakeCard:
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
        nuvei_membership.advance_card_after_success(card, charged_at)
        self.assertEqual(card.last_debit_at, charged_at)
        self.assertEqual(card.next_debit_at.date().isoformat(), "2026-09-19")
        self.assertEqual(card.failed_attempts, 0)

        nuvei_membership.schedule_card_retry(card)
        self.assertEqual(card.failed_attempts, 1)
        self.assertGreater(card.next_debit_at, charged_at)


if __name__ == "__main__":
    unittest.main()
