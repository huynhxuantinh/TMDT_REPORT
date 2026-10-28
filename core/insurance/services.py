from decimal import Decimal, ROUND_HALF_UP
from ipaddress import ip_address

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from core.models import BaoHiemYTe, BenhNhan, BenhVien, DanhMucDichVu, LogHeThong, QuyTacBHYT
from core.service_catalog.services import can_manage
from .forms import QuyTacBHYTForm


def require_admin(user):
    if not can_manage(user):
        raise PermissionDenied('Chỉ Admin được cấu hình BHYT MÔ PHỎNG.')


def rule_snapshot(rule):
    return {'id': rule.pk, 'dich_vu_id': rule.dich_vu_id, 'ten': rule.ten,
            'ty_le': str(rule.ty_le), 'hieu_luc_tu': rule.hieu_luc_tu.isoformat(),
            'hieu_luc_den': rule.hieu_luc_den.isoformat() if rule.hieu_luc_den else None,
            'dang_ap_dung': rule.dang_ap_dung, 'phien_ban': rule.phien_ban}


def audit_rule(rule, before, user, remote_addr):
    try:
        address = str(ip_address(remote_addr))
    except ValueError:
        address = None
    LogHeThong.objects.create(model_name='QuyTacBHYT', object_id=rule.pk, hanh_dong='update' if before else 'create',
                             du_lieu_cu=before, du_lieu_moi=rule_snapshot(rule), nguoi_thuc_hien=user,
                             ip_address=address, ghi_chu='Cấu hình BHYT MÔ PHỎNG')


@transaction.atomic
def save_rule(data, user, pk=None, remote_addr=''):
    require_admin(user)
    # Same catalog-before-rule order as billing. Also serializes first creation.
    service_id = QuyTacBHYT.objects.values_list('dich_vu_id', flat=True).get(pk=pk) if pk else data.get('dich_vu')
    if str(service_id).isdigit():
        DanhMucDichVu.objects.select_for_update().filter(pk=service_id).first()
    rule = QuyTacBHYT.objects.select_for_update().get(pk=pk) if pk else QuyTacBHYT()
    before = rule_snapshot(rule) if pk else None
    form = QuyTacBHYTForm(data, instance=rule)
    if not form.is_valid():
        return None, form
    if before == rule_snapshot(form.instance):
        return rule, form
    try:
        with transaction.atomic():
            if pk:
                rule.phien_ban += 1
            rule.save()
            audit_rule(rule, before, user, remote_addr)
    except (ValidationError, IntegrityError):
        form.add_error(None, 'Không thể lưu: dịch vụ đã có quy tắc hoặc dữ liệu không hợp lệ. Vui lòng kiểm tra lại.')
        return None, form
    return rule, form


def support_amount(amount, rate):
    return (amount * rate / Decimal('100')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)


def calculate_support(appointment, proposed):
    """Called inside bill_exam's transaction, after catalog rows are locked.

    Eligibility is evaluated on the examination date, never the current date.
    Do not store full card numbers or clinical notes in cost/audit snapshots.
    """
    patient = BenhNhan.objects.select_for_update().get(pk=appointment.benh_nhan_id)
    card = BaoHiemYTe.objects.select_for_update().filter(pk=patient.bhyt_id).first() if patient.bhyt_id else None
    hospital = BenhVien.objects.select_for_update().get(pk=appointment.bac_si.benh_vien_id)
    date = appointment.ngay_kham
    if card is None:
        reason = 'Bệnh nhân chưa có thẻ BHYT trong hồ sơ.'
    elif not card.ma_bhyt.strip() or card.ngay_cap > card.ngay_het_han:
        reason = 'Dữ liệu thẻ BHYT không hợp lệ.'
    elif date < card.ngay_cap:
        reason = 'Thẻ BHYT chưa có hiệu lực vào ngày khám.'
    elif date > card.ngay_het_han:
        reason = 'Thẻ BHYT đã hết hạn vào ngày khám.'
    elif not hospital.co_bhyt:
        reason = 'Bệnh viện chưa được đánh dấu có BHYT trong hệ thống.'
    else:
        reason = ''
    context = {'loai': 'BHYT MÔ PHỎNG', 'ngay_kham': date.isoformat(), 'the_id': card.pk if card else None,
               'ngay_cap': card.ngay_cap.isoformat() if card else None,
               'ngay_het_han': card.ngay_het_han.isoformat() if card else None,
               'benh_vien_id': hospital.pk, 'benh_vien_co_bhyt': hospital.co_bhyt, 'ly_do': reason,
               'lam_tron': 'ROUND_HALF_UP từng dòng đến 1 VND'}
    rules = {rule.dich_vu_id: rule for rule in QuyTacBHYT.objects.select_for_update().filter(
        dich_vu_id__in=[line['dich_vu_id'] for line in proposed]).order_by('pk')}
    total = Decimal('0')
    for line in proposed:
        rule = rules.get(line['dich_vu_id'])
        line_reason = reason
        if not line_reason:
            if rule is None:
                line_reason = 'Dịch vụ chưa có quy tắc hỗ trợ.'
            elif not rule.dang_ap_dung:
                line_reason = 'Quy tắc đã ngừng áp dụng.'
            elif date < rule.hieu_luc_tu or (rule.hieu_luc_den and date > rule.hieu_luc_den):
                line_reason = 'Quy tắc không có hiệu lực vào ngày khám.'
        rate = rule.ty_le if not line_reason else Decimal('0')
        amount = support_amount(line['thanh_tien'], rate)
        if not Decimal('0') <= amount <= line['thanh_tien']:
            raise ValidationError('BHYT MÔ PHỎNG hỗ trợ vượt chi phí dịch vụ.')
        line.update(quy_tac_bhyt_id=rule.pk if rule else None, ty_le_bhyt=rate, bhyt_ho_tro=amount,
                    bhyt_snapshot={'quy_tac': rule_snapshot(rule) if rule else None,
                                   'ly_do': line_reason or 'Áp dụng quy tắc BHYT MÔ PHỎNG.'})
        total += amount
    if not context['ly_do']:
        context['ly_do'] = 'Áp dụng quy tắc BHYT MÔ PHỎNG theo từng dịch vụ.' if total else 'Thẻ đủ điều kiện nhưng không có khoản hỗ trợ phù hợp cho các dịch vụ đã chọn.'
    return total, context
