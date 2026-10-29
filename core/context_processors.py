"""Values every server-rendered page needs: brand name and shared navigation rights."""
from django.conf import settings
from django.db import DatabaseError

from core.payments.access import can_admin, can_collect


def _safe(rule, user):
    """Never let navigation break a page: permission lookups touch the database."""
    try:
        return bool(rule(user))
    except (DatabaseError, AttributeError):
        return False


def brand(request):
    user = getattr(request, 'user', None)
    authenticated = bool(user is not None and user.is_authenticated)
    return {
        'brand_name': getattr(settings, 'SITE_BRAND_NAME', 'MedCare GIS'),
        'brand_code': getattr(settings, 'SITE_BRAND_CODE', 'QL_BENHVIEN'),
        'nav_can_collect': authenticated and _safe(can_collect, user),
        'nav_can_admin': authenticated and _safe(can_admin, user),
    }
