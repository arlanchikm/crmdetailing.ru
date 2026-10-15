from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.db.models import Q

from apps.inventory.models import ServiceConsumableNorm
from apps.customers.models import VehicleClass, ensure_default_vehicle_classes

from .models import (
    Service, ServiceCategory, ServiceVehicleClassPrice, WorkPost,
    ensure_default_service_categories,
)


class ServiceForm(forms.ModelForm):
    def __init__(self, *args, tenant=None, location=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.location = location
        if tenant:
            ensure_default_service_categories(tenant, location)
            categories = ServiceCategory.objects.filter(tenant=tenant, location=location)
            if self.instance.pk and self.instance.category_id:
                categories = categories.filter(Q(active=True) | Q(pk=self.instance.category_id))
            else:
                categories = categories.filter(active=True)
            self.fields["category"].queryset = categories
            self.fields["category"].empty_label = "Без категории"
        self.fields["price"].required = False
        self.fields["price"].widget = forms.HiddenInput()
        self.fields["employee_percent"].required = False
        self.fields["employee_percent"].initial = self.instance.employee_percent or 0
        self.vehicle_class_rows = []
        if tenant:
            ensure_default_vehicle_classes(tenant)
            existing = {
                row.vehicle_class_id: row
                for row in self.instance.vehicle_class_prices.all()
            } if self.instance.pk else {}
            for vehicle_class in VehicleClass.objects.filter(tenant=tenant, active=True):
                field_name = f"class_price_{vehicle_class.id}"
                saved = existing.get(vehicle_class.id)
                initial = saved.price if saved else None
                self.fields[field_name] = forms.DecimalField(
                    label=vehicle_class.name, min_value=0, decimal_places=2,
                    required=False, initial=initial,
                    widget=forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
                )
                duration_name = f"class_duration_{vehicle_class.id}"
                self.fields[duration_name] = forms.IntegerField(
                    label="Время, минут", min_value=1, required=False,
                    initial=saved.duration_minutes if saved else None,
                    widget=forms.NumberInput(attrs={"min": "1", "placeholder": "Общий норматив"}),
                )
                self.vehicle_class_rows.append({"vehicle_class": vehicle_class, "field": self[field_name], "duration_field": self[duration_name]})

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self.tenant:
            existing = Service.objects.filter(
                tenant=self.tenant, location=self.location, name__iexact=name,
            ).exclude(
                pk=self.instance.pk
            )
            if existing.exists():
                raise forms.ValidationError("Услуга с таким названием уже существует.")
        return name

    def clean(self):
        cleaned = super().clean()
        prices = [
            cleaned.get(f"class_price_{row['vehicle_class'].id}")
            for row in self.vehicle_class_rows
        ]
        prices = [price for price in prices if price is not None]
        has_class_fields_in_request = any(
            str(name).startswith("class_price_") for name in self.data
        )
        if not prices and cleaned.get("price") is not None and not has_class_fields_in_request:
            first_row = self.vehicle_class_rows[0] if self.vehicle_class_rows else None
            if first_row:
                field_name = f"class_price_{first_row['vehicle_class'].id}"
                cleaned[field_name] = cleaned["price"]
                prices = [cleaned["price"]]
        if not prices:
            self.add_error(None, "Укажите цену хотя бы для одного класса автомобиля.")
        else:
            cleaned["price"] = min(prices)
            self.instance.price = min(prices)
        return cleaned

    def save_vehicle_class_prices(self, service):
        active_ids = []
        for row in self.vehicle_class_rows:
            vehicle_class = row["vehicle_class"]
            price = self.cleaned_data.get(f"class_price_{vehicle_class.id}")
            duration = self.cleaned_data.get(f"class_duration_{vehicle_class.id}")
            if price is None and duration is None:
                continue
            active_ids.append(vehicle_class.id)
            ServiceVehicleClassPrice.objects.update_or_create(
                service=service, vehicle_class=vehicle_class,
                defaults={"tenant": service.tenant, "price": price, "duration_minutes": duration},
            )
        service.vehicle_class_prices.exclude(vehicle_class_id__in=active_ids).delete()

    class Meta:
        model = Service
        fields = (
            "name", "category", "price", "duration_minutes", "employee_percent",
            "calculation_type", "active",
        )


class ServiceCategoryForm(forms.ModelForm):
    def __init__(self, *args, tenant=None, location=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.location = location

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self.tenant and ServiceCategory.objects.filter(
            tenant=self.tenant, location=self.location, name__iexact=name,
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Категория с таким названием уже существует.")
        return name

    class Meta:
        model = ServiceCategory
        fields = ("name", "active", "sort_order")


class ServiceConsumableNormForm(forms.ModelForm):
    def __init__(self, *args, tenant=None, location=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["consumable"].label = "Расходник"
        self.fields["consumable"].queryset = self.fields["consumable"].queryset.filter(
            tenant=tenant, location=location, active=True, category__active=True
        ).select_related("category")
        self.fields["consumable"].empty_label = "Выберите расходник"

    class Meta:
        model = ServiceConsumableNorm
        fields = ("consumable", "quantity")
        widgets = {"quantity": forms.NumberInput(attrs={"min": "0.001", "step": "0.001"})}


class ConsumableNormFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        seen = set()
        for form in self.forms:
            data = getattr(form, "cleaned_data", {})
            if data.get("DELETE") or not data.get("consumable"):
                continue
            key = data["consumable"].pk
            if key in seen:
                raise forms.ValidationError("Расходник уже добавлен в этот норматив.")
            seen.add(key)


ServiceConsumableNormFormSet = inlineformset_factory(
    Service,
    ServiceConsumableNorm,
    form=ServiceConsumableNormForm,
    formset=ConsumableNormFormSet,
    fields=("consumable", "quantity"),
    extra=0,
    can_delete=True,
)


class WorkPostForm(forms.ModelForm):
    def __init__(self, *args, tenant=None, location=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.location = location

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self.tenant:
            existing = WorkPost.objects.filter(
                tenant=self.tenant, location=self.location, name__iexact=name,
            ).exclude(
                pk=self.instance.pk
            )
            if existing.exists():
                raise forms.ValidationError("Рабочий пост с таким названием уже существует.")
        return name

    class Meta:
        model = WorkPost
        fields = ("name", "kind", "active")
