from django.db import migrations


def apply_pending_discounts(apps, schema_editor):
    Appointment = apps.get_model("operations", "Appointment")
    for appointment in Appointment.objects.filter(discount_status="pending"):
        appointment.discount_status = "applied" if appointment.discount_amount else "none"
        appointment.total = max(appointment.subtotal - appointment.discount_amount, 0)
        appointment.save(update_fields=("discount_status", "total"))


class Migration(migrations.Migration):
    dependencies = [("operations", "0014_seed_appointment_vehicle_classes")]
    operations = [migrations.RunPython(apply_pending_discounts, migrations.RunPython.noop)]
