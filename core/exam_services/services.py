import hashlib
import json
from ipaddress import ip_address

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q

from core.models import DanhMucDichVu, DichVuThucHien, HoaDon, LichKham, LogHeThong, PhieuKham
from core.billing.services import BillingError, bill_exam, billing_context, invoice_lock_reason, is_consultation
from core.payments.access import can_collect, require_scope
from core.payments.services import _expire_pending_locked, require_completed
from .forms import PerformedServiceFormSet


class ExamServiceError(ValidationError):
    pass


def revision(rows):
    # Hash notes for stale-form detection; never put note contents into audit logs.
    values = sorted((row.pk, row.dich_vu_id, row.so_luong, row.ghi_chu, row.thoi_gian_cap_nhat.isoformat()) for row in rows)
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def lock_reason(appointment):
    try:
        require_completed(appointment)
    except Exception as exc:
        return str(exc)
    invoice = HoaDon.objects.filter(lich_kham_id=appointment.pk).first()
    if invoice:
        return invoice_lock_reason(invoice)
    return ''


def form_context(appointment, phieu=None, data=None):
    rows = list(phieu.dich_vu_thuc_hien.filter(da_huy=False).select_related('dich_vu', 'nguoi_thuc_hien')) if phieu else []
    costs = billing_context(phieu, rows)
    frozen = {row.dich_vu_id: row for row in rows}
    existing_ids = [row.dich_vu_id for row in rows]
    submitted = data is not None and any(key.startswith('services-') for key in data)
    formset = PerformedServiceFormSet(
        data if submitted else None, prefix='services',
        initial=[{'dich_vu': row.dich_vu_id, 'so_luong': row.so_luong, 'ghi_chu': row.ghi_chu} for row in rows],
        form_kwargs={'existing_ids': existing_ids},
    )
    catalog = DanhMucDichVu.objects.filter(Q(dang_ap_dung=True) | Q(pk__in=existing_ids)).order_by('ma_dich_vu')
    return {
        **costs,
        'services_formset': formset,
        'services_submitted': submitted,
        'services_revision': data.get('services_revision', '') if submitted else revision(rows),
        'services_lock_reason': lock_reason(appointment),
        'performed_services': rows,
        'services_catalog': [dict(id=entry.pk, code=entry.ma_dich_vu, name=entry.ten,
                                  price=str(frozen[entry.pk].display_price if entry.pk in frozen else entry.don_gia),
                                  consultation=is_consultation(entry.nhom), active=entry.dang_ap_dung) for entry in catalog],
    }


def audit_line(row, action, user, before_quantity=None, remote_addr='', note_changed=False):
    try:
        address = str(ip_address(remote_addr))
    except ValueError:
        address = None
    identifiers = {'phieu_kham_id': row.phieu_kham_id, 'dich_vu_id': row.dich_vu_id,
                   'ma_dich_vu': row.dich_vu.ma_dich_vu, 'bac_si_id': row.nguoi_thuc_hien_id,
                   'nguoi_ghi_nhan_id': row.nguoi_ghi_nhan_id}
    LogHeThong.objects.create(
        model_name='DichVuThucHien', object_id=row.pk, hanh_dong=action,
        du_lieu_cu={**identifiers, 'so_luong': before_quantity} if action != 'create' else None,
        du_lieu_moi={**identifiers, 'so_luong': row.so_luong} if action != 'delete' else None,
        nguoi_thuc_hien=user, ip_address=address,
        ghi_chu='Ghi nhận dịch vụ thực hiện' + ('; có sửa ghi chú' if note_changed else ''),
    )


@transaction.atomic
def sync_services(phieu, user, formset, submitted_revision, remote_addr=''):
    # Same first lock as create_invoice/start_payment; invoice creation and service
    # changes cannot pass their checks concurrently for the same appointment.
    appointment = LichKham.objects.select_for_update().get(pk=phieu.lich_kham_id)
    if not can_collect(user):
        raise PermissionDenied('Chỉ quản lý chi nhánh hoặc tài khoản có quyền thu tiền được ghi nhận dịch vụ.')
    require_scope(user, appointment)
    # Serialize with IPN too: IPN locks invoice before checking/setting its state.
    invoice = HoaDon.objects.select_for_update().filter(lich_kham=appointment).first()
    if invoice:
        _expire_pending_locked(invoice, actor=user)
    phieu = PhieuKham.objects.select_for_update().get(pk=phieu.pk, lich_kham=appointment)
    current = list(DichVuThucHien.objects.select_for_update().filter(phieu_kham=phieu, da_huy=False).order_by('pk'))
    if submitted_revision != revision(current):
        raise ExamServiceError('Danh sách dịch vụ đã thay đổi. Vui lòng tải lại trang trước khi lưu tiếp.')
    if not formset.is_valid():
        raise ExamServiceError('Danh sách dịch vụ chưa hợp lệ. Vui lòng kiểm tra số lượng và dịch vụ đã chọn.')
    desired = {}
    for form in formset.forms:
        data = form.cleaned_data
        if data and not data.get('DELETE') and data.get('dich_vu'):
            desired[data['dich_vu'].pk] = (data['so_luong'], data['ghi_chu'])
    before = {row.dich_vu_id: (row.so_luong, row.ghi_chu) for row in current}
    if before == desired:
        return
    reason = lock_reason(appointment)
    if reason:
        raise ExamServiceError(reason)
    # Re-read and lock authoritative catalog state at commit time. No client price.
    catalog = {entry.pk: entry for entry in DanhMucDichVu.objects.select_for_update().filter(pk__in=desired).order_by('pk')}
    if any(key not in catalog or (not catalog[key].dang_ap_dung and key not in before) for key in desired):
        raise ExamServiceError('Có dịch vụ đã ngừng áp dụng hoặc không còn tồn tại. Vui lòng chọn lại.')
    for row in current:
        if row.dich_vu_id not in desired:
            audit_line(row, 'delete', user, row.so_luong, remote_addr)
            if row.chi_tiet_hoa_don.exists():
                row.da_huy = True
                row.save(update_fields=['da_huy', 'thoi_gian_cap_nhat'])
            else:
                row.delete()
        elif before[row.dich_vu_id] != desired[row.dich_vu_id]:
            old_quantity, old_note = row.so_luong, row.ghi_chu
            row.so_luong, row.ghi_chu = desired[row.dich_vu_id]
            row.full_clean()
            row.save(update_fields=['so_luong', 'ghi_chu', 'thoi_gian_cap_nhat'])
            audit_line(row, 'update', user, old_quantity, remote_addr, old_note != row.ghi_chu)
    for key in sorted(desired.keys() - before.keys()):
        quantity, note = desired[key]
        row = DichVuThucHien(phieu_kham=phieu, dich_vu=catalog[key], so_luong=quantity,
                            ghi_chu=note, nguoi_thuc_hien_id=appointment.bac_si_id,
                            nguoi_ghi_nhan_id=user.pk)
        row.full_clean()
        row.save()
        audit_line(row, 'create', user, remote_addr=remote_addr)
    if appointment.trang_thai == 'xong':
        try:
            bill_exam(phieu.pk, user, recalculate=True, remote_addr=remote_addr)
        except BillingError as exc:
            raise ExamServiceError(exc.messages) from exc
