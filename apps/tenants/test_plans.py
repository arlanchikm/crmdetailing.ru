from datetime import timedelta
from decimal import Decimal
from django.test import TestCase, override_settings
from django.utils import timezone
from apps.staff.models import Employee, ensure_default_roles
from .models import Plan, Subscription, PromoCode, SubscriptionOrder, PromoRedemption
from .plans import (assign_plan, sync_subscription, counted_employees, active_employee_limit,
    activate_trial, create_order, confirm_order, order_quote, add_months)
from .services import create_company_owner

@override_settings(DEBUG=True,ALLOWED_HOSTS=['testserver','localhost','.localhost'],TENANT_DOMAIN_SUFFIXES=('.localhost','.crmdetailing.ru'))
class EmployeePackageTests(TestCase):
    def setUp(self):
        self.tenant,self.owner=create_company_owner(company_name='Тест',slug='package',owner_name='Владелец',email='package@example.com',password='test-password-123')
        self.location=self.tenant.locations.get(is_primary=True)
        self.client.force_login(self.owner)
        self.host={'HTTP_HOST':'package.localhost'}
    def test_registration_starts_selected_trial_and_excludes_owner(self):
        self.assertEqual(self.tenant.subscription.status,'trial')
        self.assertEqual(self.tenant.subscription.plan_id,'start')
        self.assertEqual(active_employee_limit(self.tenant),4)
        self.assertEqual(counted_employees(self.tenant).count(),0)
        self.assertEqual(self.tenant.employees.get(user=self.owner).company_role.builtin_key,'owner')
    def test_four_employees_can_be_added_beyond_owner_fifth_is_blocked(self):
        for i in range(4):
            response=self.client.post('/employees/new/',{'name':f'Мастер {i}','active':'on'},**self.host)
            self.assertEqual(response.status_code,302,response.content.decode()[:200])
        response=self.client.post('/employees/new/',{'name':'Пятый','active':'on'},**self.host)
        self.assertContains(response,'Достигнут лимит')
        self.assertContains(response,'href="/subscription/settings/"')
        self.assertEqual(counted_employees(self.tenant).count(),4)
    def test_trial_expires_to_read_only_without_removing_features(self):
        sub=self.tenant.subscription
        sub.trial_ends_at=timezone.now()-timedelta(minutes=1);sub.save()
        expired=sync_subscription(self.tenant)
        self.assertEqual(expired.status,'suspended')
        self.assertEqual(self.client.get('/cash-shift/',**self.host).status_code,200)
        response=self.client.post('/employees/new/',{'name':'Blocked','active':'on'},**self.host)
        self.assertEqual(response.status_code,403)
        self.assertContains(response,'Период подписки завершён',status_code=403)
        self.assertEqual(self.client.get('/subscription/settings/',**self.host).status_code,200)
        with self.assertRaisesMessage(ValueError,'уже был использован'):
            activate_trial(self.tenant,self.owner,'business')
    def test_all_features_available_in_smallest_package(self):
        from .plans import subscription_features,ALL_FEATURES
        self.assertEqual(subscription_features(self.tenant.subscription),set(ALL_FEATURES))
    def test_prices_and_six_month_total(self):
        subtotal,discount,total,promo=order_quote(self.tenant,Plan.objects.get(pk='start'),6)
        self.assertEqual(total,Decimal('7794'))
        self.assertEqual(order_quote(self.tenant,Plan.objects.get(pk='center'),1)[2],Decimal('3699'))
    def test_first_payment_promo_consumed_only_on_confirmation(self):
        promo=PromoCode.objects.create(code='FIRST',kind='percent',value=10,max_uses=1)
        order=create_order(self.tenant,Plan.objects.get(pk='business'),6,'first')
        self.assertEqual(order.total,Decimal('10794.60'))
        self.assertEqual(promo.redemptions.count(),0)
        confirm_order(order,self.owner)
        self.assertEqual(promo.redemptions.count(),1)
        self.assertEqual(sync_subscription(self.tenant).plan_id,'business')
        with self.assertRaisesMessage(ValueError,'уже использован'):
            create_order(self.tenant,order.plan,1,'FIRST')
        with self.assertRaisesMessage(ValueError,'уже обработан'):
            confirm_order(order,self.owner)
    def test_recurring_fixed_promo_and_expiry(self):
        promo=PromoCode.objects.create(code='FOREVER',kind='fixed',value=500,recurring=True)
        order=create_order(self.tenant,Plan.objects.get(pk='start'),1,'FOREVER');confirm_order(order,self.owner)
        again=create_order(self.tenant,order.plan,6,'FOREVER')
        self.assertEqual(again.total,Decimal('7294'))
        promo.ends_at=timezone.now()-timedelta(seconds=1);promo.save()
        with self.assertRaisesMessage(ValueError,'истёк'):
            order_quote(self.tenant,order.plan,6,'FOREVER')
    def test_upgrade_now_and_downgrade_at_paid_boundary(self):
        current=assign_plan(self.tenant,'business',self.owner,ends_at=timezone.now()+timedelta(days=20))
        boundary=current.ends_at
        order=create_order(self.tenant,Plan.objects.get(pk='start'),6)
        confirm_order(order,self.owner)
        current=sync_subscription(self.tenant)
        self.assertEqual(current.plan_id,'business');self.assertEqual(current.scheduled_plan_id,'start')
        future=sync_subscription(self.tenant,now=boundary+timedelta(seconds=1))
        self.assertEqual(future.plan_id,'start');self.assertEqual(future.status,'active')
        self.assertEqual(future.ends_at,add_months(boundary,6))
    def test_receptionist_has_only_reception_permissions(self):
        from django.contrib.auth import get_user_model
        from .models import Membership
        user=get_user_model().objects.create_user('reception@example.com','test-password')
        Membership.objects.create(tenant=self.tenant,user=user,role='admin')
        Employee.objects.create(tenant=self.tenant,location=self.location,user=user,name='Ресепшен',company_role=ensure_default_roles(self.tenant)['admin'])
        self.client.force_login(user)
        self.assertEqual(self.client.get('/analytics/',**self.host).status_code,403)
        self.assertEqual(self.client.get('/subscription/settings/',**self.host).status_code,403)
        self.assertEqual(self.client.get('/appointments/',**self.host).status_code,200)

    def test_inactive_employee_activation_cannot_bypass_limit(self):
        for i in range(4):
            Employee.objects.create(tenant=self.tenant,location=self.location,name=f'Активный {i}')
        inactive=Employee.objects.create(tenant=self.tenant,location=self.location,name='Неактивный',active=False)
        response=self.client.post(f'/employees/{inactive.pk}/edit/',{'name':inactive.name,'active':'on'},**self.host)
        self.assertContains(response,'Достигнут лимит')
        inactive.refresh_from_db()
        self.assertFalse(inactive.active)

    def test_recurring_promo_is_applied_automatically_on_renewal(self):
        PromoCode.objects.create(code='AUTO',kind='percent',value=20,recurring=True)
        first=create_order(self.tenant,Plan.objects.get(pk='start'),1,'AUTO')
        confirm_order(first,self.owner)
        self.assertEqual(order_quote(self.tenant,first.plan,1)[2],Decimal('1199.20'))

    def test_overdue_scheduled_period_does_not_remain_active(self):
        current=assign_plan(self.tenant,'business',self.owner,ends_at=timezone.now()+timedelta(days=20))
        boundary=current.ends_at
        confirm_order(create_order(self.tenant,Plan.objects.get(pk='start'),1),self.owner)
        future=sync_subscription(self.tenant,now=add_months(boundary,2))
        self.assertEqual(future.status,'suspended')

    def test_payment_confirmation_rechecks_current_staff_count(self):
        order=create_order(self.tenant,Plan.objects.get(pk='start'),1)
        for i in range(5):
            Employee.objects.create(tenant=self.tenant,location=self.location,name=f'Карточка {i}')
        with self.assertRaisesMessage(ValueError,'превышает лимит'):
            confirm_order(order,self.owner)
        order.refresh_from_db()
        self.assertEqual(order.status,'pending')

    def test_first_payment_only_promo_rejected_after_another_paid_order(self):
        confirm_order(create_order(self.tenant,Plan.objects.get(pk='start'),1),self.owner)
        PromoCode.objects.create(code='NEWFIRST',kind='percent',value=10,recurring=False)
        with self.assertRaisesMessage(ValueError,'первый платёж'):
            order_quote(self.tenant,Plan.objects.get(pk='start'),1,'NEWFIRST')

    def test_manual_company_price_used_for_its_current_package(self):
        sub=self.tenant.subscription
        sub.overrides={'custom_price':'999.00'};sub.save()
        self.assertEqual(order_quote(self.tenant,Plan.objects.get(pk='start'),6)[2],Decimal('5994'))
        self.assertEqual(order_quote(self.tenant,Plan.objects.get(pk='business'),6)[2],Decimal('11994'))
