from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from core.models import LogHeThong, QuyTacBHYT
from .forms import QuyTacBHYTForm
from .services import require_admin, save_rule


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
def rule_list(request):
    keyword = request.GET.get('q', '').strip()
    rules = QuyTacBHYT.objects.select_related('dich_vu')
    if keyword:
        rules = rules.filter(Q(ten__icontains=keyword) | Q(dich_vu__ma_dich_vu__icontains=keyword) | Q(dich_vu__ten__icontains=keyword))
    return render(request, 'core/insurance/list.html', {'page_obj': Paginator(rules, 25).get_page(request.GET.get('page')), 'q': keyword})


@never_cache
@admin_required
@require_http_methods(['GET', 'POST'])
def rule_form(request, pk=None):
    rule = get_object_or_404(QuyTacBHYT, pk=pk) if pk else None
    form = QuyTacBHYTForm(instance=rule)
    if request.method == 'POST':
        try:
            saved, form = save_rule(request.POST, request.user, pk, request.META.get('REMOTE_ADDR', ''))
        except QuyTacBHYT.DoesNotExist:
            raise Http404('Quy tắc không tồn tại.') from None
        if saved:
            messages.success(request, 'Đã lưu quy tắc BHYT MÔ PHỎNG. Các hóa đơn đã chốt không tự thay đổi.')
            return redirect('bhyt_rule_detail', pk=saved.pk)
        messages.error(request, 'Chưa lưu BHYT MÔ PHỎNG. Vui lòng kiểm tra dữ liệu.')
    return render(request, 'core/custom_admin_form.html', {
        'title': 'Chỉnh sửa BHYT MÔ PHỎNG' if pk else 'Thêm quy tắc BHYT MÔ PHỎNG',
        'object_name': 'Mô phỏng phục vụ đồ án, không phải kết quả giám định BHYT thực tế.',
        'form': form, 'back_href': reverse('bhyt_rule_detail', args=[pk]) if pk else reverse('bhyt_rule_list'),
    })


@never_cache
@admin_required
@require_GET
def rule_detail(request, pk):
    rule = get_object_or_404(QuyTacBHYT.objects.select_related('dich_vu'), pk=pk)
    logs = LogHeThong.objects.filter(model_name='QuyTacBHYT', object_id=pk).select_related('nguoi_thuc_hien').order_by('-thoi_gian', '-pk')
    return render(request, 'core/insurance/detail.html', {'rule': rule, 'history_page': Paginator(logs, 20).get_page(request.GET.get('page'))})
