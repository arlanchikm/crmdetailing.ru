from django.db import migrations


def seed_hours(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    Hours = apps.get_model("tenants", "TenantWorkingHours")
    for tenant in Tenant.objects.all():
        for weekday in range(7):
            Hours.objects.get_or_create(tenant=tenant, weekday=weekday)


class Migration(migrations.Migration):
    dependencies = [("tenants", "0004_tenantworkinghours")]
    operations = [migrations.RunPython(seed_hours, migrations.RunPython.noop)]
