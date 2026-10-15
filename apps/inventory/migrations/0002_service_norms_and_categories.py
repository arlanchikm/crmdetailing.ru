import uuid

import django.core.validators
import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


DEFAULT_CATEGORIES = (
    ("chemistry", "Химия", "liter"),
    ("microfiber", "Фибры", "piece"),
    ("polishing_pad", "Полировальные круги", "piece"),
    ("polishing_compound", "Полировальные пасты", "liter"),
)


def migrate_categories(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    Category = apps.get_model("inventory", "InventoryCategory")
    Consumable = apps.get_model("inventory", "Consumable")
    for tenant in Tenant.objects.all():
        categories = {}
        for key, name, unit in DEFAULT_CATEGORIES:
            categories[key] = Category.objects.create(
                tenant=tenant, name=name, unit=unit, builtin_key=key
            )
        for consumable in Consumable.objects.filter(tenant=tenant):
            category = categories.get(consumable.category) or categories["chemistry"]
            consumable.category_ref = category
            consumable.save(update_fields=("category_ref",))


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="InventoryCategory",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=120, verbose_name="название")),
                ("unit", models.CharField(choices=[("liter", "л"), ("piece", "шт.")], max_length=12, verbose_name="формат исчисления")),
                ("builtin_key", models.CharField(blank=True, max_length=32)),
                ("active", models.BooleanField(default=True, verbose_name="активна")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="inventory_categories", to="tenants.tenant")),
            ],
            options={"verbose_name": "категория расходников", "verbose_name_plural": "категории расходников", "ordering": ("name",)},
        ),
        migrations.AddField(
            model_name="consumable",
            name="category_ref",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="consumables", to="inventory.inventorycategory", verbose_name="категория"),
        ),
        migrations.RunPython(migrate_categories, migrations.RunPython.noop),
        migrations.RenameField(model_name="consumable", old_name="category", new_name="legacy_category"),
        migrations.RenameField(model_name="consumable", old_name="unit", new_name="legacy_unit"),
        migrations.RenameField(model_name="consumable", old_name="consumption_per_car", new_name="legacy_consumption_per_car"),
        migrations.AlterField(model_name="consumable", name="legacy_category", field=models.CharField(blank=True, editable=False, max_length=32)),
        migrations.AlterField(model_name="consumable", name="legacy_unit", field=models.CharField(blank=True, editable=False, max_length=12)),
        migrations.AlterField(model_name="consumable", name="legacy_consumption_per_car", field=models.DecimalField(decimal_places=3, default=0, editable=False, max_digits=14)),
        migrations.RenameField(model_name="consumable", old_name="category_ref", new_name="category"),
        migrations.CreateModel(
            name="ServiceConsumableNorm",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=14, validators=[django.core.validators.MinValueValidator(Decimal("0.001"))], verbose_name="норматив")),
                ("consumable", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="service_norms", to="inventory.consumable")),
                ("service", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="consumable_norms", to="catalog.service")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="service_consumable_norms", to="tenants.tenant")),
            ],
            options={"ordering": ("consumable__name",)},
        ),
        migrations.AddConstraint(
            model_name="inventorycategory",
            constraint=models.UniqueConstraint(fields=("tenant", "name"), name="unique_inventory_category_per_tenant"),
        ),
        migrations.AddConstraint(
            model_name="inventorycategory",
            constraint=models.UniqueConstraint(condition=models.Q(("builtin_key", ""), _negated=True), fields=("tenant", "builtin_key"), name="unique_builtin_inventory_category_per_tenant"),
        ),
        migrations.AddConstraint(
            model_name="serviceconsumablenorm",
            constraint=models.UniqueConstraint(fields=("service", "consumable"), name="unique_consumable_norm_per_service"),
        ),
    ]
