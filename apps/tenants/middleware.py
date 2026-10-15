from django.conf import settings
from django.http import Http404
from django.utils import timezone, translation

from .models import Membership, Tenant
from .plans import (
    backfill_primary_location_data, ensure_primary_location, sync_subscription, tenant_has_feature,
)


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host=request.get_host().split(':',1)[0].lower()
        if host == settings.PLATFORM_DOMAIN or (settings.DEBUG and host == 'platform.localhost'):
            request.urlconf='config.platform_urls'
        elif host == settings.BOOKING_DOMAIN or (settings.DEBUG and host == 'book.localhost'):
            request.urlconf='config.booking_urls'
        elif host == settings.SUPPORT_DOMAIN or (settings.DEBUG and host == 'support.localhost'):
            request.urlconf='config.support_urls'
        request.tenant = self._resolve_tenant(
            request, request.get_host().split(":", 1)[0].lower()
        )
        request.membership = None
        request.subscription = None
        request.current_location = None
        if request.user.is_authenticated and request.tenant:
            request.membership = (
                Membership.objects.select_related("tenant", "user")
                .filter(
                    tenant=request.tenant,
                    user=request.user,
                    status=Membership.Status.ACTIVE,
                )
                .first()
            )
        workspace_request = not request.path_info.startswith("/media/")
        if workspace_request and request.membership and request.membership.role == Membership.Role.OWNER:
            from apps.staff.models import Employee, ensure_default_roles
            owner_role = ensure_default_roles(request.tenant)["owner"]
            profile = Employee.objects.filter(tenant=request.tenant, user=request.user).first()
            if profile and profile.company_role_id != owner_role.pk:
                profile.company_role = owner_role
                profile.position = "Владелец"
                profile.save(update_fields=("company_role", "position", "updated_at"))
        if request.tenant and request.user.is_authenticated and request.user.email_2fa and request.session.get('account_2fa_user') != str(request.user.pk):
            from django.contrib.auth import logout
            from django.shortcuts import redirect
            logout(request)
            return redirect('login')
        if request.tenant and workspace_request:
            request.subscription = sync_subscription(request.tenant)
            primary_location = ensure_primary_location(request.tenant)
            backfill_primary_location_data(request.tenant, primary_location)
            locations = request.tenant.locations.filter(active=True)
            if request.membership and request.membership.role != Membership.Role.OWNER:
                permitted_ids = list(
                    request.membership.location_access.values_list("location_id", flat=True)
                )
                if permitted_ids:
                    locations = locations.filter(id__in=permitted_ids)
                else:
                    employee_location_id = request.tenant.employees.filter(
                        user=request.user, active=True,
                    ).values_list("location_id", flat=True).first()
                    locations = locations.filter(id=employee_location_id or primary_location.id)
            selected_id = request.session.get("current_location_id")
            request.current_location = locations.filter(id=selected_id).first() or locations.first()
            if request.current_location is None:
                request.current_location = primary_location
            if not tenant_has_feature(request.tenant, "multi_location"):
                request.current_location = primary_location
        if request.tenant:
            timezone.activate(request.tenant.timezone)
            translation.activate(request.tenant.language_code)
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
            translation.deactivate()

    def process_view(self, request, view_func, view_args, view_kwargs):
        from .subscription_access import ALLOWED_WRITES, workspace_read_only, subscription_denied
        if workspace_read_only(request):
            name = request.resolver_match.url_name or ""
            is_edit_screen = bool(request.membership and name.endswith(("-create", "-edit", "-delete", "-permanent-delete")))
            is_write = request.method not in {"GET", "HEAD", "OPTIONS"}
            if (is_write and name not in ALLOWED_WRITES) or is_edit_screen:
                return subscription_denied(request)
        return None

    @staticmethod
    def _resolve_tenant(request, host):
        if host in {settings.PLATFORM_DOMAIN, settings.BOOKING_DOMAIN, settings.SUPPORT_DOMAIN} or (settings.DEBUG and host in {'platform.localhost', 'book.localhost', 'support.localhost'}):
            return None
        if host in settings.PUBLIC_HOSTS:
            if not settings.DEBUG:
                return None
            slug = request.session.get("development_tenant_slug")
            if not slug:
                return None
            try:
                return Tenant.objects.get(slug=slug)
            except Tenant.DoesNotExist:
                request.session.pop("development_tenant_slug", None)
                return None

        slug = None
        for suffix in settings.TENANT_DOMAIN_SUFFIXES:
            if host.endswith(suffix):
                slug = host[: -len(suffix)]
                break

        if not slug or "." in slug or slug in settings.RESERVED_SUBDOMAINS:
            raise Http404("Компания не найдена")

        try:
            return Tenant.objects.get(slug=slug)
        except Tenant.DoesNotExist as exc:
            raise Http404("Компания не найдена") from exc
