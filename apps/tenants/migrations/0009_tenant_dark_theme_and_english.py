from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenants", "0008_tenant_interface_colors")]

    operations = [
        migrations.AlterField(
            model_name="tenant",
            name="language_code",
            field=models.CharField(
                choices=[("ru", "Русский"), ("en", "English")],
                default="ru",
                max_length=10,
                verbose_name="язык интерфейса",
            ),
        ),
        migrations.AlterField(
            model_name="tenant",
            name="theme_key",
            field=models.CharField(
                choices=[("light_blue", "Светлая синяя"), ("dark", "Тёмная")],
                default="light_blue",
                max_length=32,
                verbose_name="тема оформления",
            ),
        ),
    ]
