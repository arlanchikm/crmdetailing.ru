from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model

from apps.catalog.models import Service

from .models import CompanyRole, Employee, EmployeeService, ensure_default_roles


class EmployeeForm(forms.ModelForm):
    crm_access = forms.ChoiceField(label="Пользователь CRM", required=False)
    available_services = forms.ModelMultipleChoiceField(
        label="Услуги сотрудника", queryset=Service.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    access_email = forms.EmailField(
        label="Email для входа", required=False,
        help_text="Заполните вместе с временным паролем, чтобы создать доступ к CRM.",
    )
    temporary_password = forms.CharField(
        label="Временный пароль", required=False, min_length=8,
        widget=forms.PasswordInput(render_value=True),
    )
    def __init__(self, *args, tenant=None, location=None, advanced_payroll=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.location = location
        self.advanced_payroll = advanced_payroll
        self.is_existing = not self.instance._state.adding
        self.can_create_login = not bool(self.is_existing and self.instance.user_id)
        if self.is_existing and self.instance.user_id:
            self.fields["crm_access"].choices = (
                ("enabled", self.instance.user.email),
                ("none", "Без доступа к CRM"),
            )
            self.fields["crm_access"].initial = "enabled"
        else:
            self.fields["crm_access"].choices = (
                ("enabled", "С доступом к CRM"),
                ("none", "Без доступа к CRM"),
            )
            self.fields["crm_access"].initial = "none" if self.is_existing else "enabled"
        if tenant:
            ensure_default_roles(tenant)
            self.fields["company_role"].queryset = CompanyRole.objects.filter(
                tenant=tenant, active=True
            ).exclude(builtin_key="owner")
            if self.instance.company_role_id and self.instance.company_role.builtin_key == "owner":
                self.fields["company_role"].queryset = CompanyRole.objects.filter(pk=self.instance.company_role_id)
                self.fields["company_role"].disabled = True
                self.fields["crm_access"].disabled = True
            services = list(Service.objects.filter(tenant=tenant, location=location, active=True))
            self.fields["available_services"].queryset = Service.objects.filter(
                tenant=tenant, location=location, active=True
            )
            existing = {
                row.service_id: row
                for row in self.instance.service_settings.select_related("service").all()
            } if self.instance.pk else {}
            if self.instance.pk and not self.is_bound:
                self.initial["available_services"] = list(existing)
            selected = set(self.data.getlist("available_services")) if self.is_bound else {
                str(value) for value in self.initial.get("available_services", [])
            }
            self.service_rows = []
            for service in services:
                field_name = f"service_percent_{service.id}"
                row = existing.get(service.id)
                initial = row.percent_override if row and row.percent_override is not None else service.employee_percent
                self.fields[field_name] = forms.DecimalField(
                    label=f"Процент — {service.name}", min_value=0, max_value=100,
                    decimal_places=2, required=False, initial=initial,
                )
                self.service_rows.append({
                    "service": service, "field": self[field_name],
                    "selected": str(service.id) in selected,
                    "default_percent": service.employee_percent,
                })
        self.fields["company_role"].empty_label = "Выберите должность"
        self.fields["salary_percent"].required = False
        self.fields["fixed_monthly_salary"].required = False
        self.fields["salary_mode"].required = False
        self.fields["salary_mode"].initial = self.instance.salary_mode or Employee.SalaryMode.SERVICE_PERCENT
        if not advanced_payroll:
            self.fields["salary_mode"].choices = [
                (Employee.SalaryMode.SERVICE_PERCENT, Employee.SalaryMode.SERVICE_PERCENT.label)
            ]
            self.fields["salary_mode"].initial = Employee.SalaryMode.SERVICE_PERCENT
            self.fields["fixed_monthly_salary"].disabled = True
            self.fields["salary_percent"].disabled = True
            for row in getattr(self, "service_rows", []):
                row["field"].field.disabled = True
    def clean(self):
        cleaned = super().clean()
        if not self.advanced_payroll:
            submitted_mode = cleaned.get("salary_mode") or Employee.SalaryMode.SERVICE_PERCENT
            if submitted_mode != Employee.SalaryMode.SERVICE_PERCENT:
                self.add_error("salary_mode", "Эта схема оплаты доступна на тарифе «Бизнес».")
            else:
                cleaned["salary_mode"] = Employee.SalaryMode.SERVICE_PERCENT
                self.instance.salary_mode = Employee.SalaryMode.SERVICE_PERCENT
        access = cleaned.get("crm_access")
        if not access:
            # Keep older integrations safe: an omitted control must never create
            # a login implicitly. The interactive form still defaults to access.
            access = "enabled" if self.instance.user_id else "none"
            cleaned["crm_access"] = access
        email = (cleaned.get("access_email") or "").lower().strip()
        password = cleaned.get("temporary_password")
        if access == "enabled" and not self.instance.user_id:
            if not email:
                self.add_error("access_email", "Введите email для входа.")
            elif get_user_model().objects.filter(email__iexact=email).exists():
                self.add_error("access_email", "Этот email уже используется.")
            if not password:
                self.add_error("temporary_password", "Введите временный пароль не короче 8 символов.")
        else:
            cleaned["access_email"] = ""
            cleaned["temporary_password"] = ""
        if cleaned.get("salary_mode") in (
            Employee.SalaryMode.FIXED_PLUS_PERCENT,
            Employee.SalaryMode.FIXED_PLUS_APPOINTMENT_PERCENT,
        ):
            if cleaned.get("salary_percent") is None:
                self.add_error("salary_percent", "Укажите процентную долю.")
            if cleaned.get("fixed_monthly_salary") is None:
                self.add_error("fixed_monthly_salary", "Укажите фиксированную ставку.")
        if cleaned.get("salary_mode") == Employee.SalaryMode.FIXED_MONTHLY and cleaned.get("fixed_monthly_salary") is None:
            self.add_error("fixed_monthly_salary", "Укажите фиксированную ставку.")
        return cleaned

    def save_service_settings(self, employee):
        selected = list(self.cleaned_data.get("available_services") or [])
        EmployeeService.objects.filter(employee=employee).exclude(
            service_id__in=[service.id for service in selected]
        ).delete()
        for service in selected:
            entered = self.cleaned_data.get(f"service_percent_{service.id}") if self.advanced_payroll else None
            override = None if entered is None or entered == service.employee_percent else entered
            EmployeeService.objects.update_or_create(
                employee=employee, service=service,
                defaults={"tenant": employee.tenant, "percent_override": override},
            )

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self.tenant and Employee.objects.filter(
            tenant=self.tenant, location=self.location, name__iexact=name
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Сотрудник с таким именем уже существует.")
        return name

    class Meta:
        model = Employee
        fields = (
            "name", "company_role", "salary_mode", "salary_percent",
            "fixed_monthly_salary", "available_services", "access_email", "temporary_password", "active",
        )


class CompanyRoleForm(forms.ModelForm):
    permissions = forms.MultipleChoiceField(
        label="Права доступа",
        choices=CompanyRole.PERMISSION_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self.tenant and CompanyRole.objects.filter(
            tenant=self.tenant, name__iexact=name
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Роль с таким названием уже существует.")
        return name

    class Meta:
        model = CompanyRole
        fields = ("name", "permissions", "active")
