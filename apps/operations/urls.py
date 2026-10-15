from django.urls import path

from . import views

urlpatterns = [
    path("appointments/calendar-view/", views.calendar_view_select, name="calendar-view-select"),
    path("feedback/<uuid:token>/", views.public_rating, name="public-rating"),
    path("appointments/", views.appointment_calendar, name="appointment-calendar"),
    path("appointments/new/", views.appointment_create, name="appointment-create"),
    path("appointments/<uuid:appointment_id>/", views.appointment_detail, name="appointment-detail"),
    path("appointments/<uuid:appointment_id>/edit/", views.appointment_edit, name="appointment-edit"),
    path("appointments/<uuid:appointment_id>/delete/", views.appointment_delete, name="appointment-delete"),
    path("appointments/<uuid:appointment_id>/move/", views.appointment_move, name="appointment-move"),
    path(
        "appointments/<uuid:appointment_id>/transition/<slug:action>/",
        views.appointment_transition,
        name="appointment-transition",
    ),
    path("appointments/<uuid:appointment_id>/payments/add/", views.appointment_add_payment, name="appointment-add-payment"),
    path(
        "appointments/<uuid:appointment_id>/payments/<uuid:payment_id>/delete/",
        views.appointment_delete_payment,
        name="appointment-delete-payment",
    ),
    path("settings/payments/", views.payment_settings, name="payment-settings"),
    path("cash-shift/", views.cash_shift, name="cash-shift"),
    path("quality/", views.quality_list, name="quality-list"),
    path("quality/new/", views.complaint_form, name="complaint-create"),
    path("quality/<uuid:complaint_id>/status/", views.complaint_status_update, name="complaint-status-update"),
    path("quality/<uuid:complaint_id>/", views.complaint_detail, name="complaint-detail"),
    path("quality/<uuid:complaint_id>/edit/", views.complaint_form, name="complaint-edit"),
    path("quality/<uuid:complaint_id>/photos/<uuid:photo_id>/delete/", views.complaint_photo_delete, name="complaint-photo-delete"),
]
