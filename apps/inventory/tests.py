from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model

from apps.catalog.models import Service
from apps.customers.models import Car, Client
from apps.operations.models import Appointment, AppointmentService
from apps.staff.models import CompanyRole, Employee
from apps.tenants.models import Membership, Tenant

from .models import (
    AppointmentConsumption, Consumable, InventoryCategory, InventoryMovement,
    ServiceConsumableNorm, ensure_default_categories,
)
from .services import write_off_consumables


class ConsumableWriteoffTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="LK", slug="lk-inventory")
        self.owner = get_user_model().objects.create_user("inventory-owner@example.com", "safe-test-password")
        Membership.objects.create(tenant=self.tenant, user=self.owner, role=Membership.Role.OWNER)
        self.client_record = Client.objects.create(tenant=self.tenant, name="Иван", phone="+79990000000")
        self.car = Car.objects.create(tenant=self.tenant, client=self.client_record, make="BMW", model="X5")
        self.employee = Employee.objects.create(tenant=self.tenant, name="Мастер")
        self.service = Service.objects.create(tenant=self.tenant, name="Комплекс", price=3000, duration_minutes=120)
        self.category = ensure_default_categories(self.tenant)["chemistry"]
        start = timezone.now()
        self.appointment = Appointment.objects.create(
            tenant=self.tenant, client=self.client_record, car=self.car, employee=self.employee,
            start_at=start, end_at=start + timedelta(hours=2), total=3000,
        )
        AppointmentService.objects.create(
            tenant=self.tenant, appointment=self.appointment, service=self.service,
            name_snapshot=self.service.name, price_snapshot=self.service.price,
            duration_minutes_snapshot=self.service.duration_minutes,
        )
        self.consumable = Consumable.objects.create(
            tenant=self.tenant, name="Шампунь", category=self.category,
            cost_per_unit=Decimal("500"), planned_stock=Decimal("10"),
            actual_stock=Decimal("9.5"),
        )
        ServiceConsumableNorm.objects.create(
            tenant=self.tenant, service=self.service, consumable=self.consumable,
            quantity=Decimal("250"),
        )

    def test_writeoff_changes_plan_once_and_never_changes_fact(self):
        write_off_consumables(self.appointment)
        write_off_consumables(self.appointment)
        self.consumable.refresh_from_db()
        self.assertEqual(self.consumable.planned_stock, Decimal("9.750"))
        self.assertEqual(self.consumable.actual_stock, Decimal("9.500"))
        self.assertEqual(AppointmentConsumption.objects.count(), 1)
        self.assertEqual(AppointmentConsumption.objects.get().total_cost, Decimal("125.00"))

    def test_unit_quantity_multiplies_normative_writeoff_without_snapshot(self):
        item = self.appointment.items.get()
        item.quantity = 3
        item.save(update_fields=("quantity",))
        write_off_consumables(self.appointment)
        self.consumable.refresh_from_db()
        consumption = AppointmentConsumption.objects.get()
        self.assertEqual(self.consumable.planned_stock, Decimal("9.250"))
        self.assertEqual(consumption.quantity, Decimal("0.750"))
        self.assertEqual(consumption.total_cost, Decimal("375.00"))

    def test_service_cost_is_calculated_from_milliliters(self):
        self.assertEqual(self.service.cost, Decimal("125.000"))

    def test_new_consumable_uses_fact_as_initial_plan(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/inventory/new/",
            {
                "name": "Очиститель", "category": self.category.id,
                "cost_per_unit": "800", "actual_stock": "3.500", "active": "on",
            },
            HTTP_HOST="lk-inventory.localhost",
        )
        self.assertEqual(response.status_code, 302)
        item = Consumable.objects.get(name="Очиститель")
        self.assertEqual(item.planned_stock, Decimal("3.500"))
        self.assertEqual(item.actual_stock, Decimal("3.500"))

    def test_entering_fact_resets_plan(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/inventory/{self.consumable.id}/edit/",
            {"name": self.consumable.name, "category": self.category.id,
             "cost_per_unit": "500", "actual_stock": "8.25", "active": "on"},
            HTTP_HOST="lk-inventory.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.consumable.refresh_from_db()
        self.assertEqual(self.consumable.actual_stock, Decimal("8.250"))
        self.assertEqual(self.consumable.planned_stock, Decimal("8.250"))

    def test_inline_fact_resets_plan_and_soft_delete_hides_item(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/inventory/{self.consumable.id}/fact/", {"actual_stock": "7.25"},
            HTTP_HOST="lk-inventory.localhost",
        )
        self.assertEqual(response.status_code, 200)
        self.consumable.refresh_from_db()
        self.assertEqual(self.consumable.actual_stock, Decimal("7.250"))
        self.assertEqual(self.consumable.planned_stock, Decimal("7.250"))
        self.client.post(
            f"/inventory/{self.consumable.id}/delete/", HTTP_HOST="lk-inventory.localhost"
        )
        self.consumable.refresh_from_db()
        self.assertFalse(self.consumable.active)
        listing = self.client.get("/inventory/", HTTP_HOST="lk-inventory.localhost")
        self.assertNotContains(listing, "Шампунь")

    def test_inventory_list_computes_statuses_from_plan_and_minimum(self):
        self.consumable.minimum_stock = Decimal("5")
        self.consumable.planned_stock = Decimal("5")
        self.consumable.actual_stock = Decimal("9.5")
        self.consumable.save(update_fields=("minimum_stock", "planned_stock", "actual_stock"))
        Consumable.objects.create(
            tenant=self.tenant, name="Пустой", category=self.category,
            cost_per_unit=Decimal("100"), planned_stock=Decimal("-0.4"), actual_stock=1,
        )
        Consumable.objects.create(
            tenant=self.tenant, name="Без минимума", category=self.category,
            cost_per_unit=Decimal("100"), planned_stock=2, actual_stock=2, minimum_stock=0,
        )
        self.client.force_login(self.owner)

        response = self.client.get("/inventory/", HTTP_HOST="lk-inventory.localhost")

        self.assertEqual(response.context["status_counts"], {"normal": 1, "low": 1, "empty": 1})
        self.assertContains(response, "На исходе")
        self.assertContains(response, "Закончился")

    def test_inline_fact_returns_new_computed_status(self):
        self.consumable.minimum_stock = Decimal("5")
        self.consumable.save(update_fields=("minimum_stock",))
        self.client.force_login(self.owner)

        response = self.client.post(
            f"/inventory/{self.consumable.id}/fact/", {"actual_stock": "0"},
            HTTP_HOST="lk-inventory.localhost", HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "empty")
        self.assertEqual(response.json()["status_label"], "Закончился")

    def test_inventory_manager_without_count_permission_can_update_inline_fact(self):
        manager = get_user_model().objects.create_user(
            "inventory-manager@example.com", "safe-test-password"
        )
        Membership.objects.create(
            tenant=self.tenant, user=manager, role=Membership.Role.ADMIN
        )
        role = CompanyRole.objects.create(
            tenant=self.tenant,
            name="Управляющий складом",
            permissions=["inventory_view", "inventory_manage"],
        )
        Employee.objects.create(
            tenant=self.tenant, user=manager, name="Управляющий", company_role=role
        )
        self.client.force_login(manager)

        response = self.client.post(
            f"/inventory/{self.consumable.id}/fact/",
            {"actual_stock": "6.75"},
            HTTP_HOST="lk-inventory.localhost",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.consumable.refresh_from_db()
        self.assertEqual(self.consumable.actual_stock, Decimal("6.750"))
        self.assertEqual(self.consumable.planned_stock, Decimal("6.750"))

    def test_low_stock_compares_planned_balance(self):
        self.consumable.minimum_stock = Decimal("5")
        self.consumable.planned_stock = Decimal("6")
        self.consumable.actual_stock = Decimal("1")
        self.assertFalse(self.consumable.is_low_stock)
        self.consumable.planned_stock = Decimal("5")
        self.assertTrue(self.consumable.is_low_stock)
        self.consumable.minimum_stock = Decimal("0")
        self.consumable.planned_stock = Decimal("-0.1")
        self.assertTrue(self.consumable.is_low_stock)

    def test_milliliter_stock_uses_price_per_liter(self):
        category = InventoryCategory.objects.create(
            tenant=self.tenant, name="Пробники", unit=InventoryCategory.Unit.MILLILITER
        )
        item = Consumable.objects.create(
            tenant=self.tenant, name="Состав", category=category,
            cost_per_unit=Decimal("1000"), planned_stock=Decimal("500"), actual_stock=Decimal("500"),
        )
        norm = ServiceConsumableNorm.objects.create(
            tenant=self.tenant, service=self.service, consumable=item, quantity=Decimal("50")
        )
        self.assertEqual(norm.stock_quantity, Decimal("50"))
        self.assertEqual(norm.total_cost, Decimal("50"))
        write_off_consumables(self.appointment)
        item.refresh_from_db()
        self.assertEqual(item.planned_stock, Decimal("450"))
        self.assertEqual(
            AppointmentConsumption.objects.get(appointment=self.appointment, consumable=item).total_cost,
            Decimal("50.00"),
        )

    def test_category_can_be_created_from_consumable_modal(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/inventory/categories/new/?modal=1",
            {"name": "Наносоставы", "unit": "milliliter", "active": "on"},
            HTTP_HOST="lk-inventory.localhost",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unit"], "milliliter")
        self.assertTrue(InventoryCategory.objects.filter(name="Наносоставы").exists())

    def test_receipt_updates_both_balances_and_creates_movement(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/inventory/{self.consumable.id}/movement/",
            {"kind": "receipt", "quantity": "2.5", "unit_cost": "600", "reason": "Поставка"},
            HTTP_HOST="lk-inventory.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.consumable.refresh_from_db()
        self.assertEqual(self.consumable.planned_stock, Decimal("12.500"))
        self.assertEqual(self.consumable.actual_stock, Decimal("12.000"))
        movement = InventoryMovement.objects.get(kind=InventoryMovement.Kind.RECEIPT)
        self.assertEqual(movement.actual_delta, Decimal("2.500"))
        self.assertEqual(movement.created_by, self.owner)
