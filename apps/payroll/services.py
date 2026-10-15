import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from apps.operations.models import Appointment
from apps.staff.models import Employee, EmployeeShift
from django.utils import timezone


def fixed_salary_for_period(employee, start, end):
    if employee.salary_mode not in (
        Employee.SalaryMode.FIXED_MONTHLY, Employee.SalaryMode.FIXED_PLUS_PERCENT,
        Employee.SalaryMode.FIXED_PLUS_APPOINTMENT_PERCENT,
    ):
        return Decimal("0")
    total = Decimal("0")
    cursor = start.replace(day=1)
    while cursor <= end:
        month_days = calendar.monthrange(cursor.year, cursor.month)[1]
        month_end = cursor.replace(day=month_days)
        overlap_start, overlap_end = max(start, cursor), min(end, month_end)
        if overlap_start <= overlap_end:
            days = (overlap_end - overlap_start).days + 1
            total += employee.fixed_monthly_salary * Decimal(days) / Decimal(month_days)
        if month_end >= end:
            break
        cursor = (month_end + timedelta(days=1)).replace(day=1)
    return total.quantize(Decimal("0.01"))


def service_salary_rate(appointment, employee, item):
    assignment = next(
        (row for row in appointment.assignees.all() if row.employee_id == employee.id), None
    )
    snapshots = assignment.service_percentages_snapshot if assignment else {}
    rate = Decimal(str(snapshots.get(str(item.service_id), item.employee_percent_snapshot)))
    if rate == 0 and item.service.employee_percent > 0:
        rate = item.service.employee_percent
    return rate


def appointment_salary(appointment, employee, share_percent=Decimal("100")):
    share = share_percent / Decimal("100")
    if employee.salary_mode == Employee.SalaryMode.FIXED_PLUS_PERCENT:
        item_total = sum(
            (item.price_snapshot for item in appointment.items.all()), Decimal("0")
        )
        gross = appointment.subtotal or item_total or appointment.total
        return (
            gross * employee.salary_percent / Decimal("100") * share
        ).quantize(Decimal("0.01"))
    if employee.salary_mode == Employee.SalaryMode.FIXED_MONTHLY:
        return Decimal("0")
    amount = Decimal("0")
    for item in appointment.items.all():
        rate = service_salary_rate(appointment, employee, item)
        amount += item.price_snapshot * rate / Decimal("100")
    return (amount * share).quantize(Decimal("0.01"))


def commission_shifts(tenant, start, end):
    """Load shift eligibility once for a report; never cache across requests."""
    by_day = defaultdict(list)
    for shift in EmployeeShift.objects.filter(
        employee__tenant=tenant, employee__active=True,
        employee__salary_mode=Employee.SalaryMode.FIXED_PLUS_APPOINTMENT_PERCENT,
        date__gte=start, date__lte=end,
    ).select_related("employee"):
        by_day[(shift.employee.location_id, shift.date)].append(shift)
    return by_day


def appointment_commission_rows(appointment, *, shifts=None):
    """Return administrator booking commissions as (employee, amount, share) rows."""
    if (
        appointment.status != Appointment.Status.COMPLETED
        or appointment.payment_status != Appointment.PaymentStatus.PAID
    ):
        return []

    local_start = timezone.localtime(
        appointment.start_at, ZoneInfo(appointment.tenant.timezone)
    )
    if shifts is None:
        employees = list(Employee.objects.filter(
            tenant_id=appointment.tenant_id, location_id=appointment.location_id,
            active=True, salary_mode=Employee.SalaryMode.FIXED_PLUS_APPOINTMENT_PERCENT,
            shifts__date=local_start.date(), shifts__start_time__lte=local_start.time(),
            shifts__end_time__gt=local_start.time(),
        ).distinct())
    else:
        employees = list({shift.employee_id: shift.employee
            for shift in shifts.get((appointment.location_id, local_start.date()), ())
            if shift.start_time <= local_start.time() < shift.end_time}.values())
    if not employees:
        return []
    creator = appointment.created_by_employee
    if creator and any(employee.id == creator.id for employee in employees):
        employees = [creator]
    elif creator:
        return []
    elif appointment.source == "crm":
        # Legacy CRM records have no reliable author; do not treat them as external leads.
        return []
    divisor = Decimal(len(employees))
    share = (Decimal("100") / divisor).quantize(Decimal("0.01"))
    return [(
        employee,
        (appointment.subtotal * employee.salary_percent / Decimal("100") / divisor).quantize(Decimal("0.01")),
        share,
    ) for employee in employees]


def assignment_rows(appointment):
    rows = list(appointment.assignees.all())
    if rows:
        return [(row.employee, row.salary_share_percent) for row in rows]
    return [(appointment.employee, Decimal("100"))] if appointment.employee else []


def payroll_total_for_period(tenant, start, end):
    appointments = Appointment.objects.filter(
        tenant=tenant, status=Appointment.Status.COMPLETED,
        payment_status=Appointment.PaymentStatus.PAID,
        start_at__date__gte=start, start_at__date__lte=end,
    ).select_related("employee", "tenant", "created_by_employee").prefetch_related("items__service", "assignees__employee")
    total = Decimal("0")
    shifts = commission_shifts(tenant, start, end)
    for appointment in appointments:
        total += sum(
            (appointment_salary(appointment, employee, share) for employee, share in assignment_rows(appointment)),
            Decimal("0"),
        )
        total += sum((amount for _, amount, _ in appointment_commission_rows(appointment, shifts=shifts)), Decimal("0"))
    for employee in Employee.objects.filter(
        tenant=tenant, active=True,
        salary_mode__in=(
            Employee.SalaryMode.FIXED_MONTHLY,
            Employee.SalaryMode.FIXED_PLUS_PERCENT,
            Employee.SalaryMode.FIXED_PLUS_APPOINTMENT_PERCENT,
        ),
    ):
        total += fixed_salary_for_period(employee, start, end)
    return total.quantize(Decimal("0.01"))
