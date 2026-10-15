from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from zoneinfo import ZoneInfo

from apps.customers.models import Car, Client, normalize_phone, normalize_plate
from apps.staff.models import Employee, EmployeeService, EmployeeShift, unsupported_employee_services
from apps.tenants.models import ensure_working_hours
from .models import Appointment, AppointmentAssignee, AppointmentService


def _consumables_snapshot(service, vehicle_class=None, quantity=1):
    multiplier = Decimal(quantity)
    return [
        {
            "consumable_id": str(norm.consumable_id),
            "name": norm.consumable.name,
            "quantity": str(norm.quantity * multiplier),
            "stock_quantity": str(norm.stock_quantity * multiplier),
            "unit": norm.consumable.unit,
            "unit_cost": str(norm.consumable.cost_per_unit),
            "total_cost": str(norm.total_cost * multiplier),
        }
        for norm in service.norms_for_class(vehicle_class)
    ]


def _service_values(service, cleaned_data):
    if service.system_key == "other":
        return cleaned_data["other_price"], cleaned_data["other_duration_minutes"]
    vehicle_class = cleaned_data.get("service_classes", {}).get(str(service.id)) or getattr(cleaned_data.get("car"), "vehicle_class", None)
    quantity = cleaned_data.get("service_quantities", {}).get(str(service.id), 1)
    return (
        service.price_for_class(vehicle_class) * quantity,
        service.duration_for_class(vehicle_class) * quantity,
    )


def _pop_vehicle_class_data(cleaned_data, services):
    vehicle_class = cleaned_data.pop("car_vehicle_class", None)
    service_classes = cleaned_data.pop("service_classes", {})
    for key in [key for key in cleaned_data if key.startswith("service_class_")]:
        cleaned_data.pop(key, None)
    return vehicle_class, service_classes


def _discount_values(subtotal, kind, value):
    value = value or Decimal("0")
    if kind == Appointment.DiscountKind.PERCENT:
        amount = subtotal * min(value, Decimal("100")) / Decimal("100")
    elif kind == Appointment.DiscountKind.FIXED:
        amount = min(value, subtotal)
    else:
        return Decimal("0"), Decimal("0")
    percent = amount / subtotal * Decimal("100") if subtotal else Decimal("0")
    return amount.quantize(Decimal("0.01")), percent


def _rate_snapshot_for_employee(appointment, employee):
    settings = {
        row.service_id: row.effective_percent
        for row in EmployeeService.objects.filter(
            employee=employee, service_id__in=appointment.items.values("service_id")
        ).select_related("service")
    }
    defaults = {
        item.service_id: item.employee_percent_snapshot for item in appointment.items.all()
    }
    return {
        str(service_id): str(settings.get(service_id, percent))
        for service_id, percent in defaults.items()
    }


def _sync_assignees(appointment, primary, primary_share, additional, shares):
    appointment.assignees.all().delete()
    rows = [AppointmentAssignee(
        tenant=appointment.tenant, appointment=appointment, employee=primary,
        salary_share_percent=primary_share, is_primary=True,
        service_percentages_snapshot=_rate_snapshot_for_employee(appointment, primary),
    )]
    rows.extend(AppointmentAssignee(
        tenant=appointment.tenant, appointment=appointment, employee=employee,
        salary_share_percent=shares[str(employee.id)], is_primary=False,
        service_percentages_snapshot=_rate_snapshot_for_employee(appointment, employee),
    ) for employee in additional)
    AppointmentAssignee.objects.bulk_create(rows)


def _pop_assignment_data(cleaned_data):
    cleaned_data.pop("employees", None)
    return (
        list(cleaned_data.pop("additional_employees", [])),
        cleaned_data.pop("primary_share_percent", Decimal("100")),
        cleaned_data.pop("employee_shares", {}),
    )


def _pop_discount_data(cleaned_data):
    return {
        "kind": cleaned_data.pop("discount_kind", None) or Appointment.DiscountKind.NONE,
        "value": cleaned_data.pop("discount_value", None) or Decimal("0"),
        "reason": cleaned_data.pop("discount_reason", ""),
    }


def _validate_employee_interval(tenant, employees, start_at, end_at):
    tenant_tz = ZoneInfo(tenant.timezone)
    local_start = timezone.localtime(start_at, tenant_tz)
    local_end = timezone.localtime(end_at, tenant_tz)
    hours = ensure_working_hours(tenant)[local_start.weekday()]
    if local_start.date() != local_end.date() or hours.is_closed or not (
        hours.opens_at <= local_start.time() and local_end.time() <= hours.closes_at
    ):
        raise ValidationError("Запись должна полностью попадать в часы работы компании.")
    shifts = {
        row.employee_id: row for row in EmployeeShift.objects.filter(
            tenant=tenant, employee__in=employees, date=local_start.date()
        )
    }
    unavailable = [
        employee.name for employee in employees
        if employee.id not in shifts or local_start.time() < shifts[employee.id].start_time
        or local_end.time() > shifts[employee.id].end_time
    ]
    if unavailable:
        raise ValidationError("В выбранное время не работают: " + ", ".join(unavailable) + ".")


def _validate_employee_services(tenant, employees, services):
    unsupported = unsupported_employee_services(tenant, employees, services)
    if not unsupported:
        return
    details = "; ".join(
        f"{employee.name}: {', '.join(service.name for service in employee_services)}"
        for employee, employee_services in unsupported.items()
    )
    raise ValidationError(
        "Исполнитель не может выполнять выбранные услуги — " + details + "."
    )


@transaction.atomic
def create_appointment(*, tenant, cleaned_data, actor=None, discount_limit=None, location=None):
    entered = {key: cleaned_data.pop(key, "") for key in (
        "client_first_name", "client_last_name", "client_phone",
        "car_plate", "car_description", "car_make", "car_model",
    )}
    if entered["car_description"] and not (entered["car_make"] or entered["car_model"]):
        parts = entered["car_description"].strip().split(maxsplit=1)
        entered["car_make"] = parts[0]
        entered["car_model"] = parts[1] if len(parts) > 1 else ""
    services = list(cleaned_data.pop("services"))
    vehicle_class, service_classes = _pop_vehicle_class_data(cleaned_data, services)
    cleaned_data["service_classes"] = service_classes
    client = cleaned_data.get("client")
    car = cleaned_data.get("car")
    if not client:
        client = Client.objects.filter(
            tenant=tenant, archived_at__isnull=True,
            normalized_phone=normalize_phone(entered["client_phone"]),
        ).first()
    if not client:
        client = Client(
            tenant=tenant, name=entered["client_first_name"].strip(),
            last_name=entered["client_last_name"].strip(), phone=entered["client_phone"],
        )
        client.full_clean()
        client.save()
    if not car and entered["car_plate"]:
        car = Car.objects.filter(
            tenant=tenant, archived_at__isnull=True,
            normalized_plate=normalize_plate(entered["car_plate"]),
        ).first()
    if not car and (entered["car_make"] or entered["car_model"]):
        matching_cars = Car.objects.filter(
            tenant=tenant, client=client, archived_at__isnull=True,
        )
        if entered["car_make"]:
            matching_cars = matching_cars.filter(make__iexact=entered["car_make"].strip())
        if entered["car_model"]:
            matching_cars = matching_cars.filter(model__iexact=entered["car_model"].strip())
        car = matching_cars.first()
    if car and car.client_id != client.id:
        raise ValidationError("Этот госномер уже привязан к другому клиенту.")
    has_vehicle_data = any((entered["car_plate"], entered["car_make"], entered["car_model"]))
    if not car and has_vehicle_data:
        car = Car(
            tenant=tenant, client=client, make=entered["car_make"].strip(),
            model=entered["car_model"].strip(), plate=entered["car_plate"],
            vehicle_class=vehicle_class,
        )
        car.full_clean()
        car.save()
    elif car and vehicle_class and car.vehicle_class_id != vehicle_class.id:
        car.vehicle_class = vehicle_class
        car.save(update_fields=("vehicle_class", "updated_at"))
    cleaned_data["client"] = client
    cleaned_data["car"] = car

    additional, primary_share, shares = _pop_assignment_data(cleaned_data)
    _validate_employee_services(
        tenant, [cleaned_data.get("employee"), *additional], services
    )
    discount = _pop_discount_data(cleaned_data)
    service_values = {service.id: _service_values(service, cleaned_data) for service in services}
    service_quantities = cleaned_data.pop("service_quantities", {})
    cleaned_data.pop("service_classes", None)
    cleaned_data.pop("other_price", None)
    cleaned_data.pop("other_duration_minutes", None)
    subtotal = sum((service_values[service.id][0] for service in services), start=Decimal("0"))
    discount_amount, discount_percent = _discount_values(subtotal, discount["kind"], discount["value"])
    created_by_employee = None
    if actor and getattr(actor, "is_authenticated", False):
        created_by_employee = Employee.objects.filter(tenant=tenant, user=actor).first()
    appointment = Appointment.objects.create(
        tenant=tenant,
        location=location,
        created_by_employee=created_by_employee,
        client_name_snapshot=client.display_name,
        client_phone_snapshot=client.phone,
        car_snapshot=str(car) if car else "",
        car_make_snapshot=car.make if car else "",
        car_model_snapshot=car.model if car else "",
        car_plate_snapshot=car.plate if car else "",
        subtotal=subtotal,
        total=subtotal - discount_amount,
        discount_kind=discount["kind"], discount_value=discount["value"],
        discount_amount=discount_amount,
        discount_reason=discount["reason"],
        discount_status=(Appointment.DiscountStatus.APPLIED if discount_amount else Appointment.DiscountStatus.NONE),
        discount_requested_by=actor if discount_amount else None,
        discount_approved_by=actor if discount_amount else None,
        status=Appointment.Status.WAITING,
        **cleaned_data,
    )
    AppointmentService.objects.bulk_create(
        [
            AppointmentService(
                tenant=tenant,
                appointment=appointment,
                service=service,
                quantity=service_quantities.get(str(service.id), 1),
                vehicle_class=service_classes.get(str(service.id)),
                vehicle_class_name_snapshot=(
                    service_classes[str(service.id)].name if str(service.id) in service_classes else ""
                ),
                name_snapshot=service.name,
                price_snapshot=service_values[service.id][0],
                duration_minutes_snapshot=service_values[service.id][1],
                employee_percent_snapshot=service.employee_percent,
                consumables_snapshot=_consumables_snapshot(
                    service,
                    service_classes.get(str(service.id)) or (
                        appointment.car.vehicle_class if appointment.car_id else vehicle_class
                    ),
                    service_quantities.get(str(service.id), 1),
                ),
            )
            for service in services
        ]
    )
    _sync_assignees(
        appointment, appointment.employee, primary_share, additional, shares
    )
    return appointment

@transaction.atomic
def update_appointment(*, appointment, cleaned_data, actor=None, discount_limit=None, lock_financial=False):
    services = list(cleaned_data.pop("services"))
    vehicle_class, service_classes = _pop_vehicle_class_data(cleaned_data, services)
    cleaned_data["service_classes"] = service_classes
    additional, primary_share, shares = _pop_assignment_data(cleaned_data)
    _validate_employee_services(
        appointment.tenant, [cleaned_data.get("employee"), *additional], services
    )
    discount = _pop_discount_data(cleaned_data)
    if lock_financial:
        for name in ("service_classes", "service_quantities", "other_price", "other_duration_minutes"):
            cleaned_data.pop(name, None)
        for field in ("client", "car", "employee", "post", "start_at", "end_at", "comment"):
            setattr(appointment, field, cleaned_data.get(field))
        appointment.save()
        _sync_assignees(appointment, appointment.employee, primary_share, additional, shares)
        return appointment
    service_values = {service.id: _service_values(service, cleaned_data) for service in services}
    service_quantities = cleaned_data.pop("service_quantities", {})
    cleaned_data.pop("service_classes", None)
    cleaned_data.pop("other_price", None)
    cleaned_data.pop("other_duration_minutes", None)
    for field in ("client", "car", "employee", "post", "start_at", "end_at", "comment"):
        setattr(appointment, field, cleaned_data.get(field))
    if vehicle_class and appointment.car_id and appointment.car.vehicle_class_id != vehicle_class.id:
        appointment.car.vehicle_class = vehicle_class
        appointment.car.save(update_fields=("vehicle_class", "updated_at"))
    subtotal = sum((service_values[service.id][0] for service in services), start=Decimal("0"))
    discount_amount, discount_percent = _discount_values(subtotal, discount["kind"], discount["value"])
    appointment.subtotal = subtotal
    appointment.discount_kind = discount["kind"]
    appointment.discount_value = discount["value"]
    appointment.discount_amount = discount_amount
    appointment.discount_reason = discount["reason"]
    appointment.discount_status = (
        Appointment.DiscountStatus.APPLIED if discount_amount else Appointment.DiscountStatus.NONE
    )
    appointment.discount_requested_by = actor if discount_amount else None
    appointment.discount_approved_by = actor if discount_amount else None
    appointment.total = subtotal - discount_amount
    appointment.client_name_snapshot = appointment.client.display_name if appointment.client_id else ""
    appointment.client_phone_snapshot = appointment.client.phone if appointment.client_id else ""
    appointment.car_snapshot = str(appointment.car) if appointment.car_id else ""
    appointment.car_make_snapshot = appointment.car.make if appointment.car_id else ""
    appointment.car_model_snapshot = appointment.car.model if appointment.car_id else ""
    appointment.car_plate_snapshot = appointment.car.plate if appointment.car_id else ""
    appointment.save()
    appointment.items.all().delete()
    AppointmentService.objects.bulk_create([
        AppointmentService(
            tenant=appointment.tenant, appointment=appointment, service=service,
            quantity=service_quantities.get(str(service.id), 1),
            vehicle_class=service_classes.get(str(service.id)),
            vehicle_class_name_snapshot=(
                service_classes[str(service.id)].name if str(service.id) in service_classes else ""
            ),
            name_snapshot=service.name, price_snapshot=service_values[service.id][0],
            duration_minutes_snapshot=service_values[service.id][1],
            employee_percent_snapshot=service.employee_percent,
            consumables_snapshot=_consumables_snapshot(
                service,
                service_classes.get(str(service.id)) or (
                    appointment.car.vehicle_class if appointment.car_id else vehicle_class
                ),
                service_quantities.get(str(service.id), 1),
            ),
        ) for service in services
    ])
    _sync_assignees(appointment, appointment.employee, primary_share, additional, shares)
    return appointment


@transaction.atomic
def move_appointment(*, appointment, employee, start_at, assignment=None):
    duration = appointment.end_at - appointment.start_at
    end_at = start_at + duration
    assignments = list(appointment.assignees.select_related("employee"))
    replaced = assignment or next((row for row in assignments if row.is_primary), None)
    selected = [row.employee for row in assignments if not replaced or row.id != replaced.id]
    if employee not in selected:
        selected.append(employee)
    services = [item.service for item in appointment.items.select_related("service")]
    _validate_employee_services(appointment.tenant, selected, services)
    _validate_employee_interval(appointment.tenant, selected, start_at, end_at)
    appointment.start_at = start_at
    appointment.end_at = end_at
    if assignment is None or assignment.is_primary:
        appointment.employee = employee
    appointment.save(update_fields=("employee", "start_at", "end_at", "updated_at"))
    if assignment is not None and not assignment.is_primary:
        if appointment.assignees.exclude(pk=assignment.pk).filter(employee=employee).exists():
            raise ValidationError("Этот сотрудник уже назначен на запись.")
        assignment.employee = employee
        assignment.service_percentages_snapshot = _rate_snapshot_for_employee(appointment, employee)
        assignment.save(update_fields=("employee", "service_percentages_snapshot"))
        return appointment
    primary = appointment.assignees.filter(is_primary=True).first()
    if primary and primary.employee_id != employee.id:
        existing = appointment.assignees.filter(employee=employee).first()
        if existing:
            primary.is_primary = False
            primary.save(update_fields=("is_primary",))
            existing.is_primary = True
            existing.save(update_fields=("is_primary",))
        else:
            primary.employee = employee
            primary.service_percentages_snapshot = _rate_snapshot_for_employee(appointment, employee)
            primary.save(update_fields=("employee", "service_percentages_snapshot"))
    elif not primary:
        AppointmentAssignee.objects.create(
            tenant=appointment.tenant, appointment=appointment, employee=employee,
            salary_share_percent=100, is_primary=True,
            service_percentages_snapshot=_rate_snapshot_for_employee(appointment, employee),
        )
    return appointment


@transaction.atomic
def move_appointment_post(*, appointment, post, start_at):
    from apps.catalog.models import WorkPost
    from apps.tenants.models import Tenant
    # Same tenant lock as online bookings: concurrent reservations cannot pass together.
    Tenant.objects.select_for_update().get(pk=appointment.tenant_id)
    appointment=Appointment.objects.select_for_update().get(pk=appointment.pk, tenant_id=appointment.tenant_id)
    if post:
        post=WorkPost.objects.filter(pk=post.pk, tenant_id=appointment.tenant_id, location_id=appointment.location_id, active=True).first()
        if post is None:
            raise ValidationError("Рабочий пост больше недоступен. Выберите другой пост.")
    end_at=start_at+(appointment.end_at-appointment.start_at)
    employees=[row.employee for row in appointment.assignees.select_related("employee")]
    if not employees and appointment.employee_id:
        employees=[appointment.employee]
    _validate_employee_interval(appointment.tenant, employees, start_at, end_at)
    if post and Appointment.objects.filter(tenant_id=appointment.tenant_id, post=post,
            start_at__lt=end_at, end_at__gt=start_at).exclude(pk=appointment.pk).exclude(status=Appointment.Status.NO_SHOW).exists():
        raise ValidationError("Рабочий пост занят в выбранное время.")
    # Moving vertically must also respect all assigned employees' availability.
    from django.db.models import Q
    employee_ids=[row.pk for row in employees]
    if Appointment.objects.filter(tenant_id=appointment.tenant_id, start_at__lt=end_at, end_at__gt=start_at).filter(
            Q(employee_id__in=employee_ids)|Q(assignees__employee_id__in=employee_ids)).exclude(pk=appointment.pk).exclude(status=Appointment.Status.NO_SHOW).exists():
        raise ValidationError("Исполнитель занят в выбранное время.")
    appointment.post=post
    appointment.start_at=start_at
    appointment.end_at=end_at
    appointment.save(update_fields=("post","start_at","end_at","updated_at"))
    return appointment
