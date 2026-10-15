from django.contrib import admin

from .models import AppointmentConsumption, Consumable, InventoryCategory, InventoryMovement, ServiceConsumableNorm


admin.site.register(Consumable)
admin.site.register(AppointmentConsumption)
admin.site.register(InventoryCategory)
admin.site.register(ServiceConsumableNorm)
admin.site.register(InventoryMovement)
