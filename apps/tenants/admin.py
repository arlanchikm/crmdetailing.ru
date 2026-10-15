from django.contrib import admin

from .models import (
    Location, Membership, MembershipLocation, Plan, Subscription, SubscriptionHistory, Tenant,
)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "theme_key", "timezone", "created_at")
    list_filter = ("status", "theme_key")
    search_fields = ("name", "slug")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "tenant", "role", "status", "created_at")
    list_filter = ("role", "status")
    search_fields = ("user__email", "tenant__name", "tenant__slug")


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "monthly_price", "active", "sort_order")
    list_editable = ("monthly_price", "active", "sort_order")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("tenant", "plan", "status", "billing_period", "ends_at", "trial_used")
    list_filter = ("plan", "status", "billing_period", "trial_used")
    search_fields = ("tenant__name", "tenant__slug")
    readonly_fields = ("created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        previous = Subscription.objects.filter(pk=obj.pk).select_related("plan").first() if change else None
        super().save_model(request, obj, form, change)
        SubscriptionHistory.objects.create(
            subscription=obj, actor=request.user, action="admin_update",
            from_plan=previous.plan if previous else None, to_plan=obj.plan,
            details={"changed_fields": list(form.changed_data)},
        )
        from apps.core.models import log_event
        log_event(
            tenant=obj.tenant, actor=request.user, action="subscription_admin_update",
            entity=obj, description=f"Подписка изменена: {obj.plan.name}, {obj.get_status_display()}",
            changes={"fields": list(form.changed_data)},
        )


@admin.register(SubscriptionHistory)
class SubscriptionHistoryAdmin(admin.ModelAdmin):
    list_display = ("subscription", "action", "from_plan", "to_plan", "actor", "created_at")
    readonly_fields = ("subscription", "action", "from_plan", "to_plan", "actor", "details", "created_at")

    def has_add_permission(self, request):
        return False


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "code", "is_primary", "active")
    list_filter = ("active", "is_primary")
    search_fields = ("name", "tenant__name", "tenant__slug")


admin.site.register(MembershipLocation)
