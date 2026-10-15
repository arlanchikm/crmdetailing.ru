def tenant_context(request):
    from django.conf import settings
    from django.utils import timezone
    from django.db import models
    from apps.core.models import Notification
    from .access import ALL_PERMISSIONS, employee_for_request, permissions_for_request
    from .plans import FEATURES, subscription_features

    permissions = permissions_for_request(request) if getattr(request, "user", None) and request.user.is_authenticated else set()
    tenant = getattr(request, "tenant", None)
    unread_notifications = 0
    if tenant and getattr(request, "user", None) and request.user.is_authenticated:
        from apps.core.notifications import user_notifications
        unread_notifications = user_notifications(request).filter(read_at__isnull=True).count()
    subscription = getattr(request, "subscription", None)
    enabled_features = subscription_features(subscription) if subscription else set()
    available_locations = []
    if tenant and getattr(request, "membership", None):
        available_locations = tenant.locations.filter(active=True)
        if request.membership.role != "owner":
            access_ids = request.membership.location_access.values_list("location_id", flat=True)
            if access_ids.exists():
                available_locations = available_locations.filter(id__in=access_ids)
            elif getattr(request, "current_location", None):
                available_locations = available_locations.filter(id=request.current_location.id)
    trial_warning = None
    if subscription and subscription.status == "trial" and subscription.trial_ends_at:
        remaining = max(0, (subscription.trial_ends_at - timezone.now()).days + 1)
        if remaining <= 7:
            trial_warning = remaining
    from .subscription_access import workspace_read_only
    push_public_key = None
    if tenant and getattr(request, "membership", None):
        from apps.core.notifications import webpush_config
        push_public_key = webpush_config().public_key
    platform_email = None
    if not tenant and getattr(request, 'user', None) and request.user.is_authenticated:
        from .models import PlatformAccess
        platform_email = PlatformAccess.objects.filter(user=request.user, active=True).values_list('email', flat=True).first() or request.user.email
    return {
        "platform_email": platform_email,
        "push_public_key": push_public_key,
        "primary_domain": settings.PRIMARY_DOMAIN,
        "support_domain": settings.SUPPORT_DOMAIN,
        "subscription_read_only": workspace_read_only(request),
        "current_tenant": getattr(request, "tenant", None),
        "current_membership": getattr(request, "membership", None),
        "tenant_permissions": {code: code in permissions for code in ALL_PERMISSIONS},
        "unread_notifications": unread_notifications,
        "current_employee": employee_for_request(request) if tenant else None,
        "current_subscription": subscription,
        "tariff_features": {code: code in enabled_features for code in FEATURES},
        "feature_catalog": FEATURES,
        "current_location": getattr(request, "current_location", None),
        "available_locations": available_locations,
        "trial_warning_days": trial_warning,
        "notifications_started_at":timezone.now().isoformat(),
    }
