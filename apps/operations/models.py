import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from apps.catalog.models import Service, WorkPost
from apps.customers.models import Car, Client, VehicleClass
from apps.staff.models import Employee
from apps.tenants.models import Location, Tenant


class ActiveAppointmentManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(archived_at__isnull=True)


class Appointment(models.Model):
    class Status(models.TextChoices):
        WAITING = "waiting", "Ожидание"
        IN_PROGRESS = "in_progress", "В работе"
        COMPLETED = "completed", "Завершено"
        NO_SHOW = "no_show", "Не пришёл"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Не оплачено"
        PARTIAL = "partial", "Частично оплачено"
        PAID = "paid", "Оплачено"
        REFUNDED = "refunded", "Возвращено"

    class PaymentMethod(models.TextChoices):
        QR = "qr", "QR"
        CASH = "cash", "Наличные"
        CARD = "card", "Карта"
        TRANSFER = "transfer", "Перевод"

    class DiscountKind(models.TextChoices):
        NONE = "none", "Без скидки"
        PERCENT = "percent", "Процент"
        FIXED = "fixed", "Сумма"

    class DiscountStatus(models.TextChoices):
        NONE = "none", "Нет скидки"
        APPLIED = "applied", "Применена"
        PENDING = "pending", "Ожидает подтверждения"
        REJECTED = "rejected", "Отклонена"

    ACTIVE_STATUSES = (Status.WAITING, Status.IN_PROGRESS, Status.COMPLETED, Status.NO_SHOW)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField("источник", max_length=24, default="crm")
    feedback_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="appointments")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="appointments",
    )
    client = models.ForeignKey(
        Client, on_delete=models.SET_NULL, related_name="appointments", null=True, blank=True,
    )
    car = models.ForeignKey(
        Car, on_delete=models.SET_NULL, related_name="appointments", null=True, blank=True,
    )
    client_name_snapshot = models.CharField(max_length=321, blank=True)
    client_phone_snapshot = models.CharField(max_length=32, blank=True)
    car_snapshot = models.CharField(max_length=190, blank=True)
    car_make_snapshot = models.CharField(max_length=80, blank=True)
    car_model_snapshot = models.CharField(max_length=80, blank=True)
    car_plate_snapshot = models.CharField(max_length=24, blank=True)
    created_by_employee = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, related_name="created_appointments",
        null=True, blank=True, verbose_name="создал запись",
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="appointments",
        null=True,
        blank=True,
        verbose_name="сотрудник",
    )
    post = models.ForeignKey(
        WorkPost,
        on_delete=models.SET_NULL,
        related_name="appointments",
        null=True,
        blank=True,
        verbose_name="рабочий пост",
    )
    start_at = models.DateTimeField("начало")
    end_at = models.DateTimeField("окончание")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.WAITING)
    payment_status = models.CharField(
        max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID
    )
    total = models.DecimalField("стоимость", max_digits=14, decimal_places=2, default=0)
    subtotal = models.DecimalField("стоимость до скидки", max_digits=14, decimal_places=2, default=0)
    discount_kind = models.CharField(
        "вид скидки", max_length=12, choices=DiscountKind.choices, default=DiscountKind.NONE
    )
    discount_value = models.DecimalField(
        "скидка", max_digits=14, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    discount_amount = models.DecimalField(
        "сумма скидки", max_digits=14, decimal_places=2, default=0
    )
    discount_reason = models.CharField("причина скидки", max_length=240, blank=True)
    discount_status = models.CharField(
        "статус скидки", max_length=12, choices=DiscountStatus.choices,
        default=DiscountStatus.NONE,
    )
    discount_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="requested_appointment_discounts",
    )
    discount_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="approved_appointment_discounts",
    )
    payment_method = models.CharField(
        "последний способ оплаты", max_length=12, choices=PaymentMethod.choices, blank=True
    )
    amount_paid = models.DecimalField("оплачено", max_digits=14, decimal_places=2, default=0)
    payment_fee = models.DecimalField("комиссия оплаты", max_digits=14, decimal_places=2, default=0)
    free_service_reason = models.CharField(
        "причина бесплатной работы", max_length=240, blank=True
    )
    comment = models.TextField("комментарий", blank=True)
    work_started_at = models.DateTimeField("фактическое начало", null=True, blank=True)
    completed_at = models.DateTimeField("фактическое завершение", null=True, blank=True)
    archived_at = models.DateTimeField("удалена", null=True, blank=True)
    archive_reason = models.CharField("причина удаления", max_length=300, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="archived_appointments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ActiveAppointmentManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("start_at",)
        indexes = [models.Index(fields=("tenant", "start_at"))]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_at__gt=F("start_at")), name="appointment_end_after_start"
            )
        ]
        verbose_name = "запись"
        verbose_name_plural = "записи"

    def __str__(self):
        return f"{self.start_at:%d.%m %H:%M} — {self.car_display}"

    @property
    def client_display_name(self):
        return self.client.display_name if self.client_id else (self.client_name_snapshot or "Удалённый клиент")

    @property
    def client_phone_display(self):
        return self.client.phone if self.client_id else self.client_phone_snapshot

    @property
    def car_display(self):
        return str(self.car) if self.car_id else (self.car_snapshot or "Без автомобиля")

    @property
    def car_make_display(self):
        return self.car.make if self.car_id else self.car_make_snapshot

    @property
    def car_model_display(self):
        return self.car.model if self.car_id else self.car_model_snapshot

    @property
    def car_plate_display(self):
        return self.car.plate if self.car_id else self.car_plate_snapshot


class AppointmentService(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name="items")
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField("количество", default=1, validators=[MinValueValidator(1)])
    vehicle_class = models.ForeignKey(
        VehicleClass, on_delete=models.PROTECT, null=True, blank=True,
        related_name="appointment_services",
    )
    vehicle_class_name_snapshot = models.CharField(max_length=120, blank=True)
    name_snapshot = models.CharField(max_length=160)
    price_snapshot = models.DecimalField(max_digits=14, decimal_places=2)
    duration_minutes_snapshot = models.PositiveIntegerField()
    employee_percent_snapshot = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    consumables_snapshot = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("name_snapshot",)
        constraints = [
            models.UniqueConstraint(
                fields=("appointment", "service"), name="unique_service_per_appointment"
            ),
            models.CheckConstraint(condition=Q(quantity__gte=1), name="appointment_service_quantity_gte_1"),
        ]

    @property
    def display_name(self):
        return f"{self.name_snapshot} × {self.quantity}" if self.quantity > 1 else self.name_snapshot


class Complaint(models.Model):
    class Reason(models.TextChoices):
        QUALITY = "quality", "Плохое качество"
        DAMAGE = "damage", "Повреждение"
        INCOMPLETE = "incomplete", "Недовыполненная услуга"
        OTHER = "other", "Другое"

    class Status(models.TextChoices):
        OPEN = "open", "Открыта"
        IN_PROGRESS = "in_progress", "В работе"
        RESOLVED = "resolved", "Закрыта"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="complaints")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="complaints",
    )
    appointment = models.ForeignKey(Appointment, on_delete=models.PROTECT, related_name="complaints")
    service = models.ForeignKey(
        Service, on_delete=models.SET_NULL, null=True, blank=True, related_name="complaints"
    )
    reason = models.CharField(max_length=16, choices=Reason.choices, default=Reason.QUALITY)
    description = models.TextField("описание")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_complaints"
    )
    deadline = models.DateField("срок", null=True, blank=True)
    resolution = models.TextField("решение", blank=True)
    financial_loss = models.DecimalField(
        "финансовые потери", max_digits=14, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    is_rework = models.BooleanField("требуется переделка", default=False)
    rework_completed_at = models.DateTimeField("переделка завершена", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_complaints"
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="resolved_complaints",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("status", "deadline", "-created_at")
        indexes = [models.Index(fields=("tenant", "status", "deadline"))]


def complaint_photo_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"complaints/{instance.complaint_id}/{uuid.uuid4()}.{extension}"


class ComplaintPhoto(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="photos")
    file = models.FileField("фотография", upload_to=complaint_photo_path)
    original_name = models.CharField(max_length=255, blank=True)
    size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)


class ClientRating(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="client_ratings")
    appointment = models.OneToOneField(Appointment, on_delete=models.CASCADE, related_name="rating")
    score = models.PositiveSmallIntegerField(
        "оценка", validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField("комментарий", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="created_client_ratings"
    )
    submitted_by_client = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)


class AppointmentAssignee(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="appointment_assignees")
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name="assignees")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="appointment_assignments")
    salary_share_percent = models.DecimalField(
        "доля зарплаты, %", max_digits=5, decimal_places=2, default=100,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    is_primary = models.BooleanField("основной исполнитель", default=False)
    service_percentages_snapshot = models.JSONField(
        "проценты по услугам на момент записи", default=dict, blank=True
    )

    class Meta:
        ordering = ("-is_primary", "employee__sort_order", "employee__name")
        constraints = [
            models.UniqueConstraint(
                fields=("appointment", "employee"), name="unique_employee_per_appointment"
            ),
            models.UniqueConstraint(
                fields=("appointment",), condition=Q(is_primary=True),
                name="unique_primary_employee_per_appointment",
            ),
        ]


class PaymentSettings(models.Model):
    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="payment_settings")
    qr_fee_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    cash_fee_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    card_fee_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    transfer_fee_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])

    def rate_for(self, method):
        return getattr(self, f"{method}_fee_percent", Decimal("0"))


class CashShift(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="cash_shifts")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="cash_shifts",
    )
    opened_at = models.DateTimeField(auto_now_add=True)
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="opened_cash_shifts"
    )
    opening_cash = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="closed_cash_shifts",
        null=True, blank=True,
    )
    expected_cash = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    actual_cash = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    difference = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    comment = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ("-opened_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "location"), condition=Q(closed_at__isnull=True),
                name="unique_open_cash_shift_per_location",
            )
        ]


class CashCollection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="cash_collections")
    cash_shift = models.ForeignKey(CashShift, on_delete=models.PROTECT, related_name="collections")
    amount = models.DecimalField(
        "сумма инкассации", max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    comment = models.CharField("комментарий", max_length=240, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_collections"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)


class PaymentTransaction(models.Model):
    class Kind(models.TextChoices):
        PAYMENT = "payment", "Оплата"
        REFUND = "refund", "Возврат"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="payment_transactions")
    appointment = models.ForeignKey(Appointment, on_delete=models.PROTECT, related_name="payments")
    cash_shift = models.ForeignKey(
        CashShift, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions"
    )
    kind = models.CharField(max_length=12, choices=Kind.choices)
    method = models.CharField(max_length=12, choices=Appointment.PaymentMethod.choices)
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    fee_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    comment = models.CharField(max_length=240, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payment_transactions"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
