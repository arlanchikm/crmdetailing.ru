from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_snapshots(apps, schema_editor):
    Appointment = apps.get_model("operations", "Appointment")
    for appointment in Appointment.objects.select_related("client", "car").iterator():
        client = appointment.client
        car = appointment.car
        appointment.client_name_snapshot = " ".join(
            part for part in (client.name, client.last_name) if part
        ).strip()
        appointment.client_phone_snapshot = client.phone
        appointment.car_snapshot = (
            f"{car.make} {car.model}".strip() or car.plate or "Автомобиль не указан"
        )
        appointment.car_make_snapshot = car.make
        appointment.car_model_snapshot = car.model
        appointment.car_plate_snapshot = car.plate
        appointment.save(update_fields=(
            "client_name_snapshot", "client_phone_snapshot", "car_snapshot",
            "car_make_snapshot", "car_model_snapshot", "car_plate_snapshot",
        ))


class Migration(migrations.Migration):
    dependencies = [
        ("operations", "0019_appointment_source"),
        ("staff", "0013_appointment_salary_mode_and_permanent_delete"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(model_name="appointment", name="client_name_snapshot", field=models.CharField(blank=True, max_length=321)),
        migrations.AddField(model_name="appointment", name="client_phone_snapshot", field=models.CharField(blank=True, max_length=32)),
        migrations.AddField(model_name="appointment", name="car_snapshot", field=models.CharField(blank=True, max_length=190)),
        migrations.AddField(model_name="appointment", name="car_make_snapshot", field=models.CharField(blank=True, max_length=80)),
        migrations.AddField(model_name="appointment", name="car_model_snapshot", field=models.CharField(blank=True, max_length=80)),
        migrations.AddField(model_name="appointment", name="car_plate_snapshot", field=models.CharField(blank=True, max_length=24)),
        migrations.AddField(
            model_name="appointment", name="created_by_employee",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_appointments", to="staff.employee", verbose_name="создал запись"),
        ),
        migrations.RunPython(backfill_snapshots, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="appointment", name="car",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="appointments", to="customers.car"),
        ),
        migrations.AlterField(
            model_name="appointment", name="client",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="appointments", to="customers.client"),
        ),
    ]
