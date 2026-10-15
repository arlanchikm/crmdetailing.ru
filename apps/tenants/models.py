import uuid
from datetime import time

from django.conf import settings
from django.core.validators import RegexValidator, MinValueValidator, MaxValueValidator
from django.db import models


slug_validator = RegexValidator(
    regex=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    message="Используйте строчные латинские буквы, цифры и дефис.",
)

hex_color_validator = RegexValidator(
    regex=r"^#[0-9a-fA-F]{6}$",
    message="Укажите цвет в формате #RRGGBB.",
)


class Tenant(models.Model):
    class Theme(models.TextChoices):
        LIGHT_BLUE = "light_blue", "Светлая"
        DARK = "dark", "Тёмная"

    class Language(models.TextChoices):
        RU = "ru", "Русский"
        EN = "en", "English"

    class Status(models.TextChoices):
        ACTIVE = "active", "Активна"
        SUSPENDED = "suspended", "Приостановлена"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("название", max_length=160)
    address = models.CharField("адрес компании", max_length=300, blank=True)
    latitude = models.DecimalField("широта", max_digits=9, decimal_places=6, null=True, blank=True, validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.DecimalField("долгота", max_digits=9, decimal_places=6, null=True, blank=True, validators=[MinValueValidator(-180), MaxValueValidator(180)])
    slug = models.CharField("поддомен", max_length=63, unique=True, validators=[slug_validator])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    timezone = models.CharField(max_length=64, default="Europe/Moscow")
    timezone_auto_detected = models.BooleanField(default=False)
    theme_key = models.CharField(
        "тема оформления",
        max_length=32,
        choices=Theme.choices,
        default=Theme.LIGHT_BLUE,
    )
    logo = models.FileField("логотип", upload_to="company-logos/%Y/%m/", blank=True)
    language_code = models.CharField(
        "язык интерфейса", max_length=10, choices=Language.choices, default=Language.RU,
    )
    primary_color = models.CharField(
        "основной цвет", max_length=7, default="#087CFF", validators=(hex_color_validator,),
    )
    background_color = models.CharField(
        "оттенок фона", max_length=7, default="#DCEBFA", validators=(hex_color_validator,),
    )
    surface_color = models.CharField(
        "оттенок блоков", max_length=7, default="#FFFFFF", validators=(hex_color_validator,),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "компания"
        verbose_name_plural = "компании"

    def __str__(self):
        return self.name


class Plan(models.Model):
    class Code(models.TextChoices):
        START = "start", "Команда"
        BUSINESS = "business", "Студия"
        CENTER = "center", "Центр"
        NETWORK = "network", "Сеть"

    code = models.CharField(max_length=24, choices=Code.choices, primary_key=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=240, blank=True)
    monthly_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    prepaid_monthly_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    features = models.JSONField(default=list, blank=True)
    limits = models.JSONField(default=dict, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("sort_order", "name")
        verbose_name = "тариф"
        verbose_name_plural = "тарифы"

    def __str__(self):
        return self.name


class Subscription(models.Model):
    class Status(models.TextChoices):
        TRIAL = "trial", "Пробный период"
        ACTIVE = "active", "Активна"
        GRACE_PERIOD = "grace_period", "Льготный период"
        SUSPENDED = "suspended", "Приостановлена"
        CANCELLED = "cancelled", "Отменена"

    class BillingPeriod(models.TextChoices):
        MONTH = "month", "Месяц"
        HALF_YEAR = "half_year", "6 месяцев"
        YEAR = "year", "Год"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="subscription")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    started_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    billing_period = models.CharField(
        max_length=10, choices=BillingPeriod.choices, default=BillingPeriod.MONTH,
    )
    auto_renew = models.BooleanField(default=False)
    provider_payment_method_id = models.CharField(max_length=128, blank=True)
    scheduled_plan = models.ForeignKey(
        Plan, on_delete=models.PROTECT, null=True, blank=True,
        related_name="scheduled_subscriptions",
    )
    suspension_reason = models.CharField(max_length=300, blank=True)
    trial_used = models.BooleanField(default=False)
    trial_started_at = models.DateTimeField(null=True, blank=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    trial_previous_plan = models.ForeignKey(
        Plan, on_delete=models.PROTECT, null=True, blank=True,
        related_name="trial_return_subscriptions",
    )
    trial_activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="activated_trials",
    )
    overrides = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "подписка"
        verbose_name_plural = "подписки"

    def __str__(self):
        return f"{self.tenant} — {self.plan}"


class SubscriptionHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="history",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="subscription_changes",
    )
    action = models.CharField(max_length=64)
    from_plan = models.ForeignKey(
        Plan, on_delete=models.SET_NULL, null=True, blank=True, related_name="changes_from",
    )
    to_plan = models.ForeignKey(
        Plan, on_delete=models.SET_NULL, null=True, blank=True, related_name="changes_to",
    )
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "изменение подписки"
        verbose_name_plural = "история подписок"


class Location(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="locations")
    name = models.CharField(max_length=160)
    code = models.SlugField(max_length=63)
    active = models.BooleanField(default=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-is_primary", "name")
        constraints = [
            models.UniqueConstraint(fields=("tenant", "code"), name="unique_location_code_per_tenant"),
            models.UniqueConstraint(
                fields=("tenant",), condition=models.Q(is_primary=True),
                name="unique_primary_location_per_tenant",
            ),
        ]
        verbose_name = "точка"
        verbose_name_plural = "точки"

    def __str__(self):
        return f"{self.tenant}: {self.name}"


class MembershipLocation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    membership = models.ForeignKey(
        "Membership", on_delete=models.CASCADE, related_name="location_access",
    )
    location = models.ForeignKey(
        Location, on_delete=models.CASCADE, related_name="membership_access",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("membership", "location"), name="unique_membership_location_access",
            )
        ]


class TenantWorkingHours(models.Model):
    WEEKDAYS = (
        (0, "Понедельник"), (1, "Вторник"), (2, "Среда"), (3, "Четверг"),
        (4, "Пятница"), (5, "Суббота"), (6, "Воскресенье"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="working_hours")
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS)
    opens_at = models.TimeField("открытие", default=time(8, 0))
    closes_at = models.TimeField("закрытие", default=time(22, 0))
    is_closed = models.BooleanField("выходной", default=False)

    class Meta:
        ordering = ("weekday",)
        constraints = [
            models.UniqueConstraint(fields=("tenant", "weekday"), name="unique_tenant_working_weekday")
        ]

    def __str__(self):
        return f"{self.tenant} — {self.get_weekday_display()}"


def ensure_working_hours(tenant):
    existing = {row.weekday: row for row in tenant.working_hours.all()}
    for weekday, _ in TenantWorkingHours.WEEKDAYS:
        if weekday not in existing:
            existing[weekday] = TenantWorkingHours.objects.create(tenant=tenant, weekday=weekday)
    return [existing[index] for index, _ in TenantWorkingHours.WEEKDAYS]


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Владелец"
        ADMIN = "admin", "Администратор"
        MASTER = "master", "Мастер"

    class Status(models.TextChoices):
        ACTIVE = "active", "Активен"
        INVITED = "invited", "Приглашен"
        DISABLED = "disabled", "Отключен"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "user"), name="unique_tenant_membership"
            )
        ]
        verbose_name = "доступ к компании"
        verbose_name_plural = "доступы к компаниям"

    def __str__(self):
        return f"{self.user} — {self.tenant} ({self.role})"


class PromoCode(models.Model):
    class Kind(models.TextChoices):
        PERCENT = "percent", "Процент"
        FIXED = "fixed", "Сумма в рублях"
    code = models.CharField(max_length=40, unique=True)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    recurring = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    allowed_plans = models.JSONField(default=list, blank=True)
    allowed_periods = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)
    def __str__(self):
        return self.code


class SubscriptionOrder(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает подтверждения"
        PAID = "paid", "Оплачено"
        CANCELLED = "cancelled", "Отменено"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="subscription_orders")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    period_months = models.PositiveSmallIntegerField(default=1)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    promo = models.ForeignKey(PromoCode, on_delete=models.PROTECT, null=True, blank=True)
    promo_snapshot = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    provider = models.CharField(max_length=24, default="manual")
    provider_payment_id = models.CharField(max_length=128, blank=True)
    provider_status = models.CharField(max_length=32, blank=True)
    provider_idempotence_key = models.UUIDField(default=uuid.uuid4, editable=False)
    auto_renew_requested = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ("-created_at",)


class PromoRedemption(models.Model):
    promo = models.ForeignKey(PromoCode, on_delete=models.PROTECT, related_name="redemptions")
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT)
    order = models.OneToOneField(SubscriptionOrder, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=("promo", "tenant"), name="one_promo_per_company")]


class NetworkRequest(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новая"
        CONTACTED = "contacted", "Связались"
        CLOSED = "closed", "Закрыта"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.SET_NULL, null=True, blank=True)
    company_name = models.CharField(max_length=160)
    contact_name = models.CharField(max_length=160)
    email = models.EmailField()
    phone = models.CharField(max_length=40)
    phone_needs_review = models.BooleanField(default=False)
    locations = models.PositiveSmallIntegerField(default=2)
    comment = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ("-created_at",)


class PlatformAccess(models.Model):
    email = models.EmailField("Email платформы", unique=True, null=True, blank=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    email_2fa = models.BooleanField(default=False)
    active = models.BooleanField(default=True)


class PlatformLoginChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)


class PlatformLoginAttempt(models.Model):
    key = models.CharField(max_length=200, unique=True)
    failures = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class PlatformAudit(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=64)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ("-created_at",)
