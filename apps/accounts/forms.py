import re

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, UserCreationForm

from apps.tenants.models import Tenant

from .models import User


class TenantAuthenticationForm(AuthenticationForm):
    def clean(self):
        from .security import login_blocked, failed_login
        email=self.data.get('username','')
        if login_blocked(self.request,email):
            raise forms.ValidationError('Слишком много попыток. Повторите через 15 минут.')
        try:
            return super().clean()
        except forms.ValidationError:
            if email and self.data.get('password'): failed_login(self.request,email)
            raise

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            raise self.get_invalid_login_error()
        if not user.memberships.filter(tenant=tenant, status="active").exists():
            raise self.get_invalid_login_error()


class TenantPasswordResetForm(PasswordResetForm):
    """Limit password reset emails to active members of the current company."""

    def __init__(self, *args, tenant=None, **kwargs):
        self.tenant = tenant
        super().__init__(*args, **kwargs)

    def get_users(self, email):
        users = super().get_users(email)
        if self.tenant is None:
            return ()
        return (
            user for user in users
            if user.memberships.filter(tenant=self.tenant, status="active").exists()
        )


class CompanyRegistrationForm(UserCreationForm):
    plan = forms.ChoiceField(label="Пакет сотрудников", choices=[("start", "Команда — до 4 сотрудников"), ("business", "Студия — до 8 сотрудников"), ("center", "Центр — до 15 сотрудников")])
    company_name = forms.CharField(label="Название компании", max_length=160)
    slug = forms.CharField(label="Адрес CRM", min_length=2, max_length=40,
                           help_text="Строчные латинские буквы, цифры и дефис.")
    owner_name = forms.CharField(label="Ваше имя", max_length=160)
    email = forms.EmailField(label="Email")
    personal_data_consent = forms.BooleanField(
        label="Согласие на обработку персональных данных", required=True
    )
    offer_acceptance = forms.BooleanField(label="Принятие публичной оферты", required=True)
    marketing_consent = forms.BooleanField(
        label="Согласие на получение рекламных и маркетинговых сообщений", required=False
    )

    class Meta:
        model = User
        fields = (
            "company_name", "slug", "owner_name", "email", "password1", "password2",
            "personal_data_consent", "offer_acceptance", "marketing_consent",
        )

    def clean_slug(self):
        slug = self.cleaned_data["slug"].strip().lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?", slug):
            raise forms.ValidationError("Используйте строчные латинские буквы, цифры и дефис.")
        if slug in settings.RESERVED_SUBDOMAINS:
            raise forms.ValidationError("Этот адрес зарезервирован системой.")
        if Tenant.objects.filter(slug=slug).exists():
            raise forms.ValidationError("Этот адрес уже занят.")
        return slug

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("Пользователь с таким email уже зарегистрирован.")
        return email
