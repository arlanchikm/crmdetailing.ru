from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("staff", "0009_migrate_fixed_plus_percent")]
    operations = [
        migrations.RemoveField(model_name="companyrole", name="max_discount_percent"),
    ]
