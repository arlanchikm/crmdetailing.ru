from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.catalog.models import Service
from apps.customers.models import Client, VehicleClass
from apps.operations.models import Appointment, AppointmentAssignee, ClientRating
from apps.staff.models import Employee, EmployeeService
from apps.tenants.models import Membership, Tenant
from apps.tenants.plans import ensure_primary_location
from .booking import booking_catalog
from .models import IntegrationChannel


class OptimizationRegressionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name='Audit', slug='audit')
        self.owner = get_user_model().objects.create_user('audit@example.com', 'test-password')
        Membership.objects.create(tenant=self.tenant, user=self.owner, role='owner')
        self.location = ensure_primary_location(self.tenant)
        self.employee = Employee.objects.create(tenant=self.tenant, location=self.location, name='Master')
        self.service = Service.objects.create(tenant=self.tenant, location=self.location,
            name='Wash', price=100, duration_minutes=30)
        EmployeeService.objects.create(tenant=self.tenant, employee=self.employee, service=self.service)
        self.channel = IntegrationChannel.objects.create(tenant=self.tenant, kind='website', enabled=True)
        self.client.force_login(self.owner)
        self.host = {'HTTP_HOST': 'audit.localhost'}

    def test_invalid_notification_cursor_does_not_crash(self):
        for cursor in ['2026-99-99T00:00:00', '9999-12-31T23:59:59-23:59', 'bad']:
            with self.subTest(cursor=cursor):
                response = self.client.get('/notifications/feed/', {'after': cursor}, **self.host)
                self.assertEqual(response.status_code, 200)

    def test_booking_ratings_count_each_visit_once_for_each_assignee(self):
        customer = Client.objects.create(tenant=self.tenant, name='Client', phone='+79991112233')
        assistant = Employee.objects.create(tenant=self.tenant, location=self.location, name='Assistant')
        EmployeeService.objects.create(tenant=self.tenant, employee=assistant, service=self.service)
        for score, shared in [(5, True), (1, False)]:
            row = Appointment.objects.create(tenant=self.tenant, location=self.location,
                client=customer, employee=self.employee, start_at=timezone.now(),
                end_at=timezone.now()+timedelta(minutes=30), status='completed')
            AppointmentAssignee.objects.create(tenant=self.tenant, appointment=row,
                employee=self.employee, salary_share_percent=Decimal('50') if shared else Decimal('100'), is_primary=True)
            if shared:
                AppointmentAssignee.objects.create(tenant=self.tenant, appointment=row,
                    employee=assistant, salary_share_percent=Decimal('50'))
            ClientRating.objects.create(tenant=self.tenant, appointment=row, score=score, submitted_by_client=True)
        employees = {row['id']: row for row in booking_catalog(self.channel)[3]['employees']}
        self.assertEqual(employees[str(self.employee.pk)]['rating'], 3.0)
        self.assertEqual(employees[str(self.employee.pk)]['rating_count'], 2)
        self.assertEqual(employees[str(assistant.pk)]['rating'], 5.0)

    def test_booking_catalog_queries_are_constant_with_team_size(self):
        booking_catalog(self.channel)
        with CaptureQueriesContext(connection) as queries:
            booking_catalog(self.channel)
        baseline = len(queries)
        for i in range(12):
            employee = Employee.objects.create(tenant=self.tenant, location=self.location, name=f'Extra {i}')
            EmployeeService.objects.create(tenant=self.tenant, employee=employee, service=self.service)
        with CaptureQueriesContext(connection) as queries:
            booking_catalog(self.channel)
        self.assertLessEqual(len(queries), baseline + 1)

    def test_vehicle_class_page_queries_are_constant(self):
        from django.urls import reverse
        url = reverse('vehicle-class-list')
        self.client.get(url, **self.host)
        with CaptureQueriesContext(connection) as queries:
            self.client.get(url, **self.host)
        baseline = len(queries)
        for i in range(12):
            VehicleClass.objects.create(tenant=self.tenant, name=f'Extra {i}')
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url, **self.host)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), baseline + 1)

    def test_maximum_date_does_not_crash_analytics_or_booking(self):
        response = self.client.get('/analytics/', {'date_from': '9999-12-31', 'date_to': '9999-12-31'}, **self.host)
        self.assertEqual(response.status_code, 200)
        response = self.client.get(f'/booking/{self.channel.public_token}/availability/',
            {'date': '9999-12-31', 'services': [str(self.service.pk)]}, **self.host)
        self.assertEqual(response.status_code, 400)

    def test_notification_refresh_continues_after_one_company_fails(self):
        from unittest.mock import patch
        from apps.core.tasks import refresh_notifications_background
        Tenant.objects.create(name='Other', slug='other-audit')
        with patch('apps.core.services.refresh_operational_notifications', side_effect=[RuntimeError('test failure'), None]) as refresh:
            with self.assertLogs('apps.core.tasks', level='ERROR'):
                refresh_notifications_background()
        self.assertEqual(refresh.call_count, 2)

    def test_extreme_dates_do_not_crash_calendar_and_staff_schedule(self):
        from django.urls import reverse
        for name, key in [('appointment-calendar', 'date'), ('staff-schedule', 'week')]:
            for value in ['0001-01-01', '9999-12-31']:
                with self.subTest(page=name, value=value):
                    response = self.client.get(reverse(name), {key: value}, **self.host)
                    self.assertEqual(response.status_code, 200)

    def test_monthly_salary_supports_last_representable_month(self):
        from datetime import date
        from apps.payroll.services import fixed_salary_for_period
        self.employee.salary_mode = Employee.SalaryMode.FIXED_MONTHLY
        self.employee.fixed_monthly_salary = Decimal('31000')
        self.assertEqual(fixed_salary_for_period(self.employee, date(9999, 12, 1), date.max), Decimal('31000.00'))
