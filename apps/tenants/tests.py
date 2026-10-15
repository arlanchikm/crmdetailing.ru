from datetime import time, timedelta
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Membership, Tenant, TenantWorkingHours
from apps.staff.models import Employee, EmployeeShift


@override_settings(DEBUG=True)
class TenantRoutingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.lk = Tenant.objects.create(name="LK", slug="lk")
        cls.other = Tenant.objects.create(name="Other", slug="other")
        cls.owner = get_user_model().objects.create_user(
            email="owner@example.com", password="safe-test-password"
        )
        cls.master = get_user_model().objects.create_user(
            email="master@example.com", password="safe-test-password"
        )
        Membership.objects.create(
            tenant=cls.lk,
            user=cls.owner,
            role=Membership.Role.OWNER,
        )
        Membership.objects.create(
            tenant=cls.lk, user=cls.master, role=Membership.Role.MASTER
        )

    def test_public_host_opens_landing(self):
        response = self.client.get("/", HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "crmdetailing.ru")

    def test_unknown_subdomain_returns_404(self):
        response = self.client.get("/", HTTP_HOST="missing.localhost")
        self.assertEqual(response.status_code, 404)

    def test_local_entry_selects_tenant_without_subdomain_dns(self):
        response = self.client.get("/local/lk/", HTTP_HOST="localhost")
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)
        response = self.client.get("/login/", HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "LK")

    def test_member_can_log_in_on_own_tenant(self):
        response = self.client.post(
            "/login/",
            {"username": "owner@example.com", "password": "safe-test-password"},
            HTTP_HOST="lk.localhost",
        )
        self.assertRedirects(
            response,
            "/app/",
            fetch_redirect_response=False,
        )

    def test_member_can_log_in_through_local_fallback(self):
        self.client.get("/local/lk/", HTTP_HOST="localhost")
        response = self.client.post(
            "/login/",
            {"username": "owner@example.com", "password": "safe-test-password"},
            HTTP_HOST="localhost",
        )
        self.assertRedirects(response, "/app/", fetch_redirect_response=False)

    def test_local_entry_redirects_authenticated_member_to_dashboard(self):
        self.client.force_login(self.owner)
        response = self.client.get("/local/lk/", HTTP_HOST="localhost", follow=True)
        self.assertRedirects(response, "/analytics/")
        self.assertContains(response, "Аналитика")

    def test_member_cannot_log_in_on_other_tenant(self):
        response = self.client.post(
            "/login/",
            {"username": "owner@example.com", "password": "safe-test-password"},
            HTTP_HOST="other.localhost",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Проверьте email и пароль")

    def test_authenticated_user_cannot_open_other_tenant(self):
        self.client.force_login(self.owner)
        response = self.client.get("/app/", HTTP_HOST="other.localhost")
        self.assertEqual(response.status_code, 403)

    def test_non_owner_starts_on_appointments(self):
        self.client.force_login(self.master)
        response = self.client.get("/app/", HTTP_HOST="lk.localhost")
        self.assertRedirects(response, "/appointments/", fetch_redirect_response=False)

    def test_owner_browser_timezone_is_saved(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/app/detect-timezone/", {"timezone": "Asia/Almaty"}, HTTP_HOST="lk.localhost"
        )
        self.assertEqual(response.status_code, 200)
        self.lk.refresh_from_db()
        self.assertEqual(self.lk.timezone, "Asia/Almaty")
        self.assertTrue(self.lk.timezone_auto_detected)

    def test_owner_updates_weekly_working_hours(self):
        employee = Employee.objects.create(tenant=self.lk, name="Мастер")
        next_monday = timezone.localdate() + timedelta(days=(7 - timezone.localdate().weekday()) % 7)
        shift = EmployeeShift.objects.create(
            tenant=self.lk, employee=employee, date=next_monday,
            start_time=time(8), end_time=time(22),
        )
        self.client.force_login(self.owner)
        data = {}
        for weekday in range(7):
            data[f"opens_{weekday}"] = "09:00"
            data[f"closes_{weekday}"] = "20:30"
        data["closed_6"] = "1"
        response = self.client.post(
            "/company/settings/", {"section": "working-hours", **data}, HTTP_HOST="lk.localhost"
        )
        self.assertEqual(response.status_code, 302)
        sunday = TenantWorkingHours.objects.get(tenant=self.lk, weekday=6)
        monday = TenantWorkingHours.objects.get(tenant=self.lk, weekday=0)
        self.assertTrue(sunday.is_closed)
        self.assertEqual(monday.opens_at.strftime("%H:%M"), "09:00")
        self.assertEqual(monday.closes_at.strftime("%H:%M"), "20:30")
        shift.refresh_from_db()
        self.assertEqual(shift.start_time.strftime("%H:%M"), "09:00")
        self.assertEqual(shift.end_time.strftime("%H:%M"), "20:30")

    def test_company_settings_are_owner_only(self):
        self.client.force_login(self.master)
        response = self.client.get("/company/settings/", HTTP_HOST="lk.localhost")
        self.assertEqual(response.status_code, 403)

    def test_owner_updates_company_name_and_logo(self):
        self.client.force_login(self.owner)
        from apps.core.test_performance import image_upload
        logo = image_upload(size=(1200, 400))
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                "/company/settings/",
                {
                    "section": "company", "name": "Новая мойка", "logo": logo,
                    "language_code": Tenant.Language.RU, "theme_key": Tenant.Theme.LIGHT_BLUE,
                },
                HTTP_HOST="lk.localhost",
            )
            self.assertEqual(response.status_code, 302)
            self.lk.refresh_from_db()
            self.assertEqual(self.lk.name, "Новая мойка")
            self.assertTrue(self.lk.logo.name.endswith(".webp"))
            self.assertEqual(self.lk.language_code, Tenant.Language.RU)
            self.assertEqual(self.lk.theme_key, Tenant.Theme.LIGHT_BLUE)

    def test_interface_settings_are_separate_and_owner_can_customize_colors(self):
        self.client.force_login(self.master)
        member_page = self.client.get("/interface/settings/", HTTP_HOST="lk.localhost")
        self.assertEqual(member_page.status_code, 200)
        self.assertContains(member_page, "Оформление на этом устройстве")
        self.assertNotContains(member_page, "Оформление компании")

        self.client.force_login(self.owner)
        response = self.client.post(
            "/interface/settings/",
            {
                "language_code": Tenant.Language.RU,
                "primary_color": "#9B51E0",
                "background_color": "#E9E0F5",
                "surface_color": "#FFF8FE",
            },
            HTTP_HOST="lk.localhost",
        )
        self.assertRedirects(
            response,
            "/interface/settings/",
            fetch_redirect_response=False,
        )
        self.lk.refresh_from_db()
        self.assertEqual(self.lk.primary_color, "#9B51E0")
        self.assertEqual(self.lk.background_color, "#E9E0F5")
        self.assertEqual(self.lk.surface_color, "#FFFFFF")
        self.assertNotContains(self.client.get("/interface/settings/", HTTP_HOST="lk.localhost"), "Оттенок прозрачных блоков")

        page = self.client.get("/appointments/", HTTP_HOST="lk.localhost")
        self.assertContains(page, "--crm-custom-primary:#9B51E0")

    def test_owner_can_select_dark_theme_and_english(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/interface/settings/",
            {
                "language_code": Tenant.Language.EN,
                "primary_color": "#5A9CFF",
                "background_color": "#DCEBFA",
                "surface_color": "#FFFFFF",
            },
            HTTP_HOST="lk.localhost",
        )
        self.assertRedirects(response, "/interface/settings/", fetch_redirect_response=False)
        self.lk.refresh_from_db()
        self.assertEqual(self.lk.language_code, Tenant.Language.EN)
        self.assertEqual(self.lk.theme_key, Tenant.Theme.LIGHT_BLUE)
        page = self.client.get("/interface/settings/", HTTP_HOST="lk.localhost")
        self.assertContains(page, "crm-layout theme-light_blue")
        self.assertContains(page, "data-interface-theme=\"dark\"")
        self.assertContains(page, 'data-ui-language="en"')
        self.assertContains(page, "static/js/i18n.js")

    def test_member_updates_profile_and_password(self):
        employee = Employee.objects.create(tenant=self.lk, user=self.master, name="Старое имя")
        self.client.force_login(self.master)
        response = self.client.post(
            "/profile/settings/", {"section": "profile", "first_name": "Новое имя"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        employee.refresh_from_db()
        self.master.refresh_from_db()
        self.assertEqual(employee.name, "Новое имя")
        self.assertEqual(self.master.first_name, "Новое имя")

        response = self.client.post(
            "/profile/settings/",
            {
                "section": "password",
                "old_password": "safe-test-password",
                "new_password1": "new-safe-test-password-47",
                "new_password2": "new-safe-test-password-47",
            },
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.master.refresh_from_db()
        self.assertTrue(self.master.check_password("new-safe-test-password-47"))
        self.assertEqual(self.client.get("/profile/settings/", HTTP_HOST="lk.localhost").status_code, 200)
