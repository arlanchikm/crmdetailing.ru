from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalog", "0010_assign_uncategorized_services")]

    operations = [
        migrations.AddField(
            model_name="service",
            name="calculation_type",
            field=models.CharField(
                choices=[("service", "За услугу"), ("unit", "За единицу")],
                default="service",
                max_length=12,
                verbose_name="тип расчета услуги",
            ),
        ),
    ]
