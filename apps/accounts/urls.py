from django.contrib.auth.views import LogoutView
from django.urls import path

from .views import (
    TenantLoginView, TenantPasswordResetCompleteView, TenantPasswordResetConfirmView,
    TenantPasswordResetDoneView, TenantPasswordResetView, company_register,
    two_factor_verify,
)

urlpatterns = [
    path("register/", company_register, name="company-register"),
    path("login/", TenantLoginView.as_view(), name="login"),
    path("login/verify/", two_factor_verify, name="two-factor-verify"),
    path("password-reset/", TenantPasswordResetView.as_view(), name="password-reset"),
    path("password-reset/done/", TenantPasswordResetDoneView.as_view(), name="password-reset-done"),
    path(
        "password-reset/<uidb64>/<token>/",
        TenantPasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path("password-reset/complete/", TenantPasswordResetCompleteView.as_view(), name="password-reset-complete"),
    path("logout/", LogoutView.as_view(), name="logout"),
]
