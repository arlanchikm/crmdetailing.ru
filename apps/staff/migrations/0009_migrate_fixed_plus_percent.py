from django.db import migrations


def forward(apps, schema_editor):
    Employee = apps.get_model("staff", "Employee")
    Employee.objects.filter(salary_mode="overall_percent").update(salary_mode="fixed_plus_percent")


def backward(apps, schema_editor):
    Employee = apps.get_model("staff", "Employee")
    Employee.objects.filter(salary_mode="fixed_plus_percent").update(salary_mode="overall_percent")


class Migration(migrations.Migration):
    dependencies = [("staff", "0008_alter_employee_fixed_monthly_salary_and_more")]
    operations = [migrations.RunPython(forward, backward)]
