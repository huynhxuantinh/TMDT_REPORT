"""Hospital scope shared by HTTP reads and locked payment writes."""
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied

from core.models import LichKham, ThuNgan


def cashier_profile(user, *, lock=False):
    if not user.is_authenticated:
        return None
    profiles = ThuNgan.objects
    if lock:
        profiles = profiles.select_for_update()
    return profiles.filter(user_id=user.pk).first()


def can_collect(user):
    if not user.is_authenticated or not user.is_active:
        return False
    if cashier_profile(user):
        # Mixed administrative/clinical roles are not cashier identities.
        return not (user.is_staff or user.is_superuser or hasattr(user, 'bac_si'))
    return user.is_superuser or (user.is_staff and user.has_perm('core.thu_vnpay'))


def can_admin(user):
    return (user.is_authenticated and user.is_active
            and (user.is_staff or user.is_superuser) and not cashier_profile(user))


def scoped_appointments(user):
    profile = cashier_profile(user)
    if profile and can_collect(user):
        return LichKham.objects.filter(bac_si__benh_vien_id=profile.benh_vien_id)
    if not profile and (can_collect(user) or can_admin(user)):
        return LichKham.objects.all()
    return LichKham.objects.none()


def require_scope(user, appointment):
    """Call inside atomic after locking appointment; serialize reassignment too."""
    profile = cashier_profile(user, lock=True)
    fresh_user = User.objects.get(pk=user.pk)
    if not can_collect(fresh_user):
        raise PermissionDenied('Tài khoản không có quyền thu tiền tại quầy.')
    if profile and profile.benh_vien_id != appointment.bac_si.benh_vien_id:
        raise PermissionDenied('Hóa đơn không thuộc bệnh viện được phân công.')
