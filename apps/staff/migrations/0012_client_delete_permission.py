from django.db import migrations


def grant_default_delete(apps, schema_editor):
    Role=apps.get_model('staff','CompanyRole')
    for role in Role.objects.filter(builtin_key__in=['owner','admin']).iterator():
        if 'clients_delete' not in (role.permissions or []):
            role.permissions=[*(role.permissions or []),'clients_delete']
            role.save(update_fields=('permissions',))


class Migration(migrations.Migration):
    dependencies=[('staff','0011_remove_employee_unique_employee_name_per_tenant_and_more')]
    operations=[migrations.RunPython(grant_default_delete,migrations.RunPython.noop)]
