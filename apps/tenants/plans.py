from datetime import timedelta

from django.db import models, transaction
from django.utils import timezone

from .models import Location, Plan, Subscription, SubscriptionHistory


FEATURES = {
    "analytics_advanced": {
        "name": "Расширенная аналитика",
        "description": "Прибыль, расходы, эффективность сотрудников и услуг, загрузка постов и расход материалов.",
        "minimum_plan": Plan.Code.BUSINESS,
    },
    "inventory_automation": {
        "name": "Автоматизация расходников",
        "description": "Нормы расхода, автоматическое списание и уведомления о минимальном остатке.",
        "minimum_plan": Plan.Code.BUSINESS,
    },
    "payroll_advanced": {
        "name": "Расширенные схемы зарплаты",
        "description": "Проценты по услугам, фиксированная ставка, распределение и корректировки.",
        "minimum_plan": Plan.Code.BUSINESS,
    },
    "quality_advanced": {
        "name": "Полный контроль претензий",
        "description": "Причины, ответственные, сроки, фотографии, переделки и финансовые потери.",
        "minimum_plan": Plan.Code.BUSINESS,
    },
    "notifications_automation": {
        "name": "Автоматические сценарии уведомлений",
        "description": "Настраиваемые события, шаблоны и автоматические цепочки.",
        "minimum_plan": Plan.Code.BUSINESS,
    },
    "cash_advanced": {
        "name": "Кассовые смены и отчёты",
        "description": "Открытие и закрытие смен, инкассации, статьи операций и сверка кассы.",
        "minimum_plan": Plan.Code.BUSINESS,
    },
    "roles_advanced": {
        "name": "Детальная настройка прав",
        "description": "Собственные роли и точная настройка прав сотрудников.",
        "minimum_plan": Plan.Code.BUSINESS,
    },
    "audit_full": {
        "name": "Полная история действий",
        "description": "Журнал действий без ограничения последними 30 днями.",
        "minimum_plan": Plan.Code.BUSINESS,
    },
    "multi_location": {
        "name": "Управление сетью",
        "description": "Несколько точек, раздельный учёт и сводные показатели сети.",
        "minimum_plan": Plan.Code.NETWORK,
    },
}


ALL_FEATURES = [code for code in FEATURES if code != "multi_location"]
PLAN_CONFIG = {
    "start": {"name": "Команда", "description": "До 4 сотрудников, не считая владельца", "monthly_price": 1499,
              "prepaid_monthly_price": 1299, "features": ALL_FEATURES,
              "limits": {"active_employees": 4, "active_locations": 1, "audit_days": None}, "sort_order": 10},
    "business": {"name": "Студия", "description": "До 8 сотрудников, не считая владельца", "monthly_price": 2499,
              "prepaid_monthly_price": 1999, "features": ALL_FEATURES,
              "limits": {"active_employees": 8, "active_locations": 1, "audit_days": None}, "sort_order": 20},
    "center": {"name": "Центр", "description": "До 15 сотрудников, не считая владельца", "monthly_price": 3699,
              "prepaid_monthly_price": 2999, "features": ALL_FEATURES,
              "limits": {"active_employees": 15, "active_locations": 1, "audit_days": None}, "sort_order": 30},
    "network": {"name": "Сеть", "description": "Безлимит сотрудников, от 2 точек. Индивидуальные условия",
                "monthly_price": 0, "prepaid_monthly_price": 0, "features": ALL_FEATURES,
                "limits": {"active_employees": None, "active_locations": 1, "audit_days": None}, "sort_order": 40},
}
PLAN_RANK = {"start": 1, "business": 2, "center": 3, "network": 4}
TRIAL_DAYS = 14


def ensure_default_plans():
    existing = {plan.code: plan for plan in Plan.objects.filter(code__in=PLAN_CONFIG)}
    return {code: existing.get(code) or Plan.objects.get_or_create(code=code, defaults=values)[0]
            for code, values in PLAN_CONFIG.items()}


def counted_employees(tenant):
    from apps.staff.models import Employee
    from .models import Membership
    owners = Membership.objects.filter(tenant=tenant, role=Membership.Role.OWNER).values("user_id")
    return Employee.objects.filter(tenant=tenant, active=True).exclude(user_id__in=owners)


def plan_for_count(count):
    return "start" if count <= 4 else "business" if count <= 8 else "center" if count <= 15 else "network"


def ensure_primary_location(tenant):
    location = tenant.locations.filter(is_primary=True).first()
    if location:
        return location
    location = tenant.locations.filter(active=True).first()
    if location:
        location.is_primary = True
        location.save(update_fields=("is_primary", "updated_at"))
        return location
    return Location.objects.create(
        tenant=tenant, name="Основная", code="main", active=True, is_primary=True,
    )


def backfill_primary_location_data(tenant, location):
    """Repair legacy/imported rows that predate location support."""
    from apps.analytics.models import Expense
    from apps.catalog.models import Service, WorkPost
    from apps.inventory.models import Consumable, InventoryMovement
    from apps.operations.models import Appointment, CashShift, Complaint
    from apps.staff.models import Employee, EmployeeShift
    legacy_models = (
        Expense, Service, WorkPost, Consumable, InventoryMovement,
        Appointment, CashShift, Complaint, Employee, EmployeeShift,
    )
    # One bounded probe, rather than ten UPDATEs on every page/poll/image request.
    probes = [model.objects.filter(tenant=tenant, location__isnull=True).order_by()
              .annotate(model_name=models.Value(model._meta.label))
              .values_list("model_name", flat=True)[:1] for model in legacy_models]
    missing = set(probes[0].union(*probes[1:], all=True))
    for model in legacy_models:
        if model._meta.label in missing:
            model.objects.filter(tenant=tenant, location__isnull=True).update(location=location)


def ensure_subscription(tenant, *, existing_default=False):
    subscription = Subscription.objects.select_related("plan").filter(tenant=tenant).first()
    if subscription is not None:
        return subscription
    plans = ensure_default_plans()
    subscription, _ = Subscription.objects.get_or_create(
        tenant=tenant,
        defaults={
            "plan": plans[plan_for_count(counted_employees(tenant).count()) if existing_default else Plan.Code.START],
            "status": Subscription.Status.ACTIVE,
            "started_at": timezone.now(),
            "ends_at": timezone.now() + timedelta(days=30) if existing_default else None,
        },
    )
    return subscription


def sync_subscription(tenant, *, now=None):
    now = now or timezone.now()
    subscription = ensure_subscription(tenant, existing_default=True)
    expiry = subscription.trial_ends_at if subscription.status == Subscription.Status.TRIAL else subscription.ends_at
    if subscription.status not in {Subscription.Status.TRIAL, Subscription.Status.ACTIVE, Subscription.Status.GRACE_PERIOD} or not expiry or expiry > now:
        return subscription
    return _expire_subscription(tenant, now=now)


@transaction.atomic
def _expire_subscription(tenant, *, now):
    now = now or timezone.now()
    subscription = ensure_subscription(tenant, existing_default=True)
    subscription = Subscription.objects.select_for_update().select_related("plan").get(pk=subscription.pk)
    expiry = subscription.trial_ends_at if subscription.status == Subscription.Status.TRIAL else subscription.ends_at
    if subscription.status in {Subscription.Status.TRIAL, Subscription.Status.ACTIVE, Subscription.Status.GRACE_PERIOD} and expiry and expiry <= now:
        previous = subscription.plan
        if subscription.scheduled_plan_id:
            subscription.plan = subscription.scheduled_plan
            subscription.scheduled_plan = None
            months = subscription.overrides.pop("scheduled_period_months", 0)
            if months:
                subscription.started_at = expiry
                subscription.ends_at = add_months(expiry, months)
                subscription.status = Subscription.Status.ACTIVE
                subscription.billing_period = "month" if months == 1 else "half_year" if months == 6 else "year"
                subscription.save()
                SubscriptionHistory.objects.create(subscription=subscription, action="scheduled_plan_applied", from_plan=previous, to_plan=subscription.plan)
                if subscription.ends_at > now:
                    return subscription
        subscription.status = Subscription.Status.SUSPENDED
        subscription.suspension_reason = "Период подписки завершён. Данные доступны для просмотра и экспорта."
        subscription.save()
        SubscriptionHistory.objects.create(subscription=subscription, action="period_expired", from_plan=previous, to_plan=subscription.plan)
    return subscription


def subscription_features(subscription):
    # Functions do not depend on the staff package. Read-only access survives expiry.
    return set(ALL_FEATURES)


def subscription_limits(subscription):
    limits = dict(subscription.plan.limits or {})
    limits.update((subscription.overrides or {}).get("limits", {}))
    return limits


def tenant_has_feature(tenant, code):
    return code in ALL_FEATURES


def feature_info(code):
    return {"code": code, **FEATURES[code]}


def active_employee_limit(tenant):
    return subscription_limits(sync_subscription(tenant)).get("active_employees")


def active_location_limit(tenant):
    return subscription_limits(sync_subscription(tenant)).get("active_locations")


@transaction.atomic
def activate_trial(tenant, actor, plan_code="start"):
    subscription = ensure_subscription(tenant)
    subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
    if subscription.trial_used or Subscription.objects.filter(trial_activated_by=actor, trial_used=True).exclude(pk=subscription.pk).exists():
        raise ValueError("Пробный период уже был использован.")
    if plan_code not in {"start", "business", "center"}:
        raise ValueError("Выберите пакет сотрудников.")
    now = timezone.now()
    previous = subscription.plan
    subscription.plan = ensure_default_plans()[plan_code]
    subscription.status = Subscription.Status.TRIAL
    subscription.trial_used = True
    subscription.started_at = now
    subscription.trial_started_at = now
    subscription.trial_ends_at = now + timedelta(days=TRIAL_DAYS)
    subscription.ends_at = subscription.trial_ends_at
    subscription.trial_activated_by = actor
    subscription.save()
    SubscriptionHistory.objects.create(subscription=subscription, actor=actor, action="trial_activated", from_plan=previous, to_plan=subscription.plan, details={"days": TRIAL_DAYS})
    return subscription


def activate_business_trial(tenant, actor):
    return activate_trial(tenant, actor, "business")


@transaction.atomic
def assign_plan(tenant, plan_code, actor=None, *, ends_at=None, reason="manual"):
    subscription = sync_subscription(tenant)
    subscription = Subscription.objects.select_for_update().select_related("plan").get(pk=subscription.pk)
    plan = Plan.objects.get(code=plan_code, active=True)
    previous = subscription.plan
    subscription.plan = plan
    subscription.status = Subscription.Status.ACTIVE
    subscription.started_at = timezone.now()
    subscription.ends_at = ends_at
    subscription.scheduled_plan = None
    subscription.suspension_reason = ""
    subscription.save(update_fields=(
        "plan", "status", "started_at", "ends_at", "scheduled_plan",
        "suspension_reason", "updated_at",
    ))
    SubscriptionHistory.objects.create(
        subscription=subscription, actor=actor, action=reason,
        from_plan=previous, to_plan=plan,
    )
    return subscription


from calendar import monthrange
from decimal import Decimal
from .models import PromoCode, PromoRedemption, SubscriptionOrder


def add_months(value, months):
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, monthrange(year, month)[1]))


def order_quote(tenant, plan, months, code=""):
    if plan.code == "network" or months not in {1, 6, 12}:
        raise ValueError("Выберите пакет и период оплаты.")
    subscription = tenant.subscription
    custom_price = subscription.overrides.get("custom_price") if subscription.plan_id == plan.code else None
    monthly_price = Decimal(str(custom_price)) if custom_price is not None else plan.prepaid_monthly_price if months >= 6 else plan.monthly_price
    subtotal = monthly_price * months
    promo = None
    discount = Decimal("0")
    if not code.strip():
        latest = tenant.subscription_orders.filter(status="paid", promo__recurring=True, promo__active=True).select_related("promo").first()
        if latest and (not latest.promo.ends_at or latest.promo.ends_at > timezone.now()) and (not latest.promo.allowed_plans or plan.code in latest.promo.allowed_plans) and (not latest.promo.allowed_periods or months in latest.promo.allowed_periods):
            code = latest.promo.code
    if code.strip():
        promo = PromoCode.objects.filter(code=code.strip().upper(), active=True).first()
        now = timezone.now()
        if not promo or (promo.starts_at and promo.starts_at > now) or (promo.ends_at and promo.ends_at <= now):
            raise ValueError("Промокод недействителен или истёк.")
        if promo.allowed_plans and plan.code not in promo.allowed_plans or promo.allowed_periods and months not in promo.allowed_periods:
            raise ValueError("Промокод не действует для выбранного пакета или периода.")
        redemption = PromoRedemption.objects.filter(promo=promo, tenant=tenant).first()
        if redemption and not promo.recurring:
            raise ValueError("Промокод уже использован компанией.")
        if not promo.recurring and tenant.subscription_orders.filter(status="paid").exists():
            raise ValueError("Этот промокод действует только на первый платёж компании.")
        if not redemption and promo.max_uses is not None and promo.redemptions.count() >= promo.max_uses:
            raise ValueError("Лимит активаций промокода исчерпан.")
        discount = subtotal * promo.value / 100 if promo.kind == "percent" else promo.value
        discount = min(subtotal, max(Decimal("0"), discount)).quantize(Decimal("0.01"))
    return subtotal, discount, subtotal - discount, promo


@transaction.atomic
def create_order(tenant, plan, months, code="", *, provider="manual", auto_renew_requested=False):
    Subscription.objects.select_for_update().get(tenant=tenant)
    if code.strip():
        list(PromoCode.objects.select_for_update().filter(code=code.strip().upper()))
    subtotal, discount, total, promo = order_quote(tenant, plan, months, code)
    SubscriptionOrder.objects.filter(tenant=tenant, status="pending").update(status="cancelled")
    return SubscriptionOrder.objects.create(tenant=tenant, plan=plan, period_months=months,
        subtotal=subtotal, discount=discount, total=total, promo=promo,
        provider=provider, auto_renew_requested=auto_renew_requested,
        promo_snapshot={"code": promo.code, "kind": promo.kind, "value": str(promo.value), "recurring": promo.recurring} if promo else {})


@transaction.atomic
def confirm_order(order, actor):
    tenant_id = SubscriptionOrder.objects.values_list("tenant_id", flat=True).get(pk=order.pk)
    subscription = Subscription.objects.select_for_update().select_related("plan").get(tenant_id=tenant_id)
    order = SubscriptionOrder.objects.select_for_update(of=("self",)).select_related("tenant", "plan", "promo").get(pk=order.pk)
    if order.status != "pending":
        raise ValueError("Этот счёт уже обработан.")
    limit = order.plan.limits.get("active_employees")
    if limit is not None and counted_employees(order.tenant).count() > limit:
        raise ValueError("Число активных сотрудников превышает лимит выбранного пакета. Измените пакет или отключите лишние карточки.")
    if order.promo_id:
        PromoCode.objects.select_for_update().get(pk=order.promo_id)
        # Validate all eligibility again at confirmation; never consume on mere quote.
        order_quote(order.tenant, order.plan, order.period_months, order.promo.code)
        PromoRedemption.objects.get_or_create(promo=order.promo, tenant=order.tenant, defaults={"order": order})
    previous = subscription.plan
    now = timezone.now()
    current_end = subscription.ends_at if subscription.status == "active" and subscription.ends_at and subscription.ends_at > now else now
    if PLAN_RANK[order.plan_id] < PLAN_RANK[subscription.plan_id] and current_end > now:
        subscription.scheduled_plan = order.plan
        # Preserve current capacity to the boundary, then keep the prepaid term.
        subscription.overrides = {**subscription.overrides, "scheduled_period_months": order.period_months}
        SubscriptionHistory.objects.create(subscription=subscription, actor=actor, action="downgrade_scheduled", from_plan=previous, to_plan=order.plan)
    else:
        subscription.plan = order.plan
        subscription.status = "active"
        subscription.started_at = now
        subscription.ends_at = add_months(current_end, order.period_months)
        subscription.billing_period = "month" if order.period_months == 1 else "half_year" if order.period_months == 6 else "year"
        subscription.scheduled_plan = None
        subscription.overrides.pop("scheduled_period_months", None)
        subscription.suspension_reason = ""
        SubscriptionHistory.objects.create(subscription=subscription, actor=actor, action="payment_confirmed", from_plan=previous, to_plan=order.plan, details={"order": str(order.pk), "total": str(order.total)})
    subscription.save()
    order.status = "paid"
    order.paid_at = now
    order.save()
    return subscription
