import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.tenants.models import Plan, SubscriptionOrder
from apps.tenants.plans import create_order
from apps.tenants.services import create_company_owner
from .tasks import renew_yookassa_subscriptions
from .yookassa import YooKassaError, apply_payment, _payment_payload


@override_settings(
    DEBUG=True,
    ALLOWED_HOSTS=["testserver", "localhost", ".localhost"],
    TENANT_DOMAIN_SUFFIXES=(".localhost", ".crmdetailing.ru"),
    YOOKASSA_SHOP_ID="1485879",
    YOOKASSA_SECRET_KEY="test-secret",
    YOOKASSA_ENABLED=True,
    YOOKASSA_TEST_MODE=True,
)
class YooKassaTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = create_company_owner(
            company_name="Тест ЮKassa",
            slug="yookassa",
            owner_name="Владелец",
            email="yookassa@example.com",
            password="test-password-123",
        )
        self.plan = Plan.objects.get(pk="start")
        self.host = {"HTTP_HOST": "yookassa.localhost"}
        self.client.force_login(self.owner)

    def payment(self, order, *, status="succeeded", value=None, saved=True):
        return {
            "id": "2f4c9f20-000f-5000-9000-123456789012",
            "status": status,
            "paid": status == "succeeded",
            "amount": {"value": value or f"{order.total:.2f}", "currency": "RUB"},
            "metadata": {
                "subscription_order_id": str(order.pk),
                "tenant_id": str(self.tenant.pk),
            },
            "payment_method": {
                "id": "2f4c9f20-000f-5000-9000-method00001",
                "saved": saved,
            },
        }

    def test_payment_payload_has_no_receipt_and_can_save_method(self):
        order = create_order(
            self.tenant, self.plan, 1, provider="yookassa", auto_renew_requested=True
        )
        payload = _payment_payload(order, return_url="https://yookassa.localhost/return/")
        self.assertNotIn("receipt", payload)
        self.assertTrue(payload["capture"])
        self.assertTrue(payload["save_payment_method"])
        self.assertEqual(payload["metadata"]["subscription_order_id"], str(order.pk))

    def test_successful_payment_activates_once_and_saves_auto_renewal(self):
        order = create_order(
            self.tenant, self.plan, 1, provider="yookassa", auto_renew_requested=True
        )
        self.assertEqual(apply_payment(self.payment(order)), "succeeded")
        order.refresh_from_db()
        subscription = self.tenant.subscription
        subscription.refresh_from_db()
        first_end = subscription.ends_at
        self.assertEqual(order.status, SubscriptionOrder.Status.PAID)
        self.assertTrue(subscription.auto_renew)
        self.assertTrue(subscription.provider_payment_method_id)
        self.assertEqual(apply_payment(self.payment(order)), "succeeded")
        subscription.refresh_from_db()
        self.assertEqual(subscription.ends_at, first_end)

    def test_wrong_amount_does_not_activate_subscription(self):
        order = create_order(self.tenant, self.plan, 1, provider="yookassa")
        with self.assertRaisesMessage(YooKassaError, "Сумма платежа"):
            apply_payment(self.payment(order, value="1.00"))
        order.refresh_from_db()
        self.assertEqual(order.status, SubscriptionOrder.Status.PENDING)

    def test_canceled_payment_cancels_order(self):
        order = create_order(self.tenant, self.plan, 1, provider="yookassa")
        self.assertEqual(apply_payment(self.payment(order, status="canceled")), "canceled")
        order.refresh_from_db()
        self.assertEqual(order.status, SubscriptionOrder.Status.CANCELLED)

    @patch("apps.core.billing.create_checkout_payment", return_value="https://yookassa.test/pay")
    def test_checkout_redirects_to_yookassa(self, create_payment):
        response = self.client.post(
            "/subscription/settings/",
            {"plan": "start", "months": "1", "promo": "", "auto_renew": "on", "action": "order"},
            **self.host,
        )
        self.assertRedirects(response, "https://yookassa.test/pay", fetch_redirect_response=False)
        order = SubscriptionOrder.objects.get(tenant=self.tenant)
        self.assertEqual(order.provider, "yookassa")
        self.assertTrue(order.auto_renew_requested)
        create_payment.assert_called_once()

    @patch("apps.core.billing.sync_payment", return_value="succeeded")
    def test_webhook_uses_provider_verification(self, sync_payment):
        response = self.client.post(
            "/payments/yookassa/webhook/",
            data=json.dumps({
                "event": "payment.succeeded",
                "object": {"id": "2f4c9f20-000f-5000-9000-123456789012"},
            }),
            content_type="application/json",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        sync_payment.assert_called_once_with("2f4c9f20-000f-5000-9000-123456789012")

    @patch("apps.core.yookassa.create_recurring_payment")
    def test_auto_renewal_creates_single_provider_order(self, recurring_payment):
        subscription = self.tenant.subscription
        subscription.status = subscription.Status.ACTIVE
        subscription.ends_at = timezone.now() + timedelta(hours=4)
        subscription.auto_renew = True
        subscription.provider_payment_method_id = "saved-method"
        subscription.save()
        renew_yookassa_subscriptions()
        order = SubscriptionOrder.objects.get(tenant=self.tenant)
        self.assertEqual(order.provider, "yookassa")
        self.assertTrue(order.auto_renew_requested)
        recurring_payment.assert_called_once_with(order, "saved-method")
        renew_yookassa_subscriptions()
        self.assertEqual(SubscriptionOrder.objects.filter(tenant=self.tenant).count(), 1)

    def test_disabling_auto_renewal_erases_saved_method(self):
        subscription = self.tenant.subscription
        subscription.auto_renew = True
        subscription.provider_payment_method_id = "saved-method"
        subscription.save()
        response = self.client.post(
            "/subscription/settings/",
            {"action": "disable-auto-renew"},
            **self.host,
        )
        self.assertRedirects(response, "/subscription/settings/")
        subscription.refresh_from_db()
        self.assertFalse(subscription.auto_renew)
        self.assertEqual(subscription.provider_payment_method_id, "")
