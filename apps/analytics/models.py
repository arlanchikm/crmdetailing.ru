import uuid

from django.core.validators import MinValueValidator
from django.db import models

from apps.tenants.models import Location, Tenant


class Expense(models.Model):
    class Category(models.TextChoices):
        RENT = "rent", "Аренда"
        UTILITIES = "utilities", "Коммунальные услуги"
        SALARIES = "salaries", "Корректировки зарплаты"
        OTHER = "other", "Другие"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="expenses")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="expenses",
    )
    date = models.DateField("дата")
    category = models.CharField("категория", max_length=24, choices=Category.choices)
    amount = models.DecimalField(
        "сумма", max_digits=14, decimal_places=2, validators=[MinValueValidator(0)]
    )
    comment = models.CharField("комментарий", max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-date", "-created_at")
        indexes = [models.Index(fields=("tenant", "date"))]
        verbose_name = "затрата"
        verbose_name_plural = "затраты"
