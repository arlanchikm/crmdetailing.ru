from django.contrib import admin

from .models import CompanyRole, Employee


admin.site.register(CompanyRole)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "company_role", "active", "sort_order")
    list_filter = ("tenant", "active", "color")
    search_fields = ("name", "position")
