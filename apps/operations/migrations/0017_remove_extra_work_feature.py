from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("operations", "0016_appointment_archive_reason_appointment_archived_at_and_more")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[migrations.DeleteModel(name="AppointmentExtraWork")],
        ),
    ]
