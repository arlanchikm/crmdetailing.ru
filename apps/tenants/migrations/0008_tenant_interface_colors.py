from django.db import migrations, models

import apps.tenants.models


class Migration(migrations.Migration):
    dependencies = [("tenants", "0007_tenant_language_code")]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="primary_color",
            field=models.CharField(
                default="#087CFF",
                max_length=7,
                validators=(apps.tenants.models.hex_color_validator,),
                verbose_name="основной цвет",
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="background_color",
            field=models.CharField(
                default="#DCEBFA",
                max_length=7,
                validators=(apps.tenants.models.hex_color_validator,),
                verbose_name="оттенок фона",
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="surface_color",
            field=models.CharField(
                default="#FFFFFF",
                max_length=7,
                validators=(apps.tenants.models.hex_color_validator,),
                verbose_name="оттенок блоков",
            ),
        ),
    ]
