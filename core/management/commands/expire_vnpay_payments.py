from django.core.management.base import BaseCommand

from core.payments.services import expire_due_payments


class Command(BaseCommand):
    help = ('Chuyen giao dich VNPAY pending qua thoi gian cho IPN sang timeout '
            '(khong phai VNPAY bao het han) va ghi audit log.')

    def handle(self, *args, **options):
        count = expire_due_payments()
        self.stdout.write(self.style.SUCCESS(f'Da chuyen {count} giao dich VNPAY sang timeout.'))
