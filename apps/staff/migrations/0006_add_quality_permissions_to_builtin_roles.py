from django.db import migrations


def add_quality_permissions(apps, schema_editor):
    CompanyRole = apps.get_model("staff", "CompanyRole")
    additions = {
        "admin": ["quality_view", "quality_manage"],
    }
    for key, permissions in additions.items():
        for role in CompanyRole.objects.filter(builtin_key=key):
            current = list(role.permissions or [])
            changed = False
            for permission in permissions:
                if permission not in current:
                    current.append(permission)
                    changed = True
            if changed:
                role.permissions = current
                role.save(update_fields=("permissions",))


class Migration(migrations.Migration):
    dependencies = [("staff", "0005_companyrole_max_discount_percent_and_more")]

    operations = [migrations.RunPython(add_quality_permissions, migrations.RunPython.noop)]
