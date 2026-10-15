from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings

from apps.catalog.models import Service
from apps.inventory.models import InventoryCategory
from apps.staff.models import CompanyRole, Employee
from apps.tenants.models import Membership, Tenant, TenantWorkingHours
from apps.accounts.models import LegalAcceptance


@override_settings(DEBUG=True, PRIMARY_DOMAIN="localhost", TLS_ASK_TOKEN="test-tls-token")
class CompanyRegistrationTests(TestCase):
    def registration_data(self, **overrides):
        data = {
            "plan":"business",
            "company_name": "Новая студия",
            "slug": "new-studio",
            "owner_name": "Алексей",
            "email": "owner@new-studio.test",
            "password1": "Strong-test-password-814!",
            "password2": "Strong-test-password-814!",
            "personal_data_consent": "on",
            "offer_acceptance": "on",
        }
        data.update(overrides)
        return data

    def test_public_registration_provisions_company_and_owner(self):
        response = self.client.post("/register/", self.registration_data(), HTTP_HOST="localhost")

        self.assertRedirects(
            response,
            "http://new-studio.localhost/login/?registered=1",
            fetch_redirect_response=False,
        )
        tenant = Tenant.objects.get(slug="new-studio")
        user = get_user_model().objects.get(email="owner@new-studio.test")
        self.assertTrue(user.check_password("Strong-test-password-814!"))
        self.assertTrue(Membership.objects.filter(
            tenant=tenant, user=user, role=Membership.Role.OWNER,
        ).exists())
        self.assertTrue(Employee.objects.filter(tenant=tenant, user=user, name="Алексей").exists())
        self.assertEqual(TenantWorkingHours.objects.filter(tenant=tenant).count(), 7)
        self.assertEqual(CompanyRole.objects.filter(tenant=tenant).count(), 4)
        self.assertEqual(InventoryCategory.objects.filter(tenant=tenant).count(), 4)
        self.assertTrue(Service.objects.filter(tenant=tenant, system_key="other").exists())
        self.assertEqual(
            set(LegalAcceptance.objects.filter(user=user).values_list("document_type", flat=True)),
            {LegalAcceptance.DocumentType.PERSONAL_DATA, LegalAcceptance.DocumentType.OFFER},
        )

    def test_registration_requires_separate_personal_data_and_offer_acceptance(self):
        without_personal_data = self.registration_data()
        without_personal_data.pop("personal_data_consent")
        response = self.client.post("/register/", without_personal_data, HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "personal_data_consent", "Обязательное поле.")

        without_offer = self.registration_data(email="other@example.test", slug="other-studio")
        without_offer.pop("offer_acceptance")
        response = self.client.post("/register/", without_offer, HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "offer_acceptance", "Обязательное поле.")
        self.assertFalse(Tenant.objects.exists())

    def test_registration_records_optional_marketing_evidence(self):
        response = self.client.post(
            "/register/",
            self.registration_data(marketing_consent="on"),
            HTTP_HOST="localhost",
            HTTP_X_FORWARDED_FOR="203.0.113.24, 10.0.0.2",
            HTTP_USER_AGENT="Consent test browser",
        )
        self.assertEqual(response.status_code, 302)
        marketing = LegalAcceptance.objects.get(document_type=LegalAcceptance.DocumentType.MARKETING)
        self.assertEqual(marketing.document_version, "30.09.2026")
        self.assertEqual(str(marketing.ip_address), "203.0.113.24")
        self.assertEqual(marketing.user_agent, "Consent test browser")

    def test_legal_documents_have_separate_public_urls(self):
        for path, expected in (
            ("/legal/privacy/", "Политика в отношении обработки персональных данных"),
            ("/legal/personal-data-consent/", "Согласие на обработку персональных данных"),
            ("/legal/offer/", "Публичная оферта"),
            ("/legal/marketing-consent/", "Согласие на получение рекламных"),
        ):
            response = self.client.get(path, HTTP_HOST="localhost")
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, expected)

    def test_consent_documents_do_not_repeat_interface_confirmation(self):
        for path in (
            "/legal/personal-data-consent/",
            "/legal/marketing-consent/",
        ):
            response = self.client.get(path, HTTP_HOST="localhost")
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "Подтверждение согласия")

    def test_registration_consent_labels_keep_checkbox_before_text(self):
        response = self.client.get("/register/", HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="registration-consent"', count=3)
        self.assertContains(response, 'id="id_personal_data_consent"')
        self.assertContains(response, 'id="id_offer_acceptance"')
        self.assertContains(response, 'id="id_marketing_consent"')

    def test_public_landing_links_registration_and_tenant_login(self):
        response = self.client.get("/", HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/register/"')
        self.assertContains(response, 'id="tenant-login"')
        self.assertContains(response, ".localhost")
        self.assertContains(response, "img/crm-logo-480-20261007.webp")
        self.assertContains(response, "1299 ₽")
        self.assertContains(response, "1999 ₽")
        self.assertContains(response, "2999 ₽")

    def test_registered_owner_can_log_in_on_company_subdomain(self):
        self.client.post("/register/", self.registration_data(), HTTP_HOST="localhost")

        response = self.client.post(
            "/login/",
            {"username": "owner@new-studio.test", "password": "Strong-test-password-814!"},
            HTTP_HOST="new-studio.localhost",
        )

        self.assertRedirects(response, "/app/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["_auth_user_id"], str(get_user_model().objects.get(
            email="owner@new-studio.test"
        ).pk))

    @override_settings(
        EMAIL_HOST="smtp.example.test",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_password_reset_email_uses_company_subdomain(self):
        self.client.post("/register/", self.registration_data(), HTTP_HOST="localhost")

        login_page = self.client.get("/login/", HTTP_HOST="new-studio.localhost")
        self.assertContains(login_page, 'href="/password-reset/"')
        response = self.client.post(
            "/password-reset/",
            {"email": "owner@new-studio.test"},
            HTTP_HOST="new-studio.localhost",
        )

        self.assertRedirects(response, "/password-reset/done/", fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("new-studio.localhost/password-reset/", mail.outbox[0].body)

    def test_registration_rejects_reserved_or_existing_subdomain(self):
        response = self.client.post("/register/", self.registration_data(slug="www"), HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "зарезервирован")
        Tenant.objects.create(name="Existing", slug="taken")
        response = self.client.post(
            "/register/", self.registration_data(slug="taken"), HTTP_HOST="localhost"
        )
        self.assertContains(response, "уже занят")

    def test_registration_is_available_only_on_public_site(self):
        Tenant.objects.create(name="LK", slug="lk")
        response = self.client.get("/register/", HTTP_HOST="lk.localhost")
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)

    def test_tls_gate_allows_only_active_registered_tenant(self):
        Tenant.objects.create(name="LK", slug="lk")
        allowed = self.client.get(
            "/internal/tls/allow/?token=test-tls-token&domain=lk.localhost",
            HTTP_HOST="localhost",
        )
        unknown = self.client.get(
            "/internal/tls/allow/?token=test-tls-token&domain=missing.localhost",
            HTTP_HOST="localhost",
        )
        wrong_token = self.client.get(
            "/internal/tls/allow/?token=wrong&domain=lk.localhost",
            HTTP_HOST="localhost",
        )
        unrelated = self.client.get(
            "/internal/tls/allow/?token=test-tls-token&domain=example.org",
            HTTP_HOST="localhost",
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(wrong_token.status_code, 403)
        self.assertEqual(unrelated.status_code, 404)
