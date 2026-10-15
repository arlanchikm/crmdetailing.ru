import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.tenants.models import Location, Tenant
from apps.customers.models import VehicleClass


class ServiceCategory(models.Model):
    DEFAULTS = (
        ("wash", "Мойка"),
        ("protection", "Защита"),
        ("detailing", "Детейлинг"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="service_categories")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True,
        related_name="service_categories",
    )
    name = models.CharField("название", max_length=120)
    builtin_key = models.CharField(max_length=32, blank=True)
    active = models.BooleanField("активна", default=True)
    sort_order = models.PositiveSmallIntegerField("порядок", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "location", "name"),
                condition=models.Q(location__isnull=False),
                name="unique_service_category_per_location",
            ),
            models.UniqueConstraint(
                fields=("tenant", "name"),
                condition=models.Q(location__isnull=True),
                name="unique_service_category_without_location",
            ),
            models.UniqueConstraint(
                fields=("tenant", "location", "builtin_key"),
                condition=~models.Q(builtin_key="") & models.Q(location__isnull=False),
                name="unique_builtin_service_category_per_location",
            ),
            models.UniqueConstraint(
                fields=("tenant", "builtin_key"),
                condition=~models.Q(builtin_key="") & models.Q(location__isnull=True),
                name="unique_builtin_service_category_without_location",
            ),
        ]
        verbose_name = "категория услуг"
        verbose_name_plural = "категории услуг"

    def __str__(self):
        return self.name


def ensure_default_service_categories(tenant, location=None):
    categories = {}
    for order, (key, name) in enumerate(ServiceCategory.DEFAULTS):
        category, _ = ServiceCategory.objects.get_or_create(
            tenant=tenant, location=location, builtin_key=key,
            defaults={"name": name, "sort_order": order},
        )
        categories[key] = category
    return categories


class WorkPost(models.Model):
    class Kind(models.TextChoices):
        WASH = "wash", "Мойка"
        DETAILING = "detailing", "Детейлинг"
        UNIVERSAL = "universal", "Универсальный"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="work_posts")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="work_posts",
    )
    name = models.CharField("название", max_length=120)
    kind = models.CharField("тип", max_length=16, choices=Kind.choices, default=Kind.UNIVERSAL)
    active = models.BooleanField("активен", default=True)
    sort_order = models.PositiveSmallIntegerField("порядок", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "location", "name"), name="unique_post_name_per_location",
            )
        ]
        verbose_name = "рабочий пост"
        verbose_name_plural = "рабочие посты"

    def __str__(self):
        return self.name


class Service(models.Model):
    class CalculationType(models.TextChoices):
        SERVICE = "service", "За услугу"
        UNIT = "unit", "За единицу"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="services")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="services",
    )
    category = models.ForeignKey(
        ServiceCategory, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="services", verbose_name="категория",
    )
    name = models.CharField("название", max_length=160)
    price = models.DecimalField(
        "цена", max_digits=14, decimal_places=2, validators=[MinValueValidator(0)]
    )
    duration_minutes = models.PositiveIntegerField(
        "норматив времени, минут", validators=[MinValueValidator(1)]
    )
    employee_percent = models.DecimalField(
        "% сотруднику", max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    calculation_type = models.CharField(
        "тип расчета услуги", max_length=12, choices=CalculationType.choices,
        default=CalculationType.SERVICE,
    )
    system_key = models.CharField(max_length=32, blank=True, editable=False)
    legacy_cost = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    active = models.BooleanField("активна", default=True)
    sort_order = models.PositiveSmallIntegerField("порядок", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "location", "name"), name="unique_service_name_per_location",
            ),
            models.UniqueConstraint(
                fields=("tenant", "location", "system_key"), condition=~models.Q(system_key=""),
                name="unique_system_service_per_location",
            ),
        ]
        verbose_name = "услуга"
        verbose_name_plural = "услуги"

    def __str__(self):
        return self.name

    @property
    def cost(self):
        return self.cost_for_class(None)

    def norms_for_class(self, vehicle_class):
        norms = list(self.consumable_norms.all() if "consumable_norms" in getattr(self, "_prefetched_objects_cache", {}) else self.consumable_norms.select_related("consumable__category"))
        specific = [norm for norm in norms if vehicle_class and norm.vehicle_class_id == vehicle_class.id]
        return specific or [norm for norm in norms if norm.vehicle_class_id is None]

    def cost_for_class(self, vehicle_class):
        return sum((norm.total_cost for norm in self.norms_for_class(vehicle_class)), start=0)

    @property
    def default_norms(self):
        return self.norms_for_class(None)

    def duration_for_class(self, vehicle_class):
        if not vehicle_class:
            return self.duration_minutes
        row = next((row for row in self.vehicle_class_prices.all()
                    if vehicle_class and row.vehicle_class_id == vehicle_class.id), None)
        return row.duration_minutes if row and row.duration_minutes else self.duration_minutes

    @property
    def min_price(self):
        values = [row.price for row in self.vehicle_class_prices.all() if row.price is not None]
        return min(values) if values else self.price

    @property
    def max_price(self):
        values = [row.price for row in self.vehicle_class_prices.all() if row.price is not None]
        return max(values) if values else self.price

    def price_for_class(self, vehicle_class):
        rows = list(self.vehicle_class_prices.all())
        if vehicle_class:
            row = next(
                (item for item in rows if item.vehicle_class_id == vehicle_class.id),
                None,
            )
            if row and row.price is not None:
                return row.price
        return self.price


class ServiceVehicleClassPrice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="service_vehicle_prices")
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="vehicle_class_prices")
    vehicle_class = models.ForeignKey(
        VehicleClass, on_delete=models.PROTECT, related_name="service_prices"
    )
    price = models.DecimalField(
        "цена", max_digits=14, decimal_places=2, validators=[MinValueValidator(0)], null=True, blank=True
    )
    duration_minutes = models.PositiveIntegerField(
        "время, минут", validators=[MinValueValidator(1)], null=True, blank=True
    )

    class Meta:
        ordering = ("vehicle_class__created_at", "vehicle_class__name")
        constraints = [
            models.UniqueConstraint(
                fields=("service", "vehicle_class"), name="unique_service_vehicle_class_price"
            )
        ]

    def clean(self):
        if self.service_id and self.tenant_id != self.service.tenant_id:
            raise ValidationError("Услуга принадлежит другой компании.")
        if self.vehicle_class_id and self.tenant_id != self.vehicle_class.tenant_id:
            raise ValidationError("Класс автомобиля принадлежит другой компании.")

    @property
    def effective_price(self):
        return self.price if self.price is not None else self.service.price


def ensure_system_services(tenant, location=None):
    if location is None:
        from apps.tenants.plans import ensure_primary_location
        location = ensure_primary_location(tenant)
    service = Service.objects.filter(tenant=tenant, location=location, system_key="other").first()
    if not service:
        service = Service.objects.filter(
            tenant=tenant, location=location, name__iexact="Другое",
        ).first()
    if service:
        changed = []
        if service.system_key != "other": service.system_key = "other"; changed.append("system_key")
        if service.name != "Другое": service.name = "Другое"; changed.append("name")
        if not service.active: service.active = True; changed.append("active")
        if changed: service.save(update_fields=changed)
    else:
        service = Service.objects.create(
            tenant=tenant, location=location, system_key="other", name="Другое", price=0,
            duration_minutes=30, sort_order=32767,
        )
    return {"other": service}
