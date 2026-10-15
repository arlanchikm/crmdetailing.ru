from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.customers.models import Car, Client
from apps.catalog.models import Service, WorkPost
from apps.operations.models import (
    Appointment, AppointmentService, ClientRating, Complaint,
)
from apps.staff.models import Employee
from apps.tenants.models import Membership, Tenant

from .models import Expense


class AnalyticsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="LK", slug="lk-analytics")
        self.owner = get_user_model().objects.create_user("analytics@example.com", "safe-test-password")
        Membership.objects.create(tenant=self.tenant, user=self.owner, role=Membership.Role.OWNER)
        client = Client.objects.create(tenant=self.tenant, name="Клиент", phone="+79990000001")
        car = Car.objects.create(tenant=self.tenant, client=client, make="Audi", model="A6")
        employee = Employee.objects.create(tenant=self.tenant, name="Мастер")
        self.employee = employee
        start = timezone.now()
        self.appointments = []
        for payment in (Appointment.PaymentStatus.PAID, Appointment.PaymentStatus.UNPAID):
            self.appointments.append(Appointment.objects.create(
                tenant=self.tenant, client=client, car=car, employee=employee,
                start_at=start, end_at=start + timedelta(hours=1),
                status=Appointment.Status.COMPLETED, payment_status=payment, total=Decimal("3000"),
            ))
        Expense.objects.create(tenant=self.tenant, date=timezone.localdate(), category=Expense.Category.RENT, amount=1000)

    def test_revenue_uses_only_paid_completed_appointments(self):
        self.client.force_login(self.owner)
        response = self.client.get("/analytics/?period=day", HTTP_HOST="lk-analytics.localhost")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["revenue"], Decimal("3000"))
        self.assertEqual(response.context["profit"], Decimal("2000"))
        self.assertEqual(len(response.context["period_appointments"]), 2)
        self.assertContains(response, "История")
        self.assertContains(response, "Не оплачено")
        self.assertContains(response, 'data-label="Оплата"')
        self.assertContains(response, 'data-label="Сумма"')
        self.assertContains(response, 'class="table-card embedded-table revenue-history-table"')

    def test_custom_period_uses_inclusive_dates(self):
        self.client.force_login(self.owner)
        today = timezone.localdate()
        response = self.client.get(
            "/analytics/",
            {"date_from": today.isoformat(), "date_to": today.isoformat()},
            HTTP_HOST="lk-analytics.localhost",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["period"], "custom")
        self.assertEqual(response.context["period_start"], today)
        self.assertEqual(response.context["period_end"], today)
        self.assertEqual(response.context["revenue"], Decimal("3000"))

    def test_revenue_trend_keeps_days_without_revenue(self):
        self.client.force_login(self.owner)
        today = timezone.localdate()
        response = self.client.get(
            "/analytics/",
            {
                "date_from": (today - timedelta(days=1)).isoformat(),
                "date_to": (today + timedelta(days=1)).isoformat(),
            },
            HTTP_HOST="lk-analytics.localhost",
        )
        trend = response.context["revenue_trend"]
        self.assertEqual(len(trend), 3)
        self.assertEqual([row["date"] for row in trend], [
            today - timedelta(days=1), today, today + timedelta(days=1),
        ])
        self.assertEqual([row["amount"] for row in trend], [
            Decimal("0"), Decimal("3000"), Decimal("0"),
        ])

    def test_inventory_balances_are_not_shown_in_analytics(self):
        self.client.force_login(self.owner)
        response = self.client.get("/analytics/?period=day", HTTP_HOST="lk-analytics.localhost")
        self.assertNotContains(response, "Остатки материалов")
        self.assertNotIn("inventory_balances", response.context)

    def test_invalid_custom_period_falls_back_to_preset(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            "/analytics/",
            {"period": "week", "date_from": "2026-10-10", "date_to": "2026-10-01"},
            HTTP_HOST="lk-analytics.localhost",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["period"], "week")
        self.assertTrue(response.context["period_error"])

    def test_automatic_payroll_is_included_in_costs(self):
        self.employee.salary_mode = Employee.SalaryMode.FIXED_PLUS_PERCENT
        self.employee.salary_percent = 10
        self.employee.save()
        self.client.force_login(self.owner)
        response = self.client.get("/analytics/?period=day", HTTP_HOST="lk-analytics.localhost")
        self.assertEqual(response.context["payroll_cost"], Decimal("300.00"))
        self.assertEqual(response.context["profit"], Decimal("1700.00"))

    def test_expanded_analytics_reports_services_quality_and_posts(self):
        service = Service.objects.create(
            tenant=self.tenant, name="Комплекс", price=3000, duration_minutes=60,
        )
        post = WorkPost.objects.create(tenant=self.tenant, name="Пост 1")
        appointment = self.appointments[0]
        appointment.post = post
        appointment.subtotal = 3000
        appointment.save(update_fields=("post", "subtotal"))
        AppointmentService.objects.create(
            tenant=self.tenant, appointment=appointment, service=service,
            name_snapshot=service.name, price_snapshot=3000,
            duration_minutes_snapshot=60, consumables_snapshot=[],
        )
        ClientRating.objects.create(
            tenant=self.tenant, appointment=appointment, score=5, created_by=self.owner,
        )
        Complaint.objects.create(
            tenant=self.tenant, appointment=appointment, service=service,
            reason=Complaint.Reason.QUALITY, description="Проверка", is_rework=True,
            financial_loss=200, created_by=self.owner,
        )
        self.client.force_login(self.owner)
        response = self.client.get("/analytics/?period=day", HTTP_HOST="lk-analytics.localhost")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["average_check"], Decimal("3000"))
        self.assertEqual(response.context["complaint_count"], 1)
        self.assertEqual(response.context["rework_count"], 1)
        self.assertEqual(response.context["service_metrics"][0]["name"], "Комплекс")
        self.assertEqual(response.context["post_metrics"][0]["post"], post)
