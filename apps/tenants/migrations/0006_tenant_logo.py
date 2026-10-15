from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenants", "0005_seed_working_hours")]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="logo",
            field=models.FileField(blank=True, upload_to="company-logos/%Y/%m/", verbose_name="логотип"),
        ),
    ]
