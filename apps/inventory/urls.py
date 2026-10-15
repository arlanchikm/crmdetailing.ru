from django.urls import path

from . import views

urlpatterns = [
    path("inventory/", views.consumable_list, name="consumable-list"),
    path("inventory/new/", views.consumable_create, name="consumable-create"),
    path("inventory/<uuid:consumable_id>/edit/", views.consumable_edit, name="consumable-edit"),
    path("inventory/<uuid:consumable_id>/fact/", views.consumable_fact_update, name="consumable-fact-update"),
    path("inventory/<uuid:consumable_id>/delete/", views.consumable_delete, name="consumable-delete"),
    path("inventory/<uuid:consumable_id>/movement/", views.movement_create, name="inventory-movement-create"),
    path("inventory/movements/", views.movement_list, name="inventory-movement-list"),
    path("inventory/categories/", views.category_list, name="inventory-category-list"),
    path("inventory/categories/new/", views.category_create, name="inventory-category-create"),
    path("inventory/categories/<uuid:category_id>/edit/", views.category_edit, name="inventory-category-edit"),
]
