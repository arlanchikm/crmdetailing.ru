import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.shortcuts import render
from django.utils import timezone

from apps.operations.models import Appointment
from apps.staff.models import Employee
from apps.tenants.access import tenant_permission_required

from .services import (
    appointment_commission_rows, appointment_salary, assignment_rows,
    commission_shifts, fixed_salary_for_period, service_salary_rate,
)


def _default_period(today):
    if today.day <= 15:
        return today.replace(day=1), today.replace(day=15)
    return today.replace(day=16), today.replace(day=calendar.monthrange(today.year, today.month)[1])


def _period(request):
    start, end = _default_period(timezone.localdate())
    try:
        custom_start = date.fromisoformat(request.GET.get("start", ""))
        custom_end = date.fromisoformat(request.GET.get("end", ""))
        if custom_start <= custom_end:
            return custom_start, custom_end
    except ValueError:
        pass
    return start, end


@tenant_permission_required("payroll_view")
def payroll_dashboard(request):
    start, end = _period(request)
    appointments = list(
        Appointment.objects.filter(
            tenant=request.tenant, location=request.current_location, employee__isnull=False,
            status=Appointment.Status.COMPLETED,
            payment_status=Appointment.PaymentStatus.PAID,
            start_at__date__gte=start, start_at__date__lte=end,
        ).select_related("employee", "client", "car", "created_by_employee", "tenant").prefetch_related(
            "items__service", "assignees__employee"
        )
    )
    by_employee = defaultdict(list)
    zero_rate_services = set()
    assignment_share = {}
    commissions = defaultdict(list)
    shifts = commission_shifts(request.tenant, start, end)
    for appointment in appointments:
        for employee, share in assignment_rows(appointment):
            by_employee[employee.id].append(appointment)
            assignment_share[(appointment.id, employee.id)] = share
        for employee, amount, share in appointment_commission_rows(appointment, shifts=shifts):
            commissions[employee.id].append({
                "appointment": appointment, "salary": amount, "share": share,
            })
    rows = []
    for employee in Employee.objects.filter(
        tenant=request.tenant, location=request.current_location, active=True,
    ).select_related("company_role"):
        detail, services = [], defaultdict(lambda: {"count": 0, "amount": Decimal("0")})
        fixed_salary = fixed_salary_for_period(employee, start, end)
        salary = fixed_salary
        for appointment in by_employee[employee.id]:
            share = assignment_share[(appointment.id, employee.id)]
            row_salary = appointment_salary(appointment, employee, share)
            salary += row_salary
            if employee.salary_mode in (
                Employee.SalaryMode.SERVICE_PERCENT,
                Employee.SalaryMode.FIXED_PLUS_APPOINTMENT_PERCENT,
            ):
                for item in appointment.items.all():
                    rate = service_salary_rate(appointment, employee, item)
                    if rate == 0:
                        zero_rate_services.add(item.name_snapshot)
                    amount = item.price_snapshot * rate / Decimal("100") * share / Decimal("100")
                    services[item.name_snapshot]["count"] += item.quantity
                    services[item.name_snapshot]["amount"] += amount
                    services[item.name_snapshot]["rate"] = rate
            detail.append({"appointment": appointment, "salary": row_salary, "share": share})
        commission_rows = commissions[employee.id]
        salary += sum((item["salary"] for item in commission_rows), Decimal("0"))
        rows.append({
            "employee": employee,
            "cars": len(
                {item.id for item in by_employee[employee.id]}
                | {item["appointment"].id for item in commission_rows}
            ),
            "salary": salary,
            "appointments": detail, "booking_commissions": commission_rows,
            "services": [{"name": name, **values} for name, values in services.items()],
            "fixed_salary": fixed_salary,
        })
    return render(request, "payroll/dashboard.html", {
        "period_start": start, "period_end": end, "rows": rows,
        "total_salary": sum((row["salary"] for row in rows), Decimal("0")),
        "total_cars": sum(row["cars"] for row in rows),
        "zero_rate_services": sorted(zero_rate_services),
    })
