from datetime import datetime, timedelta, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Service
from apps.customers.models import Car, Client
from apps.operations.models import Appointment, AppointmentAssignee, AppointmentService
from apps.staff.models import Employee, EmployeeShift
from apps.tenants.models import Membership, Tenant


class PayrollDashboardTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="LK", slug="lk-payroll")
        self.owner = get_user_model().objects.create_user("payroll@example.com", "safe-test-password")
        Membership.objects.create(tenant=self.tenant, user=self.owner, role=Membership.Role.OWNER)
        self.employee = Employee.objects.create(tenant=self.tenant, name="Михаил")
        client = Client.objects.create(tenant=self.tenant, name="Иван", last_name="Петров", phone="+79990000000")
        car = Car.objects.create(tenant=self.tenant, client=client, make="BMW", model="X5", plate="А123ВС77")
        service = Service.objects.create(
            tenant=self.tenant, name="Комплекс", price=Decimal("4000"),
            duration_minutes=60, employee_percent=Decimal("25"),
        )
        start = timezone.make_aware(datetime(2026, 9, 10, 10))
        self.appointment = Appointment.objects.create(
            tenant=self.tenant, client=client, car=car, employee=self.employee,
            start_at=start, end_at=start + timedelta(hours=1), subtotal=4000, total=4000,
            status=Appointment.Status.COMPLETED, payment_status=Appointment.PaymentStatus.PAID,
        )
        AppointmentService.objects.create(
            tenant=self.tenant, appointment=self.appointment, service=service,
            name_snapshot=service.name, price_snapshot=service.price,
            duration_minutes_snapshot=60, employee_percent_snapshot=25,
        )

    def test_salary_uses_service_snapshot_percent(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            "/payroll/?start=2026-09-01&end=2026-09-15", HTTP_HOST="lk-payroll.localhost"
        )
        self.assertEqual(response.status_code, 200)
        row = response.context["rows"][0]
        self.assertEqual(row["cars"], 1)
        self.assertEqual(row["salary"], Decimal("1000"))
        self.assertContains(response, "Комплекс")

    def test_unit_quantity_total_scales_salary_and_completed_count(self):
        item = self.appointment.items.get()
        item.quantity = 3
        item.price_snapshot = Decimal("12000")
        item.duration_minutes_snapshot = 180
        item.save(update_fields=("quantity", "price_snapshot", "duration_minutes_snapshot"))
        self.appointment.subtotal = Decimal("12000")
        self.appointment.total = Decimal("12000")
        self.appointment.save(update_fields=("subtotal", "total"))
        self.client.force_login(self.owner)
        response = self.client.get(
            "/payroll/?start=2026-09-01&end=2026-09-15", HTTP_HOST="lk-payroll.localhost"
        )
        row = response.context["rows"][0]
        self.assertEqual(row["salary"], Decimal("3000"))
        self.assertEqual(row["services"][0]["count"], 3)

    def test_legacy_zero_snapshot_uses_current_service_percent(self):
        item = self.appointment.items.get()
        item.employee_percent_snapshot = 0
        item.save(update_fields=("employee_percent_snapshot",))
        self.client.force_login(self.owner)
        response = self.client.get(
            "/payroll/?start=2026-09-01&end=2026-09-15", HTTP_HOST="lk-payroll.localhost"
        )
        self.assertEqual(response.context["rows"][0]["salary"], Decimal("1000"))
        self.assertContains(response, "25%")

    def test_fixed_plus_percent_salary_uses_subtotal_before_discount(self):
        self.employee.salary_mode = Employee.SalaryMode.FIXED_PLUS_PERCENT
        self.employee.salary_percent = Decimal("12.5")
        self.appointment.subtotal = Decimal("4000")
        self.appointment.total = Decimal("2000")
        self.appointment.save(update_fields=("subtotal", "total"))
        self.employee.save()
        self.client.force_login(self.owner)
        response = self.client.get(
            "/payroll/?start=2026-09-01&end=2026-09-15", HTTP_HOST="lk-payroll.localhost"
        )
        self.assertEqual(response.context["rows"][0]["salary"], Decimal("500.00"))

    def test_fixed_monthly_salary_is_prorated(self):
        self.employee.salary_mode = Employee.SalaryMode.FIXED_MONTHLY
        self.employee.fixed_monthly_salary = Decimal("30000")
        self.employee.save()
        self.client.force_login(self.owner)
        response = self.client.get(
            "/payroll/?start=2026-09-01&end=2026-09-15", HTTP_HOST="lk-payroll.localhost"
        )
        self.assertEqual(response.context["rows"][0]["salary"], Decimal("15000.00"))

    def test_salary_is_distributed_between_assignees(self):
        second = Employee.objects.create(tenant=self.tenant, name="Анна")
        AppointmentAssignee.objects.create(
            tenant=self.tenant, appointment=self.appointment, employee=self.employee,
            salary_share_percent=60, is_primary=True,
        )
        AppointmentAssignee.objects.create(
            tenant=self.tenant, appointment=self.appointment, employee=second,
            salary_share_percent=40,
        )
        self.client.force_login(self.owner)
        response = self.client.get(
            "/payroll/?start=2026-09-01&end=2026-09-15", HTTP_HOST="lk-payroll.localhost"
        )
        salaries = {row["employee"].name: row["salary"] for row in response.context["rows"]}
        self.assertEqual(salaries, {"Анна": Decimal("400.00"), "Михаил": Decimal("600.00")})

    def test_appointment_scheme_pays_creator_and_personal_service_work(self):
        from apps.payroll.services import appointment_commission_rows, appointment_salary
        self.employee.user = self.owner
        self.employee.salary_mode = Employee.SalaryMode.FIXED_PLUS_APPOINTMENT_PERCENT
        self.employee.salary_percent = Decimal("10")
        self.employee.save()
        EmployeeShift.objects.create(
            tenant=self.tenant, employee=self.employee, date=self.appointment.start_at.date(),
            start_time=time(9), end_time=time(18),
        )
        self.appointment.created_by_employee = self.employee
        self.appointment.save(update_fields=("created_by_employee",))
        commissions = appointment_commission_rows(self.appointment)
        self.assertEqual(commissions[0][1], Decimal("400.00"))
        self.assertEqual(appointment_salary(self.appointment, self.employee), Decimal("1000.00"))

    def test_external_appointment_commission_is_split_between_shift_admins(self):
        from apps.payroll.services import appointment_commission_rows, commission_shifts
        self.employee.salary_mode = Employee.SalaryMode.FIXED_PLUS_APPOINTMENT_PERCENT
        self.employee.salary_percent = Decimal("10")
        self.employee.save()
        second = Employee.objects.create(
            tenant=self.tenant, name="Анна",
            salary_mode=Employee.SalaryMode.FIXED_PLUS_APPOINTMENT_PERCENT,
            salary_percent=Decimal("10"),
        )
        for employee in (self.employee, second):
            EmployeeShift.objects.create(
                tenant=self.tenant, employee=employee, date=self.appointment.start_at.date(),
                start_time=time(9), end_time=time(18),
            )
        self.appointment.source = "website"
        self.appointment.save(update_fields=("source",))
        rows = appointment_commission_rows(self.appointment)
        self.assertEqual({employee.name: amount for employee, amount, _ in rows}, {
            "Михаил": Decimal("200.00"), "Анна": Decimal("200.00"),
        })
        day = self.appointment.start_at.date()
        batch = commission_shifts(self.tenant, day, day)
        with self.assertNumQueries(0):
            batched_rows = appointment_commission_rows(self.appointment, shifts=batch)
        self.assertEqual({employee.pk: (amount, share) for employee, amount, share in rows},
                         {employee.pk: (amount, share) for employee, amount, share in batched_rows})

    def test_authored_appointment_is_not_split_between_shift_admins(self):
        from apps.payroll.services import appointment_commission_rows
        self.employee.salary_mode = Employee.SalaryMode.FIXED_PLUS_APPOINTMENT_PERCENT
        self.employee.salary_percent = Decimal("10")
        self.employee.save()
        second = Employee.objects.create(
            tenant=self.tenant, name="Анна",
            salary_mode=Employee.SalaryMode.FIXED_PLUS_APPOINTMENT_PERCENT,
            salary_percent=Decimal("10"),
        )
        for employee in (self.employee, second):
            EmployeeShift.objects.create(
                tenant=self.tenant, employee=employee, date=self.appointment.start_at.date(),
                start_time=time(9), end_time=time(18),
            )
        self.appointment.created_by_employee = self.employee
        self.appointment.save(update_fields=("created_by_employee",))
        rows = appointment_commission_rows(self.appointment)
        self.assertEqual([(employee.name, amount) for employee, amount, _ in rows], [
            ("Михаил", Decimal("400.00")),
        ])

    def test_appointment_commission_requires_completed_paid_appointment(self):
        from apps.payroll.services import appointment_commission_rows

        self.employee.salary_mode = Employee.SalaryMode.FIXED_PLUS_APPOINTMENT_PERCENT
        self.employee.salary_percent = Decimal("10")
        self.employee.save()
        EmployeeShift.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            date=self.appointment.start_at.date(),
            start_time=time(9),
            end_time=time(18),
        )
        self.appointment.source = "website"
        self.appointment.payment_status = Appointment.PaymentStatus.UNPAID
        self.appointment.save(update_fields=("source", "payment_status"))

        self.assertEqual(appointment_commission_rows(self.appointment), [])
