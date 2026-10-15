import hashlib
import secrets
from datetime import timedelta
from functools import wraps
from decimal import Decimal
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.hashers import make_password, check_password
from django.core.mail import send_mail, get_connection
from django.db import transaction
from django.db.models import Sum, Count
from django.db.models.functions import TruncMonth
from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.views import PasswordResetView, PasswordResetDoneView, PasswordResetConfirmView, PasswordResetCompleteView
from django.urls import reverse_lazy
from django.views.decorators.cache import never_cache
from apps.tenants.models import slug_validator
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from apps.tenants.models import (PlatformAccess, PlatformLoginAttempt, PlatformLoginChallenge, PlatformAudit,
    Tenant, Subscription, SubscriptionOrder, PromoCode, NetworkRequest, SubscriptionHistory)
from apps.tenants.plans import confirm_order, ensure_default_plans, counted_employees


def platform_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.tenant:
            raise Http404
        access = PlatformAccess.objects.filter(user=request.user, active=True).first() if request.user.is_authenticated else None
        if not access:
            return redirect('platform-login')
        if (access.email_2fa or request.user.email_2fa) and request.session.get('platform_2fa_user') != str(request.user.pk):
            return redirect('platform-login')
        return view(request, *args, **kwargs)
    return wrapped


class PlatformLoginForm(forms.Form):
    email = forms.EmailField(label='Email')
    password = forms.CharField(label='Пароль', widget=forms.PasswordInput)


def platform_login(request):
    if request.tenant:
        raise Http404
    from apps.accounts.security import issue_challenge, login_blocked, failed_login, attempt_key
    from apps.accounts.models import AuthenticationAttempt
    form = PlatformLoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        email=form.cleaned_data['email'].lower().strip()
        if login_blocked(request,email):
            form.add_error(None,'Слишком много попыток. Повторите через 15 минут.')
        else:
            access = PlatformAccess.objects.select_related('user').filter(email__iexact=email, active=True).first()
            if access is None:
                access = PlatformAccess.objects.select_related('user').filter(email__isnull=True, user__email__iexact=email, active=True).first()
            user = access.user if access and access.user.is_active and access.user.check_password(form.cleaned_data['password']) else None
            if not user:
                access = None
            if not access:
                failed_login(request,email);form.add_error(None,'Неверный email или пароль.')
            elif user.email_2fa or access.email_2fa:
                try: challenge=issue_challenge(user,'platform',allow_fallback=True)
                except ValueError as exc: form.add_error(None,str(exc))
                else:
                    AuthenticationAttempt.objects.filter(key=attempt_key(request,email)).delete()
                    request.session['login_challenge']=str(challenge.pk)
                    if challenge.delivery_failed: messages.warning(request,'Письмо не отправлено. Используйте резервный код или повторите отправку позже.')
                    return redirect('platform-verify')
            else:
                AuthenticationAttempt.objects.filter(key=attempt_key(request,email)).delete()
                login(request,user,backend='django.contrib.auth.backends.ModelBackend');request.session.set_expiry(3600)
                request.session['platform_2fa_user']=str(user.pk)
                request.session['account_2fa_user']=str(user.pk)
                PlatformAudit.objects.create(actor=user,action='login',details={})
                return redirect('platform-dashboard')
    return render(request,'platform/login.html',{'form':form})


def platform_verify(request):
    from apps.accounts.security import verify_login
    return verify_login(request,platform=True)


@platform_required
def platform_dashboard(request):
    if request.method == 'POST':
        if request.POST.get('action') == 'confirm':
            order = get_object_or_404(SubscriptionOrder, pk=request.POST.get('order_id'))
            try:
                confirm_order(order, request.user)
                PlatformAudit.objects.create(actor=request.user, action='confirm_payment', details={'order': str(order.pk), 'tenant': str(order.tenant_id), 'total': str(order.total)})
                messages.success(request, 'Оплата подтверждена, подписка обновлена.')
            except ValueError as exc:
                messages.error(request, str(exc))
        elif request.POST.get('action') == 'network-status':
            obj = get_object_or_404(NetworkRequest, pk=request.POST.get('request_id'))
            status = request.POST.get('status')
            if status in NetworkRequest.Status.values:
                obj.status = status
                obj.save()
                PlatformAudit.objects.create(actor=request.user, action='network_request_update', details={'request': str(obj.pk), 'status': status})
        return redirect('platform-dashboard')
    from apps.tenants.plans import sync_subscription
    companies = Tenant.objects.select_related('subscription__plan').all()
    for company in companies:
        company.subscription = sync_subscription(company)
        company.staff_count = counted_employees(company).count()
    return render(request, 'platform/dashboard.html', {'companies': companies, 'orders': SubscriptionOrder.objects.select_related('tenant', 'plan').filter(status='pending', provider='manual'), 'network_requests': NetworkRequest.objects.select_related('tenant')[:100], 'audit': PlatformAudit.objects.select_related('actor')[:30]})


class SubscriptionAdminForm(forms.Form):
    slug = forms.CharField(label="Поддомен компании", max_length=63, validators=[slug_validator], help_text="После изменения вход будет доступен по новому адресу.")

    def __init__(self, *args, tenant=None, **kwargs):
        self.tenant = tenant
        super().__init__(*args, **kwargs)

    def clean_slug(self):
        slug = self.cleaned_data["slug"].lower().strip()
        if slug in settings.RESERVED_SUBDOMAINS or Tenant.objects.filter(slug=slug).exclude(pk=self.tenant.pk).exists():
            raise forms.ValidationError("Поддомен занят или зарезервирован.")
        return slug

    plan = forms.ChoiceField(label='Пакет', choices=[('start', 'Команда'), ('business', 'Студия'), ('center', 'Центр'), ('network', 'Сеть')])
    status = forms.ChoiceField(label='Статус подписки', choices=Subscription.Status.choices)
    ends_at = forms.DateTimeField(label='Активна / триал до', required=False, widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'), input_formats=['%Y-%m-%dT%H:%M'])
    limit = forms.IntegerField(label='Индивидуальный лимит сотрудников', required=False, min_value=1)
    custom_price = forms.DecimalField(label='Индивидуальная цена, ₽/месяц', min_value=0, required=False, decimal_places=2)
    reason = forms.CharField(label='Причина изменения', max_length=300)
    tenant_status = forms.ChoiceField(label='Доступ компании', choices=Tenant.Status.choices)
    def clean(self):
        data = super().clean()
        if data.get('status') in {'active','trial'} and not data.get('ends_at'):
            self.add_error('ends_at', 'Укажите время окончания периода.')
        return data


@platform_required
def platform_company(request, tenant_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    sub = tenant.subscription
    initial = {'slug': tenant.slug, 'plan': sub.plan_id, 'status': sub.status, 'ends_at': sub.trial_ends_at if sub.status == 'trial' else sub.ends_at, 'limit': sub.overrides.get('limits', {}).get('active_employees'), 'custom_price': sub.overrides.get('custom_price'), 'tenant_status': tenant.status}
    form = SubscriptionAdminForm(request.POST or None, initial=initial, tenant=tenant)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            sub = Subscription.objects.select_for_update().get(pk=sub.pk)
            previous = sub.plan
            data = form.cleaned_data
            sub.plan = ensure_default_plans()[data['plan']]
            sub.status, sub.ends_at = data['status'], data['ends_at']
            if sub.status == 'trial':
                sub.trial_ends_at = data['ends_at']
                sub.trial_used = True
            sub.overrides = {}
            if data['limit']:
                sub.overrides['limits'] = {'active_employees': data['limit']}
            if data['custom_price'] is not None:
                sub.overrides['custom_price'] = str(data['custom_price'])
            sub.scheduled_plan = None
            sub.suspension_reason = data['reason'] if sub.status in {'suspended','cancelled'} else ''
            sub.save()
            old_slug = tenant.slug
            tenant.status = data['tenant_status']
            tenant.slug = data['slug']
            tenant.save(update_fields=['status', 'slug'])
            if old_slug != tenant.slug:
                PlatformAudit.objects.create(actor=request.user, action='company_subdomain_changed', details={'tenant':str(tenant.pk), 'old':old_slug, 'new':tenant.slug})
            SubscriptionHistory.objects.create(subscription=sub, actor=request.user, action='platform_manual', from_plan=previous, to_plan=sub.plan, details={'reason': data['reason'], 'status': sub.status, 'ends_at': str(sub.ends_at), 'overrides': sub.overrides})
            PlatformAudit.objects.create(actor=request.user, action='company_update', details={'tenant': str(tenant.pk), 'reason': data['reason']})
        messages.success(request, 'Компания и подписка обновлены.')
        return redirect('platform-company', tenant_id=tenant.pk)
    return render(request, 'platform/company.html', {'company': tenant, 'form': form, 'members': tenant.memberships.select_related('user'), 'history': sub.history.select_related('actor','from_plan','to_plan')[:50]})


class PromoForm(forms.ModelForm):
    recurring = forms.BooleanField(label="На все продления", required=False, help_text="Если выключено — скидка только на первый платёж компании.")
    plans = forms.MultipleChoiceField(label='Пакеты (пусто — все)', choices=[('start','Команда'),('business','Студия'),('center','Центр')], required=False, widget=forms.CheckboxSelectMultiple)
    periods = forms.TypedMultipleChoiceField(label='Периоды (пусто — все)', choices=[(1,'1 месяц'),(6,'6 месяцев'),(12,'12 месяцев')], coerce=int, required=False, widget=forms.CheckboxSelectMultiple)
    class Meta:
        model = PromoCode
        fields = ('code','kind','value','recurring','active','starts_at','ends_at','max_uses')
        labels = {'code':'Промокод','kind':'Тип скидки','value':'Размер скидки','recurring':'На все продления','active':'Активен','starts_at':'Начало','ends_at':'Окончание','max_uses':'Лимит компаний'}
        widgets = {name: forms.DateTimeInput(attrs={'type':'datetime-local'}, format='%Y-%m-%dT%H:%M') for name in ['starts_at','ends_at']}
    def clean(self):
        data = super().clean()
        value = data.get('value')
        if value is not None and (value <= 0 or data.get('kind') == 'percent' and value > 100):
            self.add_error('value','Укажите положительную скидку; процент не может превышать 100.')
        if data.get('starts_at') and data.get('ends_at') and data['starts_at'] >= data['ends_at']:
            self.add_error('ends_at','Окончание должно быть позже начала.')
        return data
    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()


@platform_required
def platform_promos(request):
    if request.method == 'POST' and request.POST.get('action') == 'toggle':
        promo = get_object_or_404(PromoCode, pk=request.POST.get('promo_id'))
        promo.active = not promo.active
        promo.save()
        PlatformAudit.objects.create(actor=request.user, action='promo_toggle', details={'code':promo.code,'active':promo.active})
        return redirect('platform-promos')
    form = PromoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        promo = form.save(commit=False)
        promo.allowed_plans = form.cleaned_data['plans']
        promo.allowed_periods = form.cleaned_data['periods']
        promo.save()
        PlatformAudit.objects.create(actor=request.user, action='promo_create', details={'code': promo.code})
        messages.success(request,'Промокод создан.')
        return redirect('platform-promos')
    return render(request,'platform/promos.html',{'form':form,'promos':PromoCode.objects.all().order_by('-created_at')})


@platform_required
def platform_security(request):
    from apps.accounts.security import security_settings
    return security_settings(request)


class PlatformPasswordResetForm(PasswordResetForm):
    def get_users(self, email):
        accesses = PlatformAccess.objects.select_related('user').filter(active=True)
        from django.db.models import Q
        accesses = accesses.filter(Q(email__iexact=email) | Q(email__isnull=True, user__email__iexact=email))
        return (access.user for access in accesses if access.user.is_active and access.user.has_usable_password())

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email, html_email_template_name=None):
        access = PlatformAccess.objects.get(user=context['user'])
        email = access.email or access.user.email
        return super().send_mail(subject_template_name, email_template_name, {**context, 'email':email}, from_email, email, html_email_template_name)


class PlatformPasswordResetView(PasswordResetView):
    form_class = PlatformPasswordResetForm
    template_name = "platform/password_reset_form.html"
    email_template_name = "platform/password_reset_email.html"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = reverse_lazy("password-reset-done")

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except (OSError, __import__('smtplib').SMTPException):
            form.add_error(None, "Не удалось отправить письмо. Повторите позже.")
            return self.form_invalid(form)


class PlatformPasswordResetDoneView(PasswordResetDoneView):
    template_name = "platform/password_reset_done.html"


class PlatformPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "platform/password_reset_confirm.html"
    success_url = reverse_lazy("password-reset-complete")

    def get_user(self, uidb64):
        user = super().get_user(uidb64)
        if user and PlatformAccess.objects.filter(user=user, active=True).exists():
            return user
        return None


class PlatformPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "platform/password_reset_complete.html"


@never_cache
@platform_required
def platform_password_change(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        PlatformAudit.objects.create(actor=user, action="platform_password_changed")
        messages.success(request, "Пароль платформы изменён. Пароль CRM компаний остался прежним.")
        return redirect("platform-security")
    return render(request, "platform/password_change.html", {"form": form})


class PaymentAnalyticsForm(forms.Form):
    start = forms.DateField(label="С", required=False, widget=forms.DateInput(attrs={"type":"date", "aria-label":"Начало периода"}))
    end = forms.DateField(label="По", required=False, widget=forms.DateInput(attrs={"type":"date", "aria-label":"Конец периода"}))
    company = forms.ModelChoiceField(label="Компания", queryset=Tenant.objects.all(), required=False, empty_label="Все компании")

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["start"] > data["end"]:
            raise forms.ValidationError("Дата начала должна быть не позже даты окончания.")
        return data


@platform_required
def platform_payments(request):
    from django.core.paginator import Paginator
    form = PaymentAnalyticsForm(request.GET)
    orders = SubscriptionOrder.objects.select_related("tenant", "plan")
    if form.is_valid():
        data = form.cleaned_data
        if data.get("company"):
            orders = orders.filter(tenant=data["company"])
        # Revenue is assigned to the payment date, not to the invoice creation date.
        paid = orders.filter(status="paid")
        if data.get("start"):
            paid = paid.filter(paid_at__date__gte=data["start"])
            orders = orders.filter(created_at__date__gte=data["start"])
        if data.get("end"):
            paid = paid.filter(paid_at__date__lte=data["end"])
            orders = orders.filter(created_at__date__lte=data["end"])
    else:
        orders = orders.none()
        paid = orders
    summary = paid.aggregate(revenue=Sum("total"), discount=Sum("discount"), count=Count("id"), companies=Count("tenant", distinct=True))
    summary["revenue"] = summary["revenue"] or Decimal("0")
    summary["discount"] = summary["discount"] or Decimal("0")
    summary["average"] = summary["revenue"] / summary["count"] if summary["count"] else Decimal("0")
    months = paid.order_by().annotate(month=TruncMonth("paid_at")).values("month").annotate(total=Sum("total"), count=Count("id")).order_by("-month")
    companies = paid.order_by().values("tenant__name", "tenant__slug").annotate(total=Sum("total"), count=Count("id")).order_by("-total")
    payments = Paginator(paid.order_by("-paid_at", "-created_at"), 50).get_page(request.GET.get("page"))
    query = request.GET.copy(); query.pop("page", None)
    return render(request, "platform/payments.html", {"form":form, "summary":summary, "months":months, "companies":companies, "payments":payments, "query":query.urlencode(), "pending":orders.filter(status="pending").count(), "cancelled":orders.filter(status="cancelled").count()})


class LegalDocumentForm(forms.Form):
    title = forms.CharField(label='Название документа', max_length=255)
    content = forms.CharField(label='Текст документа', max_length=200000, widget=forms.Textarea)

    def clean_content(self):
        from .legal import sanitize_content
        from django.utils.html import strip_tags
        content = sanitize_content(self.cleaned_data['content'])
        if not strip_tags(content).strip():
            raise forms.ValidationError('Введите текст документа.')
        return content


@platform_required
def platform_legal(request):
    from .legal import LEGAL_DOCUMENTS, get_document
    documents = [dict(get_document(slug), slug=slug) for slug in LEGAL_DOCUMENTS]
    return render(request, 'platform/legal_list.html', {'documents': documents})


@platform_required
@transaction.atomic
def platform_legal_edit(request, document_slug):
    from .legal import LEGAL_DOCUMENTS, LEGAL_VERSION, get_document, sanitize_content
    from .models import LegalDocument, LegalDocumentRevision
    from django.template.loader import render_to_string
    definition = LEGAL_DOCUMENTS.get(document_slug)
    if definition is None:
        raise Http404
    current = get_document(document_slug)
    initial_content = current['content'] if current['content'] is not None else sanitize_content(render_to_string(definition['template'], {'support_email': settings.SUPPORT_EMAIL}))
    form = LegalDocumentForm(request.POST or None, initial={'title': current['title'], 'content': initial_content})
    if request.method == 'POST' and form.is_valid():
        # Lock a single stable row for both first publication and later revisions.
        document, created = LegalDocument.objects.get_or_create(slug=document_slug, defaults={
            'title': current['title'], 'content': initial_content, 'version': LEGAL_VERSION})
        document = LegalDocument.objects.select_for_update().get(pk=document.pk)
        if created:
            LegalDocumentRevision.objects.create(document=document, title=document.title, content=document.content, version=document.version, actor=request.user)
        number = document.revisions.count()
        document.title = form.cleaned_data['title']
        document.content = form.cleaned_data['content']
        document.version = timezone.localdate().strftime('%d.%m.%Y') + '-r' + str(number)
        document.save()
        LegalDocumentRevision.objects.create(document=document, title=document.title, content=document.content, version=document.version, actor=request.user)
        PlatformAudit.objects.create(actor=request.user, action='legal_document_updated', details={'document':document_slug, 'version':document.version})
        messages.success(request, 'Документ сохранён и опубликован на сайте.')
        return redirect('platform-legal-edit', document_slug=document_slug)
    saved = LegalDocument.objects.filter(slug=document_slug).first()
    return render(request, 'platform/legal_edit.html', {'form':form, 'document_slug':document_slug, 'document':current,
        'editor_content':sanitize_content(form['content'].value() or ''), 'revisions':saved.revisions.all()[:10] if saved else []})
