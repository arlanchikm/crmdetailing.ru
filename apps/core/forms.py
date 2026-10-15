from pathlib import Path

from django import forms
from .phones import PhoneField
from .images import optimize_image

from apps.accounts.models import User
from apps.tenants.models import Tenant
from apps.catalog.models import Service
from apps.customers.models import normalize_phone


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024


def validate_uploaded_image(file):
    if not file:
        return file
    if Path(file.name).suffix.lower() not in IMAGE_EXTENSIONS:
        raise forms.ValidationError("Загрузите изображение JPG, PNG или WebP.")
    if file.size > MAX_IMAGE_SIZE:
        raise forms.ValidationError("Размер изображения не должен превышать 5 МБ.")
    content_type = getattr(file, "content_type", "")
    if content_type and not content_type.startswith("image/"):
        raise forms.ValidationError("Выбранный файл не является изображением.")
    return file


class UserSettingsForm(forms.ModelForm):
    first_name = forms.CharField(label="Имя", max_length=150)
    avatar = forms.FileField(
        label="Фотография профиля",
        required=False,
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        help_text="JPG, PNG или WebP, до 5 МБ. Лучше использовать квадратное изображение.",
    )
    remove_avatar = forms.BooleanField(label="Удалить текущую фотографию", required=False)

    class Meta:
        model = User
        fields = ("first_name", "avatar")

    def clean_avatar(self):
        return optimize_image(validate_uploaded_image(self.cleaned_data.get("avatar")), kind="avatar")


class CompanySettingsForm(forms.ModelForm):
    logo = forms.FileField(
        label="Логотип компании",
        required=False,
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        help_text="Рекомендуемый размер 1200 × 400 px (3:1). JPG, PNG или WebP, до 5 МБ.",
    )
    remove_logo = forms.BooleanField(label="Удалить текущий логотип", required=False)

    class Meta:
        model = Tenant
        fields = ("name", "logo", "address", "latitude", "longitude")
        labels = {
            "name": "Название компании",
        }

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("latitude") is None) != (cleaned.get("longitude") is None):
            self.add_error("latitude", "Укажите обе координаты или оставьте оба поля пустыми.")
        if (self.instance.pk and cleaned.get("address") != self.initial.get("address")
                and cleaned.get("latitude") == self.initial.get("latitude")
                and cleaned.get("longitude") == self.initial.get("longitude")):
            cleaned["latitude"] = None
            cleaned["longitude"] = None
        if cleaned.get("latitude") is not None and not cleaned.get("address"):
            self.add_error("address", "Укажите адрес компании.")
        return cleaned

    def clean_logo(self):
        return optimize_image(validate_uploaded_image(self.cleaned_data.get("logo")), kind="logo")


class InterfaceSettingsForm(forms.ModelForm):
    class Meta:
        model = Tenant
        fields = (
            "language_code", "primary_color", "background_color",
        )
        labels = {
            "language_code": "Язык интерфейса",
            "primary_color": "Основной цвет и кнопки",
            "background_color": "Оттенок фоновой подложки",
            "surface_color": "Оттенок прозрачных блоков",
        }
        widgets = {
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "background_color": forms.TextInput(attrs={"type": "color"}),
            "surface_color": forms.TextInput(attrs={"type": "color"}),
        }


class WebsiteIntegrationForm(forms.Form):
    enabled = forms.BooleanField(label="Форма онлайн-записи включена", required=False)
    widget_title = forms.CharField(label="Заголовок формы", max_length=120)

    def __init__(self, *args, channel=None, **kwargs):
        default_title = (
            "Online booking"
            if channel and channel.tenant.language_code == Tenant.Language.EN
            else "Онлайн-запись"
        )
        initial = kwargs.setdefault("initial", {})
        initial.setdefault("enabled", channel.enabled if channel else False)
        initial.setdefault(
            "widget_title", (channel.settings or {}).get("widget_title", default_title)
            if channel else default_title,
        )
        super().__init__(*args, **kwargs)


class PublicBookingForm(forms.Form):
    client_name = forms.CharField(label="Имя", max_length=160)
    phone = PhoneField(label="Телефон")
    car = forms.CharField(label="Автомобиль", max_length=180, required=False)
    service = forms.ChoiceField(label="Услуга", required=False)
    preferred_at = forms.DateTimeField(
        label="Желаемые дата и время", required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=("%Y-%m-%dT%H:%M",),
    )
    comment = forms.CharField(
        label="Комментарий", required=False, widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, tenant=None, language="ru", **kwargs):
        super().__init__(*args, **kwargs)
        services = Service.objects.filter(tenant=tenant, active=True).exclude(system_key="other")
        self.fields["service"].choices = [("", "Не выбрана")] + [
            (str(row.id), row.name) for row in services
        ]
        if language == "en":
            labels = {
                "client_name": "Name", "phone": "Phone", "car": "Vehicle",
                "service": "Service", "preferred_at": "Preferred date and time",
                "comment": "Comment",
            }
            for name, label in labels.items():
                self.fields[name].label = label
            self.fields["service"].choices = [("", "Not selected")] + list(
                self.fields["service"].choices[1:]
            )
            self.fields["phone"].error_messages["invalid"] = "Enter a valid phone number."

    def clean_phone(self):
        value = self.cleaned_data["phone"].strip()
        if len(normalize_phone(value)) < 10:
            message = (
                "Enter a valid phone number."
                if self.fields["client_name"].label == "Name"
                else "Укажите корректный номер телефона."
            )
            raise forms.ValidationError(message)
        return value
