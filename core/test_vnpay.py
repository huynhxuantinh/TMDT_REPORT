"""Run against a disposable PostgreSQL/PostGIS test database, not SQLite."""
import datetime
import hashlib
import hmac
from io import StringIO
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from django.contrib.auth.models import Permission, User
from django.contrib.gis.geos import Point
from django.db import DatabaseError, IntegrityError, close_old_connections, transaction
from django.core.management import call_command
from django.http import QueryDict
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import BacSi, BenhNhan, BenhVien, DanhMucDichVu, DichVuThucHien, GiaoDichThanhToan, HoaDon, Khoa, LichKham, LogHeThong, PhieuKham
from core.payments import services, vnpay


CONFIG = {
    'VNPAY_TMN_CODE': 'TESTCODE', 'VNPAY_HASH_SECRET': 'test-only-secret-not-a-merchant-credential',
    'VNPAY_PUBLIC_BASE_URL': 'https://payment-test.ngrok-free.app',
}


def sign_callback(data):
    # Independent encoder/signing path for provider simulation.
    encoded = urlencode(sorted((key, value) for key, value in data.items() if value != ''))
    digest = hmac.new(CONFIG['VNPAY_HASH_SECRET'].encode(), encoded.encode(), hashlib.sha512).hexdigest()
    return {**data, 'vnp_SecureHash': digest}


def fixtures():
    admin = User.objects.create_user('collector_admin', is_staff=True, is_superuser=True)
    staff = User.objects.create_user('collector_staff', is_staff=True)
    patient_user = User.objects.create_user('patient')
    hospital = BenhVien.objects.create(
        ten='Bệnh viện kiểm thử', dia_chi='Địa chỉ kiểm thử', phuong='Phường kiểm thử',
        vi_tri=Point(106.7, 10.77, srid=4326), gio_mo=datetime.time(7), gio_dong=datetime.time(17), loai_hinh='cong',
    )
    department = Khoa.objects.create(ten='Nội', benh_vien=hospital)
    doctor = BacSi.objects.create(ho_ten='Bác sĩ kiểm thử', chuyen_khoa='Nội', khoa=department,
                                 benh_vien=hospital, so_dien_thoai='0901000001')
    patient = BenhNhan.objects.create(user=patient_user, ho_ten='Bệnh nhân kiểm thử',
                                     ngay_sinh=datetime.date(2000, 1, 1), gioi_tinh='nam',
                                     so_dien_thoai='0901000002', dia_chi='Địa chỉ kiểm thử')
    appointment = LichKham.objects.create(benh_nhan=patient, bac_si=doctor, ngay_kham=timezone.localdate(),
                                        gio_kham=datetime.time(9), trang_thai='xong')
    PhieuKham.objects.create(lich_kham=appointment, benh_nhan=patient, trieu_chung='Test',
                            chan_doan='Test', huong_dieu_tri='Test')
    return admin, staff, patient_user, appointment


@override_settings(**CONFIG)
class VnpayProtocolTests(SimpleTestCase):
    def test_config_command_prints_exact_urls_without_secrets(self):
        output = StringIO()
        call_command('vnpay_config', stdout=output)
        self.assertIn(CONFIG['VNPAY_PUBLIC_BASE_URL'] + '/thanh-toan/vnpay/ipn/', output.getvalue())
        self.assertIn(CONFIG['VNPAY_PUBLIC_BASE_URL'] + '/thanh-toan/vnpay/return/', output.getvalue())
        self.assertNotIn(CONFIG['VNPAY_HASH_SECRET'], output.getvalue())
        self.assertNotIn(CONFIG['VNPAY_TMN_CODE'], output.getvalue())

    def test_encoding_order_and_excluded_hash_fields(self):
        data = {'vnp_TxnRef': 'abc', 'vnp_OrderInfo': 'Hoa don + / & đ', 'vnp_Amount': '1000000',
                'vnp_SecureHash': 'ignored', 'vnp_SecureHashType': 'ignored', 'other': 'ignored', 'vnp_Empty': ''}
        expected = 'vnp_Amount=1000000&vnp_OrderInfo=Hoa+don+%2B+%2F+%26+%C4%91&vnp_TxnRef=abc'
        self.assertEqual(vnpay.canonical_query(data), expected)
        expected_hash = hmac.new(b'secret', expected.encode(), hashlib.sha512).hexdigest()
        self.assertEqual(vnpay.signature(data, 'secret'), expected_hash)

    def test_signature_round_trip_and_tampering(self):
        query = QueryDict(urlencode(sign_callback({'vnp_Amount': '1000000', 'vnp_OrderInfo': 'Hoa don + 1'})))
        self.assertEqual(vnpay.verified_params(query)['vnp_OrderInfo'], 'Hoa don + 1')
        query = query.copy()
        query['vnp_Amount'] = '2000000'
        with self.assertRaises(vnpay.PaymentError):
            vnpay.verified_params(query)

    def test_duplicate_parameters_rejected(self):
        query = QueryDict(urlencode(sign_callback({'vnp_TxnRef': 'one'})) + '&vnp_TxnRef=two')
        with self.assertRaises(vnpay.PaymentError):
            vnpay.verified_params(query)

    def test_unknown_and_ambiguous_results_remain_pending(self):
        for response, state in [('00', '01'), ('00', '02'), ('07', '07'), ('24', '01'),
                                ('99', '02'), ('00', '04'), ('00', '05'), ('00', '06'), ('98', '99')]:
            with self.subTest(response=response, state=state):
                self.assertEqual(vnpay.result_state(response, state), 'pending')

    def test_invalid_public_origins_block_payment(self):
        for origin in ['http://example.com', 'https://example.com/path', 'https://x@y.com',
                       'https://example.com?x=1', 'https://example.com:wrong', 'https://example.com:444']:
            with self.subTest(origin=origin), override_settings(VNPAY_PUBLIC_BASE_URL=origin):
                self.assertTrue(vnpay.configuration_error())


@override_settings(**CONFIG)
class VnpayPaymentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.staff, cls.patient_user, cls.appointment = fixtures()

    def setUp(self):
        self.client.force_login(self.admin)
        self.invoice = services.create_invoice(self.appointment.pk, Decimal('100000'), self.admin)

    def start(self):
        return services.start_payment(self.invoice.pk, self.admin, '127.0.0.1')[0]

    def callback(self, attempt, response='00', status='00', **overrides):
        data = {
            'vnp_TmnCode': CONFIG['VNPAY_TMN_CODE'], 'vnp_TxnRef': attempt.ma_tham_chieu,
            'vnp_Amount': str(int(attempt.so_tien) * 100), 'vnp_ResponseCode': response,
            'vnp_TransactionStatus': status, 'vnp_TransactionNo': '12345678',
            'vnp_PayDate': '20261028120000', 'vnp_OrderInfo': f'Thanh toan hoa don {self.invoice.pk}',
        }
        data.update(overrides)
        return sign_callback(data)

    def ipn(self, data):
        return self.client.get(reverse('vnpay_ipn'), data).json()

    def assert_unpaid(self):
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.trang_thai, 'unpaid')
        self.assertIsNone(self.invoice.ngay_thanh_toan)

    def test_unfinished_appointment_cannot_create_invoice_or_start_payment(self):
        for state in ['cho', 'dang', 'huy']:
            with self.subTest(state=state):
                LichKham.objects.filter(pk=self.appointment.pk).update(trang_thai=state)
                with self.assertRaises(vnpay.PaymentError):
                    services.create_invoice(self.appointment.pk, 100000, self.admin)
                with self.assertRaises(vnpay.PaymentError):
                    self.start()
        self.assertEqual(GiaoDichThanhToan.objects.count(), 0)

    def test_completed_status_without_exam_is_not_enough(self):
        PhieuKham.objects.filter(lich_kham=self.appointment).delete()
        with self.assertRaises(vnpay.PaymentError):
            self.start()

    def test_duplicate_invoice_rejected(self):
        with self.assertRaises(vnpay.PaymentError):
            services.create_invoice(self.appointment.pk, 200000, self.admin)
        with self.assertRaises(IntegrityError), transaction.atomic():
            HoaDon.objects.create(lich_kham=self.appointment, so_tien=100000)

    def test_invalid_amounts_rejected(self):
        for amount in ['0', '-1', '100.1', 'NaN', 'Infinity', '10000000000', 'abc']:
            with self.subTest(amount=amount), self.assertRaises(vnpay.PaymentError):
                services.create_invoice(self.appointment.pk, amount, self.admin)

    def test_payment_url_and_amount_come_from_database(self):
        response = self.client.post(reverse('payment_start', args=[self.invoice.pk]), {'so_tien': '1', 'amount': '1'})
        self.assertEqual(response.status_code, 302)
        url = urlsplit(response.url)
        self.assertEqual((url.scheme, url.netloc, url.path), ('https', 'sandbox.vnpayment.vn', '/paymentv2/vpcpay.html'))
        params = {key: values[0] for key, values in parse_qs(url.query).items()}
        self.assertEqual(params['vnp_Amount'], '10000000')
        self.assertEqual(params['vnp_ReturnUrl'], CONFIG['VNPAY_PUBLIC_BASE_URL'] + '/thanh-toan/vnpay/return/')
        self.assertNotIn('vnp_BankCode', params)
        self.assertEqual(params['vnp_Version'], '2.1.0')
        self.assertEqual(params['vnp_CurrCode'], 'VND')
        supplied = params.pop('vnp_SecureHash')
        self.assertEqual(sign_callback(params)['vnp_SecureHash'], supplied)
        created = datetime.datetime.strptime(params['vnp_CreateDate'], '%Y%m%d%H%M%S')
        expires = datetime.datetime.strptime(params['vnp_ExpireDate'], '%Y%m%d%H%M%S')
        self.assertEqual(expires - created, datetime.timedelta(minutes=15))
        self.assertEqual(GiaoDichThanhToan.objects.get().so_tien, self.invoice.so_tien)

    def test_missing_configuration_blocks_without_creating_attempt(self):
        for field in CONFIG:
            with self.subTest(field=field), override_settings(**{field: ''}):
                response = self.client.post(reverse('payment_start', args=[self.invoice.pk]), follow=True)
                self.assertContains(response, field)
                self.assertEqual(GiaoDichThanhToan.objects.count(), 0)

    def test_invalid_ip_rolls_back_attempt(self):
        with self.assertRaises(vnpay.PaymentError):
            services.start_payment(self.invoice.pk, self.admin, 'untrusted')
        self.assertEqual(GiaoDichThanhToan.objects.count(), 0)

    def test_successful_anonymous_ipn_pays_invoice_once(self):
        attempt = self.start()
        self.client.logout()
        data = self.callback(attempt)
        self.assertEqual(self.ipn(data)['RspCode'], '00')
        self.invoice.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(self.invoice.trang_thai, 'paid')
        self.assertEqual(attempt.trang_thai, 'success')
        self.assertEqual(attempt.hoan_tat_luc, self.invoice.ngay_thanh_toan)
        completed = attempt.hoan_tat_luc
        self.assertEqual(self.ipn(data)['RspCode'], '02')
        attempt.refresh_from_db()
        self.assertEqual(attempt.hoan_tat_luc, completed)
        self.assertEqual(GiaoDichThanhToan.objects.filter(trang_thai='success').count(), 1)
        with self.assertRaises(vnpay.PaymentError):
            self.start()

    def test_verified_failure_cancellation_and_expiry_allow_retry(self):
        for response, expected in [('51', 'failed'), ('24', 'cancelled'), ('11', 'expired')]:
            with self.subTest(response=response):
                attempt = self.start()
                data = self.callback(attempt, response, '02')
                self.assertEqual(self.ipn(data)['RspCode'], '00')
                self.assertEqual(self.ipn(data)['RspCode'], '02')
                attempt.refresh_from_db()
                self.assertEqual(attempt.trang_thai, expected)
                self.assertIsNotNone(attempt.hoan_tat_luc)
                self.assert_unpaid()
        retry = self.start()
        self.assertEqual(GiaoDichThanhToan.objects.count(), 4)
        self.assertEqual(self.ipn(self.callback(retry))['RspCode'], '00')

    def test_return_does_not_confirm_success_or_failure(self):
        attempt = self.start()
        self.client.logout()
        for response, state in [('00', '00'), ('24', '02')]:
            result = self.client.get(reverse('vnpay_return'), self.callback(attempt, response, state))
            self.assertContains(result, 'chờ xác nhận IPN')
            self.assertNotContains(result, self.appointment.benh_nhan.ho_ten)
            self.assert_unpaid()
            attempt.refresh_from_db()
            self.assertEqual(attempt.trang_thai, 'pending')
        with self.assertRaises(vnpay.PaymentError):
            self.start()

    def test_return_after_ipn_displays_confirmed_status(self):
        attempt = self.start()
        data = self.callback(attempt)
        self.ipn(data)
        self.assertContains(self.client.get(reverse('vnpay_return'), data), 'Thanh toán thành công')

    def test_invalid_return_is_rejected_without_writes(self):
        attempt = self.start()
        data = self.callback(attempt, vnp_Amount='1')
        self.assertEqual(self.client.get(reverse('vnpay_return'), data).status_code, 400)
        data = self.callback(attempt)
        data['vnp_SecureHash'] = '0' * 128
        self.assertEqual(self.client.get(reverse('vnpay_return'), data).status_code, 400)
        self.assert_unpaid()

    def test_bad_signature_rejected(self):
        data = self.callback(self.start())
        data['vnp_SecureHash'] = '0' * 128
        self.assertEqual(self.ipn(data)['RspCode'], '97')
        self.assert_unpaid()

    def test_wrong_amount_rejected(self):
        self.assertEqual(self.ipn(self.callback(self.start(), vnp_Amount='100'))['RspCode'], '04')
        self.assert_unpaid()

    def test_unknown_reference_rejected(self):
        self.assertEqual(self.ipn(self.callback(self.start(), vnp_TxnRef='unknown'))['RspCode'], '01')
        self.assert_unpaid()

    def test_wrong_terminal_rejected(self):
        self.assertEqual(self.ipn(self.callback(self.start(), vnp_TmnCode='OTHER'))['RspCode'], '97')
        self.assert_unpaid()

    def test_invalid_result_fields_rejected(self):
        attempt = self.start()
        for override in [{'vnp_TransactionStatus': ''}, {'vnp_ResponseCode': '000'},
                         {'vnp_TransactionNo': 'invalid'}, {'vnp_TransactionNo': '0'}, {'vnp_PayDate': 'invalid'}]:
            with self.subTest(override=override):
                self.assertEqual(self.ipn(self.callback(attempt, **override))['RspCode'], '99')
        self.assert_unpaid()

    def test_pending_ipn_keeps_lock_and_allows_later_success(self):
        attempt = self.start()
        self.assertEqual(self.ipn(self.callback(attempt, '00', '01'))['RspCode'], '99')
        attempt.refresh_from_db()
        self.assertEqual(attempt.trang_thai, 'pending')
        self.assertIsNone(attempt.hoan_tat_luc)
        with self.assertRaises(vnpay.PaymentError):
            self.start()
        self.assertEqual(self.ipn(self.callback(attempt))['RspCode'], '00')

    def test_missing_ipn_and_local_expiry_unlocks_charge_with_audit_log(self):
        attempt = self.start()
        GiaoDichThanhToan.objects.filter(pk=attempt.pk).update(het_han_luc=timezone.now() - datetime.timedelta(hours=1))
        self.assert_unpaid()
        self.assertIsNotNone(services.expire_pending_payment(attempt.pk))
        attempt.refresh_from_db()
        self.assertEqual(attempt.trang_thai, 'expired')
        self.assertIsNotNone(attempt.hoan_tat_luc)
        self.assertTrue(LogHeThong.objects.filter(model_name='GiaoDichThanhToan', object_id=attempt.pk).exists())
        self.assertEqual(self.start().trang_thai, 'pending')

    def test_admin_can_close_reconciliation_after_checking_vnpay(self):
        attempt = self.start()
        HoaDon.objects.filter(pk=self.invoice.pk).update(can_doi_soat=True)
        response = self.client.post(reverse('cashier_monitor_reconcile_close', args=[self.invoice.pk]))
        self.assertRedirects(response, reverse('cashier_monitor_detail', args=[self.appointment.pk]))
        attempt.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(attempt.trang_thai, 'expired')
        self.assertFalse(self.invoice.can_doi_soat)
        self.assertTrue(LogHeThong.objects.filter(model_name='HoaDon', object_id=self.invoice.pk,
                                                  ghi_chu__contains='ket thuc doi soat').exists())

    def test_conflicting_callback_does_not_overwrite_terminal_status(self):
        attempt = self.start()
        self.ipn(self.callback(attempt))
        self.assertEqual(self.ipn(self.callback(attempt, '24', '02'))['RspCode'], '99')
        attempt.refresh_from_db()
        self.assertEqual(attempt.trang_thai, 'success')

    def test_conflicting_success_after_failure_freezes_new_charges(self):
        attempt = self.start()
        self.assertEqual(self.ipn(self.callback(attempt, '51', '02'))['RspCode'], '00')
        self.assertEqual(self.ipn(self.callback(attempt))['RspCode'], '99')
        self.invoice.refresh_from_db()
        self.assertTrue(self.invoice.can_doi_soat)
        self.assert_unpaid()
        with self.assertRaises(vnpay.PaymentError):
            self.start()
        self.assertContains(self.client.get(reverse('payment_detail', args=[self.appointment.pk])), 'Đã khóa lần thu mới')
        self.assertContains(self.client.get(reverse('vnpay_return'), self.callback(attempt)), 'cần đối soát')

    def test_database_error_rolls_back_both_records_and_requests_retry(self):
        attempt = self.start()
        with patch.object(HoaDon, 'save', side_effect=DatabaseError('test error')):
            self.assertEqual(self.ipn(self.callback(attempt))['RspCode'], '99')
        self.assert_unpaid()
        attempt.refresh_from_db()
        self.assertEqual(attempt.trang_thai, 'pending')
        self.assertEqual(attempt.ma_phan_hoi, '')

    def test_database_prevents_multiple_pending_and_successful_attempts(self):
        attempt = self.start()
        fields = dict(hoa_don=self.invoice, so_tien=100000, tmn_code='TESTCODE', het_han_luc=timezone.now())
        with self.assertRaises(IntegrityError), transaction.atomic():
            GiaoDichThanhToan.objects.create(**fields)
        self.ipn(self.callback(attempt))
        with self.assertRaises(IntegrityError), transaction.atomic():
            GiaoDichThanhToan.objects.create(**fields, trang_thai='success', hoan_tat_luc=timezone.now())

    def test_patient_and_unprivileged_staff_cannot_use_counter(self):
        for user in [self.patient_user, self.staff]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('payment_list')).status_code, 403)
            self.assertEqual(self.client.get(reverse('payment_detail', args=[self.appointment.pk])).status_code, 403)
            self.assertEqual(self.client.post(reverse('invoice_create', args=[self.appointment.pk]), {'so_tien': 100000}).status_code, 403)
            self.assertEqual(self.client.post(reverse('payment_start', args=[self.invoice.pk])).status_code, 403)
        self.assertEqual(GiaoDichThanhToan.objects.count(), 0)

    def test_authorized_staff_can_use_counter(self):
        self.staff.user_permissions.add(Permission.objects.get(codename='thu_vnpay', content_type__app_label='core'))
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse('payment_list')).status_code, 200)
        response = self.client.post(reverse('payment_start', args=[self.invoice.pk]))
        self.assertTrue(response.url.startswith('https://sandbox.vnpayment.vn/'))

    def test_anonymous_counter_redirects_to_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('payment_list')).status_code, 302)
        self.assertEqual(self.client.post(reverse('payment_start', args=[self.invoice.pk])).status_code, 302)

    def test_get_cannot_mutate_and_post_requires_csrf(self):
        for name, pk in [('invoice_create', self.appointment.pk), ('payment_start', self.invoice.pk)]:
            url = reverse(name, args=[pk])
            self.assertEqual(self.client.get(url).status_code, 405)
            csrf_client = Client(enforce_csrf_checks=True)
            csrf_client.force_login(self.admin)
            self.assertEqual(csrf_client.post(url, {'so_tien': 100000}).status_code, 403)
        self.assertEqual(self.client.post(reverse('vnpay_ipn')).status_code, 405)

    def test_invoice_form_saves_before_payment(self):
        self.invoice.delete()
        service = DanhMucDichVu.objects.create(ma_dich_vu='TEST_FEE', ten='Khám', nhom='Khám bệnh', don_gia=250000)
        DichVuThucHien.objects.create(phieu_kham=self.appointment.phieu_kham, dich_vu=service,
                                      nguoi_thuc_hien=self.appointment.bac_si)
        response = self.client.post(reverse('invoice_create', args=[self.appointment.pk]), {'so_tien': '1'}, follow=True)
        self.assertContains(response, 'số tiền lấy từ dữ liệu đã chốt')
        self.assertEqual(HoaDon.objects.get().so_tien, Decimal('250000'))
        self.assertEqual(GiaoDichThanhToan.objects.count(), 0)

    def test_counter_template_includes_exact_callback_urls(self):
        response = self.client.get(reverse('payment_detail', args=[self.appointment.pk]))
        self.assertContains(response, CONFIG['VNPAY_PUBLIC_BASE_URL'] + '/thanh-toan/vnpay/ipn/')
        self.assertContains(response, CONFIG['VNPAY_PUBLIC_BASE_URL'] + '/thanh-toan/vnpay/return/')


@override_settings(**CONFIG)
class VnpayConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin, _, _, self.appointment = fixtures()
        self.invoice = services.create_invoice(self.appointment.pk, 100000, self.admin)

    def concurrent(self, action):
        barrier = Barrier(2)

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return action()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            return [future.result(timeout=20) for future in futures]

    def test_simultaneous_starts_create_one_attempt(self):
        def action():
            try:
                services.start_payment(self.invoice.pk, self.admin, '127.0.0.1')
                return 'created'
            except vnpay.PaymentError:
                return 'blocked'
        self.assertCountEqual(self.concurrent(action), ['created', 'blocked'])
        self.assertEqual(GiaoDichThanhToan.objects.count(), 1)

    def test_simultaneous_ipns_confirm_once(self):
        attempt, _ = services.start_payment(self.invoice.pk, self.admin, '127.0.0.1')
        params = sign_callback({
            'vnp_TmnCode': CONFIG['VNPAY_TMN_CODE'], 'vnp_TxnRef': attempt.ma_tham_chieu,
            'vnp_Amount': '10000000', 'vnp_ResponseCode': '00', 'vnp_TransactionStatus': '00', 'vnp_TransactionNo': '12345678',
        })
        def action():
            return Client().get(reverse('vnpay_ipn'), params).json()['RspCode']
        self.assertCountEqual(self.concurrent(action), ['00', '02'])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.trang_thai, 'paid')
        self.assertEqual(GiaoDichThanhToan.objects.filter(trang_thai='success').count(), 1)

    def test_simultaneous_invoice_creation_is_unique(self):
        self.invoice.delete()
        def action():
            try:
                services.create_invoice(self.appointment.pk, 100000, self.admin)
                return 'created'
            except vnpay.PaymentError:
                return 'blocked'
        self.assertCountEqual(self.concurrent(action), ['created', 'blocked'])
        self.assertEqual(HoaDon.objects.count(), 1)
