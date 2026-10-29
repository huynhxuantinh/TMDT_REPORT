"""Presentation helpers for the three payment status layers.

Invoice status, transaction status and reconciliation state are different
concepts; each one gets its own badge so a collector can scan them apart.
"""
from django import template

register = template.Library()

TONES = {
    'paid': 'paid',
    'success': 'paid',
    'sent': 'paid',
    'unpaid': 'warn',
    'pending': 'pending',
    'sending': 'pending',
    'failed': 'danger',
    'cancelled': 'danger',
    'expired': 'danger',
    'unknown': 'danger',
    'skipped': 'neutral',
}


@register.filter
def tone(value):
    return TONES.get(value, 'neutral')
