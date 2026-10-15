import uuid

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.tenants.models import Tenant


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="audit_events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="audit_events",
    )
    action = models.CharField(max_length=80)
    entity_type = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=80, blank=True)
    description = models.CharField(max_length=300)
    changes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("tenant", "created_at"))]


def log_event(*, tenant, actor, action, entity, description, changes=None):
    return AuditEvent.objects.create(
        tenant=tenant,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        entity_type=entity.__class__.__name__,
        entity_id=str(getattr(entity, "pk", "")),
        description=description,
        changes=changes or {},
    )


def snapshot(instance, fields):
    return {field: str(getattr(instance, field, "")) for field in fields}


def changed_values(before, after):
    return {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in set(before) | set(after)
        if before.get(key) != after.get(key)
    }


class Notification(models.Model):
    class Severity(models.TextChoices):
        INFO = "info", "Информация"
        WARNING = "warning", "Предупреждение"
        CRITICAL = "critical", "Критично"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="notifications")
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
        related_name="crm_notifications",
    )
    severity = models.CharField(max_length=12, choices=Severity.choices, default=Severity.INFO)
    kind = models.CharField(max_length=40)
    title = models.CharField(max_length=180)
    message = models.CharField(max_length=300, blank=True)
    url = models.CharField(max_length=300, blank=True)
    dedupe_key = models.CharField(max_length=180, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("tenant", "read_at", "created_at"))]

    def mark_read(self):
        if not self.read_at:
            self.read_at = timezone.now()
            self.save(update_fields=("read_at",))


class IntegrationChannel(models.Model):
    class Kind(models.TextChoices):
        WEBSITE = "website", "Сайт"
        AVITO = "avito", "Avito"
        TELEPHONY = "telephony", "Телефония"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="integration_channels")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    enabled = models.BooleanField(default=False)
    public_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    background_image = models.FileField("фон формы записи", upload_to="booking-backgrounds/%Y/%m/", blank=True)
    settings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("kind",)
        constraints = [
            models.UniqueConstraint(fields=("tenant", "kind"), name="unique_integration_channel")
        ]

    def __str__(self):
        return f"{self.tenant}: {self.get_kind_display()}"


class IntegrationLead(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новая"
        PROCESSED = "processed", "Обработана"
        REJECTED = "rejected", "Отклонена"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="integration_leads")
    channel = models.ForeignKey(
        IntegrationChannel, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="leads",
    )
    source = models.CharField(max_length=24, choices=IntegrationChannel.Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    external_id = models.CharField(max_length=160, blank=True)
    client_name = models.CharField(max_length=160)
    phone = models.CharField(max_length=40)
    phone_needs_review = models.BooleanField(default=False)
    car = models.CharField(max_length=180, blank=True)
    service_name = models.CharField(max_length=180, blank=True)
    preferred_at = models.DateTimeField(null=True, blank=True)
    comment = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("tenant", "source", "status", "created_at"))]
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "source", "external_id"),
                condition=~models.Q(external_id=""),
                name="unique_external_lead_per_source",
            )
        ]

    def __str__(self):
        return f"{self.get_source_display()}: {self.client_name}"


def notify(*, tenant, kind, title, message="", url="", severity="info", recipient=None, dedupe_key=""):
    if recipient is not None:
        recipients = [recipient]
    else:
        recipients = [row.user for row in tenant.memberships.select_related("user").filter(status='active', user__is_active=True)]
    created = []
    for user in recipients or [None]:
        if user and kind in (user.disabled_notification_kinds or []):
            continue
        if dedupe_key and Notification.objects.filter(
            tenant=tenant, recipient=user, dedupe_key=dedupe_key
        ).exists():
            continue
        notification = Notification.objects.create(
            tenant=tenant, recipient=user, severity=severity, kind=kind,
            title=title, message=message, url=url, dedupe_key=dedupe_key,
        )
        created.append(notification)
        if user and user.notification_push:
            from .tasks import deliver_push
            transaction.on_commit(lambda pk=str(notification.pk): deliver_push.delay(pk), robust=True)
    return created[0] if len(created) == 1 else created


def management_notification_recipients(tenant):
    """Owners, legacy administrators and custom roles allowed to manage inventory."""
    from apps.staff.models import Employee
    from apps.tenants.models import Membership

    users = {
        row.user for row in tenant.memberships.select_related("user").filter(
            status=Membership.Status.ACTIVE,
            role__in=(Membership.Role.OWNER, Membership.Role.ADMIN),
        )
    }
    for employee in Employee.objects.filter(
        tenant=tenant, active=True, user__isnull=False, company_role__active=True,
    ).select_related("user", "company_role"):
        if "inventory_manage" in (employee.company_role.permissions or []):
            users.add(employee.user)
    return users


class ImportBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='import_batches')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    location = models.ForeignKey('tenants.Location', on_delete=models.PROTECT)
    kind = models.CharField(max_length=16)
    filename = models.CharField(max_length=160)
    columns = models.JSONField(default=list)
    rows = models.JSONField(default=list)
    mapping = models.JSONField(default=dict)
    duplicate_mode = models.CharField(max_length=12, default='skip')
    status = models.CharField(max_length=16, default='uploaded')
    results = models.JSONField(default=list)
    changes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    undone_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-created_at',)


class PushSubscription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)


class WebPushConfig(models.Model):
    """Server-only VAPID keypair; never included in company exports."""
    singleton = models.PositiveSmallIntegerField(primary_key=True, default=1)
    private_key = models.TextField()
    public_key = models.TextField()


class LegalDocument(models.Model):
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=255)
    content = models.TextField()
    version = models.CharField(max_length=32)
    updated_at = models.DateTimeField(auto_now=True)


class LegalDocumentRevision(models.Model):
    document = models.ForeignKey(LegalDocument, on_delete=models.CASCADE, related_name='revisions')
    title = models.CharField(max_length=255)
    content = models.TextField()
    version = models.CharField(max_length=32)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at', '-pk')
        constraints = [models.UniqueConstraint(fields=('document', 'version'), name='unique_legal_document_revision')]


class KnowledgeCategory(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField('название', max_length=120)
    description = models.CharField('описание', max_length=240, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ('sort_order', 'name')

    def __str__(self):
        return self.name


class KnowledgeArticle(models.Model):
    category = models.ForeignKey(KnowledgeCategory, on_delete=models.PROTECT, related_name='articles', verbose_name='раздел')
    slug = models.SlugField('адрес статьи', unique=True, max_length=120)
    title = models.CharField('заголовок', max_length=180)
    summary = models.CharField('краткое описание', max_length=300)
    content = models.TextField('текст статьи')
    screenshot = models.FileField('скриншот', upload_to='knowledge/%Y/%m/', blank=True)
    default_screenshot = models.CharField(max_length=240, blank=True)
    screenshot_caption = models.CharField('подпись к скриншоту', max_length=240, blank=True)
    published = models.BooleanField('опубликована', default=False)
    featured = models.BooleanField('показывать в популярных статьях', default=False)
    sort_order = models.PositiveSmallIntegerField('порядок', default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('category__sort_order', 'sort_order', 'title')

    def __str__(self):
        return self.title
