import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models

from .managers import UserManager


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None
    email = models.EmailField("email", unique=True)
    avatar = models.FileField("фотография профиля", upload_to="profiles/%Y/%m/", blank=True)
    calendar_resource_kind = models.CharField(max_length=8, default="employee", choices=[("employee", "По сотрудникам"), ("post", "По рабочим постам")])
    email_2fa = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    notification_toasts = models.BooleanField(default=True)
    notification_push = models.BooleanField(default=False)
    disabled_notification_kinds = models.JSONField(default=list, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    def save(self, *args, **kwargs):
        self.email = self.email.lower().strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class SecurityChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    tenant = models.ForeignKey('tenants.Tenant', on_delete=models.CASCADE, null=True, blank=True)
    purpose = models.CharField(max_length=16)
    code_hash = models.CharField(max_length=200)
    auth_hash = models.CharField(max_length=128, blank=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    resends = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(auto_now_add=True)


class RecoveryCode(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='recovery_codes')
    code_hash = models.CharField(max_length=64)
    used_at = models.DateTimeField(null=True, blank=True)


class AuthenticationAttempt(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    failures = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class LegalAcceptance(models.Model):
    class DocumentType(models.TextChoices):
        PERSONAL_DATA = "personal_data", "Согласие на обработку персональных данных"
        OFFER = "offer", "Публичная оферта"
        MARKETING = "marketing", "Согласие на рекламные сообщения"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="legal_acceptances")
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="legal_acceptances"
    )
    document_type = models.CharField(max_length=24, choices=DocumentType.choices)
    document_version = models.CharField(max_length=32)
    accepted_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    source = models.CharField(max_length=40, default="company_registration")

    class Meta:
        ordering = ("-accepted_at",)
        indexes = [
            models.Index(fields=("user", "document_type", "accepted_at")),
            models.Index(fields=("tenant", "document_type", "accepted_at")),
        ]
