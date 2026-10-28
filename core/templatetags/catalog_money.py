from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def vnd(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            return '—'
        return format(amount, ',.0f').replace(',', '.') + ' VND'
    except (InvalidOperation, ValueError, TypeError):
        return '—'
