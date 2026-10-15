from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.tenants.access import (
    can_access_appointment, employee_for_request, permissions_for_request,
    tenant_feature_required, tenant_owner_required, tenant_permission_required,
)
from apps.tenants.models import Membership
from apps.tenants.plans import tenant_has_feature
from apps.core.models import changed_values, log_event, notify, snapshot
from apps.staff.models import Employee, EmployeeService
from apps.catalog.models import WorkPost
from apps.customers.models import Client

from .calendar import build_calendar_context
from .forms import (
    AppointmentEditForm, AppointmentForm, CashShiftCloseForm, CashShiftOpenForm,
    AppointmentDeleteForm, CashCollectionForm, ClientRatingForm, ComplaintForm,
    PaymentSettingsForm, PaymentTransactionForm,
)
from .financial import add_payment_transaction, delete_payment_transaction, recalculate_payment_state
from .models import (
    Appointment, AppointmentAssignee, CashCollection, CashShift,
    ClientRating, Complaint, ComplaintPhoto, PaymentSettings, PaymentTransaction,
)
from .services import create_appointment, move_appointment, move_appointment_post, update_appointment


def _selected_date(value, tenant=None):
    if value:
        try:
            selected = date.fromisoformat(value)
            # The calendar includes links to the previous and next day.
            if date.min < selected < date.max:
                return selected
        except ValueError:
            pass
    if tenant:
        return timezone.now().astimezone(ZoneInfo(tenant.timezone)).date()
    return timezone.localdate()


@tenant_permission_required("appointments_view")
def appointment_calendar(request):
    selected = _selected_date(request.GET.get("date"), request.tenant)
    permissions = permissions_for_request(request)
    employee_scope = None if "appointments_view_all" in permissions else (employee_for_request(request) or False)
    resource_kind = request.GET.get("resource_kind")
    if resource_kind not in {"employee", "post"}:
        resource_kind = request.user.calendar_resource_kind
    context = build_calendar_context(
        tenant=request.tenant, selected_date=selected, resource_kind=resource_kind,
        employee_scope=employee_scope,
        location=request.current_location,
    )
    if request.GET.get("partial") == "1":
        return render(request, "operations/_calendar_board.html", context)
    return render(request, "operations/appointment_calendar.html", context)


@require_POST
@tenant_permission_required("appointments_view")
def calendar_view_select(request):
    kind=request.POST.get("resource_kind")
    if kind not in {"employee", "post"}:
        return JsonResponse({"error":"Неизвестный вид календаря"}, status=400)
    request.user.calendar_resource_kind=kind
    request.user.save(update_fields=("calendar_resource_kind",))
    selected=_selected_date(request.POST.get("date"),request.tenant)
    return redirect(reverse("appointment-calendar")+"?date="+selected.isoformat())


def _assignment_rows(form, post_data=None, appointment=None):
    selected = set(post_data.getlist("employees")) if post_data else set()
    existing = {}
    if appointment and not post_data:
        existing = {
            str(item.employee_id): item.salary_share_percent
            for item in appointment.assignees.all()
        }
        selected = set(existing)
    elif not post_data and form.initial.get("employee"):
        employee_id = getattr(form.initial["employee"], "id", form.initial["employee"])
        selected = {str(employee_id)}
    employees = list(form.fields["employees"].queryset)
    capability_rows = EmployeeService.objects.filter(
        tenant=form.tenant, employee__in=employees,
    ).values_list("employee_id", "service_id")
    capabilities = {}
    capability_mode = EmployeeService.objects.filter(tenant=form.tenant).exists()
    for employee_id, service_id in capability_rows:
        capabilities.setdefault(employee_id, []).append(str(service_id))
    return [{
        "employee": employee,
        "selected": str(employee.id) in selected,
        "share": (post_data.get(f"employee_share_{employee.id}", "") if post_data
                  else existing.get(str(employee.id), "")),
        "service_ids": " ".join(capabilities.get(employee.id, [])),
        "capability_mode": capability_mode,
    } for employee in employees]


def _appointment_create_context(request, form):
    clients = list(Client.objects.filter(
        tenant=request.tenant, archived_at__isnull=True
    ).prefetch_related("cars"))
    client_suggestions = []
    for client in clients:
        active_cars = [car for car in client.cars.all() if car.archived_at is None]
        if not active_cars:
            client_suggestions.append({
                "client_id": str(client.id), "car_id": "", "first_name": client.name,
                "last_name": client.last_name, "phone": client.phone, "plate": "",
                "make": "", "model": "", "car_description": "", "vehicle_class_id": "",
                "label": f"{client.display_name} · {client.phone}",
            })
        for car in active_cars:
            client_suggestions.append({
                "client_id": str(client.id), "car_id": str(car.id), "first_name": client.name,
                "last_name": client.last_name, "phone": client.phone, "plate": car.plate,
                "make": car.make, "model": car.model,
                "car_description": f"{car.make} {car.model}".strip(),
                "vehicle_class_id": str(car.vehicle_class_id or ""),
                "label": f"{client.display_name} · {client.phone} · {car.plate}",
            })
    return {
        "form": form,
        "client_suggestions": client_suggestions,
        "service_rows": form.service_rows,
        "service_price_data": form.service_price_data,
        "service_duration_data": form.service_duration_data,
        "selected_service_ids": (
            request.POST.getlist("services") if request.method == "POST"
            else [str(service.id) for service in form.initial.get("services", [])]
        ),
        "assignment_rows": _assignment_rows(
            form, request.POST if request.method == "POST" else None
        ),
    }


@tenant_permission_required("appointments_manage")
def appointment_create(request):
    initial = {}
    selected = _selected_date(request.GET.get("date"), request.tenant)
    if request.method == "GET":
        tenant_tz = ZoneInfo(request.tenant.timezone)
        try:
            selected_time = time.fromisoformat(request.GET.get("start", "10:00"))
        except ValueError:
            selected_time = time(hour=10)
        selected_start = timezone.make_aware(
            datetime.combine(selected, selected_time), tenant_tz
        )
        initial = {
            "start_at": selected_start,
            "end_at": selected_start + timedelta(hours=1),
        }
        requested_employee = None
        try:
            requested_employee = Employee.objects.filter(
                tenant=request.tenant, location=request.current_location,
                active=True, id=request.GET.get("employee")
            ).first()
        except (ValidationError, ValueError):
            pass
        if requested_employee:
            initial["employee"] = requested_employee
        try:
            requested_post = WorkPost.objects.filter(
                tenant=request.tenant, location=request.current_location,
                active=True, id=request.GET.get("post")
            ).first()
        except (ValidationError, ValueError):
            requested_post = None
        if requested_post:
            initial["post"] = requested_post
    form = AppointmentForm(
        request.POST or None, tenant=request.tenant, location=request.current_location, initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        try:
            appointment = create_appointment(
                tenant=request.tenant, cleaned_data=form.cleaned_data.copy(),
                actor=request.user, location=request.current_location,
            )
        except ValidationError as exc:
            form.add_error("start_at", exc.message)
        else:
            log_event(tenant=request.tenant, actor=request.user, action="create", entity=appointment,
                      description=f"Создана запись на {appointment.start_at:%d.%m.%Y %H:%M}")
            messages.success(request, "Запись создана.")
            if request.GET.get("modal") == "1":
                context = _appointment_context(appointment, request)
                context["appointment_created"] = True
                return render(request, "operations/_appointment_modal_detail.html", context)
            return redirect("appointment-detail", appointment_id=appointment.id)
    context = _appointment_create_context(request, form)
    if request.GET.get("modal") == "1":
        return render(
            request, "operations/_appointment_modal_create.html", context,
            status=422 if request.method == "POST" else 200,
        )
    return render(
        request, "operations/appointment_form.html", context,
    )


@require_POST
@tenant_permission_required("appointments_manage")
def appointment_move(request, appointment_id):
    appointment = get_object_or_404(
        Appointment.objects.select_related("post"), id=appointment_id, tenant=request.tenant,
        location=request.current_location,
    )
    if request.POST.get("resource_kind") == "post":
        try:
            post_id = request.POST.get("post")
            post = get_object_or_404(WorkPost, pk=post_id, tenant=request.tenant,
                                    location=request.current_location, active=True) if post_id else None
            selected = date.fromisoformat(request.POST.get("date", ""))
            selected_time = time.fromisoformat(request.POST.get("start", ""))
            start_at = timezone.make_aware(datetime.combine(selected, selected_time), ZoneInfo(request.tenant.timezone))
            before={"post":str(appointment.post_id or ""), "start":appointment.start_at.isoformat()}
            move_appointment_post(appointment=appointment, post=post, start_at=start_at)
        except (ValueError, ValidationError) as exc:
            return JsonResponse({"ok":False, "error":getattr(exc,"message",None) or str(exc)}, status=400)
        log_event(tenant=request.tenant, actor=request.user, action="move", entity=appointment,
                  description="Запись перемещена между постами", changes={"before":before,"post":str(post.pk if post else ""),"start":start_at.isoformat()})
        return JsonResponse({"ok":True})
    try:
        before = {"employee": str(appointment.employee_id), "start": appointment.start_at.isoformat()}
        employee = Employee.objects.get(
            id=request.POST.get("employee"), tenant=request.tenant,
            location=request.current_location, active=True
        )
        selected = date.fromisoformat(request.POST.get("date", ""))
        selected_time = time.fromisoformat(request.POST.get("start", ""))
        start_at = timezone.make_aware(
            datetime.combine(selected, selected_time), ZoneInfo(request.tenant.timezone)
        )
        assignment = None
        if request.POST.get("assignment"):
            assignment = AppointmentAssignee.objects.get(
                id=request.POST["assignment"], appointment=appointment, tenant=request.tenant
            )
        move_appointment(
            appointment=appointment, employee=employee, start_at=start_at, assignment=assignment
        )
    except (Employee.DoesNotExist, AppointmentAssignee.DoesNotExist, ValueError, ValidationError) as exc:
        message = getattr(exc, "message", None) or str(exc) or "Не удалось перенести запись."
        return JsonResponse({"ok": False, "error": message}, status=400)
    log_event(tenant=request.tenant, actor=request.user, action="move", entity=appointment,
              description="Запись перемещена в календаре",
              changes={"before": before, "employee": str(employee.id), "start": start_at.isoformat()})
    return JsonResponse({"ok": True})


def _appointment_context(appointment, request=None):
    from apps.payroll.services import appointment_commission_rows, appointment_salary, assignment_rows
    client_history = (
        Appointment.objects.filter(
            tenant=appointment.tenant, client=appointment.client,
            status=Appointment.Status.COMPLETED, start_at__lt=appointment.start_at,
        ).exclude(id=appointment.id).select_related("car", "employee", "post").prefetch_related("items").order_by("-start_at")[:10]
    ) if appointment.client_id else []
    consumption_rows = list(appointment.consumption_entries.all())
    if consumption_rows:
        consumables_cost = sum((row.total_cost for row in consumption_rows), Decimal("0"))
    else:
        consumables_cost = sum((
            Decimal(snapshot.get("total_cost", "0"))
            for item in appointment.items.all()
            for snapshot in (item.consumables_snapshot or [])
        ), Decimal("0"))
    salary_cost = sum((
        appointment_salary(appointment, employee, share)
        for employee, share in assignment_rows(appointment)
    ), Decimal("0"))
    salary_cost += sum(
        (amount for _, amount, _ in appointment_commission_rows(appointment)), Decimal("0")
    )
    return {
        "appointment": appointment, "client_history": client_history,
        "appointment_consumables_cost": consumables_cost,
        "appointment_salary_cost": salary_cost,
        "payment_form": PaymentTransactionForm(initial={
            "kind": PaymentTransaction.Kind.PAYMENT,
            "amount": max(appointment.total - appointment.amount_paid, 0),
        }),
        "appointment_rating": ClientRating.objects.filter(appointment=appointment).first(),
        "delete_form": AppointmentDeleteForm(),
        "feedback_url": (request.build_absolute_uri(reverse("public-rating", kwargs={"token": appointment.feedback_token})) if request else reverse("public-rating", kwargs={"token": appointment.feedback_token})),
    }


def _modal_or_redirect(request, appointment):
    if request.GET.get("modal") == "1":
        appointment = Appointment.objects.select_related(
            "client", "car", "employee", "post"
        ).prefetch_related(
            "items__service", "assignees__employee", "payments", "consumption_entries",
            "complaints__service",
        ).get(pk=appointment.pk)
        return render(request, "operations/_appointment_modal_detail.html", _appointment_context(appointment, request))
    return redirect("appointment-detail", appointment_id=appointment.id)


@tenant_permission_required("appointments_view")
def appointment_detail(request, appointment_id):
    appointment = get_object_or_404(
        Appointment.objects.select_related("client", "car", "employee", "post").prefetch_related(
            "items__service", "assignees__employee", "payments", "consumption_entries"
        ),
        id=appointment_id,
        tenant=request.tenant,
        location=request.current_location,
    )
    if not can_access_appointment(request, appointment):
        return HttpResponseForbidden("Доступна только назначенная вам работа")
    context = _appointment_context(appointment, request)
    if request.GET.get("modal") == "1":
        return render(request, "operations/_appointment_modal_detail.html", context)
    return render(
        request,
        "operations/appointment_detail.html",
        context,
    )


@require_POST
@tenant_permission_required("appointments_manage")
@transaction.atomic
def appointment_delete(request, appointment_id):
    appointment = get_object_or_404(
        Appointment.objects.select_for_update(),
        id=appointment_id, tenant=request.tenant, location=request.current_location,
    )
    form = AppointmentDeleteForm(request.POST)
    if appointment.amount_paid > 0:
        messages.error(request, "Сначала оформите полный возврат оплаты.")
        return _modal_or_redirect(request, appointment)
    if not form.is_valid():
        messages.error(request, "Укажите причину удаления записи.")
        return _modal_or_redirect(request, appointment)
    reason = form.cleaned_data["reason"].strip()
    appointment.archived_at = timezone.now()
    appointment.archived_by = request.user
    appointment.archive_reason = reason
    appointment.save(update_fields=("archived_at", "archived_by", "archive_reason", "updated_at"))
    log_event(
        tenant=request.tenant, actor=request.user, action="appointment_delete",
        entity=appointment, description="Запись удалена",
        changes={"reason": {"before": "", "after": reason}},
    )
    for membership in Membership.objects.filter(
        tenant=request.tenant, role=Membership.Role.OWNER, status=Membership.Status.ACTIVE,
    ).select_related("user"):
        notify(
            tenant=request.tenant, recipient=membership.user, kind="appointment_cancelled",
            severity="warning", title="Запись отменена",
            message=(f"{appointment.client_display_name} · "
                     f"{appointment.start_at:%d.%m.%Y %H:%M} · {reason}"),
            url=f"/appointments/?date={appointment.start_at:%Y-%m-%d}",
            dedupe_key=f"appointment-deleted:{appointment.id}",
        )
    if request.GET.get("modal") == "1":
        return render(request, "operations/_appointment_deleted.html", {"appointment": appointment})
    messages.success(request, "Запись удалена.")
    return redirect("appointment-calendar")


@tenant_permission_required("appointments_manage")
def appointment_edit(request, appointment_id):
    appointment = get_object_or_404(
        Appointment.objects.select_related("client", "car", "employee", "post").prefetch_related("items"),
        id=appointment_id, tenant=request.tenant, location=request.current_location,
    )
    form = AppointmentEditForm(
        request.POST or None, instance=appointment, tenant=request.tenant,
        location=request.current_location,
    )
    if request.method == "POST" and form.is_valid():
        tracked = ("client_id", "car_id", "employee_id", "post_id", "start_at", "end_at",
                   "subtotal", "total", "discount_kind", "discount_value", "discount_status", "comment")
        before = snapshot(Appointment.objects.get(pk=appointment.pk), tracked)
        appointment = update_appointment(
            appointment=appointment, cleaned_data=form.cleaned_data.copy(), actor=request.user,
            lock_financial=appointment.status == Appointment.Status.COMPLETED,
        )
        recalculate_payment_state(appointment)
        after = snapshot(appointment, tracked)
        log_event(tenant=request.tenant, actor=request.user, action="update", entity=appointment,
                  description="Изменена карточка записи", changes=changed_values(before, after))
        messages.success(request, "Запись сохранена.")
        if request.GET.get("modal") == "1":
            return render(request, "operations/_appointment_modal_detail.html", _appointment_context(appointment, request))
        return redirect("appointment-detail", appointment_id=appointment.id)
    selected_service_ids = request.POST.getlist("services") if request.method == "POST" else [str(value) for value in form.initial.get("services", [])]
    context = {
        "form": form, "appointment": appointment, "service_rows": form.service_rows,
        "service_price_data": form.service_price_data,
        "service_duration_data": form.service_duration_data,
        "selected_service_ids": selected_service_ids,
        "car_map": {str(car.id): str(car.client_id) for car in form.fields["car"].queryset},
        "car_class_map": {str(car.id): str(car.vehicle_class_id or "") for car in form.fields["car"].queryset},
        "focus_services": request.GET.get("focus") == "services",
        "financial_locked": form.financial_locked,
        "assignment_rows": _assignment_rows(
            form, request.POST if request.method == "POST" else None, appointment
        ),
    }
    if request.GET.get("modal") == "1":
        return render(request, "operations/_appointment_modal_form.html", context, status=422 if request.method == "POST" else 200)
    return render(request, "operations/appointment_edit.html", context)


@require_POST
@tenant_permission_required("appointments_view")
@transaction.atomic
def appointment_transition(request, appointment_id, action):
    appointment = get_object_or_404(
        Appointment.objects.select_for_update(), id=appointment_id, tenant=request.tenant,
        location=request.current_location,
    )
    if not can_access_appointment(request, appointment, manage=True):
        return HttpResponseForbidden("Можно менять статус только своей работы")
    transitions = {
        Appointment.Status.WAITING: {
            "start": Appointment.Status.IN_PROGRESS,
            "no_show": Appointment.Status.NO_SHOW,
        },
        Appointment.Status.NO_SHOW: {"start": Appointment.Status.IN_PROGRESS},
        Appointment.Status.IN_PROGRESS: {"complete": Appointment.Status.COMPLETED},
    }
    new_status = transitions.get(appointment.status, {}).get(action)
    if not new_status:
        raise Http404("Недопустимый переход статуса")
    if new_status == Appointment.Status.COMPLETED and appointment.payment_status != Appointment.PaymentStatus.PAID:
        message = "Сначала отметьте запись как оплаченную. Неоплаченную запись завершить нельзя."
        if request.GET.get("modal") == "1":
            appointment = Appointment.objects.select_related(
                "client", "car", "employee", "post"
            ).prefetch_related("items", "assignees__employee", "payments").get(pk=appointment.pk)
            context = _appointment_context(appointment, request)
            context["transition_error"] = message
            return render(request, "operations/_appointment_modal_detail.html", context, status=422)
        messages.error(request, message)
        return redirect("appointment-detail", appointment_id=appointment.id)
    previous_status = appointment.status
    appointment.status = new_status
    if new_status == Appointment.Status.IN_PROGRESS and not appointment.work_started_at:
        appointment.work_started_at = timezone.now()
    if new_status == Appointment.Status.COMPLETED:
        appointment.completed_at = timezone.now()
    appointment.save(update_fields=("status", "work_started_at", "completed_at", "updated_at"))
    if new_status == Appointment.Status.COMPLETED:
        from apps.inventory.services import write_off_consumables
        write_off_consumables(appointment, request.user)
    log_event(tenant=request.tenant, actor=request.user, action="status", entity=appointment,
              description=f"Статус записи: {appointment.get_status_display()}",
              changes={"status": {"before": previous_status, "after": new_status}})
    messages.success(request, f"Статус записи: {appointment.get_status_display()}.")
    if request.GET.get("modal") == "1":
        appointment = Appointment.objects.select_related("client", "car", "employee", "post").prefetch_related("items").get(pk=appointment.pk)
        return render(request, "operations/_appointment_modal_detail.html", _appointment_context(appointment, request))
    return redirect("appointment-detail", appointment_id=appointment.id)


@require_POST
@tenant_permission_required("appointments_manage")
def appointment_add_payment(request, appointment_id):
    appointment = get_object_or_404(
        Appointment, id=appointment_id, tenant=request.tenant, location=request.current_location,
    )
    form = PaymentTransactionForm(request.POST)
    if form.is_valid():
        try:
            row = add_payment_transaction(appointment=appointment, actor=request.user, **form.cleaned_data)
        except ValidationError as exc:
            messages.error(request, exc.message)
        else:
            log_event(
                tenant=request.tenant, actor=request.user, action=row.kind, entity=appointment,
                description=f"{row.get_kind_display()} {row.amount} ₽, {row.get_method_display()}",
            )
            messages.success(request, "Операция оплаты сохранена.")
    else:
        messages.error(request, "Проверьте сумму и способ оплаты.")
    if request.GET.get("modal") == "1":
        appointment = Appointment.objects.select_related("client", "car", "employee", "post").prefetch_related("items", "assignees__employee", "payments").get(pk=appointment.pk)
        return render(request, "operations/_appointment_modal_detail.html", _appointment_context(appointment, request))
    return redirect("appointment-detail", appointment_id=appointment.id)


@require_POST
@tenant_permission_required("appointments_manage")
def appointment_delete_payment(request, appointment_id, payment_id):
    payment = get_object_or_404(
        PaymentTransaction.objects.select_related("appointment"),
        id=payment_id, appointment_id=appointment_id, tenant=request.tenant,
        appointment__location=request.current_location,
    )
    description = f"{payment.get_kind_display()} {payment.amount} ₽, {payment.get_method_display()}"
    appointment = delete_payment_transaction(payment=payment)
    log_event(
        tenant=request.tenant, actor=request.user, action="payment_delete", entity=appointment,
        description=f"Удалена операция: {description}",
    )
    messages.success(request, "Операция оплаты удалена.")
    return _modal_or_redirect(request, appointment)


@tenant_owner_required
@tenant_feature_required("cash_advanced")
def payment_settings(request):
    settings_row, _ = PaymentSettings.objects.get_or_create(tenant=request.tenant)
    form = PaymentSettingsForm(request.POST or None, instance=settings_row)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_event(tenant=request.tenant, actor=request.user, action="update", entity=settings_row,
                  description="Изменены комиссии способов оплаты")
        messages.success(request, "Комиссии сохранены.")
        return redirect("payment-settings")
    return render(request, "operations/payment_settings.html", {"form": form})


@tenant_permission_required("expenses_manage")
@tenant_feature_required("cash_advanced")
def cash_shift(request):
    active = CashShift.objects.filter(
        tenant=request.tenant, location=request.current_location, closed_at__isnull=True,
    ).first()
    if active:
        cash_rows = active.transactions.filter(method=Appointment.PaymentMethod.CASH)
        incoming = sum((row.amount for row in cash_rows if row.kind == PaymentTransaction.Kind.PAYMENT), Decimal("0"))
        refunds = sum((row.amount for row in cash_rows if row.kind == PaymentTransaction.Kind.REFUND), Decimal("0"))
        collected = sum(active.collections.values_list("amount", flat=True), Decimal("0"))
        live_expected = active.opening_cash + incoming - refunds - collected
        if active.expected_cash != live_expected:
            active.expected_cash = live_expected
            active.save(update_fields=("expected_cash",))
    open_form = CashShiftOpenForm(prefix="open")
    close_form = CashShiftCloseForm(prefix="close")
    collection_form = CashCollectionForm(prefix="collection")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "open" and not active:
            open_form = CashShiftOpenForm(request.POST, prefix="open")
            if open_form.is_valid():
                active = open_form.save(commit=False)
                active.tenant, active.location, active.opened_by = (
                    request.tenant, request.current_location, request.user,
                )
                active.expected_cash = active.opening_cash
                active.save()
                log_event(tenant=request.tenant, actor=request.user, action="cash_shift_open", entity=active,
                          description="Открыта кассовая смена")
                return redirect("cash-shift")
        elif action == "close" and active:
            close_form = CashShiftCloseForm(request.POST, prefix="close")
            if close_form.is_valid():
                cash_rows = active.transactions.filter(method=Appointment.PaymentMethod.CASH)
                incoming = sum((row.amount for row in cash_rows if row.kind == PaymentTransaction.Kind.PAYMENT), Decimal("0"))
                refunds = sum((row.amount for row in cash_rows if row.kind == PaymentTransaction.Kind.REFUND), Decimal("0"))
                collected = sum(active.collections.values_list("amount", flat=True), Decimal("0"))
                active.expected_cash = active.opening_cash + incoming - refunds - collected
                active.actual_cash = close_form.cleaned_data["actual_cash"]
                active.difference = active.actual_cash - active.expected_cash
                active.comment = close_form.cleaned_data["comment"]
                active.closed_at, active.closed_by = timezone.now(), request.user
                active.save()
                log_event(tenant=request.tenant, actor=request.user, action="cash_shift_close", entity=active,
                          description=f"Закрыта кассовая смена, расхождение {active.difference} ₽")
                if active.difference:
                    notify(
                        tenant=request.tenant, kind="cash_difference", severity="critical",
                        title="Расхождение кассовой смены",
                        message=f"Фактическая касса отличается на {active.difference} ₽",
                        url="/cash-shift/", dedupe_key=f"cash-difference:{active.id}",
                    )
                return redirect("cash-shift")
        elif action == "collect" and active:
            if request.membership.role != Membership.Role.OWNER:
                return HttpResponseForbidden("Инкассация доступна только владельцу")
            collection_form = CashCollectionForm(request.POST, prefix="collection")
            if collection_form.is_valid():
                row = collection_form.save(commit=False)
                if row.amount > active.expected_cash:
                    collection_form.add_error("amount", "Сумма не может быть больше ожидаемых наличных в кассе.")
                else:
                    row.tenant, row.cash_shift, row.created_by = request.tenant, active, request.user
                    row.save()
                    active.expected_cash -= row.amount
                    active.save(update_fields=("expected_cash",))
                    log_event(
                        tenant=request.tenant, actor=request.user, action="cash_collection",
                        entity=row, description=f"Инкассация наличных: {row.amount} ₽",
                    )
                    messages.success(request, "Инкассация сохранена.")
                    return redirect("cash-shift")
    history = list(CashShift.objects.filter(
        tenant=request.tenant, location=request.current_location,
    )[:30])
    displayed_expected_cash = (
        active.expected_cash if active
        else history[0].expected_cash if history
        else Decimal("0")
    )
    return render(request, "operations/cash_shift.html", {
        "active_shift": active, "open_form": open_form, "close_form": close_form,
        "collection_form": collection_form, "shift_history": history,
        "displayed_expected_cash": displayed_expected_cash,
    })


def public_rating(request, token):
    appointment = get_object_or_404(
        Appointment.objects.select_related("tenant", "client", "car").prefetch_related("items"),
        feedback_token=token, status=Appointment.Status.COMPLETED,
    )
    rating = ClientRating.objects.filter(appointment=appointment).first()
    if rating and rating.submitted_by_client:
        return render(request, "operations/public_rating.html", {
            "appointment": appointment, "rating": rating, "saved": True,
        })
    form = ClientRatingForm(request.POST or None, instance=rating)
    if request.method == "POST" and form.is_valid():
        rating = form.save(commit=False)
        rating.tenant = appointment.tenant
        rating.appointment = appointment
        rating.created_by = None
        rating.submitted_by_client = True
        rating.save()
        if rating.score <= 3:
            notify(
                tenant=appointment.tenant, kind="low_rating", severity="critical",
                title=f"Низкая оценка: {rating.score} из 5",
                message=f"{appointment.client_display_name} · {rating.comment}",
                url=f"/appointments/{appointment.id}/", dedupe_key=f"rating:{rating.id}:{rating.score}",
            )
        return render(request, "operations/public_rating.html", {
            "appointment": appointment, "rating": rating, "saved": True,
        })
    return render(request, "operations/public_rating.html", {
        "appointment": appointment, "form": form,
    })


@tenant_permission_required("quality_view")
def quality_list(request):
    overdue = Complaint.objects.filter(
        tenant=request.tenant, location=request.current_location, deadline__lt=timezone.localdate()
    ).exclude(status=Complaint.Status.RESOLVED).select_related("appointment__client")
    for row in overdue[:50]:
        notify(
            tenant=request.tenant, kind="overdue_complaint", severity="critical",
            title="Просрочен срок по претензии",
            message=f"{row.appointment.client_display_name} · срок {row.deadline:%d.%m.%Y}",
            url=f"/quality/{row.id}/", dedupe_key=f"overdue-complaint:{row.id}",
        )
    base_complaints = Complaint.objects.filter(
        tenant=request.tenant, location=request.current_location,
    )
    counts = base_complaints.aggregate(
        all=Count("id"),
        open=Count("id", filter=Q(status=Complaint.Status.OPEN)),
        in_progress=Count("id", filter=Q(status=Complaint.Status.IN_PROGRESS)),
        resolved=Count("id", filter=Q(status=Complaint.Status.RESOLVED)),
    )
    complaints = base_complaints.select_related(
        "appointment__client", "appointment__car", "assigned_to", "service"
    ).prefetch_related("photos").annotate(photo_count=Count("photos", distinct=True))
    query = request.GET.get("q", "").strip()
    if query:
        complaints = complaints.filter(
            Q(description__icontains=query)
            | Q(resolution__icontains=query)
            | Q(service__name__icontains=query)
            | Q(appointment__client__name__icontains=query)
            | Q(appointment__client__last_name__icontains=query)
            | Q(appointment__client_name_snapshot__icontains=query)
            | Q(appointment__car__make__icontains=query)
            | Q(appointment__car__model__icontains=query)
            | Q(appointment__car__plate__icontains=query)
            | Q(appointment__car_snapshot__icontains=query)
            | Q(appointment__car_plate_snapshot__icontains=query)
        )
    if request.GET.get("status"):
        complaints = complaints.filter(status=request.GET["status"])
    return render(request, "operations/quality_list.html", {
        "complaints": complaints, "status_filter": request.GET.get("status", ""),
        "status_choices": Complaint.Status.choices, "query": query,
        "complaint_counts": counts,
    })


@require_POST
@tenant_permission_required("quality_manage")
def complaint_status_update(request, complaint_id):
    complaint = get_object_or_404(
        Complaint, id=complaint_id, tenant=request.tenant, location=request.current_location,
    )
    status = request.POST.get("status")
    if status not in Complaint.Status.values:
        return JsonResponse({"error": "Неизвестный статус претензии."}, status=400)
    if complaint.status != status:
        complaint.status = status
        complaint.resolved_by = request.user if status == Complaint.Status.RESOLVED else None
        complaint.save(update_fields=("status", "resolved_by", "updated_at"))
        log_event(
            tenant=request.tenant, actor=request.user, action="complaint_status_update", entity=complaint,
            description=f"Статус претензии: {complaint.get_status_display().lower()}",
        )
    return JsonResponse({"status": complaint.status, "label": complaint.get_status_display()})


@tenant_permission_required("quality_view")
def complaint_detail(request, complaint_id):
    complaint = get_object_or_404(
        Complaint.objects.select_related(
            "appointment__client", "appointment__car", "service", "assigned_to"
        ).prefetch_related("photos"),
        id=complaint_id, tenant=request.tenant, location=request.current_location,
    )
    template = (
        "operations/_complaint_modal_detail.html"
        if request.GET.get("modal") == "1" else "operations/complaint_detail.html"
    )
    return render(request, template, {
        "complaint": complaint,
        "advanced_quality": tenant_has_feature(request.tenant, "quality_advanced"),
    })


def _save_complaint_photos(complaint, photos):
    for photo in photos:
        ComplaintPhoto.objects.create(
            complaint=complaint, file=photo, original_name=photo.name, size=photo.size,
        )


@tenant_permission_required("quality_manage")
def complaint_form(request, complaint_id=None):
    complaint = None
    if complaint_id:
        complaint = get_object_or_404(
            Complaint, id=complaint_id, tenant=request.tenant, location=request.current_location,
        )
    initial = {}
    if not complaint and request.GET.get("appointment"):
        initial["appointment"] = request.GET["appointment"]
    form = ComplaintForm(
        request.POST or None, request.FILES or None, instance=complaint, tenant=request.tenant,
        location=request.current_location,
        advanced=tenant_has_feature(request.tenant, "quality_advanced"),
        initial=initial, existing_photo_count=complaint.photos.count() if complaint else 0,
    )
    if request.method == "POST" and form.is_valid():
        row = form.save(commit=False)
        if row._state.adding:
            row.tenant, row.location, row.created_by = (
                request.tenant, request.current_location, request.user,
            )
        if row.status == Complaint.Status.RESOLVED:
            row.resolved_by = request.user
        row.save()
        _save_complaint_photos(row, form.cleaned_data.get("photos") or [])
        log_event(tenant=request.tenant, actor=request.user, action="complaint_update", entity=row,
                  description=f"Претензия: {row.get_status_display().lower()}")
        if row.status != Complaint.Status.RESOLVED:
            notify(
                tenant=request.tenant, kind="complaint", severity="critical",
                title="Открыта претензия клиента", message=row.description[:240],
                url=f"/quality/{row.id}/", dedupe_key=f"complaint:{row.id}",
            )
        messages.success(request, "Претензия сохранена.")
        return redirect("quality-list")
    return render(request, "operations/complaint_form.html", {
        "form": form, "complaint": complaint,
        "advanced_quality": tenant_has_feature(request.tenant, "quality_advanced"),
    })


@require_POST
@tenant_permission_required("quality_manage")
@tenant_feature_required("quality_advanced")
def complaint_photo_delete(request, complaint_id, photo_id):
    complaint = get_object_or_404(
        Complaint, id=complaint_id, tenant=request.tenant, location=request.current_location,
    )
    photo = get_object_or_404(ComplaintPhoto, id=photo_id, complaint=complaint)
    original_name = photo.original_name
    photo.file.delete(save=False)
    photo.delete()
    log_event(
        tenant=request.tenant, actor=request.user, action="complaint_photo_delete",
        entity=complaint, description=f"Удалена фотография претензии «{original_name}»",
    )
    messages.success(request, "Фотография удалена.")
    return redirect("complaint-edit", complaint_id=complaint.id)
