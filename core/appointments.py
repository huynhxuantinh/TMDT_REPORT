"""Locked appointment writes and explicit clinical transition policy."""
import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import BacSi, BenhNhan, LichKham, LogHeThong


def validate_exam_time(appointment, user, reason=''):
    now = timezone.localtime()
    scheduled = timezone.make_aware(datetime.datetime.combine(
        appointment.ngay_kham, appointment.gio_kham), timezone.get_current_timezone())
    permission = None
    if scheduled > now:
        permission = 'core.kham_som'
    elif appointment.ngay_kham < now.date():
        permission = 'core.kham_hoi_to'
    if permission:
        if not user.has_perm(permission):
            raise ValidationError('Chưa đến giờ khám hoặc lịch thuộc ngày trước. Cần quyền khám sớm/nhập hồi tố riêng.')
        if not 10 <= len(reason.strip()) <= 500:
            raise ValidationError('Ngoại lệ thời gian cần lý do từ 10 đến 500 ký tự để lưu kiểm toán.')
    return permission


def transition_exam(appointment, user, target, reason=''):
    """Caller holds appointment lock inside atomic, including exam creation."""
    if not user.is_active or appointment.bac_si.user_id != user.pk:
        raise PermissionDenied('Chỉ bác sĩ phụ trách được chuyển trạng thái khám.')
    if (appointment.trang_thai, target) not in {('cho', 'dang'), ('dang', 'xong')}:
        raise ValidationError('Chỉ chuyển từ chờ khám → đang khám → đã xong. Hãy tải lại trang.')
    exception = validate_exam_time(appointment, user, reason)
    before = appointment.trang_thai
    appointment.trang_thai = target
    appointment.save(update_fields=['trang_thai', 'ngay_cap_nhat'])
    LogHeThong.objects.create(
        model_name='LichKham', object_id=appointment.pk, hanh_dong='update',
        nguoi_thuc_hien=user, du_lieu_cu={'trang_thai': before},
        du_lieu_moi={'trang_thai': target, 'quyen_ngoai_le': exception},
        ghi_chu=('Ngoại lệ thời gian: ' + reason.strip()) if exception else 'Chuyển trạng thái khám.',
    )


@transaction.atomic
def book_appointment(appointment):
    # Patient first, doctor second: serializes both one/day and slot validation.
    BenhNhan.objects.select_for_update().get(pk=appointment.benh_nhan_id)
    BacSi.objects.select_for_update().get(pk=appointment.bac_si_id)
    appointment.full_clean()
    appointment.save()
    return appointment
