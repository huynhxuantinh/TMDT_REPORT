from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import DanhMucDichVu
from core.service_catalog.services import record_change


class Command(BaseCommand):
    help = 'Tạo danh mục dịch vụ demo với giá giả định; không ghi đè dịch vụ đã có.'

    @transaction.atomic
    def handle(self, *args, **options):
        rows = [
            ('DEMO_KHAM_TQ', 'Khám tổng quát', 'Khám bệnh', '100000'),
            ('DEMO_KHAM_CK', 'Khám chuyên khoa', 'Khám bệnh', '150000'),
            ('DEMO_XN_MAU', 'Xét nghiệm máu', 'Xét nghiệm', '80000'),
            ('DEMO_XN_NT', 'Xét nghiệm nước tiểu', 'Xét nghiệm', '50000'),
            ('DEMO_SIEU_AM', 'Siêu âm', 'Chẩn đoán hình ảnh', '120000'),
            ('DEMO_X_QUANG', 'Chụp X-quang', 'Chẩn đoán hình ảnh', '100000'),
        ]
        created_count = 0
        for code, name, group, price in rows:
            service, created = DanhMucDichVu.objects.get_or_create(
                ma_dich_vu=code,
                defaults={'ten': name, 'nhom': group, 'don_gia': Decimal(price),
                          'mo_ta': 'Dữ liệu giả định phục vụ đồ án, không phải bảng giá chính thức của bệnh viện.'},
            )
            if created:
                record_change(service, None, note='Tạo dữ liệu giả định bằng seed_dich_vu_demo')
                created_count += 1
        self.stdout.write(self.style.SUCCESS(f'Đã thêm {created_count} dịch vụ demo; giữ nguyên {len(rows) - created_count} mã đã có.'))
