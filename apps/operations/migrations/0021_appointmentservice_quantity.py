import django.core.validators
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("operations", "0020_optional_car_snapshots_and_creator")]

    operations = [
        migrations.AddField(
            model_name="appointmentservice",
            name="quantity",
            field=models.PositiveIntegerField(
                default=1,
                validators=[django.core.validators.MinValueValidator(1)],
                verbose_name="количество",
            ),
        ),
        migrations.AddConstraint(
            model_name="appointmentservice",
            constraint=models.CheckConstraint(
                condition=Q(quantity__gte=1),
                name="appointment_service_quantity_gte_1",
            ),
        ),
    ]
