from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Service, ServiceVehicleClassPrice, WorkPost
from apps.customers.models import Car, Client, ensure_default_vehicle_classes
from apps.staff.models import CompanyRole, Employee, EmployeeService, EmployeeShift
from apps.tenants.models import Membership, Tenant

from .models import (
    Appointment, AppointmentAssignee, CashCollection, CashShift,
    ClientRating, Complaint, PaymentSettings, PaymentTransaction,
)
from .financial import add_payment_transaction
from .services import create_appointment


class OperationWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="LK", slug="lk")
        cls.other_tenant = Tenant.objects.create(name="Other", slug="other")
        User = get_user_model()
        cls.owner = User.objects.create_user("owner@example.com", "safe-test-password")
        cls.master = User.objects.create_user("master@example.com", "safe-test-password")
        Membership.objects.create(tenant=cls.tenant, user=cls.owner, role=Membership.Role.OWNER)
        Membership.objects.create(tenant=cls.tenant, user=cls.master, role=Membership.Role.MASTER)

        cls.client_record = Client.objects.create(
            tenant=cls.tenant, name="Иван Петров", phone="+7 999 123-45-67"
        )
        cls.car = Car.objects.create(
            tenant=cls.tenant,
            client=cls.client_record,
            make="BMW",
            model="X5",
            plate="А123ВС77",
        )
        cls.post = WorkPost.objects.create(tenant=cls.tenant, name="Пост №1")
        cls.employee = Employee.objects.create(
            tenant=cls.tenant, name="Алексей", position="Мастер", color=Employee.Color.BLUE
        )
        cls.second_employee = Employee.objects.create(
            tenant=cls.tenant, name="Мария", position="Администратор", color=Employee.Color.PURPLE
        )
        for employee in (cls.employee, cls.second_employee):
            EmployeeShift.objects.create(
                tenant=cls.tenant, employee=employee, date=datetime(2026, 9, 15).date(),
                start_time=time(9), end_time=time(20),
            )
        cls.service = Service.objects.create(
            tenant=cls.tenant,
            name="Комплексная мойка",
            price=Decimal("5500.00"),
            duration_minutes=90,
        )
        cls.long_service = Service.objects.create(
            tenant=cls.tenant,
            name="Полировка",
            price=Decimal("7000.00"),
            duration_minutes=60,
        )

        cls.other_client = Client.objects.create(
            tenant=cls.other_tenant, name="Чужой клиент", phone="+7 900 000-00-00"
        )
        cls.other_car = Car.objects.create(
            tenant=cls.other_tenant,
            client=cls.other_client,
            make="Audi",
            model="A6",
        )
        cls.other_post = WorkPost.objects.create(tenant=cls.other_tenant, name="Чужой пост")
        cls.other_employee = Employee.objects.create(
            tenant=cls.other_tenant, name="Чужой сотрудник"
        )
        cls.other_service = Service.objects.create(
            tenant=cls.other_tenant,
            name="Чужая услуга",
            price=Decimal("1000.00"),
            duration_minutes=30,
        )

    @staticmethod
    def start_time(hour=10):
        return timezone.make_aware(datetime(2026, 9, 15, hour, 0))

    def create_record(self, *, start=None, employee=None, post=None, status=None):
        start = start or self.start_time()
        appointment = create_appointment(
            tenant=self.tenant,
            cleaned_data={
                "client": self.client_record,
                "car": self.car,
                "employee": employee or self.employee,
                "post": self.post if post is None else post,
                "services": [self.service],
                "start_at": start,
                "end_at": start + timedelta(minutes=90),
                "comment": "",
            },
        )
        if status:
            appointment.status = status
            appointment.save(update_fields=("status",))
        return appointment

    def test_services_calculate_end_time_and_total(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/appointments/new/",
            {
                "client_mode": "existing",
                "client": self.client_record.id,
                "car": self.car.id,
                "employee": self.employee.id,
                "post": self.post.id,
                "services": [self.service.id, self.long_service.id],
                "start_at": "2026-09-15T10:00",
                "comment": "Первый визит",
            },
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        appointment = Appointment.objects.get(tenant=self.tenant)
        self.assertEqual(timezone.localtime(appointment.end_at).hour, 12)
        self.assertEqual(timezone.localtime(appointment.end_at).minute, 30)
        self.assertEqual(appointment.total, Decimal("12500.00"))
        self.assertEqual(appointment.items.count(), 2)

    def test_unit_service_quantity_is_saved_once_and_recalculates_totals(self):
        unit_service = Service.objects.create(
            tenant=self.tenant, name="Химчистка сидения", price=Decimal("1400"),
            duration_minutes=20, calculation_type=Service.CalculationType.UNIT,
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            "/appointments/new/",
            {
                "client": self.client_record.id, "car": self.car.id,
                "employee": self.employee.id, "services": [unit_service.id],
                f"service_quantity_{unit_service.id}": "3",
                "start_at": "2026-09-15T10:00", "comment": "",
            },
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        appointment = Appointment.objects.latest("created_at")
        item = appointment.items.get()
        self.assertEqual(appointment.items.count(), 1)
        self.assertEqual(item.quantity, 3)
        self.assertEqual(item.price_snapshot, Decimal("4200"))
        self.assertEqual(item.duration_minutes_snapshot, 60)
        self.assertEqual(appointment.total, Decimal("4200"))
        self.assertEqual(timezone.localtime(appointment.end_at).strftime("%H:%M"), "11:00")

        response = self.client.post(
            f"/appointments/{appointment.id}/edit/",
            {
                "client": self.client_record.id, "car": self.car.id,
                "employee": self.employee.id, "employees": [self.employee.id],
                "services": [unit_service.id],
                f"service_quantity_{unit_service.id}": "2",
                "start_at": "2026-09-15T10:00", "comment": "",
            },
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        appointment.refresh_from_db()
        item = appointment.items.get()
        self.assertEqual(appointment.items.count(), 1)
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.price_snapshot, Decimal("2800"))
        self.assertEqual(item.duration_minutes_snapshot, 40)
        self.assertEqual(appointment.total, Decimal("2800"))

    def test_service_calculation_type_ignores_posted_quantity(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/appointments/new/",
            {
                "client": self.client_record.id, "car": self.car.id,
                "employee": self.employee.id, "services": [self.service.id],
                f"service_quantity_{self.service.id}": "5",
                "start_at": "2026-09-15T12:00", "comment": "",
            },
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        item = Appointment.objects.latest("created_at").items.get()
        self.assertEqual(item.quantity, 1)
        self.assertEqual(item.price_snapshot, Decimal("5500"))

    def test_service_vehicle_class_price_can_override_car_class(self):
        classes = ensure_default_vehicle_classes(self.tenant)
        self.car.vehicle_class = classes["sedan"]
        self.car.save(update_fields=("vehicle_class",))
        ServiceVehicleClassPrice.objects.create(
            tenant=self.tenant, service=self.service,
            vehicle_class=classes["sedan"], price=Decimal("2200"),
        )
        ServiceVehicleClassPrice.objects.create(
            tenant=self.tenant, service=self.service,
            vehicle_class=classes["suv"], price=Decimal("3000"),
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            "/appointments/new/",
            {
                "client": self.client_record.id, "car": self.car.id,
                "employees": [self.employee.id], "services": [self.service.id],
                "car_vehicle_class": classes["sedan"].id,
                f"service_class_{self.service.id}": classes["suv"].id,
                "start_at": "2026-09-15T10:00", "comment": "",
            }, HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        appointment = Appointment.objects.latest("created_at")
        item = appointment.items.get()
        self.assertEqual(appointment.subtotal, Decimal("3000"))
        self.assertEqual(item.vehicle_class, classes["suv"])
        self.assertEqual(item.vehicle_class_name_snapshot, "Внедорожник")

    def test_end_time_can_be_overridden(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/appointments/new/",
            {
                "client": self.client_record.id, "car": self.car.id,
                "employee": self.employee.id, "post": "", "services": [self.service.id],
                "start_at": "2026-09-15T10:00", "end_at": "2026-09-15T12:15",
            }, HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(timezone.localtime(Appointment.objects.get().end_at).strftime("%H:%M"), "12:15")

    def test_service_cards_are_visible_in_form(self):
        self.client.force_login(self.owner)
        response = self.client.get("/appointments/new/", HTTP_HOST="lk.localhost")
        self.assertContains(response, "service-select-card")
        self.assertContains(response, "Комплексная мойка")
        self.assertContains(response, "data-duration=\"90\"")
        self.assertNotContains(response, "Выбрать из базы")
        self.assertNotContains(response, "Добавить вручную")
        self.assertContains(response, "client-suggestion-data")

    def test_employee_without_selected_service_cannot_be_assigned(self):
        EmployeeService.objects.create(
            tenant=self.tenant, employee=self.employee, service=self.long_service,
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            "/appointments/new/?modal=1",
            {
                "client": self.client_record.id,
                "car": self.car.id,
                "employees": [self.employee.id],
                "services": [self.service.id],
                "start_at": "2026-09-15T10:00",
            },
            HTTP_HOST="lk.localhost",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 422)
        self.assertContains(
            response,
            "Исполнитель не может выполнять выбранные услуги",
            status_code=422,
        )
        self.assertFalse(Appointment.objects.exists())

    def test_employee_capabilities_are_exposed_to_appointment_picker(self):
        EmployeeService.objects.create(
            tenant=self.tenant, employee=self.employee, service=self.long_service,
        )
        self.client.force_login(self.owner)
        response = self.client.get("/appointments/new/", HTTP_HOST="lk.localhost")
        self.assertContains(response, 'data-capability-mode="1"')
        self.assertContains(response, f'data-service-ids="{self.long_service.id}"')
        self.assertContains(response, "Не выполняет выбранные услуги")

    def test_typed_exact_client_is_reused_without_duplicate(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/appointments/new/",
            {
                "client_first_name": "Иван", "client_last_name": "Петров",
                "client_phone": self.client_record.phone, "car_plate": self.car.plate,
                "car_make": self.car.make, "car_model": self.car.model,
                "employee": self.employee.id, "post": "", "services": [self.service.id],
                "start_at": "2026-09-15T17:00",
            }, HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Client.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(Car.objects.filter(tenant=self.tenant).count(), 1)

    def test_overlapping_employee_appointments_are_allowed(self):
        self.create_record()
        self.client.force_login(self.owner)
        response = self.client.post(
            "/appointments/new/",
            {
                "client_mode": "existing",
                "client": self.client_record.id,
                "car": self.car.id,
                "employee": self.employee.id,
                "post": "",
                "services": [self.service.id],
                "start_at": "2026-09-15T10:30",
            },
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Appointment.objects.count(), 2)

    def test_other_tenant_objects_cannot_be_selected(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/appointments/new/",
            {
                "client_mode": "existing",
                "client": self.other_client.id,
                "car": self.other_car.id,
                "employee": self.other_employee.id,
                "post": self.other_post.id,
                "services": [self.other_service.id],
                "start_at": "2026-09-15T10:00",
            },
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Appointment.objects.exists())

    def test_manual_client_and_car_are_created_with_appointment(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/appointments/new/",
            {
                "client_first_name": "Новый",
                "client_last_name": "Клиент",
                "client_phone": "+7 955 555-55-55",
                "car_description": "Kia K5",
                "car_plate": "В555ВВ77",
                "employee": self.employee.id,
                "post": self.post.id,
                "services": [self.service.id],
                "start_at": "2026-09-15T13:00",
            },
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        appointment = Appointment.objects.get(client__name="Новый")
        self.assertEqual(appointment.client.last_name, "Клиент")
        self.assertEqual(appointment.car.make, "Kia")
        self.assertEqual(appointment.car.model, "K5")

    def test_grid_link_prefills_date_time_and_employee(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            f"/appointments/new/?date=2026-09-15&start=12:00&employee={self.employee.id}",
            HTTP_HOST="lk.localhost",
        )
        initial = response.context["form"].initial
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'for="id_car_description"')
        self.assertContains(response, "Автомобиль")
        self.assertNotContains(response, ">Марка автомобиля<")
        self.assertContains(response, "data-service-list-window")
        self.assertContains(response, "data-has-class-choice")
        content = response.content.decode()
        self.assertLess(content.index("appointment-price-box"), content.index("data-assignee-picker"))
        self.assertLess(content.index("data-assignee-picker"), content.index("appointment-comment-field"))
        suggestion = next(
            item for item in response.context["client_suggestions"]
            if item["car_id"] == str(self.car.id)
        )
        self.assertEqual(suggestion["car_description"], "BMW X5")
        self.assertEqual(suggestion["plate"], "А123ВС77")
        self.assertEqual(initial["start_at"].hour, 12)
        self.assertEqual(initial["employee"], self.employee)

    def test_post_is_optional(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/appointments/new/",
            {
                "client_mode": "existing",
                "client": self.client_record.id,
                "car": self.car.id,
                "employee": self.employee.id,
                "post": "",
                "services": [self.service.id],
                "start_at": "2026-09-15T15:00",
            },
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(Appointment.objects.get().post)

    def test_optional_post_can_be_double_booked(self):
        self.create_record()
        self.client.force_login(self.owner)
        response = self.client.post(
            "/appointments/new/",
            {
                "client_mode": "existing",
                "client": self.client_record.id,
                "car": self.car.id,
                "employee": self.second_employee.id,
                "post": self.post.id,
                "services": [self.service.id],
                "start_at": "2026-09-15T10:30",
            },
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Appointment.objects.count(), 2)

    def test_master_cannot_create_appointment(self):
        self.client.force_login(self.master)
        response = self.client.get("/appointments/new/", HTTP_HOST="lk.localhost")
        self.assertEqual(response.status_code, 403)

    def test_calendar_does_not_show_other_tenant(self):
        other_start = self.start_time()
        Appointment.objects.create(
            tenant=self.other_tenant,
            client=self.other_client,
            car=self.other_car,
            employee=self.other_employee,
            post=self.other_post,
            start_at=other_start,
            end_at=other_start + timedelta(hours=1),
            total=1000,
        )
        self.create_record()
        self.client.force_login(self.owner)
        response = self.client.get(
            "/appointments/?date=2026-09-15", HTTP_HOST="lk.localhost"
        )
        self.assertContains(response, "Иван Петров")
        self.assertNotContains(response, "Чужой клиент")
        self.assertContains(response, f'data-resource="{self.employee.id}"')
        self.assertContains(response, 'data-pixels-per-minute="1.6"')
        self.assertNotContains(response, 'data-pixels-per-minute="1,6"')
        self.assertContains(response, "first-tick")
        self.assertContains(response, "last-tick")
        self.assertContains(response, "08:15")
        self.assertContains(response, "date-popover")
        self.assertContains(response, "09:00–20:00")
        self.assertNotContains(response, "Неделя")

    def test_only_scheduled_employees_are_calendar_columns(self):
        EmployeeShift.objects.filter(employee=self.second_employee).delete()
        self.client.force_login(self.owner)
        response = self.client.get("/appointments/?date=2026-09-15", HTTP_HOST="lk.localhost")
        self.assertContains(response, f'data-resource="{self.employee.id}"')
        self.assertNotContains(response, f'data-resource="{self.second_employee.id}"')

    def test_client_without_car_is_in_suggestions(self):
        Client.objects.create(tenant=self.tenant, name="Пётр", last_name="Безмашинный", phone="+79995550000")
        self.client.force_login(self.owner)
        response = self.client.get("/appointments/new/", HTTP_HOST="lk.localhost")
        suggestion = next(item for item in response.context["client_suggestions"] if item["last_name"] == "Безмашинный")
        self.assertEqual(suggestion["car_id"], "")

    def test_other_service_requires_comment_and_uses_custom_values(self):
        self.client.force_login(self.owner)
        self.client.get("/appointments/new/", HTTP_HOST="lk.localhost")
        other = Service.objects.get(tenant=self.tenant, system_key="other")
        invalid = self.client.post(
            "/appointments/new/",
            {
                "client": self.client_record.id, "car": self.car.id,
                "employee": self.employee.id, "post": "", "services": [other.id],
                "start_at": "2026-09-15T18:00", "other_price": "3200",
                "other_duration_minutes": "45", "comment": "",
            }, HTTP_HOST="lk.localhost",
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "обязательно опишите работу")
        valid = self.client.post(
            "/appointments/new/",
            {
                "client": self.client_record.id, "car": self.car.id,
                "employee": self.employee.id, "post": "", "services": [other.id],
                "start_at": "2026-09-15T18:00", "other_price": "3200",
                "other_duration_minutes": "45", "comment": "Удаление сложного пятна",
            }, HTTP_HOST="lk.localhost",
        )
        self.assertEqual(valid.status_code, 302)
        appointment = Appointment.objects.get(tenant=self.tenant)
        self.assertEqual(appointment.total, Decimal("3200"))
        self.assertEqual(appointment.items.get().duration_minutes_snapshot, 45)

    def test_drag_move_changes_employee_and_time(self):
        appointment = self.create_record()
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/appointments/{appointment.id}/move/",
            {"employee": self.second_employee.id, "date": "2026-09-15", "start": "12:15"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 200)
        appointment.refresh_from_db()
        self.assertEqual(appointment.employee, self.second_employee)
        self.assertEqual(appointment.assignees.get(is_primary=True).employee, self.second_employee)
        self.assertEqual(timezone.localtime(appointment.start_at).strftime("%H:%M"), "12:15")

    def test_drag_move_rejects_employee_without_appointment_service(self):
        appointment = self.create_record()
        EmployeeService.objects.create(
            tenant=self.tenant, employee=self.employee, service=self.service,
        )
        EmployeeService.objects.create(
            tenant=self.tenant, employee=self.second_employee, service=self.long_service,
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/appointments/{appointment.id}/move/",
            {"employee": self.second_employee.id, "date": "2026-09-15", "start": "12:15"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("не может выполнять", response.json()["error"])
        appointment.refresh_from_db()
        self.assertEqual(appointment.employee, self.employee)
        self.assertEqual(timezone.localtime(appointment.start_at).strftime("%H:%M"), "10:00")

    def test_unified_post_calendar_includes_unassigned(self):
        self.create_record()
        start = self.start_time(14)
        Appointment.objects.create(
            tenant=self.tenant, client=self.client_record, car=self.car,
            employee=self.employee, post=None, start_at=start,
            end_at=start + timedelta(hours=1), total=100,
        )
        self.client.force_login(self.owner)
        self.client.post("/appointments/calendar-view/", {"resource_kind":"post", "date":"2026-09-15"}, HTTP_HOST="lk.localhost")
        response = self.client.get("/appointments/?date=2026-09-15", HTTP_HOST="lk.localhost")
        self.assertContains(response, self.post.name)
        self.assertContains(response, "Комплексная мойка")
        self.assertContains(response, "Без поста")

    def test_status_flow_requires_payment_before_completion(self):
        appointment = self.create_record()
        self.client.force_login(self.owner)
        for action, expected in (("start", Appointment.Status.IN_PROGRESS),):
            response = self.client.post(
                f"/appointments/{appointment.id}/transition/{action}/",
                HTTP_HOST="lk.localhost",
            )
            self.assertEqual(response.status_code, 302)
            appointment.refresh_from_db()
            self.assertEqual(appointment.status, expected)
        blocked = self.client.post(
            f"/appointments/{appointment.id}/transition/complete/", HTTP_HOST="lk.localhost"
        )
        self.assertEqual(blocked.status_code, 302)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.IN_PROGRESS)
        modal_blocked = self.client.post(
            f"/appointments/{appointment.id}/transition/complete/?modal=1",
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(modal_blocked.status_code, 422)
        message = "Сначала отметьте запись как оплаченную. Неоплаченную запись завершить нельзя."
        self.assertEqual(modal_blocked.content.decode().count(message), 1)
        appointment.payment_status = Appointment.PaymentStatus.PAID
        appointment.save(update_fields=("payment_status",))
        self.client.post(f"/appointments/{appointment.id}/transition/complete/", HTTP_HOST="lk.localhost")
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.COMPLETED)
        self.assertEqual(len(Appointment.Status.choices), 4)

    def test_no_show_can_return_to_work_and_calendar_shows_payment(self):
        appointment = self.create_record()
        self.client.force_login(self.owner)
        self.client.post(
            f"/appointments/{appointment.id}/transition/no_show/", HTTP_HOST="lk.localhost"
        )
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.NO_SHOW)
        calendar = self.client.get("/appointments/?date=2026-09-15", HTTP_HOST="lk.localhost")
        self.assertContains(calendar, "status-card-no_show")
        self.assertContains(calendar, "Не оплачено")
        self.client.post(
            f"/appointments/{appointment.id}/transition/start/", HTTP_HOST="lk.localhost"
        )
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.IN_PROGRESS)

    def test_client_history_is_visible_from_appointment_and_client(self):
        earlier = self.create_record(start=self.start_time(10), status=Appointment.Status.COMPLETED)
        current = self.create_record(start=self.start_time(14))
        self.client.force_login(self.owner)
        appointment_response = self.client.get(
            f"/appointments/{current.id}/", HTTP_HOST="lk.localhost"
        )
        client_response = self.client.get(
            f"/clients/{self.client_record.id}/", HTTP_HOST="lk.localhost"
        )
        self.assertContains(appointment_response, "История")
        self.assertContains(appointment_response, earlier.start_at.strftime("%d.%m.%Y"))
        self.assertContains(client_response, "История посещений")
        self.assertContains(client_response, "Комплексная мойка", count=1)

    def test_order_routes_are_removed(self):
        self.client.force_login(self.owner)
        response = self.client.get("/orders/", HTTP_HOST="lk.localhost")
        self.assertEqual(response.status_code, 404)

    def test_appointment_opens_as_modal_and_can_be_edited(self):
        appointment = self.create_record()
        self.client.force_login(self.owner)
        modal = self.client.get(
            f"/appointments/{appointment.id}/?modal=1", HTTP_HOST="lk.localhost"
        )
        self.assertEqual(modal.status_code, 200)
        self.assertContains(modal, "Добавить услугу")
        self.assertNotContains(modal, "Дополнительные работы")
        self.assertNotContains(modal, "<!doctype html>")
        response = self.client.post(
            f"/appointments/{appointment.id}/edit/?modal=1",
            {
                "client": self.client_record.id, "car": self.car.id,
                "employee": self.employee.id, "post": "",
                "services": [self.long_service.id],
                "start_at": "2026-09-15T13:00", "comment": "Обновлено",
            }, HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 200)
        appointment.refresh_from_db()
        self.assertEqual(appointment.total, Decimal("7000.00"))
        self.assertEqual(appointment.items.get().name_snapshot, "Полировка")

    def test_payment_transaction_supports_qr_fee_and_partial_payment(self):
        appointment = self.create_record()
        PaymentSettings.objects.create(tenant=self.tenant, qr_fee_percent=Decimal("1.5"))
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/appointments/{appointment.id}/payments/add/",
            {"kind": "payment", "method": "qr", "amount": "1000", "comment": "Аванс"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        appointment.refresh_from_db()
        self.assertEqual(appointment.payment_status, Appointment.PaymentStatus.PARTIAL)
        self.assertEqual(appointment.amount_paid, Decimal("1000.00"))
        self.assertEqual(appointment.payment_fee, Decimal("15.00"))
        self.assertEqual(PaymentTransaction.objects.get().method, Appointment.PaymentMethod.QR)
        refund = self.client.post(
            f"/appointments/{appointment.id}/payments/add/",
            {"kind": "refund", "method": "qr", "amount": "200", "comment": "Частичный возврат"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(refund.status_code, 302)
        appointment.refresh_from_db()
        self.assertEqual(appointment.amount_paid, Decimal("800.00"))

    def test_salary_shares_create_multiple_assignees(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/appointments/new/",
            {"client": self.client_record.id, "car": self.car.id,
             "employee": self.employee.id, "additional_employees": [self.second_employee.id],
             "primary_share_percent": "60", f"employee_share_{self.second_employee.id}": "40",
             "post": "", "services": [self.service.id], "start_at": "2026-09-15T18:00"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        appointment = Appointment.objects.latest("created_at")
        shares = {row.employee_id: row.salary_share_percent for row in appointment.assignees.all()}
        self.assertEqual(shares[self.employee.id], Decimal("60.00"))
        self.assertEqual(shares[self.second_employee.id], Decimal("40.00"))

    def test_new_executor_picker_distributes_participation_equally(self):
        self.service.employee_percent = 30
        self.service.save(update_fields=("employee_percent",))
        self.client.force_login(self.owner)
        response = self.client.post(
            "/appointments/new/",
            {"client": self.client_record.id, "car": self.car.id,
             "employees": [self.employee.id, self.second_employee.id],
             "post": "", "services": [self.service.id], "start_at": "2026-09-15T18:30"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        appointment = Appointment.objects.latest("created_at")
        shares = sorted(row.salary_share_percent for row in appointment.assignees.all())
        self.assertEqual(shares, [Decimal("50.00"), Decimal("50.00")])
        from apps.payroll.services import appointment_salary, assignment_rows
        salaries = [appointment_salary(appointment, employee, share) for employee, share in assignment_rows(appointment)]
        self.assertEqual(salaries, [Decimal("825.00"), Decimal("825.00")])

    def test_personal_service_percent_is_snapshotted_for_salary(self):
        self.service.employee_percent = 30
        self.service.save(update_fields=("employee_percent",))
        EmployeeService.objects.create(
            tenant=self.tenant, employee=self.employee, service=self.service,
            percent_override=40,
        )
        EmployeeService.objects.create(
            tenant=self.tenant, employee=self.second_employee, service=self.service,
        )
        self.client.force_login(self.owner)
        self.client.post(
            "/appointments/new/",
            {"client": self.client_record.id, "car": self.car.id,
             "employees": [self.employee.id, self.second_employee.id],
             "services": [self.service.id], "start_at": "2026-09-15T18:30"},
            HTTP_HOST="lk.localhost",
        )
        appointment = Appointment.objects.latest("created_at")
        from apps.payroll.services import appointment_salary, assignment_rows
        salaries = {
            employee.id: appointment_salary(appointment, employee, share)
            for employee, share in assignment_rows(appointment)
        }
        self.assertEqual(salaries[self.employee.id], Decimal("1100.00"))
        self.assertEqual(salaries[self.second_employee.id], Decimal("825.00"))

    def test_appointment_requires_only_client_name_and_phone(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/appointments/new/",
            {"client_first_name": "Новый", "client_phone": "+79998887766",
             "employees": [self.employee.id], "services": [self.service.id],
             "start_at": "2026-09-15T18:30"}, HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        appointment = Appointment.objects.latest("created_at")
        self.assertEqual(appointment.client.name, "Новый")
        self.assertIsNone(appointment.car)
        self.assertFalse(Car.objects.filter(client=appointment.client).exists())

    def test_existing_client_booking_without_vehicle_does_not_create_empty_car(self):
        existing_count = self.client_record.cars.count()
        appointment = create_appointment(
            tenant=self.tenant,
            cleaned_data={
                "client_first_name": self.client_record.name,
                "client_last_name": self.client_record.last_name,
                "client_phone": self.client_record.phone,
                "car_make": "", "car_model": "", "car_plate": "",
                "employee": self.employee, "services": [self.service],
                "start_at": self.start_time(17),
                "end_at": self.start_time(17) + timedelta(minutes=90),
                "comment": "", "source": "website",
            },
        )
        self.assertEqual(appointment.client, self.client_record)
        self.assertIsNone(appointment.car)
        self.assertEqual(self.client_record.cars.count(), existing_count)

    def test_entered_vehicle_reuses_matching_client_car_before_creating(self):
        appointment = create_appointment(
            tenant=self.tenant,
            cleaned_data={
                "client_first_name": self.client_record.name,
                "client_last_name": self.client_record.last_name,
                "client_phone": self.client_record.phone,
                "car_make": "bmw", "car_model": "x5", "car_plate": "",
                "employee": self.employee, "services": [self.service],
                "start_at": self.start_time(17),
                "end_at": self.start_time(17) + timedelta(minutes=90),
                "comment": "", "source": "website",
            },
        )
        self.assertEqual(appointment.car, self.car)
        self.assertEqual(self.client_record.cars.count(), 1)

    def test_cash_payment_updates_open_shift_balance(self):
        appointment = self.create_record()
        self.client.force_login(self.owner)
        self.client.post(
            "/cash-shift/", {"action": "open", "open-opening_cash": "1000"},
            HTTP_HOST="lk.localhost",
        )
        response = self.client.post(
            f"/appointments/{appointment.id}/payments/add/",
            {"kind": "payment", "method": "cash", "amount": "2500", "comment": ""},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        from .models import CashShift
        self.assertEqual(CashShift.objects.get(closed_at__isnull=True).expected_cash, Decimal("3500.00"))

    def test_closed_cash_shift_keeps_expected_cash_visible(self):
        CashShift.objects.create(
            tenant=self.tenant,
            opened_by=self.owner,
            closed_at=timezone.now(),
            closed_by=self.owner,
            opening_cash=Decimal("1000"),
            expected_cash=Decimal("1250"),
            actual_cash=Decimal("1240"),
            difference=Decimal("-10"),
        )
        self.client.force_login(self.owner)

        response = self.client.get("/cash-shift/", HTTP_HOST="lk.localhost")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["active_shift"])
        self.assertEqual(response.context["displayed_expected_cash"], Decimal("1250"))
        self.assertContains(response, "Ожидается наличных в кассе")
        self.assertContains(response, "1250,00 ₽")

    def test_cash_payment_automatically_opens_shift(self):
        appointment = self.create_record()
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/appointments/{appointment.id}/payments/add/",
            {"kind": "payment", "method": "cash", "amount": "5500", "comment": ""},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        appointment.refresh_from_db()
        shift = CashShift.objects.get(closed_at__isnull=True)
        payment = PaymentTransaction.objects.get(appointment=appointment)
        self.assertEqual(shift.expected_cash, Decimal("5500.00"))
        self.assertEqual(payment.cash_shift, shift)
        self.assertEqual(appointment.payment_status, Appointment.PaymentStatus.PAID)
        detail = self.client.get(
            f"/appointments/{appointment.id}/?modal=1", HTTP_HOST="lk.localhost"
        )
        self.assertContains(detail, f"/payments/{payment.id}/delete/")

    def test_confirmed_cash_payment_can_be_deleted(self):
        appointment = self.create_record()
        shift = CashShift.objects.create(
            tenant=self.tenant, opened_by=self.owner,
            opening_cash=Decimal("1000"), expected_cash=Decimal("1000"),
        )
        payment = add_payment_transaction(
            appointment=appointment, actor=self.owner,
            kind=PaymentTransaction.Kind.PAYMENT,
            method=Appointment.PaymentMethod.CASH,
            amount=Decimal("5500"), comment="",
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/appointments/{appointment.id}/payments/{payment.id}/delete/",
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        appointment.refresh_from_db()
        shift.refresh_from_db()
        self.assertFalse(PaymentTransaction.objects.filter(id=payment.id).exists())
        self.assertEqual(shift.expected_cash, Decimal("1000.00"))
        self.assertEqual(appointment.amount_paid, Decimal("0.00"))
        self.assertEqual(appointment.payment_status, Appointment.PaymentStatus.UNPAID)

    def test_discount_is_applied_immediately_for_employee(self):
        admin = get_user_model().objects.create_user("admin-discount@example.com", "safe-test-password")
        Membership.objects.create(tenant=self.tenant, user=admin, role=Membership.Role.ADMIN)
        role = CompanyRole.objects.create(
            tenant=self.tenant, name="Администратор 5%",
            permissions=["appointments_view", "appointments_view_all", "appointments_manage"],
        )
        Employee.objects.create(tenant=self.tenant, name="Администратор", user=admin, company_role=role)
        self.client.force_login(admin)
        response = self.client.post(
            "/appointments/new/",
            {"client": self.client_record.id, "car": self.car.id, "employee": self.employee.id,
             "primary_share_percent": "100", "post": "", "services": [self.service.id],
             "start_at": "2026-09-15T18:00", "discount_kind": "percent",
             "discount_value": "10", "discount_reason": "Постоянный клиент"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        appointment = Appointment.objects.latest("created_at")
        self.assertEqual(appointment.discount_status, Appointment.DiscountStatus.APPLIED)
        self.assertEqual(appointment.total, Decimal("4950.00"))

    def test_create_modal_and_employee_unavailable_time(self):
        self.client.force_login(self.owner)
        modal = self.client.get(
            f"/appointments/new/?date=2026-09-15&start=10:00&employee={self.employee.id}&modal=1",
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(modal.status_code, 200)
        self.assertContains(modal, "appointment-create-columns")
        self.assertNotContains(modal, "<html")
        invalid = self.client.post(
            "/appointments/new/?modal=1",
            {"client": self.client_record.id, "car": self.car.id,
             "employees": [self.employee.id], "services": [self.service.id],
             "start_at": "2026-09-15T19:00"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertContains(invalid, "В выбранное время не работают", status_code=422)

    def test_calendar_marks_time_outside_employee_shift_unavailable(self):
        shift = EmployeeShift.objects.get(employee=self.employee, date="2026-09-15")
        shift.start_time, shift.end_time = time(14), time(20)
        shift.save(update_fields=("start_time", "end_time"))
        self.client.force_login(self.owner)
        response = self.client.get("/appointments/?date=2026-09-15", HTTP_HOST="lk.localhost")
        self.assertContains(response, 'data-shift-start="840"')
        self.assertContains(response, "Смена ещё не началась")

    def test_drag_move_is_blocked_outside_employee_shift(self):
        appointment = self.create_record()
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/appointments/{appointment.id}/move/",
            {"employee": self.second_employee.id, "date": "2026-09-15", "start": "19:00"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 400)
        appointment.refresh_from_db()
        self.assertEqual(timezone.localtime(appointment.start_at).strftime("%H:%M"), "10:00")

    def test_master_can_only_open_and_start_assigned_work(self):
        role = CompanyRole.objects.create(
            tenant=self.tenant, name="Мастер своих работ",
            permissions=["appointments_view", "appointments_status_own"],
        )
        self.employee.user, self.employee.company_role = self.master, role
        self.employee.save()
        own = self.create_record(employee=self.employee)
        other = self.create_record(start=self.start_time(14), employee=self.second_employee)
        self.client.force_login(self.master)
        self.assertEqual(self.client.get(f"/appointments/{own.id}/", HTTP_HOST="lk.localhost").status_code, 200)
        self.assertEqual(self.client.get(f"/appointments/{other.id}/", HTTP_HOST="lk.localhost").status_code, 403)
        response = self.client.post(
            f"/appointments/{own.id}/transition/start/", HTTP_HOST="lk.localhost"
        )
        self.assertEqual(response.status_code, 302)

    def test_public_rating_requires_reason_for_low_score(self):
        appointment = self.create_record(status=Appointment.Status.COMPLETED)
        url = f"/feedback/{appointment.feedback_token}/"
        invalid = self.client.post(url, {"score": "2", "comment": ""}, HTTP_HOST="lk.localhost")
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "укажите комментарий")
        valid = self.client.post(
            url, {"score": "2", "comment": "Остались разводы"}, HTTP_HOST="lk.localhost"
        )
        self.assertEqual(valid.status_code, 200)
        rating = ClientRating.objects.get(appointment=appointment)
        self.assertTrue(rating.submitted_by_client)
        self.assertIsNone(rating.created_by)

    def test_complaint_links_appointment_service_and_rework(self):
        appointment = self.create_record(status=Appointment.Status.COMPLETED)
        self.client.force_login(self.owner)
        response = self.client.post(
            "/quality/new/",
            {
                "appointment": appointment.id, "reason": Complaint.Reason.QUALITY,
                "service": self.service.id, "description": "Разводы на кузове",
                "status": Complaint.Status.OPEN, "assigned_to": self.employee.id,
                "is_rework": "on", "financial_loss": "1500",
            },
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        complaint = Complaint.objects.get(appointment=appointment)
        self.assertEqual(complaint.service, self.service)
        self.assertTrue(complaint.is_rework)
        edit = self.client.get(f"/quality/{complaint.id}/edit/", HTTP_HOST="lk.localhost")
        detail = self.client.get(f"/quality/{complaint.id}/?modal=1", HTTP_HOST="lk.localhost")
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Открыть запись")

    def test_calendar_shows_and_moves_each_assignee(self):
        appointment = self.create_record()
        primary = appointment.assignees.get(is_primary=True)
        primary.salary_share_percent = 60
        primary.save(update_fields=("salary_share_percent",))
        secondary = AppointmentAssignee.objects.create(
            tenant=self.tenant, appointment=appointment, employee=self.second_employee,
            salary_share_percent=40,
        )
        third = Employee.objects.create(tenant=self.tenant, name="Третий мастер")
        EmployeeShift.objects.create(
            tenant=self.tenant, employee=third, date=datetime(2026, 9, 15).date(),
            start_time=time(9), end_time=time(20),
        )
        self.client.force_login(self.owner)
        calendar = self.client.get("/appointments/?date=2026-09-15", HTTP_HOST="lk.localhost")
        self.assertContains(calendar, f'data-assignment="{primary.id}"')
        self.assertContains(calendar, f'data-assignment="{secondary.id}"')
        move = self.client.post(
            f"/appointments/{appointment.id}/move/",
            {"employee": third.id, "assignment": secondary.id,
             "date": "2026-09-15", "start": "12:15"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(move.status_code, 200)
        appointment.refresh_from_db()
        secondary.refresh_from_db()
        self.assertEqual(appointment.employee, self.employee)
        self.assertEqual(secondary.employee, third)
        self.assertEqual(timezone.localtime(appointment.start_at).strftime("%H:%M"), "12:15")

    def test_appointment_delete_requires_reason_and_archives_record(self):
        from apps.core.models import Notification
        appointment = self.create_record()
        self.client.force_login(self.owner)
        invalid = self.client.post(
            f"/appointments/{appointment.id}/delete/", {"reason": ""},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(invalid.status_code, 302)
        self.assertTrue(Appointment.objects.filter(id=appointment.id).exists())
        response = self.client.post(
            f"/appointments/{appointment.id}/delete/", {"reason": "Клиент отменил визит"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Appointment.objects.filter(id=appointment.id).exists())
        archived = Appointment.all_objects.get(id=appointment.id)
        self.assertEqual(archived.archive_reason, "Клиент отменил визит")
        self.assertTrue(Notification.objects.filter(
            recipient=self.owner, kind="appointment_cancelled"
        ).exists())

    def test_paid_appointment_must_be_refunded_before_delete(self):
        appointment = self.create_record()
        appointment.amount_paid = appointment.total
        appointment.payment_status = Appointment.PaymentStatus.PAID
        appointment.save(update_fields=("amount_paid", "payment_status"))
        self.client.force_login(self.owner)
        self.client.post(
            f"/appointments/{appointment.id}/delete/", {"reason": "Отмена"},
            HTTP_HOST="lk.localhost",
        )
        self.assertTrue(Appointment.objects.filter(id=appointment.id).exists())

    def test_completed_appointment_keeps_services_and_discount_when_edited(self):
        appointment = self.create_record(status=Appointment.Status.COMPLETED)
        before_total = appointment.total
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/appointments/{appointment.id}/edit/",
            {
                "client": self.client_record.id, "car": self.car.id,
                "employees": [self.employee.id], "services": [self.long_service.id],
                "start_at": "2026-09-15T10:00", "end_at": "2026-09-15T12:00",
                "discount_kind": "fixed", "discount_value": "5000",
                "discount_reason": "Подмена данных", "comment": "Разрешённое изменение",
            }, HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        appointment.refresh_from_db()
        self.assertEqual(appointment.total, before_total)
        self.assertEqual(appointment.items.get().service, self.service)
        self.assertEqual(appointment.comment, "Разрешённое изменение")

    def test_owner_can_collect_cash_from_open_shift(self):
        shift = CashShift.objects.create(
            tenant=self.tenant, opened_by=self.owner, opening_cash=Decimal("1000"),
            expected_cash=Decimal("1000"),
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            "/cash-shift/", {"action": "collect", "collection-amount": "250",
                             "collection-comment": "В сейф"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        shift.refresh_from_db()
        self.assertEqual(shift.expected_cash, Decimal("750"))
        self.assertTrue(CashCollection.objects.filter(cash_shift=shift, amount=250).exists())

class CalendarFragmentAndComplaintStatusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Fragment", slug="fragment")
        User = get_user_model()
        cls.owner = User.objects.create_user("fragment-owner@example.com", "safe-test-password")
        Membership.objects.create(tenant=cls.tenant, user=cls.owner, role=Membership.Role.OWNER)
        cls.client_record = Client.objects.create(tenant=cls.tenant, name="Иван", phone="+7 999 123-45-67")
        cls.car = Car.objects.create(tenant=cls.tenant, client=cls.client_record, make="BMW", model="X5")
        cls.employee = Employee.objects.create(tenant=cls.tenant, name="Алексей", position="Мастер")
        EmployeeShift.objects.create(
            tenant=cls.tenant, employee=cls.employee, date=datetime(2026, 9, 15).date(),
            start_time=time(9), end_time=time(20),
        )
        cls.service = Service.objects.create(tenant=cls.tenant, name="Комплекс", price=Decimal("1000"), duration_minutes=60)

    def create_record(self):
        start = timezone.make_aware(datetime(2026, 9, 15, 10, 0))
        return create_appointment(
            tenant=self.tenant,
            cleaned_data={
                "client": self.client_record, "car": self.car, "employee": self.employee,
                "post": None, "services": [self.service], "start_at": start,
                "end_at": start + timedelta(minutes=60), "comment": "",
            },
        )

    def test_calendar_fragment_uses_requested_resource_kind_without_saving_profile(self):
        self.client.force_login(self.owner)
        original_kind = self.owner.calendar_resource_kind
        response = self.client.get(
            "/appointments/?date=2026-09-15&resource_kind=post&partial=1",
            HTTP_HOST="fragment.localhost",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-resource-kind="post"')
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.calendar_resource_kind, original_kind)

    def test_quality_manager_can_update_complaint_status_inline(self):
        appointment = self.create_record()
        complaint = Complaint.objects.create(
            tenant=self.tenant, appointment=appointment, reason=Complaint.Reason.QUALITY,
            description="Нужна проверка", created_by=self.owner,
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/quality/{complaint.id}/status/", {"status": Complaint.Status.RESOLVED},
            HTTP_HOST="fragment.localhost", HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        complaint.refresh_from_db()
        self.assertEqual(complaint.status, Complaint.Status.RESOLVED)
        self.assertEqual(complaint.resolved_by, self.owner)

    def test_quality_searches_client_car_service_and_description(self):
        appointment = self.create_record()
        Complaint.objects.create(
            tenant=self.tenant, appointment=appointment, service=self.service,
            reason=Complaint.Reason.QUALITY, description="Следы после полировки",
            created_by=self.owner,
        )
        other_client = Client.objects.create(
            tenant=self.tenant, name="Мария", phone="+7 999 765-43-21",
        )
        other_car = Car.objects.create(
            tenant=self.tenant, client=other_client, make="Lada", model="Vesta",
        )
        start = timezone.make_aware(datetime(2026, 9, 16, 10, 0))
        other_appointment = Appointment.objects.create(
            tenant=self.tenant, client=other_client, car=other_car,
            start_at=start, end_at=start + timedelta(hours=1),
        )
        Complaint.objects.create(
            tenant=self.tenant, appointment=other_appointment,
            reason=Complaint.Reason.OTHER, description="Другой случай", created_by=self.owner,
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            "/quality/?q=полировки", HTTP_HOST="fragment.localhost",
        )

        self.assertContains(response, "Следы после полировки")
        self.assertNotContains(response, "Другой случай")

    def test_quality_status_javascript_serializes_before_disabling_select(self):
        script = (Path(settings.BASE_DIR) / "static/js/app.js").read_text()
        serialize = script.index("const payload = new FormData(form)")
        disable = script.index("select.disabled = true", serialize)
        self.assertLess(serialize, disable)
        self.assertIn("Сохранено", script)
