from django import forms

from core.models import DanhMucDichVu


class DichVuForm(forms.ModelForm):
    class Meta:
        model = DanhMucDichVu
        fields = ['ma_dich_vu', 'ten', 'nhom', 'mo_ta', 'don_gia', 'dang_ap_dung']
        widgets = {
            'mo_ta': forms.Textarea(attrs={'rows': 4}),
            'don_gia': forms.NumberInput(attrs={'min': '0', 'max': '9999999999', 'step': '1'}),
        }
        help_texts = {
            'ma_dich_vu': 'Mã được chuẩn hóa thành chữ in hoa và giữ cố định sau khi tạo.',
            'nhom': 'Nhập nhóm hiện có hoặc tên nhóm mới, ví dụ: Khám bệnh, Xét nghiệm.',
            'don_gia': 'Nhập số nguyên VND, từ 0 đến 9.999.999.999. Đây là bảng giá chung toàn hệ thống.',
        }
        error_messages = {
            'ma_dich_vu': {'unique': 'Mã dịch vụ này đã tồn tại.', 'required': 'Vui lòng nhập mã dịch vụ.'},
            'ten': {'required': 'Vui lòng nhập tên dịch vụ.'},
            'nhom': {'required': 'Vui lòng nhập nhóm dịch vụ.'},
            'don_gia': {
                'required': 'Vui lòng nhập đơn giá.', 'invalid': 'Đơn giá phải là số nguyên VND hợp lệ.',
                'min_value': 'Đơn giá không được âm.', 'max_decimal_places': 'Đơn giá VND không có phần thập phân.',
                'max_digits': 'Đơn giá tối đa 9.999.999.999 VND.',
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # Enforced by Django's bound form, not just an HTML disabled attribute.
            self.fields['ma_dich_vu'].disabled = True

    def clean_ma_dich_vu(self):
        return self.cleaned_data['ma_dich_vu'].strip().upper()
