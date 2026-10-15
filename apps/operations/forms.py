from datetime import timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal

from django import forms
from apps.core.phones import PhoneField
from django.utils import timezone

from apps.catalog.models import Service, WorkPost, ensure_system_services
from apps.customers.models import (
    Car, Client, VehicleClass, ensure_default_vehicle_classes, normalize_phone, normalize_plate,
)
from apps.customers.forms import MultipleFileField, MultipleFileInput
from apps.staff.models import Employee, EmployeeShift, unsupported_employee_services
from apps.tenants.models import ensure_working_hours

from .models import (
    Appointment, CashCollection, CashShift, ClientRating, Complaint,
    PaymentSettings, PaymentTransaction,
)


class ClientChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.display_name} · {obj.phone}"


class CarChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        plate = f" · {obj.plate}" if obj.plate else ""
        return f"{obj.client.display_name} — {obj.make} {obj.model}{plate}"


def _share_value(form, raw, employee):
    try:
        return forms.DecimalField(min_value=0, max_value=100).clean(raw)
    except forms.ValidationError:
        form.add_error("employees", f"Проверьте долю участия: {employee.name}.")
        return Decimal("0")


def _setup_vehicle_class_fields(form, tenant):
    defaults = ensure_default_vehicle_classes(tenant)
    classes = list(VehicleClass.objects.filter(tenant=tenant, active=True))
    queryset = VehicleClass.objects.filter(tenant=tenant, active=True)
    form.fields["car_vehicle_class"].queryset = queryset
    form.fields["car_vehicle_class"].empty_label = None
    sedan = defaults["sedan"]
    if not form.is_bound:
        current_car = getattr(form.instance, "car", None) if form.instance.pk else None
        form.initial["car_vehicle_class"] = (
            current_car.vehicle_class_id if current_car and current_car.vehicle_class_id else sedan.id
        )
    existing_items = {
        row.service_id: row for row in form.instance.items.select_related("vehicle_class").all()
    } if form.instance.pk else {}
    form.service_rows = []
    form.service_price_data = {}
    form.service_duration_data = {}
    for service in form.fields["services"].queryset.prefetch_related("vehicle_class_prices"):
        price_rows = list(service.vehicle_class_prices.all())
        field_name = f"service_class_{service.id}"
        item = existing_items.get(service.id)
        quantity_name = f"service_quantity_{service.id}"
        raw_quantity = form.data.get(quantity_name) if form.is_bound else None
        quantity = raw_quantity or (item.quantity if item else 1)
        initial = item.vehicle_class_id if item and item.vehicle_class_id else form.initial.get("car_vehicle_class")
        form.fields[field_name] = forms.ModelChoiceField(
            label=f"Класс для услуги «{service.name}»", queryset=queryset,
            required=False, empty_label=None, initial=initial,
            widget=forms.Select(attrs={"class": "service-class-select", "data-service-id": str(service.id)}),
        )
        prices = {str(vehicle_class.id): str(service.price_for_class(vehicle_class)) for vehicle_class in classes}
        form.service_rows.append({
            "service": service, "class_field": form[field_name],
            "has_class_choice": len(classes) > 1,
            "quantity": quantity,
        })
        form.service_price_data[str(service.id)] = prices
        form.service_duration_data[str(service.id)] = {str(c.id): service.duration_for_class(c) for c in classes}
    form.vehicle_classes = classes


def _clean_service_quantities(form, cleaned):
    quantities = {}
    existing = {
        row.service_id: row.quantity for row in form.instance.items.all()
    } if form.instance.pk else {}
    for service in cleaned.get("services") or []:
        quantity = 1
        if service.calculation_type == Service.CalculationType.UNIT:
            raw = form.data.get(f"service_quantity_{service.id}")
            if raw in (None, "") and not form.is_bound:
                raw = existing.get(service.id, 1)
            try:
                quantity = forms.IntegerField(min_value=1).clean(raw or 1)
            except forms.ValidationError:
                form.add_error("services", f"Проверьте количество услуги «{service.name}».")
                quantity = 1
        quantities[str(service.id)] = quantity
    cleaned["service_quantities"] = quantities
    return quantities


def _clean_vehicle_class_fields(form, cleaned):
    vehicle_class = cleaned.get("car_vehicle_class")
    car = cleaned.get("car")
    if not vehicle_class and car and car.vehicle_class_id:
        vehicle_class = car.vehicle_class
    if not vehicle_class:
        vehicle_class = VehicleClass.objects.filter(tenant=form.tenant, active=True).first()
    if vehicle_class and vehicle_class.tenant_id != form.tenant.id:
        form.add_error("car_vehicle_class", "Класс автомобиля принадлежит другой компании.")
    cleaned["car_vehicle_class"] = vehicle_class
    service_classes = {}
    for service in cleaned.get("services") or []:
        price_rows = list(service.vehicle_class_prices.all())
        selected = cleaned.get(f"service_class_{service.id}") or vehicle_class
        if not selected:
            form.add_error("services", f"Выберите класс автомобиля для услуги «{service.name}».")
        else:
            service_classes[str(service.id)] = selected
    cleaned["service_classes"] = service_classes
    return vehicle_class, service_classes


def _validate_working_time(form, employees, start_at, end_at):
    if not employees or not start_at or not end_at:
        return
    tenant_tz = ZoneInfo(form.tenant.timezone)
    local_start = timezone.localtime(start_at, tenant_tz)
    local_end = timezone.localtime(end_at, tenant_tz)
    hours = ensure_working_hours(form.tenant)[local_start.weekday()]
    if local_start.date() != local_end.date() or hours.is_closed or not (
        hours.opens_at <= local_start.time() and local_end.time() <= hours.closes_at
    ):
        form.add_error("start_at", "Запись должна полностью попадать в часы работы компании.")
        return
    shifts = {
        row.employee_id: row for row in EmployeeShift.objects.filter(
            tenant=form.tenant, employee__in=employees, date=local_start.date()
        )
    }
    unavailable = [
        employee.name for employee in employees
        if employee.id not in shifts or local_start.time() < shifts[employee.id].start_time
        or local_end.time() > shifts[employee.id].end_time
    ]
    if unavailable:
        form.add_error(
            "employees",
            "В выбранное время не работают: " + ", ".join(unavailable) + ".",
        )


def _validate_employee_services(form, employees, services):
    unsupported = unsupported_employee_services(form.tenant, employees, services or [])
    if unsupported:
        details = "; ".join(
            f"{employee.name}: {', '.join(service.name for service in employee_services)}"
            for employee, employee_services in unsupported.items()
        )
        form.add_error(
            "employees",
            "Исполнитель не может выполнять выбранные услуги — " + details + ".",
        )


class AppointmentForm(forms.ModelForm):
    client = ClientChoiceField(queryset=Client.objects.none(), required=False, widget=forms.HiddenInput)
    car = CarChoiceField(queryset=Car.objects.none(), required=False, widget=forms.HiddenInput)
    client_first_name = forms.CharField(label="Имя *", max_length=160, required=False)
    client_last_name = forms.CharField(label="Фамилия", max_length=160, required=False)
    client_phone = PhoneField(label="Номер телефона *", required=False)
    car_plate = forms.CharField(label="Гос. номер", max_length=24, required=False)
    car_description = forms.CharField(
        label="Автомобиль", max_length=160, required=False,
    )
    car_make = forms.CharField(label="Марка автомобиля", max_length=80, required=False, widget=forms.HiddenInput)
    car_model = forms.CharField(label="Модель автомобиля", max_length=80, required=False, widget=forms.HiddenInput)
    car_vehicle_class = forms.ModelChoiceField(
        label="Класс автомобиля *", queryset=VehicleClass.objects.none(), required=False,
    )
    other_price = forms.DecimalField(label="Стоимость услуги «Другое»", min_value=0, required=False)
    other_duration_minutes = forms.IntegerField(label="Длительность услуги «Другое», минут", min_value=1, required=False)
    services = forms.ModelMultipleChoiceField(
        label="Услуги",
        queryset=Service.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )
    employees = forms.ModelMultipleChoiceField(
        label="Исполнители", queryset=Employee.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    additional_employees = forms.ModelMultipleChoiceField(
        label="Дополнительные исполнители", queryset=Employee.objects.none(),
        required=False, widget=forms.CheckboxSelectMultiple,
    )
    primary_share_percent = forms.DecimalField(
        label="Доля зарплаты основного исполнителя, %", min_value=0, max_value=100,
        decimal_places=2, initial=100, widget=forms.HiddenInput,
    )
    end_at = forms.DateTimeField(
        label="Окончание",
        required=False,
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )

    def __init__(self, *args, tenant=None, location=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.location = location
        ensure_system_services(tenant, location)
        self.fields["client"].queryset = Client.objects.filter(
            tenant=tenant, archived_at__isnull=True
        )
        self.fields["car"].queryset = Car.objects.filter(
            tenant=tenant, archived_at__isnull=True
        ).select_related("client")
        self.fields["employee"].queryset = Employee.objects.filter(
            tenant=tenant, location=location, active=True,
        )
        self.fields["employee"].required = False
        self.fields["employee"].widget = forms.HiddenInput()
        self.fields["employees"].queryset = self.fields["employee"].queryset
        self.fields["additional_employees"].queryset = self.fields["employee"].queryset
        self.fields["employee"].empty_label = "Выберите сотрудника"
        self.fields["post"].queryset = WorkPost.objects.filter(
            tenant=tenant, location=location, active=True,
        )
        self.fields["post"].required = False
        self.fields["post"].empty_label = "Без назначения поста"
        self.fields["services"].queryset = Service.objects.filter(
            tenant=tenant, location=location, active=True,
        ).prefetch_related(
            "vehicle_class_prices"
        )
        _setup_vehicle_class_fields(self, tenant)
        for name in ("primary_share_percent", "discount_kind", "discount_value", "discount_reason", "free_service_reason"):
            self.fields[name].required = False
        self.fields["primary_share_percent"].initial = 100
        self.fields["discount_kind"].initial = Appointment.DiscountKind.NONE
        self.fields["discount_value"].initial = 0
        if not self.is_bound and self.initial.get("employee"):
            employee_id = getattr(self.initial["employee"], "id", self.initial["employee"])
            self.initial["employees"] = [employee_id]

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("car") and (cleaned.get("car_description") or "").strip():
            parts = cleaned["car_description"].strip().split(maxsplit=1)
            cleaned["car_make"] = parts[0]
            cleaned["car_model"] = parts[1] if len(parts) > 1 else ""
        client = cleaned.get("client")
        car = cleaned.get("car")
        employee = cleaned.get("employee")
        post = cleaned.get("post")
        start_at = cleaned.get("start_at")
        services = cleaned.get("services")
        vehicle_class, service_classes = _clean_vehicle_class_fields(self, cleaned)
        service_quantities = _clean_service_quantities(self, cleaned)
        additional_employees = list(cleaned.get("additional_employees") or [])
        end_at = cleaned.get("end_at")
        if start_at and services:
            other = next((service for service in services if service.system_key == "other"), None)
            if other:
                if cleaned.get("other_price") is None:
                    self.add_error("other_price", "Укажите стоимость услуги.")
                if not cleaned.get("other_duration_minutes"):
                    self.add_error("other_duration_minutes", "Укажите длительность услуги.")
                if not (cleaned.get("comment") or "").strip():
                    self.add_error("comment", "Для услуги «Другое» обязательно опишите работу.")
            if any((cleaned.get("other_price") if service.system_key == "other" else service.price_for_class(service_classes.get(str(service.id)))) == 0
                   for service in services) and not (
                cleaned.get("free_service_reason") or ""
            ).strip():
                self.add_error("free_service_reason", "Укажите причину бесплатной работы.")
            duration = sum(
                (cleaned.get("other_duration_minutes") or service.duration_minutes)
                if service.system_key == "other" else (
                    service.duration_for_class(service_classes.get(str(service.id)))
                    * service_quantities.get(str(service.id), 1)
                )
                for service in services
            )
            if not end_at:
                end_at = start_at + timedelta(minutes=duration)
                cleaned["end_at"] = end_at
        if start_at and end_at and end_at <= start_at:
            self.add_error("end_at", "Окончание должно быть позже начала.")
        discount_kind = cleaned.get("discount_kind") or Appointment.DiscountKind.NONE
        discount_value = cleaned.get("discount_value") or 0
        if discount_kind != Appointment.DiscountKind.NONE and discount_value:
            if not (cleaned.get("discount_reason") or "").strip():
                self.add_error("discount_reason", "Укажите причину скидки.")
            if discount_kind == Appointment.DiscountKind.PERCENT and discount_value > 100:
                self.add_error("discount_value", "Процент скидки не может быть больше 100.")

        if not client:
            for field, message in (
                ("client_first_name", "Введите имя."), ("client_phone", "Введите номер телефона."),
            ):
                if not cleaned.get(field):
                    self.add_error(field, message)
        phone = normalize_phone(cleaned.get("client_phone"))
        if cleaned.get("client_phone") and not 10 <= len(phone) <= 15:
            self.add_error("client_phone", "Введите корректный номер телефона.")
        if client and car and car.client_id != client.id:
            self.add_error("car_plate", "Автомобиль не принадлежит выбранному клиенту.")
        for obj, field in (
            (client, "client"),
            (car, "car"),
            (employee, "employee"),
            (post, "post"),
        ):
            if obj and obj.tenant_id != self.tenant.id:
                self.add_error(field, "Объект принадлежит другой компании.")
        selected_employees = list(cleaned.get("employees") or [])
        legacy_mode = not self.data.getlist("employees")
        if not selected_employees and employee:
            selected_employees = [employee, *[item for item in additional_employees if item.id != employee.id]]
        if not selected_employees:
            self.add_error("employees", "Выберите хотя бы одного исполнителя.")
            cleaned["employee_shares"] = {}
            return cleaned
        employee = employee if employee in selected_employees else selected_employees[0]
        additional_employees = [item for item in selected_employees if item.id != employee.id]
        cleaned["employee"] = employee
        cleaned["additional_employees"] = additional_employees
        manual = self.data.get("custom_salary_split") == "1" or legacy_mode
        shares = {}
        if manual:
            primary_share = _share_value(
                self, self.data.get(f"employee_share_{employee.id}", self.data.get("primary_share_percent", "100")), employee
            )
            total_share = primary_share
            for item in additional_employees:
                share = _share_value(self, self.data.get(f"employee_share_{item.id}", "0"), item)
                shares[str(item.id)] = share
                total_share += share
            if total_share != 100:
                self.add_error("employees", "Сумма долей участия всех исполнителей должна быть 100%.")
        else:
            count = Decimal(len(selected_employees))
            equal = (Decimal("100") / count).quantize(Decimal("0.01"))
            primary_share = Decimal("100") - equal * Decimal(len(additional_employees))
            shares = {str(item.id): equal for item in additional_employees}
        cleaned["primary_share_percent"] = primary_share
        cleaned["employee_shares"] = shares
        _validate_employee_services(self, selected_employees, services)
        _validate_working_time(self, selected_employees, start_at, cleaned.get("end_at"))
        return cleaned

    class Meta:
        model = Appointment
        fields = (
            "client",
            "car",
            "employee", "employees",
            "additional_employees", "primary_share_percent",
            "post",
            "services",
            "start_at",
            "end_at",
            "comment",
            "discount_kind", "discount_value", "discount_reason", "free_service_reason",
        )
        widgets = {
            "start_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}
            ),
            "end_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}
            ),
            "comment": forms.Textarea(attrs={"rows": 3}),
        }


class AppointmentEditForm(forms.ModelForm):
    client = ClientChoiceField(label="Клиент", queryset=Client.objects.none())
    car = CarChoiceField(
        label="Автомобиль", queryset=Car.objects.none(), required=False,
        empty_label="Без автомобиля",
    )
    services = forms.ModelMultipleChoiceField(
        label="Услуги", queryset=Service.objects.none(), widget=forms.CheckboxSelectMultiple
    )
    employees = forms.ModelMultipleChoiceField(
        label="Исполнители", queryset=Employee.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    other_price = forms.DecimalField(label="Стоимость услуги «Другое»", min_value=0, required=False)
    other_duration_minutes = forms.IntegerField(label="Длительность услуги «Другое», минут", min_value=1, required=False)
    car_vehicle_class = forms.ModelChoiceField(
        label="Класс автомобиля *", queryset=VehicleClass.objects.none(), required=False,
    )
    end_at = forms.DateTimeField(
        label="Окончание", required=False,
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )
    additional_employees = forms.ModelMultipleChoiceField(
        label="Дополнительные исполнители", queryset=Employee.objects.none(),
        required=False, widget=forms.CheckboxSelectMultiple,
    )
    primary_share_percent = forms.DecimalField(
        label="Доля зарплаты основного исполнителя, %", min_value=0, max_value=100,
        decimal_places=2, initial=100, widget=forms.HiddenInput,
    )

    def __init__(self, *args, tenant=None, location=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.location = location
        ensure_system_services(tenant, location)
        self.fields["client"].queryset = Client.objects.filter(tenant=tenant, archived_at__isnull=True)
        self.fields["car"].queryset = Car.objects.filter(tenant=tenant, archived_at__isnull=True).select_related("client")
        self.fields["employee"].queryset = Employee.objects.filter(
            tenant=tenant, location=location, active=True,
        )
        self.fields["employee"].required = False
        self.fields["employee"].widget = forms.HiddenInput()
        self.fields["employees"].queryset = self.fields["employee"].queryset
        self.fields["additional_employees"].queryset = self.fields["employee"].queryset
        self.fields["post"].queryset = WorkPost.objects.filter(
            tenant=tenant, location=location, active=True,
        )
        self.fields["post"].required = False
        self.fields["post"].empty_label = "Без назначения поста"
        self.fields["services"].queryset = Service.objects.filter(
            tenant=tenant, location=location, active=True,
        ).prefetch_related(
            "vehicle_class_prices"
        )
        _setup_vehicle_class_fields(self, tenant)
        self.financial_locked = bool(
            self.instance.pk and self.instance.status == Appointment.Status.COMPLETED
        )
        for name in ("primary_share_percent", "discount_kind", "discount_value", "discount_reason", "free_service_reason"):
            self.fields[name].required = False
        if self.instance.pk and not self.is_bound:
            self.initial["services"] = list(self.instance.items.values_list("service_id", flat=True))
            other_item = self.instance.items.filter(service__system_key="other").first()
            if other_item:
                self.initial["other_price"] = other_item.price_snapshot
                self.initial["other_duration_minutes"] = other_item.duration_minutes_snapshot
            assignments = list(self.instance.assignees.all())
            primary = next((item for item in assignments if item.is_primary), None)
            self.initial["primary_share_percent"] = primary.salary_share_percent if primary else 100
            self.initial["additional_employees"] = [
                item.employee_id for item in assignments if not item.is_primary
            ]
            self.initial["employees"] = [item.employee_id for item in assignments] or [self.instance.employee_id]
        if self.financial_locked:
            self.initial["services"] = list(self.instance.items.values_list("service_id", flat=True))
            locked_other = self.instance.items.filter(service__system_key="other").first()
            if locked_other:
                self.initial["other_price"] = locked_other.price_snapshot
                self.initial["other_duration_minutes"] = locked_other.duration_minutes_snapshot
            for name in (
                "services", "car_vehicle_class", "other_price", "other_duration_minutes",
                "discount_kind", "discount_value", "discount_reason", "free_service_reason",
            ):
                self.fields[name].disabled = True
            for name in [name for name in self.fields if name.startswith("service_class_")]:
                self.fields[name].disabled = True

    def clean(self):
        cleaned = super().clean()
        client, car = cleaned.get("client"), cleaned.get("car")
        employee, post = cleaned.get("employee"), cleaned.get("post")
        start_at, services = cleaned.get("start_at"), cleaned.get("services")
        vehicle_class, service_classes = _clean_vehicle_class_fields(self, cleaned)
        service_quantities = _clean_service_quantities(self, cleaned)
        additional_employees = list(cleaned.get("additional_employees") or [])
        if client and car and car.client_id != client.id:
            self.add_error("car", "Автомобиль не принадлежит выбранному клиенту.")
        if start_at and services:
            other = next((service for service in services if service.system_key == "other"), None)
            if other:
                if cleaned.get("other_price") is None:
                    self.add_error("other_price", "Укажите стоимость услуги.")
                if not cleaned.get("other_duration_minutes"):
                    self.add_error("other_duration_minutes", "Укажите длительность услуги.")
                if not (cleaned.get("comment") or "").strip():
                    self.add_error("comment", "Для услуги «Другое» обязательно опишите работу.")
            if any((cleaned.get("other_price") if service.system_key == "other" else service.price_for_class(service_classes.get(str(service.id)))) == 0
                   for service in services) and not (
                cleaned.get("free_service_reason") or ""
            ).strip():
                self.add_error("free_service_reason", "Укажите причину бесплатной работы.")
            if not cleaned.get("end_at"):
                cleaned["end_at"] = start_at + timedelta(minutes=sum(
                    (cleaned.get("other_duration_minutes") or service.duration_minutes)
                    if service.system_key == "other" else (
                        service.duration_for_class(service_classes.get(str(service.id)))
                        * service_quantities.get(str(service.id), 1)
                    )
                    for service in services
                ))
        end_at = cleaned.get("end_at")
        if start_at and end_at and end_at <= start_at:
            self.add_error("end_at", "Окончание должно быть позже начала.")
        discount_kind = cleaned.get("discount_kind") or Appointment.DiscountKind.NONE
        discount_value = cleaned.get("discount_value") or 0
        if discount_kind != Appointment.DiscountKind.NONE and discount_value:
            if not (cleaned.get("discount_reason") or "").strip():
                self.add_error("discount_reason", "Укажите причину скидки.")
            if discount_kind == Appointment.DiscountKind.PERCENT and discount_value > 100:
                self.add_error("discount_value", "Процент скидки не может быть больше 100.")
        for obj, field in ((client, "client"), (car, "car"), (employee, "employee"), (post, "post")):
            if obj and obj.tenant_id != self.tenant.id:
                self.add_error(field, "Объект принадлежит другой компании.")
        selected_employees = list(cleaned.get("employees") or [])
        legacy_mode = not self.data.getlist("employees")
        if not selected_employees and employee:
            selected_employees = [employee, *[item for item in additional_employees if item.id != employee.id]]
        if not selected_employees:
            self.add_error("employees", "Выберите хотя бы одного исполнителя.")
            cleaned["employee_shares"] = {}
            return cleaned
        employee = employee if employee in selected_employees else selected_employees[0]
        additional_employees = [item for item in selected_employees if item.id != employee.id]
        cleaned["employee"] = employee
        cleaned["additional_employees"] = additional_employees
        manual = self.data.get("custom_salary_split") == "1" or legacy_mode
        shares = {}
        if manual:
            existing_shares = {
                str(row.employee_id): row.salary_share_percent for row in self.instance.assignees.all()
            }
            primary_share = _share_value(
                self, self.data.get(f"employee_share_{employee.id}", self.data.get(
                    "primary_share_percent", existing_shares.get(str(employee.id), "100")
                )), employee
            )
            total_share = primary_share
            for item in additional_employees:
                share = _share_value(
                    self, self.data.get(f"employee_share_{item.id}", existing_shares.get(str(item.id), "0")), item
                )
                shares[str(item.id)] = share
                total_share += share
            if total_share != 100:
                self.add_error("employees", "Сумма долей участия всех исполнителей должна быть 100%.")
        else:
            count = Decimal(len(selected_employees))
            equal = (Decimal("100") / count).quantize(Decimal("0.01"))
            primary_share = Decimal("100") - equal * Decimal(len(additional_employees))
            shares = {str(item.id): equal for item in additional_employees}
        cleaned["primary_share_percent"] = primary_share
        cleaned["employee_shares"] = shares
        _validate_employee_services(self, selected_employees, services)
        _validate_working_time(self, selected_employees, start_at, cleaned.get("end_at"))
        return cleaned

    class Meta:
        model = Appointment
        fields = (
            "client", "car", "employee", "employees", "additional_employees", "primary_share_percent",
            "post", "services", "start_at", "end_at", "comment",
            "discount_kind", "discount_value", "discount_reason", "free_service_reason",
        )
        widgets = {
            "start_at": forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
            "comment": forms.Textarea(attrs={"rows": 3}),
        }


class PaymentTransactionForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].label = "Операция"
        self.fields["method"].label = "Способ оплаты"
        self.fields["method"].choices = [("", "Выберите способ оплаты"), *Appointment.PaymentMethod.choices]
        self.fields["amount"].label = "Сумма"
        self.fields["comment"].label = "Комментарий"

    class Meta:
        model = PaymentTransaction
        fields = ("kind", "method", "amount", "comment")


class PaymentSettingsForm(forms.ModelForm):
    class Meta:
        model = PaymentSettings
        fields = ("qr_fee_percent", "cash_fee_percent", "card_fee_percent", "transfer_fee_percent")
        labels = {
            "qr_fee_percent": "Комиссия QR, %", "cash_fee_percent": "Комиссия наличных, %",
            "card_fee_percent": "Комиссия карты, %", "transfer_fee_percent": "Комиссия перевода, %",
        }


class CashShiftOpenForm(forms.ModelForm):
    class Meta:
        model = CashShift
        fields = ("opening_cash",)
        labels = {"opening_cash": "Наличные в кассе на начало смены"}


class CashShiftCloseForm(forms.Form):
    actual_cash = forms.DecimalField(label="Фактические наличные в кассе", min_value=0)
    comment = forms.CharField(label="Комментарий", max_length=240, required=False)


class CashCollectionForm(forms.ModelForm):
    class Meta:
        model = CashCollection
        fields = ("amount", "comment")


class AppointmentDeleteForm(forms.Form):
    reason = forms.CharField(
        label="Причина удаления", max_length=300,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Обязательно укажите причину"}),
    )


class ComplaintForm(forms.ModelForm):
    photos = MultipleFileField(
        label="Фотографии", required=False,
        widget=MultipleFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        help_text="До 5 фотографий, каждая не более 10 МБ.",
    )

    def __init__(self, *args, tenant=None, location=None, existing_photo_count=0, advanced=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.existing_photo_count = existing_photo_count
        self.fields["appointment"].queryset = Appointment.objects.filter(
            tenant=tenant, status=Appointment.Status.COMPLETED
        ).select_related("client", "car").order_by("-start_at")
        self.fields["appointment"].queryset = self.fields["appointment"].queryset.filter(
            tenant=tenant, location=location,
        )
        self.fields["assigned_to"].queryset = Employee.objects.filter(
            tenant=tenant, location=location, active=True,
        )
        self.fields["service"].queryset = Service.objects.filter(
            tenant=tenant, location=location, active=True,
        )
        self.fields["service"].required = False
        self.fields["appointment"].empty_label = "Выберите запись"
        self.fields["appointment"].label_from_instance = lambda appointment: (
            f"{timezone.localtime(appointment.start_at):%d.%m %H:%M} — "
            f"{appointment.client_display_name} · {appointment.car_display}"
        )
        self.fields["assigned_to"].empty_label = "Не назначен"
        self.fields["service"].empty_label = "Без привязки к услуге"
        if not advanced:
            for name in (
                "service", "assigned_to", "reason", "deadline", "is_rework",
                "financial_loss", "resolution", "photos",
            ):
                self.fields.pop(name, None)

    def clean_photos(self):
        photos = self.cleaned_data.get("photos") or []
        if self.existing_photo_count + len(photos) > 5:
            raise forms.ValidationError("У одной претензии может быть не больше 5 фотографий.")
        allowed = {"image/jpeg", "image/png", "image/webp"}
        for photo in photos:
            if photo.size > 10 * 1024 * 1024:
                raise forms.ValidationError(f"Файл «{photo.name}» больше 10 МБ.")
            if getattr(photo, "content_type", "") not in allowed:
                raise forms.ValidationError(f"Файл «{photo.name}» должен быть JPG, PNG или WebP.")
        return photos

    class Meta:
        model = Complaint
        fields = (
            "appointment", "reason", "service", "description", "status", "assigned_to", "deadline",
            "is_rework", "rework_completed_at", "resolution", "financial_loss", "photos",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "resolution": forms.Textarea(attrs={"rows": 3}),
            "deadline": forms.DateInput(attrs={"type": "date"}),
            "rework_completed_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }
        labels = {
            "appointment": "Запись", "reason": "Причина обращения", "service": "Услуга",
            "description": "Описание претензии", "status": "Статус",
            "assigned_to": "Ответственный", "deadline": "Срок решения",
            "is_rework": "Требуется переделка", "rework_completed_at": "Переделка выполнена",
            "resolution": "Решение", "financial_loss": "Финансовые потери",
        }

    def clean(self):
        cleaned = super().clean()
        appointment = cleaned.get("appointment")
        if appointment and appointment.tenant_id != self.tenant.id:
            self.add_error("appointment", "Запись принадлежит другой компании.")
        service = cleaned.get("service")
        if appointment and service and not appointment.items.filter(service=service).exists():
            self.add_error("service", "Эта услуга не входила в выбранную запись.")
        if cleaned.get("status") == Complaint.Status.RESOLVED and not (cleaned.get("resolution") or "").strip():
            self.add_error("resolution", "Для закрытия укажите принятое решение.")
        return cleaned


class ClientRatingForm(forms.ModelForm):
    class Meta:
        model = ClientRating
        fields = ("score", "comment")
        widgets = {
            "score": forms.RadioSelect(choices=[(i, str(i)) for i in range(1, 6)]),
            "comment": forms.Textarea(attrs={"rows": 2}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("score") and cleaned["score"] <= 3 and not (cleaned.get("comment") or "").strip():
            self.add_error("comment", "Для оценки 3 и ниже укажите комментарий клиента.")
        return cleaned
