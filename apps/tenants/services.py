from django.db import transaction

from apps.accounts.models import User
from apps.catalog.models import ensure_system_services
from apps.customers.models import ensure_default_vehicle_classes
from apps.inventory.models import ensure_default_categories
from apps.staff.models import Employee, ensure_default_roles

from .models import Membership, Tenant, ensure_working_hours
from .plans import ensure_primary_location, ensure_subscription


@transaction.atomic
def create_company_owner(*, company_name, slug, owner_name, email, password, plan_code="start"):
    from .plans import activate_trial
    tenant = Tenant.objects.create(name=company_name.strip(), slug=slug.strip().lower())
    ensure_subscription(tenant)
    location = ensure_primary_location(tenant)
    user = User.objects.create_user(email=email.strip().lower(), password=password,
                                    first_name=owner_name.strip())
    Membership.objects.create(tenant=tenant, user=user, role=Membership.Role.OWNER)
    roles = ensure_default_roles(tenant)
    Employee.objects.create(tenant=tenant, location=location, user=user, name=owner_name.strip(),
                            position="Владелец", company_role=roles["owner"], active=True)
    ensure_working_hours(tenant)
    ensure_default_categories(tenant)
    ensure_default_vehicle_classes(tenant)
    ensure_system_services(tenant, location)
    activate_trial(tenant, user, plan_code)
    tenant._state.fields_cache.pop("subscription", None)
    return tenant, user
