"""Cashier scope, full cash settlement, mixed-method races and compatible migration."""
import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.billing.services import bill_exam, invoice_lock_reason
from core.exam_services.services import ExamServiceError, form_context, sync_services
from core.forms import AdminBacSiForm, AdminUserForm
from core.models import (BacSi, BenhVien, ChiTietHoaDon, DichVuThucHien, GiaoDichThanhToan,
                         HoaDon, LichKham, LogHeThong, PhieuKham, QuanLyChiNhanh)
from core.payments.access import can_collect
from core.payments.services import collect_cash, create_invoice, process_ipn, start_payment
from core.payments.vnpay import PaymentError
from core.test_billing import callback
from core.test_exam_services import payload
from core.test_insurance import InsuranceFixtures
from core.test_vnpay import CONFIG, fixtures as payment_fixtures


class CashierFixtures(InsuranceFixtures):
    def prepare_cashier(self):
        self.prepare()
        self.cashier = User.objects.create_user('cashier', password='Cashier-test-73829!')
        self.profile = QuanLyChiNhanh.objects.create(user=self.cashier, benh_vien=self.hospital)

    def cash(self, invoice, user=None):
        return collect_cash(invoice.pk, user or self.cashier, expected_version=invoice.phien_ban)

    def other_hospital(self):
        values = {field.name: getattr(self.hospital, field.name) for field in BenhVien._meta.fields if not field.primary_key}
        values['ten'] = 'Bệnh viện khác'
        return BenhVien.objects.create(**values)

    def other_invoice(self):
        hospital = self.other_hospital()
        doctor = BacSi.objects.create(ho_ten='Bác sĩ khác viện', benh_vien=hospital,
            khoa=self.appointment.bac_si.khoa, chuyen_khoa='Nội', so_dien_thoai='0902000001')
        appointment = LichKham.objects.create(benh_nhan=self.appointment.benh_nhan, bac_si=doctor,
            ngay_kham=self.date, gio_kham=datetime.time(14), trang_thai='xong')
        PhieuKham.objects.create(lich_kham=appointment, benh_nhan=appointment.benh_nhan,
            trieu_chung='PRIVATE CLINICAL', chan_doan='PRIVATE CLINICAL', huong_dieu_tri='PRIVATE CLINICAL')
        return create_invoice(appointment.pk, 70000, self.admin)


@override_settings(**CONFIG)
class CashierAccessTests(CashierFixtures, TestCase):
    def setUp(self):
        self.prepare_cashier()
        self.invoice_row = self.invoice()
        self.client.force_login(self.cashier)

    def test_admin_creates_account_and_one_hospital_without_staff_privilege(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('cashier_create'), {
            'username': 'cashier_new', 'password': 'Random-test-98327!', 'first_name': 'An',
            'benh_vien': self.hospital.pk, 'is_active': 'on', 'is_staff': 'on', 'is_superuser': 'on'})
        self.assertRedirects(response, reverse('cashier_list'))
        account = User.objects.get(username='cashier_new')
        self.assertTrue(account.check_password('Random-test-98327!'))
        self.assertEqual(account.quan_ly_chi_nhanh.benh_vien_id, self.hospital.pk)
        self.assertFalse(account.is_staff or account.is_superuser)
        self.assertTrue(can_collect(account))
        audit = LogHeThong.objects.get(model_name='QuanLyChiNhanh', object_id=account.quan_ly_chi_nhanh.pk)
        self.assertNotIn('password', str(audit.du_lieu_moi))

    def test_admin_can_reassign_disable_and_enable(self):
        hospital = self.other_hospital()
        self.client.force_login(self.admin)
        data = {'username': self.cashier.username, 'benh_vien': hospital.pk}
        self.assertEqual(self.client.post(reverse('cashier_edit', args=[self.profile.pk]), data).status_code, 302)
        self.cashier.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertFalse(self.cashier.is_active)
        self.assertEqual(self.profile.benh_vien_id, hospital.pk)
        self.assertTrue(self.cashier.check_password('Cashier-test-73829!'))
        data['is_active'] = 'on'
        self.client.post(reverse('cashier_edit', args=[self.profile.pk]), data)
        self.cashier.refresh_from_db()
        self.assertTrue(self.cashier.is_active)
        with self.assertRaises(PermissionDenied):
            self.cash(self.invoice_row)

    def test_duplicate_account_and_missing_hospital_cannot_create_profile(self):
        self.client.force_login(self.admin)
        for values in [{'username': self.cashier.username, 'benh_vien': self.hospital.pk}, {'username': 'new'}]:
            response = self.client.post(reverse('cashier_create'), {**values, 'password': 'Valid-839212-Password!'})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['form'].errors)
        self.assertEqual(QuanLyChiNhanh.objects.count(), 1)

    def test_cashier_management_requires_global_admin(self):
        for user in [self.cashier, self.doctor, self.patient_user]:
            self.client.force_login(user)
            for route, args in [('cashier_list', []), ('cashier_create', []), ('cashier_edit', [self.profile.pk]), ('cashier_monitor', [])]:
                self.assertEqual(self.client.get(reverse(route, args=args)).status_code, 403)
            self.assertEqual(self.client.post(reverse('cashier_edit', args=[self.profile.pk]), {}).status_code, 403)

    def test_no_staff_is_automatically_assigned_and_admin_monitor_is_read_only(self):
        staff = User.objects.create_user('global_admin', is_staff=True)
        self.assertFalse(can_collect(staff))
        self.client.force_login(staff)
        self.assertContains(self.client.get(reverse('cashier_monitor')), f'Hóa đơn #{self.invoice_row.pk}')
        response = self.client.get(reverse('cashier_monitor_detail', args=[self.appointment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="received_full"')
        self.assertNotContains(response, 'Thanh toán VNPAY</button>')
        self.assertEqual(self.client.post(reverse('payment_cash', args=[self.invoice_row.pk])).status_code, 403)
        self.assertEqual(QuanLyChiNhanh.objects.count(), 1)

    def test_dashboard_only_own_hospital_and_search_filter(self):
        foreign = self.other_invoice()
        response = self.client.get(reverse('payment_list'))
        self.assertEqual([row.pk for row in response.context['page_obj']], [self.appointment.pk])
        self.assertNotContains(response, 'Bệnh viện khác')
        self.assertEqual(len(self.client.get(reverse('payment_list'), {'q': foreign.pk}).context['page_obj']), 0)
        self.assertEqual(len(self.client.get(reverse('payment_list'), {'q': self.invoice_row.pk}).context['page_obj']), 1)
        self.assertEqual(len(self.client.get(reverse('payment_list'), {'status': 'paid'}).context['page_obj']), 0)
        self.cash(self.invoice_row)
        self.assertContains(self.client.get(reverse('payment_list'), {'status': 'paid'}), 'Tiền mặt')

    def test_cross_hospital_ids_blocked_for_all_endpoints(self):
        foreign = self.other_invoice()
        self.assertEqual(self.client.get(reverse('payment_detail', args=[foreign.lich_kham_id])).status_code, 404)
        for route, pk in [('payment_cash', foreign.pk), ('payment_start', foreign.pk), ('invoice_create', foreign.lich_kham_id)]:
            self.assertEqual(self.client.post(reverse(route, args=[pk]), {'received_full': 'yes', 'version': 1}).status_code, 404)
        for action in [lambda: self.cash(foreign), lambda: start_payment(foreign.pk, self.cashier, '127.0.0.1'),
                       lambda: bill_exam(foreign.lich_kham.phieu_kham.pk, self.cashier)]:
            with self.assertRaises(PermissionDenied):
                action()
        self.assertFalse(GiaoDichThanhToan.objects.exists())

    def test_cashier_can_read_services_but_no_clinical_notes(self):
        PhieuKham.objects.filter(pk=self.phieu.pk).update(chan_doan='PRIVATE CLINICAL', trieu_chung='PRIVATE CLINICAL')
        response = self.client.get(reverse('payment_detail', args=[self.appointment.pk]))
        self.assertContains(response, self.service.ten)
        self.assertContains(response, 'Xác nhận đã nhận tiền')
        self.assertNotContains(response, 'PRIVATE CLINICAL')

    def test_manager_can_edit_catalog_but_not_insurance_exam_or_manual_amount(self):
        self.assertEqual(self.client.get(reverse('service_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('bhyt_rule_list')).status_code, 200)
        self.assertEqual(self.client.post(reverse('service_create'), {
            'ma_dich_vu': 'QL-TEST', 'ten': 'Dịch vụ quản lý', 'nhom': 'Khám bệnh', 'don_gia': '100000',
        }).status_code, 302)
        self.assertEqual(self.client.post(reverse('bhyt_rule_create'), {}).status_code, 200)
        before_exam = list(PhieuKham.objects.values())
        self.assertRedirects(self.client.post(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]), {}), reverse('home'))
        self.assertEqual(list(PhieuKham.objects.values()), before_exam)
        with self.assertRaises(PermissionDenied):
            bill_exam(self.phieu.pk, self.cashier, recalculate=True)
        with self.assertRaises(PaymentError):
            create_invoice(self.appointment.pk, 1, self.cashier)
        self.client.post(reverse('invoice_create', args=[self.appointment.pk]), {'so_tien': '1'})
        self.invoice_row.refresh_from_db()
        self.assertEqual(self.invoice_row.so_tien, 100000)

    def test_generic_admin_cannot_accidentally_promote_cashier_or_link_doctor(self):
        form = AdminUserForm({'username': self.cashier.username, 'role': 'admin', 'is_active': 'on'}, instance=self.cashier)
        self.assertFalse(form.is_valid())
        form = AdminBacSiForm(instance=self.appointment.bac_si)
        self.assertFalse(form.fields['user'].queryset.filter(pk=self.cashier.pk).exists())

    def test_login_redirects_cashier_to_counter(self):
        self.client.logout()
        self.assertRedirects(self.client.post(reverse('login'), {'username': 'cashier', 'password': 'Cashier-test-73829!'}), reverse('payment_list'))

    def test_inactive_cashier_cannot_collect_even_with_stale_user_object(self):
        User.objects.filter(pk=self.cashier.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.cash(self.invoice_row)
        self.assertFalse(GiaoDichThanhToan.objects.exists())
        self.assertEqual(self.client.get(reverse('payment_list')).status_code, 302)

    def test_cash_endpoint_requires_login_permission_csrf_post_and_confirmation(self):
        url = reverse('payment_cash', args=[self.invoice_row.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        secure_client = Client(enforce_csrf_checks=True)
        secure_client.force_login(self.cashier)
        self.assertEqual(secure_client.post(url, {'received_full': 'yes', 'version': 1}).status_code, 403)
        self.client.post(url, {'version': 1})
        self.assertFalse(GiaoDichThanhToan.objects.exists())
        self.client.force_login(self.patient_user)
        self.assertEqual(self.client.post(url, {'received_full': 'yes', 'version': 1}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)

    def test_cash_endpoint_ignores_forged_amount_actor_hospital_and_method(self):
        self.client.post(reverse('payment_cash', args=[self.invoice_row.pk]), {'received_full': 'yes', 'version': 1,
            'so_tien': 1, 'nguoi_tao': self.admin.pk, 'benh_vien': 999999, 'phuong_thuc': 'VNPAY'})
        payment = GiaoDichThanhToan.objects.get()
        self.assertEqual((payment.so_tien, payment.nguoi_tao_id, payment.benh_vien_id, payment.phuong_thuc),
                         (100000, self.cashier.pk, self.hospital.pk, 'CASH'))


@override_settings(**CONFIG)
class CashPaymentTests(CashierFixtures, TestCase):
    def setUp(self):
        self.prepare_cashier()

    def test_cash_success_has_complete_receipt_and_locks_invoice(self):
        invoice = self.invoice()
        lines_before = list(ChiTietHoaDon.objects.values())
        attempt = self.cash(invoice)
        invoice.refresh_from_db()
        self.assertEqual((attempt.phuong_thuc, attempt.trang_thai, attempt.so_tien), ('CASH', 'success', 100000))
        self.assertEqual((attempt.nguoi_tao_id, attempt.benh_vien_id), (self.cashier.pk, self.hospital.pk))
        self.assertEqual(invoice.ngay_thanh_toan, attempt.hoan_tat_luc)
        self.assertEqual(invoice.trang_thai, 'paid')
        self.assertIsNone(attempt.het_han_luc)
        self.assertEqual(attempt.tmn_code, '')
        audit = LogHeThong.objects.get(model_name='GiaoDichThanhToan', object_id=attempt.pk)
        self.assertEqual(audit.du_lieu_moi['hoa_don_id'], invoice.pk)
        self.assertTrue(invoice_lock_reason(invoice))
        self.assertEqual(list(ChiTietHoaDon.objects.values()), lines_before)
        data = payload(self.phieu, [(self.service, 2, '')])
        context = form_context(self.appointment, self.phieu, data)
        with self.assertRaises(ExamServiceError):
            sync_services(self.phieu, self.doctor, context['services_formset'], context['services_revision'])

    def test_cash_duplicate_rejected_without_second_receipt(self):
        invoice = self.invoice()
        self.cash(invoice)
        with self.assertRaises(PaymentError):
            self.cash(invoice)
        self.assertEqual(GiaoDichThanhToan.objects.count(), 1)

    def test_pending_local_expiry_allows_cash_and_keeps_late_result_for_reconciliation(self):
        invoice = self.invoice()
        attempt, _ = start_payment(invoice.pk, self.cashier, '127.0.0.1')
        GiaoDichThanhToan.objects.filter(pk=attempt.pk).update(het_han_luc=timezone.now() - datetime.timedelta(days=1))
        cash = self.cash(invoice)
        self.assertEqual(cash.trang_thai, 'success')
        attempt.refresh_from_db()
        self.assertEqual(attempt.trang_thai, 'expired')
        self.assertEqual(process_ipn(callback(attempt, '99', '02'))[0], '99')
        invoice.refresh_from_db()
        self.assertTrue(invoice.can_doi_soat)
        self.assertEqual(GiaoDichThanhToan.objects.count(), 2)

    def test_reconciliation_blocks_cash(self):
        invoice = self.invoice()
        HoaDon.objects.filter(pk=invoice.pk).update(can_doi_soat=True)
        with self.assertRaises(PaymentError):
            self.cash(invoice)
        self.assertFalse(GiaoDichThanhToan.objects.exists())

    def test_verified_failure_can_switch_to_cash(self):
        invoice = self.invoice()
        attempt, _ = start_payment(invoice.pk, self.cashier, '127.0.0.1')
        self.assertEqual(process_ipn(callback(attempt, '51', '02'))[0], '00')
        self.cash(invoice)
        self.assertEqual(GiaoDichThanhToan.objects.filter(trang_thai='success').count(), 1)
        attempt.refresh_from_db()
        self.assertEqual(attempt.trang_thai, 'failed')

    def test_verified_cancel_or_expiry_can_switch_to_cash(self):
        for code in ['24', '11']:
            with self.subTest(code=code), transaction.atomic():
                # Roll back each scenario while retaining the fixture.
                invoice = self.invoice()
                attempt, _ = start_payment(invoice.pk, self.cashier, '127.0.0.1')
                self.assertEqual(process_ipn(callback(attempt, code, '02'))[0], '00')
                self.assertEqual(self.cash(invoice).trang_thai, 'success')
                transaction.set_rollback(True)

    def test_cash_success_blocks_vnpay(self):
        invoice = self.invoice()
        self.cash(invoice)
        with self.assertRaises(PaymentError):
            start_payment(invoice.pk, self.cashier, '127.0.0.1')
        self.assertEqual(GiaoDichThanhToan.objects.count(), 1)

    def test_vnpay_success_blocks_cash_and_duplicate_ipn_is_idempotent(self):
        invoice = self.invoice()
        attempt, _ = start_payment(invoice.pk, self.cashier, '127.0.0.1')
        self.assertEqual(process_ipn(callback(attempt))[0], '00')
        self.assertEqual(process_ipn(callback(attempt))[0], '02')
        with self.assertRaises(PaymentError):
            self.cash(invoice)
        self.assertEqual(GiaoDichThanhToan.objects.count(), 1)

    def test_late_conflicting_ipn_after_cash_freezes_without_second_success(self):
        invoice = self.invoice()
        attempt, _ = start_payment(invoice.pk, self.cashier, '127.0.0.1')
        process_ipn(callback(attempt, '51', '02'))
        cash = self.cash(invoice)
        self.assertEqual(process_ipn(callback(attempt))[0], '99')
        invoice.refresh_from_db()
        self.assertTrue(invoice.can_doi_soat)
        self.assertEqual(invoice.trang_thai, 'paid')
        self.assertEqual(list(invoice.giao_dichs.filter(trang_thai='success').values_list('pk', flat=True)), [cash.pk])

    def test_signed_callback_and_return_cannot_treat_cash_as_vnpay(self):
        attempt = self.cash(self.invoice())
        self.assertEqual(self.client.get(reverse('vnpay_ipn'), callback(attempt)).json()['RspCode'], '01')
        self.assertEqual(self.client.get(reverse('vnpay_return'), callback(attempt)).status_code, 400)

    def test_old_failed_return_after_cash_shows_invoice_already_paid(self):
        invoice = self.invoice()
        attempt, _ = start_payment(invoice.pk, self.cashier, '127.0.0.1')
        params = callback(attempt, '51', '02')
        process_ipn(params)
        self.cash(invoice)
        response = self.client.get(reverse('vnpay_return'), params)
        self.assertContains(response, 'Không thu thêm tiền')
        self.assertNotContains(response, 'Hóa đơn chưa thanh toán')

    def test_return_does_not_confirm_payment_and_cash_stays_blocked(self):
        invoice = self.invoice()
        attempt, _ = start_payment(invoice.pk, self.cashier, '127.0.0.1')
        self.assertEqual(self.client.get(reverse('vnpay_return'), callback(attempt)).status_code, 200)
        with self.assertRaises(PaymentError):
            self.cash(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.trang_thai, 'unpaid')

    def test_zero_invoice_creates_neither_cash_nor_vnpay(self):
        self.card()
        self.rule('100')
        invoice = self.invoice()
        self.assertEqual(invoice.so_tien, 0)
        with self.assertRaises(PaymentError):
            self.cash(invoice)
        with self.assertRaises(PaymentError):
            start_payment(invoice.pk, self.cashier, '127.0.0.1')
        self.assertFalse(GiaoDichThanhToan.objects.exists())

    def test_small_bhyt_balance_paid_exactly_in_cash(self):
        self.card()
        self.rule('99')
        invoice = self.invoice()
        self.assertEqual(invoice.so_tien, 1000)
        with self.assertRaises(PaymentError):
            start_payment(invoice.pk, self.cashier, '127.0.0.1')
        attempt = self.cash(invoice)
        invoice.refresh_from_db()
        self.assertEqual((attempt.so_tien, invoice.so_tien, invoice.bhyt_ho_tro), (1000, 1000, 99000))

    def test_one_vnd_is_not_rounded(self):
        self.service.don_gia = 10000
        self.service.save()
        self.card()
        self.rule('99.99')
        self.assertEqual(self.cash(self.invoice()).so_tien, 1)

    def test_cash_independent_of_vnpay_configuration(self):
        invoice = self.invoice()
        with override_settings(VNPAY_TMN_CODE='', VNPAY_HASH_SECRET='', VNPAY_PUBLIC_BASE_URL=''):
            self.assertEqual(self.cash(invoice).trang_thai, 'success')

    def test_stale_version_or_missing_confirmation_version_blocked(self):
        invoice = self.invoice()
        for version in [None, 'not-a-version', 0, invoice.phien_ban + 1]:
            with self.subTest(version=version), self.assertRaises(PaymentError):
                collect_cash(invoice.pk, self.cashier, expected_version=version)
        self.assertFalse(GiaoDichThanhToan.objects.exists())

    def test_incomplete_exam_or_corrupt_totals_block_cash(self):
        invoice = self.invoice()
        LichKham.objects.filter(pk=self.appointment.pk).update(trang_thai='dang')
        with self.assertRaises(PaymentError):
            self.cash(invoice)
        LichKham.objects.filter(pk=self.appointment.pk).update(trang_thai='xong')
        HoaDon.objects.filter(pk=invoice.pk).update(so_tien=200000, tong_chi_phi=200000)
        with self.assertRaises(PaymentError):
            self.cash(invoice)
        self.assertFalse(GiaoDichThanhToan.objects.exists())

    def test_audit_error_rolls_back_cash_and_invoice(self):
        invoice = self.invoice()
        with patch('core.payments.services.LogHeThong.objects.create', side_effect=DatabaseError('audit down')):
            with self.assertRaises(DatabaseError):
                self.cash(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.trang_thai, 'unpaid')
        self.assertFalse(GiaoDichThanhToan.objects.exists())

    def test_database_blocks_second_success_and_invalid_cash_metadata(self):
        invoice = self.invoice()
        self.cash(invoice)
        with self.assertRaises(IntegrityError), transaction.atomic():
            GiaoDichThanhToan.objects.create(hoa_don=invoice, so_tien=100000, phuong_thuc='VNPAY',
                tmn_code='TEST', trang_thai='success', het_han_luc=timezone.now(), hoan_tat_luc=timezone.now())
        cash = invoice.giao_dichs.get()
        for changes in [{'so_tien': 0}, {'trang_thai': 'pending', 'hoan_tat_luc': None},
                        {'tmn_code': 'NOT-CASH'}, {'benh_vien': None}]:
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                GiaoDichThanhToan.objects.filter(pk=cash.pk).update(**changes)


@override_settings(**CONFIG)
class CashConcurrencyTests(CashierFixtures, TransactionTestCase):
    def setUp(self):
        self.prepare_cashier()
        self.invoice_row = self.invoice()

    def workers(self, *functions):
        barrier = Barrier(len(functions))
        def worker(function):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return function()
                except (PaymentError, ExamServiceError):
                    return 'blocked'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(functions)) as pool:
            tasks = [pool.submit(worker, function) for function in functions]
            return [task.result(timeout=30) for task in tasks]

    def test_two_cashiers_collect_once(self):
        second = User.objects.create_user('second_cashier')
        QuanLyChiNhanh.objects.create(user=second, benh_vien=self.hospital)
        results = self.workers(lambda: self.cash(self.invoice_row).pk, lambda: self.cash(self.invoice_row, second).pk)
        self.assertEqual(results.count('blocked'), 1)
        self.assertEqual(GiaoDichThanhToan.objects.filter(trang_thai='success').count(), 1)
        self.assertEqual(LogHeThong.objects.filter(model_name='GiaoDichThanhToan').count(), 1)

    def test_cash_racing_vnpay_start_never_creates_two_attempts(self):
        results = self.workers(lambda: self.cash(self.invoice_row).pk,
            lambda: start_payment(self.invoice_row.pk, self.cashier, '127.0.0.1')[0].pk)
        self.assertEqual(results.count('blocked'), 1)
        self.assertEqual(GiaoDichThanhToan.objects.count(), 1)

    def test_success_ipn_racing_cash_keeps_only_vnpay_success(self):
        attempt, _ = start_payment(self.invoice_row.pk, self.cashier, '127.0.0.1')
        results = self.workers(lambda: process_ipn(callback(attempt))[0], lambda: self.cash(self.invoice_row).pk,
                               lambda: process_ipn(callback(attempt))[0])
        self.assertCountEqual(results, ['00', '02', 'blocked'])
        self.assertEqual(GiaoDichThanhToan.objects.count(), 1)
        self.invoice_row.refresh_from_db()
        self.assertEqual(self.invoice_row.trang_thai, 'paid')

    def test_late_conflicting_ipn_racing_cash_is_frozen_and_not_double_paid(self):
        attempt, _ = start_payment(self.invoice_row.pk, self.cashier, '127.0.0.1')
        process_ipn(callback(attempt, '51', '02'))
        self.workers(lambda: process_ipn(callback(attempt))[0], lambda: self.cash(self.invoice_row).pk)
        self.invoice_row.refresh_from_db()
        self.assertTrue(self.invoice_row.can_doi_soat)
        self.assertLessEqual(GiaoDichThanhToan.objects.filter(trang_thai='success').count(), 1)

    def test_cash_racing_doctor_revision_never_collects_a_changed_amount(self):
        data = payload(self.phieu, [(self.service, 2, '')])
        def edit():
            context = form_context(self.appointment, self.phieu, data)
            sync_services(self.phieu, self.doctor, context['services_formset'], context['services_revision'])
            return 'edited'
        cash_result, edit_result = self.workers(lambda: self.cash(self.invoice_row).so_tien, edit)
        self.invoice_row.refresh_from_db()
        if edit_result == 'edited':
            self.assertEqual(cash_result, 'blocked')
            self.assertEqual(self.invoice_row.so_tien, 200000)
            self.assertFalse(GiaoDichThanhToan.objects.exists())
        else:
            self.assertEqual(cash_result, 100000)
            self.assertEqual(self.invoice_row.so_tien, 100000)
            self.assertEqual(self.invoice_row.trang_thai, 'paid')


class CashierMigrationTests(TransactionTestCase):
    def test_0024_preserves_old_invoice_details_and_vnpay_values_without_assigning_users(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        previous = [('core', '0023_bhyt_mo_phong')]
        try:
            executor.migrate(previous)
            apps = executor.loader.project_state(previous).apps
            admin, _, _, appointment = payment_fixtures()
            OldInvoice = apps.get_model('core', 'HoaDon')
            OldPayment = apps.get_model('core', 'GiaoDichThanhToan')
            OldLine = apps.get_model('core', 'ChiTietHoaDon')
            service = apps.get_model('core', 'DanhMucDichVu').objects.create(ma_dich_vu='MIGRATE-CASH', ten='Khám cũ', nhom='Khám', don_gia=100000)
            performed = apps.get_model('core', 'DichVuThucHien').objects.create(phieu_kham_id=appointment.phieu_kham.pk,
                dich_vu_id=service.pk, nguoi_thuc_hien_id=appointment.bac_si_id)
            invoice = OldInvoice.objects.create(lich_kham_id=appointment.pk, so_tien=90000,
                phieu_kham_id=appointment.phieu_kham.pk, nguon='services', tong_chi_phi=100000, bhyt_ho_tro=10000,
                trang_thai='paid', ngay_thanh_toan=timezone.now(), nguoi_tao_id=admin.pk)
            OldLine.objects.create(hoa_don_id=invoice.pk, dich_vu_id=service.pk, dich_vu_thuc_hien_id=performed.pk,
                ma_dich_vu='MIGRATE-CASH', ten_dich_vu='Khám cũ', nhom_dich_vu='Khám', so_luong=1,
                don_gia=100000, thanh_tien=100000, ty_le_bhyt=10, bhyt_ho_tro=10000)
            OldPayment.objects.create(hoa_don_id=invoice.pk, so_tien=90000, phuong_thuc='VNPAY',
                tmn_code='OLD-CODE', trang_thai='success', het_han_luc=timezone.now(), hoan_tat_luc=timezone.now(),
                ma_giao_dich_vnpay='9876543', ma_phan_hoi='00', trang_thai_vnpay='00')
            old_models = [OldInvoice, OldPayment, OldLine, apps.get_model('auth', 'User')]
            before = [list(model.objects.order_by('pk').values()) for model in old_models]
            MigrationExecutor(connection).migrate(latest)
            self.assertEqual(before, [list(model.objects.order_by('pk').values()) for model in old_models])
            self.assertIsNone(GiaoDichThanhToan.objects.get().benh_vien_id)
            self.assertFalse(QuanLyChiNhanh.objects.exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
