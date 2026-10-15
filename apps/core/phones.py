"""One phone policy for UI forms, imports and integration adapters."""
import re

import phonenumbers
from django import forms
from django.core.exceptions import ValidationError

PHONE_ERROR = 'Укажите российский номер в формате +7 (800) 555-35-35.'


def phone_digits(value):
    digits = re.sub(r'\D', '', str(value or ''))
    if len(digits) == 10:
        digits = '7' + digits
    elif len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    return digits


def format_phone(value):
    raw = str(value or '').strip()
    digits = phone_digits(raw)
    if not re.fullmatch(r'\+?[\d\s()\-]+', raw) or len(digits) != 11 or not digits.startswith('7'):
        raise ValidationError(PHONE_ERROR)
    try:
        number = phonenumbers.parse('+' + digits, None)
    except phonenumbers.NumberParseException:
        raise ValidationError(PHONE_ERROR)
    if not phonenumbers.is_valid_number_for_region(number, 'RU'):
        raise ValidationError(PHONE_ERROR)
    return f'+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}'


class PhoneInput(forms.TextInput):
    input_type = 'tel'

    def __init__(self, attrs=None):
        super().__init__({'autocomplete': 'tel', 'inputmode': 'tel',
            'placeholder': '+7 (800) 555-35-35', 'data-phone-input': '1', **(attrs or {})})


class PhoneField(forms.CharField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', PhoneInput())
        kwargs.setdefault('max_length', 40)
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        return format_phone(value) if value else ''
