from django.contrib import admin

from .models import (
    Appointment, AppointmentAssignee, AppointmentService,
    CashCollection, CashShift, ClientRating, Complaint, ComplaintPhoto,
    PaymentSettings, PaymentTransaction,
)


class AppointmentServiceInline(admin.TabularInline):
    model = AppointmentService
    extra = 0


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = (
        "start_at", "client", "car", "employee", "post", "status",
        "payment_status", "total", "tenant"
    )
    list_filter = ("tenant", "status", "payment_status", "employee", "post")
    search_fields = ("client__name", "car__plate")
    inlines = (AppointmentServiceInline,)


admin.site.register(AppointmentAssignee)
admin.site.register(PaymentTransaction)
admin.site.register(PaymentSettings)
admin.site.register(CashShift)
admin.site.register(CashCollection)
admin.site.register(Complaint)
admin.site.register(ComplaintPhoto)
admin.site.register(ClientRating)
