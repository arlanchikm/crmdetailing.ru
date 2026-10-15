import base64
import json
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from apps.tenants.models import Tenant, Membership
from apps.tenants.plans import sync_subscription
from apps.core.models import PushSubscription

@override_settings(DEBUG=True, ALLOWED_HOSTS=['localhost','.localhost'])
class PushOnboardingTests(TestCase):
    def setUp(self):
        self.tenant=Tenant.objects.create(name='Push',slug='push')
        self.user=get_user_model().objects.create_user(email='push@example.com',password='push-password123')
        Membership.objects.create(tenant=self.tenant,user=self.user,role='owner')
        self.host={'HTTP_HOST':'push.localhost'}
        self.client.force_login(self.user)

    def payload(self):
        encode=lambda value:base64.urlsafe_b64encode(value).decode().rstrip('=')
        key=ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint)
        return {'endpoint':'https://fcm.googleapis.com/fcm/send/test-device',
            'keys':{'p256dh':encode(key),'auth':encode(b'1234567890123456')},'enable_push':True}

    def test_onboarding_present_for_member_not_public_registration(self):
        response=self.client.get('/appointments/',**self.host)
        self.assertContains(response,'id="push-onboarding"')
        self.assertContains(response,'data-push-public-key=')
        self.assertNotContains(self.client.get('/register/',HTTP_HOST='localhost'),'id="push-onboarding"')

    def test_valid_device_opt_in_enables_preference_and_saves_one_device(self):
        payload=self.payload()
        for _ in range(2):
            response=self.client.post('/notifications/push/subscribe/',json.dumps(payload),content_type='application/json',**self.host)
            self.assertEqual(response.status_code,200)
        self.user.refresh_from_db();self.assertTrue(self.user.notification_push)
        self.assertEqual(PushSubscription.objects.filter(user=self.user,tenant=self.tenant).count(),1)

    def test_invalid_device_does_not_enable_push(self):
        payload=self.payload();payload['endpoint']='https://example.com/not-push'
        response=self.client.post('/notifications/push/subscribe/',json.dumps(payload),content_type='application/json',**self.host)
        self.assertEqual(response.status_code,400)
        self.user.refresh_from_db();self.assertFalse(self.user.notification_push)
        self.assertFalse(PushSubscription.objects.exists())

    def test_background_restore_does_not_override_disabled_preference(self):
        payload=self.payload();payload['enable_push']=False
        response=self.client.post('/notifications/push/subscribe/',json.dumps(payload),content_type='application/json',**self.host)
        self.assertEqual(response.status_code,403)
        self.user.refresh_from_db();self.assertFalse(self.user.notification_push)

    def test_preference_rolls_back_when_subscription_save_fails(self):
        with patch('apps.core.notifications.PushSubscription.objects.update_or_create',side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                self.client.post('/notifications/push/subscribe/',json.dumps(self.payload()),content_type='application/json',**self.host)
        self.user.refresh_from_db();self.assertFalse(self.user.notification_push)

    def test_push_opt_in_works_with_expired_subscription(self):
        sub=sync_subscription(self.tenant);sub.status='suspended';sub.save()
        response=self.client.post('/notifications/push/subscribe/',json.dumps(self.payload()),content_type='application/json',**self.host)
        self.assertEqual(response.status_code,200)
