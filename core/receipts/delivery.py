"""Durable, single-owner Mailtrap Sandbox delivery after payment commit.

SMTP cannot atomically commit with PostgreSQL. An ambiguous send is therefore
never automatically retried; an operator must reconcile the Testing Inbox.
"""
import logging
import smtplib
from functools import partial

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMessage, get_connection
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from core.models import EmailPhieuThanhToan, HoaDon, LichKham, LogHeThong
from core.payments.access import can_admin, can_collect, require_scope
from .pdf import money, render_pdf, snapshot

logger = logging.getLogger(__name__)


class DeliveryError(Exception):
    pass


ERROR_MESSAGES = {
    'missing_email': 'Bệnh nhân chưa có email đăng ký hợp lệ. Cập nhật hồ sơ rồi thử lại.',
    'sandbox_config': 'Chưa cấu hình SMTP Mailtrap Testing hợp lệ; kiểm tra cấu hình trên máy chủ.',
    'not_confirmed': 'Hóa đơn chưa đủ điều kiện gửi xác nhận thanh toán hoặc đang cần đối soát.',
    'pdf_error': 'Chưa tạo được PDF đính kèm. Kiểm tra thư viện và font rồi thử lại.',
    'smtp_open': 'Không kết nối/đăng nhập được Mailtrap Testing. Kiểm tra cấu hình rồi thử lại.',
    'smtp_rejected': 'Mailtrap từ chối nhận thư. Kiểm tra cấu hình/hạn mức rồi thử lại.',
    'smtp_unknown': 'Chưa rõ Mailtrap đã nhận thư hay chưa. Không gửi lại trước khi đối soát.',
    'verified_absent': 'Đã kiểm tra Mailtrap chưa nhận thư; có thể thử lại.',
}


def eligible(invoice):
    return (invoice.trang_thai == 'paid' and invoice.ngay_thanh_toan is not None
            and not invoice.can_doi_soat and invoice.so_tien > 0 and not invoice.hoan_tat_bhyt
            and invoice.giao_dichs.filter(trang_thai='success', so_tien=invoice.so_tien).exists())


def require_access(user, invoice):
    """Called in atomic; retain the cashier scope and active-account checks."""
    fresh = User.objects.get(pk=user.pk)
    if can_admin(fresh):
        return
    if not can_collect(fresh):
        raise PermissionDenied('Cần quyền xem chứng từ tại quầy.')
    require_scope(fresh, invoice.lich_kham)


def locked_authorized_invoice(invoice_id, user):
    # Keep the existing payment order: appointment -> cashier assignment -> invoice.
    invoice = HoaDon.objects.get(pk=invoice_id)
    appointment = LichKham.objects.select_for_update().get(pk=invoice.lich_kham_id)
    invoice.lich_kham = appointment
    require_access(user, invoice)
    return HoaDon.objects.select_for_update().get(pk=invoice_id)


def audit(row, note, user=None):
    LogHeThong.objects.create(model_name='EmailPhieuThanhToan', object_id=row.pk,
        hanh_dong='update', nguoi_thuc_hien=user,
        du_lieu_moi={'hoa_don_id': row.hoa_don_id, 'trang_thai': row.trang_thai,
                     'so_lan_thu': row.so_lan_thu, 'ma_loi': row.ma_loi}, ghi_chu=note)


def schedule_confirmation(invoice):
    """Called under payment invoice lock; SMTP is deferred until outer commit.

    A delivery-record failure must not cancel an otherwise valid payment.
    The counter can explicitly prepare a missing record later.
    """
    try:
        with transaction.atomic():
            row, created = EmailPhieuThanhToan.objects.get_or_create(hoa_don=invoice)
            if created:
                transaction.on_commit(partial(dispatch_safely, row.pk, 0), robust=True)
    except Exception:
        # Never log raw SMTP/DB exceptions (they may contain private values).
        logger.error('Receipt scheduling failed for invoice_id=%s; payment remains valid.', invoice.pk)


def sandbox_connection():
    # Explicit backend and host allowlist prevent accidental use of Email Sending
    # or arbitrary SMTP even if another app email feature uses a different backend.
    if (settings.EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend'
            or settings.EMAIL_HOST != 'sandbox.smtp.mailtrap.io'
            or settings.EMAIL_PORT not in (25, 465, 587, 2525)
            or not settings.EMAIL_HOST_USER or not settings.EMAIL_HOST_PASSWORD
            or settings.EMAIL_USE_TLS == settings.EMAIL_USE_SSL):
        raise DeliveryError('sandbox_config')
    return get_connection(backend='django.core.mail.backends.smtp.EmailBackend',
        host='sandbox.smtp.mailtrap.io', port=settings.EMAIL_PORT,
        username=settings.EMAIL_HOST_USER, password=settings.EMAIL_HOST_PASSWORD,
        use_tls=settings.EMAIL_USE_TLS, use_ssl=settings.EMAIL_USE_SSL,
        timeout=min(max(settings.EMAIL_TIMEOUT or 20, 1), 20), fail_silently=False)


@transaction.atomic
def claim(row_id, expected_attempts):
    invoice_id = EmailPhieuThanhToan.objects.values_list('hoa_don_id', flat=True).get(pk=row_id)
    invoice = HoaDon.objects.select_for_update().get(pk=invoice_id)
    row = EmailPhieuThanhToan.objects.select_for_update().get(pk=row_id)
    if row.trang_thai != 'pending' or row.so_lan_thu != expected_attempts:
        return None
    row.so_lan_thu += 1
    row.bat_dau_luc = timezone.now()
    row.ma_loi = ''
    row.gui_luc = None
    if not eligible(invoice):
        row.trang_thai, row.ma_loi = 'failed', 'not_confirmed'
        row.save()
        audit(row, 'Không gửi xác nhận khi chưa đủ điều kiện')
        return None
    patient = invoice.lich_kham.benh_nhan
    email = patient.user.email.strip() if patient.user_id else ''
    try:
        validate_email(email)
    except ValidationError:
        row.trang_thai, row.ma_loi, row.email_nhan = 'skipped', 'missing_email', ''
        row.save()
        audit(row, 'Bỏ qua do thiếu email đăng ký hợp lệ')
        return None
    row.email_nhan = email
    row.trang_thai = 'sending'
    row.save()
    audit(row, 'Bắt đầu gửi phiếu vào Mailtrap Testing', row.nguoi_thu_lai)
    return row, snapshot(invoice)


@transaction.atomic
def finish(row, state, error=''):
    current = EmailPhieuThanhToan.objects.select_for_update().get(pk=row.pk)
    if current.trang_thai != 'sending' or current.so_lan_thu != row.so_lan_thu:
        return
    current.trang_thai, current.ma_loi = state, error
    current.gui_luc = timezone.now() if state == 'sent' else None
    current.save()
    audit(current, 'Kết quả gửi phiếu vào Mailtrap Testing', current.nguoi_thu_lai)


def dispatch(row_id, expected_attempts):
    # It is invalid to call SMTP from inside any application transaction.
    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(partial(dispatch_safely, row_id, expected_attempts), robust=True)
        return
    claimed = claim(row_id, expected_attempts)
    if claimed is None:
        return
    row, data = claimed
    try:
        connection = sandbox_connection()
    except Exception:
        finish(row, 'failed', 'sandbox_config')
        return
    try:
        pdf = render_pdf(data)
        body = (f'Thanh toán dịch vụ khám chữa bệnh đã được ghi nhận thành công.\n\n'
                f'Mã hóa đơn: #{data["id"]}\nSố tiền đã thanh toán: {money(data["amount"])}\n'
                f'Phương thức thanh toán: {data["method"]}\n\n'
                'PDF đính kèm là phiếu thanh toán nội bộ, không phải hóa đơn điện tử có giá trị pháp lý.\n'
                'Đây là email kiểm thử được giữ tại Mailtrap Email Testing, không gửi tới hộp thư thật.\n'
                'MedCare GIS')
        message = EmailMessage('Xác nhận thanh toán dịch vụ khám chữa bệnh', body,
            settings.DEFAULT_FROM_EMAIL, [row.email_nhan], connection=connection,
            headers={'Message-ID': f'<receipt-{row.ma_thu}@qlbenhvien.local>'})
        message.attach(f'phieu-thanh-toan-{row.hoa_don_id}.pdf', pdf, 'application/pdf')
        message.message()  # Validate headers/attachment before entering SMTP.
    except Exception:
        finish(row, 'failed', 'pdf_error')
        return
    try:
        connection.open()
    except Exception:
        try:
            connection.close()
        except Exception:
            pass
        finish(row, 'failed', 'smtp_open')
        return
    state, error = 'unknown', 'smtp_unknown'
    try:
        if message.send(fail_silently=False) == 1:
            state, error = 'sent', ''
        else:
            state, error = 'failed', 'smtp_rejected'
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPSenderRefused,
            smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError, smtplib.SMTPHeloError):
        state, error = 'failed', 'smtp_rejected'
    except Exception:
        # A timeout/disconnect may occur after DATA was accepted by the server.
        # Retrying blindly would violate the one-confirmation requirement.
        pass
    finally:
        try:
            connection.close()
        except Exception:
            pass
    finish(row, state, error)


def dispatch_safely(row_id, expected_attempts):
    try:
        dispatch(row_id, expected_attempts)
    except Exception:
        # If SMTP succeeded but the DB write failed, leave 'sending' blocked.
        # Neither this callback nor retries can roll back payment or send twice.
        logger.error('Receipt delivery needs inspection: delivery_id=%s.', row_id)


@transaction.atomic
def retry_confirmation(invoice_id, user, expected_attempts):
    invoice = locked_authorized_invoice(invoice_id, user)
    if not eligible(invoice):
        raise DeliveryError('Chỉ gửi xác nhận cho hóa đơn đã có giao dịch thành công, không cần đối soát.')
    row, _ = EmailPhieuThanhToan.objects.select_for_update().get_or_create(hoa_don=invoice)
    if str(row.so_lan_thu) != str(expected_attempts):
        raise DeliveryError('Trạng thái gửi đã thay đổi. Tải lại trang trước khi thử lại.')
    if row.trang_thai not in ('pending', 'failed', 'skipped'):
        raise DeliveryError('Thư đã gửi, đang gửi hoặc chưa rõ kết quả. Không được gửi trùng.')
    row.trang_thai, row.ma_loi = 'pending', ''
    row.nguoi_thu_lai = user
    row.save()
    transaction.on_commit(partial(dispatch_safely, row.pk, row.so_lan_thu), robust=True)
    return row


@transaction.atomic
def reconcile(invoice_id, expected_attempts, received, note):
    """Operator-only command after independently inspecting the Testing Inbox."""
    row = EmailPhieuThanhToan.objects.select_for_update().get(hoa_don_id=invoice_id)
    if row.trang_thai not in ('sending', 'unknown') or row.so_lan_thu != expected_attempts:
        raise DeliveryError('Trạng thái/lần gửi đã thay đổi hoặc không cần đối soát.')
    if not row.bat_dau_luc or (timezone.now()-row.bat_dau_luc).total_seconds() < 120:
        raise DeliveryError('Đợi ít nhất 2 phút từ lần gửi để tránh xử lý khi SMTP còn đang hoạt động.')
    if not note.strip():
        raise DeliveryError('Cần ghi chú đã kiểm tra Inbox; không ghi credentials.')
    row.trang_thai = 'sent' if received else 'failed'
    row.gui_luc = timezone.now() if received else None
    row.ma_loi = '' if received else 'verified_absent'
    row.save()
    # Note is required for operator intention but not stored verbatim: it could contain secrets.
    audit(row, 'Đối soát thủ công Mailtrap: đã nhận' if received else 'Đối soát thủ công Mailtrap: xác minh chưa nhận')
    return row
