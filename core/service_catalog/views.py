from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.models import DanhMucDichVu, LogHeThong
from .forms import DichVuForm
from .services import require_admin, save_service, set_service_status


def admin_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        require_admin(request.user)
        return view(request, *args, **kwargs)
    return wrapped


@never_cache
@admin_required
@require_GET
def service_list(request):
    keyword = request.GET.get('q', '').strip()
    group = request.GET.get('nhom', '').strip()
    status = request.GET.get('trang_thai', '')
    services = DanhMucDichVu.objects.all()
    if keyword:
        services = services.filter(Q(ma_dich_vu__icontains=keyword) | Q(ten__icontains=keyword))
    if group:
        services = services.filter(nhom=group)
    if status in ('1', '0'):
        services = services.filter(dang_ap_dung=(status == '1'))
    elif status:
        services = services.none()
    page = Paginator(services, 25).get_page(request.GET.get('page'))
    filters = request.GET.copy()
    filters.pop('page', None)
    return render(request, 'core/service_catalog/list.html', {
        'page_obj': page, 'q': keyword, 'nhom': group, 'trang_thai': status,
        'groups': DanhMucDichVu.objects.order_by('nhom').values_list('nhom', flat=True).distinct(),
        'query_string': filters.urlencode(),
    })


@never_cache
@admin_required
@require_http_methods(['GET', 'POST'])
def service_form(request, pk=None):
    service = get_object_or_404(DanhMucDichVu, pk=pk) if pk else None
    form = DichVuForm(instance=service)
    if request.method == 'POST':
        try:
            saved, form = save_service(request.POST, request.user, pk, request.META.get('REMOTE_ADDR', ''))
        except DanhMucDichVu.DoesNotExist:
            raise Http404('Dịch vụ không tồn tại.') from None
        if saved:
            messages.success(request, 'Đã cập nhật dịch vụ và bảng giá.' if pk else 'Đã thêm dịch vụ y tế.')
            return redirect('service_detail', pk=saved.pk)
        messages.error(request, 'Chưa lưu. Vui lòng kiểm tra các thông tin được đánh dấu bên dưới.')
    # Reuse the current admin form without changing its other consumers.
    return render(request, 'core/custom_admin_form.html', {
        'title': 'Chỉnh sửa dịch vụ y tế' if pk else 'Thêm dịch vụ y tế — bảng giá chung',
        'object_name': str(service) if service else '', 'form': form,
        'back_href': reverse('service_detail', args=[pk]) if pk else reverse('service_list'),
    })


@never_cache
@admin_required
@require_GET
def service_detail(request, pk):
    service = get_object_or_404(DanhMucDichVu, pk=pk)
    history = LogHeThong.objects.filter(model_name='DanhMucDichVu', object_id=pk).select_related('nguoi_thuc_hien').order_by('-thoi_gian', '-pk')
    return render(request, 'core/service_catalog/detail.html', {
        'service': service, 'history_page': Paginator(history, 20).get_page(request.GET.get('page')),
    })


@never_cache
@admin_required
@require_POST
def service_status(request, pk):
    desired = request.POST.get('dang_ap_dung')
    if desired not in ('0', '1'):
        return HttpResponseBadRequest('Trạng thái áp dụng không hợp lệ.')
    try:
        service = set_service_status(pk, desired == '1', request.user, request.META.get('REMOTE_ADDR', ''))
    except DanhMucDichVu.DoesNotExist:
        raise Http404('Dịch vụ không tồn tại.') from None
    messages.success(request, 'Dịch vụ đang áp dụng.' if service.dang_ap_dung else 'Đã ngừng áp dụng dịch vụ; lịch sử được giữ nguyên.')
    return redirect('service_detail', pk=pk)
