from django.db import migrations


def remove_discount_notifications(apps, schema_editor):
    Notification = apps.get_model("core", "Notification")
    Notification.objects.filter(kind="discount").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_notification"),
        ("operations", "0015_apply_pending_discounts"),
    ]
    operations = [migrations.RunPython(remove_discount_notifications, migrations.RunPython.noop)]
