"""All payment writes pass through locked, atomic operations."""
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from core.models import GiaoDichThanhToan, HoaDon, LichKham, LogHeThong, PhieuKham
from . import state, vnpay
from .access import can_collect, manager_profile, require_scope


def _apply_vnpay_result(attempt, params):
    """Copy the provider result onto the attempt without deciding its meaning."""
    attempt.ma_phan_hoi = params['vnp_ResponseCode']
    attempt.trang_thai_vnpay = params['vnp_TransactionStatus']
    attempt.ma_giao_dich_vnpay = params['vnp_TransactionNo']
    attempt.vnp_pay_date = params.get('vnp_PayDate', '')


def _log_attempt(attempt, before, note, actor=None, extra=None):
    LogHeThong.objects.create(
        model_name='GiaoDichThanhToan', object_id=attempt.pk, hanh_dong='update',
        du_lieu_cu=before,
        du_lieu_moi={'trang_thai': attempt.trang_thai,
                     'hoan_tat_luc': attempt.hoan_tat_luc.isoformat() if attempt.hoan_tat_luc else None,
                     **(extra or {})},
        nguoi_thuc_hien=actor, ghi_chu=note,
    )


def _expire_pending_locked(invoice, *, now=None, actor=None, note=''):
    """Close an overdue VNPAY attempt once its late-IPN window is over.

    Before that window closes the attempt stays `pending` on purpose: a success
    notification that arrives late must still be able to settle the invoice.
    The attempt becomes `timeout`, never `expired` — VNPAY never said it expired.
    """
    now = now or timezone.now()
    attempt = GiaoDichThanhToan.objects.select_for_update().filter(
        hoa_don=invoice, phuong_thuc='VNPAY', trang_thai='pending', het_han_luc__lte=now,
    ).first()
    if attempt is None:
        return None
    if now < state.grace_deadline(attempt):
        return None
    before = {'trang_thai': attempt.trang_thai, 'het_han_luc': attempt.het_han_luc.isoformat()}
    attempt.trang_thai = GiaoDichThanhToan.TrangThai.QUA_HAN_CHO
    attempt.hoan_tat_luc = now
    attempt.save(update_fields=['trang_thai', 'hoan_tat_luc', 'ngay_cap_nhat'])
    _log_attempt(
        attempt, before,
        note or 'Qua thoi gian cho IPN nhung VNPAY chua tra ket qua. Chuyen sang timeout de doi soat.',
        actor,
    )
    return attempt


@transaction.atomic
def expire_pending_payment(payment_id, *, now=None, actor=None):
    """Durably expire an overdue VNPAY attempt; safe for commands and retries."""
    invoice_id = GiaoDichThanhToan.objects.values_list('hoa_don_id', flat=True).get(pk=payment_id)
    invoice = HoaDon.objects.select_for_update().get(pk=invoice_id)
    return _expire_pending_locked(invoice, now=now, actor=actor)


def expire_due_payments(*, now=None):
    """Expire all due attempts one invoice at a time, avoiding long table locks."""
    now = now or timezone.now()
    payment_ids = list(GiaoDichThanhToan.objects.filter(
        phuong_thuc='VNPAY', trang_thai='pending', het_han_luc__lte=now,
    ).values_list('pk', flat=True))
    return sum(expire_pending_payment(payment_id, now=now) is not None for payment_id in payment_ids)


def resolve_reconciliation(invoice_id, user, *, reason, reference, ip_address=''):
    """Admin-only closure, allowed only with provider evidence.

    Every attempt of the invoice is checked with VNPAY querydr first. Closing is
    refused when any attempt reports that money moved, and also when the check
    cannot be verified: "cannot prove" must never be read as "no payment".
    """
    from .access import can_admin

    if not can_admin(user):
        raise PermissionDenied('Chi Admin giam sat moi duoc ket thuc doi soat VNPAY.')
    reason = (reason or '').strip()
    reference = (reference or '').strip()
    if len(reason) < 10:
        raise vnpay.PaymentError('Phải ghi lý do đối soát cụ thể (ít nhất 10 ký tự) để lưu làm bằng chứng.')
    if len(reason) > 500:
        raise vnpay.PaymentError('Lý do đối soát quá dài (tối đa 500 ký tự).')
    invoice = HoaDon.objects.get(pk=invoice_id)
    _reconciliation_precheck(invoice, reference)
    # Network calls happen before any row is locked or modified.
    checks = _query_all_attempts(invoice, reference, ip_address)
    return _apply_reconciliation(invoice_id, user, reason, reference, checks)


def _reconciliation_precheck(invoice, reference):
    if not invoice.can_doi_soat:
        raise vnpay.PaymentError('Hoa don nay khong o trang thai can doi soat.')
    if invoice.giao_dichs.filter(trang_thai=GiaoDichThanhToan.TrangThai.THU_TRUNG).exists():
        # Money was taken twice; releasing the invoice would let the patient pay a third time.
        raise vnpay.PaymentError('Hoa don co khoan thu trung can hoan tien; khong duoc mo khoa de thu tiep.')
    attempts = list(GiaoDichThanhToan.objects.filter(hoa_don=invoice, phuong_thuc='VNPAY'))
    if not attempts:
        raise vnpay.PaymentError('Hoa don chua co giao dich VNPAY nao de doi soat.')
    if reference.lower() not in {attempt.ma_tham_chieu.lower() for attempt in attempts}:
        raise vnpay.PaymentError(
            'Mã tham chiếu đã kiểm tra không thuộc hóa đơn này. Hãy nhập đúng mã giao dịch VNPAY của hóa đơn.')
    return attempts


def _query_outcome(result, attempt):
    """Turn a verified querydr answer into paid / unpaid / failed / unknown."""
    amount = result.get('vnp_Amount', '')
    if amount and (not amount.isdigit() or int(amount) != int(attempt.so_tien) * 100):
        return 'unknown'
    if result.get('vnp_ResponseCode', '') != '00':
        # 91 = not found; anything else is not an answer we can rely on.
        return 'unknown'
    return {'00': 'paid', '01': 'unpaid', '02': 'failed'}.get(
        result.get('vnp_TransactionStatus', ''), 'unknown')


def _query_all_attempts(invoice, reference, ip_address):
    checks = []
    for attempt in GiaoDichThanhToan.objects.filter(hoa_don=invoice, phuong_thuc='VNPAY').order_by('pk'):
        result = vnpay.query_transaction(attempt, ip_address or '127.0.0.1')
        outcome = _query_outcome(result, attempt)
        if outcome == 'unknown':
            raise vnpay.PaymentError(
                f'VNPAY chưa xác nhận được kết quả giao dịch {attempt.ma_tham_chieu} '
                f'(mã {result.get("vnp_ResponseCode", "?")}/{result.get("vnp_TransactionStatus", "?")}). '
                'Chưa thể kết thúc đối soát.')
        checks.append({
            'pk': attempt.pk, 'outcome': outcome, 'result': result,
            'before': (attempt.trang_thai, attempt.ma_giao_dich_vnpay),
        })
    return checks


def _querydr_params(result):
    return {
        'vnp_ResponseCode': result.get('vnp_ResponseCode', '00'),
        'vnp_TransactionStatus': result.get('vnp_TransactionStatus', '00'),
        'vnp_TransactionNo': result.get('vnp_TransactionNo', ''),
        'vnp_PayDate': result.get('vnp_PayDate', ''),
    }


def _query_evidence(check):
    result = check['result']
    return {
        'querydr_response_code': result.get('vnp_ResponseCode', ''),
        'querydr_transaction_status': result.get('vnp_TransactionStatus', ''),
        'querydr_transaction_no': result.get('vnp_TransactionNo', ''),
        'querydr_pay_date': result.get('vnp_PayDate', ''),
        'querydr_outcome': check['outcome'],
    }


@transaction.atomic
def _apply_reconciliation(invoice_id, user, reason, reference, checks):
    appointment_id = HoaDon.objects.values_list('lich_kham_id', flat=True).get(pk=invoice_id)
    LichKham.objects.select_for_update().get(pk=appointment_id)
    invoice = HoaDon.objects.select_for_update().get(pk=invoice_id)
    _reconciliation_precheck(invoice, reference)

    locked = {}
    for check in checks:
        attempt = GiaoDichThanhToan.objects.select_for_update().get(pk=check['pk'])
        if (attempt.trang_thai, attempt.ma_giao_dich_vnpay) != check['before']:
            # The attempt moved between the VNPAY query and this lock; evidence is stale.
            raise vnpay.PaymentError('Giao dịch VNPAY vừa thay đổi trong lúc đối soát. Tải lại trang và kiểm tra lại.')
        locked[check['pk']] = attempt

    settled = False
    for check in checks:
        if check['outcome'] != 'paid':
            continue
        _settle_after_local_timeout(invoice, locked[check['pk']], _querydr_params(check['result']),
                                   'success', actor=user,
                                   note=('VNPAY querydr xac nhan da thu tien; he thong ghi nhan thanh toan '
                                         'thay vi mo khoa hoa don.'))
        settled = True

    if settled:
        duplicate = invoice.giao_dichs.filter(
            trang_thai=GiaoDichThanhToan.TrangThai.THU_TRUNG).exists()
        if invoice.trang_thai == HoaDon.TrangThai.DA_THANH_TOAN and not duplicate:
            invoice.can_doi_soat = False
            invoice.save(update_fields=['can_doi_soat', 'ngay_cap_nhat'])
        LogHeThong.objects.create(
            model_name='HoaDon', object_id=invoice.pk, hanh_dong='update',
            du_lieu_cu={'can_doi_soat': True},
            du_lieu_moi={'can_doi_soat': invoice.can_doi_soat, 'querydr': 'paid', 'reference': reference},
            nguoi_thuc_hien=user,
            ghi_chu=(f'Doi soat VNPAY (querydr) phat hien da thu tien'
                     f'{" va co khoan thu trung" if duplicate else ""}. Ly do admin: {reason}'),
        )
        return 'duplicate' if duplicate else 'settled'

    now = timezone.now()
    for check in checks:
        attempt = locked[check['pk']]
        before = {'trang_thai': attempt.trang_thai, 'het_han_luc': attempt.het_han_luc.isoformat()
                  if attempt.het_han_luc else None}
        if attempt.trang_thai == 'pending':
            attempt.trang_thai = GiaoDichThanhToan.TrangThai.QUA_HAN_CHO
            attempt.hoan_tat_luc = now
            attempt.save(update_fields=['trang_thai', 'hoan_tat_luc', 'ngay_cap_nhat'])
        _log_attempt(
            attempt, before,
            f'Admin ket thuc doi soat. Ly do: {reason}. VNPAY querydr bao khong co tien chuyen.',
            user, extra=_query_evidence(check),
        )
    invoice.can_doi_soat = False
    invoice.save(update_fields=['can_doi_soat', 'ngay_cap_nhat'])
    LogHeThong.objects.create(
        model_name='HoaDon', object_id=invoice.pk, hanh_dong='update',
        du_lieu_cu={'can_doi_soat': True}, du_lieu_moi={'can_doi_soat': False},
        nguoi_thuc_hien=user,
        ghi_chu=(f'Admin ket thuc doi soat VNPAY sau khi querydr xac nhan chua thanh toan. '
                 f'Ly do: {reason}. Ma tham chieu da kiem tra: {reference}.'),
    )
    # A paid invoice stays paid: closing only removes the doubt flag, it never unlocks.
    return 'paid' if invoice.trang_thai == HoaDon.TrangThai.DA_THANH_TOAN else ''


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
    _expire_pending_locked(invoice, actor=user)
    from core.billing.services import payable_error
    amount_error = payable_error(invoice)
    if amount_error:
        raise vnpay.PaymentError(amount_error)
    if invoice.can_doi_soat:
        raise vnpay.PaymentError('Hóa đơn có kết quả VNPAY mâu thuẫn, cần đối soát trước khi thu tiếp.')
    if invoice.trang_thai == HoaDon.TrangThai.DA_THANH_TOAN:
        raise vnpay.PaymentError('Hóa đơn đã thanh toán, không thể thu thêm.')
    if invoice.giao_dichs.exclude(trang_thai__in=state.RELEASABLE).exists():
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
    _expire_pending_locked(invoice, actor=user)
    if invoice.so_tien <= 0 or invoice.hoan_tat_bhyt:
        raise vnpay.PaymentError('Hóa đơn 0đ đã hoàn tất bằng BHYT MÔ PHỎNG; không thu tiền.')
    if invoice.trang_thai == 'paid':
        raise vnpay.PaymentError('Hóa đơn đã thanh toán, không được thu thêm.')
    if invoice.can_doi_soat:
        raise vnpay.PaymentError('Hóa đơn cần đối soát; chưa được thu tiền mặt.')
    if invoice.giao_dichs.exclude(trang_thai__in=state.RELEASABLE).exists():
        raise vnpay.PaymentError('Giao dịch đang xử lý, còn trong thời gian chờ IPN hoặc chưa rõ kết quả; chưa được thu tiền mặt.')
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


def _settle_after_local_timeout(invoice, attempt, params, outcome, *, actor=None, note=''):
    """Apply a verified result that arrived after this attempt stopped waiting.

    VNPAY alone decides whether money moved. Our local timer running out is not
    evidence of anything, so a verified success still settles the invoice; if
    the invoice is already settled the money was taken twice and must be refunded.
    """
    before = {'trang_thai': attempt.trang_thai, 'ma_giao_dich_vnpay': attempt.ma_giao_dich_vnpay}
    _apply_vnpay_result(attempt, params)
    now = timezone.now()
    if outcome == 'pending':
        return '99', 'Payment pending; retry notification'
    attempt.hoan_tat_luc = now
    if outcome != 'success':
        attempt.trang_thai = outcome
        attempt.save(update_fields=['ma_phan_hoi', 'trang_thai_vnpay', 'ma_giao_dich_vnpay',
                                    'vnp_pay_date', 'trang_thai', 'hoan_tat_luc', 'ngay_cap_nhat'])
        _log_attempt(attempt, before, f'IPN den sau thoi gian cho: VNPAY bao {outcome}; khong co tien chuyen.',
                     actor)
        return '00', 'Confirm Success'
    if invoice.so_tien != attempt.so_tien:
        return '04', 'Invalid amount'
    settled = (invoice.trang_thai == HoaDon.TrangThai.DA_THANH_TOAN
               or GiaoDichThanhToan.objects.filter(
                   hoa_don=invoice, trang_thai=GiaoDichThanhToan.TrangThai.THANH_CONG).exists())
    if settled:
        attempt.trang_thai = GiaoDichThanhToan.TrangThai.THU_TRUNG
        attempt.save(update_fields=['ma_phan_hoi', 'trang_thai_vnpay', 'ma_giao_dich_vnpay',
                                    'vnp_pay_date', 'trang_thai', 'hoan_tat_luc', 'ngay_cap_nhat'])
        invoice.can_doi_soat = True
        invoice.save(update_fields=['can_doi_soat', 'ngay_cap_nhat'])
        _log_attempt(
            attempt, before,
            'Thu trung: VNPAY xac nhan thanh cong sau khi hoa don da thanh toan. Can hoan tien cho benh nhan.',
            actor,
        )
        LogHeThong.objects.create(
            model_name='HoaDon', object_id=invoice.pk, hanh_dong='update',
            du_lieu_cu={'can_doi_soat': False}, du_lieu_moi={'can_doi_soat': True},
            nguoi_thuc_hien=actor,
            ghi_chu='Phat hien thu trung VNPAY sau khi hoa don da thanh toan; khoa thu tien va can hoan tien.',
        )
        return '00', 'Duplicate payment recorded; refund required'
    attempt.trang_thai = GiaoDichThanhToan.TrangThai.THANH_CONG
    attempt.save(update_fields=['ma_phan_hoi', 'trang_thai_vnpay', 'ma_giao_dich_vnpay',
                                'vnp_pay_date', 'trang_thai', 'hoan_tat_luc', 'ngay_cap_nhat'])
    invoice.trang_thai = HoaDon.TrangThai.DA_THANH_TOAN
    invoice.ngay_thanh_toan = now
    # Provider evidence just replaced the doubt that flagged this invoice.
    invoice.can_doi_soat = False
    invoice.save(update_fields=['trang_thai', 'ngay_thanh_toan', 'can_doi_soat', 'ngay_cap_nhat'])
    _log_attempt(attempt, before,
                 note or 'IPN thanh cong den sau thoi gian cho; ghi nhan hoa don da thanh toan.', actor)
    from core.receipts.delivery import schedule_confirmation
    schedule_confirmation(invoice)
    return '00', 'Confirm Success'


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
    outcome = vnpay.result_state(params['vnp_ResponseCode'], params['vnp_TransactionStatus'])
    if attempt.trang_thai != 'pending':
        if attempt.trang_thai == outcome and attempt.ma_giao_dich_vnpay == params['vnp_TransactionNo']:
            return '02', 'Order already confirmed'
        if (attempt.trang_thai == GiaoDichThanhToan.TrangThai.THU_TRUNG and outcome == 'success'
                and attempt.ma_giao_dich_vnpay == params['vnp_TransactionNo']):
            # Refund case already recorded: acknowledge so VNPAY stops retrying.
            return '02', 'Order already confirmed'
        if attempt.trang_thai == GiaoDichThanhToan.TrangThai.QUA_HAN_CHO:
            return _settle_after_local_timeout(invoice, attempt, params, outcome)
        # A later contradictory callback must not leave a failed invoice retryable.
        # Preserve the original terminal record and freeze new charges for review.
        invoice.can_doi_soat = True
        invoice.save(update_fields=['can_doi_soat', 'ngay_cap_nhat'])
        return '99', 'Conflicting result; reconciliation required'
    if invoice.so_tien != attempt.so_tien:
        return '04', 'Invalid amount'
    if invoice.trang_thai == 'paid':
        return '02', 'Order already confirmed'
    _apply_vnpay_result(attempt, params)
    attempt.trang_thai = outcome
    if outcome != 'pending':
        attempt.hoan_tat_luc = timezone.now()
    attempt.save(update_fields=['ma_phan_hoi', 'trang_thai_vnpay', 'ma_giao_dich_vnpay', 'vnp_pay_date', 'trang_thai', 'hoan_tat_luc', 'ngay_cap_nhat'])
    if outcome == 'pending':
        # Ask for IPN retry. The invoice stays frozen during the late-IPN window;
        # only then does the attempt become 'timeout' and a new charge become legal.
        return '99', 'Payment pending; retry notification'
    if outcome == 'success':
        invoice.trang_thai = HoaDon.TrangThai.DA_THANH_TOAN
        invoice.ngay_thanh_toan = attempt.hoan_tat_luc
        invoice.save(update_fields=['trang_thai', 'ngay_thanh_toan', 'ngay_cap_nhat'])
        from core.receipts.delivery import schedule_confirmation
        schedule_confirmation(invoice)
    return '00', 'Confirm Success'
