from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from core.models import HoaDon
from core.payments.access import can_admin, can_collect, scoped_appointments
from .delivery import DeliveryError, locked_authorized_invoice, retry_confirmation
from .pdf import render_pdf, snapshot


def receipt_access(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not (can_admin(request.user) or can_collect(request.user)):
            raise PermissionDenied('Chỉ nhân viên có quyền mới được truy cập phiếu thanh toán.')
        return view(request, *args, **kwargs)
    return wrapped


def scoped_invoice(request, invoice_id):
    return get_object_or_404(HoaDon.objects.filter(lich_kham__in=scoped_appointments(request.user)), pk=invoice_id)


@never_cache
@receipt_access
@require_GET
def receipt_pdf(request, invoice_id, download=False):
    scoped_invoice(request, invoice_id)
    with transaction.atomic():
        invoice = locked_authorized_invoice(invoice_id, request.user)
        data = snapshot(invoice)
    response = HttpResponse(render_pdf(data), content_type='application/pdf')
    disposition = 'attachment' if download else 'inline'
    response['Content-Disposition'] = f'{disposition}; filename="phieu-thanh-toan-{invoice_id}.pdf"'
    response['X-Content-Type-Options'] = 'nosniff'
    response['X-Frame-Options'] = 'SAMEORIGIN'
    response['Referrer-Policy'] = 'no-referrer'
    # Private responses are never placed in media/ or a public CDN cache.
    response['Cache-Control'] = 'private, no-store, max-age=0'
    return response


@never_cache
@receipt_access
@require_GET
def receipt_print(request, invoice_id):
    invoice = scoped_invoice(request, invoice_id)
    return render(request, 'core/receipts/print.html', {'invoice': invoice})


@never_cache
@receipt_access
@require_POST
def receipt_retry(request, invoice_id):
    invoice = scoped_invoice(request, invoice_id)
    try:
        retry_confirmation(invoice.pk, request.user, request.POST.get('attempts'))
        messages.success(request, 'Đã xử lý yêu cầu gửi vào Mailtrap Testing. Xem kết quả bên dưới.')
    except DeliveryError as exc:
        messages.error(request, str(exc))
    route = 'cashier_monitor_detail' if can_admin(request.user) and not can_collect(request.user) else 'payment_detail'
    return redirect(route, lich_id=invoice.lich_kham_id)
