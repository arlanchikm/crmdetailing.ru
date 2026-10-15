from django.urls import path, re_path
from apps.core import views, booking, booking_appearance, booking_links
from apps.core.media import media_file

urlpatterns = [
    path("health/", views.health, name="health"),
    path("booking/<uuid:token>/", views.booking_widget, name="booking-widget"),
    path("booking/<uuid:token>/availability/", booking.availability_view, name="booking-availability"),
    path("booking/<uuid:token>/class/<uuid:class_id>/image/", booking.class_image, name="booking-class-image"),
    path("booking/<uuid:token>/background/", booking_appearance.background_image, name="booking-background"),
    re_path(r"^media/(?P<path>.+)$", media_file, name="media-file"),
    path("<slug:slug>", booking_links.company_booking, name="company-booking"),
    path("<slug:slug>/", booking_links.company_booking),
]
