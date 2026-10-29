"""VNPAY 2.1.0 encoding and verification; no database writes here."""
import hashlib
import hmac
import ipaddress
import json
import re
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from django.conf import settings
from django.urls import reverse
from django.utils import timezone


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


# --- querydr -----------------------------------------------------------------
# A local timer and an admin's memory are not evidence. Before an invoice is
# released for a new charge we ask VNPAY what really happened to each attempt.
# A failed or unverifiable query must never be treated as "no money moved".

QUERY_URL_DEFAULT = 'https://sandbox.vnpayment.vn/merchant_webapi/api/transaction'

#: Field order VNPAY uses for the querydr HMAC (documented pipe-separated form).
QUERY_REQUEST_FIELDS = (
    'vnp_RequestId', 'vnp_Version', 'vnp_Command', 'vnp_TmnCode', 'vnp_TxnRef',
    'vnp_TransactionDate', 'vnp_CreateDate', 'vnp_IpAddr', 'vnp_OrderInfo',
)
QUERY_RESPONSE_FIELDS = (
    'vnp_ResponseId', 'vnp_ResponseCode', 'vnp_Message', 'vnp_TmnCode', 'vnp_TxnRef',
    'vnp_Amount', 'vnp_BankCode', 'vnp_PayDate', 'vnp_TransactionNo',
    'vnp_TransactionType', 'vnp_TransactionStatus', 'vnp_OrderInfo',
)


def pipe_signature(params, fields, secret):
    raw = '|'.join(str(params.get(field, '')) for field in fields)
    return hmac.new(secret.encode('utf-8'), raw.encode('utf-8'), hashlib.sha512).hexdigest()


def query_url():
    return getattr(settings, 'VNPAY_QUERY_URL', QUERY_URL_DEFAULT)


def query_payload(attempt, ip_address, *, now=None, request_id=None):
    """Build a signed querydr request for one attempt. No network, no writes."""
    error = configuration_error()
    if error:
        raise PaymentError(error)
    try:
        ipaddress.ip_address(ip_address)
    except ValueError:
        raise PaymentError('Không xác định được địa chỉ IP hợp lệ để truy vấn VNPAY.') from None
    local_tz = ZoneInfo('Asia/Ho_Chi_Minh')
    signed_at = attempt.ngay_tao.astimezone(local_tz)
    queried_at = (now or timezone.now()).astimezone(local_tz)
    payload = {
        'vnp_RequestId': (request_id or uuid.uuid4().hex)[:32],
        'vnp_Version': '2.1.0',
        'vnp_Command': 'querydr',
        'vnp_TmnCode': settings.VNPAY_TMN_CODE,
        'vnp_TxnRef': attempt.ma_tham_chieu,
        'vnp_OrderInfo': f'Truy van giao dich {attempt.ma_tham_chieu}',
        'vnp_TransactionDate': signed_at.strftime('%Y%m%d%H%M%S'),
        'vnp_CreateDate': queried_at.strftime('%Y%m%d%H%M%S'),
        'vnp_IpAddr': ip_address,
    }
    payload['vnp_SecureHash'] = pipe_signature(payload, QUERY_REQUEST_FIELDS, settings.VNPAY_HASH_SECRET)
    return payload


def http_post_json(url, payload, timeout=10):
    """Single HTTP POST. Isolated so tests can replace it and never touch the network."""
    request = Request(
        url, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
        method='POST',
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def verified_query_response(raw):
    """Validate a querydr response and return only its verified fields.

    Fails closed: an unsigned, wrongly signed or non-2.1.0 answer proves nothing,
    so the caller must not treat it as evidence that no money moved.
    """
    if not isinstance(raw, dict):
        raise PaymentError('VNPAY trả về dữ liệu truy vấn không hợp lệ.')
    supplied = str(raw.get('vnp_SecureHash', ''))
    if not re.fullmatch(r'[0-9a-fA-F]{128}', supplied):
        raise PaymentError('VNPAY không ký dữ liệu truy vấn; không thể dùng làm bằng chứng.')
    expected = pipe_signature(raw, QUERY_RESPONSE_FIELDS, settings.VNPAY_HASH_SECRET)
    if not hmac.compare_digest(expected, supplied.lower()):
        raise PaymentError('Chữ ký dữ liệu truy vấn VNPAY không hợp lệ.')
    if str(raw.get('vnp_TmnCode', '')) != settings.VNPAY_TMN_CODE:
        raise PaymentError('Dữ liệu truy vấn không thuộc tài khoản VNPAY của hệ thống.')
    return {field: str(raw.get(field, '')) for field in QUERY_RESPONSE_FIELDS}


def query_transaction(attempt, ip_address, *, now=None):
    """Ask VNPAY for the authoritative result of one attempt."""
    payload = query_payload(attempt, ip_address, now=now)
    try:
        raw = http_post_json(query_url(), payload)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        raise PaymentError('Không kết nối được VNPAY để truy vấn giao dịch. Chưa thể kết thúc đối soát.') from None
    return verified_query_response(raw)

