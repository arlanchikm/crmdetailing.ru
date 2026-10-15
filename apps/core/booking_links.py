"""Public company booking links and downloadable QR codes."""
from io import BytesIO

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_safe, require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.clickjacking import xframe_options_exempt

from apps.tenants.access import tenant_owner_required
from .models import IntegrationChannel


def booking_url(tenant):
    return f"https://{settings.BOOKING_DOMAIN}/{tenant.slug}"


@csrf_exempt
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def company_booking(request, slug):
    from .views import booking_widget
    channel = get_object_or_404(IntegrationChannel, tenant__slug=slug,
        tenant__status="active", kind="website", enabled=True)
    return booking_widget(request, token=channel.public_token)


@require_safe
@tenant_owner_required
def booking_qr(request):
    import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=12, border=4)
    qr.add_data(booking_url(request.tenant))
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="transparent")
    output = BytesIO()
    image.save(output, format="PNG")
    response = HttpResponse(output.getvalue(), content_type="image/png")
    if request.GET.get("download") == "1":
        response["Content-Disposition"] = f'attachment; filename="booking-{request.tenant.slug}-qr.png"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
