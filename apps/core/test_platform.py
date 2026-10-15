from datetime import timedelta
from django.test import TestCase,override_settings
from django.utils import timezone
from django.core import mail
from apps.tenants.models import PlatformAccess,PromoCode,NetworkRequest
from apps.tenants.services import create_company_owner

@override_settings(DEBUG=True,ALLOWED_HOSTS=['testserver','localhost','.localhost'],TENANT_DOMAIN_SUFFIXES=('.localhost','.crmdetailing.ru'))
class PlatformTests(TestCase):
    def setUp(self):
        self.tenant,self.owner=create_company_owner(company_name='Платформа',slug='platform-test',owner_name='Создатель',email='platform@example.com',password='platform-password-123')
        self.host={'HTTP_HOST':'platform.localhost'}
    def test_only_allowlisted_platform_user_can_access(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/',**self.host).status_code,302)
        PlatformAccess.objects.create(user=self.owner)
        self.assertEqual(self.client.get('/',**self.host).status_code,200)
        self.assertEqual(self.client.get('/platform/',HTTP_HOST='platform-test.localhost').status_code,404)
    def test_platform_login_and_promo_creation(self):
        PlatformAccess.objects.create(user=self.owner)
        response=self.client.post('/login/',{'email':self.owner.email,'password':'platform-password-123'},**self.host)
        self.assertEqual(response.status_code,302)
        response=self.client.post('/promos/',{'code':'launch','kind':'percent','value':'20','active':'on','plans':['start'],'periods':['6']},**self.host)
        self.assertEqual(response.status_code,302)
        promo=PromoCode.objects.get(code='LAUNCH')
        self.assertEqual(promo.allowed_periods,[6])
        self.assertEqual(promo.allowed_plans,['start'])
    @override_settings(EMAIL_HOST='smtp.example.com',EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_email_2fa_required_before_platform_access(self):
        PlatformAccess.objects.create(user=self.owner,email_2fa=True)
        response=self.client.post('/login/',{'email':self.owner.email,'password':'platform-password-123'},**self.host)
        self.assertEqual(response.url,'/verify/')
        self.assertEqual(len(mail.outbox),1)
        import re
        code=re.search(r'Ваш код: (\d{6})',mail.outbox[0].body).group(1)
        self.assertEqual(self.client.get('/',**self.host).status_code,302)
        wrong=self.client.post('/verify/',{'code':'bad'},**self.host)
        self.assertContains(wrong,'Неверный код')
        success=self.client.post('/verify/',{'code':code},**self.host)
        self.assertEqual(success.url,'/')
        self.assertEqual(self.client.get('/',**self.host).status_code,200)
        self.assertIsNotNone(__import__('apps.accounts.models',fromlist=['SecurityChallenge']).SecurityChallenge.objects.get().consumed_at)
    def test_network_request_is_saved_to_platform(self):
        response=self.client.post('/network-request/',{'company_name':'Сеть','contact_name':'Иван','email':'network@example.com','phone':'79991234567','locations':'2','comment':'Три точки'},HTTP_HOST='localhost')
        self.assertContains(response,'Заявка принята')
        self.assertEqual(NetworkRequest.objects.get().status,'new')
    def test_expired_owner_can_export_own_data(self):
        sub=self.tenant.subscription;sub.trial_ends_at=timezone.now()-timedelta(seconds=1);sub.save()
        self.client.force_login(self.owner)
        response=self.client.get('/company/export/',HTTP_HOST='platform-test.localhost')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Content-Type'],'application/zip')

    def test_network_request_form_visible_inside_company_crm(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/network-request/',HTTP_HOST='platform-test.localhost'),'Отправить заявку')

    def test_platform_shows_legacy_trial_expiry_when_paid_period_empty(self):
        from django.template.defaultfilters import date as format_date
        PlatformAccess.objects.create(user=self.owner)
        sub=self.tenant.subscription
        sub.ends_at=None;sub.save()
        self.client.force_login(self.owner)
        response=self.client.get('/',**self.host)
        expected=format_date(timezone.localtime(sub.trial_ends_at),'d.m.Y H:i')
        self.assertContains(response,expected)

    @override_settings(EMAIL_HOST='smtp.example.com')
    def test_broken_smtp_cannot_enable_2fa_and_lock_out_owner(self):
        from unittest.mock import patch
        from smtplib import SMTPAuthenticationError
        access=PlatformAccess.objects.create(user=self.owner)
        self.client.force_login(self.owner)
        with patch('apps.accounts.security.send_mail',side_effect=SMTPAuthenticationError(535,b'invalid credentials')):
            response=self.client.post('/security/',{'action':'enable','password':'platform-password-123'},**self.host)
        self.assertContains(response,'Не удалось отправить код')
        access.refresh_from_db()
        self.assertFalse(access.email_2fa)
        self.assertEqual(self.client.get('/',**self.host).status_code,200)
