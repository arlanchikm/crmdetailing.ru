from datetime import timedelta
from io import BytesIO, StringIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django import forms
from PIL import Image

from apps.catalog.models import Service
from apps.customers.models import Client
from apps.operations.models import Appointment, AppointmentService, ClientRating, Complaint
from apps.staff.models import Employee, EmployeeShift
from apps.tenants.models import Membership, Tenant
from apps.tenants.plans import backfill_primary_location_data, ensure_primary_location
from apps.core.images import optimize_image


def image_upload(mode='RGB', size=(2400, 800), image_format='PNG', exif=None):
    output = BytesIO()
    image = Image.new(mode, size, (30, 70, 100, 80) if mode == 'RGBA' else (30, 70, 100))
    image.save(output, format=image_format, **({'exif': exif} if exif else {}))
    return SimpleUploadedFile('image.' + image_format.lower(), output.getvalue(), content_type='image/' + image_format.lower())


class IdentityImageTests(SimpleTestCase):
    def test_logo_and_avatar_are_bounded_webp(self):
        for kind, bounds in [('logo', (1200, 400)), ('avatar', (256, 256))]:
            uploaded = image_upload()
            result = optimize_image(uploaded, kind=kind)
            with Image.open(result) as image:
                self.assertEqual(image.format, 'WEBP')
                self.assertLessEqual(image.width, bounds[0])
                self.assertLessEqual(image.height, bounds[1])
            self.assertLess(result.size, uploaded.size)

    def test_alpha_and_orientation_survive_optimization(self):
        with Image.open(optimize_image(image_upload('RGBA'), kind='logo')) as image:
            self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(image.getpixel((0, 0))[3], 80)
        exif = Image.Exif()
        exif[274] = 6
        with Image.open(optimize_image(image_upload(size=(400, 200), image_format='JPEG', exif=exif), kind='logo')) as image:
            self.assertEqual(image.size, (200, 400))
            self.assertFalse(image.getexif())

    def test_corrupt_image_is_rejected(self):
        with self.assertRaises(forms.ValidationError):
            optimize_image(SimpleUploadedFile('bad.png', b'not a picture'), kind='logo')

    def test_damage_photos_are_bounded_and_invalid_uploads_are_rejected(self):
        from apps.customers.forms import CarDamageForm
        upload = image_upload(size=(3000, 1000))
        form = CarDamageForm()
        form.cleaned_data = {"photos": [upload]}
        photo = form.clean_photos()[0]
        self.assertEqual(photo.original_name, upload.name)
        with Image.open(photo) as decoded:
            self.assertEqual(decoded.format, "WEBP")
            self.assertLessEqual(decoded.width, 2560)
        form.cleaned_data = {"photos": [SimpleUploadedFile("bad.png", b"bad", content_type="image/png")]}
        with self.assertRaises(forms.ValidationError):
            form.clean_photos()

    def test_saved_file_is_not_reencoded(self):
        class SavedFile:
            name = 'stored.png'
        saved = SavedFile()
        self.assertIs(optimize_image(saved, kind='logo'), saved)


class HotPathTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name='Performance', slug='perf')
        self.owner = get_user_model().objects.create_user('perf@example.com', 'safe-test-password')
        Membership.objects.create(tenant=self.tenant, user=self.owner, role='owner')
        self.location = ensure_primary_location(self.tenant)
        self.employee = Employee.objects.create(tenant=self.tenant, location=self.location, name='Admin',
            salary_mode=Employee.SalaryMode.FIXED_PLUS_APPOINTMENT_PERCENT, salary_percent=10)
        self.service = Service.objects.create(tenant=self.tenant, location=self.location, name='Wash', price=1000, duration_minutes=60)
        self.today = timezone.localdate()
        EmployeeShift.objects.create(tenant=self.tenant, location=self.location, employee=self.employee,
            date=self.today)
        self.client.force_login(self.owner)
        self.host = {'HTTP_HOST': 'perf.localhost'}

    def add_appointments(self, count):
        offset = Client.objects.filter(tenant=self.tenant).count()
        for index in range(count):
            client = Client.objects.create(tenant=self.tenant, name=f'Client {index}', phone=f'+7900{offset + index:07d}')
            row = Appointment.objects.create(tenant=self.tenant, location=self.location, client=client,
                employee=self.employee, start_at=timezone.now(), end_at=timezone.now()+timedelta(hours=1),
                status='completed', payment_status='paid', source='website', subtotal=1000, total=1000)
            AppointmentService.objects.create(tenant=self.tenant, appointment=row, service=self.service,
                name_snapshot='Wash', price_snapshot=1000, duration_minutes_snapshot=60)
            ClientRating.objects.create(tenant=self.tenant, appointment=row, score=5, submitted_by_client=True)
            Complaint.objects.create(tenant=self.tenant, location=self.location, appointment=row,
                assigned_to=self.employee, description='Test', created_by=self.owner)

    def test_warm_poll_does_not_write_or_lock(self):
        self.client.get('/notifications/feed/', **self.host)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/notifications/feed/', **self.host)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 14)
        self.assertFalse(any(q['sql'].startswith(('UPDATE', 'INSERT', 'DELETE')) or 'FOR UPDATE' in q['sql'] for q in queries))

    def test_legacy_location_repair_is_preserved(self):
        employee = Employee.objects.create(tenant=self.tenant, name='Legacy')
        backfill_primary_location_data(self.tenant, self.location)
        employee.refresh_from_db()
        self.assertEqual(employee.location, self.location)
        with CaptureQueriesContext(connection) as queries:
            backfill_primary_location_data(self.tenant, self.location)
        self.assertEqual(len(queries), 1)
        self.assertFalse(any('UPDATE ' in q['sql'] for q in queries))

    def test_report_queries_do_not_grow_with_appointments_and_employees(self):
        self.add_appointments(1)
        paths = ['/analytics/?period=month', f'/payroll/?start={self.today}&end={self.today}']
        before = {}
        for path in paths:
            self.client.get(path, **self.host)
            with CaptureQueriesContext(connection) as queries:
                self.client.get(path, **self.host)
            before[path] = len(queries)
        self.add_appointments(20)
        for i in range(10):
            Employee.objects.create(tenant=self.tenant, location=self.location, name=f'Extra {i}')
        for path in paths:
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(path, **self.host)
            self.assertEqual(response.status_code, 200)
            self.assertLessEqual(len(queries), before[path] + 1, path)

    def test_conditional_media_still_checks_membership(self):
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            self.owner.avatar.save('avatar.png', image_upload(size=(32, 32)))
            response = self.client.get(self.owner.avatar.url, **self.host)
            self.assertEqual(response.status_code, 200)
            b''.join(response.streaming_content)
            etag = response['ETag']
            response = self.client.get(self.owner.avatar.url, HTTP_IF_NONE_MATCH=etag, **self.host)
            self.assertEqual(response.status_code, 304)
            Membership.objects.filter(tenant=self.tenant, user=self.owner).update(status='disabled')
            response = self.client.get(self.owner.avatar.url, HTTP_IF_NONE_MATCH=etag, **self.host)
            self.assertEqual(response.status_code, 403)

    def test_existing_image_optimization_is_idempotent_and_retains_original(self):
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            self.tenant.logo.save('original.png', image_upload())
            original = self.tenant.logo.name
            output = StringIO()
            call_command('optimize_identity_images', stdout=output)
            self.tenant.refresh_from_db()
            self.assertEqual(self.tenant.logo.name, original)
            call_command('optimize_identity_images', apply=True, stdout=output)
            self.tenant.refresh_from_db()
            optimized = self.tenant.logo.name
            self.assertNotEqual(optimized, original)
            self.assertTrue(self.tenant.logo.storage.exists(original))
            call_command('optimize_identity_images', apply=True, stdout=output)
            self.tenant.refresh_from_db()
            self.assertEqual(self.tenant.logo.name, optimized)

class MediaOptimizationTests(TestCase):
    def test_existing_damage_photos_keep_originals_and_are_idempotent(self):
        from apps.customers.models import Car, CarDamage, CarDamagePhoto
        from datetime import date
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            tenant = Tenant.objects.create(name='Photo test', slug='photo-test')
            client = Client.objects.create(tenant=tenant, name='Client', phone='+79990000001')
            car = Car.objects.create(tenant=tenant, client=client, make='BMW', model='X5')
            damage = CarDamage.objects.create(tenant=tenant, car=car, x_percent=40, y_percent=38, discovered_on=date(2026, 10, 5))
            photo = CarDamagePhoto.objects.create(damage=damage, file=image_upload(), original_name='original.png')
            original = photo.file.name
            call_command('optimize_media_images', apply=True, stdout=StringIO())
            photo.refresh_from_db()
            optimized = photo.file.name
            self.assertNotEqual(original, optimized)
            self.assertTrue(photo.file.storage.exists(original))
            self.assertEqual(photo.size, photo.file.size)
            self.assertEqual(photo.original_name, 'original.png')
            call_command('optimize_media_images', apply=True, stdout=StringIO())
            photo.refresh_from_db()
            self.assertEqual(photo.file.name, optimized)
