from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0004_appointment_completed_at_appointment_work_started_at")]
    operations = [
        migrations.AddField(
            model_name="appointmentservice",
            name="consumables_snapshot",
            field=models.JSONField(blank=True, default=list),
        )
    ]
