from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.db.models import Avg, Count, Q, Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.inventory.models import AppointmentConsumption
from apps.operations.models import Appointment, ClientRating, Complaint
from apps.staff.models import Employee
from apps.tenants.access import tenant_feature_required, tenant_permission_required
from apps.tenants.plans import tenant_has_feature
from apps.payroll.services import appointment_salary, assignment_rows, payroll_total_for_period
from apps.core.models import log_event

from .forms import ExpenseForm
from .models import Expense


def _period_bounds(period, date_from=None, date_to=None):
    today = timezone.localdate()
    period_error = ""
    if date_from or date_to:
        try:
            custom_start = date.fromisoformat(date_from or "")
            custom_end = date.fromisoformat(date_to or "")
            if custom_start > custom_end:
                raise ValueError
            exclusive_end = custom_end + timedelta(days=1)
        except (TypeError, ValueError, OverflowError):
            period_error = "Укажите корректный период: дата «С» не должна быть позже даты «По»."
        else:
            return "custom", custom_start, exclusive_end, period_error
    if period == "day":
        start = today
    elif period == "week":
        start = today - timedelta(days=today.weekday())
    else:
        period = "month"
        start = today.replace(day=1)
    return period, start, today + timedelta(days=1), period_error


@tenant_permission_required("analytics_view")
def analytics_dashboard(request):
    period, start, end, period_error = _period_bounds(
        request.GET.get("period", "month"),
        request.GET.get("date_from"),
        request.GET.get("date_to"),
    )
    period_appointments = list(
        Appointment.objects.filter(
            tenant=request.tenant,
            location=request.current_location,
            start_at__date__gte=start,
            start_at__date__lt=end,
        ).select_related("employee", "client", "car", "post").prefetch_related(
            "items__service", "assignees__employee"
        ).order_by("-start_at")
    )
    completed = [item for item in period_appointments if item.status == Appointment.Status.COMPLETED]
    revenue = sum(
        (appointment.total for appointment in completed if appointment.payment_status == Appointment.PaymentStatus.PAID),
        Decimal("0"),
    )
    paid_completed = [
        appointment for appointment in completed
        if appointment.payment_status == Appointment.PaymentStatus.PAID
    ]
    average_check = revenue / len(paid_completed) if paid_completed else Decimal("0")
    advanced_analytics = tenant_has_feature(request.tenant, "analytics_advanced")
    if not advanced_analytics:
        return render(request, "analytics/dashboard.html", {
            "period": period,
            "period_start": start,
            "period_end": end - timedelta(days=1),
            "period_error": period_error,
            "revenue": revenue,
            "average_check": average_check,
            "completed_count": len(completed),
            "appointment_count": len(period_appointments),
            "advanced_analytics": False,
        })
    writeoffs = AppointmentConsumption.objects.filter(
        tenant=request.tenant,
        appointment__location=request.current_location,
        appointment__start_at__date__gte=start,
        appointment__start_at__date__lt=end,
    )
    consumables_cost = writeoffs.aggregate(total=Sum("total_cost"))["total"] or Decimal("0")
    manual_expenses = Expense.objects.filter(
        tenant=request.tenant, location=request.current_location, date__gte=start, date__lt=end,
    )
    expense_groups = {key: Decimal("0") for key, _ in Expense.Category.choices}
    for row in manual_expenses.values("category").annotate(total=Sum("amount")):
        expense_groups[row["category"]] = row["total"]
    manual_total = sum(expense_groups.values(), Decimal("0"))
    payroll_cost = payroll_total_for_period(request.tenant, start, end - timedelta(days=1))
    payment_fees = sum((appointment.payment_fee for appointment in completed), Decimal("0"))
    discounts = sum((appointment.discount_amount for appointment in period_appointments
                     if appointment.discount_status == Appointment.DiscountStatus.APPLIED), Decimal("0"))

    service_rows = defaultdict(lambda: {
        "count": 0, "revenue": Decimal("0"), "minutes": 0,
        "material_cost": Decimal("0"), "salary_cost": Decimal("0"),
    })
    for appointment in completed:
        items = list(appointment.items.all())
        planned_total = sum((item.duration_minutes_snapshot for item in items), 0) or 1
        actual_minutes = (
            max(1, int((appointment.completed_at - appointment.work_started_at).total_seconds() / 60))
            if appointment.work_started_at and appointment.completed_at else planned_total
        )
        appointment_payroll = (
            sum(
                (appointment_salary(appointment, employee, share)
                 for employee, share in assignment_rows(appointment)),
                Decimal("0"),
            )
            if appointment.payment_status == Appointment.PaymentStatus.PAID else Decimal("0")
        )
        for item in items:
            row = service_rows[item.name_snapshot]
            row["count"] += item.quantity
            ratio = item.price_snapshot / appointment.subtotal if appointment.subtotal else Decimal("0")
            if appointment.payment_status == Appointment.PaymentStatus.PAID:
                row["revenue"] += appointment.total * ratio
            row["minutes"] += round(actual_minutes * item.duration_minutes_snapshot / planned_total)
            row["material_cost"] += sum(
                (Decimal(value.get("total_cost", "0")) for value in item.consumables_snapshot or []),
                Decimal("0"),
            )
            row["salary_cost"] += appointment_payroll * ratio
    attributed_salary = sum((row["salary_cost"] for row in service_rows.values()), Decimal("0"))
    unattributed_salary = max(payroll_cost - attributed_salary, Decimal("0"))
    if revenue and unattributed_salary:
        for row in service_rows.values():
            row["salary_cost"] += unattributed_salary * row["revenue"] / revenue
    service_metrics = []
    for name, row in sorted(service_rows.items(), key=lambda pair: pair[1]["revenue"], reverse=True):
        service_metrics.append({
            "name": name, **row,
            "average_minutes": round(row["minutes"] / row["count"]) if row["count"] else 0,
            "result": row["revenue"] - row["material_cost"] - row["salary_cost"],
        })

    day_count = max(1, (end - start).days)
    post_rows = defaultdict(lambda: {"minutes": 0, "count": 0})
    for appointment in period_appointments:
        if not appointment.post_id:
            continue
        row = post_rows[appointment.post_id]
        row["post"] = appointment.post
        row["count"] += 1
        row["minutes"] += max(0, int((appointment.end_at - appointment.start_at).total_seconds() / 60))
    post_metrics = []
    for row in post_rows.values():
        row["utilization"] = min(100, round(row["minutes"] / (day_count * 14 * 60) * 100))
        post_metrics.append(row)

    revenue_by_date = defaultdict(lambda: Decimal("0"))
    for appointment in paid_completed:
        local_date = appointment.start_at.astimezone(ZoneInfo(request.tenant.timezone)).date()
        revenue_by_date[local_date] += appointment.total
    revenue_trend = []
    cursor = start
    while cursor < end:
        amount = revenue_by_date[cursor]
        revenue_trend.append({"date": cursor, "amount": amount})
        cursor += timedelta(days=1)

    material_rows = list(writeoffs.values(
        "consumable__name", "consumable__category__unit"
    ).annotate(quantity=Sum("quantity"), cost=Sum("total_cost")).order_by("-cost"))
    client_counts = Counter(row.client_id for row in completed)
    period_client_ids = set(client_counts)
    previous_client_ids = set(Appointment.objects.filter(
        tenant=request.tenant, client_id__in=period_client_ids,
        status=Appointment.Status.COMPLETED, start_at__date__lt=start,
    ).order_by().values_list("client_id", flat=True).distinct())
    repeat_client_ids = previous_client_ids | {pk for pk, count in client_counts.items() if count > 1}
    repeat_rate = round(len(repeat_client_ids) / len(period_client_ids) * 100) if period_client_ids else 0

    complaints = Complaint.objects.filter(
        tenant=request.tenant, location=request.current_location,
        created_at__date__gte=start, created_at__date__lt=end,
    )
    complaint_count = complaints.count()
    rework_count = complaints.filter(is_rework=True).count()
    complaint_loss = complaints.aggregate(total=Sum("financial_loss"))["total"] or Decimal("0")
    ratings = ClientRating.objects.filter(
        tenant=request.tenant, submitted_by_client=True,
        appointment__start_at__date__gte=start,
        appointment__start_at__date__lt=end,
    )
    average_rating = ratings.aggregate(value=Avg("score"))["value"] or 0
    rating_count = ratings.count()
    metrics = defaultdict(lambda: {
        "count": 0, "revenue": Decimal("0"), "minutes": 0, "services": defaultdict(list),
        "paid_count": 0,
    })
    for appointment in completed:
        assignments = assignment_rows(appointment)
        if not assignments:
            continue
        actual_minutes = None
        if appointment.work_started_at and appointment.completed_at:
            actual_minutes = max(1, int((appointment.completed_at - appointment.work_started_at).total_seconds() / 60))
        planned_total = sum(item.duration_minutes_snapshot for item in appointment.items.all()) or 1
        for employee, share in assignments:
            row = metrics[employee.id]
            row["employee"] = employee
            row["count"] += 1
            if appointment.payment_status == Appointment.PaymentStatus.PAID:
                row["revenue"] += appointment.total * share / Decimal("100")
                row["paid_count"] += 1
            row["minutes"] += actual_minutes or planned_total
            for item in appointment.items.all():
                allocated = (
                    round(actual_minutes * item.duration_minutes_snapshot / planned_total)
                    if actual_minutes else item.duration_minutes_snapshot
                )
                per_unit = allocated / item.quantity
                row["services"][item.name_snapshot].extend(
                    per_unit for _ in range(item.quantity)
                )
    complaint_metrics = {row["assigned_to_id"]: row for row in complaints.order_by()
        .values("assigned_to_id").annotate(count=Count("pk"), reworks=Count("pk", filter=Q(is_rework=True)))}
    rating_scores = defaultdict(dict)
    for rating_id, score, primary_id, assignee_id in ratings.values_list(
        "id", "score", "appointment__employee_id", "appointment__assignees__employee_id"
    ):
        for employee_id in {primary_id, assignee_id} - {None}:
            rating_scores[employee_id][rating_id] = score
    employee_metrics = []
    for employee in Employee.objects.filter(
        tenant=request.tenant, location=request.current_location, active=True,
    ):
        row = metrics[employee.id]
        scores = list(rating_scores[employee.id].values())
        service_averages = [
            (name, round(sum(values) / len(values))) for name, values in row["services"].items()
        ]
        employee_metrics.append({
            "employee": employee,
            "count": row["count"],
            "revenue": row["revenue"],
            "average_minutes": round(row["minutes"] / row["count"]) if row["count"] else 0,
            "average_check": row["revenue"] / row["paid_count"] if row["paid_count"] else Decimal("0"),
            "utilization": min(100, round(row["minutes"] / (day_count * 14 * 60) * 100)),
            "complaints": complaint_metrics.get(employee.id, {}).get("count", 0),
            "reworks": complaint_metrics.get(employee.id, {}).get("reworks", 0),
            "rating": sum(scores) / len(scores) if scores else 0,
            "service_averages": service_averages,
        })
    return render(request, "analytics/dashboard.html", {
        "period": period,
        "advanced_analytics": True,
        "period_start": start,
        "period_end": end - timedelta(days=1),
        "period_error": period_error,
        "revenue": revenue,
        "average_check": average_check,
        "completed_count": len(completed),
        "consumables_cost": consumables_cost,
        "average_consumables_cost": consumables_cost / len(completed) if completed else 0,
        "expense_breakdown": [
            {"code": code, "label": label, "amount": expense_groups[code]}
            for code, label in Expense.Category.choices
        ],
        "manual_total": manual_total,
        "payroll_cost": payroll_cost,
        "payment_fees": payment_fees,
        "discounts": discounts,
        "complaint_loss": complaint_loss,
        "total_costs": consumables_cost + manual_total + payroll_cost + payment_fees + complaint_loss,
        "profit": revenue - consumables_cost - manual_total - payroll_cost - payment_fees - complaint_loss,
        "employee_metrics": employee_metrics,
        "service_metrics": service_metrics, "post_metrics": post_metrics,
        "revenue_trend": revenue_trend, "material_rows": material_rows,
        "repeat_clients": len(repeat_client_ids), "unique_clients": len(period_client_ids),
        "repeat_rate": repeat_rate, "complaint_count": complaint_count,
        "rework_count": rework_count, "average_rating": average_rating,
        "rating_count": rating_count,
        "expenses": manual_expenses[:12],
        "period_appointments": period_appointments,
    })


@tenant_permission_required("expenses_manage")
@tenant_feature_required("analytics_advanced")
def expense_create(request):
    form = ExpenseForm(request.POST or None, initial={"date": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        expense = form.save(commit=False)
        expense.tenant = request.tenant
        expense.location = request.current_location
        expense.save()
        log_event(tenant=request.tenant, actor=request.user, action="expense_create", entity=expense,
                  description=f"Добавлена затрата {expense.amount} ₽: {expense.get_category_display()}")
        messages.success(request, "Затрата добавлена.")
        return redirect("analytics-dashboard")
    return render(request, "analytics/expense_form.html", {"form": form})
