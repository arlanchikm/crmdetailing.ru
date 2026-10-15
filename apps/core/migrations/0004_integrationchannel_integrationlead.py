import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_remove_discount_approval_notifications"),
        ("tenants", "0009_tenant_dark_theme_and_english"),
    ]

    operations = [
        migrations.CreateModel(
            name="IntegrationChannel",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("website", "Сайт"), ("avito", "Avito"), ("telephony", "Телефония")], max_length=24)),
                ("enabled", models.BooleanField(default=False)),
                ("public_token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("settings", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="integration_channels", to="tenants.tenant")),
            ],
            options={"ordering": ("kind",)},
        ),
        migrations.AddConstraint(
            model_name="integrationchannel",
            constraint=models.UniqueConstraint(fields=("tenant", "kind"), name="unique_integration_channel"),
        ),
        migrations.CreateModel(
            name="IntegrationLead",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("source", models.CharField(choices=[("website", "Сайт"), ("avito", "Avito"), ("telephony", "Телефония")], max_length=24)),
                ("status", models.CharField(choices=[("new", "Новая"), ("processed", "Обработана"), ("rejected", "Отклонена")], default="new", max_length=16)),
                ("external_id", models.CharField(blank=True, max_length=160)),
                ("client_name", models.CharField(max_length=160)),
                ("phone", models.CharField(max_length=40)),
                ("car", models.CharField(blank=True, max_length=180)),
                ("service_name", models.CharField(blank=True, max_length=180)),
                ("preferred_at", models.DateTimeField(blank=True, null=True)),
                ("comment", models.TextField(blank=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("channel", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="leads", to="core.integrationchannel")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="integration_leads", to="tenants.tenant")),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.AddIndex(
            model_name="integrationlead",
            index=models.Index(fields=["tenant", "source", "status", "created_at"], name="core_integr_tenant__a3e4f6_idx"),
        ),
    ]
