from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.tenants.models import Membership, Tenant
from apps.core.models import AuditEvent
from apps.catalog.models import Service

from .models import CompanyRole, Employee, EmployeeService, EmployeeShift


class EmployeeViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="LK", slug="lk")
        cls.other_tenant = Tenant.objects.create(name="Other", slug="other")
        User = get_user_model()
        cls.owner = User.objects.create_user("owner-staff@example.com", "safe-test-password")
        cls.master = User.objects.create_user("master-staff@example.com", "safe-test-password")
        Membership.objects.create(tenant=cls.tenant, user=cls.owner, role=Membership.Role.OWNER)
        Membership.objects.create(tenant=cls.tenant, user=cls.master, role=Membership.Role.MASTER)
        cls.employee = Employee.objects.create(tenant=cls.tenant, name="Алексей")
        Employee.objects.create(tenant=cls.other_tenant, name="Чужой сотрудник")

    def test_employee_list_is_tenant_scoped(self):
        self.client.force_login(self.owner)
        response = self.client.get("/employees/", HTTP_HOST="lk.localhost")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Алексей")
        self.assertNotContains(response, "Чужой сотрудник")

    def test_owner_can_create_employee(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/employees/new/",
            {
                "name": "Мария",
                "crm_access": "none",
                "position": "Мастер полировки",
                "color": Employee.Color.PURPLE,
                "active": "on",
                "sort_order": 2,
            },
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        employee = Employee.objects.get(tenant=self.tenant, name="Мария")
        self.assertIn(employee.color, Employee.Color.values)

    def test_employee_form_does_not_offer_color(self):
        self.client.force_login(self.owner)
        response = self.client.get("/employees/new/", HTTP_HOST="lk.localhost")
        self.assertNotContains(response, 'id="id_color"')
        self.assertNotContains(response, "Порядок")

    def test_employee_services_inherit_global_percent_and_allow_override(self):
        service = Service.objects.create(
            tenant=self.tenant, name="Полировка", price=10000, duration_minutes=120,
            employee_percent=30,
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/employees/{self.employee.id}/edit/",
            {
                "name": self.employee.name, "crm_access": "none", "salary_mode": Employee.SalaryMode.SERVICE_PERCENT,
                "available_services": [service.id], f"service_percent_{service.id}": "30",
                "active": "on",
            }, HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        setting = EmployeeService.objects.get(employee=self.employee, service=service)
        self.assertIsNone(setting.percent_override)
        response = self.client.post(
            f"/employees/{self.employee.id}/edit/",
            {
                "name": self.employee.name, "crm_access": "none", "salary_mode": Employee.SalaryMode.SERVICE_PERCENT,
                "available_services": [service.id], f"service_percent_{service.id}": "35",
                "active": "on",
            }, HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        setting.refresh_from_db()
        self.assertEqual(setting.percent_override, 35)

    def test_owner_can_save_and_copy_weekly_schedule(self):
        self.client.force_login(self.owner)
        prefix = f"shift_{self.employee.id}_2026-09-07"
        response = self.client.post(
            "/schedule/?week=2026-09-07",
            {prefix: "on", prefix + "_start": "10:00", prefix + "_end": "19:00", "action": "save"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        shift = EmployeeShift.objects.get(employee=self.employee, date="2026-09-07")
        self.assertEqual(shift.start_time.strftime("%H:%M"), "10:00")
        response = self.client.post(
            "/schedule/?week=2026-09-14", {"action": "copy_previous"}, HTTP_HOST="lk.localhost"
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(EmployeeShift.objects.filter(employee=self.employee, date="2026-09-14").exists())

    def test_master_cannot_edit_employee(self):
        self.client.force_login(self.master)
        response = self.client.get(
            f"/employees/{self.employee.id}/edit/", HTTP_HOST="lk.localhost"
        )
        self.assertEqual(response.status_code, 403)

    def test_theme_class_comes_from_tenant_setting(self):
        self.client.force_login(self.owner)
        response = self.client.get("/employees/", HTTP_HOST="lk.localhost")
        self.assertContains(response, "theme-light_blue")

    def test_custom_role_permissions_control_sections(self):
        role = CompanyRole.objects.create(
            tenant=self.tenant, name="Инвентаризатор", permissions=["inventory_view"]
        )
        Employee.objects.create(
            tenant=self.tenant, name="Инвентаризатор", user=self.master, company_role=role
        )
        self.client.force_login(self.master)
        allowed = self.client.get("/inventory/", HTTP_HOST="lk.localhost")
        forbidden = self.client.get("/analytics/", HTTP_HOST="lk.localhost")
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(forbidden.status_code, 403)

    def test_owner_can_create_employee_login_and_audit_event(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/employees/new/",
            {"name": "Ирина", "crm_access": "enabled", "access_email": "irina@example.com",
             "temporary_password": "temporary-123", "active": "on", "sort_order": 3},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        employee = Employee.objects.get(name="Ирина")
        self.assertEqual(employee.user.email, "irina@example.com")
        self.assertTrue(employee.user.memberships.filter(tenant=self.tenant).exists())
        self.assertTrue(AuditEvent.objects.filter(entity_id=str(employee.id)).exists())

    def test_new_employee_defaults_to_crm_access(self):
        self.client.force_login(self.owner)
        response = self.client.get("/employees/new/", HTTP_HOST="lk.localhost")
        self.assertContains(response, 'option value="enabled" selected')
        self.assertContains(response, 'id="id_access_email"')
        self.assertContains(response, 'id="id_temporary_password"')

    def test_existing_email_cannot_be_assigned_to_new_employee(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/employees/new/",
            {"name": "Дубликат", "crm_access": "enabled", "access_email": self.master.email,
             "temporary_password": "temporary-123", "active": "on"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Этот email уже используется.")
        self.assertFalse(Employee.objects.filter(tenant=self.tenant, name="Дубликат").exists())

    def test_existing_employee_login_is_read_only_and_password_is_unchanged(self):
        self.employee.user = self.master
        self.employee.save(update_fields=("user", "updated_at"))
        old_password = self.master.password
        self.client.force_login(self.owner)
        get_response = self.client.get(
            f"/employees/{self.employee.id}/edit/", HTTP_HOST="lk.localhost"
        )
        self.assertContains(get_response, self.master.email)
        self.assertNotContains(get_response, 'id="id_access_email"')
        self.assertNotContains(get_response, 'id="id_temporary_password"')
        response = self.client.post(
            f"/employees/{self.employee.id}/edit/",
            {"name": self.employee.name, "crm_access": "enabled",
             "access_email": "changed@example.com", "temporary_password": "changed-password",
             "salary_mode": Employee.SalaryMode.SERVICE_PERCENT, "active": "on"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.master.refresh_from_db()
        self.assertEqual(self.employee.user, self.master)
        self.assertEqual(self.master.email, "master-staff@example.com")
        self.assertEqual(self.master.password, old_password)

    def test_removing_crm_access_disables_membership(self):
        self.employee.user = self.master
        self.employee.save(update_fields=("user", "updated_at"))
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/employees/{self.employee.id}/edit/",
            {"name": self.employee.name, "crm_access": "none",
             "salary_mode": Employee.SalaryMode.SERVICE_PERCENT, "active": "on"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertIsNone(self.employee.user)
        membership = Membership.objects.get(tenant=self.tenant, user=self.master)
        self.assertEqual(membership.status, Membership.Status.DISABLED)


class EmployeeVisibilityTests(EmployeeViewsTests):
    def test_owner_is_hidden_and_other_staff_remain(self):
        owner_profile = Employee.objects.create(tenant=self.tenant, user=self.owner, name='Hidden owner')
        self.client.force_login(self.owner)
        response = self.client.get('/employees/', HTTP_HOST='lk.localhost')
        self.assertNotIn(owner_profile, response.context['employees'])
        self.assertIn(self.employee, response.context['employees'])
        self.assertTrue(Employee.objects.filter(pk=owner_profile.pk).exists())

    def test_limit_warning_only_appears_when_full(self):
        from apps.tenants.plans import active_employee_limit
        self.client.force_login(self.owner)
        response = self.client.get('/employees/new/', HTTP_HOST='lk.localhost')
        self.assertNotContains(response, 'Лимит сотрудников исчерпан')
        limit = active_employee_limit(self.tenant)
        for index in range(limit - 1):
            Employee.objects.create(tenant=self.tenant, name=f'Employee {index}')
        response = self.client.get('/employees/new/', HTTP_HOST='lk.localhost')
        self.assertContains(response, 'Лимит сотрудников исчерпан')
