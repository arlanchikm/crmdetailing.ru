from decimal import Decimal

from django.db import transaction
from django.db.models import F

from django.core.exceptions import ValidationError
from apps.core.models import management_notification_recipients, notify
from apps.tenants.plans import tenant_has_feature

from .models import AppointmentConsumption, Consumable, InventoryMovement


def notify_low_stock(consumable):
    if not tenant_has_feature(consumable.tenant, "inventory_automation"):
        return
    if not consumable.is_low_stock:
        return
    severity = "critical" if consumable.planned_stock <= 0 else "warning"
    for recipient in management_notification_recipients(consumable.tenant):
        notify(
            tenant=consumable.tenant, kind="low_stock", severity=severity, recipient=recipient,
            title=(f"Закончился расходник: {consumable.name}" if severity == "critical" else f"Заканчивается {consumable.name}"),
            message=f"План: {consumable.planned_stock} {consumable.unit_display}",
            url="/inventory/", dedupe_key=f"low-stock:{consumable.id}",
        )


@transaction.atomic
def apply_stock_movement(*, consumable, kind, quantity, actor, reason="", unit_cost=None):
    consumable = Consumable.objects.select_for_update().get(pk=consumable.pk)
    old_plan, old_fact = consumable.planned_stock, consumable.actual_stock
    if kind == InventoryMovement.Kind.RECEIPT:
        consumable.planned_stock += quantity
        consumable.actual_stock += quantity
    elif kind == InventoryMovement.Kind.MANUAL_WRITEOFF:
        if quantity > consumable.actual_stock:
            raise ValidationError("Нельзя списать больше фактического остатка.")
        consumable.planned_stock -= quantity
        consumable.actual_stock -= quantity
    elif kind in (InventoryMovement.Kind.CORRECTION, InventoryMovement.Kind.INVENTORY):
        consumable.planned_stock = quantity
        consumable.actual_stock = quantity
    else:
        raise ValidationError("Недопустимая складская операция.")
    if unit_cost is not None:
        consumable.cost_per_unit = unit_cost
    consumable.save(update_fields=("planned_stock", "actual_stock", "cost_per_unit", "updated_at"))
    movement = InventoryMovement.objects.create(
        tenant=consumable.tenant, location=consumable.location,
        consumable=consumable, kind=kind,
        planned_delta=consumable.planned_stock - old_plan,
        actual_delta=consumable.actual_stock - old_fact,
        planned_after=consumable.planned_stock, actual_after=consumable.actual_stock,
        unit_cost_snapshot=consumable.cost_per_unit, reason=reason.strip(), created_by=actor,
    )
    notify_low_stock(consumable)
    return movement


@transaction.atomic
def write_off_consumables(appointment, actor=None):
    """Writes off service norms once while preserving the manually counted stock."""
    if not tenant_has_feature(appointment.tenant, "inventory_automation"):
        return appointment.consumption_entries.none()
    aggregated = {}
    items = appointment.items.select_related("service").all()
    for item in items:
        snapshots = item.consumables_snapshot
        if not snapshots:
            multiplier = Decimal(item.quantity)
            snapshots = [
                {
                    "consumable_id": str(norm.consumable_id),
                    "stock_quantity": str(norm.stock_quantity * multiplier),
                    "unit_cost": str(norm.consumable.cost_per_unit),
                    "total_cost": str(norm.total_cost * multiplier),
                }
                for norm in item.service.norms_for_class(
                    item.vehicle_class or (appointment.car.vehicle_class if appointment.car_id else None)
                )
            ]
        for snapshot in snapshots:
            consumable_id = snapshot["consumable_id"]
            row = aggregated.setdefault(
                consumable_id,
                {"quantity": Decimal("0"), "unit_cost": Decimal(snapshot["unit_cost"]), "total_cost": Decimal("0")},
            )
            row["quantity"] += Decimal(snapshot["stock_quantity"])
            row["total_cost"] += Decimal(snapshot.get("total_cost", "0"))

    consumables = {
        str(item.id): item
        for item in Consumable.objects.select_for_update().filter(
            tenant=appointment.tenant, location=appointment.location, id__in=aggregated.keys()
        ).order_by("pk")
    }
    for consumable_id, values in aggregated.items():
        consumable = consumables.get(consumable_id)
        if not consumable:
            continue
        quantity = values["quantity"]
        unit_cost = values["unit_cost"]
        _, created = AppointmentConsumption.objects.get_or_create(
            tenant=appointment.tenant,
            appointment=appointment,
            consumable=consumable,
            defaults={
                "quantity": quantity,
                "unit_cost_snapshot": unit_cost,
                "total_cost": values["total_cost"].quantize(Decimal("0.01")),
            },
        )
        if created:
            Consumable.objects.filter(pk=consumable.pk).update(
                planned_stock=F("planned_stock") - quantity
            )
            consumable.refresh_from_db(fields=("planned_stock", "actual_stock"))
            InventoryMovement.objects.create(
                tenant=appointment.tenant, location=appointment.location,
                consumable=consumable, appointment=appointment,
                kind=InventoryMovement.Kind.NORMATIVE_WRITEOFF,
                planned_delta=-quantity, actual_delta=Decimal("0"),
                planned_after=consumable.planned_stock, actual_after=consumable.actual_stock,
                unit_cost_snapshot=unit_cost, reason="Завершение записи",
                created_by=actor,
            )
            notify_low_stock(consumable)
    return appointment.consumption_entries.all()
