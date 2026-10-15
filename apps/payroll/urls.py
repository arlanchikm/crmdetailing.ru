from django.urls import path

from . import views

urlpatterns = [path("payroll/", views.payroll_dashboard, name="payroll-dashboard")]
