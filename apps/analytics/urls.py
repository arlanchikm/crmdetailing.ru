from django.urls import path

from . import views

urlpatterns = [
    path("analytics/", views.analytics_dashboard, name="analytics-dashboard"),
    path("analytics/expenses/new/", views.expense_create, name="expense-create"),
]
