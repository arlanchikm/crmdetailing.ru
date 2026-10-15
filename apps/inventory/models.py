import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.catalog.models import Service
from apps.operations.models import Appointment
from apps.tenants.models import Location, Tenant


class InventoryCategory(models.Model):
    class Unit(models.TextChoices):
        LITER = "liter", "л"
        MILLILITER = "milliliter", "мл"
        PIECE = "piece", "шт."

    DEFAULTS = (
        ("chemistry", "Химия", Unit.LITER),
        ("microfiber", "Фибры", Unit.PIECE),
        ("polishing_pad", "Полировальные круги", Unit.PIECE),
        ("polishing_compound", "Полировальные пасты", Unit.LITER),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="inventory_categories")
    name = models.CharField("название", max_length=120)
    unit = models.CharField("формат исчисления", max_length=12, choices=Unit.choices)
    builtin_key = models.CharField(max_length=32, blank=True)
    active = models.BooleanField("активна", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("tenant", "name"), name="unique_inventory_category_per_tenant"),
            models.UniqueConstraint(
                fields=("tenant", "builtin_key"),
                condition=~Q(builtin_key=""),
                name="unique_builtin_inventory_category_per_tenant",
            ),
        ]
        verbose_name = "категория расходников"
        verbose_name_plural = "категории расходников"

    def __str__(self):
        return self.name


def ensure_default_categories(tenant):
    categories = {}
    for key, name, unit in InventoryCategory.DEFAULTS:
        category, _ = InventoryCategory.objects.get_or_create(
            tenant=tenant, builtin_key=key, defaults={"name": name, "unit": unit}
        )
        categories[key] = category
    return categories


class Consumable(models.Model):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="consumables")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="consumables",
    )
    name = models.CharField("название", max_length=160)
    category = models.ForeignKey(
        InventoryCategory,
        on_delete=models.PROTECT,
        related_name="consumables",
        null=True,
        verbose_name="категория",
    )
    legacy_category = models.CharField(max_length=32, blank=True, editable=False)
    legacy_unit = models.CharField(max_length=12, blank=True, editable=False)
    cost_per_unit = models.DecimalField(
        "стоимость за единицу", max_digits=14, decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    planned_stock = models.DecimalField(
        "план", max_digits=14, decimal_places=3, default=0,
    )
    actual_stock = models.DecimalField(
        "факт", max_digits=14, decimal_places=3, default=0,
        validators=[MinValueValidator(0)],
    )
    minimum_stock = models.DecimalField(
        "минимальный остаток", max_digits=14, decimal_places=3, default=0,
        validators=[MinValueValidator(0)],
    )
    legacy_consumption_per_car = models.DecimalField(
        max_digits=14, decimal_places=3, default=0, editable=False
    )
    active = models.BooleanField("активен", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("category", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "location", "name"), name="unique_consumable_per_location",
            )
        ]
        verbose_name = "расходник"
        verbose_name_plural = "расходники"

    def __str__(self):
        return self.name

    @property
    def unit(self):
        return self.category.unit if self.category_id else InventoryCategory.Unit.PIECE

    @property
    def unit_display(self):
        return {
            InventoryCategory.Unit.LITER: "л",
            InventoryCategory.Unit.MILLILITER: "мл",
            InventoryCategory.Unit.PIECE: "шт.",
        }[self.unit]

    @property
    def variance(self):
        return self.actual_stock - self.planned_stock

    @property
    def is_low_stock(self):
        return self.planned_stock <= 0 or (
            self.minimum_stock > 0 and self.planned_stock <= self.minimum_stock
        )

    @property
    def price_unit_display(self):
        return "л" if self.unit in (
            InventoryCategory.Unit.LITER, InventoryCategory.Unit.MILLILITER
        ) else "шт."


class ServiceConsumableNorm(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="service_consumable_norms")
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="consumable_norms")
    vehicle_class = models.ForeignKey(
        "customers.VehicleClass", on_delete=models.PROTECT, null=True, blank=True,
        related_name="consumable_norms",
    )
    consumable = models.ForeignKey(Consumable, on_delete=models.PROTECT, related_name="service_norms")
    quantity = models.DecimalField(
        "норматив", max_digits=14, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))]
    )

    class Meta:
        ordering = ("consumable__name",)
        constraints = [
            models.UniqueConstraint(fields=("service", "consumable"), condition=models.Q(vehicle_class__isnull=True), name="unique_default_service_norm"),
            models.UniqueConstraint(fields=("service", "vehicle_class", "consumable"), condition=models.Q(vehicle_class__isnull=False), name="unique_class_service_norm"),
        ]

    @property
    def input_unit_display(self):
        return "мл" if self.consumable.unit in (
            InventoryCategory.Unit.LITER, InventoryCategory.Unit.MILLILITER
        ) else "шт."

    @property
    def stock_quantity(self):
        return self.quantity / 1000 if self.consumable.unit == InventoryCategory.Unit.LITER else self.quantity

    @property
    def total_cost(self):
        if self.consumable.unit in (
            InventoryCategory.Unit.LITER, InventoryCategory.Unit.MILLILITER
        ):
            return self.quantity / 1000 * self.consumable.cost_per_unit
        return self.quantity * self.consumable.cost_per_unit


class AppointmentConsumption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="consumption_entries")
    appointment = models.ForeignKey(
        Appointment, on_delete=models.CASCADE, related_name="consumption_entries"
    )
    consumable = models.ForeignKey(
        Consumable, on_delete=models.PROTECT, related_name="writeoffs"
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost_snapshot = models.DecimalField(max_digits=14, decimal_places=2)
    total_cost = models.DecimalField(max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("appointment", "consumable"), name="unique_consumable_writeoff_per_appointment"
            )
        ]


class InventoryMovement(models.Model):
    class Kind(models.TextChoices):
        RECEIPT = "receipt", "Поступление"
        MANUAL_WRITEOFF = "manual_writeoff", "Ручное списание"
        CORRECTION = "correction", "Корректировка"
        INVENTORY = "inventory", "Инвентаризация"
        NORMATIVE_WRITEOFF = "normative_writeoff", "Списание по нормативу"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="inventory_movements")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="inventory_movements",
    )
    consumable = models.ForeignKey(Consumable, on_delete=models.PROTECT, related_name="movements")
    appointment = models.ForeignKey(
        Appointment, on_delete=models.SET_NULL, related_name="inventory_movements",
        null=True, blank=True,
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    planned_delta = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    actual_delta = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    planned_after = models.DecimalField(max_digits=14, decimal_places=3)
    actual_after = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost_snapshot = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reason = models.CharField("причина", max_length=240, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="inventory_movements"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("tenant", "created_at"))]
