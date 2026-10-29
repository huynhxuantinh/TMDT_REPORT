"""Invoice snapshots and revisions. Lock order: appointment, invoice, exam, services."""
from decimal import Decimal
from ipaddress import ip_address

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import (BacSi, ChiTietHoaDon, DanhMucDichVu, DichVuThucHien,
                         HoaDon, LichKham, LogHeThong, PhieuKham)
from core.insurance.services import calculate_support, support_amount


class BillingError(ValidationError):
    pass


def invoice_lock_reason(invoice):
    if invoice.nguon != HoaDon.Nguon.DICH_VU:
        return 'Đã có hóa đơn thử nghiệm cũ. Giữ nguyên dịch vụ và số tiền; không tự chuyển đổi hóa đơn này.'
    if invoice.can_doi_soat:
        return 'Hóa đơn cần đối soát VNPAY; chưa được điều chỉnh dịch vụ hoặc thu tiếp.'
    if invoice.trang_thai != HoaDon.TrangThai.CHUA_THANH_TOAN:
        if invoice.hoan_tat_bhyt:
            return 'Hóa đơn đã hoàn tất nhờ BHYT MÔ PHỎNG, bệnh nhân trả 0đ. Dịch vụ và chi phí đã khóa.'
        return 'Hóa đơn đã thanh toán. Dịch vụ và chi phí đã khóa.'
    if invoice.giao_dichs.exclude(trang_thai__in=['failed', 'cancelled', 'expired']).exists():
        return 'VNPAY đang xử lý, chưa xác định kết quả hoặc đã thành công. Chưa được điều chỉnh dịch vụ.'
    return ''


def is_consultation(group):
    # These are category labels already used by the shared catalog, never service codes.
    return ' '.join(group.casefold().split()) in {'khám', 'khám bệnh'}


def billing_context(phieu, rows=None):
    rows = list(phieu.dich_vu_thuc_hien.filter(da_huy=False).select_related('dich_vu')) if rows is None and phieu else (rows or [])
    invoice = HoaDon.objects.filter(lich_kham_id=phieu.lich_kham_id).first() if phieu else None
    lines = list(invoice.chi_tiet_hien_hanh) if invoice and invoice.nguon == HoaDon.Nguon.DICH_VU else []
    by_row = {line.dich_vu_thuc_hien_id: line for line in lines}
    for row in rows:
        line = by_row.get(row.pk)
        row.display_price = line.don_gia if line else row.dich_vu.don_gia
        row.display_code = line.ma_dich_vu if line else row.dich_vu.ma_dich_vu
        row.display_name = line.ten_dich_vu if line else row.dich_vu.ten
        row.line_total = row.display_price * row.so_luong
    return {'billing_invoice': invoice, 'billing_lines': lines, 'performed_services': rows,
            'services_total': sum((row.line_total for row in rows), Decimal('0'))}


def cost_snapshot(invoice):
    return {'phien_ban': invoice.phien_ban, 'so_tien': str(invoice.so_tien),
            'phieu_kham_id': invoice.phieu_kham_id,
            'tong_chi_phi': str(invoice.tong_truoc_bhyt), 'bhyt_ho_tro': str(invoice.bhyt_ho_tro),
            'hoan_tat_bhyt': invoice.hoan_tat_bhyt, 'bhyt_snapshot': invoice.bhyt_snapshot,
            'chi_tiet': [dict(dich_vu_thuc_hien_id=line.dich_vu_thuc_hien_id,
                             dich_vu_id=line.dich_vu_id, ma_dich_vu=line.ma_dich_vu,
                             so_luong=line.so_luong, don_gia=str(line.don_gia), thanh_tien=str(line.thanh_tien),
                             bhyt_ho_tro=str(line.bhyt_ho_tro), ty_le_bhyt=str(line.ty_le_bhyt),
                             bhyt_snapshot=line.bhyt_snapshot)
                         for line in invoice.chi_tiet_hien_hanh]}


def audit_invoice(invoice, before, user, remote_addr=''):
    try:
        address = str(ip_address(remote_addr))
    except ValueError:
        address = None
    LogHeThong.objects.create(model_name='HoaDon', object_id=invoice.pk,
                             hanh_dong='update' if before else 'create', du_lieu_cu=before,
                             du_lieu_moi=cost_snapshot(invoice), nguoi_thuc_hien=user,
                             ip_address=address, ghi_chu='Tính chi phí từ dịch vụ thực hiện')


@transaction.atomic
def bill_exam(phieu_id, user, *, recalculate=False, remote_addr=''):
    """Create or recalculate a completed exam invoice within the collector scope."""
    from core.payments.access import can_collect, require_scope

    appointment_id = PhieuKham.objects.values_list('lich_kham_id', flat=True).get(pk=phieu_id)
    appointment = LichKham.objects.select_for_update().get(pk=appointment_id)
    if not can_collect(user):
        raise PermissionDenied('Chỉ quản lý chi nhánh hoặc tài khoản có quyền thu tiền được tính chi phí.')
    require_scope(user, appointment)
    if appointment.trang_thai != 'xong':
        raise BillingError('Chỉ lập hóa đơn sau khi hoàn tất khám.')
    invoice = HoaDon.objects.select_for_update().filter(lich_kham=appointment).first()
    if invoice and not recalculate:
        return invoice
    if invoice:
        reason = invoice_lock_reason(invoice)
        if reason:
            raise BillingError(reason)
    phieu = PhieuKham.objects.select_for_update().get(pk=phieu_id, lich_kham=appointment)
    if phieu.benh_nhan_id != appointment.benh_nhan_id:
        raise BillingError('Phiếu khám không khớp bệnh nhân của lịch khám.')
    rows = list(DichVuThucHien.objects.select_for_update().filter(phieu_kham=phieu, da_huy=False).order_by('pk'))
    if not rows:
        raise BillingError('Chưa có dịch vụ thực hiện. Hãy chọn dịch vụ và phí khám thực tế trước khi hoàn tất/lưu phiếu.')
    old_lines = {line.dich_vu_thuc_hien_id: line for line in invoice.chi_tiet_hien_hanh} if invoice else {}
    catalog = {entry.pk: entry for entry in DanhMucDichVu.objects.select_for_update().filter(
        pk__in=[row.dich_vu_id for row in rows]).order_by('pk')}
    proposed = []
    total = Decimal('0')
    for row in rows:
        old = old_lines.get(row.pk)
        entry = catalog[row.dich_vu_id]
        price = old.don_gia if old else entry.don_gia
        if row.so_luong < 1 or price < 0 or not price.is_finite() or price != price.to_integral_value():
            raise BillingError('Đơn giá hoặc số lượng dịch vụ không hợp lệ.')
        amount = price * row.so_luong
        total += amount
        proposed.append(dict(dich_vu_thuc_hien_id=row.pk, dich_vu_id=row.dich_vu_id,
                             ma_dich_vu=old.ma_dich_vu if old else entry.ma_dich_vu,
                             ten_dich_vu=old.ten_dich_vu if old else entry.ten,
                             nhom_dich_vu=old.nhom_dich_vu if old else entry.nhom,
                             so_luong=row.so_luong, don_gia=price, thanh_tien=amount))
    # Preserve the existing invoice/payment amount limits; never round or inflate.
    if not Decimal('5000') <= total <= Decimal('9999999999'):
        raise BillingError('Tổng chi phí phải từ 5.000 đến 9.999.999.999 VND theo giới hạn thanh toán hiện tại. Kiểm tra dịch vụ và số lượng.')
    if invoice and invoice.phieu_kham_id != phieu.pk:
        raise BillingError('Hóa đơn không khớp phiếu khám; cần kiểm tra dữ liệu.')
    try:
        insurance_total, insurance_snapshot = calculate_support(appointment, proposed)
    except ValidationError as exc:
        raise BillingError(exc.messages) from exc
    payable = total - insurance_total
    if invoice and len(old_lines) == len(proposed) and all(
        data['dich_vu_thuc_hien_id'] in old_lines and all(
            getattr(old_lines[data['dich_vu_thuc_hien_id']], key) == value for key, value in data.items())
        for data in proposed
    ) and invoice.bhyt_snapshot == insurance_snapshot:
        if invoice.so_tien != payable or invoice.bhyt_ho_tro != insurance_total or invoice.tong_truoc_bhyt != total:
            raise BillingError('Tổng hóa đơn không khớp chi tiết đã chốt; cần kiểm tra dữ liệu.')
        return invoice
    before = cost_snapshot(invoice) if invoice else None
    if invoice:
        invoice.phien_ban += 1
    else:
        invoice = HoaDon(lich_kham=appointment, phieu_kham=phieu,
                         nguon=HoaDon.Nguon.DICH_VU, nguoi_tao=user)
    invoice.tong_chi_phi = total
    invoice.bhyt_ho_tro = insurance_total
    invoice.bhyt_snapshot = insurance_snapshot
    invoice.so_tien = payable
    if payable == 0:
        # Existing pending, successful or disputed attempts were rejected under lock.
        invoice.hoan_tat_bhyt = True
        invoice.trang_thai = HoaDon.TrangThai.DA_THANH_TOAN
        invoice.ngay_thanh_toan = timezone.now()
    invoice.full_clean()
    invoice.save()
    for data in proposed:
        line = ChiTietHoaDon(hoa_don=invoice, phien_ban=invoice.phien_ban, **data)
        line.full_clean()
        line.save()
    audit_invoice(invoice, before, user, remote_addr)
    return invoice


def validate_invoice_total(invoice):
    """Read under invoice lock immediately before creating a VNPAY attempt."""
    if invoice.nguon != HoaDon.Nguon.DICH_VU:
        return
    lines = list(invoice.chi_tiet_hien_hanh.select_related('dich_vu_thuc_hien'))
    active = {row.pk: row for row in DichVuThucHien.objects.filter(phieu_kham_id=invoice.phieu_kham_id, da_huy=False)}
    if (not lines or {line.dich_vu_thuc_hien_id for line in lines} != active.keys()
            or invoice.phieu_kham.lich_kham_id != invoice.lich_kham_id
            or sum((line.thanh_tien for line in lines), Decimal('0')) != invoice.tong_truoc_bhyt
            or sum((line.bhyt_ho_tro for line in lines), Decimal('0')) != invoice.bhyt_ho_tro
            or invoice.tong_truoc_bhyt - invoice.bhyt_ho_tro != invoice.so_tien
            or (invoice.so_tien == 0 and not (invoice.hoan_tat_bhyt and invoice.trang_thai == 'paid'))
            or any(line.thanh_tien != line.don_gia * line.so_luong
                   or line.bhyt_ho_tro != support_amount(line.thanh_tien, line.ty_le_bhyt)
                   or line.dich_vu_id != line.dich_vu_thuc_hien.dich_vu_id
                   or line.so_luong != line.dich_vu_thuc_hien.so_luong for line in lines)):
        raise BillingError('Hóa đơn không khớp dịch vụ/chi tiết đã chốt. Cần kiểm tra trước khi thanh toán.')


def payable_error(invoice):
    if invoice.so_tien == 0:
        return 'BHYT MÔ PHỎNG hỗ trợ toàn bộ. Hóa đơn đã hoàn tất, không tạo giao dịch VNPAY 0đ.'
    if invoice.so_tien < 5000:
        return 'Số bệnh nhân phải trả dưới 5.000 VND, chưa đủ mức tối thiểu VNPAY đang cấu hình trong project. Giữ nguyên hóa đơn, không làm tròn tăng tiền.'
    return ''
