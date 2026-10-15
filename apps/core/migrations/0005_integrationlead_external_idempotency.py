from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0004_integrationchannel_integrationlead")]

    operations = [
        migrations.AddConstraint(
            model_name="integrationlead",
            constraint=models.UniqueConstraint(
                condition=~models.Q(external_id=""),
                fields=("tenant", "source", "external_id"),
                name="unique_external_lead_per_source",
            ),
        ),
    ]
