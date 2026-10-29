"""All payment writes pass through locked, atomic operations."""
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import GiaoDichThanhToan, HoaDon, LichKham, LogHeThong, PhieuKham
from . import vnpay
from .access import can_collect, manager_profile, require_scope


def require_completed(appointment):
    if appointment.trang_thai != 'xong' or not PhieuKham.objects.filter(
        lich_kham=appointment, benh_nhan_id=appointment.benh_nhan_id
    ).exists():
        raise vnpay.PaymentError('Chỉ thu tiền sau khi bác sĩ hoàn tất khám và lập phiếu khám hợp lệ.')


@transaction.atomic
def create_invoice(appointment_id, amount, user):
    """Legacy invoice constructor retained for regression fixtures, not exposed by current views."""
    if not can_collect(user) or manager_profile(user):
        raise vnpay.PaymentError('Tài khoản chưa được cấp quyền thu tiền tại quầy.')
    appointment = LichKham.objects.select_for_update().get(pk=appointment_id)
    require_scope(user, appointment)
    require_completed(appointment)
    try:
        amount = Decimal(str(amount))
        if not amount.is_finite() or amount != amount.to_integral_value() or not 5000 <= amount <= 9999999999:
            raise ValueError
    except (ValueError, InvalidOperation):
        raise vnpay.PaymentError('Số tiền phải là số nguyên VND từ 5.000 đến 9.999.999.999.') from None
    if HoaDon.objects.filter(lich_kham=appointment).exists():
        raise vnpay.PaymentError('Lịch khám đã có hóa đơn; không tạo hóa đơn trùng.')
    return HoaDon.objects.create(lich_kham=appointment, so_tien=amount, nguoi_tao=user)


@transaction.atomic
def start_payment(invoice_id, user, ip_address):
    if not can_collect(user):
        raise vnpay.PaymentError('Tài khoản chưa được cấp quyền thu tiền tại quầy.')
    error = vnpay.configuration_error()
    if error:
        raise vnpay.PaymentError(error)
    appointment_id = HoaDon.objects.values_list('lich_kham_id', flat=True).get(pk=invoice_id)
    appointment = LichKham.objects.select_for_update().get(pk=appointment_id)
    require_scope(user, appointment)
    require_completed(appointment)
    invoice = HoaDon.objects.select_for_update().get(pk=invoice_id)
    from core.billing.services import payable_error
    amount_error = payable_error(invoice)
    if amount_error:
        raise vnpay.PaymentError(amount_error)
    if invoice.can_doi_soat:
        raise vnpay.PaymentError('Hóa đơn có kết quả VNPAY mâu thuẫn, cần đối soát trước khi thu tiếp.')
    if invoice.trang_thai == HoaDon.TrangThai.DA_THANH_TOAN:
        raise vnpay.PaymentError('Hóa đơn đã thanh toán, không thể thu thêm.')
    if invoice.giao_dichs.filter(trang_thai__in=['pending', 'success']).exists():
        raise vnpay.PaymentError('Giao dịch đang chờ xác nhận hoặc đã thành công. Không tạo lần thu mới; hãy kiểm tra IPN.')
    from core.billing.services import BillingError, validate_invoice_total
    try:
        validate_invoice_total(invoice)
    except BillingError as exc:
        raise vnpay.PaymentError(' '.join(exc.messages)) from exc
    payment = GiaoDichThanhToan.objects.create(
        hoa_don=invoice, so_tien=invoice.so_tien, tmn_code=settings.VNPAY_TMN_CODE,
        nguoi_tao=user, benh_vien_id=appointment.bac_si.benh_vien_id,
        het_han_luc=timezone.now() + timedelta(minutes=settings.VNPAY_TIMEOUT_MINUTES),
    )
    # Any URL/configuration error rolls back the newly created attempt.
    return payment, vnpay.payment_url(payment, ip_address)


@transaction.atomic
def collect_cash(invoice_id, user, *, expected_version):
    """Receive the entire database amount once, sharing invoice locks with IPN."""
    if not can_collect(user):
        raise vnpay.PaymentError('Tài khoản chưa được cấp quyền thu tiền tại quầy.')
    appointment_id = HoaDon.objects.values_list('lich_kham_id', flat=True).get(pk=invoice_id)
    appointment = LichKham.objects.select_for_update().get(pk=appointment_id)
    require_scope(user, appointment)
    require_completed(appointment)
    invoice = HoaDon.objects.select_for_update().get(pk=invoice_id)
    if invoice.so_tien <= 0 or invoice.hoan_tat_bhyt:
        raise vnpay.PaymentError('Hóa đơn 0đ đã hoàn tất bằng BHYT MÔ PHỎNG; không thu tiền.')
    if invoice.trang_thai == 'paid':
        raise vnpay.PaymentError('Hóa đơn đã thanh toán, không được thu thêm.')
    if invoice.can_doi_soat:
        raise vnpay.PaymentError('Hóa đơn cần đối soát; chưa được thu tiền mặt.')
    if invoice.giao_dichs.exclude(trang_thai__in=['failed', 'cancelled', 'expired']).exists():
        raise vnpay.PaymentError('Giao dịch đang xử lý hoặc chưa rõ kết quả; chưa được thu tiền mặt.')
    if str(invoice.phien_ban) != str(expected_version):
        raise vnpay.PaymentError('Hóa đơn đã thay đổi. Tải lại và kiểm tra số tiền trước khi xác nhận.')
    from core.billing.services import BillingError, validate_invoice_total
    try:
        validate_invoice_total(invoice)
    except BillingError as exc:
        raise vnpay.PaymentError(' '.join(exc.messages)) from exc
    now = timezone.now()
    payment = GiaoDichThanhToan.objects.create(
        hoa_don=invoice, so_tien=invoice.so_tien, phuong_thuc='CASH', trang_thai='success',
        benh_vien_id=appointment.bac_si.benh_vien_id, nguoi_tao=user, hoan_tat_luc=now,
    )
    invoice.trang_thai = 'paid'
    invoice.ngay_thanh_toan = now
    invoice.save(update_fields=['trang_thai', 'ngay_thanh_toan', 'ngay_cap_nhat'])
    LogHeThong.objects.create(
        model_name='GiaoDichThanhToan', object_id=payment.pk, hanh_dong='create', nguoi_thuc_hien=user,
        ghi_chu='Xác nhận đã nhận đủ tiền mặt tại quầy',
        du_lieu_moi={'hoa_don_id': invoice.pk, 'phien_ban': invoice.phien_ban,
                     'phuong_thuc': 'CASH', 'so_tien': str(invoice.so_tien),
                     'benh_vien_id': payment.benh_vien_id, 'nguoi_xac_nhan_id': user.pk,
                     'ma_tham_chieu': payment.ma_tham_chieu, 'hoan_tat_luc': now.isoformat()},
    )
    from core.receipts.delivery import schedule_confirmation
    schedule_confirmation(invoice)
    return payment


def validate_callback(params, payment):
    if payment.phuong_thuc != 'VNPAY':
        return '01', 'Order not found'
    if params.get('vnp_TmnCode') != payment.tmn_code or payment.tmn_code != settings.VNPAY_TMN_CODE:
        return '97', 'Invalid merchant'
    amount = params.get('vnp_Amount', '')
    if not re.fullmatch(r'[0-9]{1,12}', amount) or int(amount) != int(payment.so_tien) * 100:
        return '04', 'Invalid amount'
    if not all(re.fullmatch(r'[0-9]{2}', params.get(key, '')) for key in ('vnp_ResponseCode', 'vnp_TransactionStatus')):
        return '99', 'Invalid result'
    if not re.fullmatch(r'[0-9]{1,15}', params.get('vnp_TransactionNo', '')):
        return '99', 'Invalid transaction number'
    if params.get('vnp_PayDate') and not re.fullmatch(r'[0-9]{14}', params['vnp_PayDate']):
        return '99', 'Invalid payment date'
    if vnpay.result_state(params['vnp_ResponseCode'], params['vnp_TransactionStatus']) == 'success' and int(params['vnp_TransactionNo']) == 0:
        return '99', 'Invalid transaction number'
    return None


@transaction.atomic
def process_ipn(params):
    """Caller MUST verify HMAC before entering this service."""
    reference = params.get('vnp_TxnRef', '')
    attempt = GiaoDichThanhToan.objects.filter(ma_tham_chieu=reference, phuong_thuc='VNPAY').first()
    if attempt is None:
        return '01', 'Order not found'
    # Always lock invoice before attempt: serializes IPN with payment creation.
    invoice = HoaDon.objects.select_for_update().get(pk=attempt.hoa_don_id)
    attempt = GiaoDichThanhToan.objects.select_for_update().get(pk=attempt.pk)
    invalid = validate_callback(params, attempt)
    if invalid:
        return invalid
    state = vnpay.result_state(params['vnp_ResponseCode'], params['vnp_TransactionStatus'])
    if attempt.trang_thai != 'pending':
        if attempt.trang_thai == state and attempt.ma_giao_dich_vnpay == params['vnp_TransactionNo']:
            return '02', 'Order already confirmed'
        # A later contradictory callback must not leave a failed invoice retryable.
        # Preserve the original terminal record and freeze new charges for review.
        invoice.can_doi_soat = True
        invoice.save(update_fields=['can_doi_soat', 'ngay_cap_nhat'])
        return '99', 'Conflicting result; reconciliation required'
    if invoice.so_tien != attempt.so_tien:
        return '04', 'Invalid amount'
    if invoice.trang_thai == 'paid':
        return '02', 'Order already confirmed'
    attempt.ma_phan_hoi = params['vnp_ResponseCode']
    attempt.trang_thai_vnpay = params['vnp_TransactionStatus']
    attempt.ma_giao_dich_vnpay = params['vnp_TransactionNo']
    attempt.vnp_pay_date = params.get('vnp_PayDate', '')
    attempt.trang_thai = state
    if state != 'pending':
        attempt.hoan_tat_luc = timezone.now()
    attempt.save(update_fields=['ma_phan_hoi', 'trang_thai_vnpay', 'ma_giao_dich_vnpay', 'vnp_pay_date', 'trang_thai', 'hoan_tat_luc', 'ngay_cap_nhat'])
    if state == 'pending':
        # Ask for IPN retry; do not permit a new charge even after local expiry.
        return '99', 'Payment pending; retry notification'
    if state == 'success':
        invoice.trang_thai = HoaDon.TrangThai.DA_THANH_TOAN
        invoice.ngay_thanh_toan = attempt.hoan_tat_luc
        invoice.save(update_fields=['trang_thai', 'ngay_thanh_toan', 'ngay_cap_nhat'])
        from core.receipts.delivery import schedule_confirmation
        schedule_confirmation(invoice)
    return '00', 'Confirm Success'
