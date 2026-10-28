from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Send a test email (useful for verifying Mailtrap SMTP integration)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            dest="to_email",
            required=True,
            help="Recipient email address.",
        )
        parser.add_argument(
            "--subject",
            default="Test email from QL_BENHVIEN",
            help="Email subject.",
        )
        parser.add_argument(
            "--message",
            default="Mailtrap SMTP integration is working.",
            help="Email body text.",
        )

    def handle(self, *args, **options):
        to_email = options["to_email"].strip()
        if not to_email:
            raise CommandError("Missing recipient email. Use --to your@email.com")

        sent_count = send_mail(
            subject=options["subject"],
            message=options["message"],
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to_email],
            fail_silently=False,
        )
        if sent_count != 1:
            raise CommandError("Email was not sent.")

        self.stdout.write(
            self.style.SUCCESS(f"Sent test email to {to_email}")
        )
