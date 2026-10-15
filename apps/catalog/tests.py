from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.customers.models import ensure_default_vehicle_classes
from apps.inventory.models import Consumable, ServiceConsumableNorm, ensure_default_categories
from apps.inventory.services import write_off_consumables
from apps.operations.models import Appointment
from apps.operations.services import create_appointment
from apps.staff.models import Employee, EmployeeService, EmployeeShift
from apps.tenants.services import create_company_owner
from .models import Service, ServiceCategory, ServiceVehicleClassPrice


@override_settings(DEBUG=True, ALLOWED_HOSTS=['testserver', 'localhost', '.localhost'])
class ClassServiceNormTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = create_company_owner(
            company_name='Нормативы', slug='classnorms', owner_name='Владелец',
            email='classnorms@example.test', password='safe-test-password',
        )
        self.location = self.tenant.locations.get(is_primary=True)
        self.classes = ensure_default_vehicle_classes(self.tenant)
        self.service = Service.objects.create(tenant=self.tenant, location=self.location,
                                              name='Мойка', price=1000, duration_minutes=30)
        self.consumable = Consumable.objects.create(
            tenant=self.tenant, location=self.location, name='Шампунь',
            category=ensure_default_categories(self.tenant)['chemistry'],
            cost_per_unit=1000, planned_stock=10, actual_stock=10,
        )
        self.common = ServiceConsumableNorm.objects.create(
            tenant=self.tenant, service=self.service, consumable=self.consumable, quantity=50,
        )
        self.host = {'HTTP_HOST': 'classnorms.localhost'}
        self.client.force_login(self.owner)

    def test_class_norm_replaces_common_and_empty_class_falls_back(self):
        suv = self.classes['suv']
        ServiceVehicleClassPrice.objects.create(tenant=self.tenant, service=self.service,
                                                vehicle_class=suv, duration_minutes=75)
        ServiceConsumableNorm.objects.create(tenant=self.tenant, service=self.service,
                                            vehicle_class=suv, consumable=self.consumable, quantity=200)
        self.assertEqual(self.service.duration_for_class(suv), 75)
        self.assertEqual(self.service.price_for_class(suv), Decimal('1000'))
        self.assertEqual(self.service.cost_for_class(suv), Decimal('200'))
        self.assertEqual(self.service.cost_for_class(self.classes['sedan']), Decimal('50'))
        self.assertEqual(self.service.duration_for_class(self.classes['sedan']), 30)
        self.assertEqual(self.service.cost, Decimal('50'))

    def editor_data(self):
        suv = self.classes['suv']
        return {
            'name': self.service.name, 'duration_minutes': 30,
            'calculation_type': Service.CalculationType.SERVICE, 'active': 'on',
            f'class_price_{suv.pk}': 1600, f'class_duration_{suv.pk}': 75,
            'norms-TOTAL_FORMS': 1, 'norms-INITIAL_FORMS': 1,
            'norms-0-id': str(self.common.pk), 'norms-0-consumable': str(self.consumable.pk), 'norms-0-quantity': 50,
            f'norms_{suv.pk}-TOTAL_FORMS': 1, f'norms_{suv.pk}-INITIAL_FORMS': 0,
            f'norms_{suv.pk}-0-consumable': str(self.consumable.pk), f'norms_{suv.pk}-0-quantity': 200,
        }

    def test_editor_saves_same_supply_separately_for_common_and_class(self):
        response = self.client.post(f'/services/{self.service.pk}/edit/', self.editor_data(), **self.host)
        self.assertEqual(response.status_code, 302)
        self.service.refresh_from_db()
        self.assertEqual(self.service.duration_for_class(self.classes['suv']), 75)
        self.assertEqual(self.service.cost_for_class(self.classes['suv']), Decimal('200'))
        self.assertEqual(self.service.cost, Decimal('50'))

    def test_editor_rejects_duplicate_supply_in_same_class_without_partial_save(self):
        data = self.editor_data(); prefix = f"norms_{self.classes['suv'].pk}"
        data.update({f'{prefix}-TOTAL_FORMS': 2, f'{prefix}-1-consumable': str(self.consumable.pk), f'{prefix}-1-quantity': 100})
        response = self.client.post(f'/services/{self.service.pk}/edit/', data, **self.host)
        self.assertContains(response, 'Расходник уже добавлен')
        self.assertFalse(self.service.vehicle_class_prices.exists())
        self.assertEqual(self.service.consumable_norms.count(), 1)

    def test_record_snapshots_class_duration_and_supply_cost_for_writeoff(self):
        suv = self.classes['suv']
        ServiceVehicleClassPrice.objects.create(tenant=self.tenant, service=self.service,
                                                vehicle_class=suv, price=1600, duration_minutes=75)
        norm = ServiceConsumableNorm.objects.create(tenant=self.tenant, service=self.service,
                    vehicle_class=suv, consumable=self.consumable, quantity=200)
        employee = Employee.objects.create(tenant=self.tenant, location=self.location, name='Мастер')
        EmployeeService.objects.create(tenant=self.tenant, employee=employee, service=self.service)
        day = timezone.localdate() + timedelta(days=2)
        EmployeeShift.objects.create(tenant=self.tenant, location=self.location, employee=employee,
                                     date=day, start_time=time(8), end_time=time(22))
        start = timezone.make_aware(datetime.combine(day, time(10)), ZoneInfo(self.tenant.timezone))
        appointment = create_appointment(tenant=self.tenant, location=self.location, cleaned_data={
            'client_first_name':'Иван', 'client_last_name':'', 'client_phone':'+79991234567',
            'car_make':'BMW', 'car_model':'X5', 'car_plate':'A123BC77',
            'car_vehicle_class':suv, 'service_classes':{str(self.service.pk):suv},
            'services':[self.service], 'employee':employee, 'post':None,
            'start_at':start, 'end_at':start+timedelta(minutes=75), 'comment':'',
        })
        item = appointment.items.get()
        self.assertEqual(item.duration_minutes_snapshot, 75)
        self.assertEqual(item.consumables_snapshot[0]['quantity'], '200.000')
        norm.quantity = 500; norm.save()
        appointment.status = Appointment.Status.COMPLETED; appointment.save()
        write_off_consumables(appointment); write_off_consumables(appointment)
        self.consumable.refresh_from_db()
        self.assertEqual(self.consumable.planned_stock, Decimal('9.800'))
        self.assertEqual(appointment.consumption_entries.get().total_cost, Decimal('200'))

    def test_unit_service_multiplies_class_price_duration_and_consumables(self):
        suv = self.classes['suv']
        self.service.calculation_type = Service.CalculationType.UNIT
        self.service.employee_percent = Decimal('25')
        self.service.save(update_fields=('calculation_type', 'employee_percent'))
        ServiceVehicleClassPrice.objects.create(
            tenant=self.tenant, service=self.service, vehicle_class=suv,
            price=Decimal('1400'), duration_minutes=20,
        )
        employee = Employee.objects.create(
            tenant=self.tenant, location=self.location, name='Мастер по сидениям',
        )
        day = timezone.localdate() + timedelta(days=3)
        EmployeeShift.objects.create(
            tenant=self.tenant, location=self.location, employee=employee,
            date=day, start_time=time(8), end_time=time(22),
        )
        start = timezone.make_aware(datetime.combine(day, time(10)), ZoneInfo(self.tenant.timezone))
        appointment = create_appointment(
            tenant=self.tenant, location=self.location,
            cleaned_data={
                'client_first_name': 'Анна', 'client_last_name': '',
                'client_phone': '+79997654321', 'car_make': 'BMW', 'car_model': 'X5',
                'car_plate': 'B321BC77', 'car_vehicle_class': suv,
                'service_classes': {str(self.service.pk): suv},
                'service_quantities': {str(self.service.pk): 3},
                'services': [self.service], 'employee': employee, 'post': None,
                'start_at': start, 'end_at': start + timedelta(minutes=60), 'comment': '',
            },
        )
        item = appointment.items.get()
        self.assertEqual(item.quantity, 3)
        self.assertEqual(item.price_snapshot, Decimal('4200'))
        self.assertEqual(item.duration_minutes_snapshot, 60)
        self.assertEqual(item.consumables_snapshot[0]['quantity'], '150.000')
        self.assertEqual(Decimal(item.consumables_snapshot[0]['total_cost']), Decimal('150'))
        self.assertEqual(item.display_name, 'Мойка × 3')
        self.assertEqual(appointment.subtotal, Decimal('4200'))

    def test_bulk_class_price_update_saves_all_rows(self):
        sedan = self.classes['sedan']
        suv = self.classes['suv']
        sedan_price = ServiceVehicleClassPrice.objects.create(
            tenant=self.tenant, service=self.service, vehicle_class=sedan, price=Decimal('1000'),
        )
        suv_price = ServiceVehicleClassPrice.objects.create(
            tenant=self.tenant, service=self.service, vehicle_class=suv, price=Decimal('1600'),
        )

        response = self.client.post(
            f'/services/{self.service.pk}/prices/{sedan_price.pk}/',
            {f'price_{sedan_price.pk}': '1250,50', f'price_{suv_price.pk}': '2100'},
            **self.host,
        )

        self.assertEqual(response.status_code, 200)
        sedan_price.refresh_from_db()
        suv_price.refresh_from_db()
        self.service.refresh_from_db()
        self.assertEqual(sedan_price.price, Decimal('1250.50'))
        self.assertEqual(suv_price.price, Decimal('2100'))
        self.assertEqual(self.service.price, Decimal('1250.50'))

    def test_bulk_class_price_update_is_atomic_when_any_value_is_invalid(self):
        sedan = self.classes['sedan']
        suv = self.classes['suv']
        sedan_price = ServiceVehicleClassPrice.objects.create(
            tenant=self.tenant, service=self.service, vehicle_class=sedan, price=Decimal('1000'),
        )
        suv_price = ServiceVehicleClassPrice.objects.create(
            tenant=self.tenant, service=self.service, vehicle_class=suv, price=Decimal('1600'),
        )

        response = self.client.post(
            f'/services/{self.service.pk}/prices/{sedan_price.pk}/',
            {f'price_{sedan_price.pk}': '1250', f'price_{suv_price.pk}': 'ошибка'},
            **self.host,
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['errors'][str(suv_price.pk)], 'Неверное значение')
        sedan_price.refresh_from_db()
        suv_price.refresh_from_db()
        self.service.refresh_from_db()
        self.assertEqual(sedan_price.price, Decimal('1000'))
        self.assertEqual(suv_price.price, Decimal('1600'))
        self.assertEqual(self.service.price, Decimal('1000'))

    def test_service_category_can_be_assigned_and_delete_leaves_service(self):
        category = ServiceCategory.objects.create(
            tenant=self.tenant, location=self.location, name="Защитные покрытия",
        )
        self.service.category = category
        self.service.save(update_fields=("category",))

        response = self.client.post(
            f"/services/categories/{category.pk}/delete/", **self.host,
        )

        self.assertEqual(response.status_code, 302)
        self.service.refresh_from_db()
        self.assertIsNone(self.service.category)
        self.assertTrue(Service.objects.filter(pk=self.service.pk).exists())

    def test_service_active_toggle_uses_existing_edit_route(self):
        response = self.client.post(
            f"/services/{self.service.pk}/edit/", {"action": "toggle_active"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest", **self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.service.refresh_from_db()
        self.assertFalse(self.service.active)
        self.assertFalse(response.json()["active"])
