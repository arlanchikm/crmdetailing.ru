import json
import logging
from datetime import timedelta
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import Notification, PushSubscription

logger = logging.getLogger(__name__)

@shared_task(ignore_result=True, autoretry_for=(ConnectionError,),retry_backoff=True,max_retries=3)
def deliver_push(notification_id):
    from pywebpush import webpush,WebPushException
    from requests.exceptions import RequestException
    from .notifications import webpush_config
    row=Notification.objects.select_related('tenant','recipient').filter(pk=notification_id).first()
    if not row or not row.recipient or row.read_at: return
    user=row.recipient
    if not user.is_active or not user.notification_push or row.kind in (user.disabled_notification_kinds or []): return
    if not user.memberships.filter(tenant=row.tenant,status='active').exists(): return
    config=webpush_config()
    url=row.url if row.url.startswith('/') and not row.url.startswith('//') else '/notifications/'
    payload=json.dumps({'id':str(row.pk),'user_id':str(user.pk),'title':row.title,'message':row.message,
        'severity':row.severity,'url':url,'read_url':f'/notifications/{row.pk}/read/'},ensure_ascii=False)
    for subscription in PushSubscription.objects.filter(user=user,tenant=row.tenant):
        try:
            webpush(subscription_info={'endpoint':subscription.endpoint,'keys':{'p256dh':subscription.p256dh,'auth':subscription.auth}},
                data=payload,vapid_private_key=config.private_key,
                vapid_claims={'sub':'mailto:'+settings.SUPPORT_EMAIL},ttl=3600,timeout=10)
        except RequestException:
            raise ConnectionError('Push service unavailable')
        except WebPushException as exc:
            if exc.response is not None and exc.response.status_code in {404,410}: subscription.delete()
            elif exc.response is not None and exc.response.status_code>=500: raise ConnectionError('Push service unavailable')


@shared_task(ignore_result=True)
def refresh_notifications_background():
    from apps.tenants.models import Tenant
    from .services import refresh_operational_notifications
    for tenant in Tenant.objects.filter(status='active').iterator(chunk_size=100):
        try:
            refresh_operational_notifications(tenant)
        except Exception:
            # The failed company's transaction rolls back; the next beat retries it.
            logger.exception('Operational notification refresh failed for tenant %s', tenant.pk)


@shared_task(ignore_result=True)
def renew_yookassa_subscriptions():
    if not settings.YOOKASSA_ENABLED:
        return
    from apps.tenants.models import Subscription

    deadline = timezone.now() + timedelta(hours=24)
    subscription_ids = list(
        Subscription.objects.filter(
            status=Subscription.Status.ACTIVE,
            auto_renew=True,
            provider_payment_method_id__gt="",
            ends_at__isnull=False,
            ends_at__lte=deadline,
        ).values_list("pk", flat=True)
    )
    for subscription_id in subscription_ids:
        _renew_subscription(subscription_id)


def _renew_subscription(subscription_id):
    from apps.core.models import notify
    from apps.core.yookassa import YooKassaError, create_recurring_payment
    from apps.tenants.models import Subscription, SubscriptionOrder
    from apps.tenants.plans import create_order

    now = timezone.now()
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().select_related("tenant", "plan").get(
            pk=subscription_id
        )
        if (
            subscription.status != Subscription.Status.ACTIVE
            or not subscription.auto_renew
            or not subscription.provider_payment_method_id
            or not subscription.ends_at
            or subscription.ends_at > now + timedelta(hours=24)
        ):
            return
        recent_attempt = SubscriptionOrder.objects.filter(
            tenant=subscription.tenant,
            provider="yookassa",
            created_at__gte=now - timedelta(hours=12),
        ).exists()
        if recent_attempt:
            return
        months = {
            Subscription.BillingPeriod.MONTH: 1,
            Subscription.BillingPeriod.HALF_YEAR: 6,
            Subscription.BillingPeriod.YEAR: 12,
        }[subscription.billing_period]
        order = create_order(
            subscription.tenant,
            subscription.plan,
            months,
            provider="yookassa",
            auto_renew_requested=True,
        )
        payment_method_id = subscription.provider_payment_method_id
    try:
        create_recurring_payment(order, payment_method_id)
    except YooKassaError:
        SubscriptionOrder.objects.filter(pk=order.pk).update(
            status=SubscriptionOrder.Status.CANCELLED,
            provider_status="error",
        )
        notify(
            tenant=order.tenant,
            kind="subscription_payment",
            title="Не удалось продлить подписку",
            message="Проверьте способ оплаты и оплатите подписку вручную.",
            url="/subscription/settings/",
            severity="warning",
            dedupe_key=f"subscription-renewal-error:{order.pk}",
        )
