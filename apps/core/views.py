import secrets

from django.conf import settings
from datetime import datetime, timedelta
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.db import models, transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override

from zoneinfo import ZoneInfo, available_timezones

from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from apps.tenants.access import employee_for_request, tenant_member_required, tenant_owner_required
from apps.tenants.models import Location, Membership, SubscriptionHistory, Tenant, ensure_working_hours
from apps.tenants.plans import (
    FEATURES, activate_business_trial, active_location_limit,
    ensure_default_plans, sync_subscription, subscription_features, subscription_limits, tenant_has_feature,
)

from .forms import (
    CompanySettingsForm, InterfaceSettingsForm, PublicBookingForm,
    UserSettingsForm, WebsiteIntegrationForm,
)
from .models import AuditEvent, IntegrationChannel, IntegrationLead, Notification, log_event
from .services import ingest_integration_lead, refresh_operational_notifications
from .legal import LEGAL_DOCUMENTS, LEGAL_VERSION, get_document


def landing(request):
    if request.tenant:
        if request.user.is_authenticated and request.membership:
            return redirect("dashboard")
        return redirect("login")
    return render(request, "landing.html", {
        "primary_domain": settings.PRIMARY_DOMAIN,
        "support_email": settings.SUPPORT_EMAIL,
        "plans": list(ensure_default_plans().values()),
    })


def legal_document(request, document_slug):
    document = get_document(document_slug)
    if document is None:
        raise Http404
    return render(request, "legal/document.html", {
        "document": document,
        "document_version": document["version"],
        "support_email": settings.SUPPORT_EMAIL,
    })


@tenant_member_required
def dashboard(request):
    if request.membership.role == Membership.Role.OWNER:
        return redirect("analytics-dashboard")
    return redirect("appointment-calendar")


@tenant_member_required
def profile_settings(request):
    employee = employee_for_request(request)
    initial_name = employee.name if employee else (request.user.first_name or request.user.email)
    profile_form = UserSettingsForm(instance=request.user, initial={"first_name": initial_name})
    password_form = PasswordChangeForm(request.user)
    if request.method == "POST":
        section = request.POST.get("section")
        if section == "profile":
            profile_form = UserSettingsForm(request.POST, request.FILES, instance=request.user)
            if profile_form.is_valid():
                profile_name = profile_form.cleaned_data["first_name"].strip()
                if employee and employee.tenant.employees.filter(name=profile_name).exclude(pk=employee.pk).exists():
                    profile_form.add_error("first_name", "В компании уже есть сотрудник с таким именем.")
                else:
                    if profile_form.cleaned_data.get("remove_avatar") and request.user.avatar:
                        request.user.avatar.delete(save=False)
                        request.user.avatar = ""
                    profile_form.save()
                    if employee and employee.name != profile_name:
                        employee.name = profile_name
                        employee.save(update_fields=("name", "updated_at"))
                    messages.success(request, "Настройки пользователя сохранены.")
                    log_event(tenant=request.tenant, actor=request.user, action="profile_update",
                              entity=request.user, description="Изменены настройки пользователя")
                    return redirect("profile-settings")
        elif section == "password":
            password_form = PasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Пароль изменён.")
                log_event(tenant=request.tenant, actor=request.user, action="password_update",
                          entity=request.user, description="Пользователь изменил пароль")
                return redirect("profile-settings")
    profession = employee.display_position if employee else request.membership.get_role_display()
    for field in password_form.fields.values():
        field.widget.attrs.pop("autofocus", None)
    return render(request, "core/profile_settings.html", {
        "profile_form": profile_form,
        "password_form": password_form,
        "profession": profession,
    })


@tenant_owner_required
def company_settings(request):
    rows = ensure_working_hours(request.tenant)
    company_form = CompanySettingsForm(instance=request.tenant)
    if request.method == "POST" and request.POST.get("section") == "company":
        company_form = CompanySettingsForm(request.POST, request.FILES, instance=request.tenant)
        if company_form.is_valid():
            if company_form.cleaned_data.get("remove_logo") and request.tenant.logo:
                request.tenant.logo.delete(save=False)
                request.tenant.logo = ""
            company_form.save()
            messages.success(request, "Настройки компании сохранены.")
            log_event(tenant=request.tenant, actor=request.user, action="company_settings_update",
                      entity=request.tenant, description="Изменены данные компании")
            return redirect("company-settings")
    elif request.method == "POST" and request.POST.get("section") == "working-hours":
        errors = []
        parsed = []
        for row in rows:
            is_closed = request.POST.get(f"closed_{row.weekday}") == "1"
            try:
                opens_at = datetime.strptime(
                    request.POST.get(f"opens_{row.weekday}", "08:00"), "%H:%M"
                ).time()
                closes_at = datetime.strptime(
                    request.POST.get(f"closes_{row.weekday}", "22:00"), "%H:%M"
                ).time()
            except ValueError:
                errors.append(f"{row.get_weekday_display()}: проверьте время.")
                continue
            if not is_closed and (opens_at >= closes_at or opens_at.minute % 15 or closes_at.minute % 15):
                errors.append(f"{row.get_weekday_display()}: задайте интервал с шагом 15 минут.")
                continue
            parsed.append((row, opens_at, closes_at, is_closed))
        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            from apps.staff.models import EmployeeShift
            local_today = timezone.now().astimezone(ZoneInfo(request.tenant.timezone)).date()
            with transaction.atomic():
                for row, opens_at, closes_at, is_closed in parsed:
                    row.opens_at, row.closes_at, row.is_closed = opens_at, closes_at, is_closed
                    row.save(update_fields=("opens_at", "closes_at", "is_closed"))
                    shifts = EmployeeShift.objects.filter(
                        tenant=request.tenant, date__gte=local_today, date__week_day=((row.weekday + 1) % 7) + 1,
                    )
                    if is_closed:
                        shifts.delete()
                        continue
                    for shift in shifts:
                        shift.start_time = max(shift.start_time, opens_at)
                        shift.end_time = min(shift.end_time, closes_at)
                        if shift.start_time >= shift.end_time:
                            shift.delete()
                        else:
                            shift.save(update_fields=("start_time", "end_time"))
            messages.success(request, "Часы работы сохранены.")
            log_event(tenant=request.tenant, actor=request.user, action="working_hours_update",
                      entity=request.tenant, description="Изменены часы работы компании")
            return redirect("company-settings")
    return render(request, "core/company_settings.html", {
        "company_form": company_form,
        "working_hours": rows,
        "work_posts": request.tenant.work_posts.filter(location=request.current_location),
    })


@tenant_member_required
def interface_settings(request):
    can_customize_company_interface = request.membership.role == Membership.Role.OWNER
    interface_form = InterfaceSettingsForm(
        request.POST if request.method == "POST" and can_customize_company_interface else None,
        instance=request.tenant,
    )
    if request.method == "POST" and not can_customize_company_interface:
        return HttpResponse("Недостаточно прав.", status=403)
    if request.method == "POST" and interface_form.is_valid():
        interface_form.save()
        messages.success(request, "Настройки интерфейса сохранены.")
        log_event(
            tenant=request.tenant,
            actor=request.user,
            action="interface_settings_update",
            entity=request.tenant,
            description="Изменены язык или цвета интерфейса",
        )
        return redirect("interface-settings")
    return render(request, "core/interface_settings.html", {
        "interface_form": interface_form,
        "can_customize_company_interface": can_customize_company_interface,
    })


@tenant_owner_required
def subscription_settings(request):
    from .billing import subscription_page
    return subscription_page(request)


@tenant_owner_required
def location_settings(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            if not tenant_has_feature(request.tenant, "multi_location"):
                return render(request, "core/tariff_locked.html", {
                    "locked_feature": FEATURES["multi_location"],
                }, status=403)
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Укажите название точки.")
            else:
                counter = request.tenant.locations.count() + 1
                code = f"point-{counter}"
                while request.tenant.locations.filter(code=code).exists():
                    counter += 1
                    code = f"point-{counter}"
                location = Location.objects.create(tenant=request.tenant, name=name, code=code)
                log_event(
                    tenant=request.tenant, actor=request.user, action="location_create",
                    entity=location, description=f"Создана точка «{name}»",
                )
                messages.success(request, "Точка добавлена.")
            return redirect("location-settings")
        if action == "archive":
            location = get_object_or_404(
                Location, tenant=request.tenant, pk=request.POST.get("location_id"),
                is_primary=False,
            )
            if location.appointments.exists() or location.employees.exists():
                messages.error(request, "Нельзя отключить точку, пока к ней привязаны записи или сотрудники.")
            else:
                location.active = False
                location.save(update_fields=("active", "updated_at"))
                messages.success(request, "Точка отключена.")
            return redirect("location-settings")
    return render(request, "core/location_settings.html", {
        "locations": request.tenant.locations.all(),
        "can_manage_multiple": tenant_has_feature(request.tenant, "multi_location"),
        "location_limit": active_location_limit(request.tenant),
    })


@require_POST
@tenant_member_required
def select_location(request):
    location = get_object_or_404(Location, tenant=request.tenant, active=True, pk=request.POST.get("location_id"))
    if request.membership.role != Membership.Role.OWNER:
        allowed = request.membership.location_access.filter(location=location).exists()
        if not allowed:
            employee = employee_for_request(request)
            allowed = bool(employee and employee.location_id == location.id)
        if not allowed:
            return HttpResponse("Нет доступа к точке", status=403)
    request.session["current_location_id"] = str(location.id)
    return redirect(request.POST.get("next") or "dashboard")


@tenant_owner_required
def integration_settings(request):
    channels = {}
    for kind, _label in IntegrationChannel.Kind.choices:
        channels[kind], _ = IntegrationChannel.objects.get_or_create(
            tenant=request.tenant, kind=kind,
        )
    website = channels[IntegrationChannel.Kind.WEBSITE]
    website_form = WebsiteIntegrationForm(channel=website)
    if request.method == "POST":
        section = request.POST.get("section")
        if section == "website":
            website_form = WebsiteIntegrationForm(request.POST, channel=website)
            if website_form.is_valid():
                website.enabled = website_form.cleaned_data["enabled"]
                website.settings = {
                    **(website.settings or {}),
                    "widget_title": website_form.cleaned_data["widget_title"],
                    "version": 1,
                }
                website.save(update_fields=("enabled", "settings", "updated_at"))
                messages.success(request, "Настройки формы онлайн-записи сохранены.")
                log_event(
                    tenant=request.tenant, actor=request.user, action="integration_settings_update",
                    entity=website, description="Изменены настройки интеграции с сайтом",
                    changes={"enabled": website.enabled},
                )
                return redirect("integration-settings")
        elif section == "lead":
            lead = get_object_or_404(
                IntegrationLead, tenant=request.tenant, pk=request.POST.get("lead_id")
            )
            status = request.POST.get("status")
            if status in IntegrationLead.Status.values:
                lead.status = status
                lead.save(update_fields=("status", "updated_at"))
            return redirect("integration-settings")
    from .booking_links import booking_url
    widget_url = booking_url(request.tenant)
    embed_code = (
        f'<iframe src="{widget_url}" title="Онлайн-запись {request.tenant.name}" '
        'width="100%" height="720" style="border:0;border-radius:20px" loading="lazy"></iframe>'
    )
    return render(request, "core/integration_settings.html", {
        "website_form": website_form,
        "website": website,
        "channels": channels,
        "widget_url": widget_url,
        "embed_code": embed_code,
        "leads": IntegrationLead.objects.filter(tenant=request.tenant)[:50],
    })


@csrf_exempt
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def booking_widget(request, token):
    import uuid
    from django.core import signing
    from django.core.exceptions import ValidationError
    from .booking import resolve_channel, booking_catalog, OnlineBookingForm, submit_booking, nearest_availability
    channel = resolve_channel(token)
    tenant = channel.tenant
    submitted = False
    appointment = None
    with override(tenant.language_code), timezone.override(tenant.timezone):
        initial_token = signing.dumps({"channel": str(channel.pk), "nonce": str(uuid.uuid4())}, salt="online-booking")
        _, services, _, catalog = booking_catalog(channel)
        today=datetime.fromisoformat(catalog['today']).date()
        initial_day,_=nearest_availability(tenant,services,today,any_service=True) if services else (None,[])
        catalog['initial_date']=(initial_day or today).isoformat()
        form = OnlineBookingForm(request.POST or None, channel=channel, initial={"submission_token":initial_token,'date':initial_day or today})
        if request.method == "POST" and not request.POST.get("website") and form.is_valid():
            try:
                appointment = submit_booking(channel, form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                submitted = True
    from .booking_appearance import appearance_context
    from .booking_location import location_context
    with override(tenant.language_code), timezone.override(tenant.timezone):
        return render(request, "core/booking_widget.html", {**appearance_context(channel), **location_context(tenant), "tenant":tenant, "form":form,
            "submitted":submitted, "appointment":appointment, "catalog":catalog,
            "booking_disabled": sync_subscription(tenant).status in {"suspended","cancelled"},
            "widget_title":(channel.settings or {}).get("widget_title", "Онлайн-запись")})


def health(request):
    return JsonResponse({"status": "ok"})


@require_http_methods(["GET"])
def tls_domain_allowed(request):
    supplied_token = request.GET.get("token", "")
    if not settings.TLS_ASK_TOKEN or not secrets.compare_digest(supplied_token, settings.TLS_ASK_TOKEN):
        return HttpResponse(status=403)
    domain = request.GET.get("domain", "").strip().lower().rstrip(".")
    suffix = f".{settings.PRIMARY_DOMAIN}"
    if not domain.endswith(suffix):
        return HttpResponse(status=404)
    slug = domain[: -len(suffix)]
    if domain in {settings.PLATFORM_DOMAIN, settings.BOOKING_DOMAIN, settings.SUPPORT_DOMAIN}:
        return HttpResponse('allowed',content_type='text/plain')
    if not slug or "." in slug or slug in settings.RESERVED_SUBDOMAINS:
        return HttpResponse(status=404)
    if not Tenant.objects.filter(slug=slug).exists():
        return HttpResponse(status=404)
    return HttpResponse("allowed", content_type="text/plain")


@tenant_owner_required
def audit_log(request):
    events = AuditEvent.objects.filter(tenant=request.tenant)
    if not tenant_has_feature(request.tenant, "audit_full"):
        events = events.filter(created_at__gte=timezone.now() - timedelta(days=30))
    events = events.exclude(
        action__startswith="extra_work_"
    ).exclude(entity_type="AppointmentExtraWork").select_related("actor")[:300]
    action_labels = {
        "create": "Создание", "update": "Изменение", "archive": "Удаление",
        "appointment_delete": "Удаление записи", "move": "Перенос записи",
        "status": "Изменение статуса", "payment_status": "Изменение оплаты",
        "payment": "Оплата", "refund": "Возврат", "payment_delete": "Удаление оплаты",
        "cash_shift_open": "Открытие смены",
        "rating": "Оценка клиента",
        "cash_shift_close": "Закрытие смены", "cash_collection": "Инкассация",
        "client_create": "Создание клиента", "client_update": "Изменение клиента",
        "client_archive": "Удаление клиента", "client_restore": "Восстановление клиента", "car_create": "Создание автомобиля",
        "car_update": "Изменение автомобиля", "car_archive": "Удаление автомобиля", "car_restore": "Восстановление автомобиля",
        "car_damage_create": "Добавление повреждения", "car_damage_update": "Изменение повреждения",
        "car_damage_archive": "Удаление повреждения", "car_damage_photo_delete": "Удаление фотографии повреждения",
        "complaint_update": "Изменение претензии", "complaint_photo_delete": "Удаление фотографии претензии",
        "employee_create": "Создание сотрудника", "employee_update": "Изменение сотрудника",
        "role_create": "Создание роли", "role_update": "Изменение роли",
        "service_create": "Создание услуги", "service_update": "Изменение услуги",
        "service_price_update": "Изменение цены услуги", "post_create": "Создание рабочего поста",
        "post_update": "Изменение рабочего поста", "vehicle_class_create": "Создание класса автомобиля",
        "vehicle_class_update": "Изменение класса автомобиля", "expense_create": "Добавление затрат",
        "inventory": "Изменение остатка", "schedule_update": "Изменение графика",
        "schedule_copy": "Копирование графика",
        "timezone_update": "Изменение часового пояса", "working_hours_update": "Изменение часов работы",
        "profile_update": "Изменение профиля", "password_update": "Смена пароля",
        "company_settings_update": "Изменение настроек компании",
        "interface_settings_update": "Изменение интерфейса",
        "integration_settings_update": "Изменение интеграции",
        "integration_lead_create": "Получение внешней заявки",
    }
    entity_labels = {
        "Appointment": "Запись", "Client": "Клиент", "Car": "Автомобиль",
        "CarDamage": "Повреждение автомобиля", "Complaint": "Претензия",
        "Employee": "Сотрудник", "CompanyRole": "Роль", "Service": "Услуга",
        "WorkPost": "Рабочий пост", "VehicleClass": "Класс автомобиля",
        "Expense": "Затрата", "InventoryMovement": "Движение расходника",
        "Consumable": "Расходник", "ClientRating": "Оценка клиента",
        "PaymentSettings": "Настройки оплаты", "ComplaintPhoto": "Фотография претензии",
        "CashShift": "Кассовая смена", "CashCollection": "Инкассация",
        "PaymentTransaction": "Платёж",
        "Tenant": "Компания", "User": "Пользователь",
        "IntegrationChannel": "Канал интеграции", "IntegrationLead": "Внешняя заявка",
    }
    field_labels = {
        "client_id": "Клиент", "car_id": "Автомобиль", "employee_id": "Сотрудник",
        "employee": "Сотрудник", "post_id": "Рабочий пост", "start_at": "Начало",
        "start": "Начало", "end_at": "Окончание", "subtotal": "Стоимость до скидки",
        "total": "Стоимость", "discount_kind": "Вид скидки", "discount_value": "Скидка",
        "discount_status": "Статус скидки", "comment": "Комментарий", "status": "Статус",
        "payment_status": "Статус оплаты", "reason": "Причина", "before": "До изменения",
    }
    value_labels = {
        "waiting": "Ожидание", "in_progress": "В работе", "completed": "Завершено",
        "no_show": "Не пришёл", "unpaid": "Не оплачено", "partial": "Частично оплачено",
        "paid": "Оплачено", "refunded": "Возвращено", "none": "Нет",
        "percent": "Процент", "fixed": "Сумма", "applied": "Применена",
    }
    def display(value):
        if value is None or value == "":
            return "—"
        if isinstance(value, (dict, list)):
            return str(value).replace("'", "")
        return value_labels.get(str(value), str(value))
    for event in events:
        event.action_label = action_labels.get(event.action, "Другое действие")
        event.entity_label = entity_labels.get(event.entity_type, "Объект CRM")
        rows = []
        for field, value in (event.changes or {}).items():
            if isinstance(value, dict) and ("before" in value or "after" in value):
                before, after = display(value.get("before")), display(value.get("after"))
            else:
                before, after = "—", display(value)
            rows.append((field_labels.get(field, "Изменение"), before, after))
        event.change_rows = rows
    return render(request, "audit/log.html", {"events": events})


@tenant_member_required
def notification_list(request):
    refresh_operational_notifications(request.tenant)
    from .notifications import user_notifications
    notifications = user_notifications(request)
    return render(request, "notifications/list.html", {
        "notifications": notifications[:100],
        "automation_enabled": tenant_has_feature(request.tenant, "notifications_automation"),
    })


@require_POST
@tenant_member_required
def notification_read(request, notification_id=None):
    rows = Notification.objects.filter(tenant=request.tenant).exclude(kind="extra_work").filter(
        models.Q(recipient__isnull=True) | models.Q(recipient=request.user)
    )
    if notification_id:
        get_object_or_404(rows, id=notification_id).mark_read()
    else:
        rows.filter(read_at__isnull=True).update(read_at=timezone.now())
    if request.headers.get('X-Requested-With')=='XMLHttpRequest':
        from .notifications import user_notifications
        return JsonResponse({'unread':user_notifications(request).filter(read_at__isnull=True).count()})
    return redirect("notification-list")


@require_POST
@tenant_owner_required
def detect_timezone(request):
    timezone_name = request.POST.get("timezone", "")
    if timezone_name not in available_timezones():
        return JsonResponse({"ok": False, "error": "Неизвестный часовой пояс"}, status=400)
    request.tenant.timezone = timezone_name
    request.tenant.timezone_auto_detected = True
    request.tenant.save(update_fields=("timezone", "timezone_auto_detected"))
    log_event(tenant=request.tenant, actor=request.user, action="timezone_update", entity=request.tenant,
              description=f"Определён часовой пояс {timezone_name}")
    return JsonResponse({"ok": True, "timezone": timezone_name})


def local_tenant_entry(request, slug):
    if not settings.DEBUG:
        raise Http404
    if not Tenant.objects.filter(slug=slug).exists():
        raise Http404("Компания не найдена")
    request.session["development_tenant_slug"] = slug
    return redirect("login")


def local_tenant_exit(request):
    if not settings.DEBUG:
        raise Http404
    request.session.pop("development_tenant_slug", None)
    return redirect("landing")
