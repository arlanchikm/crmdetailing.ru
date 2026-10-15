from django import forms
from django.urls import reverse

from .models import Consumable, InventoryCategory, InventoryMovement, ensure_default_categories


class ConsumableForm(forms.ModelForm):
    def __init__(self, *args, tenant=None, location=None, advanced=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.location = location
        if tenant:
            ensure_default_categories(tenant)
            self.fields["category"].queryset = InventoryCategory.objects.filter(
                tenant=tenant, active=True
            )
        self.fields["category"].empty_label = "Выберите категорию"
        self.fields["category"].widget.attrs.update({
            "data-add-url": reverse("inventory-category-create") + "?modal=1",
            "data-add-label": "+ Добавить категорию",
        })
        self.fields["minimum_stock"].required = False
        self.fields["minimum_stock"].initial = self.instance.minimum_stock or 0
        if not advanced:
            self.fields["minimum_stock"].disabled = True
            self.fields["minimum_stock"].help_text = "Уведомления о минимальном остатке доступны на тарифе «Бизнес»."
        if not self.instance._state.adding:
            unit = self.instance.unit
            liquid = unit in (InventoryCategory.Unit.LITER, InventoryCategory.Unit.MILLILITER)
            self.fields["cost_per_unit"].label = "Стоимость за литр" if liquid else "Стоимость за шт."
            self.fields["actual_stock"].label = {
                InventoryCategory.Unit.LITER: "Факт (л.)",
                InventoryCategory.Unit.MILLILITER: "Факт (мл)",
                InventoryCategory.Unit.PIECE: "Факт (шт.)",
            }[unit]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self.tenant and Consumable.objects.filter(
            tenant=self.tenant, location=self.location, name__iexact=name
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Расходник с таким названием уже существует.")
        return name

    class Meta:
        model = Consumable
        fields = (
            "name", "category", "cost_per_unit", "actual_stock", "minimum_stock", "active",
        )
        help_texts = {
            "actual_stock": "Введите результат ручного подсчета.",
        }


class InventoryCategoryForm(forms.ModelForm):
    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        if not self.instance._state.adding and self.instance.consumables.exists():
            self.fields["unit"].disabled = True
            self.fields["unit"].help_text = "Формат нельзя изменить, пока в категории есть расходники."

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self.tenant and InventoryCategory.objects.filter(
            tenant=self.tenant, name__iexact=name
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Категория с таким названием уже существует.")
        return name

    class Meta:
        model = InventoryCategory
        fields = ("name", "unit", "active")


class StockMovementForm(forms.Form):
    kind = forms.ChoiceField(
        label="Операция",
        choices=(
            (InventoryMovement.Kind.RECEIPT, "Поступление"),
            (InventoryMovement.Kind.MANUAL_WRITEOFF, "Ручное списание"),
            (InventoryMovement.Kind.CORRECTION, "Корректировка остатка"),
            (InventoryMovement.Kind.INVENTORY, "Инвентаризация"),
        ),
    )
    quantity = forms.DecimalField(label="Количество", min_value=0, decimal_places=3)
    unit_cost = forms.DecimalField(
        label="Стоимость за единицу", min_value=0, decimal_places=2, required=False
    )
    reason = forms.CharField(label="Причина или комментарий", max_length=240, required=False)

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        if kind in (InventoryMovement.Kind.CORRECTION, InventoryMovement.Kind.INVENTORY):
            if not (cleaned.get("reason") or "").strip():
                self.add_error("reason", "Укажите причину корректировки или результат инвентаризации.")
        if kind == InventoryMovement.Kind.RECEIPT and cleaned.get("quantity") == 0:
            self.add_error("quantity", "Количество поступления должно быть больше нуля.")
        return cleaned
