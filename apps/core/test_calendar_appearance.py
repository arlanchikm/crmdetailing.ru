import io
import shutil
import tempfile
from datetime import datetime, timedelta, time
from decimal import Decimal
from zoneinfo import ZoneInfo
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from apps.accounts.models import User
from apps.tenants.models import Membership
from apps.tenants.services import create_company_owner
from apps.staff.models import Employee, EmployeeShift, CompanyRole, ensure_default_roles
from apps.catalog.models import WorkPost
from apps.customers.models import Client, Car
from apps.operations.models import Appointment, AppointmentAssignee
from .models import IntegrationChannel


@override_settings(DEBUG=True, ALLOWED_HOSTS=['testserver','localhost','.localhost'], TENANT_DOMAIN_SUFFIXES=('.localhost','.crmdetailing.ru'))
class CalendarAppearanceTests(TestCase):
    def setUp(self):
        self.tenant,self.owner=create_company_owner(company_name='Студия',slug='visualqa',owner_name='Владелец',email='visualqa@example.test',password='test-password')
        self.location=self.tenant.locations.get(is_primary=True)
        self.host={'HTTP_HOST':'visualqa.localhost'}
        self.roles=ensure_default_roles(self.tenant)
        self.admin=User.objects.create_user('visualadmin@example.test','test-password')
        Membership.objects.create(tenant=self.tenant,user=self.admin,role='admin')
        self.employee=Employee.objects.create(tenant=self.tenant,location=self.location,name='Администратор',user=self.admin,company_role=self.roles['admin'])
        self.day=timezone.now().astimezone(ZoneInfo(self.tenant.timezone)).date()+timedelta(days=2)
        for employee in [self.employee]:
            EmployeeShift.objects.create(tenant=self.tenant,location=self.location,employee=employee,date=self.day,start_time=time(8),end_time=time(22))
        self.customer=Client.objects.create(tenant=self.tenant,name='Иван',phone='+7 (999) 123-45-67')
        self.car=Car.objects.create(tenant=self.tenant,client=self.customer,make='BMW',model='X3',plate='A123BC77')
        self.post=WorkPost.objects.create(tenant=self.tenant,location=self.location,name='Пост 1')
        self.client.force_login(self.owner)

    def appointment(self, **changes):
        start=timezone.make_aware(datetime.combine(self.day,time(10)),ZoneInfo(self.tenant.timezone))
        data={'tenant':self.tenant,'location':self.location,'client':self.customer,'car':self.car,'employee':self.employee,'start_at':start,'end_at':start+timedelta(hours=1),'subtotal':Decimal(100),'total':Decimal(100)}
        data.update(changes)
        return Appointment.objects.create(**data)

    def test_delete_and_restore_preserve_records_and_independently_deleted_cars(self):
        job=self.appointment(status='completed',payment_status='paid')
        previous=Car.objects.create(tenant=self.tenant,client=self.customer,plate='OLD777',archived_at=timezone.now()-timedelta(days=1))
        self.assertEqual(self.client.post(f'/clients/{self.customer.pk}/archive/',**self.host).status_code,302)
        self.customer.refresh_from_db();self.car.refresh_from_db()
        self.assertIsNotNone(self.customer.archived_at)
        self.assertEqual(self.customer.archived_at,self.car.archived_at)
        self.assertContains(self.client.get('/clients/?archived=1',**self.host),'Иван')
        self.assertContains(self.client.get(f'/cars/{self.car.pk}/',**self.host),'Восстановить')
        self.client.post(f'/clients/{self.customer.pk}/restore/',**self.host)
        self.customer.refresh_from_db();self.car.refresh_from_db();previous.refresh_from_db()
        self.assertIsNone(self.customer.archived_at);self.assertIsNone(self.car.archived_at)
        self.assertIsNotNone(previous.archived_at)
        self.assertTrue(Appointment.objects.filter(pk=job.pk,payment_status='paid',car=self.car).exists())

    def test_admin_delete_permission_can_be_revoked_without_reappearing(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(f'/cars/{self.car.pk}/archive/',**self.host).status_code,302)
        self.assertEqual(self.client.post(f'/cars/{self.car.pk}/restore/',**self.host).status_code,302)
        role=self.roles['admin'];role.permissions.remove('clients_delete');role.save()
        ensure_default_roles(self.tenant)
        role.refresh_from_db();self.assertNotIn('clients_delete',role.permissions)
        self.assertEqual(self.client.post(f'/cars/{self.car.pk}/archive/',**self.host).status_code,403)
        self.assertEqual(self.client.get('/clients/?archived=1',**self.host).status_code,403)

    def test_restoring_car_requires_active_parent(self):
        self.client.post(f'/clients/{self.customer.pk}/archive/',**self.host)
        self.client.post(f'/cars/{self.car.pk}/restore/',**self.host)
        self.car.refresh_from_db();self.assertIsNotNone(self.car.archived_at)

    def test_permanent_client_delete_keeps_appointment_snapshots(self):
        job = self.appointment(status="completed", payment_status="paid")
        self.client.post(f'/clients/{self.customer.pk}/archive/', **self.host)
        response = self.client.post(
            f'/clients/{self.customer.pk}/delete-permanently/', **self.host
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Client.objects.filter(pk=self.customer.pk).exists())
        self.assertFalse(Car.objects.filter(pk=self.car.pk).exists())
        job.refresh_from_db()
        self.assertIsNone(job.client)
        self.assertIsNone(job.car)
        self.assertEqual(job.client_display_name, 'Иван')
        self.assertIn('BMW X3', job.car_display)

    def test_cross_tenant_delete_and_restore_fail(self):
        other,_=create_company_owner(company_name='Other',slug='othervisual',owner_name='Owner',email='othervisual@example.test',password='test-password')
        customer=Client.objects.create(tenant=other,name='Other',phone='+7 (999) 765-43-21',archived_at=timezone.now())
        self.assertEqual(self.client.post(f'/clients/{customer.pk}/restore/',**self.host).status_code,404)
        self.assertEqual(self.client.post(f'/clients/{customer.pk}/archive/',**self.host).status_code,404)

    def test_post_calendar_remembers_preference_and_renders_unassigned_once(self):
        job=self.appointment()
        second=Employee.objects.create(tenant=self.tenant,location=self.location,name='Второй')
        AppointmentAssignee.objects.create(tenant=self.tenant,appointment=job,employee=self.employee,is_primary=True,salary_share_percent=50)
        AppointmentAssignee.objects.create(tenant=self.tenant,appointment=job,employee=second,salary_share_percent=50)
        self.client.post('/appointments/calendar-view/',{'resource_kind':'post','date':self.day.isoformat()},**self.host)
        page=self.client.get('/appointments/?date='+self.day.isoformat(),**self.host)
        self.assertEqual(page.context['resource_kind'],'post')
        self.assertEqual(sum(len(col['entries']) for col in page.context['calendar_columns']),1)
        self.assertContains(page,'Без поста');self.assertContains(page,'Изменить посты')
        self.assertNotContains(page,'>Рабочие посты</a>')
        self.owner.refresh_from_db();self.assertEqual(self.owner.calendar_resource_kind,'post')

    def test_post_move_preserves_assignees_and_rejects_conflicts(self):
        job=self.appointment()
        assignment=AppointmentAssignee.objects.create(tenant=self.tenant,appointment=job,employee=self.employee,is_primary=True,salary_share_percent=100,service_percentages_snapshot={'test':'30'})
        data={'resource_kind':'post','post':str(self.post.pk),'date':self.day.isoformat(),'start':'10:00'}
        self.assertEqual(self.client.post(f'/appointments/{job.pk}/move/',data,**self.host).status_code,200)
        job.refresh_from_db();assignment.refresh_from_db()
        self.assertEqual(job.post,self.post);self.assertEqual(job.employee,self.employee)
        self.assertEqual(assignment.service_percentages_snapshot,{'test':'30'})
        otherpost=WorkPost.objects.create(tenant=self.tenant,location=self.location,name='Занят')
        busy=self.appointment(post=otherpost)
        data['post']=str(otherpost.pk)
        self.assertEqual(self.client.post(f'/appointments/{job.pk}/move/',data,**self.host).status_code,400)
        job.refresh_from_db();self.assertEqual(job.post,self.post)
        busy.delete()
        data['post']=''
        self.assertEqual(self.client.post(f'/appointments/{job.pk}/move/',data,**self.host).status_code,200)
        job.refresh_from_db();self.assertIsNone(job.post_id)
        from django.core.exceptions import ValidationError
        from apps.operations.services import move_appointment_post
        WorkPost.objects.filter(pk=self.post.pk).update(active=False)
        with self.assertRaisesMessage(ValidationError, 'Рабочий пост больше недоступен'):
            move_appointment_post(appointment=job,post=self.post,start_at=job.start_at)

    def test_post_management_remains_owner_only_even_with_settings_permission(self):
        self.roles['admin'].permissions.append('settings_manage');self.roles['admin'].save()
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/posts/new/',**self.host).status_code,403)
        self.assertEqual(self.client.get(f'/posts/{self.post.pk}/edit/',**self.host).status_code,403)
        self.admin.calendar_resource_kind='post';self.admin.save()
        self.assertNotContains(self.client.get('/appointments/',**self.host),'Изменить посты')

    def test_company_settings_manage_posts_and_legacy_url_redirects(self):
        self.assertContains(self.client.get('/company/settings/',**self.host),'Пост 1')
        self.assertEqual(self.client.get('/posts/',**self.host).status_code,302)

    def test_preview_allows_same_origin_iframe_and_cannot_create_booking(self):
        page=self.client.get('/integrations/form/preview/',**self.host)
        self.assertEqual(page.status_code,200)
        self.assertEqual(page['X-Frame-Options'],'SAMEORIGIN')
        self.assertContains(page,'data-booking-preview="true"')
        self.assertEqual(self.client.post('/integrations/form/preview/',{},**self.host).status_code,405)
        self.assertEqual(Appointment.objects.count(),0)

    def test_custom_appearance_is_separate_from_crm_and_public_form_uses_it(self):
        channel=IntegrationChannel.objects.create(tenant=self.tenant,kind='website',enabled=True)
        values={'accent_color':'#15A060','page_color':'#EBF9EE','text_color':'#183122','overlay':25}
        original=self.tenant.primary_color
        self.assertEqual(self.client.post('/integrations/form/',values,**self.host).status_code,302)
        channel.refresh_from_db();self.tenant.refresh_from_db()
        self.assertEqual(channel.settings['appearance']['accent_color'],'#15A060')
        self.assertNotIn('block_color',channel.settings['appearance'])
        self.assertEqual(self.tenant.primary_color,original)
        self.assertContains(self.client.get(f'/booking/{channel.public_token}/',**self.host),'#15A060')
        values['text_color']='red;position:fixed'
        self.assertEqual(self.client.post('/integrations/form/',values,**self.host).status_code,200)
        channel.refresh_from_db();self.assertEqual(channel.settings['appearance']['text_color'],'#183122')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/integrations/form/',**self.host).status_code,403)

    def test_upload_background_validate_and_reset(self):
        directory=tempfile.mkdtemp();self.addCleanup(shutil.rmtree,directory)
        with override_settings(MEDIA_ROOT=directory):
            channel=IntegrationChannel.objects.create(tenant=self.tenant,kind='website',enabled=True)
            image=io.BytesIO();Image.new('RGB',(160,90),'green').save(image,'PNG')
            values={'accent_color':'#15A060','page_color':'#EBF9EE','text_color':'#183122','overlay':100,'background':SimpleUploadedFile('background.png',image.getvalue(),content_type='image/png')}
            self.assertEqual(self.client.post('/integrations/form/',values,**self.host).status_code,302)
            channel.refresh_from_db();self.assertTrue(channel.background_image.name.endswith('.webp'))
            self.assertEqual(channel.settings['appearance']['overlay'],100)
            imagepage=self.client.get(f'/booking/{channel.public_token}/background/',**self.host)
            self.assertEqual(imagepage.status_code,200);self.assertEqual(imagepage['Content-Type'],'image/webp');self.assertGreater(len(b''.join(imagepage.streaming_content)),0)
            values['background']=SimpleUploadedFile('broken.png',b'not an image',content_type='image/png')
            self.assertEqual(self.client.post('/integrations/form/',values,**self.host).status_code,200)
            self.client.post('/integrations/form/',{'action':'reset'},**self.host)
            channel.refresh_from_db();self.assertFalse(channel.background_image)
            self.assertNotIn('appearance',channel.settings)
