from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]
    operations = [
        migrations.RenameField(model_name="service", old_name="cost", new_name="legacy_cost"),
        migrations.AlterField(
            model_name="service",
            name="legacy_cost",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=14),
        ),
    ]
