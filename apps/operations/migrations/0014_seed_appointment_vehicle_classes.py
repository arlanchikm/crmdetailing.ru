from django.db import migrations


def seed_snapshots(apps, schema_editor):
    AppointmentService = apps.get_model("operations", "AppointmentService")
    for item in AppointmentService.objects.select_related("appointment__car__vehicle_class"):
        vehicle_class = item.appointment.car.vehicle_class
        if vehicle_class:
            item.vehicle_class = vehicle_class
            item.vehicle_class_name_snapshot = vehicle_class.name
            item.save(update_fields=("vehicle_class", "vehicle_class_name_snapshot"))


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0006_seed_vehicle_class_prices"),
        ("operations", "0013_appointmentservice_vehicle_class_and_more"),
    ]
    operations = [migrations.RunPython(seed_snapshots, migrations.RunPython.noop)]
