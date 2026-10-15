from pathlib import PurePosixPath
from hashlib import sha256

from django.utils.cache import get_conditional_response

from django.contrib.auth import get_user_model
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_safe

from apps.customers.models import CarDamagePhoto, VehicleClass
from apps.operations.models import ComplaintPhoto
from apps.tenants.access import permissions_for_request
from apps.tenants.models import Membership


IMAGE_CONTENT_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


@require_safe
def media_file(request, path):
    """Serve uploaded files only after resolving them to the current tenant."""
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404

    if path.startswith("company-logos/"):
        if tenant.logo.name != path:
            raise Http404
        field_file = tenant.logo
    elif path.startswith("vehicle-classes/"):
        field_file = get_object_or_404(VehicleClass, tenant=tenant, image=path).image
    else:
        if not getattr(request, "membership", None):
            return HttpResponseForbidden("Нет доступа к файлу")
        if path.startswith("damages/"):
            if "clients_view" not in permissions_for_request(request):
                return HttpResponseForbidden("Нет доступа к файлу")
            photo = get_object_or_404(
                CarDamagePhoto, file=path, damage__tenant=tenant,
            )
            field_file = photo.file
        elif path.startswith("complaints/"):
            if "quality_view" not in permissions_for_request(request):
                return HttpResponseForbidden("Нет доступа к файлу")
            photo = get_object_or_404(
                ComplaintPhoto, file=path, complaint__tenant=tenant,
            )
            field_file = photo.file
        elif path.startswith("profiles/"):
            user = get_object_or_404(
                get_user_model(), avatar=path,
                memberships__tenant=tenant,
                memberships__status=Membership.Status.ACTIVE,
            )
            field_file = user.avatar
        else:
            raise Http404

    content_type = IMAGE_CONTENT_TYPES.get(
        PurePosixPath(field_file.name).suffix.lower(), "application/octet-stream"
    )
    # Authorize first on every request. Private photographs stay no-store.
    cacheable = path.startswith(("company-logos/", "profiles/"))
    etag = None
    if cacheable:
        try:
            modified = field_file.storage.get_modified_time(field_file.name).timestamp()
            size = field_file.size
            etag = '"' + sha256(f"{field_file.name}:{size}:{modified}".encode()).hexdigest() + '"'
        except (OSError, NotImplementedError) as exc:
            if isinstance(exc, OSError):
                raise Http404 from exc
        if etag:
            conditional = get_conditional_response(request, etag=etag)
            if conditional is not None:
                conditional["Cache-Control"] = "private, no-cache"
                conditional["ETag"] = etag
                conditional["X-Content-Type-Options"] = "nosniff"
                return conditional
    try:
        response = FileResponse(field_file.open("rb"), content_type=content_type)
    except OSError as exc:
        raise Http404 from exc
    response["Cache-Control"] = "private, no-cache" if cacheable else "private, no-store"
    if etag:
        response["ETag"] = etag
    response["X-Content-Type-Options"] = "nosniff"
    if content_type == "application/octet-stream":
        response["Content-Disposition"] = 'attachment; filename="download"'
    return response
