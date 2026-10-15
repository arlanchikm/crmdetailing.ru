from django.contrib.auth import get_user_model
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.tenants.models import Membership, Tenant
from apps.operations.models import Appointment
from apps.staff.models import Employee

from .models import Car, CarDamage, CarDamagePhoto, Client, ensure_default_vehicle_classes


class CustomerIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="LK", slug="lk")
        cls.other_tenant = Tenant.objects.create(name="Other", slug="other")
        User = get_user_model()
        cls.owner = User.objects.create_user("owner@example.com", "safe-test-password")
        cls.master = User.objects.create_user("master@example.com", "safe-test-password")
        Membership.objects.create(tenant=cls.tenant, user=cls.owner, role=Membership.Role.OWNER)
        Membership.objects.create(tenant=cls.tenant, user=cls.master, role=Membership.Role.MASTER)
        cls.other_client = Client.objects.create(
            tenant=cls.other_tenant,
            name="Скрытый клиент",
            phone="+7 900 000-00-00",
        )

    def test_owner_creates_client_in_current_tenant(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/clients/new/",
            {"name": "Иван Петров", "phone": "+7 999 123-45-67", "email": ""},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        created = Client.objects.get(name="Иван Петров")
        self.assertEqual(created.tenant, self.tenant)
        self.assertEqual(created.normalized_phone, "79991234567")

    def test_master_cannot_create_client(self):
        self.client.force_login(self.master)
        response = self.client.post(
            "/clients/new/",
            {"name": "Нельзя создать", "phone": "+7 999 123-45-67"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Client.objects.filter(name="Нельзя создать").exists())

    def test_client_list_does_not_show_other_tenant(self):
        Client.objects.create(tenant=self.tenant, name="Видимый клиент", phone="+7 911 111-11-11")
        self.client.force_login(self.owner)
        response = self.client.get("/clients/", HTTP_HOST="lk.localhost")
        self.assertContains(response, "Видимый клиент")
        self.assertNotContains(response, "Скрытый клиент")

    def test_text_search_does_not_match_every_client(self):
        Client.objects.create(tenant=self.tenant, name="Иван", phone="+7 911 111-11-11")
        Client.objects.create(tenant=self.tenant, name="Петр", phone="+7 922 222-22-22")
        self.client.force_login(self.owner)
        response = self.client.get("/clients/?q=Иван", HTTP_HOST="lk.localhost")
        self.assertContains(response, "Иван")
        self.assertNotContains(response, "Петр")

    def test_mislinked_cross_tenant_car_is_not_displayed(self):
        client = Client.objects.create(
            tenant=self.tenant, name="Видимый клиент", phone="+7 911 111-11-11"
        )
        Car.objects.create(
            tenant=self.other_tenant,
            client=client,
            make="Скрытая марка",
            model="Скрытая модель",
        )
        self.client.force_login(self.owner)
        response = self.client.get(
            f"/clients/{client.id}/", HTTP_HOST="lk.localhost"
        )
        self.assertNotContains(response, "Скрытая марка")

    def test_car_cannot_be_added_to_other_tenant_client(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/clients/{self.other_client.id}/cars/new/",
            {"make": "BMW", "model": "X5", "plate": "А123ВС77"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Car.objects.exists())

    def test_car_can_be_added_with_current_tenant_vehicle_class(self):
        client = Client.objects.create(
            tenant=self.tenant, name="Иван", phone="+7 911 111-11-11"
        )
        vehicle_class = ensure_default_vehicle_classes(self.tenant)["crossover"]
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/clients/{client.id}/cars/new/",
            {
                "vehicle_class": vehicle_class.id,
                "make": "BMW", "model": "X3", "plate": "А123ВС77",
                "color": "Чёрный", "vin": "", "notes": "",
            },
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        car = Car.objects.get(client=client)
        self.assertEqual(car.tenant, self.tenant)
        self.assertEqual(car.vehicle_class, vehicle_class)

    def test_duplicate_plate_is_rejected_inside_tenant(self):
        client = Client.objects.create(
            tenant=self.tenant, name="Первый", phone="+7 922 222-22-22"
        )
        Car.objects.create(
            tenant=self.tenant,
            client=client,
            make="Audi",
            model="A6",
            plate="А123ВС77",
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/clients/{client.id}/cars/new/",
            {"make": "BMW", "model": "X5", "plate": "а 123 вс-77"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Автомобиль с таким госномером уже существует")
        self.assertEqual(Car.objects.count(), 1)

    def test_client_and_car_cards_show_business_history(self):
        client = Client.objects.create(
            tenant=self.tenant, name="Иван", last_name="Петров", phone="+7 911 111-11-11"
        )
        car = Car.objects.create(
            tenant=self.tenant, client=client, make="BMW", model="X5", plate="А123ВС77"
        )
        employee = Employee.objects.create(tenant=self.tenant, name="Мастер")
        start = timezone.now() - timedelta(days=1)
        Appointment.objects.create(
            tenant=self.tenant, client=client, car=car, employee=employee,
            start_at=start, end_at=start + timedelta(hours=1),
            status=Appointment.Status.COMPLETED, payment_status=Appointment.PaymentStatus.PAID,
            total=Decimal("6000"), subtotal=Decimal("6000"), comment="Чистый салон",
        )
        self.client.force_login(self.owner)
        client_response = self.client.get(f"/clients/{client.id}/", HTTP_HOST="lk.localhost")
        car_response = self.client.get(f"/cars/{car.id}/", HTTP_HOST="lk.localhost")
        self.assertEqual(client_response.context["revenue"], Decimal("6000"))
        self.assertContains(client_response, "Средний чек")
        self.assertContains(car_response, "Чистый салон")
        self.assertContains(car_response, "История посещений")

    def test_car_can_be_edited_and_archived(self):
        client = Client.objects.create(tenant=self.tenant, name="Иван", phone="+79991111111")
        car = Car.objects.create(tenant=self.tenant, client=client, make="BMW", model="X5")
        self.client.force_login(self.owner)
        edit = self.client.post(
            f"/cars/{car.id}/edit/",
            {"make": "BMW", "model": "X6", "plate": "", "color": "Чёрный", "vin": "", "notes": ""},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(edit.status_code, 302)
        car.refresh_from_db()
        self.assertEqual(car.model, "X6")
        archive = self.client.post(f"/cars/{car.id}/archive/", HTTP_HOST="lk.localhost")
        self.assertEqual(archive.status_code, 302)
        car.refresh_from_db()
        self.assertIsNotNone(car.archived_at)

    def test_damage_marker_is_created_and_soft_deleted(self):
        client = Client.objects.create(tenant=self.tenant, name="Иван", phone="+79991111111")
        car = Car.objects.create(tenant=self.tenant, client=client, make="BMW", model="X5")
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/cars/{car.id}/damages/new/",
            {"new-x_percent": "42.5", "new-y_percent": "38.25",
             "new-discovered_on": "2026-09-11", "new-description": "Царапина на двери"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        damage = CarDamage.objects.get(car=car)
        page = self.client.get(f"/cars/{car.id}/", HTTP_HOST="lk.localhost")
        self.assertContains(page, "Царапина на двери")
        self.assertContains(page, "vehicle-damage-map.webp")
        response = self.client.post(
            f"/cars/{car.id}/damages/{damage.id}/delete/", HTTP_HOST="lk.localhost"
        )
        self.assertEqual(response.status_code, 302)
        damage.refresh_from_db()
        self.assertIsNotNone(damage.archived_at)

    def test_damage_marker_cannot_be_created_outside_vehicle(self):
        client = Client.objects.create(tenant=self.tenant, name="Иван", phone="+79991111112")
        car = Car.objects.create(tenant=self.tenant, client=client, make="BMW", model="X5")
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/cars/{car.id}/damages/new/",
            {"new-x_percent": "50", "new-y_percent": "10",
             "new-discovered_on": "2026-09-11", "new-description": "Мимо кузова"},
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CarDamage.objects.filter(car=car).exists())

    def test_individual_damage_photo_can_be_deleted(self):
        client = Client.objects.create(tenant=self.tenant, name="Иван", phone="+79991111113")
        car = Car.objects.create(tenant=self.tenant, client=client, make="BMW", model="X5")
        damage = CarDamage.objects.create(
            tenant=self.tenant, car=car, x_percent="42.5", y_percent="38.25",
            discovered_on="2026-09-11", description="Царапина",
        )
        photo = CarDamagePhoto.objects.create(
            damage=damage, file="damages/test/missing.jpg", original_name="scratch.jpg"
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/cars/{car.id}/damages/{damage.id}/photos/{photo.id}/delete/",
            HTTP_HOST="lk.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CarDamagePhoto.objects.filter(id=photo.id).exists())
