from django import forms
from apps.core.phones import PhoneField

from django.utils import timezone

from .models import (
    Car, CarDamage, Client, VehicleClass, ensure_default_vehicle_classes, normalize_plate,
)


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        values = data if isinstance(data, (list, tuple)) else [data] if data else []
        return [super(MultipleFileField, self).clean(value, initial) for value in values]


class ClientForm(forms.ModelForm):
    phone = PhoneField(label='Телефон *')
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].label = "Имя *"
        self.fields["phone"].label = "Телефон *"

    class Meta:
        model = Client
        fields = ("name", "last_name", "phone", "email", "notes")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Иван"}),
            "last_name": forms.TextInput(attrs={"placeholder": "Петров"}),
            "phone": forms.TextInput(attrs={"placeholder": "+7 999 123-45-67"}),
            "email": forms.EmailInput(attrs={"placeholder": "client@example.com"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }


class CarForm(forms.ModelForm):
    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        if tenant:
            # ModelForm runs Car.clean() during is_valid(), before the view gets a
            # chance to attach the tenant to a newly-created car.
            self.instance.tenant = tenant
            ensure_default_vehicle_classes(tenant)
            self.fields["vehicle_class"].queryset = VehicleClass.objects.filter(
                tenant=tenant, active=True
            )
            self.fields["vehicle_class"].label = "Класс автомобиля *"
            self.fields["vehicle_class"].empty_label = "Выберите класс"
            self.fields["vehicle_class"].required = False
            self.fields["vehicle_class"].widget.attrs["required"] = "required"
        self.fields["make"].required = False
        self.fields["model"].required = False

    def clean_plate(self):
        plate = self.cleaned_data["plate"]
        normalized = normalize_plate(plate)
        if normalized and self.tenant:
            existing = Car.objects.filter(
                tenant=self.tenant, normalized_plate=normalized
            ).exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError("Автомобиль с таким госномером уже существует.")
        return plate

    def clean_vehicle_class(self):
        vehicle_class = self.cleaned_data.get("vehicle_class")
        if not vehicle_class and self.tenant:
            vehicle_class = ensure_default_vehicle_classes(self.tenant)["sedan"]
        return vehicle_class

    class Meta:
        model = Car
        fields = ("vehicle_class", "make", "model", "plate", "color", "vin", "notes")
        widgets = {
            "make": forms.TextInput(attrs={"placeholder": "BMW"}),
            "model": forms.TextInput(attrs={"placeholder": "X5"}),
            "plate": forms.TextInput(attrs={"placeholder": "А123ВС77"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class VehicleClassForm(forms.ModelForm):
    image = forms.FileField(label="Изображение класса", required=False, widget=forms.FileInput(attrs={"accept":"image/png,image/jpeg,image/webp"}), help_text="PNG с прозрачностью, JPG или WebP, до 5 МБ. Автомобиль будет вписан без обрезания.")

    def clean_image(self):
        from apps.core.forms import validate_uploaded_image
        from PIL import Image
        from django.core.files.base import ContentFile
        from io import BytesIO
        file = validate_uploaded_image(self.cleaned_data.get("image"))
        if not file:
            return file
        try:
            with Image.open(file) as source:
                source.load()
                result = source.convert("RGBA")
                result.thumbnail((1200,600))
                canvas = Image.new("RGBA", (1200,600), (0,0,0,0))
                canvas.alpha_composite(result, ((1200-result.width)//2,(600-result.height)//2))
                output = BytesIO()
                canvas.save(output, format="WEBP", quality=90, method=4)
        except (OSError, ValueError):
            raise forms.ValidationError("Не удалось прочитать изображение.")
        return ContentFile(output.getvalue(), name="vehicle-class.webp")

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self.tenant and VehicleClass.objects.filter(
            tenant=self.tenant, name__iexact=name
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Класс с таким названием уже существует.")
        return name

    class Meta:
        model = VehicleClass
        fields = ("name", "image", "sort_order", "active")


class CarDamageForm(forms.ModelForm):
    photos = MultipleFileField(
        label="Фотографии", required=False, widget=MultipleFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        help_text="До 5 фотографий, каждая не более 10 МБ.",
    )

    def __init__(self, *args, existing_photo_count=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.existing_photo_count = existing_photo_count
        if not self.is_bound and not self.instance.discovered_on:
            self.initial["discovered_on"] = timezone.localdate()

    def clean_photos(self):
        photos = self.cleaned_data.get("photos") or []
        if self.existing_photo_count + len(photos) > 5:
            raise forms.ValidationError("У одного повреждения может быть не больше 5 фотографий.")
        allowed = {"image/jpeg", "image/png", "image/webp"}
        for photo in photos:
            if photo.size > 10 * 1024 * 1024:
                raise forms.ValidationError(f"Файл «{photo.name}» больше 10 МБ.")
            if getattr(photo, "content_type", "") not in allowed:
                raise forms.ValidationError(f"Файл «{photo.name}» должен быть JPG, PNG или WebP.")
        from apps.core.images import optimize_image
        optimized = []
        for photo in photos:
            image = optimize_image(photo, kind="damage")
            image.original_name = photo.name
            optimized.append(image)
        return optimized

    def clean(self):
        cleaned = super().clean()
        x, y = cleaned.get("x_percent"), cleaned.get("y_percent")
        if x is not None and y is not None:
            polygons = (
                ((3,18),(18,18),(22,30),(22,70),(18,83),(3,83),(1,70),(1,30)),
                ((23,35),(26,31),(38,26),(59,25),(68,29),(73,36),(72,45),(65,48),(31,48),(23,43)),
                ((75,31),(78,26),(94,26),(98,31),(97,47),(76,47)),
                ((23,62),(26,59),(39,54),(59,54),(67,58),(73,66),(72,75),(31,76),(24,71)),
                ((75,60),(78,54),(94,54),(98,60),(97,77),(76,77)),
            )
            def inside(points):
                result = False
                previous = points[-1]
                for current in points:
                    if ((current[1] > y) != (previous[1] > y)) and (
                        x < (previous[0] - current[0]) * (y - current[1]) /
                        (previous[1] - current[1]) + current[0]
                    ):
                        result = not result
                    previous = current
                return result
            if not any(inside(points) for points in polygons):
                self.add_error("x_percent", "Поставьте отметку непосредственно на изображении автомобиля.")
        return cleaned

    class Meta:
        model = CarDamage
        fields = ("x_percent", "y_percent", "discovered_on", "description", "photos")
        widgets = {
            "x_percent": forms.HiddenInput(), "y_percent": forms.HiddenInput(),
            "discovered_on": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 3, "placeholder": "Опишите повреждение"}),
        }
