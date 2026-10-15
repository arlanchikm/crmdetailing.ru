import re
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.tenants.models import Tenant


def normalize_phone(value):
    from apps.core.phones import phone_digits
    return phone_digits(value)


def normalize_plate(value):
    return re.sub(r"[\s-]", "", value or "").upper()


class VehicleClass(models.Model):
    """Класс автомобиля компании, определяющий цену услуги."""

    DEFAULTS = (
        ("sedan", "Седан"),
        ("crossover", "Паркетник"),
        ("suv", "Внедорожник"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="vehicle_classes")
    name = models.CharField("название", max_length=120)
    image = models.FileField("изображение", upload_to="vehicle-classes/%Y/%m/", blank=True)
    sort_order = models.PositiveSmallIntegerField("порядок", default=0)
    builtin_key = models.CharField(max_length=32, blank=True)
    active = models.BooleanField("активен", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "created_at", "name")
        constraints = [
            models.UniqueConstraint(fields=("tenant", "name"), name="unique_vehicle_class_name_per_tenant"),
            models.UniqueConstraint(
                fields=("tenant", "builtin_key"), condition=~Q(builtin_key=""),
                name="unique_builtin_vehicle_class_per_tenant",
            ),
        ]
        verbose_name = "класс автомобиля"
        verbose_name_plural = "классы автомобилей"

    def __str__(self):
        return self.name


def ensure_default_vehicle_classes(tenant):
    classes = {}
    for key, name in VehicleClass.DEFAULTS:
        vehicle_class, _ = VehicleClass.objects.get_or_create(
            tenant=tenant, builtin_key=key, defaults={"name": name}
        )
        classes[key] = vehicle_class
    return classes


class Client(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="clients")
    name = models.CharField("имя", max_length=160)
    last_name = models.CharField("фамилия", max_length=160, blank=True)
    phone = models.CharField("телефон", max_length=32)
    normalized_phone = models.CharField(max_length=20, editable=False)
    phone_needs_review = models.BooleanField(default=False)
    email = models.EmailField("email", blank=True)
    notes = models.TextField("заметки", blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        indexes = [models.Index(fields=("tenant", "normalized_phone"))]
        verbose_name = "клиент"
        verbose_name_plural = "клиенты"

    def clean(self):
        from apps.core.phones import format_phone
        try:
            self.phone = format_phone(self.phone)
            self.phone_needs_review = False
        except ValidationError as exc:
            raise ValidationError({'phone': exc.messages})
        self.normalized_phone = normalize_phone(self.phone)

    def save(self, *args, **kwargs):
        from apps.core.phones import format_phone
        # Preserve legacy invalid values on unrelated updates; forms reject them.
        try:
            self.phone = format_phone(self.phone)
            self.phone_needs_review = False
        except ValidationError:
            if self._state.adding or ('phone' in (kwargs.get('update_fields') or [])):
                raise
        self.normalized_phone = normalize_phone(self.phone)
        if kwargs.get("update_fields") and "phone" in kwargs["update_fields"]:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"normalized_phone", "phone_needs_review"}
        super().save(*args, **kwargs)

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return " ".join(part for part in (self.name, self.last_name) if part).strip()


class Car(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="cars")
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="cars")
    vehicle_class = models.ForeignKey(
        VehicleClass, on_delete=models.PROTECT, related_name="cars",
        null=True, blank=True, verbose_name="класс автомобиля",
    )
    make = models.CharField("марка", max_length=80, blank=True)
    model = models.CharField("модель", max_length=80, blank=True)
    plate = models.CharField("госномер", max_length=24, blank=True)
    normalized_plate = models.CharField(max_length=24, blank=True, editable=False)
    color = models.CharField("цвет", max_length=64, blank=True)
    vin = models.CharField("VIN", max_length=32, blank=True)
    notes = models.TextField("заметки", blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("make", "model")
        indexes = [models.Index(fields=("tenant", "normalized_plate"))]
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "normalized_plate"),
                condition=~Q(normalized_plate=""),
                name="unique_plate_per_tenant",
            )
        ]
        verbose_name = "автомобиль"
        verbose_name_plural = "автомобили"

    def clean(self):
        self.normalized_plate = normalize_plate(self.plate)
        if self.client_id and self.tenant_id != self.client.tenant_id:
            raise ValidationError("Автомобиль и клиент должны принадлежать одной компании.")
        if self.vehicle_class_id and self.tenant_id != self.vehicle_class.tenant_id:
            raise ValidationError("Класс автомобиля принадлежит другой компании.")

    def save(self, *args, **kwargs):
        self.normalized_plate = normalize_plate(self.plate)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.make} {self.model}".strip() or self.plate or "Автомобиль не указан"


def damage_photo_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"damages/{instance.damage.car_id}/{uuid.uuid4()}.{extension}"


class CarDamage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="car_damages")
    car = models.ForeignKey(Car, on_delete=models.CASCADE, related_name="damages")
    x_percent = models.DecimalField(
        "позиция по горизонтали, %", max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    y_percent = models.DecimalField(
        "позиция по вертикали, %", max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    discovered_on = models.DateField("дата добавления")
    description = models.TextField("описание")
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_car_damages",
    )
    updated_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="updated_car_damages",
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-discovered_on", "-created_at")

    def clean(self):
        if self.car_id and self.tenant_id != self.car.tenant_id:
            raise ValidationError("Повреждение и автомобиль должны принадлежать одной компании.")


class CarDamagePhoto(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    damage = models.ForeignKey(CarDamage, on_delete=models.CASCADE, related_name="photos")
    file = models.FileField("фотография", upload_to=damage_photo_path)
    original_name = models.CharField(max_length=255, blank=True)
    size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
