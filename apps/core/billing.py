import json
import uuid
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from .phones import PhoneField
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from apps.tenants.models import Plan, NetworkRequest, SubscriptionOrder
from apps.tenants.plans import ensure_default_plans, create_order, order_quote, counted_employees
from apps.tenants.access import tenant_owner_required
from .yookassa import YooKassaError, create_checkout_payment, is_configured, sync_payment


class CheckoutForm(forms.Form):
    plan = forms.ChoiceField(label="Пакет", choices=[("start", "Команда"), ("business", "Студия"), ("center", "Центр")])
    months = forms.TypedChoiceField(label="Период", coerce=int, choices=[(1, "1 месяц"), (6, "6 месяцев"), (12, "12 месяцев")])
    promo = forms.CharField(label="Промокод", max_length=40, required=False)
    auto_renew = forms.BooleanField(
        label="Автоматически продлевать подписку",
        required=False,
        help_text="Способ оплаты сохранится в ЮKassa. Автопродление можно отключить в любой момент.",
    )


class NetworkRequestForm(forms.ModelForm):
    phone = PhoneField(label='Телефон')
    class Meta:
        model = NetworkRequest
        fields = ("company_name", "contact_name", "email", "phone", "locations", "comment")
        labels = {"company_name": "Компания", "contact_name": "Ваше имя", "email": "Email", "phone": "Телефон", "locations": "Количество точек", "comment": "Комментарий"}
        widgets = {"comment": forms.Textarea(attrs={"rows": 3})}
    def clean_phone(self):
        return self.cleaned_data['phone']

    def clean_locations(self):
        value = self.cleaned_data['locations']
        if value < 2:
            raise forms.ValidationError("Для сети нужно не менее двух точек.")
        return value


def subscription_page(request):
    plans = ensure_default_plans()
    if request.method == "POST" and request.POST.get("action") == "disable-auto-renew":
        request.subscription.auto_renew = False
        request.subscription.provider_payment_method_id = ""
        request.subscription.save(update_fields=("auto_renew", "provider_payment_method_id", "updated_at"))
        messages.success(request, "Автопродление отключено.")
        return redirect("subscription-settings")
    form = CheckoutForm(request.POST or None, initial={
        "plan": request.subscription.plan_id if request.subscription.plan_id != "network" else "center",
        "months": 6,
        "auto_renew": request.subscription.auto_renew,
    })
    quote = None
    if request.method == "POST" and form.is_valid():
        plan = plans[form.cleaned_data['plan']]
        if plan.limits['active_employees'] < counted_employees(request.tenant).count():
            form.add_error('plan', 'В компании больше активных сотрудников, чем позволяет этот пакет. Сначала отключите лишние карточки.')
        else:
            try:
                subtotal, discount, total, promo = order_quote(request.tenant, plan, form.cleaned_data['months'], form.cleaned_data['promo'])
                quote = {"subtotal": subtotal, "discount": discount, "total": total}
                if request.POST.get('action') == 'order':
                    if not is_configured():
                        form.add_error(None, "Онлайн-оплата временно недоступна: ЮKassa ещё не настроена.")
                    else:
                        order = create_order(
                            request.tenant,
                            plan,
                            form.cleaned_data['months'],
                            form.cleaned_data['promo'],
                            provider="yookassa",
                            auto_renew_requested=form.cleaned_data['auto_renew'],
                        )
                        return_url = request.build_absolute_uri(reverse("yookassa-return"))
                        return_url += "?" + urlencode({"order": str(order.pk)})
                        try:
                            confirmation_url = create_checkout_payment(order, return_url=return_url)
                        except YooKassaError as exc:
                            order.status = SubscriptionOrder.Status.CANCELLED
                            order.provider_status = "error"
                            order.save(update_fields=("status", "provider_status"))
                            form.add_error(None, str(exc))
                        else:
                            return redirect(confirmation_url)
            except ValueError as exc:
                form.add_error(None, str(exc))
    return render(request, 'core/subscription_settings.html', {"subscription": request.subscription,
        "plans": list(plans.values()), "form": form, "quote": quote,
        "employee_count": counted_employees(request.tenant).count(),
        "orders": SubscriptionOrder.objects.filter(tenant=request.tenant).select_related('plan')[:20],
        "history": request.subscription.history.select_related('from_plan', 'to_plan')[:20],
        "yookassa_configured": is_configured(),
        "yookassa_test_mode": settings.YOOKASSA_TEST_MODE,
    })


@tenant_owner_required
def yookassa_return(request):
    try:
        order_id = uuid.UUID(str(request.GET.get("order")))
    except (ValueError, TypeError):
        raise Http404
    order = get_object_or_404(
        SubscriptionOrder,
        pk=order_id,
        tenant=request.tenant,
        provider="yookassa",
    )
    if order.provider_payment_id:
        try:
            status = sync_payment(order.provider_payment_id)
        except YooKassaError as exc:
            messages.warning(request, f"Проверяем оплату: {exc}")
        else:
            if status == "succeeded":
                messages.success(request, "Оплата получена. Подписка обновлена.")
            elif status == "canceled":
                messages.error(request, "Платёж отменён.")
            else:
                messages.info(request, "Платёж обрабатывается. Статус обновится автоматически.")
    return redirect("subscription-settings")


@csrf_exempt
@require_POST
def yookassa_webhook(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        event = payload.get("event")
        payment_id = str((payload.get("object") or {}).get("id") or "")
        if event not in {"payment.succeeded", "payment.canceled"} or not payment_id:
            return HttpResponse(status=400)
        sync_payment(payment_id)
    except (ValueError, UnicodeDecodeError, YooKassaError):
        return HttpResponse(status=400)
    return HttpResponse(status=200)


@require_http_methods(['GET', 'POST'])
def network_request(request):
    form = NetworkRequestForm(request.POST or None, initial={"company_name": request.tenant.name if request.tenant else '', "contact_name": request.user.first_name if request.user.is_authenticated else '', "email": request.user.email if request.user.is_authenticated else ''})
    if request.method == 'POST' and not request.POST.get('website') and form.is_valid():
        obj = form.save(commit=False)
        obj.tenant = request.tenant
        obj.save()
        return render(request, 'core/network_request.html', {'submitted': True})
    return render(request, 'core/network_request.html', {'form': form})


from apps.tenants.access import tenant_owner_required

@tenant_owner_required
def company_export(request):
    import io, zipfile
    from django.apps import apps
    from django.core import serializers
    from django.http import HttpResponse
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for model in apps.get_models():
            if model._meta.app_label == 'accounts' or model._meta.label_lower in {'core.pushsubscription','core.webpushconfig'}:
                continue
            if any(field.name == 'tenant' for field in model._meta.fields):
                manager = getattr(model, 'all_objects', model.objects)
                rows = manager.filter(tenant=request.tenant)
                archive.writestr(f'{model._meta.app_label}-{model._meta.model_name}.json', serializers.serialize('json', rows))
    response = HttpResponse(output.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{request.tenant.slug}-export.zip"'
    return response
