import io
import re
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.core import mail
from django.db import transaction
from django.utils import timezone
from apps.tenants.services import create_company_owner
from apps.tenants.models import Membership, PlatformAccess
from apps.accounts.models import User, SecurityChallenge, RecoveryCode
from apps.accounts.security import issue_challenge, check_challenge, resend_challenge, new_recovery_codes
from apps.customers.models import Client,Car
from apps.staff.models import Employee
from apps.inventory.models import Consumable, InventoryCategory,InventoryMovement
from apps.core.models import ImportBatch,Notification,PushSubscription,notify
from apps.core.imports import preview,apply_import,undo_import,read_table,FIELDS
from apps.core.phones import format_phone

TEST_SETTINGS=dict(DEBUG=True,ALLOWED_HOSTS=['testserver','localhost','.localhost'],
 TENANT_DOMAIN_SUFFIXES=('.localhost','.crmdetailing.ru'),EMAIL_HOST='smtp.example.com',
 EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])

@override_settings(**TEST_SETTINGS)
class ImportTests(TestCase):
    def setUp(self):
        self.tenant,self.owner=create_company_owner(company_name='Импорт',slug='imports-test',owner_name='Владелец',email='import@example.com',password='secret-test')
        self.location=self.tenant.locations.get(is_primary=True)
        self.host={'HTTP_HOST':'imports-test.localhost'}
        self.client.force_login(self.owner)
    def batch(self,kind,rows,duplicate_mode='skip'):
        keys=[k for k,_,_ in FIELDS[kind]]
        source=[{'number':i+2,'values':[str(row.get(k,'')) for k in keys]} for i,row in enumerate(rows)]
        batch=ImportBatch.objects.create(tenant=self.tenant,location=self.location,actor=self.owner,kind=kind,filename='test.csv',columns=keys,rows=source,mapping={k:str(i) for i,k in enumerate(keys)},duplicate_mode=duplicate_mode,status='preview')
        batch.results=preview(batch);batch.save();return batch
    def test_client_preview_does_not_write_and_apply_is_idempotent(self):
        b=self.batch('clients',[{'name':'Иван','phone':'88005553535'}])
        self.assertEqual(b.results[0]['action'],'create');self.assertFalse(Client.objects.exists())
        apply_import(b,self.owner);apply_import(b,self.owner)
        self.assertEqual(Client.objects.count(),1);self.assertEqual(Client.objects.get().phone,'+7 (800) 555-35-35')
        undo_import(b,self.owner);self.assertFalse(Client.objects.exists())
    def test_update_preserves_empty_fields_and_undo_restores(self):
        c=Client.objects.create(tenant=self.tenant,name='Старое',phone='79991234567',notes='Сохранить')
        b=self.batch('clients',[{'name':'Новое','phone':'9991234567'}],'update')
        self.assertEqual(b.results[0]['action'],'update');apply_import(b,self.owner)
        c.refresh_from_db();self.assertEqual(c.name,'Новое');self.assertEqual(c.notes,'Сохранить')
        undo_import(b,self.owner);c.refresh_from_db();self.assertEqual(c.name,'Старое')
    def test_skip_existing(self):
        Client.objects.create(tenant=self.tenant,name='Иван',phone='79991234567')
        b=self.batch('clients',[{'name':'Другой','phone':'89991234567'}]);apply_import(b,self.owner)
        self.assertEqual(b.results[0]['action'],'skip');self.assertEqual(Client.objects.get().name,'Иван')
    def test_invalid_phone_requires_explicit_skip(self):
        b=self.batch('clients',[{'name':'Плохой','phone':'123'},{'name':'Хороший','phone':'79991234567'}])
        self.assertEqual([r['action'] for r in b.results],['error','create'])
        with self.assertRaises(ValueError):apply_import(b,self.owner)
        self.assertFalse(Client.objects.exists());apply_import(b,self.owner,skip_errors=True)
        self.assertEqual(Client.objects.count(),1)
    def test_duplicate_in_file_rejected(self):
        b=self.batch('clients',[{'name':'Иван','phone':'79991234567'},{'name':'Другой','phone':'89991234567'}])
        self.assertEqual([r['action'] for r in b.results],['create','error'])
    def test_preview_refreshes_when_crm_changed(self):
        b=self.batch('clients',[{'name':'Импорт','phone':'79991234567'}])
        Client.objects.create(tenant=self.tenant,name='Создан позже',phone='79991234567')
        self.assertIsNone(apply_import(b,self.owner));b.refresh_from_db();self.assertEqual(b.results[0]['action'],'skip')
    def test_undo_refuses_changed_card(self):
        b=self.batch('clients',[{'name':'Импорт','phone':'79991234567'}]);apply_import(b,self.owner)
        c=Client.objects.get();c.notes='Позднее изменение';c.save()
        with self.assertRaises(ValueError):undo_import(b,self.owner)
        self.assertTrue(Client.objects.exists())
    def test_undo_refuses_new_relationship(self):
        b=self.batch('clients',[{'name':'Импорт','phone':'79991234567'}]);apply_import(b,self.owner)
        Car.objects.create(tenant=self.tenant,client=Client.objects.get(),plate='A123BC77')
        with self.assertRaises(ValueError):undo_import(b,self.owner)
        self.assertTrue(Car.objects.exists())
    def test_cars_link_existing_client_and_duplicate_vin(self):
        c=Client.objects.create(tenant=self.tenant,name='Иван',phone='79991234567')
        rows=[{'client_phone':'79991234567','plate':plate,'vin':'WBA12345678901234'} for plate in ['A123BC77','B123BC77']]
        b=self.batch('cars',rows);self.assertEqual([r['action'] for r in b.results],['create','error'])
        apply_import(b,self.owner,skip_errors=True);self.assertEqual(Car.objects.get().client,c)
    def test_cars_cannot_reference_foreign_client(self):
        other,_=create_company_owner(company_name='Другая',slug='import-other',owner_name='Владелец',email='otherimport@example.com',password='secret-test')
        Client.objects.create(tenant=other,name='Чужой',phone='79991234567')
        b=self.batch('cars',[{'client_phone':'79991234567','plate':'A123BC77'}]);self.assertEqual(b.results[0]['action'],'error')
    def test_employee_limit(self):
        b=self.batch('employees',[{'name':f'Сотрудник {i}'} for i in range(5)])
        self.assertTrue(all(r['action']=='error' for r in b.results))
    def test_employee_import_and_owner_not_counted(self):
        b=self.batch('employees',[{'name':f'Сотрудник {i}'} for i in range(4)])
        self.assertTrue(all(r['action']=='create' for r in b.results));apply_import(b,self.owner)
        self.assertEqual(Employee.objects.exclude(user=self.owner).count(),4);undo_import(b,self.owner);self.assertFalse(Employee.objects.exclude(user=self.owner).exists())
    def test_consumables_create_stock_journal_and_undo(self):
        b=self.batch('consumables',[{'name':'Материал','category':'Новая категория','unit':'л','cost_per_unit':'250,50','planned_stock':'10','actual_stock':'8'}])
        self.assertEqual(b.results[0]['action'],'create');apply_import(b,self.owner)
        self.assertEqual(Consumable.objects.get().actual_stock,Decimal('8'));self.assertEqual(InventoryMovement.objects.count(),1)
        undo_import(b,self.owner);self.assertFalse(Consumable.objects.exists());self.assertFalse(InventoryMovement.objects.exists())
        self.assertFalse(InventoryCategory.objects.filter(name='Новая категория').exists())
    def test_csv_encoding_and_excel_integer_phone(self):
        columns,rows=read_table(SimpleUploadedFile('clients.csv','Имя;Телефон\nИван;88005553535'.encode('cp1251')))
        self.assertEqual(columns,['Имя','Телефон']);self.assertEqual(rows[0]['values'][0],'Иван')
        from openpyxl import Workbook
        w=Workbook();w.active.append(['Имя','Телефон']);w.active.append(['Иван',88005553535]);data=io.BytesIO();w.save(data)
        _,rows=read_table(SimpleUploadedFile('clients.xlsx',data.getvalue()));self.assertEqual(rows[0]['values'][1],'88005553535')
    def test_owner_upload_mapping_preview_apply_and_report(self):
        response=self.client.post('/data/import/',{'kind':'clients','file':SimpleUploadedFile('clients.csv','Имя;Телефон\nИван;79991234567'.encode())},**self.host)
        self.assertEqual(response.status_code,302);url=response.url
        response=self.client.post(url,{'action':'preview','duplicate_mode':'skip','map_name':'0','map_phone':'1'},**self.host)
        self.assertEqual(response.status_code,302);self.assertFalse(Client.objects.exists())
        self.assertContains(self.client.get(url,**self.host),'Подтвердить импорт')
        self.client.post(url,{'action':'apply'},**self.host);self.assertEqual(Client.objects.count(),1)
        b=ImportBatch.objects.get();self.assertEqual(self.client.get(f'/data/import/{b.pk}/report/',**self.host).status_code,200)
    def test_import_is_owner_only(self):
        user=User.objects.create_user(email='staffimport@example.com',password='secret-test');Membership.objects.create(tenant=self.tenant,user=user,role='admin')
        self.client.force_login(user);self.assertEqual(self.client.get('/data/import/',**self.host).status_code,403)
    def test_guide_and_templates_available_for_each_kind(self):
        for kind in FIELDS:
            self.assertEqual(self.client.get(f'/data/import/guide/{kind}/',**self.host).status_code,200)
            self.assertEqual(self.client.get(f'/data/import/template/{kind}/?format=xlsx',**self.host).status_code,200)
    def test_export_excludes_account_challenges_and_device_keys(self):
        import zipfile
        issue_challenge(self.owner,'verify-email',self.tenant)
        PushSubscription.objects.create(user=self.owner,tenant=self.tenant,endpoint='https://fcm.googleapis.com/fcm/send/export-test',p256dh='key',auth='secret')
        response=self.client.get('/company/export/',**self.host)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn('customers-client.json',archive.namelist())
            self.assertFalse(any(name.startswith('accounts-') for name in archive.namelist()))
            self.assertNotIn('core-pushsubscription.json',archive.namelist())
            self.assertNotIn('core-webpushconfig.json',archive.namelist())
    def test_subscription_tab_removed(self):
        response=self.client.get('/profile/settings/',**self.host)
        self.assertContains(response,'Импорт и экспорт');self.assertNotContains(response,'Тариф и подписка')

@override_settings(**TEST_SETTINGS)
class SecurityTests(TestCase):
    def setUp(self):
        self.tenant,self.owner=create_company_owner(company_name='Защита',slug='security-test',owner_name='Владелец',email='security@example.com',password='secret-test')
        self.host={'HTTP_HOST':'security-test.localhost'}
    def code(self):return re.search(r'Ваш код: (\d{6})',mail.outbox[-1].body).group(1)
    def test_enable_requires_password_and_mail_confirmation(self):
        self.client.force_login(self.owner)
        self.client.post('/account/security/',{'action':'enable','password':'bad'},**self.host)
        self.assertFalse(SecurityChallenge.objects.exists())
        self.client.post('/account/security/',{'action':'enable','password':'secret-test'},**self.host)
        self.owner.refresh_from_db();self.assertFalse(self.owner.email_2fa)
        response=self.client.post('/account/security/',{'action':'confirm','code':self.code()},**self.host)
        self.owner.refresh_from_db();self.assertTrue(self.owner.email_2fa);self.assertIsNotNone(self.owner.email_verified_at)
        self.assertContains(response,'Сохраните резервные коды');self.assertEqual(RecoveryCode.objects.count(),10)
    def test_bad_smtp_does_not_enable(self):
        self.client.force_login(self.owner)
        with patch('apps.accounts.security.send_mail',side_effect=OSError('unavailable')):
            self.assertContains(self.client.post('/account/security/',{'action':'enable','password':'secret-test'},**self.host),'Не удалось отправить код')
        self.owner.refresh_from_db();self.assertFalse(self.owner.email_2fa);self.assertFalse(SecurityChallenge.objects.exists())
    def enable(self):
        self.owner.email_2fa=True;self.owner.save(update_fields=['email_2fa'])
    def test_password_alone_does_not_login_with_2fa(self):
        self.enable();response=self.client.post('/login/',{'username':self.owner.email,'password':'secret-test'},**self.host)
        self.assertEqual(response.url,'/login/verify/');self.assertNotIn('_auth_user_id',self.client.session)
        response=self.client.post('/login/verify/',{'code':self.code()},**self.host)
        self.assertEqual(response.status_code,302);self.assertEqual(self.client.session['_auth_user_id'],str(self.owner.pk))
    def test_recovery_login_when_smtp_unavailable(self):
        self.enable();codes=new_recovery_codes(self.owner)
        with patch('apps.accounts.security.send_mail',side_effect=OSError()):
            self.client.post('/login/',{'username':self.owner.email,'password':'secret-test'},**self.host)
        self.client.post('/login/verify/',{'code':codes[0]},**self.host)
        self.assertIn('_auth_user_id',self.client.session);self.assertEqual(RecoveryCode.objects.filter(used_at__isnull=False).count(),1)
    def test_code_is_one_use_expiring_and_attempt_limited(self):
        ch=issue_challenge(self.owner,'login',self.tenant);code=self.code()
        with transaction.atomic(): self.assertTrue(check_challenge(ch,code)[0])
        with transaction.atomic(): self.assertFalse(check_challenge(ch,code)[0])
        ch=issue_challenge(self.owner,'enable',self.tenant);ch.expires_at=timezone.now()-timedelta(seconds=1);ch.save()
        with transaction.atomic():self.assertFalse(check_challenge(ch,self.code())[0])
        ch=issue_challenge(self.owner,'disable',self.tenant)
        with transaction.atomic():
            for _ in range(5):self.assertFalse(check_challenge(ch,'wrong')[0])
            self.assertFalse(check_challenge(ch,self.code())[0])
    def test_password_change_invalidates_pending_code(self):
        ch=issue_challenge(self.owner,'login',self.tenant);code=self.code()
        self.owner.set_password('new-password');self.owner.save();ch.user=self.owner
        with transaction.atomic():self.assertFalse(check_challenge(ch,code)[0])
    def test_resend_cooldown_and_attempts_not_reset(self):
        ch=issue_challenge(self.owner,'login',self.tenant);old=self.code()
        with self.assertRaises(ValueError):resend_challenge(ch)
        ch.sent_at=timezone.now()-timedelta(seconds=61);ch.attempts=3;ch.save();resend_challenge(ch)
        self.assertEqual(ch.attempts,3)
        with transaction.atomic():self.assertFalse(check_challenge(ch,old)[0])
    def test_every_role_can_enable_2fa(self):
        self.client.force_login(self.owner);Membership.objects.filter(user=self.owner).update(role='admin')
        self.assertEqual(self.client.get('/account/security/',**self.host).status_code,200)
    def test_old_platform_paths_gone_and_reserved_slug(self):
        from apps.accounts.forms import CompanyRegistrationForm
        for host in ['localhost','security-test.localhost','platform.localhost']:
            self.assertEqual(self.client.get('/platform/',HTTP_HOST=host).status_code,404)
        for slug in ['admin','support','administrator','about','platform','noreply']:
            form=CompanyRegistrationForm({'slug':slug});form.is_valid();self.assertIn('slug',form.errors)
        form=CompanyRegistrationForm({'slug':'platfrom'});form.is_valid();self.assertNotIn('slug',form.errors)

@override_settings(**TEST_SETTINGS)
class PhoneTests(TestCase):
    def test_format_normalization_and_invalid_numbers(self):
        for phone in ['88005553535','8005553535','+7 (800) 555-35-35']:
            self.assertEqual(format_phone(phone),'+7 (800) 555-35-35')
        for phone in ['123','+1 202 555 0123','+7 123 456 7890','799912345678','79991234567abc','+7 701 123 4567']:
            with self.assertRaises(ValidationError):format_phone(phone)
    def test_client_update_fields_updates_normalized_phone(self):
        tenant,_=create_company_owner(company_name='Телефоны',slug='phone-test',owner_name='Владелец',email='phone@example.com',password='secret-test')
        c=Client.objects.create(tenant=tenant,name='Клиент',phone='79991234567');c.phone='88005553535';c.save(update_fields=['phone']);c.refresh_from_db()
        self.assertEqual(c.normalized_phone,'78005553535');self.assertEqual(c.phone,'+7 (800) 555-35-35')

@override_settings(**TEST_SETTINGS)
class NotificationTests(TestCase):
    def setUp(self):
        self.tenant,self.owner=create_company_owner(company_name='Уведомления',slug='notify-test',owner_name='Владелец',email='notify@example.com',password='secret-test')
        self.host={'HTTP_HOST':'notify-test.localhost'};self.client.force_login(self.owner)
    def test_feed_unread_count_scope_and_read_on_open(self):
        since=timezone.now()-timedelta(seconds=1)
        row=notify(tenant=self.tenant,kind='online_booking',title='Новая запись',recipient=self.owner,url='/app/')
        other=User.objects.create_user(email='notifyother@example.com',password='secret-test')
        Notification.objects.create(tenant=self.tenant,recipient=other,title='Чужое')
        data=self.client.get('/notifications/feed/',{'after':since.isoformat()},**self.host).json()
        self.assertEqual(data['unread'],1);self.assertEqual(len(data['items']),1)
        row.refresh_from_db();self.assertIsNone(row.read_at)
        response=self.client.post(data['items'][0]['read_url'],{},HTTP_X_REQUESTED_WITH='XMLHttpRequest',**self.host)
        self.assertEqual(response.json()['unread'],0)
    def test_disabled_kinds_not_created_and_existing_hidden(self):
        Notification.objects.create(tenant=self.tenant,recipient=self.owner,title='Старая запись',kind='online_booking')
        self.owner.disabled_notification_kinds=['online_booking'];self.owner.save(update_fields=['disabled_notification_kinds'])
        self.assertEqual(notify(tenant=self.tenant,kind='online_booking',title='Отключено',recipient=self.owner),[])
        self.assertEqual(self.client.get('/notifications/feed/',**self.host).json()['unread'],0)
    def test_all_kinds_can_be_disabled_and_push_endpoints_removed(self):
        PushSubscription.objects.create(user=self.owner,tenant=self.tenant,endpoint='https://fcm.googleapis.com/fcm/send/test',p256dh='key',auth='secret')
        self.client.post('/notifications/settings/',{},**self.host)
        self.owner.refresh_from_db();self.assertFalse(self.owner.notification_push);self.assertFalse(self.owner.notification_toasts)
        self.assertIn('online_booking',self.owner.disabled_notification_kinds);self.assertFalse(PushSubscription.objects.exists())
    def subscription(self):
        import base64
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
        enc=lambda b:base64.urlsafe_b64encode(b).decode().rstrip('=')
        return {'endpoint':'https://fcm.googleapis.com/fcm/send/test','keys':{'p256dh':enc(ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint)),'auth':enc(b'1234567890123456')}}
    def test_push_requires_opt_in_csrf_and_public_push_host(self):
        import json
        data=self.subscription()
        response=self.client.post('/notifications/push/subscribe/',json.dumps(data),content_type='application/json',**self.host)
        self.assertEqual(response.status_code,403)
        self.owner.notification_push=True;self.owner.save(update_fields=['notification_push'])
        self.assertEqual(self.client.post('/notifications/push/subscribe/',json.dumps(data),content_type='application/json',**self.host).status_code,200)
        data['endpoint']='https://web.push.apple.com/3/device/test'
        self.assertEqual(self.client.post('/notifications/push/subscribe/',json.dumps(data),content_type='application/json',**self.host).status_code,200)
        for endpoint in ['http://fcm.googleapis.com/x','https://127.0.0.1/x','https://fcm.googleapis.com.evil.test/x','https://fcm.googleapis.com:8080/x','https://user:pass@fcm.googleapis.com/x']:
            data['endpoint']=endpoint;self.assertEqual(self.client.post('/notifications/push/subscribe/',json.dumps(data),content_type='application/json',**self.host).status_code,400)
        from django.test import Client as HttpClient
        c=HttpClient(enforce_csrf_checks=True);c.force_login(self.owner)
        self.assertEqual(c.post('/notifications/push/subscribe/',json.dumps(self.subscription()),content_type='application/json',**self.host).status_code,403)
    def test_vapid_key_persistent_and_service_worker_manifest_public(self):
        from apps.core.notifications import webpush_config
        key=webpush_config();self.assertEqual(webpush_config().public_key,key.public_key)
        self.assertEqual(self.client.get('/crm-sw.js',**self.host)['Service-Worker-Allowed'],'/')
        self.assertEqual(self.client.get('/manifest.webmanifest',**self.host).json()['start_url'],'/app/')
    def test_stale_push_endpoint_removed(self):
        from apps.core.tasks import deliver_push
        from pywebpush import WebPushException
        from requests import Response
        self.owner.notification_push=True;self.owner.save(update_fields=['notification_push'])
        data=self.subscription();PushSubscription.objects.create(user=self.owner,tenant=self.tenant,endpoint=data['endpoint'],**data['keys'])
        row=Notification.objects.create(tenant=self.tenant,recipient=self.owner,title='Запись',kind='online_booking')
        response=Response();response.status_code=410
        with patch('pywebpush.webpush',side_effect=WebPushException('gone',response=response)):
            deliver_push.run(str(row.pk))
        self.assertFalse(PushSubscription.objects.exists())
    def test_feed_pagination_does_not_drop_equal_timestamps(self):
        since=timezone.now()-timedelta(seconds=1);moment=timezone.now()
        rows=[Notification(tenant=self.tenant,recipient=self.owner,title=f'Запись {i}') for i in range(105)]
        Notification.objects.bulk_create(rows);Notification.objects.update(created_at=moment)
        first=self.client.get('/notifications/feed/',{'after':since.isoformat()},**self.host).json()
        second=self.client.get('/notifications/feed/',{'after':first['cursor'],'after_id':first['cursor_id']},**self.host).json()
        self.assertEqual(len(first['items']),100);self.assertEqual(len(second['items']),5)
        self.assertEqual(len({r['id'] for r in first['items']+second['items']}),105)
