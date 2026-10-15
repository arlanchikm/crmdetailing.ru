from django.contrib import admin

from .models import Service, WorkPost


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "duration_minutes", "active", "tenant")
    list_filter = ("tenant", "active")
    search_fields = ("name",)


@admin.register(WorkPost)
class WorkPostAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "active", "tenant")
    list_filter = ("tenant", "kind", "active")
    search_fields = ("name",)

