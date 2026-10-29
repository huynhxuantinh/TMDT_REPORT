from django.core.management.base import BaseCommand

from core.payments.services import expire_due_payments


class Command(BaseCommand):
    help = 'Chuyen giao dich VNPAY pending qua han sang expired va ghi audit log.'

    def handle(self, *args, **options):
        count = expire_due_payments()
        self.stdout.write(self.style.SUCCESS(f'Da het han {count} giao dich VNPAY.'))
