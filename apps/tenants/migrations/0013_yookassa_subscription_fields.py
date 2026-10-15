from django.db import migrations, models
import uuid


class Migration(migrations.Migration):
    dependencies = [("tenants", "0012_networkrequest_phone_needs_review_and_more")]

    operations = [
        migrations.AddField(
            model_name="subscription",
            name="provider_payment_method_id",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="subscriptionorder",
            name="auto_renew_requested",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="subscriptionorder",
            name="provider_idempotence_key",
            field=models.UUIDField(default=uuid.uuid4, editable=False),
        ),
        migrations.AddField(
            model_name="subscriptionorder",
            name="provider_status",
            field=models.CharField(blank=True, max_length=32),
        ),
    ]
