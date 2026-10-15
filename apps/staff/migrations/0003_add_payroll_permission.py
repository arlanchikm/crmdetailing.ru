from django.db import migrations


def add_payroll_permission(apps, schema_editor):
    CompanyRole = apps.get_model("staff", "CompanyRole")
    for role in CompanyRole.objects.filter(builtin_key="admin"):
        permissions = list(role.permissions or [])
        if "payroll_view" not in permissions:
            permissions.append("payroll_view")
            role.permissions = permissions
            role.save(update_fields=("permissions",))


class Migration(migrations.Migration):
    dependencies = [("staff", "0002_employee_user_companyrole_employee_company_role_and_more")]
    operations = [migrations.RunPython(add_payroll_permission, migrations.RunPython.noop)]
