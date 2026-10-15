from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from datetime import timedelta
from tempfile import TemporaryDirectory

from apps.inventory.models import Consumable, InventoryCategory
from apps.customers.models import Car, CarDamage, CarDamagePhoto, Client
from apps.operations.models import Appointment, Complaint, ComplaintPhoto
from apps.tenants.models import Membership, Tenant

from .models import IntegrationChannel, IntegrationLead, Notification
from .services import ingest_integration_lead


class MediaAccessTests(TestCase):
    def setUp(self):
        self.media_dir = TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        self.media_settings = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)

        self.tenant = Tenant.objects.create(name="Media", slug="media-one")
        self.other_tenant = Tenant.objects.create(name="Other", slug="media-two")
        self.owner = get_user_model().objects.create_user(
            "media-owner@example.com", "safe-test-password"
        )
        Membership.objects.create(
            tenant=self.tenant, user=self.owner, role=Membership.Role.OWNER,
        )
        Membership.objects.create(
            tenant=self.other_tenant, user=self.owner, role=Membership.Role.OWNER,
        )
        self.tenant.logo.save(
            "logo.png", SimpleUploadedFile("logo.png", b"logo", content_type="image/png")
        )
        self.owner.avatar.save(
            "avatar.png", SimpleUploadedFile("avatar.png", b"avatar", content_type="image/png")
        )
        client = Client.objects.create(tenant=self.tenant, name="Client", phone="79991234567")
        car = Car.objects.create(tenant=self.tenant, client=client, make="BMW")
        damage = CarDamage.objects.create(
            tenant=self.tenant, car=car, x_percent=10, y_percent=20,
            discovered_on=timezone.localdate(), description="Scratch",
        )
        self.damage_photo = CarDamagePhoto.objects.create(
            damage=damage,
            file=SimpleUploadedFile("damage.jpg", b"damage", content_type="image/jpeg"),
        )
        start = timezone.now()
        appointment = Appointment.objects.create(
            tenant=self.tenant, client=client, car=car,
            start_at=start, end_at=start + timedelta(hours=1),
        )
        complaint = Complaint.objects.create(
            tenant=self.tenant, appointment=appointment,
            created_by=self.owner, description="Complaint",
        )
        self.complaint_photo = ComplaintPhoto.objects.create(
            complaint=complaint,
            file=SimpleUploadedFile("complaint.jpg", b"complaint", content_type="image/jpeg"),
        )

    def test_private_photos_require_membership_on_their_own_tenant(self):
        for photo in (self.damage_photo, self.complaint_photo):
            path = photo.file.url
            self.client.logout()
            anonymous = self.client.get(path, HTTP_HOST="media-one.localhost")
            self.assertEqual(anonymous.status_code, 403)

            self.client.force_login(self.owner)
            own = self.client.get(path, HTTP_HOST="media-one.localhost")
            self.assertEqual(own.status_code, 200)
            self.assertEqual(own["Cache-Control"], "private, no-store")
            self.assertEqual(b"".join(own.streaming_content),
                             b"damage" if photo == self.damage_photo else b"complaint")

            other = self.client.get(path, HTTP_HOST="media-two.localhost")
            self.assertEqual(other.status_code, 404)

    def test_logo_is_public_only_on_its_tenant_and_avatar_requires_login(self):
        own_logo = self.client.get(self.tenant.logo.url, HTTP_HOST="media-one.localhost")
        self.assertEqual(own_logo.status_code, 200)
        self.assertEqual(b"".join(own_logo.streaming_content), b"logo")
        other_logo = self.client.get(self.tenant.logo.url, HTTP_HOST="media-two.localhost")
        self.assertEqual(other_logo.status_code, 404)

        avatar = self.client.get(self.owner.avatar.url, HTTP_HOST="media-one.localhost")
        self.assertEqual(avatar.status_code, 403)
        self.client.force_login(self.owner)
        avatar = self.client.get(self.owner.avatar.url, HTTP_HOST="media-one.localhost")
        self.assertEqual(avatar.status_code, 200)
        self.assertEqual(b"".join(avatar.streaming_content), b"avatar")


class NotificationCenterTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="LK", slug="lk-notifications")
        self.other_tenant = Tenant.objects.create(name="Other", slug="other-notifications")
        self.owner = get_user_model().objects.create_user(
            "notify@example.com", "safe-test-password"
        )
        Membership.objects.create(
            tenant=self.tenant, user=self.owner, role=Membership.Role.OWNER
        )

    def test_center_generates_stock_alert_and_keeps_tenants_isolated(self):
        category = InventoryCategory.objects.create(
            tenant=self.tenant, name="Химия", unit=InventoryCategory.Unit.LITER
        )
        Consumable.objects.create(
            tenant=self.tenant, category=category, name="Шампунь",
            cost_per_unit=1000, planned_stock=0, actual_stock=0, minimum_stock=1,
        )
        Notification.objects.create(
            tenant=self.other_tenant, kind="secret", title="Чужое уведомление"
        )
        self.client.force_login(self.owner)
        response = self.client.get("/notifications/", HTTP_HOST="lk-notifications.localhost")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Закончился расходник: Шампунь")
        self.assertNotContains(response, "Чужое уведомление")
        row = Notification.objects.get(tenant=self.tenant, kind="stock")
        read = self.client.post(
            f"/notifications/{row.id}/read/", HTTP_HOST="lk-notifications.localhost"
        )
        self.assertEqual(read.status_code, 302)
        row.refresh_from_db()
        self.assertIsNotNone(row.read_at)
        refreshed = self.client.get("/notifications/", HTTP_HOST="lk-notifications.localhost")
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(Notification.objects.filter(tenant=self.tenant, kind="stock").count(), 1)
        self.assertNotContains(refreshed, 'class="nav-badge">1</b>')


class IntegrationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Widget Wash", slug="widget-wash")
        self.owner = get_user_model().objects.create_user(
            "widget-owner@example.com", "safe-test-password"
        )
        Membership.objects.create(
            tenant=self.tenant, user=self.owner, role=Membership.Role.OWNER,
        )

    def test_owner_can_configure_embeddable_widget(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/integrations/settings/",
            {"section": "website", "enabled": "on", "widget_title": "Записаться на мойку"},
            HTTP_HOST="widget-wash.localhost",
        )
        self.assertRedirects(
            response, "/integrations/settings/", fetch_redirect_response=False,
        )
        channel = IntegrationChannel.objects.get(
            tenant=self.tenant, kind=IntegrationChannel.Kind.WEBSITE,
        )
        self.assertTrue(channel.enabled)
        self.assertEqual(channel.settings["widget_title"], "Записаться на мойку")
        page = self.client.get(
            "/integrations/settings/", HTTP_HOST="widget-wash.localhost",
        )
        self.assertContains(page, "https://book.crmdetailing.ru/widget-wash")
        self.assertContains(page, "Avito")
        self.assertContains(page, "Телефония")

    def test_public_widget_rejects_incomplete_booking_without_creating_lead(self):
        channel = IntegrationChannel.objects.create(tenant=self.tenant, kind="website", enabled=True)
        response = self.client.post(f"/booking/{channel.public_token}/", {"client_name":"Иван", "phone":"79991234567"}, HTTP_HOST="widget-wash.localhost")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(IntegrationLead.objects.filter(tenant=self.tenant).exists())

    def test_disabled_widget_is_not_public(self):
        channel = IntegrationChannel.objects.create(
            tenant=self.tenant, kind=IntegrationChannel.Kind.WEBSITE, enabled=False,
        )
        response = self.client.get(
            f"/booking/{channel.public_token}/", HTTP_HOST="widget-wash.localhost",
        )
        self.assertEqual(response.status_code, 404)

    def test_external_connector_retries_are_idempotent(self):
        channel = IntegrationChannel.objects.create(
            tenant=self.tenant, kind=IntegrationChannel.Kind.AVITO, enabled=True,
        )
        first, first_created = ingest_integration_lead(
            tenant=self.tenant, channel=channel, source=IntegrationChannel.Kind.AVITO,
            external_id="avito-42", client_name="Анна", phone="+79990000000",
            payload={"schema_version": 1},
        )
        second, second_created = ingest_integration_lead(
            tenant=self.tenant, channel=channel, source=IntegrationChannel.Kind.AVITO,
            external_id="avito-42", client_name="Анна", phone="+79990000000",
            payload={"schema_version": 1},
        )
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.id, second.id)
        self.assertEqual(IntegrationLead.objects.filter(tenant=self.tenant).count(), 1)
