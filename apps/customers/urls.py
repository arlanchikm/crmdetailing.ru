from django.urls import path

from . import views

urlpatterns = [
    path("vehicle-classes/", views.vehicle_class_list, name="vehicle-class-list"),
    path("vehicle-classes/new/", views.vehicle_class_create, name="vehicle-class-create"),
    path("vehicle-classes/<uuid:vehicle_class_id>/edit/", views.vehicle_class_edit, name="vehicle-class-edit"),
    path("clients/", views.client_list, name="client-list"),
    path("clients/new/", views.client_create, name="client-create"),
    path("clients/<uuid:client_id>/", views.client_detail, name="client-detail"),
    path("clients/<uuid:client_id>/edit/", views.client_edit, name="client-edit"),
    path("clients/<uuid:client_id>/restore/", views.client_restore, name="client-restore"),
    path("clients/<uuid:client_id>/delete-permanently/", views.client_permanent_delete, name="client-permanent-delete"),
    path("cars/<uuid:car_id>/restore/", views.car_restore, name="car-restore"),
    path("cars/<uuid:car_id>/delete-permanently/", views.car_permanent_delete, name="car-permanent-delete"),
    path("clients/<uuid:client_id>/archive/", views.client_archive, name="client-archive"),
    path("clients/<uuid:client_id>/cars/new/", views.car_create, name="car-create"),
    path("cars/<uuid:car_id>/", views.car_detail, name="car-detail"),
    path("cars/<uuid:car_id>/edit/", views.car_edit, name="car-edit"),
    path("cars/<uuid:car_id>/archive/", views.car_archive, name="car-archive"),
    path("cars/<uuid:car_id>/damages/new/", views.car_damage_create, name="car-damage-create"),
    path("cars/<uuid:car_id>/damages/<uuid:damage_id>/edit/", views.car_damage_edit, name="car-damage-edit"),
    path("cars/<uuid:car_id>/damages/<uuid:damage_id>/delete/", views.car_damage_delete, name="car-damage-delete"),
    path("cars/<uuid:car_id>/damages/<uuid:damage_id>/photos/<uuid:photo_id>/delete/", views.car_damage_photo_delete, name="car-damage-photo-delete"),
]
