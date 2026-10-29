"""Private Vietnamese PDFs and post-commit Mailtrap-only delivery; no live SMTP."""
import datetime
import io
import smtplib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.mail.backends.locmem import EmailBackend
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader

from core.models import ChiTietHoaDon, EmailPhieuThanhToan, GiaoDichThanhToan, HoaDon, LogHeThong, PhieuKham
from core.payments.services import collect_cash, create_invoice, start_payment
from core.receipts import delivery
from core.receipts.pdf import invoice_snapshot, render_pdf
from core.test_billing import callback
from core.test_cashiers import CashierFixtures
from core.test_vnpay import CONFIG, fixtures as payment_fixtures

MAIL_SETTINGS = {**CONFIG, 'EMAIL_BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
    'EMAIL_HOST': 'sandbox.smtp.mailtrap.io', 'EMAIL_PORT': 2525,
    'EMAIL_HOST_USER': 'fake-sandbox-user', 'EMAIL_HOST_PASSWORD': 'fake-sandbox-password',
    'EMAIL_USE_TLS': True, 'EMAIL_USE_SSL': False, 'EMAIL_TIMEOUT': 5,
    'DEFAULT_FROM_EMAIL': 'receipts@example.test'}


def pdf_text(content):
    return '\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages)


@override_settings(**MAIL_SETTINGS)
class ReceiptPDFTests(CashierFixtures, TestCase):
    def setUp(self):
        self.prepare_cashier()
        self.client.force_login(self.cashier)

    def pdf(self, invoice, route='receipt_view'):
        return self.client.get(reverse(route, args=[invoice.pk]))

    def test_vietnamese_pdf_has_real_invoice_fields_and_embedded_fonts(self):
        self.appointment.benh_nhan.ho_ten = 'Nguyễn Thị Ánh Tuyết'
        self.appointment.benh_nhan.save()
        invoice = self.invoice()
        response = self.pdf(invoice)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF-'))
        text = pdf_text(response.content)
        for value in ['PHIẾU THANH TOÁN NỘI BỘ', 'Nguyễn Thị Ánh Tuyết', self.hospital.ten,
                      self.service.ten, f'Mã hóa đơn: #{invoice.pk}', '100.000 VND', 'Chưa thanh toán']:
            self.assertIn(value, text)
        reader = PdfReader(io.BytesIO(response.content))
        self.assertAlmostEqual(float(reader.pages[0].mediabox.width), 595.28, places=1)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.height), 841.89, places=1)
        fonts = [ref.get_object() for ref in reader.pages[0]['/Resources']['/Font'].values()]
        embedded = [f for f in fonts if '/FontDescriptor' in f and '/FontFile2' in f['/FontDescriptor']]
        self.assertGreaterEqual(len(embedded), 2)
        self.assertTrue(all('/ToUnicode' in font for font in embedded))

    def test_pdf_uses_frozen_prices_and_bhyt_amounts(self):
        self.card()
        self.rule('80')
        invoice = self.invoice()
        self.service.don_gia = 999999
        self.service.save()
        text = pdf_text(self.pdf(invoice).content)
        for value in ['100.000 VND', '80.000 VND', '20.000 VND', 'BHYT hỗ trợ (MÔ PHỎNG)']:
            self.assertIn(value, text)
        self.assertNotIn('999.999', text)
        self.assertNotIn('SIM-CARD-PRIVATE', text)

    def test_unpaid_and_pending_are_not_receipts_confirming_paid(self):
        invoice = self.invoice()
        text = pdf_text(self.pdf(invoice).content)
        self.assertIn('chưa xác nhận đã thu tiền', text)
        self.assertNotIn('Đã thanh toán', text)
        start_payment(invoice.pk, self.cashier, '127.0.0.1')
        text = pdf_text(self.pdf(invoice).content)
        self.assertIn('Đang xử lý', text)
        self.assertNotIn('Đã thanh toán', text)

    def test_cash_paid_pdf_has_method_and_actual_paid_time(self):
        invoice = self.invoice()
        payment = self.cash(invoice)
        text = pdf_text(self.pdf(invoice).content)
        self.assertIn('Đã thanh toán', text)
        self.assertIn('Tiền mặt', text)
        self.assertIn(timezone.localtime(payment.hoan_tat_luc).strftime('%d/%m/%Y %H:%M:%S'), text)

    def test_zero_bhyt_pdf_without_payment_or_email(self):
        self.card()
        self.rule('100')
        invoice = self.invoice()
        text = pdf_text(self.pdf(invoice).content)
        self.assertIn('0 VND', text)
        self.assertIn('Không cần thu tiền', text)
        self.assertIn('Đã hoàn tất bằng BHYT MÔ PHỎNG', text)
        self.assertFalse(GiaoDichThanhToan.objects.exists())
        self.assertFalse(EmailPhieuThanhToan.objects.exists())

    def test_legacy_invoice_has_no_fabricated_services(self):
        invoice = create_invoice(self.appointment.pk, 77000, self.admin)
        text = pdf_text(self.pdf(invoice).content)
        self.assertIn('77.000 VND', text)
        self.assertIn('Hóa đơn cũ chưa lưu chi tiết', text)
        self.assertFalse(ChiTietHoaDon.objects.exists())

    def test_reconciliation_pdf_is_not_unqualified_paid_confirmation(self):
        invoice = self.invoice()
        self.cash(invoice)
        HoaDon.objects.filter(pk=invoice.pk).update(can_doi_soat=True)
        text = pdf_text(self.pdf(invoice).content)
        self.assertIn('Cần đối soát', text)
        self.assertNotIn('Trạng thái: Đã thanh toán', text)

    def test_no_clinical_data_or_interpreted_markup(self):
        PhieuKham.objects.filter(pk=self.phieu.pk).update(chan_doan='PRIVATE-DIAGNOSIS', trieu_chung='PRIVATE-SYMPTOMS')
        self.service.ten = '<b>Khám & kiểm tra</b>'
        self.service.save()
        text = pdf_text(self.pdf(self.invoice()).content)
        self.assertIn('<b>Khám & kiểm tra</b>', text)
        self.assertNotIn('PRIVATE-DIAGNOSIS', text)
        self.assertNotIn('PRIVATE-SYMPTOMS', text)

    def test_multiple_pages_repeat_header_and_fit_a4(self):
        data = invoice_snapshot(self.invoice().pk)
        data['lines'] = data['lines'] * 100
        reader = PdfReader(io.BytesIO(render_pdf(data)))
        self.assertGreater(len(reader.pages), 2)
        for page in reader.pages:
            self.assertIn('MedCare GIS', page.extract_text())
            self.assertAlmostEqual(float(page.mediabox.width), 595.28, places=1)
        self.assertIn('Đơn giá đã chốt', reader.pages[1].extract_text())

    def test_view_download_print_are_private_and_side_effect_free(self):
        invoice = self.invoice()
        before = list(HoaDon.objects.values())
        self.assertTrue(self.pdf(invoice)['Content-Disposition'].startswith('inline;'))
        download = self.pdf(invoice, 'receipt_download')
        self.assertTrue(download['Content-Disposition'].startswith('attachment;'))
        self.assertIn('no-store', download['Cache-Control'])
        self.assertIn('private', download['Cache-Control'])
        self.assertEqual(download['X-Frame-Options'], 'SAMEORIGIN')
        self.assertContains(self.pdf(invoice, 'receipt_print'), 'Ctrl+P')
        self.assertEqual(list(HoaDon.objects.values()), before)
        self.assertFalse(EmailPhieuThanhToan.objects.exists())

    def test_patient_other_patient_doctor_and_anonymous_cannot_download(self):
        invoice = self.invoice()
        for user in [self.patient_user, self.doctor, self.other_doctor]:
            self.client.force_login(user)
            for route in ['receipt_view', 'receipt_download', 'receipt_print']:
                self.assertEqual(self.pdf(invoice, route).status_code, 403)
        self.client.logout()
        self.assertEqual(self.pdf(invoice).status_code, 302)

    def test_cashier_cannot_view_download_print_or_retry_foreign_invoice(self):
        invoice = self.other_invoice()
        for route in ['receipt_view', 'receipt_download', 'receipt_print']:
            self.assertEqual(self.pdf(invoice, route).status_code, 404)
        self.assertEqual(self.client.post(reverse('receipt_email_retry', args=[invoice.pk]), {'attempts': 0}).status_code, 404)
        self.assertFalse(EmailPhieuThanhToan.objects.exists())

    def test_admin_without_collect_permission_can_view_all_receipts(self):
        invoice = self.other_invoice()
        admin = User.objects.create_user('readonly_admin', is_staff=True)
        self.client.force_login(admin)
        self.assertEqual(self.pdf(invoice).status_code, 200)

    def test_buttons_exist_on_invoice_and_retry_requires_post_csrf(self):
        invoice = self.invoice()
        response = self.client.get(reverse('payment_detail', args=[self.appointment.pk]))
        for text in ['Xem phiếu PDF', 'Tải PDF', 'In phiếu', 'Mailtrap Email Testing']:
            self.assertContains(response, text)
        retry = reverse('receipt_email_retry', args=[invoice.pk])
        self.assertEqual(self.client.get(retry).status_code, 405)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.cashier)
        self.assertEqual(secure.post(retry, {'attempts': 0}).status_code, 403)


class MailFixtures(CashierFixtures):
    def prepare_mail(self):
        self.prepare_cashier()
        User.objects.filter(pk=self.patient_user.pk).update(email='patient@example.com')
        self.invoice_row = self.invoice()
        self.backend = EmailBackend()
        mail.outbox = []
        self.factory = patch('core.receipts.delivery.get_connection', return_value=self.backend)
        self.factory_mock = self.factory.start()
        self.addCleanup(self.factory.stop)
        self.client.force_login(self.cashier)

    def row(self):
        return EmailPhieuThanhToan.objects.get(hoa_don=self.invoice_row)

    def ipn(self, payment, code='00', state='00'):
        return self.client.get(reverse('vnpay_ipn'), callback(payment, code, state)).json()['RspCode']

    def retry(self, attempts=None, user=None):
        return delivery.retry_confirmation(self.invoice_row.pk, user or self.cashier,
            self.row().so_lan_thu if attempts is None else attempts)


@override_settings(**MAIL_SETTINGS)
class ReceiptDeliveryTests(MailFixtures, TransactionTestCase):
    def setUp(self):
        self.prepare_mail()

    def test_cash_sends_after_outer_commit_with_pdf_and_registered_email(self):
        with transaction.atomic():
            self.cash(self.invoice_row)
            self.assertEqual(mail.outbox, [])
            self.assertEqual(self.row().trang_thai, 'pending')
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.subject, 'Xác nhận thanh toán dịch vụ khám chữa bệnh')
        self.assertEqual(message.to, ['patient@example.com'])
        self.assertIn('100.000 VND', message.body)
        self.assertIn('Tiền mặt', message.body)
        attachment = message.attachments[0]
        self.assertEqual(attachment.mimetype, 'application/pdf')
        self.assertIn('Đã thanh toán', pdf_text(attachment.content))
        self.assertEqual((self.row().trang_thai, self.row().so_lan_thu), ('sent', 1))
        self.assertIsNotNone(self.row().gui_luc)
        self.factory_mock.assert_called_once()
        self.assertEqual(self.factory_mock.call_args.kwargs['host'], 'sandbox.smtp.mailtrap.io')
        self.assertEqual(self.factory_mock.call_args.kwargs['backend'], 'django.core.mail.backends.smtp.EmailBackend')

    def test_outer_rollback_creates_no_email_or_delivery(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.cash(self.invoice_row)
                raise RuntimeError('rollback')
        self.assertFalse(EmailPhieuThanhToan.objects.exists())
        self.assertEqual(mail.outbox, [])
        self.invoice_row.refresh_from_db()
        self.assertEqual(self.invoice_row.trang_thai, 'unpaid')

    def test_valid_ipn_sends_once_and_duplicate_ipn_does_not_send(self):
        payment, _ = start_payment(self.invoice_row.pk, self.cashier, '127.0.0.1')
        self.assertEqual(self.ipn(payment), '00')
        self.assertEqual(self.ipn(payment), '02')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('VNPAY', mail.outbox[0].body)
        self.assertEqual(EmailPhieuThanhToan.objects.count(), 1)

    def test_return_before_ipn_and_reload_never_send(self):
        payment, _ = start_payment(self.invoice_row.pk, self.cashier, '127.0.0.1')
        for _ in range(2):
            self.assertEqual(self.client.get(reverse('vnpay_return'), callback(payment)).status_code, 200)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(EmailPhieuThanhToan.objects.exists())
        self.ipn(payment)
        for route, args in [('payment_list', []), ('payment_detail', [self.appointment.pk]), ('receipt_view', [self.invoice_row.pk])]:
            self.client.get(reverse(route, args=args))
        self.client.get(reverse('vnpay_return'), callback(payment))
        self.assertEqual(len(mail.outbox), 1)

    def test_invalid_signature_and_failed_ipn_do_not_send(self):
        payment, _ = start_payment(self.invoice_row.pk, self.cashier, '127.0.0.1')
        params = callback(payment)
        params['vnp_SecureHash'] = 'invalid'
        self.assertEqual(self.client.get(reverse('vnpay_ipn'), params).json()['RspCode'], '97')
        self.assertEqual(self.ipn(payment, '51', '02'), '00')
        self.assertEqual(mail.outbox, [])
        self.assertFalse(EmailPhieuThanhToan.objects.exists())

    def test_repeated_cash_post_cannot_send_again(self):
        url = reverse('payment_cash', args=[self.invoice_row.pk])
        data = {'received_full': 'yes', 'version': self.invoice_row.phien_ban}
        self.client.post(url, data)
        self.client.post(url, data)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(GiaoDichThanhToan.objects.filter(trang_thai='success').count(), 1)

    def test_missing_email_is_skipped_without_fake_recipient_then_can_retry(self):
        User.objects.filter(pk=self.patient_user.pk).update(email='')
        self.cash(self.invoice_row)
        self.assertEqual((self.row().trang_thai, self.row().email_nhan), ('skipped', ''))
        self.assertEqual(mail.outbox, [])
        User.objects.filter(pk=self.patient_user.pk).update(email='registered@example.com')
        self.retry()
        self.assertEqual(mail.outbox[0].to, ['registered@example.com'])

    def test_invalid_email_is_skipped_without_smtp(self):
        User.objects.filter(pk=self.patient_user.pk).update(email='not-an-email')
        self.cash(self.invoice_row)
        self.assertEqual(self.row().trang_thai, 'skipped')
        self.factory_mock.assert_not_called()

    def test_missing_patient_account_is_skipped(self):
        self.appointment.benh_nhan.user = None
        self.appointment.benh_nhan.save()
        self.cash(self.invoice_row)
        self.assertEqual(self.row().ma_loi, 'missing_email')
        self.assertEqual(mail.outbox, [])

    def test_pdf_failure_does_not_rollback_payment_and_retry_is_controlled(self):
        with patch('core.receipts.delivery.render_pdf', side_effect=ValueError('private diagnostic')):
            self.cash(self.invoice_row)
        self.assertEqual((self.row().trang_thai, self.row().ma_loi), ('failed', 'pdf_error'))
        self.invoice_row.refresh_from_db()
        self.assertEqual(self.invoice_row.trang_thai, 'paid')
        self.retry(attempts=1)
        self.assertEqual(len(mail.outbox), 1)
        with self.assertRaises(delivery.DeliveryError):
            self.retry(attempts=1)
        with self.assertRaises(delivery.DeliveryError):
            self.retry(attempts=2)
        self.assertEqual(len(mail.outbox), 1)

    def test_connection_failure_can_retry_without_losing_payment(self):
        with patch.object(self.backend, 'open', side_effect=smtplib.SMTPAuthenticationError(535, b'SECRET-MUST-NOT-LOG')):
            self.cash(self.invoice_row)
        self.assertEqual(self.row().ma_loi, 'smtp_open')
        self.invoice_row.refresh_from_db()
        self.assertEqual(self.invoice_row.trang_thai, 'paid')
        self.retry()
        self.assertEqual(len(mail.outbox), 1)
        audits = str(list(LogHeThong.objects.filter(model_name='EmailPhieuThanhToan').values()))
        self.assertNotIn('SECRET-MUST-NOT-LOG', audits)

    def test_explicit_smtp_rejection_can_retry(self):
        with patch.object(self.backend, 'send_messages', side_effect=smtplib.SMTPDataError(550, b'private details')):
            self.cash(self.invoice_row)
        self.assertEqual((self.row().trang_thai, self.row().ma_loi), ('failed', 'smtp_rejected'))
        self.retry()
        self.assertEqual(len(mail.outbox), 1)

    def test_unknown_smtp_result_blocks_retry_even_if_server_received_it(self):
        send = self.backend.send_messages
        def accepted_then_disconnect(messages):
            send(messages)
            raise smtplib.SMTPServerDisconnected('private server response')
        with patch.object(self.backend, 'send_messages', side_effect=accepted_then_disconnect):
            self.cash(self.invoice_row)
        self.assertEqual(self.row().trang_thai, 'unknown')
        self.assertEqual(len(mail.outbox), 1)
        with self.assertRaises(delivery.DeliveryError):
            self.retry()
        self.assertEqual(len(mail.outbox), 1)

    def test_connection_close_error_after_acceptance_does_not_enable_duplicate(self):
        with patch.object(self.backend, 'close', side_effect=OSError('socket closed')):
            self.cash(self.invoice_row)
        self.assertEqual(self.row().trang_thai, 'sent')
        with self.assertRaises(delivery.DeliveryError):
            self.retry()

    def test_delivery_state_write_failure_is_blocked_not_retried(self):
        with patch('core.receipts.delivery.finish', side_effect=DatabaseError('PRIVATE-DB-DETAIL')):
            with self.assertLogs('core.receipts.delivery', level='ERROR') as logs:
                self.cash(self.invoice_row)
        self.assertEqual(self.row().trang_thai, 'sending')
        self.assertNotIn('PRIVATE-DB-DETAIL', str(logs.output))
        self.assertEqual(len(mail.outbox), 1)
        with self.assertRaises(delivery.DeliveryError):
            self.retry()

    def test_queue_failure_does_not_rollback_payment_and_manual_send_recovers(self):
        with patch('core.receipts.delivery.EmailPhieuThanhToan.objects.get_or_create', side_effect=DatabaseError('private')):
            with self.assertLogs('core.receipts.delivery', level='ERROR'):
                self.cash(self.invoice_row)
        self.invoice_row.refresh_from_db()
        self.assertEqual(self.invoice_row.trang_thai, 'paid')
        self.assertFalse(EmailPhieuThanhToan.objects.exists())
        delivery.retry_confirmation(self.invoice_row.pk, self.cashier, 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_sending_host_or_alternative_backend_or_missing_credentials_are_blocked(self):
        cases = [{'EMAIL_HOST': 'live.smtp.mailtrap.io'}, {'EMAIL_HOST': 'smtp.gmail.com'},
                 {'EMAIL_BACKEND': 'django.core.mail.backends.console.EmailBackend'}, {'EMAIL_HOST_PASSWORD': ''},
                 {'EMAIL_USE_TLS': False, 'EMAIL_USE_SSL': False}]
        self.cash(self.invoice_row)  # Start from a genuine paid invoice; no second settlement.
        self.row().delete()
        mail.outbox = []
        self.factory_mock.reset_mock()
        for options in cases:
            with self.subTest(options=options), override_settings(**options):
                row = EmailPhieuThanhToan.objects.create(hoa_don=self.invoice_row)
                delivery.dispatch_safely(row.pk, 0)
                row.refresh_from_db()
                self.assertEqual(row.ma_loi, 'sandbox_config')
                row.delete()
        self.factory_mock.assert_not_called()
        self.assertEqual(mail.outbox, [])

    def test_retry_cannot_forge_recipient_and_admin_can_see_delivery_status(self):
        with override_settings(EMAIL_HOST_PASSWORD=''):
            self.cash(self.invoice_row)
        response = self.client.post(reverse('receipt_email_retry', args=[self.invoice_row.pk]),
                                    {'attempts': 1, 'email': 'attacker@example.com'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mail.outbox[0].to, ['patient@example.com'])
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('cashier_monitor_detail', args=[self.appointment.pk])), 'Đã gửi vào Mailtrap Testing')

    def test_unpaid_or_disputed_invoice_cannot_be_sent_by_retry(self):
        with self.assertRaises(delivery.DeliveryError):
            delivery.retry_confirmation(self.invoice_row.pk, self.cashier, 0)
        with override_settings(EMAIL_HOST_PASSWORD=''):
            self.cash(self.invoice_row)
        HoaDon.objects.filter(pk=self.invoice_row.pk).update(can_doi_soat=True)
        with self.assertRaises(delivery.DeliveryError):
            self.retry()
        self.assertEqual(mail.outbox, [])

    def test_deactivated_cashier_cannot_retry_using_stale_user(self):
        with override_settings(EMAIL_HOST_PASSWORD=''):
            self.cash(self.invoice_row)
        User.objects.filter(pk=self.cashier.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.retry()

    def test_reconcile_requires_delay_and_expected_attempt_then_never_resends_received_mail(self):
        with patch.object(self.backend, 'send_messages', side_effect=TimeoutError):
            self.cash(self.invoice_row)
        with self.assertRaises(delivery.DeliveryError):
            delivery.reconcile(self.invoice_row.pk, 1, True, 'Inbox checked')
        EmailPhieuThanhToan.objects.filter(pk=self.row().pk).update(bat_dau_luc=timezone.now()-datetime.timedelta(minutes=3))
        delivery.reconcile(self.invoice_row.pk, 1, True, 'Inbox checked')
        self.assertEqual(self.row().trang_thai, 'sent')
        with self.assertRaises(delivery.DeliveryError):
            self.retry()

    def test_reconciled_absence_can_be_retried_once(self):
        with patch.object(self.backend, 'send_messages', side_effect=TimeoutError):
            self.cash(self.invoice_row)
        EmailPhieuThanhToan.objects.filter(pk=self.row().pk).update(bat_dau_luc=timezone.now()-datetime.timedelta(minutes=3))
        delivery.reconcile(self.invoice_row.pk, 1, False, 'Verified not received')
        self.retry()
        self.assertEqual(len(mail.outbox), 1)


@override_settings(**MAIL_SETTINGS)
class ReceiptConcurrencyTests(MailFixtures, TransactionTestCase):
    def setUp(self):
        self.prepare_mail()

    def workers(self, *functions):
        barrier = Barrier(len(functions))
        def worker(fn):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return fn()
                except delivery.DeliveryError:
                    return 'blocked'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(functions)) as executor:
            jobs = [executor.submit(worker, fn) for fn in functions]
            return [job.result(timeout=30) for job in jobs]

    def test_concurrent_retry_requests_send_one_email(self):
        with override_settings(EMAIL_HOST_PASSWORD=''):
            self.cash(self.invoice_row)
        self.workers(lambda: self.retry(attempts=1).pk, lambda: self.retry(attempts=1).pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual((self.row().so_lan_thu, self.row().trang_thai), (2, 'sent'))

    def test_concurrent_failed_retries_do_not_repeat_same_attempt(self):
        with override_settings(EMAIL_HOST_PASSWORD=''):
            self.cash(self.invoice_row)
            self.workers(lambda: self.retry(attempts=1).pk, lambda: self.retry(attempts=1).pk)
        self.assertEqual(self.row().so_lan_thu, 2)
        self.assertEqual(mail.outbox, [])

    def test_concurrent_valid_ipns_emit_one_confirmation(self):
        payment, _ = start_payment(self.invoice_row.pk, self.cashier, '127.0.0.1')
        def ipn_request():
            return Client().get(reverse('vnpay_ipn'), callback(payment)).json()['RspCode']
        self.assertCountEqual(self.workers(ipn_request, ipn_request), ['00', '02'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(EmailPhieuThanhToan.objects.count(), 1)

    def test_two_dispatchers_claim_once(self):
        with patch('core.receipts.delivery.dispatch_safely'):
            self.cash(self.invoice_row)
        row = self.row()
        self.workers(lambda: delivery.dispatch_safely(row.pk, 0), lambda: delivery.dispatch_safely(row.pk, 0))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.row().so_lan_thu, 1)


class ReceiptMigrationTests(TransactionTestCase):
    def test_0025_keeps_old_invoice_and_payment_values_and_does_not_backfill_mail(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        old = [('core', '0024_thu_ngan_tien_mat')]
        try:
            executor.migrate(old)
            apps = executor.loader.project_state(old).apps
            admin, _, _, appointment = payment_fixtures()
            OldInvoice = apps.get_model('core', 'HoaDon')
            OldPayment = apps.get_model('core', 'GiaoDichThanhToan')
            invoice = OldInvoice.objects.create(lich_kham_id=appointment.pk, so_tien=50000,
                trang_thai='paid', ngay_thanh_toan=timezone.now(), nguoi_tao_id=admin.pk)
            OldPayment.objects.create(hoa_don_id=invoice.pk, so_tien=50000, trang_thai='success',
                phuong_thuc='CASH', benh_vien_id=appointment.bac_si.benh_vien_id, hoan_tat_luc=timezone.now())
            before = [list(model.objects.values()) for model in [OldInvoice, OldPayment]]
            MigrationExecutor(connection).migrate(latest)
            self.assertEqual(before, [list(model.objects.values()) for model in [OldInvoice, OldPayment]])
            self.assertFalse(EmailPhieuThanhToan.objects.exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
