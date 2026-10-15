from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.urls import re_path

from apps.core.media import media_file

urlpatterns = [
    re_path(r"^media/(?P<path>.+)$", media_file, name="media-file"),
    path("", include("apps.accounts.urls")),
    path("", include("apps.customers.urls")),
    path("", include("apps.catalog.urls")),
    path("", include("apps.staff.urls")),
    path("", include("apps.operations.urls")),
    path("", include("apps.inventory.urls")),
    path("", include("apps.analytics.urls")),
    path("", include("apps.payroll.urls")),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
