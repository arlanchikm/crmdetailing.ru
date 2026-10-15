from datetime import timedelta
from decimal import Decimal
from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import mail
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.tenants.models import PlatformAccess, SubscriptionOrder, Tenant
from apps.tenants.services import create_company_owner


@override_settings(DEBUG=True, ALLOWED_HOSTS=['testserver', 'localhost', '.localhost'],
    TENANT_DOMAIN_SUFFIXES=('.localhost', '.crmdetailing.ru'),
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PlatformManagementTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = create_company_owner(company_name='Test', slug='management',
            owner_name='Owner', email='tenant@example.com', password='existing-pass-123')
        self.admin = get_user_model().objects.create_user(email='admin@example.com', password='platform-pass-123')
        PlatformAccess.objects.create(user=self.admin)
        self.host = {'HTTP_HOST': 'platform.localhost'}
        self.crm = {'HTTP_HOST': 'management.localhost'}

    def test_platform_reset_only_emails_platform_account(self):
        for email in [self.owner.email, self.admin.email]:
            self.assertEqual(self.client.post('/password-reset/', {'email':email}, **self.host).status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.admin.email])
        self.assertIn('platform.localhost/password-reset/', mail.outbox[0].body)

    def test_platform_reset_rejects_tenant_user_token(self):
        uid = urlsafe_base64_encode(force_bytes(self.owner.pk))
        token = default_token_generator.make_token(self.owner)
        response = self.client.get(f'/password-reset/{uid}/{token}/', **self.host)
        self.assertContains(response, 'Ссылка недействительна')

    def test_platform_password_change_keeps_crm_password_and_session(self):
        self.client.force_login(self.admin)
        response = self.client.post('/password/change/', {'old_password':'platform-pass-123',
            'new_password1':'new-platform-pass-456', 'new_password2':'new-platform-pass-456'}, **self.host)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/', **self.host).status_code, 200)
        self.admin.refresh_from_db(); self.owner.refresh_from_db()
        self.assertTrue(self.admin.check_password('new-platform-pass-456'))
        self.assertTrue(self.owner.check_password('existing-pass-123'))
        self.client.logout()
        self.assertEqual(self.client.post('/login/', {'username':self.admin.email, 'password':'new-platform-pass-456'}, **self.crm).status_code, 200)

    def test_owner_migration_separates_account_and_revokes_old_platform_login(self):
        PlatformAccess.objects.all().delete()
        self.owner.email = 'platform-owner@example.invalid'; self.owner.save()
        existing = get_user_model().objects.create_user(email='owner@example.invalid', password='other-crm-pass')
        PlatformAccess.objects.create(user=self.owner)
        import_module('apps.tenants.migrations.0015_separate_platform_identity').separate_owner(apps, None)
        new_access = PlatformAccess.objects.get(email='platform-owner@example.invalid')
        new_user = new_access.user
        self.assertNotEqual(new_user.pk, existing.pk)
        self.assertTrue(existing.check_password('other-crm-pass'))
        self.assertFalse(new_user.memberships.exists())
        self.assertTrue(new_user.check_password('existing-pass-123'))
        self.assertFalse(PlatformAccess.objects.filter(user=self.owner).exists())
        self.assertEqual(self.client.post('/login/', {'email':self.owner.email, 'password':'existing-pass-123'}, **self.host).status_code, 200)
        self.assertEqual(self.client.post('/login/', {'email':new_access.email, 'password':'existing-pass-123'}, **self.host).status_code, 302)
        new_user.set_password('independent-pass-456'); new_user.save()
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password('existing-pass-123'))

    def company_data(self, **updates):
        data = {'slug':self.tenant.slug, 'plan':'start', 'status':'active',
            'ends_at':(timezone.now()+timedelta(days=30)).isoformat(), 'reason':'Адрес изменён', 'tenant_status':'active'}
        return {**data, **updates}

    def test_subdomain_change_and_validation(self):
        self.client.force_login(self.admin)
        url = f'/company/{self.tenant.pk}/'
        Tenant.objects.create(name='Other', slug='occupied')
        for slug in ['platform', 'occupied', 'bad.name', '-bad']:
            response = self.client.post(url, self.company_data(slug=slug), **self.host)
            self.assertEqual(response.status_code, 200)
            self.tenant.refresh_from_db(); self.assertEqual(self.tenant.slug, 'management')
        self.assertEqual(self.client.post(url, self.company_data(slug='renamed'), **self.host).status_code, 302)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.slug, 'renamed')
        self.client.force_login(self.owner)
        response = self.client.get('/login/', **self.crm)
        self.assertEqual(response.status_code, 404, getattr(response, 'url', ''))
        self.assertEqual(self.client.get('/appointments/', HTTP_HOST='renamed.localhost').status_code, 200)

    def test_payments_use_paid_date_and_exclude_unpaid_orders(self):
        plan = self.tenant.subscription.plan
        for status, amount, paid_date, created in [
            ('paid', '200', timezone.now(), timezone.now()-timedelta(days=60)),
            ('paid', '300', timezone.now()-timedelta(days=60), timezone.now()),
            ('pending', '900', None, timezone.now()), ('cancelled', '800', None, timezone.now())]:
            order = SubscriptionOrder.objects.create(tenant=self.tenant, plan=plan, status=status,
                subtotal=amount, total=amount, paid_at=paid_date)
            SubscriptionOrder.objects.filter(pk=order.pk).update(created_at=created)
        self.client.force_login(self.admin)
        response = self.client.get('/payments/', {'start':timezone.localdate().isoformat()}, **self.host)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['summary']['revenue'], Decimal('200'))
        self.assertEqual(response.context['summary']['count'], 1)
        self.assertEqual(response.context['pending'], 1)

    def expire(self):
        sub = self.tenant.subscription
        sub.trial_ends_at = timezone.now()-timedelta(days=1); sub.save()
        self.client.force_login(self.owner)

    def test_expired_workspace_readable_and_write_blocked(self):
        self.expire()
        self.assertContains(self.client.get('/appointments/', **self.crm), 'Подписка закончилась')
        self.assertContains(self.client.get('/clients/new/', **self.crm), 'subscription-warning', status_code=403)
        response = self.client.post('/clients/new/', {'name':'Blocked'}, **self.crm)
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'subscription-warning', status_code=403)
        self.assertFalse(self.tenant.clients.filter(name='Blocked').exists())
        response = self.client.post('/clients/new/', {}, HTTP_ACCEPT='application/json', **self.crm)
        self.assertEqual(response.json()['error'], 'subscription_expired')
        self.assertEqual(self.client.get('/subscription/settings/', **self.crm).status_code, 200)
        self.assertEqual(self.client.post('/company/settings/', {}, **self.crm).status_code, 403)
        self.assertEqual(self.client.post('/logout/', **self.crm).status_code, 302)

    def test_suspended_tenant_can_still_login_and_view(self):
        self.expire()
        self.tenant.status = 'suspended'; self.tenant.save()
        self.assertEqual(self.client.get('/appointments/', **self.crm).status_code, 200)
        self.assertEqual(self.client.post('/clients/new/', {}, **self.crm).status_code, 403)

    def test_renewal_post_is_not_intercepted(self):
        self.expire()
        self.assertEqual(self.client.post('/subscription/settings/', {'plan':'start','months':'6','action':'quote'}, **self.crm).status_code, 200)

    def test_platform_alias_recovery_and_2fa_use_real_email(self):
        from apps.accounts.security import issue_challenge
        access = PlatformAccess.objects.get(user=self.admin)
        access.email = 'shared@example.com'; access.save()
        self.admin.email = 'internal-platform@accounts.invalid'; self.admin.save()
        tenant_user = get_user_model().objects.create_user(email='shared@example.com', password='tenant-separate-pass')
        response = self.client.post('/login/', {'email':'shared@example.com','password':'platform-pass-123'}, **self.host)
        self.assertEqual(response.status_code, 302)
        self.client.logout()
        self.client.post('/password-reset/', {'email':'shared@example.com'}, **self.host)
        self.assertEqual(mail.outbox[-1].to, ['shared@example.com'])
        issue_challenge(self.admin, 'platform')
        self.assertEqual(mail.outbox[-1].to, ['shared@example.com'])
        uid = urlsafe_base64_encode(force_bytes(self.admin.pk))
        token = default_token_generator.make_token(self.admin)
        self.assertContains(self.client.get(f'/password-reset/{uid}/{token}/', **self.crm), 'недействительна')

    def test_platform_reset_completes_and_does_not_change_crm_user(self):
        uid = urlsafe_base64_encode(force_bytes(self.admin.pk))
        token = default_token_generator.make_token(self.admin)
        response = self.client.get(f'/password-reset/{uid}/{token}/', **self.host)
        self.assertEqual(response.status_code, 302)
        response = self.client.post(response.url, {'new_password1':'reset-platform-password-456',
            'new_password2':'reset-platform-password-456'}, **self.host)
        self.assertEqual(response.status_code, 302)
        self.admin.refresh_from_db(); self.owner.refresh_from_db()
        self.assertTrue(self.admin.check_password('reset-platform-password-456'))
        self.assertTrue(self.owner.check_password('existing-pass-123'))
