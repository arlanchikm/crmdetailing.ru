import uuid
from datetime import datetime,timedelta,time
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.test import TestCase,override_settings
from django.core import signing
from django.utils import timezone
from apps.tenants.services import create_company_owner
from apps.staff.models import Employee,EmployeeService,EmployeeShift
from apps.catalog.models import Service,ServiceVehicleClassPrice,WorkPost
from apps.customers.models import ensure_default_vehicle_classes
from apps.operations.models import Appointment,ClientRating
from .models import IntegrationChannel,IntegrationLead,Notification
from .booking import booking_catalog,available_assignments

@override_settings(DEBUG=True,ALLOWED_HOSTS=['testserver','localhost','.localhost'],TENANT_DOMAIN_SUFFIXES=('.localhost','.crmdetailing.ru'))
class OnlineBookingTests(TestCase):
    def setUp(self):
        self.tenant,self.owner=create_company_owner(company_name='Мойка',slug='online',owner_name='Владелец',email='online@example.com',password='test-password')
        self.location=self.tenant.locations.get(is_primary=True)
        self.channel=IntegrationChannel.objects.create(tenant=self.tenant,kind='website',enabled=True)
        self.classes=ensure_default_vehicle_classes(self.tenant)
        self.service=Service.objects.create(tenant=self.tenant,location=self.location,name='Мойка',price=100,duration_minutes=90)
        ServiceVehicleClassPrice.objects.create(tenant=self.tenant,service=self.service,vehicle_class=self.classes['sedan'],price=500)
        ServiceVehicleClassPrice.objects.create(tenant=self.tenant,service=self.service,vehicle_class=self.classes['suv'],price=900)
        self.employee=Employee.objects.create(tenant=self.tenant,location=self.location,name='Мастер')
        EmployeeService.objects.create(tenant=self.tenant,employee=self.employee,service=self.service)
        self.date=timezone.localtime(timezone.now(),ZoneInfo(self.tenant.timezone)).date()+timedelta(days=2)
        EmployeeShift.objects.create(tenant=self.tenant,location=self.location,employee=self.employee,date=self.date,start_time=time(9),end_time=time(18))
        self.url=f'/booking/{self.channel.public_token}/'
        self.host={'HTTP_HOST':'online.localhost'}
        self.token=signing.dumps({'channel':str(self.channel.pk),'nonce':str(uuid.uuid4())},salt='online-booking')
    def test_catalog_categories_only_include_bookable_services(self):
        from apps.catalog.models import ServiceCategory
        category = ServiceCategory.objects.create(tenant=self.tenant, location=self.location, name='Мойка кузова')
        ServiceCategory.objects.create(tenant=self.tenant, location=self.location, name='Пустая категория')
        self.service.category = category; self.service.save()
        data = booking_catalog(self.channel)[3]
        self.assertEqual(data['categories'], [{'id':str(category.pk),'name':'Мойка кузова'}])
        self.assertEqual(data['services'][0]['category'], str(category.pk))
        self.service.category = None; self.service.save()
        self.assertEqual(booking_catalog(self.channel)[3]['categories'], [{'id':'uncategorized','name':'Без категории'}])

    def test_booking_header_has_address_and_lazy_map(self):
        self.tenant.address = 'Ульяновск, 2-й пер. Мира, 13А'
        self.tenant.latitude = Decimal('54.322900'); self.tenant.longitude = Decimal('48.396900')
        self.tenant.save()
        response = self.client.get(self.url, **self.host)
        self.assertContains(response, self.tenant.address)
        self.assertContains(response, 'data-booking-map-open', count=2)
        self.assertContains(response, 'data-src="https://yandex.ru/map-widget/v1/')
        self.assertNotContains(response, '<iframe class="booking-map-frame" src=')
        self.assertContains(response, 'data-yandex-app=')
        self.assertNotContains(response, 'booking-route-options')
        self.assertNotContains(response, 'booking-map-external')
        self.assertContains(response, 'booking-categories')

    def data(self,**overrides):
        data={'vehicle_class':str(self.classes['suv'].pk),'services':[str(self.service.pk)],'date':self.date.isoformat(),'time':'10:15','client_name':'Иван','phone':'79991234567','make':'BMW','model':'X5','submission_token':self.token}
        data.update(overrides);return data
    def test_online_booking_creates_crm_record_with_class_price_and_assignment(self):
        response=self.client.post(self.url,self.data(),**self.host)
        self.assertContains(response,'Вы записаны')
        appointment=Appointment.objects.get(tenant=self.tenant)
        self.assertEqual(appointment.employee,self.employee)
        self.assertEqual(appointment.total,Decimal('900'))
        self.assertEqual(appointment.end_at-appointment.start_at,timedelta(minutes=90))
        self.assertEqual(appointment.status,'waiting');self.assertEqual(appointment.payment_status,'unpaid')
        self.assertEqual(appointment.source,'website')
        self.assertEqual(IntegrationLead.objects.get(tenant=self.tenant).status,'processed')
        self.assertTrue(Notification.objects.filter(recipient=self.owner,kind='online_booking').exists())
    def test_busy_slots_removed_and_repeated_submission_does_not_duplicate(self):
        self.client.post(self.url,self.data(),**self.host)
        self.client.post(self.url,self.data(),**self.host)
        self.assertEqual(Appointment.objects.filter(tenant=self.tenant).count(),1)
        result=self.client.get(self.url+'availability/',{'date':self.date.isoformat(),'services':[str(self.service.pk)]},**self.host)
        self.assertNotIn('10:15',result.json()['slots'])
        self.assertNotIn('11:00',result.json()['slots'])
        self.assertIn('11:45',result.json()['slots'])
    def test_company_hours_and_employee_shift_limit_slots(self):
        result=self.client.get(self.url+'availability/',{'date':self.date.isoformat(),'services':[str(self.service.pk)]},**self.host)
        self.assertEqual(result.json()['slots'][0],'09:00')
        self.assertEqual(result.json()['slots'][-1],'16:30')
        self.tenant.working_hours.filter(weekday=self.date.weekday()).update(is_closed=True)
        result=self.client.get(self.url+'availability/',{'date':self.date.isoformat(),'services':[str(self.service.pk)]},**self.host)
        self.assertEqual(result.json()['slots'],[])

    def test_class_duration_limits_available_slots_and_booking_end(self):
        row = self.service.vehicle_class_prices.get(vehicle_class=self.classes['suv'])
        row.duration_minutes = 120; row.save()
        result = self.client.get(self.url+'availability/', {
            'date':self.date.isoformat(), 'services':[str(self.service.pk)],
            'vehicle_class':str(self.classes['suv'].pk),
        }, **self.host).json()
        self.assertEqual(result['duration'], 120)
        self.assertEqual(result['slots'][-1], '16:00')
        self.client.post(self.url, self.data(), **self.host)
        appointment = Appointment.objects.get(tenant=self.tenant)
        self.assertEqual(appointment.end_at-appointment.start_at, timedelta(minutes=120))
        self.assertEqual(appointment.items.get().duration_minutes_snapshot, 120)
    def test_unsupported_employee_and_service_rejected_on_server(self):
        incapable=Employee.objects.create(tenant=self.tenant,location=self.location,name='Не умеет')
        response=self.client.post(self.url,self.data(employee=str(incapable.pk)),**self.host)
        self.assertFalse(Appointment.objects.filter(tenant=self.tenant).exists())
        foreign,_=create_company_owner(company_name='Другая',slug='foreign-online',owner_name='Другой',email='foreign-online@example.com',password='test-password')
        service=Service.objects.create(tenant=foreign,location=foreign.locations.first(),name='Чужая',price=100,duration_minutes=15)
        self.client.post(self.url,self.data(services=[str(service.pk)]),**self.host)
        self.assertFalse(Appointment.objects.filter(tenant=self.tenant).exists())
    def test_no_explicit_capability_and_expired_subscription_cannot_book(self):
        EmployeeService.objects.filter(tenant=self.tenant).delete()
        self.assertEqual(booking_catalog(self.channel)[3]['services'],[])
        self.client.post(self.url,self.data(),**self.host)
        self.assertEqual(Appointment.objects.filter(tenant=self.tenant).count(),0)
        sub=self.tenant.subscription;sub.trial_ends_at=timezone.now()-timedelta(seconds=1);sub.save()
        response=self.client.get(self.url,**self.host)
        self.assertContains(response,'Онлайн-запись временно недоступна')
    def test_quarter_hour_interval_and_shift_end_checked_at_submit(self):
        self.client.post(self.url,self.data(time='10:17'),**self.host)
        self.client.post(self.url,self.data(time='17:00'),**self.host)
        self.assertEqual(Appointment.objects.filter(tenant=self.tenant).count(),0)

    def test_auto_assignment_chooses_least_busy_capable_specialist(self):
        other=Employee.objects.create(tenant=self.tenant,location=self.location,name='Второй')
        EmployeeService.objects.create(tenant=self.tenant,employee=other,service=self.service)
        EmployeeShift.objects.create(tenant=self.tenant,location=self.location,employee=other,date=self.date,start_time=time(9),end_time=time(18))
        self.client.post(self.url,self.data(employee=str(self.employee.pk),time='09:00'),**self.host)
        self.token=signing.dumps({'channel':str(self.channel.pk),'nonce':str(uuid.uuid4())},salt='online-booking')
        self.client.post(self.url,self.data(time='12:00'),**self.host)
        self.assertEqual(Appointment.objects.filter(tenant=self.tenant).order_by('-start_at').first().employee,other)

    def test_a_full_work_post_prevents_booking_and_quarter_hours_are_rounded(self):
        post=WorkPost.objects.create(tenant=self.tenant,location=self.location,name='Пост 1')
        self.client.post(self.url,self.data(),**self.host)
        another=Employee.objects.create(tenant=self.tenant,location=self.location,name='Другой свободный')
        EmployeeService.objects.create(tenant=self.tenant,employee=another,service=self.service)
        EmployeeShift.objects.create(tenant=self.tenant,location=self.location,employee=another,date=self.date,start_time=time(9),end_time=time(18))
        result=self.client.get(self.url+'availability/',{'date':self.date.isoformat(),'services':[str(self.service.pk)]},**self.host)
        self.assertNotIn('10:15',result.json()['slots'])
        Appointment.objects.filter(tenant=self.tenant).update(status='no_show')
        self.tenant.working_hours.filter(weekday=self.date.weekday()).update(opens_at=time(9,10))
        result=self.client.get(self.url+'availability/',{'date':self.date.isoformat(),'services':[str(self.service.pk)]},**self.host)
        self.assertEqual(result.json()['slots'][0],'09:15')

    def test_day_schedule_loaded_once_for_all_slots(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        from .booking import availability_context
        with CaptureQueriesContext(connection) as captured:
            context=availability_context(self.tenant,self.date)
            for minute in range(0,60,15):
                available_assignments(self.tenant,[self.service],self.date,time(10,minute),context=context)
        self.assertLessEqual(len(captured),10)

    def test_default_date_uses_nearest_available_shift(self):
        response=self.client.get(self.url,**self.host)
        self.assertContains(response,f'value="{self.date.isoformat()}"')
        self.assertContains(response,'js/app.js')
        result=self.client.get(self.url+'availability/',{'date':timezone.localtime().date().isoformat(),'nearest':'1','services':[str(self.service.pk)]},**self.host).json()
        self.assertEqual(result['date'],self.date.isoformat());self.assertTrue(result['slots'])
    def test_nearest_skips_company_closed_day_and_manual_date_stays(self):
        self.tenant.working_hours.filter(weekday=self.date.weekday()).update(is_closed=True)
        later=self.date+timedelta(days=1)
        EmployeeShift.objects.create(tenant=self.tenant,location=self.location,employee=self.employee,date=later,start_time=time(9),end_time=time(18))
        args={'date':self.date.isoformat(),'nearest':'1','services':[str(self.service.pk)]}
        self.assertEqual(self.client.get(self.url+'availability/',args,**self.host).json()['date'],later.isoformat())
        args.pop('nearest')
        result=self.client.get(self.url+'availability/',args,**self.host).json()
        self.assertEqual(result['date'],self.date.isoformat());self.assertEqual(result['slots'],[])

    @override_settings(ALLOWED_HOSTS=['book.crmdetailing.ru'])
    def test_company_link_renders_and_books_without_workspace_session(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        host = {'HTTP_HOST':'book.crmdetailing.ru'}
        response = client.get('/online', **host)
        self.assertContains(response, 'Мойка')
        self.assertIsNone(response.wsgi_request.tenant)
        response = client.post('/online', self.data(), **host)
        self.assertContains(response, 'Вы записаны')
        self.assertContains(response, f'{self.date:%d.%m.%Y} 10:15')
        self.assertEqual(Appointment.objects.get().tenant, self.tenant)
        availability = client.get(self.url+'availability/', {'date':self.date.isoformat(), 'services':[str(self.service.pk)]}, **host)
        self.assertEqual(availability.status_code, 200)
        self.assertNotIn('10:15', availability.json()['slots'])

    @override_settings(ALLOWED_HOSTS=['book.crmdetailing.ru'])
    def test_company_link_rejects_missing_and_disabled_channels(self):
        host = {'HTTP_HOST':'book.crmdetailing.ru'}
        self.assertEqual(self.client.get('/missing', **host).status_code, 404)
        self.channel.enabled = False
        self.channel.save()
        self.assertEqual(self.client.get('/online', **host).status_code, 404)

    def test_qr_download_is_transparent_png_and_requires_owner(self):
        from io import BytesIO
        from PIL import Image
        from unittest.mock import patch
        self.client.force_login(self.owner)
        import qrcode
        qr = qrcode.QRCode()
        with patch('qrcode.QRCode', return_value=qr):
            response = self.client.get('/integrations/form/qr/?download=1', **self.host)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(qr.data_list[0].data.decode(), 'https://book.crmdetailing.ru/online')
        image = Image.open(BytesIO(response.content)).convert('RGBA')
        self.assertEqual(image.getpixel((0,0))[3], 0)
        self.assertIn('booking-online-qr.png', response['Content-Disposition'])
        self.client.logout()
        self.assertEqual(self.client.get('/integrations/form/qr/', **self.host).status_code, 302)

    def test_booking_domain_is_reserved_and_allowed_for_tls(self):
        from django.conf import settings
        self.assertIn('book', settings.RESERVED_SUBDOMAINS)
        with override_settings(TLS_ASK_TOKEN='test-token'):
            response=self.client.get('/internal/tls/allow/', {'token':'test-token','domain':'book.crmdetailing.ru'}, **self.host)
        self.assertEqual(response.status_code, 200)
