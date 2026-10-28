from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Q, Exists, OuterRef
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_GET, require_POST

from core.models import GiaoDichThanhToan, HoaDon, LichKham, LogHeThong, PhieuKham
from core.billing.services import BillingError, bill_exam, validate_invoice_total, payable_error
from . import services, vnpay
from .access import can_admin, cashier_profile, scoped_appointments


def collector_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if kwargs.get('monitor'):
            allowed = can_admin(request.user)
        else:
            allowed = services.can_collect(request.user)
        if not allowed:
            raise PermissionDenied('Cần quyền thu tiền tại quầy hoặc quyền Admin giám sát.')
        return view(request, *args, **kwargs)
    return wrapped


@never_cache
@collector_required
@require_GET
def payment_list(request, monitor=False):
    appointments = scoped_appointments(request.user).filter(trang_thai='xong', phieu_kham__isnull=False).select_related(
        'benh_nhan', 'bac_si__benh_vien', 'hoa_don'
    ).prefetch_related('hoa_don__giao_dichs').annotate(
        payment_pending=Exists(GiaoDichThanhToan.objects.filter(hoa_don__lich_kham_id=OuterRef('pk'), trang_thai='pending')),
        payment_failed=Exists(GiaoDichThanhToan.objects.filter(hoa_don__lich_kham_id=OuterRef('pk'), trang_thai__in=['failed', 'cancelled', 'expired'])),
    ).order_by('-ngay_kham', '-pk')
    query = request.GET.get('q', '').strip()[:100]
    if query:
        search = Q(benh_nhan__ho_ten__icontains=query)
        if query.isascii() and query.isdigit() and len(query) <= 10:
            search |= Q(hoa_don__pk=int(query))
        appointments = appointments.filter(search)
    status = request.GET.get('status', '')
    filters = {
        'paid': Q(hoa_don__trang_thai='paid', hoa_don__can_doi_soat=False),
        'reconcile': Q(hoa_don__can_doi_soat=True),
        'pending': Q(payment_pending=True, hoa_don__can_doi_soat=False),
        'unpaid': Q(hoa_don__trang_thai='unpaid', payment_pending=False, hoa_don__can_doi_soat=False),
        'failed': Q(hoa_don__trang_thai='unpaid', payment_pending=False, payment_failed=True, hoa_don__can_doi_soat=False),
    }
    if status in filters:
        appointments = appointments.filter(filters[status])
    page = Paginator(appointments, 30).get_page(request.GET.get('page'))
    return render(request, 'core/payments/list.html', {'page_obj': page, 'q': query, 'status': status,
        'profile': cashier_profile(request.user), 'monitor': monitor,
        'detail_route': 'cashier_monitor_detail' if monitor else 'payment_detail'})


@never_cache
@collector_required
@require_GET
def payment_detail(request, lich_id, monitor=False):
    appointment = get_object_or_404(scoped_appointments(request.user).select_related('benh_nhan', 'bac_si__benh_vien'), pk=lich_id)
    invoice = HoaDon.objects.filter(lich_kham=appointment).first()
    attempts = list(invoice.giao_dichs.select_related('nguoi_tao', 'benh_vien')) if invoice else []
    try:
        services.require_completed(appointment)
        completion_error = ''
    except vnpay.PaymentError as exc:
        completion_error = str(exc)
    pending = any(attempt.trang_thai == 'pending' for attempt in attempts)
    config_error = vnpay.configuration_error()
    invoice_error = ''
    if invoice:
        try:
            validate_invoice_total(invoice)
        except BillingError as exc:
            invoice_error = ' '.join(exc.messages)
    amount_error = payable_error(invoice) if invoice else ''
    available = bool(invoice and invoice.trang_thai == 'unpaid' and not invoice.can_doi_soat
                     and not any(a.trang_thai not in ['failed', 'cancelled', 'expired'] for a in attempts)
                     and not completion_error and not invoice_error and not monitor)
    return render(request, 'core/payments/detail.html', {
        'monitor': monitor, 'list_route': 'cashier_monitor' if monitor else 'payment_list',
        'detail_route': 'cashier_monitor_detail' if monitor else 'payment_detail',
        'can_cash': available and invoice.so_tien > 0 and not invoice.hoan_tat_bhyt,
        'lich': appointment, 'invoice': invoice, 'attempts': attempts,
        'completion_error': completion_error, 'pending': pending, 'invoice_error': invoice_error,
        'amount_error': amount_error,
        'invoice_lines': list(invoice.chi_tiet_hien_hanh) if invoice else [],
        'invoice_logs': LogHeThong.objects.filter(model_name='HoaDon', object_id=invoice.pk).select_related('nguoi_thuc_hien') if invoice else [],
        'config_error': '' if invoice and invoice.hoan_tat_bhyt else config_error,
        'can_start': available and not config_error and not amount_error,
        'ipn_url': vnpay.callback_url('vnpay_ipn') if not config_error else '',
        'return_url': vnpay.callback_url('vnpay_return') if not config_error else '',
    })


@collector_required
@require_POST
def invoice_create(request, lich_id):
    get_object_or_404(scoped_appointments(request.user), pk=lich_id)
    phieu = get_object_or_404(PhieuKham, lich_kham_id=lich_id)
    try:
        # Recovery for completed older exams with services and no invoice.
        # This endpoint never accepts a browser-supplied amount or reprices an invoice.
        invoice = bill_exam(phieu.pk, request.user, remote_addr=request.META.get('REMOTE_ADDR', ''))
        messages.success(request, f'Hóa đơn #{invoice.pk} đã được lưu; số tiền lấy từ dữ liệu đã chốt.')
    except BillingError as exc:
        messages.error(request, ' '.join(exc.messages))
    return redirect('payment_detail', lich_id=lich_id)


@never_cache
@collector_required
@require_POST
def payment_start(request, invoice_id):
    invoice = get_object_or_404(HoaDon.objects.filter(lich_kham__in=scoped_appointments(request.user)), pk=invoice_id)
    try:
        # Do not trust client-supplied amount or arbitrary forwarding headers.
        _, url = services.start_payment(invoice.pk, request.user, request.META.get('REMOTE_ADDR', ''))
    except vnpay.PaymentError as exc:
        messages.error(request, str(exc))
        return redirect('payment_detail', lich_id=invoice.lich_kham_id)
    return redirect(url)


@never_cache
@collector_required
@require_POST
def payment_cash(request, invoice_id):
    invoice = get_object_or_404(HoaDon.objects.filter(lich_kham__in=scoped_appointments(request.user)), pk=invoice_id)
    try:
        if request.POST.get('received_full') != 'yes':
            raise vnpay.PaymentError('Cần xác nhận đã nhận đủ toàn bộ số tiền trên hóa đơn.')
        services.collect_cash(invoice.pk, request.user, expected_version=request.POST.get('version'))
        messages.success(request, 'Đã ghi nhận thanh toán toàn bộ bằng tiền mặt.')
    except vnpay.PaymentError as exc:
        messages.error(request, str(exc))
    return redirect('payment_detail', lich_id=invoice.lich_kham_id)


@never_cache
@require_GET
@sensitive_variables()
def vnpay_ipn(request):
    try:
        params = vnpay.verified_params(request.GET)
    except vnpay.PaymentError:
        return JsonResponse({'RspCode': '97', 'Message': 'Invalid Signature'})
    try:
        code, message = services.process_ipn(params)
    except DatabaseError:
        # Rollback happens in the service; never acknowledge a failed commit.
        code, message = '99', 'Database unavailable; retry notification'
    return JsonResponse({'RspCode': code, 'Message': message})


@never_cache
@require_GET
@sensitive_variables()
def vnpay_return(request):
    # Public signed return displays only a reference/status, never patient data.
    # No write, even for a successful, authentic return received before IPN.
    context = {}
    try:
        params = vnpay.verified_params(request.GET)
        attempt = GiaoDichThanhToan.objects.filter(ma_tham_chieu=params.get('vnp_TxnRef', ''), phuong_thuc='VNPAY').first()
        if attempt is None or services.validate_callback(params, attempt):
            raise vnpay.PaymentError('Invalid result')
        context = {'attempt': attempt, 'can_collect': services.can_collect(request.user)
                   and scoped_appointments(request.user).filter(pk=attempt.hoa_don.lich_kham_id).exists()}
    except vnpay.PaymentError:
        context['error'] = 'Không xác minh được thông tin trả về. Vui lòng nhờ nhân viên kiểm tra hóa đơn.'
    return render(request, 'core/payments/return.html', context, status=400 if context.get('error') else 200)
