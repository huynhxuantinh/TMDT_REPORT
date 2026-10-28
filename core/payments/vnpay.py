"""VNPAY 2.1.0 encoding and verification; no database writes here."""
import hashlib
import hmac
import ipaddress
import re
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from django.conf import settings
from django.urls import reverse


class PaymentError(ValueError):
    pass


def configuration_error():
    missing = [name for name in ('VNPAY_TMN_CODE', 'VNPAY_HASH_SECRET', 'VNPAY_PUBLIC_BASE_URL')
               if not getattr(settings, name, '')]
    if missing:
        return 'Chưa cấu hình: ' + ', '.join(missing) + '. Nạp biến rồi khởi động lại Django.'
    try:
        origin = urlsplit(settings.VNPAY_PUBLIC_BASE_URL)
        if (origin.scheme != 'https' or not origin.hostname or origin.username or origin.password
                or origin.path or origin.query or origin.fragment or origin.port not in (None, 443)):
            raise ValueError
    except ValueError:
        return 'VNPAY_PUBLIC_BASE_URL phải là địa chỉ gốc HTTPS ngrok, không có đường dẫn hoặc tham số.'
    return ''


def callback_url(name):
    return settings.VNPAY_PUBLIC_BASE_URL + reverse(name)


def canonical_query(params):
    return urlencode(sorted((key, str(value)) for key, value in params.items()
                            if key.startswith('vnp_') and key not in ('vnp_SecureHash', 'vnp_SecureHashType')
                            and value != ''))


def signature(params, secret):
    return hmac.new(secret.encode('utf-8'), canonical_query(params).encode('utf-8'), hashlib.sha512).hexdigest()


def verified_params(query):
    # Reject duplicate keys instead of letting QueryDict silently choose one.
    if any(len(values) != 1 for key, values in query.lists() if key.startswith('vnp_')):
        raise PaymentError('Invalid Signature')
    params = {key: value for key, value in query.items() if key.startswith('vnp_')}
    supplied = params.get('vnp_SecureHash', '')
    if (not settings.VNPAY_HASH_SECRET or not re.fullmatch(r'[0-9a-fA-F]{128}', supplied)
            or not hmac.compare_digest(signature(params, settings.VNPAY_HASH_SECRET), supplied.lower())):
        raise PaymentError('Invalid Signature')
    return params


def result_state(response_code, transaction_status):
    if (response_code, transaction_status) == ('00', '00'):
        return 'success'
    # Only definitive failures release the invoice for retry. Suspicious, reversed,
    # processing, missing/unknown or contradictory codes remain pending.
    if transaction_status == '02':
        if response_code == '24':
            return 'cancelled'
        if response_code == '11':
            return 'expired'
        if response_code in {'09', '10', '12', '13', '51', '65', '75', '79'}:
            return 'failed'
    return 'pending'


def payment_url(payment, ip_address):
    error = configuration_error()
    if error:
        raise PaymentError(error)
    try:
        ipaddress.ip_address(ip_address)
    except ValueError:
        raise PaymentError('Không xác định được địa chỉ IP hợp lệ.') from None
    local_tz = ZoneInfo('Asia/Ho_Chi_Minh')
    params = {
        'vnp_Version': '2.1.0', 'vnp_Command': 'pay',
        'vnp_TmnCode': payment.tmn_code, 'vnp_Amount': str(int(payment.so_tien) * 100),
        'vnp_CurrCode': 'VND', 'vnp_TxnRef': payment.ma_tham_chieu,
        'vnp_OrderInfo': f'Thanh toan hoa don {payment.hoa_don_id}',
        'vnp_OrderType': 'other', 'vnp_Locale': 'vn', 'vnp_IpAddr': ip_address,
        'vnp_ReturnUrl': callback_url('vnpay_return'),
        'vnp_CreateDate': payment.ngay_tao.astimezone(local_tz).strftime('%Y%m%d%H%M%S'),
        'vnp_ExpireDate': payment.het_han_luc.astimezone(local_tz).strftime('%Y%m%d%H%M%S'),
    }
    return settings.VNPAY_PAYMENT_URL + '?' + canonical_query(params) + '&vnp_SecureHash=' + signature(params, settings.VNPAY_HASH_SECRET)
