import uuid
from datetime import time
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.tenants.models import Location, Tenant


class CompanyRole(models.Model):
    PERMISSION_CHOICES = (
        ("appointments_view", "Просмотр записей"),
        ("appointments_view_all", "Просмотр записей всех сотрудников"),
        ("appointments_manage", "Создание и изменение записей"),
        ("appointments_status_own", "Начало и завершение своих работ"),
        ("clients_view", "Просмотр клиентов"),
        ("clients_manage", "Изменение клиентов"),
        ("clients_delete", "Удаление и восстановление клиентов и автомобилей"),
        ("clients_permanent_delete", "Окончательное удаление клиентов и автомобилей"),
        ("services_view", "Просмотр услуг"),
        ("services_manage", "Изменение услуг"),
        ("inventory_view", "Просмотр расходников"),
        ("inventory_manage", "Изменение расходников"),
        ("inventory_count", "Ввод фактических остатков"),
        ("analytics_view", "Просмотр аналитики"),
        ("payroll_view", "Просмотр расчета зарплат"),
        ("quality_view", "Просмотр качества и претензий"),
        ("quality_manage", "Изменение претензий и оценок"),
        ("expenses_manage", "Ввод постоянных затрат"),
        ("staff_view", "Просмотр сотрудников"),
        ("staff_manage", "Изменение сотрудников"),
        ("settings_manage", "Настройка рабочих постов"),
    )

    DEFAULTS = {
        "owner": {
            "name": "Владелец",
            "permissions": [code for code, _ in PERMISSION_CHOICES] + ["roles_manage"],
        },
        "admin": {
            "name": "Администратор",
            "permissions": ["appointments_view", "appointments_view_all", "appointments_manage",
                            "clients_view", "clients_manage", "clients_delete", "clients_permanent_delete",
                            "services_view", "staff_view"],
        },
        "polisher": {
            "name": "Полировщик",
            "permissions": [
                "appointments_view", "appointments_status_own", "clients_view", "services_view",
                "inventory_view", "inventory_count", "staff_view",
            ],
        },
        "detailer": {
            "name": "Детейлер",
            "permissions": [
                "appointments_view", "appointments_status_own", "clients_view", "services_view",
                "inventory_view", "inventory_count", "staff_view",
            ],
        },
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="company_roles")
    name = models.CharField("название", max_length=120)
    builtin_key = models.CharField(max_length=32, blank=True)
    permissions = models.JSONField("права доступа", default=list, blank=True)
    active = models.BooleanField("активна", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("tenant", "name"), name="unique_role_name_per_tenant"),
            models.UniqueConstraint(
                fields=("tenant", "builtin_key"),
                condition=~Q(builtin_key=""),
                name="unique_builtin_role_per_tenant",
            ),
        ]
        verbose_name = "роль в компании"
        verbose_name_plural = "роли в компании"

    def __str__(self):
        return self.name

    def has_permission(self, permission):
        return permission in (self.permissions or [])


def ensure_default_roles(tenant):
    roles = {}
    stored = list(CompanyRole.objects.filter(tenant=tenant))
    by_key = {row.builtin_key: row for row in stored if row.builtin_key}
    legacy = {row.name: row for row in stored if not row.builtin_key}
    for key, values in CompanyRole.DEFAULTS.items():
        existing = legacy.get(values["name"])
        if existing and key not in by_key:
            existing.builtin_key = key
            existing.save(update_fields=("builtin_key", "updated_at"))
            by_key[key] = existing
        role = by_key.get(key)
        if role is None:
            role, _ = CompanyRole.objects.get_or_create(
                tenant=tenant, builtin_key=key,
                defaults={"name": values["name"], "permissions": values["permissions"]},
            )
        missing = [code for code in values["permissions"] if code not in (role.permissions or [])]
        if missing and key == "owner":
            role.permissions = [*(role.permissions or []), *missing]
            role.save(update_fields=("permissions", "updated_at"))
        roles[key] = role
    return roles


class Employee(models.Model):
    class SalaryMode(models.TextChoices):
        SERVICE_PERCENT = "service_percent", "% по услугам"
        FIXED_PLUS_PERCENT = "fixed_plus_percent", "Фиксированная ставка + % от услуг"
        FIXED_PLUS_APPOINTMENT_PERCENT = (
            "fixed_plus_appointment_percent", "Фиксированная ставка + % от записей"
        )
        FIXED_MONTHLY = "fixed_monthly", "Фиксированная ставка за месяц"

    class Color(models.TextChoices):
        BLUE = "blue", "Синий"
        PURPLE = "purple", "Фиолетовый"
        ORANGE = "orange", "Оранжевый"
        GREEN = "green", "Зеленый"
        GRAY = "gray", "Серый"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="employees")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="employees",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="employee_profiles",
        null=True,
        blank=True,
        verbose_name="пользователь CRM",
    )
    name = models.CharField("имя", max_length=160)
    position = models.CharField("должность", max_length=120, blank=True)
    company_role = models.ForeignKey(
        CompanyRole,
        on_delete=models.SET_NULL,
        related_name="employees",
        null=True,
        blank=True,
        verbose_name="должность и права",
    )
    color = models.CharField(
        "цвет в расписании", max_length=16, choices=Color.choices, default=Color.BLUE
    )
    active = models.BooleanField("активен", default=True)
    sort_order = models.PositiveSmallIntegerField("порядок", default=0)
    salary_mode = models.CharField(
        "схема зарплаты", max_length=36, choices=SalaryMode.choices,
        default=SalaryMode.SERVICE_PERCENT,
    )
    salary_percent = models.DecimalField(
        "% доля", max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    fixed_monthly_salary = models.DecimalField(
        "фиксированная ставка за месяц", max_digits=14, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    services = models.ManyToManyField(
        "catalog.Service", through="EmployeeService", related_name="capable_employees", blank=True,
        verbose_name="доступные услуги",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "location", "name"), name="unique_employee_name_per_location"
            ),
            models.UniqueConstraint(
                fields=("tenant", "user"),
                condition=Q(user__isnull=False),
                name="unique_employee_user_per_tenant",
            ),
        ]
        verbose_name = "сотрудник"
        verbose_name_plural = "сотрудники"

    def __str__(self):
        return self.name

    @property
    def display_position(self):
        return self.company_role.name if self.company_role_id else (self.position or "Сотрудник")


class EmployeeService(models.Model):
    """Услуга сотрудника и его персональный процент; null означает процент из карточки услуги."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="employee_services")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="service_settings")
    service = models.ForeignKey("catalog.Service", on_delete=models.CASCADE, related_name="employee_settings")
    percent_override = models.DecimalField(
        "персональный процент", max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )

    class Meta:
        ordering = ("service__sort_order", "service__name")
        constraints = [
            models.UniqueConstraint(fields=("employee", "service"), name="unique_employee_service")
        ]

    @property
    def effective_percent(self):
        return self.percent_override if self.percent_override is not None else self.service.employee_percent


def unsupported_employee_services(tenant, employees, services):
    """Return selected services an employee cannot perform.

    Tenants without any capability rows keep the legacy all-services behavior.
    Once capabilities are configured, an employee must have an explicit row for
    every service assigned to them.
    """
    employees = [employee for employee in employees if employee is not None]
    services = list(services)
    if not employees or not services:
        return {}
    settings = EmployeeService.objects.filter(tenant=tenant)
    if not settings.exists():
        return {}
    allowed = {}
    for employee_id, service_id in settings.filter(
        employee__in=employees, service__in=services,
    ).values_list("employee_id", "service_id"):
        allowed.setdefault(employee_id, set()).add(service_id)
    return {
        employee: [service for service in services if service.id not in allowed.get(employee.id, set())]
        for employee in employees
        if any(service.id not in allowed.get(employee.id, set()) for service in services)
    }


class EmployeeShift(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="employee_shifts")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="employee_shifts",
    )
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="shifts")
    date = models.DateField("дата")
    start_time = models.TimeField("начало смены", default=time(9, 0))
    end_time = models.TimeField("окончание смены", default=time(20, 0))

    class Meta:
        ordering = ("date", "employee__sort_order", "employee__name")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "location", "employee", "date"),
                name="unique_employee_shift_location_date",
            )
        ]
        verbose_name = "смена сотрудника"
        verbose_name_plural = "смены сотрудников"

    def clean(self):
        if self.employee_id and self.tenant_id != self.employee.tenant_id:
            raise ValidationError("Смена и сотрудник должны принадлежать одной компании.")
        if self.start_time >= self.end_time:
            raise ValidationError("Окончание смены должно быть позже начала.")

    def __str__(self):
        return f"{self.employee} — {self.date:%d.%m.%Y}"
