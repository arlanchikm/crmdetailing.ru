import base64
import json
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction

from apps.tenants.models import Subscription, SubscriptionOrder
from apps.tenants.plans import confirm_order


API_ROOT = "https://api.yookassa.ru/v3"


class YooKassaError(RuntimeError):
    pass


def is_configured():
    return bool(settings.YOOKASSA_SHOP_ID and settings.YOOKASSA_SECRET_KEY)


def _request(method, path, *, payload=None, idempotence_key=None):
    if not is_configured():
        raise YooKassaError("ЮKassa пока не настроена.")
    credentials = f"{settings.YOOKASSA_SHOP_ID}:{settings.YOOKASSA_SECRET_KEY}".encode()
    headers = {
        "Authorization": "Basic " + base64.b64encode(credentials).decode("ascii"),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "CRM-Detailing/1.0",
    }
    if idempotence_key:
        headers["Idempotence-Key"] = str(idempotence_key)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(API_ROOT + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise YooKassaError(f"ЮKassa отклонила запрос (HTTP {exc.code}).") from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise YooKassaError("Не удалось связаться с ЮKassa. Повторите попытку позже.") from exc


def _payment_payload(order, *, return_url=None, payment_method_id=None):
    payload = {
        "amount": {"value": f"{order.total:.2f}", "currency": "RUB"},
        "capture": True,
        "description": f"Подписка CRM Detailing: {order.plan.name}, {order.period_months} мес.",
        "metadata": {"subscription_order_id": str(order.pk), "tenant_id": str(order.tenant_id)},
    }
    if payment_method_id:
        payload["payment_method_id"] = payment_method_id
    else:
        payload["confirmation"] = {"type": "redirect", "return_url": return_url}
        if order.auto_renew_requested:
            payload["save_payment_method"] = True
    return payload


def create_checkout_payment(order, *, return_url):
    payment = _request(
        "POST",
        "/payments",
        payload=_payment_payload(order, return_url=return_url),
        idempotence_key=order.provider_idempotence_key,
    )
    payment_id = str(payment.get("id") or "")
    confirmation_url = str((payment.get("confirmation") or {}).get("confirmation_url") or "")
    if not payment_id or not confirmation_url:
        raise YooKassaError("ЮKassa не вернула ссылку для оплаты.")
    SubscriptionOrder.objects.filter(pk=order.pk).update(
        provider="yookassa",
        provider_payment_id=payment_id,
        provider_status=str(payment.get("status") or "pending"),
    )
    return confirmation_url


def create_recurring_payment(order, payment_method_id):
    payment = _request(
        "POST",
        "/payments",
        payload=_payment_payload(order, payment_method_id=payment_method_id),
        idempotence_key=order.provider_idempotence_key,
    )
    payment_id = str(payment.get("id") or "")
    if not payment_id:
        raise YooKassaError("ЮKassa не вернула идентификатор платежа.")
    SubscriptionOrder.objects.filter(pk=order.pk).update(
        provider="yookassa",
        provider_payment_id=payment_id,
        provider_status=str(payment.get("status") or "pending"),
    )
    return apply_payment(payment)


def get_payment(payment_id):
    if (
        not payment_id
        or len(payment_id) > 128
        or not payment_id.replace("-", "").isalnum()
    ):
        raise YooKassaError("Некорректный идентификатор платежа.")
    return _request("GET", f"/payments/{payment_id}")


def sync_payment(payment_id):
    return apply_payment(get_payment(payment_id))


@transaction.atomic
def apply_payment(payment):
    payment_id = str(payment.get("id") or "")
    metadata = payment.get("metadata") or {}
    order_id = str(metadata.get("subscription_order_id") or "")
    if not payment_id or not order_id:
        raise YooKassaError("Платёж не связан со счётом CRM.")
    try:
        order = SubscriptionOrder.objects.select_for_update(of=("self",)).select_related(
            "tenant", "plan", "promo"
        ).get(pk=order_id, provider="yookassa")
    except (SubscriptionOrder.DoesNotExist, ValueError) as exc:
        raise YooKassaError("Счёт для платежа не найден.") from exc
    if order.provider_payment_id and order.provider_payment_id != payment_id:
        raise YooKassaError("Идентификатор платежа не совпадает со счётом.")
    provider_status = str(payment.get("status") or "")
    order.provider_payment_id = payment_id
    order.provider_status = provider_status
    order.save(update_fields=("provider_payment_id", "provider_status"))
    if provider_status == "canceled":
        if order.status == SubscriptionOrder.Status.PENDING:
            order.status = SubscriptionOrder.Status.CANCELLED
            order.save(update_fields=("status",))
        return "canceled"
    if provider_status != "succeeded" or payment.get("paid") is not True:
        return "pending"
    amount = payment.get("amount") or {}
    try:
        paid_amount = Decimal(str(amount.get("value")))
    except (InvalidOperation, TypeError) as exc:
        raise YooKassaError("ЮKassa вернула некорректную сумму платежа.") from exc
    if amount.get("currency") != "RUB" or paid_amount != order.total:
        raise YooKassaError("Сумма платежа не совпадает со счётом.")
    if order.status == SubscriptionOrder.Status.PAID:
        return "succeeded"
    if order.status != SubscriptionOrder.Status.PENDING:
        raise YooKassaError("Счёт уже отменён.")
    confirm_order(order, actor=None)
    subscription = Subscription.objects.select_for_update().get(tenant=order.tenant)
    payment_method = payment.get("payment_method") or {}
    method_id = str(payment_method.get("id") or "") if payment_method.get("saved") else ""
    subscription.auto_renew = bool(order.auto_renew_requested and method_id)
    subscription.provider_payment_method_id = method_id if subscription.auto_renew else ""
    subscription.save(update_fields=("auto_renew", "provider_payment_method_id", "updated_at"))
    transaction.on_commit(lambda: _notify_payment_result(order, subscription.auto_renew))
    return "succeeded"


def _notify_payment_result(order, auto_renew):
    from apps.core.models import notify

    notify(
        tenant=order.tenant,
        kind="subscription_payment",
        title="Подписка оплачена",
        message=(
            f"{order.plan.name}, {order.period_months} мес. · {order.total} ₽"
            + (" · автопродление включено" if auto_renew else "")
        ),
        url="/subscription/settings/",
        dedupe_key=f"subscription-payment:{order.pk}",
    )
