import secrets
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.operations.models import Appointment
from apps.tenants.access import tenant_feature_required, tenant_owner_required, tenant_permission_required
from apps.tenants.models import Membership
from apps.tenants.models import ensure_working_hours
from apps.tenants.plans import active_employee_limit, counted_employees, tenant_has_feature
from apps.core.models import log_event

from .forms import CompanyRoleForm, EmployeeForm
from .models import CompanyRole, Employee, EmployeeShift, ensure_default_roles


def _sync_access(employee, form):
    access = form.cleaned_data.get("crm_access")
    if access == "none":
        user = employee.user
        if not user:
            return
        employee.user = None
        employee.save(update_fields=("user", "updated_at"))
        if not Employee.objects.filter(tenant=employee.tenant, user=user).exclude(pk=employee.pk).exists():
            Membership.objects.filter(tenant=employee.tenant, user=user).update(status=Membership.Status.DISABLED)
        return
    if employee.user_id:
        return
    email = (form.cleaned_data.get("access_email") or "").lower().strip()
    password = form.cleaned_data.get("temporary_password")
    User = get_user_model()
    user = User.objects.create_user(email=email, password=password)
    Membership.objects.create(
        tenant=employee.tenant, user=user,
        role=Membership.Role.MASTER, status=Membership.Status.ACTIVE,
    )
    membership = user.memberships.get(tenant=employee.tenant)
    if employee.location_id:
        membership.location_access.get_or_create(location=employee.location)
    if employee.user_id != user.id:
        employee.user = user
        employee.save(update_fields=("user", "updated_at"))


def _employee_limit_context(tenant):
    limit = active_employee_limit(tenant)
    return {"employee_limit_reached": limit is not None and counted_employees(tenant).count() >= limit,
            "employee_limit": limit}


@tenant_permission_required("staff_view")
def employee_list(request):
    ensure_default_roles(request.tenant)
    employees = Employee.objects.filter(
        tenant=request.tenant, location=request.current_location,
    ).exclude(user_id__in=request.tenant.memberships.filter(role=Membership.Role.OWNER).values("user_id")).exclude(company_role__builtin_key="owner").select_related("company_role")
    return render(request, "staff/employee_list.html", {"employees": employees})


@tenant_permission_required("staff_manage")
@transaction.atomic
def employee_create(request):
    from apps.tenants.models import Subscription
    Subscription.objects.select_for_update().get(tenant=request.tenant)
    advanced_payroll = tenant_has_feature(request.tenant, "payroll_advanced")
    form = EmployeeForm(
        request.POST or None, tenant=request.tenant, location=request.current_location,
        advanced_payroll=advanced_payroll,
    )
    if request.method == "POST" and form.is_valid():
        limit = active_employee_limit(request.tenant)
        if form.cleaned_data.get("active") and limit is not None and counted_employees(request.tenant).count() >= limit:
            form.add_error("active", f"Достигнут лимит: до {limit} активных сотрудников без учёта владельца. Выберите пакет с большим лимитом.")
            return render(request, "staff/employee_form.html", {
                "form": form, "title": "Новый сотрудник", "advanced_payroll": advanced_payroll, **_employee_limit_context(request.tenant),
            })
        with transaction.atomic():
            employee = form.save(commit=False)
            employee.tenant = request.tenant
            employee.location = request.current_location
            employee.color = secrets.choice([value for value, _ in Employee.Color.choices])
            employee.save()
            form.save_service_settings(employee)
            _sync_access(employee, form)
            log_event(tenant=request.tenant, actor=request.user, action="employee_create", entity=employee,
                      description=f"Добавлен сотрудник «{employee.name}»")
        messages.success(request, "Сотрудник добавлен.")
        return redirect("employee-list")
    return render(request, "staff/employee_form.html", {
        "form": form, "title": "Новый сотрудник", "advanced_payroll": advanced_payroll, **_employee_limit_context(request.tenant),
    })


@tenant_permission_required("staff_manage")
@transaction.atomic
def employee_edit(request, employee_id):
    from apps.tenants.models import Subscription
    Subscription.objects.select_for_update().get(tenant=request.tenant)
    employee = get_object_or_404(
        Employee, id=employee_id, tenant=request.tenant, location=request.current_location,
    )
    advanced_payroll = tenant_has_feature(request.tenant, "payroll_advanced")
    form = EmployeeForm(
        request.POST or None, instance=employee, tenant=request.tenant,
        location=request.current_location, advanced_payroll=advanced_payroll,
    )
    if request.method == "POST" and form.is_valid():
        limit = active_employee_limit(request.tenant)
        activating = form.cleaned_data.get("active") and not Employee.objects.get(pk=employee.pk).active
        if employee.user_id and request.tenant.memberships.filter(user_id=employee.user_id, role="owner").exists():
            activating = False
        if activating and limit is not None and counted_employees(request.tenant).count() >= limit:
            form.add_error("active", f"Достигнут лимит: до {limit} активных сотрудников без учёта владельца. Выберите пакет с большим лимитом.")
            return render(request, "staff/employee_form.html", {
                "form": form, "title": "Редактирование сотрудника",
                "advanced_payroll": advanced_payroll, **_employee_limit_context(request.tenant),
            })
        with transaction.atomic():
            employee = form.save()
            form.save_service_settings(employee)
            _sync_access(employee, form)
            log_event(tenant=request.tenant, actor=request.user, action="employee_update", entity=employee,
                      description=f"Изменён сотрудник «{employee.name}»")
        messages.success(request, "Сотрудник сохранен.")
        return redirect("employee-list")
    return render(
        request,
        "staff/employee_form.html",
        {"form": form, "title": "Редактирование сотрудника", "advanced_payroll": advanced_payroll, **_employee_limit_context(request.tenant)},
    )


@tenant_permission_required("staff_view")
def employee_detail(request, employee_id):
    employee = get_object_or_404(
        Employee.objects.select_related("company_role"), id=employee_id, tenant=request.tenant,
        location=request.current_location,
    )
    history = Appointment.objects.filter(
        tenant=request.tenant,
        status=Appointment.Status.COMPLETED,
    ).filter(
        Q(employee=employee) | Q(assignees__employee=employee)
    ).select_related("client", "car").prefetch_related(
        "items", "assignees__employee"
    ).distinct().order_by("-start_at")
    return render(request, "staff/employee_detail.html", {"employee": employee, "history": history})


@tenant_owner_required
def role_list(request):
    ensure_default_roles(request.tenant)
    roles = CompanyRole.objects.filter(tenant=request.tenant).prefetch_related("employees")
    return render(request, "staff/role_list.html", {
        "roles": roles, "advanced_roles": tenant_has_feature(request.tenant, "roles_advanced"),
    })


@tenant_owner_required
@tenant_feature_required("roles_advanced")
def role_create(request):
    form = CompanyRoleForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        role = form.save(commit=False)
        role.tenant = request.tenant
        role.save()
        log_event(tenant=request.tenant, actor=request.user, action="role_create", entity=role,
                  description=f"Добавлена роль «{role.name}»")
        messages.success(request, "Роль добавлена.")
        return redirect("role-list")
    return render(request, "staff/role_form.html", {"form": form, "title": "Новая роль"})


@tenant_owner_required
@tenant_feature_required("roles_advanced")
def role_edit(request, role_id):
    role = get_object_or_404(CompanyRole, id=role_id, tenant=request.tenant)
    form = CompanyRoleForm(request.POST or None, instance=role, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_event(tenant=request.tenant, actor=request.user, action="role_update", entity=role,
                  description=f"Изменена роль «{role.name}»")
        messages.success(request, "Роль сохранена.")
        return redirect("role-list")
    return render(request, "staff/role_form.html", {"form": form, "title": "Настройка роли"})


def _week_start(value, tenant):
    try:
        selected = date.fromisoformat(value)
        week_start = selected - timedelta(days=selected.weekday())
        # Both navigation links and all seven displayed dates must be valid.
        week_start - timedelta(days=7)
        week_start + timedelta(days=7)
    except (TypeError, ValueError, OverflowError):
        selected = timezone.now().astimezone(ZoneInfo(tenant.timezone)).date()
    return selected - timedelta(days=selected.weekday())


@tenant_permission_required("staff_view")
def schedule(request):
    week_start = _week_start(request.GET.get("week"), request.tenant)
    days = [week_start + timedelta(days=offset) for offset in range(7)]
    employees = list(Employee.objects.filter(
        tenant=request.tenant, location=request.current_location, active=True,
    ))
    can_manage = request.tenant_permissions.get("staff_manage", False)
    company_hours = ensure_working_hours(request.tenant)
    hours_by_day = {day: company_hours[day.weekday()] for day in days}
    errors = []
    if request.method == "POST" and can_manage:
        action = request.POST.get("action", "save")
        if action == "copy_previous":
            previous_start = week_start - timedelta(days=7)
            previous = EmployeeShift.objects.filter(
                tenant=request.tenant, location=request.current_location,
                date__gte=previous_start, date__lt=week_start,
            )
            with transaction.atomic():
                for shift in previous:
                    target_date = shift.date + timedelta(days=7)
                    hours = hours_by_day[target_date]
                    if hours.is_closed:
                        EmployeeShift.objects.filter(
                            tenant=request.tenant, location=request.current_location,
                            employee=shift.employee, date=target_date,
                        ).delete()
                        continue
                    start_time = max(shift.start_time, hours.opens_at)
                    end_time = min(shift.end_time, hours.closes_at)
                    if start_time >= end_time:
                        continue
                    EmployeeShift.objects.update_or_create(
                        tenant=request.tenant, location=request.current_location, employee=shift.employee,
                        date=target_date,
                        defaults={"location": request.current_location, "start_time": start_time, "end_time": end_time},
                    )
            messages.success(request, "График скопирован с прошлой недели.")
            log_event(tenant=request.tenant, actor=request.user, action="schedule_copy", entity=request.tenant,
                      description=f"Скопирован график на неделю с {week_start:%d.%m.%Y}")
            return redirect(f"{request.path}?week={week_start.isoformat()}")
        changes = []
        for employee in employees:
            for day in days:
                prefix = f"shift_{employee.id}_{day.isoformat()}"
                enabled = request.POST.get(prefix) == "on"
                if enabled:
                    hours = hours_by_day[day]
                    if hours.is_closed:
                        errors.append(f"{employee.name}, {day:%d.%m}: компания в этот день закрыта.")
                        continue
                    try:
                        start_time = datetime.strptime(request.POST.get(prefix + "_start", ""), "%H:%M").time()
                        end_time = datetime.strptime(request.POST.get(prefix + "_end", ""), "%H:%M").time()
                        if start_time >= end_time:
                            raise ValueError
                    except ValueError:
                        errors.append(f"{employee.name}, {day:%d.%m}: проверьте время смены.")
                    else:
                        if start_time < hours.opens_at or end_time > hours.closes_at:
                            errors.append(
                                f"{employee.name}, {day:%d.%m}: смена должна быть в пределах "
                                f"{hours.opens_at:%H:%M}–{hours.closes_at:%H:%M}."
                            )
                        else:
                            changes.append((employee, day, start_time, end_time))
                else:
                    changes.append((employee, day, None, None))
        if not errors:
            with transaction.atomic():
                for employee, day, start_time, end_time in changes:
                    if start_time is None:
                        EmployeeShift.objects.filter(
                            tenant=request.tenant, location=request.current_location,
                            employee=employee, date=day,
                        ).delete()
                    else:
                        EmployeeShift.objects.update_or_create(
                            tenant=request.tenant, location=request.current_location,
                            employee=employee, date=day,
                            defaults={"start_time": start_time, "end_time": end_time},
                        )
            messages.success(request, "График сохранён.")
            log_event(tenant=request.tenant, actor=request.user, action="schedule_update", entity=request.tenant,
                      description=f"Сохранён график на неделю с {week_start:%d.%m.%Y}")
            return redirect(f"{request.path}?week={week_start.isoformat()}")
    shifts = {
        (shift.employee_id, shift.date): shift
        for shift in EmployeeShift.objects.filter(
            tenant=request.tenant, location=request.current_location,
            date__gte=week_start, date__lt=week_start + timedelta(days=7)
        )
    }
    rows = [{
        "employee": employee,
        "days": [{"date": day, "shift": shifts.get((employee.id, day)), "hours": hours_by_day[day]}
                 for day in days],
    } for employee in employees]
    return render(request, "staff/schedule.html", {
        "week_start": week_start, "week_end": days[-1], "days": days, "rows": rows,
        "previous_week": week_start - timedelta(days=7), "next_week": week_start + timedelta(days=7),
        "can_manage": can_manage, "errors": errors,
        "company_hours": company_hours,
    })
