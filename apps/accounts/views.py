import ipaddress

from django.conf import settings
from django.contrib.auth.views import (
    LoginView, PasswordResetCompleteView, PasswordResetConfirmView,
    PasswordResetDoneView, PasswordResetView,
)
from django.contrib import messages
from django.views.decorators.cache import never_cache
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_http_methods

from apps.tenants.services import create_company_owner
from apps.core.legal import LEGAL_VERSION, get_document

from .forms import CompanyRegistrationForm, TenantAuthenticationForm, TenantPasswordResetForm
from .models import LegalAcceptance


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    candidate = forwarded.split(",", 1)[0].strip() or request.META.get("REMOTE_ADDR") or ""
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


class TenantLoginView(LoginView):
    authentication_form = TenantAuthenticationForm
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        from .security import issue_challenge, attempt_key
        from .models import AuthenticationAttempt
        user=form.get_user()
        AuthenticationAttempt.objects.filter(key=attempt_key(self.request,user.email)).delete()
        if user.email_2fa:
            try:
                challenge=issue_challenge(user,'login',self.request.tenant,allow_fallback=True)
            except ValueError as exc:
                form.add_error(None,str(exc));return self.form_invalid(form)
            self.request.session['login_challenge']=str(challenge.pk)
            self.request.session['login_next']=self.get_success_url()
            if challenge.delivery_failed:
                messages.warning(self.request,'Письмо не отправлено. Используйте резервный код или повторите отправку позже.')
            return redirect('two-factor-verify')
        response=super().form_valid(form)
        self.request.session['account_2fa_user']=str(user.pk)
        return response

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "tenant", None) is None:
            return redirect("landing")
        return super().dispatch(request, *args, **kwargs)


class TenantRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "tenant", None) is None:
            return redirect("landing")
        return super().dispatch(request, *args, **kwargs)


class TenantPasswordResetView(TenantRequiredMixin, PasswordResetView):
    form_class = TenantPasswordResetForm
    template_name = "registration/password_reset_form.html"
    email_template_name = "registration/password_reset_email.html"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = reverse_lazy("password-reset-done")

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "tenant": self.request.tenant}

    def form_valid(self, form):
        if not settings.EMAIL_HOST:
            form.add_error(None, "Отправка писем скоро будет подключена. Пока обратитесь к владельцу компании.")
            return self.form_invalid(form)
        self.extra_email_context = {"current_tenant": self.request.tenant}
        try:
            return super().form_valid(form)
        except (OSError, __import__("smtplib").SMTPException):
            form.add_error(None, "Не удалось отправить письмо. Повторите позже или обратитесь к владельцу компании.")
            return self.form_invalid(form)


class TenantPasswordResetDoneView(TenantRequiredMixin, PasswordResetDoneView):
    template_name = "registration/password_reset_done.html"


class TenantPasswordResetConfirmView(TenantRequiredMixin, PasswordResetConfirmView):
    def get_user(self, uidb64):
        user = super().get_user(uidb64)
        if user and user.memberships.filter(tenant=self.request.tenant, status="active").exists():
            return user
        return None

    template_name = "registration/password_reset_confirm.html"
    success_url = reverse_lazy("password-reset-complete")


class TenantPasswordResetCompleteView(TenantRequiredMixin, PasswordResetCompleteView):
    template_name = "registration/password_reset_complete.html"


@require_http_methods(["GET", "POST"])
def company_register(request):
    if request.tenant:
        return redirect("dashboard" if request.user.is_authenticated else "login")
    form = CompanyRegistrationForm(request.POST or None, initial={"plan": request.GET.get("plan", "start")})
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                tenant, user = create_company_owner(
                    company_name=form.cleaned_data["company_name"],
                    slug=form.cleaned_data["slug"],
                    owner_name=form.cleaned_data["owner_name"],
                    email=form.cleaned_data["email"],
                    password=form.cleaned_data["password1"],
                    plan_code=form.cleaned_data["plan"],
                )
                acceptance_kwargs = {
                    "user": user,
                    "tenant": tenant,
                    "ip_address": _client_ip(request),
                    "user_agent": request.META.get("HTTP_USER_AGENT", "")[:2000],
                }
                document_types = [
                    LegalAcceptance.DocumentType.PERSONAL_DATA,
                    LegalAcceptance.DocumentType.OFFER,
                ]
                if form.cleaned_data["marketing_consent"]:
                    document_types.append(LegalAcceptance.DocumentType.MARKETING)
                LegalAcceptance.objects.bulk_create([
                    LegalAcceptance(document_type=document_type, document_version=get_document({LegalAcceptance.DocumentType.PERSONAL_DATA: "personal-data-consent", LegalAcceptance.DocumentType.OFFER: "offer", LegalAcceptance.DocumentType.MARKETING: "marketing-consent"}[document_type])["version"], **acceptance_kwargs)
                    for document_type in document_types
                ])
        except IntegrityError:
            form.add_error(None, "Не удалось создать компанию: адрес или email уже занят.")
        else:
            scheme = "http" if settings.DEBUG else "https"
            port = f":{request.get_port()}" if settings.DEBUG and request.get_port() not in {"80", "443"} else ""
            return redirect(f"{scheme}://{tenant.slug}.{settings.PRIMARY_DOMAIN}{port}/login/?registered=1")
    return render(request, "registration/company_register.html", {
        "form": form,
        "primary_domain": settings.PRIMARY_DOMAIN,
        "personal_data_version": get_document("personal-data-consent")["version"],
        "offer_version": get_document("offer")["version"],
    })


@never_cache
def two_factor_verify(request):
    if not request.tenant:
        return redirect('landing')
    from .security import verify_login
    return verify_login(request)
