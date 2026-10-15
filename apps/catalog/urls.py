from django.urls import path

from . import views

urlpatterns = [
    path("services/", views.service_list, name="service-list"),
    path("services/categories/", views.service_category_list, name="service-category-list"),
    path("services/categories/new/", views.service_category_create, name="service-category-create"),
    path("services/categories/<uuid:category_id>/edit/", views.service_category_edit, name="service-category-edit"),
    path("services/categories/<uuid:category_id>/delete/", views.service_category_delete, name="service-category-delete"),
    path("services/new/", views.service_create, name="service-create"),
    path("services/<uuid:service_id>/edit/", views.service_edit, name="service-edit"),
    path("services/<uuid:service_id>/prices/<uuid:price_id>/", views.service_class_price_update, name="service-class-price-update"),
    path("posts/", views.post_list, name="post-list"),
    path("posts/new/", views.post_create, name="post-create"),
    path("posts/<uuid:post_id>/edit/", views.post_edit, name="post-edit"),
]
