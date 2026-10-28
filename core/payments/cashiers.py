"""Global administrators manage cashier identities through the existing User."""
from functools import wraps

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods

from core.models import BenhVien, LogHeThong, ThuNgan
from .access import can_admin


class CashierForm(forms.ModelForm):
    benh_vien = forms.ModelChoiceField(label='Bệnh viện được phân công', queryset=BenhVien.objects.order_by('ten'))
    password = forms.CharField(label='Mật khẩu mới', required=False, strip=False,
                               widget=forms.PasswordInput, help_text='Để trống khi sửa nếu giữ mật khẩu hiện tại.')

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']
        labels = {'username': 'Tên đăng nhập', 'first_name': 'Tên', 'last_name': 'Họ',
                  'email': 'Email', 'is_active': 'Đang hoạt động'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password'].required = not bool(self.instance.pk)

    def clean_username(self):
        username = self.cleaned_data['username']
        if username.isdigit():
            raise forms.ValidationError('Tên đăng nhập cần có chữ để sử dụng màn hình đăng nhập hiện tại.')
        return username

    def _post_clean(self):
        super()._post_clean()
        password = self.cleaned_data.get('password')
        if password:
            try:
                validate_password(password, self.instance)
            except forms.ValidationError as exc:
                self.add_error('password', exc)


def admin_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not can_admin(request.user):
            raise PermissionDenied('Chỉ Admin toàn cục được quản lý thu ngân.')
        return view(request, *args, **kwargs)
    return wrapped


@never_cache
@admin_required
@require_GET
def cashier_list(request):
    rows = ThuNgan.objects.select_related('user', 'benh_vien').order_by('user__username')
    return render(request, 'core/payments/cashiers.html', {'page_obj': Paginator(rows, 30).get_page(request.GET.get('page'))})


@never_cache
@sensitive_post_parameters('password')
@admin_required
@require_http_methods(['GET', 'POST'])
@transaction.atomic
def cashier_form(request, pk=None):
    # The same profile row is locked by collection; assignment cannot change mid-payment.
    profile = get_object_or_404(ThuNgan.objects.select_for_update(), pk=pk) if pk else None
    account = User.objects.select_for_update().get(pk=profile.user_id) if profile else User(is_active=True)
    form = CashierForm(request.POST if request.method == 'POST' else None, instance=account,
                       initial={'benh_vien': profile.benh_vien_id} if profile else None)
    if request.method == 'POST' and form.is_valid():
        before = {'benh_vien_id': profile.benh_vien_id, 'is_active': account.is_active} if profile else None
        # ModelForm mutates its instance during validation; read the persisted active flag.
        if before:
            before['is_active'] = User.objects.values_list('is_active', flat=True).get(pk=account.pk)
        account = form.save(commit=False)
        account.is_staff = account.is_superuser = False
        if form.cleaned_data['password']:
            account.set_password(form.cleaned_data['password'])
        account.save()
        if profile is None:
            profile = ThuNgan(user=account)
        profile.benh_vien = form.cleaned_data['benh_vien']
        profile.save()
        LogHeThong.objects.create(model_name='ThuNgan', object_id=profile.pk,
            hanh_dong='update' if before else 'create', nguoi_thuc_hien=request.user,
            du_lieu_cu=before, du_lieu_moi={'user_id': account.pk, 'benh_vien_id': profile.benh_vien_id,
                                          'is_active': account.is_active}, ghi_chu='Quản lý phân công thu ngân')
        messages.success(request, 'Đã lưu tài khoản và bệnh viện của thu ngân.')
        return redirect('cashier_list')
    return render(request, 'core/payments/cashier_form.html', {'form': form, 'profile': profile})
