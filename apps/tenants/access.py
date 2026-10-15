from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render

from .models import Membership
from .plans import feature_info, tenant_has_feature


ALL_PERMISSIONS = {
    "appointments_view", "appointments_view_all", "appointments_manage",
    "appointments_status_own", "clients_view", "clients_manage", "clients_delete",
    "clients_permanent_delete",
    "services_view", "services_manage", "inventory_view", "inventory_manage",
    "inventory_count", "analytics_view", "expenses_manage", "staff_view",
    "staff_manage", "settings_manage", "roles_manage", "payroll_view",
    "quality_view", "quality_manage",
}

LEGACY_PERMISSIONS = {
    Membership.Role.ADMIN: {"appointments_view", "appointments_view_all", "appointments_manage", "clients_view", "clients_manage", "clients_delete", "clients_permanent_delete", "services_view", "staff_view"},
    Membership.Role.MASTER: {
        "appointments_view", "appointments_status_own", "clients_view", "services_view", "inventory_view",
        "inventory_count", "staff_view",
    },
}


def permissions_for_request(request):
    if not hasattr(request, "_crm_permissions"):
        request._crm_permissions = _permissions_for_request(request)
    return request._crm_permissions


def _permissions_for_request(request):
    membership = getattr(request, "membership", None)
    tenant = getattr(request, "tenant", None)
    if not membership or not tenant:
        return set()
    if membership.role == Membership.Role.OWNER:
        return set(ALL_PERMISSIONS)
    employee = employee_for_request(request)
    if employee and employee.company_role and employee.company_role.active:
        return set(employee.company_role.permissions or [])
    return set(LEGACY_PERMISSIONS.get(membership.role, set()))


def employee_for_request(request):
    if not hasattr(request, "_crm_employee"):
        request._crm_employee = _employee_for_request(request)
    return request._crm_employee


def _employee_for_request(request):
    """Return the active employee profile linked to the current CRM user."""
    tenant = getattr(request, "tenant", None)
    if not tenant or not getattr(request, "user", None) or not request.user.is_authenticated:
        return None
    from apps.staff.models import Employee
    return Employee.objects.select_related("company_role").filter(tenant=tenant, user=request.user, active=True).first()


def can_access_appointment(request, appointment, *, manage=False):
    permissions = permissions_for_request(request)
    if "appointments_manage" in permissions or "appointments_view_all" in permissions:
        return True
    employee = employee_for_request(request)
    if not employee:
        return False
    assigned = appointment.employee_id == employee.id or appointment.assignees.filter(
        employee=employee
    ).exists()
    if manage:
        return assigned and "appointments_status_own" in permissions
    return assigned and "appointments_view" in permissions


def tenant_permission_required(permission):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            membership = getattr(request, "membership", None)
            if getattr(request, "tenant", None) is None or membership is None:
                return HttpResponseForbidden("Нет доступа к этой компании")
            permissions = permissions_for_request(request)
            request.tenant_permissions = {code: code in permissions for code in ALL_PERMISSIONS}
            if permission not in permissions:
                return HttpResponseForbidden("Недостаточно прав")
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def tenant_feature_required(feature_code):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if getattr(request, "tenant", None) is None or getattr(request, "membership", None) is None:
                return HttpResponseForbidden("Нет доступа к этой компании")
            if not tenant_has_feature(request.tenant, feature_code):
                return render(request, "core/tariff_locked.html", {
                    "locked_feature": feature_info(feature_code),
                }, status=403)
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def tenant_roles_required(*roles):
    allowed_roles = set(roles)

    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            membership = getattr(request, "membership", None)
            if getattr(request, "tenant", None) is None or membership is None:
                return HttpResponseForbidden("Нет доступа к этой компании")
            if allowed_roles and membership.role not in allowed_roles:
                return HttpResponseForbidden("Недостаточно прав")
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


tenant_member_required = tenant_roles_required(
    Membership.Role.OWNER,
    Membership.Role.ADMIN,
    Membership.Role.MASTER,
)
tenant_manager_required = tenant_roles_required(
    Membership.Role.OWNER,
    Membership.Role.ADMIN,
)


tenant_owner_required = tenant_roles_required(Membership.Role.OWNER)
