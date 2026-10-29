"""Which transaction states still allow a new charge for the same invoice.

A local timer is not proof that no money moved. Only VNPAY may declare a
transaction finished (failed / cancelled / expired); everything else keeps the
invoice frozen until the late-IPN window is over or an operator decides.
"""
from datetime import timedelta

from django.conf import settings

#: A new charge may be created when every attempt is in one of these states.
RELEASABLE = ('failed', 'cancelled', 'expired', 'timeout')
#: Terminal states that still mean "money may have moved".
HELD = ('success', 'duplicate')


def grace_minutes():
    return getattr(settings, 'VNPAY_IPN_GRACE_MINUTES', 45)


def grace_deadline(attempt):
    """Last moment a late IPN is still expected for this attempt."""
    if attempt.het_han_luc is None:
        return None
    return attempt.het_han_luc + timedelta(minutes=grace_minutes())


def blocks_new_charge(attempt, now):
    """True while this attempt may still resolve to a successful payment."""
    if attempt.trang_thai == 'pending':
        deadline = grace_deadline(attempt)
        return deadline is None or now < deadline
    return attempt.trang_thai in HELD


def any_blocking(attempts, now):
    return any(blocks_new_charge(attempt, now) for attempt in attempts)
