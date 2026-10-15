from django.urls import path

from . import views

urlpatterns = [
    path("employees/", views.employee_list, name="employee-list"),
    path("employees/new/", views.employee_create, name="employee-create"),
    path("employees/<uuid:employee_id>/", views.employee_detail, name="employee-detail"),
    path("employees/<uuid:employee_id>/edit/", views.employee_edit, name="employee-edit"),
    path("schedule/", views.schedule, name="staff-schedule"),
    path("roles/", views.role_list, name="role-list"),
    path("roles/new/", views.role_create, name="role-create"),
    path("roles/<uuid:role_id>/edit/", views.role_edit, name="role-edit"),
]
