from datetime import timedelta

from django.db.models import Q
from django.db import transaction
from django.utils import timezone

from apps.customers.models import Client
from apps.inventory.models import Consumable
from apps.operations.models import Appointment, CashShift
from apps.staff.models import Employee

from apps.tenants.models import Membership

from .models import IntegrationChannel, IntegrationLead, management_notification_recipients, notify


def ingest_integration_lead(
    *, tenant, source, client_name, phone, channel=None, external_id="",
    car="", service_name="", preferred_at=None, comment="", payload=None,
):
    """Common idempotent entry point for website, Avito and telephony adapters."""
    from .phones import format_phone
    phone = format_phone(phone)
    if source not in IntegrationChannel.Kind.values:
        raise ValueError("Unknown integration source")
    if channel and (channel.tenant_id != tenant.id or channel.kind != source):
        raise ValueError("Integration channel does not match tenant or source")
    values = {
        "channel": channel,
        "client_name": client_name,
        "phone": phone,
        "car": car,
        "service_name": service_name,
        "preferred_at": preferred_at,
        "comment": comment,
        "payload": payload or {},
    }
    if external_id:
        lead, created = IntegrationLead.objects.get_or_create(
            tenant=tenant, source=source, external_id=external_id, defaults=values,
        )
    else:
        lead = IntegrationLead.objects.create(
            tenant=tenant, source=source, external_id="", **values,
        )
        created = True
    if created:
        owners = tenant.memberships.filter(
            role=Membership.Role.OWNER, status=Membership.Status.ACTIVE,
        ).select_related("user")
        for membership in owners:
            notify(
                tenant=tenant, recipient=membership.user, kind="integration_lead",
                title=f"Новая заявка: {lead.client_name}",
                message=f"{lead.get_source_display()} · {lead.phone}"
                        f"{' · ' + lead.service_name if lead.service_name else ''}",
                url="/integrations/settings/",
            )
    return lead, created


@transaction.atomic
def refresh_operational_notifications(tenant):
    """Materialize current operational risks in the notification center."""
    from apps.tenants.models import Tenant
    Tenant.objects.select_for_update().get(pk=tenant.pk)
    stock_recipients = None
    for item in Consumable.objects.filter(tenant=tenant, active=True).select_related("category").iterator(chunk_size=200):
        if not item.is_low_stock:
            continue
        if item.planned_stock <= 0:
            severity, title = "critical", f"Закончился расходник: {item.name}"
        else:
            severity, title = "warning", f"Низкий остаток: {item.name}"
        if stock_recipients is None:
            stock_recipients = management_notification_recipients(tenant)
        for recipient in stock_recipients:
            notify(
                tenant=tenant, kind="stock", severity=severity, title=title, recipient=recipient,
                message=f"План {item.planned_stock} {item.unit_display}",
                url="/inventory/", dedupe_key=f"stock-state:{item.id}:{severity}",
            )

    now = timezone.now()
    overdue = Appointment.objects.filter(
        tenant=tenant, end_at__lt=now,
    ).exclude(status__in=(Appointment.Status.COMPLETED, Appointment.Status.NO_SHOW)).select_related("client", "car")[:50]
    for appointment in overdue:
        notify(
            tenant=tenant, kind="overdue_appointment", severity="critical",
            title="Запись просрочена",
            message=f"{appointment.client_display_name} · {appointment.car_display} · до {appointment.end_at:%d.%m %H:%M}",
            url=f"/appointments/{appointment.id}/", dedupe_key=f"overdue:{appointment.id}",
        )

    for shift in CashShift.objects.filter(
        tenant=tenant, closed_at__isnull=False
    ).exclude(difference=0)[:30]:
        notify(
            tenant=tenant, kind="cash_difference", severity="critical",
            title="Расхождение кассовой смены",
            message=f"{shift.closed_at:%d.%m.%Y %H:%M} · {shift.difference} ₽",
            url="/cash-shift/", dedupe_key=f"cash-difference:{shift.id}",
        )

    inactivity_boundary = now - timedelta(days=180)
    inactive_clients = Client.objects.filter(
        tenant=tenant, archived_at__isnull=True,
        appointments__status=Appointment.Status.COMPLETED,
    ).exclude(appointments__start_at__gte=inactivity_boundary).distinct()[:30]
    for client in inactive_clients:
        notify(
            tenant=tenant, kind="inactive_client", severity="warning",
            title="Клиент давно не приезжал", message=client.display_name,
            url=f"/clients/{client.id}/", dedupe_key=f"inactive-client:{client.id}:180",
        )

    for employee in Employee.objects.filter(tenant=tenant, active=True):
        rows = Appointment.objects.filter(
            tenant=tenant, status=Appointment.Status.COMPLETED,
            work_started_at__isnull=False, completed_at__isnull=False,
        ).filter(
            Q(employee=employee) | Q(assignees__employee=employee)
        ).prefetch_related("items").distinct().order_by("-completed_at")[:20]
        ratios = []
        for appointment in rows:
            planned = sum((item.duration_minutes_snapshot for item in appointment.items.all()), 0)
            actual = (appointment.completed_at - appointment.work_started_at).total_seconds() / 60
            if planned:
                ratios.append(actual / planned)
        if len(ratios) >= 3 and sum(ratios) / len(ratios) > 1.2:
            excess = round((sum(ratios) / len(ratios) - 1) * 100)
            notify(
                tenant=tenant, kind="employee_time", severity="warning",
                title="Сотрудник превышает норматив времени",
                message=f"{employee.name} · в среднем на {excess}% по последним работам",
                url=f"/employees/{employee.id}/", dedupe_key=f"employee-time:{employee.id}",
            )
