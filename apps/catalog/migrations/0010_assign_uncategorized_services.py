from django.db import migrations


def assign_uncategorized_services(apps, schema_editor):
    Service = apps.get_model("catalog", "Service")
    ServiceCategory = apps.get_model("catalog", "ServiceCategory")

    remaining = Service.objects.filter(category__isnull=True, system_key="")
    for service in remaining.iterator():
        category = ServiceCategory.objects.filter(
            tenant_id=service.tenant_id,
            location_id=service.location_id,
            builtin_key="detailing",
        ).first()
        if category is not None:
            Service.objects.filter(pk=service.pk, category__isnull=True).update(category_id=category.pk)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0009_servicecategory_service_category_and_more")]

    operations = [migrations.RunPython(assign_uncategorized_services, migrations.RunPython.noop)]
