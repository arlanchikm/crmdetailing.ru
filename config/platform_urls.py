from django.urls import path
from django.contrib.auth.views import LogoutView
from apps.core import platform, views, knowledge

urlpatterns = [
    path('knowledge/',knowledge.manage,name='platform-knowledge'),
    path('knowledge/new/',knowledge.edit,name='platform-knowledge-create'),
    path('knowledge/<int:article_id>/',knowledge.edit,name='platform-knowledge-edit'),
    path('',platform.platform_dashboard,name='platform-dashboard'),
    path('login/',platform.platform_login,name='platform-login'),
    path('verify/',platform.platform_verify,name='platform-verify'),
    path('company/<uuid:tenant_id>/',platform.platform_company,name='platform-company'),
    path('promos/',platform.platform_promos,name='platform-promos'),
    path('legal/', platform.platform_legal, name='platform-legal'),
    path('legal/<slug:document_slug>/', platform.platform_legal_edit, name='platform-legal-edit'),
    path('payments/', platform.platform_payments, name='platform-payments'),
    path('password/change/', platform.platform_password_change, name='platform-password-change'),
    path('password-reset/', platform.PlatformPasswordResetView.as_view(), name='password-reset'),
    path('password-reset/done/', platform.PlatformPasswordResetDoneView.as_view(), name='password-reset-done'),
    path('password-reset/complete/', platform.PlatformPasswordResetCompleteView.as_view(), name='password-reset-complete'),
    path('password-reset/<uidb64>/<token>/', platform.PlatformPasswordResetConfirmView.as_view(), name='password-reset-confirm'),
    path('security/',platform.platform_security,name='platform-security'),
    path('logout/',LogoutView.as_view(next_page='/login/'),name='logout'),
    path('health/',views.health,name='health'),
]
