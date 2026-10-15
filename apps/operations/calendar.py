from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from types import SimpleNamespace

from django.db import models
from django.utils import timezone

from apps.catalog.models import WorkPost
from apps.staff.models import Employee, EmployeeShift
from apps.tenants.models import ensure_working_hours

from .models import Appointment


def build_calendar_context(*, tenant, selected_date, resource_kind, employee_scope=None, location=None):
    tenant_tz = ZoneInfo(tenant.timezone)
    day_start = timezone.make_aware(datetime.combine(selected_date, time.min), tenant_tz)
    day_end = day_start + timedelta(days=1)
    appointment_query = Appointment.objects.filter(
        tenant=tenant, start_at__lt=day_end, end_at__gt=day_start
    )
    if location is not None:
        appointment_query = appointment_query.filter(location=location)
    if employee_scope is False:
        appointment_query = appointment_query.none()
    elif employee_scope is not None:
        appointment_query = appointment_query.filter(
            models.Q(employee=employee_scope) | models.Q(assignees__employee=employee_scope)
        ).distinct()
    appointments = list(
        appointment_query.select_related("client", "car", "employee", "post").prefetch_related(
            "items", "assignees__employee"
        )
    )
    working_hours = ensure_working_hours(tenant)[selected_date.weekday()]
    business_closed = working_hours.is_closed
    if resource_kind == "post":
        used_posts = {row.post_id for row in appointments if row.post_id}
        resources = list(WorkPost.objects.filter(tenant=tenant, location=location).filter(
            models.Q(active=True) | models.Q(pk__in=used_posts)))
        resources.append(SimpleNamespace(id="", name="Без поста", active=True,
                                         get_kind_display=lambda: "Не назначен"))
        resource_rows = lambda appointment: [(appointment.post_id or "", None, appointment.employee)]
    else:
        shifts = list(EmployeeShift.objects.filter(
            tenant=tenant, location=location, date=selected_date, employee__active=True
        ).select_related("employee__company_role"))
        if employee_scope is False:
            shifts = []
        elif employee_scope is not None:
            shifts = [shift for shift in shifts if shift.employee_id == employee_scope.id]
        resources = [shift.employee for shift in shifts]
        shift_by_employee = {shift.employee_id: shift for shift in shifts}
        if employee_scope not in (None, False):
            resource_rows = lambda appointment: [
                (employee_scope.id, next(
                    (row for row in appointment.assignees.all() if row.employee_id == employee_scope.id),
                    None,
                ), employee_scope)
            ]
        else:
            def resource_rows(appointment):
                assignments = list(appointment.assignees.all())
                if assignments:
                    return [(row.employee_id, row, row.employee) for row in assignments]
                return [(appointment.employee_id, None, appointment.employee)]
    if business_closed:
        resources = []
    opening_minutes = working_hours.opens_at.hour * 60 + working_hours.opens_at.minute
    closing_minutes = working_hours.closes_at.hour * 60 + working_hours.closes_at.minute
    pixels_per_minute = 1.6
    entries = {resource.id: [] for resource in resources}
    for appointment in appointments:
        local_start = timezone.localtime(appointment.start_at, tenant_tz)
        local_end = timezone.localtime(appointment.end_at, tenant_tz)
        start_minutes = max(opening_minutes, local_start.hour * 60 + local_start.minute)
        end_minutes = min(closing_minutes, local_end.hour * 60 + local_end.minute)
        if end_minutes <= opening_minutes or start_minutes >= closing_minutes:
            continue
        for key, assignment, employee in resource_rows(appointment):
            if key not in entries:
                continue
            entries[key].append({
                "appointment": appointment,
                "assignment": assignment,
                "employee": employee,
                "top": round((start_minutes - opening_minutes) * pixels_per_minute, 2),
                "height": round(max(36, (end_minutes - start_minutes) * pixels_per_minute), 2),
                "duration": int((appointment.end_at - appointment.start_at).total_seconds() / 60),
            })
    for rows in entries.values():
        rows.sort(key=lambda item: (item["appointment"].start_at, item["appointment"].end_at))
        active, group, max_lanes = [], [], 1
        def finish_group():
            for grouped in group:
                grouped["lane_count"] = max_lanes
        for entry in rows:
            start_at = entry["appointment"].start_at
            active = [item for item in active if item["appointment"].end_at > start_at]
            if not active and group:
                finish_group()
                group, max_lanes = [], 1
            used = {item["lane"] for item in active}
            lane = next(index for index in range(len(used) + 1) if index not in used)
            entry["lane"] = lane
            active.append(entry)
            group.append(entry)
            max_lanes = max(max_lanes, lane + 1)
        if group:
            finish_group()
    ticks = []
    for offset in range(0, closing_minutes - opening_minutes + 1, 15):
        total = opening_minutes + offset
        ticks.append({
            "top": round(offset * pixels_per_minute, 2), "label": f"{total // 60:02d}:{total % 60:02d}",
            "is_hour": total % 60 == 0, "is_first": offset == 0,
            "is_last": offset == closing_minutes - opening_minutes,
        })
    local_now = timezone.now().astimezone(tenant_tz)
    current_time_top = None
    if local_now.date() == selected_date:
        minutes = local_now.hour * 60 + local_now.minute
        if opening_minutes <= minutes <= closing_minutes:
            current_time_top = round((minutes - opening_minutes) * pixels_per_minute, 2)
    columns = []
    for resource in resources:
        column = {"resource": resource, "entries": entries[resource.id]}
        if resource_kind == "employee":
            shift = shift_by_employee[resource.id]
            shift_start_minutes = max(
                opening_minutes, shift.start_time.hour * 60 + shift.start_time.minute
            )
            shift_end_minutes = min(
                closing_minutes, shift.end_time.hour * 60 + shift.end_time.minute
            )
            column.update({
                "shift": shift,
                "shift_start_minutes": shift_start_minutes,
                "shift_end_minutes": shift_end_minutes,
                "unavailable_before_height": round(
                    max(0, shift_start_minutes - opening_minutes) * pixels_per_minute, 2
                ),
                "unavailable_after_top": round(
                    max(0, shift_end_minutes - opening_minutes) * pixels_per_minute, 2
                ),
                "unavailable_after_height": round(
                    max(0, closing_minutes - shift_end_minutes) * pixels_per_minute, 2
                ),
            })
        columns.append(column)
    return {
        "calendar_columns": columns,
        "timeline_ticks": ticks,
        "board_height": round((closing_minutes - opening_minutes) * pixels_per_minute),
        "opening_hour": opening_minutes // 60, "closing_hour": closing_minutes // 60,
        "opening_minutes": opening_minutes, "closing_minutes": closing_minutes,
        "pixels_per_minute": pixels_per_minute, "selected_date": selected_date,
        "previous_date": selected_date - timedelta(days=1), "next_date": selected_date + timedelta(days=1),
        "resource_kind": resource_kind,
        "current_time_top": current_time_top,
        "current_time_label": local_now.strftime("%H:%M"),
        "tenant_timezone": tenant.timezone,
        "business_closed": business_closed,
    }
