from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def trim_decimal(value, places=3):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value
    text = f"{number:.{int(places)}f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")
