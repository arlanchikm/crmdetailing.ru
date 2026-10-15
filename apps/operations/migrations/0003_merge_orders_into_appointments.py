from datetime import timedelta

from django.db import migrations, models


def merge_orders_into_appointments(apps, schema_editor):
    Appointment = apps.get_model("operations", "Appointment")
    AppointmentService = apps.get_model("operations", "AppointmentService")
    Order = apps.get_model("operations", "Order")

    appointment_statuses = {
        "draft": "waiting",
        "confirmed": "waiting",
        "arrived": "in_progress",
        "converted": "completed",
        "cancelled": "completed",
        "no_show": "completed",
    }
    order_statuses = {
        "draft": "waiting",
        "accepted": "waiting",
        "in_progress": "in_progress",
        "ready": "completed",
        "closed": "completed",
        "cancelled": "completed",
    }

    for appointment in Appointment.objects.all().iterator():
        appointment.status = appointment_statuses.get(appointment.status, "waiting")
        appointment.save(update_fields=("status",))

    for order in Order.objects.prefetch_related("items").all().iterator(chunk_size=200):
        if order.appointment_id:
            appointment = Appointment.objects.get(pk=order.appointment_id)
            appointment.status = order_statuses.get(order.work_status, appointment.status)
            appointment.payment_status = order.payment_status
            appointment.total = order.total
            if order.comment and not appointment.comment:
                appointment.comment = order.comment
            appointment.save(
                update_fields=("status", "payment_status", "total", "comment")
            )
            continue

        items = list(order.items.all())
        duration_minutes = sum(item.duration_minutes_snapshot for item in items) or 60
        appointment = Appointment.objects.create(
            tenant_id=order.tenant_id,
            client_id=order.client_id,
            car_id=order.car_id,
            employee_id=order.employee_id,
            post_id=order.post_id,
            start_at=order.planned_start_at,
            end_at=order.planned_start_at + timedelta(minutes=duration_minutes),
            status=order_statuses.get(order.work_status, "waiting"),
            payment_status=order.payment_status,
            total=order.total,
            comment=order.comment,
        )
        AppointmentService.objects.bulk_create(
            [
                AppointmentService(
                    tenant_id=order.tenant_id,
                    appointment_id=appointment.id,
                    service_id=item.service_id,
                    name_snapshot=item.name_snapshot,
                    price_snapshot=item.price,
                    duration_minutes_snapshot=item.duration_minutes_snapshot,
                )
                for item in items
                if item.service_id
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("operations", "0002_appointment_employee_order_employee_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="appointment",
            old_name="expected_price",
            new_name="total",
        ),
        migrations.AlterField(
            model_name="appointment",
            name="total",
            field=models.DecimalField(
                "стоимость", max_digits=14, decimal_places=2, default=0
            ),
        ),
        migrations.AddField(
            model_name="appointment",
            name="payment_status",
            field=models.CharField(
                choices=[
                    ("unpaid", "Не оплачено"),
                    ("partial", "Частично оплачено"),
                    ("paid", "Оплачено"),
                    ("refunded", "Возвращено"),
                ],
                default="unpaid",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="appointment",
            name="status",
            field=models.CharField(
                choices=[
                    ("waiting", "Ожидание"),
                    ("in_progress", "В работе"),
                    ("completed", "Завершено"),
                ],
                default="waiting",
                max_length=16,
            ),
        ),
        migrations.RunPython(merge_orders_into_appointments, migrations.RunPython.noop),
        migrations.DeleteModel(name="OrderItem"),
        migrations.DeleteModel(name="Order"),
        migrations.DeleteModel(name="OrderSequence"),
    ]
