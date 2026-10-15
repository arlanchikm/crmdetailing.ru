from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('tenants', '0013_yookassa_subscription_fields'), ('accounts', '0005_legalacceptance')]
    operations = [
        migrations.AddField(model_name='platformaccess', name='email',
            field=models.EmailField('Email платформы', unique=True, null=True, blank=True, max_length=254)),
    ]
