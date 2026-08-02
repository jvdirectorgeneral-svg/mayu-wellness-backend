import asyncio
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import paypal_subscriptions


class FakeRequest:
    def __init__(self, headers):
        self.headers = headers


SIGNED_HEADERS = {
    "paypal-auth-algo": "SHA256withRSA",
    "paypal-cert-url": "https://api-m.sandbox.paypal.com/cert.pem",
    "paypal-transmission-id": "test-transmission",
    "paypal-transmission-sig": "test-signature",
    "paypal-transmission-time": "2026-08-02T12:00:00Z",
}


class PayPalWebhookVerificationTests(unittest.TestCase):
    def run_verification(self, headers=SIGNED_HEADERS):
        return asyncio.run(
            paypal_subscriptions.verify_paypal_webhook_signature(
                FakeRequest(headers),
                {"id": "WH-TEST", "event_type": "PAYMENT.SALE.COMPLETED"},
            )
        )

    def test_accepts_signature_confirmed_by_paypal(self):
        with patch.dict(
            os.environ,
            {"PAYPAL_SUBSCRIPTIONS_WEBHOOK_ID": "WH-ID"},
            clear=False,
        ), patch.object(
            paypal_subscriptions, "get_token", return_value="token"
        ), patch.object(
            paypal_subscriptions,
            "paypal_request",
            return_value={"verification_status": "SUCCESS"},
        ) as paypal_request:
            self.assertIsNone(self.run_verification())
            payload = paypal_request.call_args.args[3]
            self.assertEqual(payload["webhook_id"], "WH-ID")
            self.assertEqual(payload["transmission_id"], "test-transmission")

    def test_rejects_invalid_signature(self):
        with patch.dict(
            os.environ,
            {"PAYPAL_SUBSCRIPTIONS_WEBHOOK_ID": "WH-ID"},
            clear=False,
        ), patch.object(
            paypal_subscriptions, "get_token", return_value="token"
        ), patch.object(
            paypal_subscriptions,
            "paypal_request",
            return_value={"verification_status": "FAILURE"},
        ):
            with self.assertRaises(HTTPException) as raised:
                self.run_verification()
            self.assertEqual(raised.exception.status_code, 401)

    def test_rejects_missing_signature_headers(self):
        with patch.dict(
            os.environ,
            {"PAYPAL_SUBSCRIPTIONS_WEBHOOK_ID": "WH-ID"},
            clear=False,
        ):
            with self.assertRaises(HTTPException) as raised:
                self.run_verification({})
            self.assertEqual(raised.exception.status_code, 400)

    def test_fails_closed_without_webhook_id(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PAYPAL_SUBSCRIPTIONS_WEBHOOK_ID", None)
            with self.assertRaises(HTTPException) as raised:
                self.run_verification()
            self.assertEqual(raised.exception.status_code, 503)


class PayPalAdminAccessTests(unittest.TestCase):
    class User:
        def __init__(self, role):
            self.role = role

    def test_allows_admin_and_superadmin(self):
        self.assertIsNone(paypal_subscriptions.require_admin(self.User("admin")))
        self.assertIsNone(
            paypal_subscriptions.require_admin(self.User("superadmin"))
        )

    def test_rejects_non_admin(self):
        with self.assertRaises(HTTPException) as raised:
            paypal_subscriptions.require_admin(self.User("member"))
        self.assertEqual(raised.exception.status_code, 403)


class PayPalSandboxTestPlanTests(unittest.TestCase):
    def test_test_plan_endpoint_is_hidden_in_live_mode(self):
        payload = paypal_subscriptions.CreateSandboxTestPlanRequest(
            setup_token="test-token"
        )
        with patch.dict(
            os.environ,
            {"PAYPAL_SUBSCRIPTIONS_MODE": "live"},
            clear=False,
        ):
            with self.assertRaises(HTTPException) as raised:
                paypal_subscriptions.create_sandbox_test_plan(payload)
        self.assertEqual(raised.exception.status_code, 404)

    def test_test_plan_endpoint_rejects_invalid_token(self):
        payload = paypal_subscriptions.CreateSandboxTestPlanRequest(
            setup_token="wrong-token"
        )
        with patch.dict(
            os.environ,
            {
                "PAYPAL_SUBSCRIPTIONS_MODE": "sandbox",
                "PAYPAL_SUBSCRIPTIONS_TEST_SETUP_TOKEN": "expected-token",
            },
            clear=False,
        ):
            with self.assertRaises(HTTPException) as raised:
                paypal_subscriptions.create_sandbox_test_plan(payload)
        self.assertEqual(raised.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
