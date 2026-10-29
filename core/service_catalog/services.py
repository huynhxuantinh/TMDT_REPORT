from ipaddress import ip_address

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from core.models import DanhMucDichVu, LogHeThong
from core.payments.access import can_collect
from .forms import DichVuForm


def can_manage(user):
    return can_collect(user)


def require_admin(user):
    if not can_manage(user):
        raise PermissionDenied('Chỉ quản lý chi nhánh hoặc tài khoản có quyền thu tiền mới được quản lý dịch vụ.')


def snapshot(service):
    return {
        'ma_dich_vu': service.ma_dich_vu, 'ten': service.ten, 'nhom': service.nhom,
        'mo_ta': service.mo_ta, 'don_gia': format(service.don_gia, 'f'),
        'dang_ap_dung': service.dang_ap_dung,
    }


def record_change(service, before, user=None, remote_addr='', note=''):
    try:
        address = str(ip_address(remote_addr))
    except ValueError:
        address = None
    return LogHeThong.objects.create(
        model_name='DanhMucDichVu', object_id=service.pk,
        hanh_dong='create' if before is None else 'update',
        du_lieu_cu=before, du_lieu_moi=snapshot(service),
        nguoi_thuc_hien=user, ip_address=address, ghi_chu=note,
    )


def save_service(data, user, pk=None, remote_addr=''):
    require_admin(user)
    with transaction.atomic():
        service = DanhMucDichVu.objects.select_for_update().get(pk=pk) if pk else DanhMucDichVu()
        before = snapshot(service) if pk else None
        form = DichVuForm(data, instance=service)
        if not form.is_valid():
            return None, form
        try:
            # Savepoint also handles two concurrent requests using the same code.
            with transaction.atomic():
                if before != snapshot(form.instance):
                    service = form.save()
                    record_change(service, before, user, remote_addr, 'Cập nhật danh mục và bảng giá chung')
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
            return None, form
        except IntegrityError:
            form.add_error(None, 'Không thể lưu: mã dịch vụ bị trùng hoặc dữ liệu không hợp lệ. Vui lòng kiểm tra lại.')
            return None, form
    return service, form


@transaction.atomic
def set_service_status(pk, active, user, remote_addr=''):
    require_admin(user)
    if type(active) is not bool:
        raise ValidationError('Trạng thái áp dụng không hợp lệ.')
    service = DanhMucDichVu.objects.select_for_update().get(pk=pk)
    if service.dang_ap_dung != active:
        before = snapshot(service)
        service.dang_ap_dung = active
        service.save(update_fields=['dang_ap_dung', 'ngay_cap_nhat'])
        record_change(service, before, user, remote_addr, 'Áp dụng dịch vụ' if active else 'Ngừng áp dụng dịch vụ')
    return service
