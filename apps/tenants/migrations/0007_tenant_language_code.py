from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenants", "0006_tenant_logo")]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="language_code",
            field=models.CharField(
                choices=[("ru", "Русский")], default="ru", max_length=10,
                verbose_name="язык интерфейса",
            ),
        ),
    ]
