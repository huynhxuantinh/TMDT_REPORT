from django import forms
from django.db.models import Q
from django.forms import BaseFormSet, formset_factory

from core.models import DanhMucDichVu


class PerformedServiceForm(forms.Form):
    dich_vu = forms.ModelChoiceField(queryset=DanhMucDichVu.objects.none(), label='Dịch vụ',
                                    error_messages={'invalid_choice': 'Dịch vụ không tồn tại hoặc đã ngừng áp dụng.'})
    so_luong = forms.IntegerField(label='Số lượng', min_value=1, max_value=2147483647, initial=1,
                                 error_messages={'min_value': 'Số lượng phải lớn hơn 0.', 'invalid': 'Số lượng phải là số nguyên dương.'})
    ghi_chu = forms.CharField(label='Ghi chú', required=False, max_length=2000, widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, existing_ids=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dich_vu'].queryset = DanhMucDichVu.objects.filter(
            Q(dang_ap_dung=True) | Q(pk__in=existing_ids)
        ).order_by('ma_dich_vu')


class BasePerformedServiceFormSet(BaseFormSet):
    def clean(self):
        if any(self.errors):
            return
        seen = set()
        for form in self.forms:
            data = form.cleaned_data
            if not data or data.get('DELETE') or not data.get('dich_vu'):
                continue
            service_id = data['dich_vu'].pk
            if service_id in seen:
                raise forms.ValidationError('Mỗi dịch vụ chỉ chọn một lần trong phiếu khám. Hãy điều chỉnh số lượng.')
            seen.add(service_id)


PerformedServiceFormSet = formset_factory(PerformedServiceForm, formset=BasePerformedServiceFormSet,
                                         extra=1, can_delete=True)
