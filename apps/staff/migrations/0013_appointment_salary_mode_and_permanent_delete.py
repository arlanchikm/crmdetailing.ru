from django.db import migrations, models


def grant_permanent_delete(apps, schema_editor):
    CompanyRole = apps.get_model("staff", "CompanyRole")
    for role in CompanyRole.objects.filter(builtin_key__in=("owner", "admin")):
        permissions = list(role.permissions or [])
        if "clients_permanent_delete" not in permissions:
            permissions.append("clients_permanent_delete")
            role.permissions = permissions
            role.save(update_fields=("permissions",))


class Migration(migrations.Migration):
    dependencies = [("staff", "0012_client_delete_permission")]
    operations = [
        migrations.AlterField(
            model_name="employee",
            name="salary_mode",
            field=models.CharField(
                choices=[
                    ("service_percent", "% по услугам"),
                    ("fixed_plus_percent", "Фиксированная ставка + % от услуг"),
                    ("fixed_plus_appointment_percent", "Фиксированная ставка + % от записей"),
                    ("fixed_monthly", "Фиксированная ставка за месяц"),
                ],
                default="service_percent", max_length=36, verbose_name="схема зарплаты",
            ),
        ),
        migrations.RunPython(grant_permanent_delete, migrations.RunPython.noop),
    ]
