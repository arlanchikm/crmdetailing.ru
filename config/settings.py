import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-local-development-key")
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
if not DEBUG and SECRET_KEY == "unsafe-local-development-key":
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set in production")
ALLOWED_HOSTS = [
    item.strip()
    for item in os.getenv(
        "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,.localhost"
    ).split(",")
    if item.strip()
]

PLATFORM_OWNER_EMAIL = os.getenv("DJANGO_PLATFORM_OWNER_EMAIL", "platform-owner@example.invalid").strip().lower()

PRIMARY_DOMAIN = os.getenv("DJANGO_PRIMARY_DOMAIN", "crmdetailing.ru").strip().lower()
PLATFORM_DOMAIN = f'platform.{PRIMARY_DOMAIN}'
BOOKING_DOMAIN = f'book.{PRIMARY_DOMAIN}'
SUPPORT_DOMAIN = f'support.{PRIMARY_DOMAIN}'
TLS_ASK_TOKEN = os.getenv("TLS_ASK_TOKEN", "")
if not DEBUG and not TLS_ASK_TOKEN:
    raise ImproperlyConfigured("TLS_ASK_TOKEN must be set in production")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.accounts",
    "apps.tenants",
    "apps.customers",
    "apps.catalog",
    "apps.staff",
    "apps.operations",
    "apps.inventory",
    "apps.analytics",
    "apps.payroll",
    "apps.core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.tenants.middleware.TenantMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.tenants.context_processors.tenant_context",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "crm_detailing"),
        "USER": os.getenv("POSTGRES_USER", "crm_detailing"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "local-development-password"),
        "HOST": os.getenv("POSTGRES_HOST", "db"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "landing"

EMAIL_BACKEND = os.getenv("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("DJANGO_EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("DJANGO_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_SSL = os.getenv("DJANGO_EMAIL_USE_SSL", "0") == "1"
EMAIL_USE_TLS = os.getenv("DJANGO_EMAIL_USE_TLS", "1") == "1"
EMAIL_TIMEOUT = int(os.getenv("DJANGO_EMAIL_TIMEOUT", "10"))
DEFAULT_FROM_EMAIL = os.getenv("DJANGO_DEFAULT_FROM_EMAIL", "CRM Detailing <noreply@crmdetailing.ru>")
SUPPORT_EMAIL = os.getenv("DJANGO_SUPPORT_EMAIL", "info.crmdetailing@yandex.ru")

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "").strip()
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "").strip()
YOOKASSA_TEST_MODE = os.getenv("YOOKASSA_TEST_MODE", "1") == "1"
YOOKASSA_ENABLED = bool(YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY)

PUBLIC_HOSTS = {
    item.strip().lower()
    for item in os.getenv(
        "DJANGO_PUBLIC_HOSTS", "localhost,127.0.0.1,crmdetailing.ru,www.crmdetailing.ru"
    ).split(",")
    if item.strip()
}
TENANT_DOMAIN_SUFFIXES = tuple(
    item.strip().lower()
    for item in os.getenv(
        "DJANGO_TENANT_DOMAIN_SUFFIXES", ".localhost,.crmdetailing.ru"
    ).split(",")
    if item.strip()
)
RESERVED_SUBDOMAINS = {"www", "api", "admin", "static", "status", "support", "administrator", "about", "platform", "book", "noreply"}

CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.getenv("REDIS_URL", "redis://redis:6379/0")
CELERY_BEAT_SCHEDULE = {
    'operational-notifications': {
        'task': 'apps.core.tasks.refresh_notifications_background',
        'schedule': 120.0,
    },
    'subscription-auto-renewal': {
        'task': 'apps.core.tasks.renew_yookassa_subscriptions',
        'schedule': 3600.0,
    },
}

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"

CSRF_TRUSTED_ORIGINS = [
    item.strip()
    for item in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if item.strip()
]
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "0"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
