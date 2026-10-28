"""TASK 04 tests use PostgreSQL transactions and signed simulated VNPAY callbacks."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.billing.services import BillingError, bill_exam, is_consultation
from core.exam_services.services import ExamServiceError, form_context, sync_services
from core.models import (ChiTietHoaDon, DanhMucDichVu, DichVuThucHien, GiaoDichThanhToan,
                         HoaDon, LichKham, LichSuKhamBenh, LogHeThong, PhieuKham, ThongBao)
from core.payments import services as payments
from core.payments.vnpay import PaymentError
from core.test_exam_services import fixtures, payload
from core.test_vnpay import CONFIG, fixtures as payment_fixtures, sign_callback


def callback(attempt, code='00', status='00', number='12345678'):
    return sign_callback({'vnp_TmnCode': CONFIG['VNPAY_TMN_CODE'], 'vnp_TxnRef': attempt.ma_tham_chieu,
                          'vnp_Amount': str(int(attempt.so_tien) * 100), 'vnp_ResponseCode': code,
                          'vnp_TransactionStatus': status, 'vnp_TransactionNo': number})


@override_settings(**CONFIG)
class BillingTests(TestCase):
    def setUp(self):
        (self.admin, self.patient, self.appointment, self.doctor, self.other_doctor,
         self.service, self.other, self.inactive) = fixtures()
        self.phieu = PhieuKham.objects.get(lich_kham=self.appointment)
        self.client.force_login(self.doctor)

    def record(self, service=None, quantity=1):
        return DichVuThucHien.objects.create(phieu_kham=self.phieu, dich_vu=service or self.service,
                                            so_luong=quantity, nguoi_thuc_hien=self.appointment.bac_si)

    def invoice(self, quantity=1):
        self.record(quantity=quantity)
        return bill_exam(self.phieu.pk, self.doctor)

    def edit(self, items, **extra):
        data = payload(self.phieu, items)
        data.update(extra)
        return self.client.post(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]), data)

    def new_exam(self, items=None, data=None):
        self.phieu.delete()
        LichKham.objects.filter(pk=self.appointment.pk).update(trang_thai='dang')
        return self.client.post(reverse('doctor_exam', args=[self.appointment.pk]),
                                payload(None, items or []) if data is None else data)

    def ipn(self, attempt, code='00', status='00'):
        return self.client.get(reverse('vnpay_ipn'), callback(attempt, code, status)).json()['RspCode']

    def test_complete_one_service_creates_linked_invoice_and_snapshot(self):
        response = self.new_exam([(self.service, 2, '')])
        self.assertEqual(response.status_code, 302)
        invoice = HoaDon.objects.get()
        line = ChiTietHoaDon.objects.get()
        self.assertEqual(invoice.so_tien, Decimal('200000'))
        self.assertEqual(invoice.nguon, 'services')
        self.assertEqual(invoice.lich_kham_id, self.appointment.pk)
        self.assertEqual(invoice.phieu_kham_id, PhieuKham.objects.get().pk)
        self.assertEqual(line.dich_vu_thuc_hien.phieu_kham_id, invoice.phieu_kham_id)
        self.assertEqual((line.so_luong, line.don_gia, line.thanh_tien), (2, Decimal('100000'), Decimal('200000')))
        self.assertEqual(invoice.nguoi_tao_id, self.doctor.pk)
        self.assertContains(self.client.get(reverse('bac_si_home')), 'Đã tạo hóa đơn')

    def test_multiple_services_and_consultation_fee_count_once(self):
        self.assertTrue(is_consultation('  KHÁM BỆNH '))
        self.assertTrue(is_consultation(self.service.nhom))
        self.assertFalse(is_consultation(self.other.nhom))
        self.new_exam([(self.service, 1, ''), (self.other, 3, '')])
        invoice = HoaDon.objects.get()
        self.assertEqual(invoice.so_tien, Decimal('250000'))
        self.assertEqual(invoice.chi_tiet_hien_hanh.count(), 2)

    def test_no_unselected_consultation_fee_is_added(self):
        self.new_exam([(self.other, 2, '')])
        self.assertEqual(HoaDon.objects.get().so_tien, Decimal('100000'))
        self.assertEqual(ChiTietHoaDon.objects.get().dich_vu_id, self.other.pk)

    def test_missing_services_blocks_completion_and_signals(self):
        before_notifications = ThongBao.objects.count()
        response = self.new_exam([])
        self.assertContains(response, 'Chưa có dịch vụ thực hiện')
        self.assertFalse(HoaDon.objects.exists())
        self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())
        self.assertFalse(LichSuKhamBenh.objects.exists())
        self.assertEqual(ThongBao.objects.count(), before_notifications)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, 'dang')

    def test_omitting_formset_cannot_bypass_required_services(self):
        response = self.new_exam(data={'trieu_chung': 'T', 'chan_doan': 'C', 'huong_dieu_tri': 'H'})
        self.assertContains(response, 'Chưa có dịch vụ thực hiện')
        self.assertFalse(HoaDon.objects.exists())
        self.assertFalse(PhieuKham.objects.exists())

    def test_zero_below_limit_and_overflow_totals_rejected_without_partial_save(self):
        self.record()
        for price, quantity in [('0', 1), ('4999', 1), ('9999999999', 2)]:
            with self.subTest(price=price, quantity=quantity):
                DanhMucDichVu.objects.filter(pk=self.service.pk).update(don_gia=Decimal(price))
                DichVuThucHien.objects.filter(phieu_kham=self.phieu).update(so_luong=quantity)
                with self.assertRaises(BillingError):
                    bill_exam(self.phieu.pk, self.doctor)
                self.assertFalse(HoaDon.objects.exists())
                self.assertFalse(ChiTietHoaDon.objects.exists())

    def test_zero_price_line_allowed_with_valid_positive_total(self):
        self.record()
        self.record(service=self.inactive)
        invoice = bill_exam(self.phieu.pk, self.doctor)
        self.assertEqual(invoice.so_tien, Decimal('100000'))
        self.assertEqual(invoice.chi_tiet_hien_hanh.get(dich_vu=self.inactive).thanh_tien, 0)

    def test_price_and_name_snapshots_survive_catalog_changes(self):
        invoice = self.invoice()
        old = list(invoice.chi_tiet_hien_hanh.values())
        self.service.don_gia = Decimal('999000')
        self.service.ten = 'Tên mới'
        self.service.save()
        self.assertEqual(list(invoice.chi_tiet_hien_hanh.values()), old)
        self.client.force_login(self.admin)
        response = self.client.get(reverse('payment_detail', args=[self.appointment.pk]))
        self.assertContains(response, '100.000 VND')
        self.assertContains(response, 'Khám chuyên khoa')
        self.assertNotContains(response, 'Tên mới')
        attempt, url = payments.start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.assertEqual(parse_qs(urlsplit(url).query)['vnp_Amount'], ['10000000'])
        self.assertEqual(attempt.so_tien, Decimal('100000'))

    def test_repeated_completion_and_invoice_creation_are_idempotent(self):
        data = payload(None, [(self.service, 1, '')])
        self.new_exam(data=data)
        self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), data)
        invoice = HoaDon.objects.get()
        self.assertEqual(bill_exam(invoice.phieu_kham_id, self.doctor).pk, invoice.pk)
        self.assertEqual(ChiTietHoaDon.objects.count(), 1)
        self.assertEqual(LogHeThong.objects.filter(model_name='HoaDon').count(), 1)

    def test_edit_updates_total_version_and_before_after_audit(self):
        invoice = self.invoice()
        response = self.edit([(self.service, 3, ''), (self.other, 2, '')])
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual((invoice.so_tien, invoice.phien_ban), (Decimal('400000'), 2))
        self.assertEqual(invoice.chi_tiet_hien_hanh.count(), 2)
        self.assertEqual(invoice.chi_tiets.filter(phien_ban=1).get().so_luong, 1)
        log = LogHeThong.objects.filter(model_name='HoaDon', hanh_dong='update').get()
        self.assertEqual(log.du_lieu_cu['so_tien'], '100000')
        self.assertEqual(log.du_lieu_moi['so_tien'], '400000')
        self.assertEqual(log.nguoi_thuc_hien_id, self.doctor.pk)
        self.assertNotIn('chan_doan', str(log.du_lieu_moi))

    def test_quantity_revision_preserves_old_price_new_service_uses_current_price(self):
        invoice = self.invoice()
        DanhMucDichVu.objects.filter(pk=self.service.pk).update(don_gia=Decimal('700000'))
        DanhMucDichVu.objects.filter(pk=self.other.pk).update(don_gia=Decimal('60000'))
        self.edit([(self.service, 2, ''), (self.other, 1, '')])
        invoice.refresh_from_db()
        self.assertEqual(invoice.so_tien, Decimal('260000'))
        self.assertEqual(invoice.chi_tiet_hien_hanh.get(dich_vu=self.service).don_gia, Decimal('100000'))

    def test_deleted_billed_service_is_archived_with_original_details_retained(self):
        invoice = self.invoice()
        old_line = invoice.chi_tiet_hien_hanh.get()
        self.edit([(self.service, 1, '', True), (self.other, 1, '')])
        invoice.refresh_from_db()
        self.assertEqual(invoice.so_tien, Decimal('50000'))
        old_line.dich_vu_thuc_hien.refresh_from_db()
        self.assertTrue(old_line.dich_vu_thuc_hien.da_huy)
        self.assertTrue(ChiTietHoaDon.objects.filter(pk=old_line.pk).exists())
        response = self.client.get(reverse('phieu_kham_detail', args=[self.phieu.pk]))
        self.assertContains(response, self.other.ten)
        self.assertNotContains(response, self.service.ten)
        with self.assertRaises(ProtectedError):
            old_line.dich_vu_thuc_hien.delete()
        with self.assertRaises(ProtectedError):
            old_line.delete()

    def test_readding_removed_service_creates_traceable_new_row_and_price(self):
        invoice = self.invoice()
        old_id = DichVuThucHien.objects.get().pk
        self.edit([(self.other, 1, '')])
        DanhMucDichVu.objects.filter(pk=self.service.pk).update(don_gia=Decimal('120000'))
        self.edit([(self.other, 1, ''), (self.service, 1, '')])
        invoice.refresh_from_db()
        line = invoice.chi_tiet_hien_hanh.get(dich_vu=self.service)
        self.assertNotEqual(line.dich_vu_thuc_hien_id, old_id)
        self.assertEqual(line.don_gia, Decimal('120000'))
        self.assertEqual(invoice.so_tien, Decimal('170000'))

    def test_cannot_delete_all_services_and_leave_zero_invoice(self):
        invoice = self.invoice()
        before = list(invoice.chi_tiets.values())
        response = self.edit([])
        self.assertContains(response, 'Chưa có dịch vụ thực hiện')
        invoice.refresh_from_db()
        self.assertEqual(invoice.phien_ban, 1)
        self.assertEqual(list(invoice.chi_tiets.values()), before)
        self.assertFalse(DichVuThucHien.objects.get().da_huy)

    def test_note_only_change_does_not_reprice_or_increment_invoice(self):
        invoice = self.invoice()
        response = self.edit([(self.service, 1, 'Ghi chú riêng tư')])
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.phien_ban, 1)
        self.assertEqual(LogHeThong.objects.filter(model_name='HoaDon').count(), 1)

    def test_pending_and_locally_expired_attempts_block_service_changes(self):
        invoice = self.invoice()
        attempt, _ = payments.start_payment(invoice.pk, self.admin, '127.0.0.1')
        GiaoDichThanhToan.objects.filter(pk=attempt.pk).update(het_han_luc=timezone.now())
        before = list(HoaDon.objects.values()), list(GiaoDichThanhToan.objects.values())
        self.assertContains(self.edit([(self.service, 2, '')]), 'VNPAY đang xử lý')
        self.assertEqual((list(HoaDon.objects.values()), list(GiaoDichThanhToan.objects.values())), before)

    def test_unknown_verified_result_keeps_invoice_locked(self):
        invoice = self.invoice()
        attempt, _ = payments.start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.assertEqual(self.ipn(attempt, '99', '02'), '99')
        self.assertContains(self.edit([(self.service, 2, '')]), 'VNPAY đang xử lý')
        with self.assertRaises(PaymentError):
            payments.start_payment(invoice.pk, self.admin, '127.0.0.1')

    def test_successful_and_duplicate_ipn_lock_invoice_without_duplicate_charge(self):
        invoice = self.invoice()
        attempt, _ = payments.start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.assertEqual(self.ipn(attempt), '00')
        before = list(HoaDon.objects.values()), list(ChiTietHoaDon.objects.values())
        self.assertEqual(self.ipn(attempt), '02')
        self.assertContains(self.edit([(self.service, 2, '')]), 'đã thanh toán')
        self.assertEqual((list(HoaDon.objects.values()), list(ChiTietHoaDon.objects.values())), before)
        with self.assertRaises(PaymentError):
            payments.start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.assertEqual(GiaoDichThanhToan.objects.count(), 1)

    def test_definitive_failure_keeps_amount_and_allows_retry(self):
        invoice = self.invoice()
        attempt, _ = payments.start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.assertEqual(self.ipn(attempt, '51', '02'), '00')
        retry, _ = payments.start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.assertEqual(retry.so_tien, Decimal('100000'))
        self.assertNotEqual(retry.ma_tham_chieu, attempt.ma_tham_chieu)

    def test_definitive_failure_allows_revision_and_retry_at_new_total(self):
        invoice = self.invoice()
        old, _ = payments.start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.ipn(old, '51', '02')
        self.assertEqual(self.edit([(self.service, 2, '')]).status_code, 302)
        self.assertEqual(self.ipn(old, '51', '02'), '02')
        new, _ = payments.start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.assertEqual(new.so_tien, Decimal('200000'))
        old.refresh_from_db()
        self.assertEqual((old.so_tien, old.trang_thai), (Decimal('100000'), 'failed'))
        self.assertEqual(self.ipn(new), '00')

    def test_conflicting_old_callback_after_revision_freezes_invoice(self):
        invoice = self.invoice()
        old, _ = payments.start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.ipn(old, '51', '02')
        self.edit([(self.service, 2, '')])
        self.assertEqual(self.ipn(old), '99')
        invoice.refresh_from_db()
        self.assertTrue(invoice.can_doi_soat)
        self.assertEqual(invoice.so_tien, Decimal('200000'))
        self.assertContains(self.edit([(self.service, 3, '')]), 'cần đối soát')
        with self.assertRaises(PaymentError):
            payments.start_payment(invoice.pk, self.admin, '127.0.0.1')

    def test_manual_legacy_invoice_is_not_converted_or_repriced(self):
        self.record()
        invoice = payments.create_invoice(self.appointment.pk, 777000, self.admin)
        before = HoaDon.objects.values().get()
        self.assertEqual(bill_exam(self.phieu.pk, self.admin).pk, invoice.pk)
        self.assertContains(self.edit([(self.service, 2, '')]), 'hóa đơn thử nghiệm cũ')
        self.assertEqual(HoaDon.objects.values().get(), before)
        self.assertFalse(ChiTietHoaDon.objects.exists())

    def test_collector_recovery_ignores_browser_amount_and_cannot_reprice(self):
        self.record(quantity=2)
        self.client.force_login(self.admin)
        url = reverse('invoice_create', args=[self.appointment.pk])
        self.assertEqual(self.client.post(url, {'so_tien': '1'}).status_code, 302)
        self.client.post(url, {'so_tien': '900000'})
        self.assertEqual(HoaDon.objects.get().so_tien, Decimal('200000'))
        self.assertEqual(ChiTietHoaDon.objects.count(), 1)
        response = self.client.get(reverse('payment_detail', args=[self.appointment.pk]))
        self.assertNotContains(response, 'name="so_tien"')
        self.assertContains(response, '200.000 VND')

    def test_collector_cannot_create_missing_invoice_without_services(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('invoice_create', args=[self.appointment.pk]), {'so_tien': '99999'}, follow=True)
        self.assertContains(response, 'Chưa có dịch vụ thực hiện')
        self.assertFalse(HoaDon.objects.exists())

    def test_nonowners_cannot_bill_or_revise_and_cannot_view_counter(self):
        invoice = self.invoice()
        for user in [self.patient, self.other_doctor]:
            with self.subTest(user=user.pk):
                with self.assertRaises(PermissionDenied):
                    bill_exam(self.phieu.pk, user, recalculate=True)
                self.client.force_login(user)
                self.assertEqual(self.client.get(reverse('payment_detail', args=[self.appointment.pk])).status_code, 403)
        with self.assertRaises(PermissionDenied):
            bill_exam(self.phieu.pk, self.admin, recalculate=True)
        invoice.refresh_from_db()
        self.assertEqual(invoice.phien_ban, 1)

    def test_unfinished_appointment_cannot_bill_or_pay(self):
        invoice = self.invoice()
        for state in ['cho', 'dang', 'huy']:
            with self.subTest(state=state):
                LichKham.objects.filter(pk=self.appointment.pk).update(trang_thai=state)
                with self.assertRaises(BillingError):
                    bill_exam(self.phieu.pk, self.doctor)
                with self.assertRaises(PaymentError):
                    payments.start_payment(invoice.pk, self.admin, '127.0.0.1')

    def test_billing_audit_failure_rolls_back_completion_and_all_signals(self):
        notifications = ThongBao.objects.count()
        with patch('core.billing.services.audit_invoice', side_effect=DatabaseError('audit down')):
            with self.assertRaises(DatabaseError):
                self.new_exam([(self.service, 1, '')])
        self.assertFalse(HoaDon.objects.exists())
        self.assertFalse(ChiTietHoaDon.objects.exists())
        self.assertFalse(DichVuThucHien.objects.exists())
        self.assertFalse(PhieuKham.objects.exists())
        self.assertFalse(LichSuKhamBenh.objects.exists())
        self.assertEqual(ThongBao.objects.count(), notifications)

    def test_revision_audit_failure_rolls_back_services_and_details(self):
        invoice = self.invoice()
        before = list(HoaDon.objects.values()), list(ChiTietHoaDon.objects.values()), list(DichVuThucHien.objects.values())
        with patch('core.billing.services.audit_invoice', side_effect=DatabaseError('audit down')):
            with self.assertRaises(DatabaseError):
                self.edit([(self.service, 3, '')])
        self.assertEqual((list(HoaDon.objects.values()), list(ChiTietHoaDon.objects.values()), list(DichVuThucHien.objects.values())), before)

    def test_invoice_line_database_constraints_and_immutable_save(self):
        invoice = self.invoice()
        line = invoice.chi_tiet_hien_hanh.get()
        for changes in [{'so_luong': 0}, {'don_gia': -1}, {'thanh_tien': 1}]:
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                ChiTietHoaDon.objects.filter(pk=line.pk).update(**changes)
        with self.assertRaises(ValidationError):
            line.save()
        with self.assertRaises(ProtectedError):
            invoice.delete()

    def test_inconsistent_database_total_blocks_payment(self):
        invoice = self.invoice()
        HoaDon.objects.filter(pk=invoice.pk).update(so_tien=200000, tong_chi_phi=200000)
        with self.assertRaises(PaymentError):
            payments.start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.assertFalse(GiaoDichThanhToan.objects.exists())

    def test_estimate_ui_and_read_only_invoice_display(self):
        invoice = self.invoice()
        response = self.client.get(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]))
        self.assertContains(response, 'Tổng chi phí dự kiến')
        self.assertContains(response, 'Thành tiền')
        self.assertContains(response, 'name="services-TOTAL_FORMS"')
        attempt, _ = payments.start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.ipn(attempt)
        response = self.client.get(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]))
        self.assertNotContains(response, 'name="services-TOTAL_FORMS"')
        self.assertContains(response, 'Giá đã chốt')


@override_settings(**CONFIG)
class BillingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin, _, self.appointment, self.doctor, _, self.service, _, _ = fixtures()
        self.phieu = PhieuKham.objects.get(lich_kham=self.appointment)

    def record(self):
        return DichVuThucHien.objects.create(phieu_kham=self.phieu, dich_vu=self.service,
                                            nguoi_thuc_hien=self.appointment.bac_si)

    def run_workers(self, *workers):
        def isolated(worker):
            close_old_connections()
            try:
                return worker()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            futures = [pool.submit(isolated, worker) for worker in workers]
            return [future.result(timeout=30) for future in futures]

    def test_simultaneous_bill_creation_returns_same_invoice(self):
        self.record()
        barrier = Barrier(2)
        def worker():
            barrier.wait(timeout=10)
            return bill_exam(self.phieu.pk, self.doctor).pk
        results = self.run_workers(worker, worker)
        self.assertEqual(results[0], results[1])
        self.assertEqual(HoaDon.objects.count(), 1)
        self.assertEqual(ChiTietHoaDon.objects.count(), 1)

    def test_simultaneous_completion_creates_one_exam_invoice_and_notification(self):
        self.phieu.delete()
        LichKham.objects.filter(pk=self.appointment.pk).update(trang_thai='dang')
        count = ThongBao.objects.filter(loai='phieu_kham').count()
        barrier = Barrier(2)
        data = payload(None, [(self.service, 1, '')])
        def worker():
            client = Client()
            client.force_login(self.doctor)
            barrier.wait(timeout=10)
            return client.post(reverse('doctor_exam', args=[self.appointment.pk]), data).status_code
        self.assertTrue(all(status in [200, 302] for status in self.run_workers(worker, worker)))
        self.assertEqual(PhieuKham.objects.count(), 1)
        self.assertEqual(HoaDon.objects.count(), 1)
        self.assertEqual(ChiTietHoaDon.objects.count(), 1)
        self.assertEqual(ThongBao.objects.filter(loai='phieu_kham').count(), count + 1)

    def test_payment_start_racing_revision_has_consistent_amount(self):
        self.record()
        invoice = bill_exam(self.phieu.pk, self.doctor)
        data = payload(self.phieu, [(self.service, 2, '')])
        barrier = Barrier(2)
        def pay():
            barrier.wait(timeout=10)
            return payments.start_payment(invoice.pk, self.admin, '127.0.0.1')[0].so_tien
        def edit():
            context = form_context(self.appointment, self.phieu, data)
            barrier.wait(timeout=10)
            try:
                sync_services(self.phieu, self.doctor, context['services_formset'], context['services_revision'])
                return 'edited'
            except ExamServiceError:
                return 'blocked'
        amount, outcome = self.run_workers(pay, edit)
        invoice.refresh_from_db()
        self.assertEqual(invoice.so_tien, amount)
        self.assertEqual(amount, Decimal('200000') if outcome == 'edited' else Decimal('100000'))
        self.assertEqual(sum(line.thanh_tien for line in invoice.chi_tiet_hien_hanh), amount)

    def test_revision_waiting_on_successful_ipn_is_blocked(self):
        self.record()
        invoice = bill_exam(self.phieu.pk, self.doctor)
        attempt, _ = payments.start_payment(invoice.pk, self.admin, '127.0.0.1')
        locked = Event()
        editing = Event()
        data = payload(self.phieu, [(self.service, 2, '')])
        def ipn():
            with transaction.atomic():
                HoaDon.objects.select_for_update().get(pk=invoice.pk)
                result = payments.process_ipn(callback(attempt))
                locked.set()
                if not editing.wait(timeout=10):
                    raise RuntimeError('Edit did not arrive')
                return result[0]
        def edit():
            context = form_context(self.appointment, self.phieu, data)
            if not locked.wait(timeout=10):
                raise RuntimeError('IPN did not arrive')
            editing.set()
            try:
                sync_services(self.phieu, self.doctor, context['services_formset'], context['services_revision'])
                return 'edited'
            except ExamServiceError:
                return 'blocked'
        self.assertEqual(self.run_workers(ipn, edit), ['00', 'blocked'])
        invoice.refresh_from_db()
        self.assertEqual((invoice.trang_thai, invoice.so_tien, invoice.phien_ban), ('paid', Decimal('100000'), 1))


class BillingMigrationTests(TransactionTestCase):
    def test_migration_preserves_paid_legacy_invoice_and_transaction(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        target = [('core', '0021_dich_vu_thuc_hien')]
        try:
            executor.migrate(target)
            old_apps = executor.loader.project_state(target).apps
            OldInvoice = old_apps.get_model('core', 'HoaDon')
            OldPayment = old_apps.get_model('core', 'GiaoDichThanhToan')
            admin, _, _, appointment = payment_fixtures()
            invoice = OldInvoice.objects.create(lich_kham_id=appointment.pk, so_tien=123000,
                                                trang_thai='paid', ngay_thanh_toan=timezone.now(), nguoi_tao_id=admin.pk)
            OldPayment.objects.create(hoa_don_id=invoice.pk, so_tien=123000, trang_thai='success',
                                      tmn_code='TESTCODE', ma_tham_chieu='migration-paid',
                                      het_han_luc=timezone.now(), hoan_tat_luc=timezone.now())
            before_invoice = OldInvoice.objects.values().get()
            before_payment = OldPayment.objects.values().get()
            MigrationExecutor(connection).migrate([('core', '0022_hoa_don_dich_vu')])
            self.assertEqual(OldInvoice.objects.values().get(), before_invoice)
            self.assertEqual(OldPayment.objects.values().get(), before_payment)
            InvoiceAt0022 = MigrationExecutor(connection).loader.project_state([('core', '0022_hoa_don_dich_vu')]).apps.get_model('core', 'HoaDon')
            current = InvoiceAt0022.objects.get(pk=invoice.pk)
            self.assertEqual((current.nguon, current.phien_ban, current.phieu_kham_id), ('manual', 1, None))
            self.assertFalse(ChiTietHoaDon.objects.exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
