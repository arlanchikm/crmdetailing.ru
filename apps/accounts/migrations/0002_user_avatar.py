from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="avatar",
            field=models.FileField(blank=True, upload_to="profiles/%Y/%m/", verbose_name="фотография профиля"),
        ),
    ]
