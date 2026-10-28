from django import forms
from core.models import QuyTacBHYT


class QuyTacBHYTForm(forms.ModelForm):
    class Meta:
        model = QuyTacBHYT
        fields = ['dich_vu', 'ten', 'ty_le', 'hieu_luc_tu', 'hieu_luc_den', 'dang_ap_dung']
        widgets = {'hieu_luc_tu': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
                   'hieu_luc_den': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
                   'ty_le': forms.NumberInput(attrs={'min': '0', 'max': '100', 'step': '0.01'})}
        help_texts = {'ty_le': 'Dữ liệu đồ án do Admin cấu hình, không phải tỷ lệ BHYT chính thức.',
                      'dich_vu': 'Mỗi dịch vụ có tối đa một quy tắc. Chỉ xét khi thẻ còn hiệu lực vào ngày khám và bệnh viện có BHYT.',
                      'hieu_luc_den': 'Để trống nếu quy tắc chưa có ngày kết thúc.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['dich_vu'].disabled = True
