from django.db import migrations


def seed_classes(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    VehicleClass = apps.get_model("customers", "VehicleClass")
    Car = apps.get_model("customers", "Car")
    defaults = (("sedan", "Седан"), ("crossover", "Паркетник"), ("suv", "Внедорожник"))
    for tenant in Tenant.objects.all():
        rows = {}
        for key, name in defaults:
            rows[key], _ = VehicleClass.objects.get_or_create(
                tenant=tenant, builtin_key=key, defaults={"name": name, "active": True}
            )
        Car.objects.filter(tenant=tenant, vehicle_class__isnull=True).update(
            vehicle_class=rows["sedan"]
        )


class Migration(migrations.Migration):
    dependencies = [("customers", "0005_vehicleclass_car_vehicle_class_and_more")]
    operations = [migrations.RunPython(seed_classes, migrations.RunPython.noop)]
