"""Central write protection for expired company workspaces."""
from django.http import JsonResponse
from django.shortcuts import render

# Authentication, payment renewal and navigation remain available.
ALLOWED_WRITES = frozenset({
    'login', 'logout', 'two-factor-verify', 'password-reset', 'password-reset-confirm',
    'subscription-settings', 'yookassa-webhook', 'select-location',
    'notification-read', 'notification-read-all', 'push-unsubscribe', 'push-subscribe',
})


def workspace_read_only(request):
    subscription = getattr(request, 'subscription', None)
    tenant = getattr(request, 'tenant', None)
    return bool(tenant and (tenant.status == 'suspended' or
        (subscription and subscription.status in {'suspended', 'cancelled'})))


def subscription_denied(request):
    if 'application/json' in request.headers.get('Accept', ''):
        return JsonResponse({'error': 'subscription_expired',
            'message': 'Подписка закончилась. Продлите её, чтобы продолжить работу.'}, status=403)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return render(request, 'core/_subscription_warning.html', status=403)
    return render(request, 'core/subscription_expired.html', {'show_subscription_warning': True}, status=403)
