from django.contrib import admin

from .models import Car, Client


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email", "tenant", "created_at")
    search_fields = ("name", "phone", "normalized_phone", "email")
    list_filter = ("tenant",)


@admin.register(Car)
class CarAdmin(admin.ModelAdmin):
    list_display = ("make", "model", "plate", "client", "tenant")
    search_fields = ("make", "model", "plate", "normalized_plate", "vin")
    list_filter = ("tenant",)

