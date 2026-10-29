from django.core.management.base import BaseCommand, CommandError

from core.models import EmailPhieuThanhToan
from core.receipts.delivery import DeliveryError, reconcile


class Command(BaseCommand):
    help = 'Đối soát email chưa rõ kết quả sau khi người vận hành kiểm tra Mailtrap Testing Inbox. Không gửi email.'

    def add_arguments(self, parser):
        parser.add_argument('invoice_id', type=int)
        parser.add_argument('--attempt', type=int, required=True)
        parser.add_argument('--result', choices=['received', 'not-received'], required=True)
        parser.add_argument('--note', required=True, help='Xác nhận đã kiểm tra Inbox; không nhập credentials.')

    def handle(self, *args, **options):
        try:
            row = reconcile(options['invoice_id'], options['attempt'], options['result'] == 'received', options['note'])
        except (DeliveryError, EmailPhieuThanhToan.DoesNotExist) as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(self.style.SUCCESS(f'Hóa đơn #{row.hoa_don_id}: {row.get_trang_thai_display()}. Không gửi thư trong lệnh này.'))
