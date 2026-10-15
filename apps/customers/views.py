from django.contrib import messages
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.tenants.access import tenant_permission_required, permissions_for_request
from apps.operations.models import Appointment
from apps.core.models import log_event

from .forms import CarDamageForm, CarForm, ClientForm, VehicleClassForm
from .models import (
    Car, CarDamage, CarDamagePhoto, Client, VehicleClass,
    ensure_default_vehicle_classes, normalize_phone, normalize_plate,
)


@tenant_permission_required("clients_view")
def client_list(request):
    query = request.GET.get("q", "").strip()
    archived = request.GET.get("archived") == "1"
    if archived and "clients_delete" not in permissions_for_request(request):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Недостаточно прав")
    visible_cars = Car.objects.filter(tenant=request.tenant, archived_at__isnull=not archived)
    clients = Client.objects.filter(
        tenant=request.tenant, archived_at__isnull=not archived
    ).prefetch_related(Prefetch("cars", queryset=visible_cars, to_attr="active_cars"))
    if query:
        car_matches = visible_cars.filter(client_id=OuterRef("pk")).filter(
            Q(make__icontains=query)
            | Q(model__icontains=query)
            | Q(normalized_plate__icontains=normalize_plate(query))
        )
        match = Q(name__icontains=query) | Q(last_name__icontains=query) | Q(car_match=True)
        phone = normalize_phone(query)
        if phone:
            match |= Q(normalized_phone__contains=phone)
        clients = clients.annotate(car_match=Exists(car_matches)).filter(match)
    return render(request, "customers/client_list.html", {"clients": clients[:100], "query": query, "archived": archived})


@tenant_permission_required("clients_view")
def client_detail(request, client_id):
    client = get_object_or_404(
        Client.objects.prefetch_related(
            Prefetch(
                "cars",
                queryset=Car.objects.filter(
                    tenant=request.tenant,
                ),
                to_attr="active_cars",
            )
        ),
        id=client_id,
        tenant=request.tenant,
    )
    visits = (
        Appointment.objects.filter(
            tenant=request.tenant,
            client=client,
            status=Appointment.Status.COMPLETED,
        )
        .select_related("car", "employee", "post")
        .prefetch_related("items")
        .order_by("-start_at")
    )
    visits_list = list(visits)
    paid_visits = [row for row in visits_list if row.payment_status == Appointment.PaymentStatus.PAID]
    revenue = sum((row.total for row in paid_visits), Decimal("0"))
    return render(
        request,
        "customers/client_detail.html",
        {
            "client": client, "visits": visits_list,
            "visible_cars": [car for car in client.active_cars if car.archived_at is None],
            "deleted_cars": [car for car in client.active_cars if car.archived_at is not None],
            "visits_count": len(visits_list), "revenue": revenue,
            "average_check": revenue / len(paid_visits) if paid_visits else Decimal("0"),
            "last_visit": visits_list[0].start_at if visits_list else None,
        },
    )


@tenant_permission_required("clients_manage")
def client_create(request):
    form = ClientForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        client = form.save(commit=False)
        client.tenant = request.tenant
        client.full_clean()
        client.save()
        log_event(tenant=request.tenant, actor=request.user, action="client_create", entity=client,
                  description=f"Добавлен клиент «{client.display_name}»")
        messages.success(request, "Клиент добавлен.")
        return redirect("client-detail", client_id=client.id)
    return render(request, "customers/form.html", {"form": form, "title": "Новый клиент"})


@tenant_permission_required("clients_manage")
def client_edit(request, client_id):
    client = get_object_or_404(Client, id=client_id, tenant=request.tenant, archived_at__isnull=True)
    form = ClientForm(request.POST or None, instance=client)
    if request.method == "POST" and form.is_valid():
        client = form.save()
        log_event(tenant=request.tenant, actor=request.user, action="client_update", entity=client,
                  description=f"Изменён клиент «{client.display_name}»")
        messages.success(request, "Данные клиента сохранены.")
        return redirect("client-detail", client_id=client.id)
    return render(request, "customers/form.html", {"form": form, "title": "Редактирование клиента"})


@tenant_permission_required("clients_manage")
def car_create(request, client_id):
    client = get_object_or_404(Client, id=client_id, tenant=request.tenant, archived_at__isnull=True)
    form = CarForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        car = form.save(commit=False)
        car.tenant = request.tenant
        car.client = client
        car.full_clean()
        car.save()
        log_event(tenant=request.tenant, actor=request.user, action="car_create", entity=car,
                  description=f"Добавлен автомобиль «{car}» клиенту {client.display_name}")
        messages.success(request, "Автомобиль добавлен.")
        return redirect("client-detail", client_id=client.id)
    return render(
        request,
        "customers/form.html",
        {"form": form, "title": f"Новый автомобиль — {client.display_name}"},
    )


@tenant_permission_required("services_manage")
def vehicle_class_list(request):
    ensure_default_vehicle_classes(request.tenant)
    classes = VehicleClass.objects.filter(tenant=request.tenant).annotate(car_count=Count('cars'))
    return render(request, "customers/vehicle_class_list.html", {"vehicle_classes": classes})


@tenant_permission_required("services_manage")
def vehicle_class_create(request):
    form = VehicleClassForm(request.POST or None, request.FILES or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        vehicle_class = form.save(commit=False)
        vehicle_class.tenant = request.tenant
        vehicle_class.save()
        from apps.catalog.models import ServiceVehicleClassPrice
        for service in request.tenant.services.all():
            ServiceVehicleClassPrice.objects.get_or_create(
                tenant=request.tenant, service=service, vehicle_class=vehicle_class,
                defaults={"price": service.price},
            )
        log_event(tenant=request.tenant, actor=request.user, action="vehicle_class_create",
                  entity=vehicle_class, description=f"Добавлен класс авто «{vehicle_class.name}»")
        messages.success(request, "Класс автомобиля добавлен.")
        return redirect("vehicle-class-list")
    return render(request, "customers/form.html", {"form": form, "title": "Новый класс автомобиля"})


@tenant_permission_required("services_manage")
def vehicle_class_edit(request, vehicle_class_id):
    vehicle_class = get_object_or_404(VehicleClass, id=vehicle_class_id, tenant=request.tenant)
    form = VehicleClassForm(request.POST or None, request.FILES or None, instance=vehicle_class, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_event(tenant=request.tenant, actor=request.user, action="vehicle_class_update",
                  entity=vehicle_class, description=f"Изменён класс авто «{vehicle_class.name}»")
        messages.success(request, "Класс автомобиля сохранён.")
        return redirect("vehicle-class-list")
    return render(request, "customers/form.html", {"form": form, "title": "Редактирование класса"})


@tenant_permission_required("clients_view")
def car_detail(request, car_id):
    car = get_object_or_404(
        Car.objects.select_related("client"), id=car_id, tenant=request.tenant,
    )
    visits = list(
        Appointment.objects.filter(
            tenant=request.tenant, car=car, status=Appointment.Status.COMPLETED,
        ).select_related("client", "employee", "post").prefetch_related(
            "items", "assignees__employee", "complaints", "rating"
        ).order_by("-start_at")
    )
    paid = [row for row in visits if row.payment_status == Appointment.PaymentStatus.PAID]
    revenue = sum((row.total for row in paid), Decimal("0"))
    damages = list(car.damages.filter(archived_at__isnull=True).prefetch_related("photos"))
    damage_rows = [{
        "damage": damage,
        "form": CarDamageForm(instance=damage, prefix=str(damage.id), existing_photo_count=damage.photos.count()),
    } for damage in damages]
    return render(request, "customers/car_detail.html", {
        "car": car, "visits": visits, "visits_count": len(visits),
        "revenue": revenue, "average_check": revenue / len(paid) if paid else Decimal("0"),
        "last_visit": visits[0].start_at if visits else None,
        "damage_rows": damage_rows, "damage_form": CarDamageForm(prefix="new"),
    })


def _save_damage_photos(damage, photos):
    for photo in photos:
        CarDamagePhoto.objects.create(
            damage=damage, file=photo, original_name=getattr(photo, "original_name", photo.name), size=photo.size,
        )


@require_POST
@tenant_permission_required("clients_manage")
def car_damage_create(request, car_id):
    car = get_object_or_404(Car, id=car_id, tenant=request.tenant, archived_at__isnull=True)
    form = CarDamageForm(request.POST, request.FILES, prefix="new")
    if form.is_valid():
        damage = form.save(commit=False)
        damage.tenant, damage.car = request.tenant, car
        damage.created_by = damage.updated_by = request.user
        damage.save()
        _save_damage_photos(damage, form.cleaned_data["photos"])
        log_event(tenant=request.tenant, actor=request.user, action="car_damage_create", entity=damage,
                  description=f"Добавлено повреждение автомобиля «{car}»")
        messages.success(request, "Повреждение добавлено.")
    else:
        messages.error(request, "Проверьте дату, описание и фотографии повреждения.")
    return redirect("car-detail", car_id=car.id)


@require_POST
@tenant_permission_required("clients_manage")
def car_damage_edit(request, car_id, damage_id):
    car = get_object_or_404(Car, id=car_id, tenant=request.tenant, archived_at__isnull=True)
    damage = get_object_or_404(CarDamage, id=damage_id, car=car, tenant=request.tenant, archived_at__isnull=True)
    form = CarDamageForm(
        request.POST, request.FILES, instance=damage, prefix=str(damage.id),
        existing_photo_count=damage.photos.count(),
    )
    if form.is_valid():
        damage = form.save(commit=False)
        damage.updated_by = request.user
        damage.save()
        _save_damage_photos(damage, form.cleaned_data["photos"])
        log_event(tenant=request.tenant, actor=request.user, action="car_damage_update", entity=damage,
                  description=f"Изменено повреждение автомобиля «{car}»")
        messages.success(request, "Повреждение сохранено.")
    else:
        messages.error(request, "Не удалось сохранить повреждение. Проверьте заполнение полей.")
    return redirect("car-detail", car_id=car.id)


@require_POST
@tenant_permission_required("clients_manage")
def car_damage_delete(request, car_id, damage_id):
    car = get_object_or_404(Car, id=car_id, tenant=request.tenant, archived_at__isnull=True)
    damage = get_object_or_404(CarDamage, id=damage_id, car=car, tenant=request.tenant, archived_at__isnull=True)
    damage.archived_at = timezone.now()
    damage.updated_by = request.user
    damage.save(update_fields=("archived_at", "updated_by", "updated_at"))
    log_event(tenant=request.tenant, actor=request.user, action="car_damage_archive", entity=damage,
              description=f"Удалено повреждение автомобиля «{car}»")
    messages.success(request, "Повреждение удалено.")
    return redirect("car-detail", car_id=car.id)


@require_POST
@tenant_permission_required("clients_manage")
def car_damage_photo_delete(request, car_id, damage_id, photo_id):
    car = get_object_or_404(Car, id=car_id, tenant=request.tenant, archived_at__isnull=True)
    damage = get_object_or_404(
        CarDamage, id=damage_id, car=car, tenant=request.tenant, archived_at__isnull=True
    )
    photo = get_object_or_404(CarDamagePhoto, id=photo_id, damage=damage)
    original_name = photo.original_name
    photo.file.delete(save=False)
    photo.delete()
    log_event(
        tenant=request.tenant, actor=request.user, action="car_damage_photo_delete", entity=damage,
        description=f"Удалена фотография «{original_name or 'без названия'}» у повреждения автомобиля «{car}»",
    )
    messages.success(request, "Фотография повреждения удалена.")
    return redirect("car-detail", car_id=car.id)


@tenant_permission_required("clients_manage")
def car_edit(request, car_id):
    car = get_object_or_404(
        Car, id=car_id, tenant=request.tenant, archived_at__isnull=True
    )
    form = CarForm(request.POST or None, instance=car, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        car = form.save()
        log_event(tenant=request.tenant, actor=request.user, action="car_update", entity=car,
                  description=f"Изменён автомобиль «{car}»")
        messages.success(request, "Данные автомобиля сохранены.")
        return redirect("car-detail", car_id=car.id)
    return render(request, "customers/form.html", {
        "form": form, "title": f"Редактирование — {car}", "cancel_url": f"/cars/{car.id}/",
    })


@require_POST
@tenant_permission_required("clients_delete")
@transaction.atomic
def car_archive(request, car_id):
    car = get_object_or_404(Car, id=car_id, tenant=request.tenant, archived_at__isnull=True)
    Client.objects.select_for_update().get(pk=car.client_id, tenant=request.tenant)
    car.archived_at = timezone.now()
    car.save(update_fields=("archived_at", "updated_at"))
    log_event(tenant=request.tenant, actor=request.user, action="car_archive", entity=car,
              description=f"Автомобиль «{car}» удалён из рабочих списков")
    messages.success(request, "Автомобиль удалён. Его можно восстановить в карточке клиента.")
    return redirect("client-detail", client_id=car.client_id)


@require_POST
@tenant_permission_required("clients_delete")
@transaction.atomic
def client_archive(request, client_id):
    client = get_object_or_404(Client.objects.select_for_update(), id=client_id,
                              tenant=request.tenant, archived_at__isnull=True)
    archived_at = timezone.now()
    client.archived_at = archived_at
    client.save(update_fields=("archived_at", "updated_at"))
    client.cars.filter(archived_at__isnull=True).update(archived_at=archived_at, updated_at=archived_at)
    log_event(tenant=request.tenant, actor=request.user, action="client_archive", entity=client,
              description=f"Клиент «{client.display_name}» удалён из рабочих списков")
    messages.success(request, "Клиент удалён. Восстановление доступно в разделе «Удалённые».")
    return redirect("client-list")


@require_POST
@tenant_permission_required("clients_delete")
@transaction.atomic
def client_restore(request, client_id):
    client = get_object_or_404(Client.objects.select_for_update(), id=client_id,
                              tenant=request.tenant, archived_at__isnull=False)
    archived_at = client.archived_at
    client.archived_at = None
    client.save(update_fields=("archived_at", "updated_at"))
    client.cars.filter(archived_at=archived_at).update(archived_at=None, updated_at=timezone.now())
    log_event(tenant=request.tenant, actor=request.user, action="client_restore", entity=client,
              description=f"Клиент «{client.display_name}» восстановлен")
    messages.success(request, "Клиент и удалённые вместе с ним автомобили восстановлены.")
    return redirect("client-detail", client_id=client.pk)


@require_POST
@tenant_permission_required("clients_delete")
@transaction.atomic
def car_restore(request, car_id):
    car = get_object_or_404(Car, id=car_id, tenant=request.tenant, archived_at__isnull=False)
    client = Client.objects.select_for_update().get(pk=car.client_id, tenant=request.tenant)
    if client.archived_at:
        messages.error(request, "Сначала восстановите клиента.")
        return redirect("client-detail", client_id=client.pk)
    car.archived_at = None
    car.save(update_fields=("archived_at", "updated_at"))
    log_event(tenant=request.tenant, actor=request.user, action="car_restore", entity=car,
              description=f"Автомобиль «{car}» восстановлен")
    messages.success(request, "Автомобиль восстановлен.")
    return redirect("car-detail", car_id=car.pk)


def _delete_car_files(car):
    for photo in CarDamagePhoto.objects.filter(damage__car=car).only("file"):
        photo.file.delete(save=False)


def _snapshot_car_appointments(car):
    Appointment.all_objects.filter(car=car).update(
        car_snapshot=str(car), car_make_snapshot=car.make,
        car_model_snapshot=car.model, car_plate_snapshot=car.plate,
    )


@require_POST
@tenant_permission_required("clients_permanent_delete")
@transaction.atomic
def car_permanent_delete(request, car_id):
    car = get_object_or_404(
        Car.objects.select_for_update().select_related("client"),
        id=car_id, tenant=request.tenant, archived_at__isnull=False,
    )
    client_id = car.client_id
    label = str(car)
    _snapshot_car_appointments(car)
    _delete_car_files(car)
    log_event(
        tenant=request.tenant, actor=request.user, action="car_permanent_delete", entity=car,
        description=f"Автомобиль «{label}» окончательно удалён",
    )
    car.delete()
    messages.success(request, "Автомобиль окончательно удалён. История записей сохранена.")
    return redirect("client-detail", client_id=client_id)


@require_POST
@tenant_permission_required("clients_permanent_delete")
@transaction.atomic
def client_permanent_delete(request, client_id):
    client = get_object_or_404(
        Client.objects.select_for_update(), id=client_id,
        tenant=request.tenant, archived_at__isnull=False,
    )
    label = client.display_name
    Appointment.all_objects.filter(client=client).update(
        client_name_snapshot=client.display_name, client_phone_snapshot=client.phone,
    )
    for car in client.cars.select_for_update().all():
        _snapshot_car_appointments(car)
        _delete_car_files(car)
    log_event(
        tenant=request.tenant, actor=request.user, action="client_permanent_delete", entity=client,
        description=f"Клиент «{label}» окончательно удалён",
    )
    client.delete()
    messages.success(request, "Клиент и его автомобили окончательно удалены. История записей сохранена.")
    return redirect("client-list")
