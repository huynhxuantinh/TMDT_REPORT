from django.core.management.base import BaseCommand, CommandError

from core.payments.vnpay import callback_url, configuration_error


class Command(BaseCommand):
    help = 'Kiểm tra cấu hình và in IPN/Return URL VNPAY Sandbox, không in thông tin bí mật.'

    def handle(self, *args, **options):
        error = configuration_error()
        if error:
            raise CommandError(error)
        self.stdout.write('IPN URL (nhập tại VNPAY SIT Testing): ' + callback_url('vnpay_ipn'))
        self.stdout.write('ReturnURL (tự gửi trong yêu cầu thanh toán): ' + callback_url('vnpay_return'))
        self.stdout.write(self.style.SUCCESS('Cấu hình đủ. Chưa xác minh kết nối ngrok hoặc giao dịch thực tế.'))
