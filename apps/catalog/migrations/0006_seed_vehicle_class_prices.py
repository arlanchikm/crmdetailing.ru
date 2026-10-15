from django.db import migrations


def seed_prices(apps, schema_editor):
    Service = apps.get_model("catalog", "Service")
    Price = apps.get_model("catalog", "ServiceVehicleClassPrice")
    VehicleClass = apps.get_model("customers", "VehicleClass")
    for service in Service.objects.all():
        for vehicle_class in VehicleClass.objects.filter(tenant_id=service.tenant_id, active=True):
            Price.objects.get_or_create(
                tenant_id=service.tenant_id, service=service, vehicle_class=vehicle_class,
                defaults={"price": service.price},
            )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0005_servicevehicleclassprice"),
        ("customers", "0006_seed_vehicle_classes"),
    ]
    operations = [migrations.RunPython(seed_prices, migrations.RunPython.noop)]
