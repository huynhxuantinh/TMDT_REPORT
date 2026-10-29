import datetime
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.billing.services import BillingError, bill_exam, validate_invoice_total
from core.exam_services.services import ExamServiceError, form_context, sync_services
from core.insurance.services import save_rule, support_amount
from core.models import (BaoHiemYTe, BenhNhan, BenhVien, ChiTietHoaDon, DichVuThucHien,
                         GiaoDichThanhToan, HoaDon, LichKham, LogHeThong, PhieuKham, QuyTacBHYT)
from core.payments.services import start_payment, process_ipn, create_invoice
from core.payments.vnpay import PaymentError
from core.test_exam_services import fixtures, payload
from core.test_billing import callback
from core.test_vnpay import CONFIG, fixtures as payment_fixtures


def rule_data(service, date, rate='80.00', **overrides):
    data = {'dich_vu': str(service.pk), 'ten': 'Quy tắc đồ án', 'ty_le': rate,
            'hieu_luc_tu': (date - datetime.timedelta(days=30)).isoformat(),
            'hieu_luc_den': '', 'dang_ap_dung': 'on'}
    data.update(overrides)
    return data


class InsuranceFixtures:
    def prepare(self):
        (self.admin, self.patient_user, self.appointment, self.doctor, self.other_doctor,
         self.service, self.other, self.inactive) = fixtures()
        self.phieu = PhieuKham.objects.get(lich_kham=self.appointment)
        self.date = self.appointment.ngay_kham
        self.hospital = self.appointment.bac_si.benh_vien
        BenhVien.objects.filter(pk=self.hospital.pk).update(co_bhyt=True)

    def card(self, start=None, end=None):
        card = BaoHiemYTe.objects.create(ma_bhyt='SIM-CARD-PRIVATE', ngay_cap=start or self.date - datetime.timedelta(days=10),
                                        ngay_het_han=end or self.date + datetime.timedelta(days=10))
        BenhNhan.objects.filter(pk=self.appointment.benh_nhan_id).update(bhyt=card)
        return card

    def rule(self, rate='80.00', service=None, **overrides):
        saved, form = save_rule(rule_data(service or self.service, self.date, rate, **overrides), self.admin)
        assert saved, form.errors
        return saved

    def record(self, service=None, quantity=1):
        return DichVuThucHien.objects.create(phieu_kham=self.phieu, dich_vu=service or self.service,
                                            so_luong=quantity, nguoi_thuc_hien=self.appointment.bac_si)

    def invoice(self, quantity=1):
        self.record(quantity=quantity)
        return bill_exam(self.phieu.pk, self.doctor)


@override_settings(**CONFIG)
class InsuranceBillingTests(InsuranceFixtures, TestCase):
    def setUp(self):
        self.prepare()
        self.client.force_login(self.doctor)

    def edit(self, items, **extra):
        data = payload(self.phieu, items)
        data.update(extra)
        return self.client.post(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]), data)

    def ipn(self, attempt, code='00', status='00'):
        return self.client.get(reverse('vnpay_ipn'), callback(attempt, code, status)).json()['RspCode']

    def test_no_card_gives_zero_support_and_reason(self):
        self.rule()
        invoice = self.invoice()
        self.assertEqual((invoice.tong_chi_phi, invoice.bhyt_ho_tro, invoice.so_tien), (100000, 0, 100000))
        self.assertIn('chưa có thẻ', invoice.bhyt_snapshot['ly_do'])

    def test_expired_card_at_exam_date_not_supported(self):
        self.card(start=self.date - datetime.timedelta(days=30), end=self.date - datetime.timedelta(days=1))
        self.rule()
        invoice = self.invoice()
        self.assertEqual(invoice.bhyt_ho_tro, 0)
        self.assertIn('hết hạn', invoice.bhyt_snapshot['ly_do'])

    def test_future_card_not_supported(self):
        self.card(start=self.date + datetime.timedelta(days=1))
        self.rule()
        invoice = self.invoice()
        self.assertEqual(invoice.bhyt_ho_tro, 0)
        self.assertIn('chưa có hiệu lực', invoice.bhyt_snapshot['ly_do'])

    def test_hospital_without_bhyt_not_supported(self):
        self.card()
        self.rule()
        BenhVien.objects.filter(pk=self.hospital.pk).update(co_bhyt=False)
        invoice = self.invoice()
        self.assertEqual(invoice.bhyt_ho_tro, 0)
        self.assertIn('Bệnh viện', invoice.bhyt_snapshot['ly_do'])

    def test_service_without_rule_has_zero_support(self):
        self.card()
        invoice = self.invoice()
        self.assertEqual(invoice.bhyt_ho_tro, 0)
        self.assertIn('chưa có quy tắc', invoice.chi_tiet_hien_hanh.get().bhyt_snapshot['ly_do'])

    def test_disabled_expired_and_future_rules_are_not_applied(self):
        self.card()
        rule = self.rule()
        self.record()
        for changes, reason in [({'dang_ap_dung': False}, 'ngừng'),
                                ({'dang_ap_dung': True, 'hieu_luc_den': self.date - datetime.timedelta(days=1)}, 'hiệu lực'),
                                ({'hieu_luc_den': None, 'hieu_luc_tu': self.date + datetime.timedelta(days=1)}, 'hiệu lực')]:
            with self.subTest(changes=changes):
                QuyTacBHYT.objects.filter(pk=rule.pk).update(**changes)
                invoice = bill_exam(self.phieu.pk, self.doctor, recalculate=True)
                self.assertEqual(invoice.bhyt_ho_tro, 0)
                self.assertIn(reason, invoice.chi_tiet_hien_hanh.get().bhyt_snapshot['ly_do'])

    def test_partial_support_snapshot_and_exact_net_payment(self):
        self.card()
        rule = self.rule('80.00')
        invoice = self.invoice(2)
        line = invoice.chi_tiet_hien_hanh.get()
        self.assertEqual((invoice.tong_chi_phi, invoice.bhyt_ho_tro, invoice.so_tien), (200000, 160000, 40000))
        self.assertEqual((line.ty_le_bhyt, line.bhyt_ho_tro, line.benh_nhan_tra), (80, 160000, 40000))
        self.assertEqual(line.bhyt_snapshot['quy_tac']['id'], rule.pk)
        self.assertEqual(invoice.trang_thai, 'unpaid')
        validate_invoice_total(invoice)
        attempt, url = start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.assertEqual(attempt.so_tien, 40000)
        self.assertEqual(parse_qs(urlsplit(url).query)['vnp_Amount'], ['4000000'])

    def test_mixed_services_sum_support_per_line(self):
        self.card()
        self.rule('80.00')
        self.record(quantity=2)
        self.record(self.other, 3)
        invoice = bill_exam(self.phieu.pk, self.doctor)
        self.assertEqual((invoice.tong_chi_phi, invoice.bhyt_ho_tro, invoice.so_tien), (350000, 160000, 190000))
        self.assertEqual(sum(line.bhyt_ho_tro for line in invoice.chi_tiet_hien_hanh), invoice.bhyt_ho_tro)

    def test_full_support_auto_settles_and_cannot_start_zero_payment(self):
        self.card()
        self.rule('100.00')
        invoice = self.invoice()
        self.assertEqual((invoice.so_tien, invoice.trang_thai, invoice.hoan_tat_bhyt), (0, 'paid', True))
        self.assertIsNotNone(invoice.ngay_thanh_toan)
        self.assertFalse(GiaoDichThanhToan.objects.exists())
        validate_invoice_total(invoice)
        for _ in range(2):
            with self.assertRaises(PaymentError):
                start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.assertFalse(GiaoDichThanhToan.objects.exists())
        self.assertContains(self.edit([(self.service, 2, '')]), 'đã hoàn tất nhờ BHYT MÔ PHỎNG')

    def test_completed_exam_automatically_applies_insurance(self):
        self.card()
        self.rule('100.00')
        self.phieu.delete()
        LichKham.objects.filter(pk=self.appointment.pk).update(trang_thai='dang')
        response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), payload(None, [(self.service, 1, '')]), follow=True)
        self.assertContains(response, 'Hóa đơn 0đ đã tự hoàn tất')
        self.assertTrue(HoaDon.objects.get().hoan_tat_bhyt)
        self.assertEqual(ChiTietHoaDon.objects.count(), 1)
        self.assertFalse(GiaoDichThanhToan.objects.exists())

    def test_duplicate_completion_or_creation_does_not_resettle_zero_invoice(self):
        self.card()
        self.rule('100.00')
        invoice = self.invoice()
        before = HoaDon.objects.values().get()
        self.assertEqual(bill_exam(self.phieu.pk, self.doctor).pk, invoice.pk)
        self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), payload(self.phieu, [(self.service, 1, '')]))
        self.assertEqual(HoaDon.objects.values().get(), before)
        self.assertEqual(LogHeThong.objects.filter(model_name='HoaDon').count(), 1)

    def test_rounding_uses_decimal_half_up_per_line(self):
        self.assertEqual(support_amount(Decimal('5001'), Decimal('50')), Decimal('2501'))
        self.card()
        self.rule('33.33')
        self.service.don_gia = Decimal('10001')
        self.service.save()
        invoice = self.invoice()
        self.assertEqual((invoice.bhyt_ho_tro, invoice.so_tien), (3333, 6668))

    def test_date_boundaries_use_exam_date_inclusive(self):
        self.card(start=self.date, end=self.date)
        self.rule('80', hieu_luc_tu=self.date.isoformat(), hieu_luc_den=self.date.isoformat())
        with patch('django.utils.timezone.localdate', return_value=self.date + datetime.timedelta(days=100)):
            invoice = self.invoice()
        self.assertEqual(invoice.bhyt_ho_tro, 80000)

    def test_edit_recalculates_support_and_preserves_all_old_snapshots(self):
        self.card()
        self.rule('80')
        invoice = self.invoice()
        old_lines = list(invoice.chi_tiets.values())
        response = self.edit([(self.service, 3, '')])
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual((invoice.phien_ban, invoice.tong_chi_phi, invoice.bhyt_ho_tro, invoice.so_tien), (2, 300000, 240000, 60000))
        self.assertEqual(list(invoice.chi_tiets.filter(phien_ban=1).values()), old_lines)
        log = LogHeThong.objects.filter(model_name='HoaDon', hanh_dong='update').get()
        self.assertEqual(log.du_lieu_cu['bhyt_ho_tro'], '80000')
        self.assertEqual(log.du_lieu_moi['bhyt_ho_tro'], '240000')

    def test_rule_change_does_not_touch_invoice_but_explicit_edit_uses_new_rule(self):
        self.card()
        rule = self.rule('80')
        invoice = self.invoice()
        before = HoaDon.objects.values().get(), list(ChiTietHoaDon.objects.values())
        saved, form = save_rule(rule_data(self.service, self.date, '50'), self.admin, rule.pk)
        self.assertIsNotNone(saved, form.errors)
        self.assertEqual((HoaDon.objects.values().get(), list(ChiTietHoaDon.objects.values())), before)
        self.edit([(self.service, 2, '')])
        invoice.refresh_from_db()
        self.assertEqual((invoice.bhyt_ho_tro, invoice.so_tien), (100000, 100000))
        self.assertEqual(invoice.chi_tiet_hien_hanh.get().bhyt_snapshot['quy_tac']['phien_ban'], 2)

    def test_unpaid_invoice_can_be_revised_to_zero(self):
        self.card()
        rule = self.rule('80')
        invoice = self.invoice()
        save_rule(rule_data(self.service, self.date, '100'), self.admin, rule.pk)
        self.assertEqual(self.edit([(self.service, 2, '')]).status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual((invoice.so_tien, invoice.trang_thai, invoice.hoan_tat_bhyt, invoice.phien_ban), (0, 'paid', True, 2))
        self.assertFalse(GiaoDichThanhToan.objects.exists())

    def test_pending_attempt_expired_locally_allows_revision_to_zero(self):
        self.card()
        rule = self.rule('80')
        invoice = self.invoice()
        attempt, _ = start_payment(invoice.pk, self.admin, '127.0.0.1')
        # Local expiry alone no longer releases the invoice: the late-IPN window
        # must be over before the amount can change.
        GiaoDichThanhToan.objects.filter(pk=attempt.pk).update(
            het_han_luc=timezone.now() - datetime.timedelta(minutes=settings.VNPAY_IPN_GRACE_MINUTES + 1))
        save_rule(rule_data(self.service, self.date, '100'), self.admin, rule.pk)
        self.assertEqual(self.edit([(self.service, 2, '')]).status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual((invoice.so_tien, invoice.hoan_tat_bhyt, invoice.phien_ban), (0, True, 2))

    def test_reconciliation_blocks_revision_to_zero(self):
        self.card()
        rule = self.rule('80')
        invoice = self.invoice()
        HoaDon.objects.filter(pk=invoice.pk).update(can_doi_soat=True)
        save_rule(rule_data(self.service, self.date, '100'), self.admin, rule.pk)
        self.assertContains(self.edit([(self.service, 2, '')]), 'cần đối soát')
        invoice.refresh_from_db()
        self.assertEqual(invoice.so_tien, 20000)

    def test_failed_attempt_allows_zero_settlement_but_late_conflict_freezes(self):
        self.card()
        rule = self.rule('80')
        invoice = self.invoice()
        attempt, _ = start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.assertEqual(self.ipn(attempt, '51', '02'), '00')
        save_rule(rule_data(self.service, self.date, '100'), self.admin, rule.pk)
        self.edit([(self.service, 2, '')])
        invoice.refresh_from_db()
        self.assertTrue(invoice.hoan_tat_bhyt)
        self.assertEqual(self.ipn(attempt, '51', '02'), '02')
        self.assertEqual(self.ipn(attempt), '99')
        invoice.refresh_from_db()
        self.assertTrue(invoice.can_doi_soat)
        self.assertEqual(invoice.so_tien, 0)
        self.assertEqual(GiaoDichThanhToan.objects.get().trang_thai, 'failed')

    def test_valid_net_ipn_duplicate_ipn_and_paid_lock(self):
        self.card()
        self.rule('80')
        invoice = self.invoice()
        attempt, _ = start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.assertEqual(self.ipn(attempt), '00')
        before = HoaDon.objects.values().get()
        self.assertEqual(self.ipn(attempt), '02')
        self.assertContains(self.edit([(self.service, 2, '')]), 'đã thanh toán')
        self.assertEqual(HoaDon.objects.values().get(), before)
        self.assertFalse(HoaDon.objects.get().hoan_tat_bhyt)

    def test_failed_payment_retry_uses_same_net_amount(self):
        self.card()
        self.rule('80')
        invoice = self.invoice()
        attempt, _ = start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.ipn(attempt, '51', '02')
        retry, _ = start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.assertEqual(retry.so_tien, 20000)

    def test_tampered_frontend_support_and_payment_amount_ignored(self):
        self.card()
        self.rule('80')
        response = self.edit([(self.service, 1, '')], bhyt_ho_tro='100000', ty_le_bhyt='100', so_tien='0')
        self.assertEqual(response.status_code, 302)
        invoice = HoaDon.objects.get()
        self.assertEqual(invoice.so_tien, 20000)
        self.client.force_login(self.admin)
        response = self.client.post(reverse('payment_start', args=[invoice.pk]), {'so_tien': '1'})
        self.assertEqual(parse_qs(urlsplit(response.url).query)['vnp_Amount'], ['2000000'])

    def test_below_gateway_minimum_is_preserved_not_rounded_or_sent(self):
        self.card()
        self.rule('99.99')
        invoice = self.invoice()
        self.assertEqual((invoice.so_tien, invoice.trang_thai), (10, 'unpaid'))
        with self.assertRaises(PaymentError):
            start_payment(invoice.pk, self.admin, '127.0.0.1')
        self.assertFalse(GiaoDichThanhToan.objects.exists())
        self.client.force_login(self.admin)
        response = self.client.get(reverse('payment_detail', args=[self.appointment.pk]))
        self.assertContains(response, 'dưới 5.000 VND')
        self.assertNotContains(response, '>Thanh toán VNPAY</button>')

    def test_legacy_invoice_is_unchanged_by_rule_or_card_updates(self):
        self.card()
        invoice = create_invoice(self.appointment.pk, 123000, self.admin)
        before = HoaDon.objects.values().get()
        self.rule('100')
        self.assertEqual(bill_exam(self.phieu.pk, self.admin).pk, invoice.pk)
        self.assertEqual(HoaDon.objects.values().get(), before)
        self.assertIsNone(invoice.tong_chi_phi)

    def test_snapshot_does_not_log_card_number_or_clinical_notes(self):
        self.card()
        self.rule()
        invoice = self.invoice()
        audit = LogHeThong.objects.filter(model_name='HoaDon').get()
        for snapshot in [invoice.bhyt_snapshot, audit.du_lieu_moi, invoice.chi_tiet_hien_hanh.get().bhyt_snapshot]:
            self.assertNotIn('SIM-CARD-PRIVATE', str(snapshot))
            self.assertNotIn('chan_doan', str(snapshot))

    def test_zero_settlement_audit_failure_rolls_back_everything(self):
        self.card()
        self.rule('100')
        self.record()
        with patch('core.billing.services.audit_invoice', side_effect=DatabaseError('audit down')):
            with self.assertRaises(DatabaseError):
                bill_exam(self.phieu.pk, self.doctor)
        self.assertFalse(HoaDon.objects.exists())
        self.assertFalse(ChiTietHoaDon.objects.exists())
        self.assertFalse(GiaoDichThanhToan.objects.exists())

    def test_database_support_bounds_and_zero_state_constraints(self):
        self.card()
        self.rule('80')
        invoice = self.invoice()
        line = invoice.chi_tiet_hien_hanh.get()
        for values in [{'bhyt_ho_tro': -1}, {'bhyt_ho_tro': 100001}, {'ty_le_bhyt': 101}]:
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                ChiTietHoaDon.objects.filter(pk=line.pk).update(**values)
        for values in [{'so_tien': 0}, {'bhyt_ho_tro': 200000}, {'tong_chi_phi': None, 'bhyt_ho_tro': 0, 'so_tien': 1}]:
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                HoaDon.objects.filter(pk=invoice.pk).update(**values)

    def test_zero_and_partial_invoice_ui_explicitly_labels_simulation(self):
        self.card()
        rule = self.rule('80')
        invoice = self.invoice()
        self.client.force_login(self.admin)
        response = self.client.get(reverse('payment_detail', args=[self.appointment.pk]))
        for text in ['BHYT MÔ PHỎNG', '80.000 VND', '20.000 VND', '100.000 VND', 'không phải kết quả giám định']:
            self.assertContains(response, text)
        save_rule(rule_data(self.service, self.date, '100'), self.admin, rule.pk)
        bill_exam(self.phieu.pk, self.doctor, recalculate=True)
        response = self.client.get(reverse('payment_detail', args=[self.appointment.pk]))
        self.assertContains(response, 'Đã hoàn tất')
        self.assertContains(response, 'không tạo giao dịch VNPAY')
        self.assertNotContains(response, '>Thanh toán VNPAY</button>')


class InsuranceAdminTests(InsuranceFixtures, TestCase):
    def setUp(self):
        self.prepare()
        self.client.force_login(self.admin)

    def test_admin_create_edit_disable_history_and_dashboard_link(self):
        response = self.client.post(reverse('bhyt_rule_create'), rule_data(self.service, self.date), follow=True)
        self.assertContains(response, 'BHYT MÔ PHỎNG')
        rule = QuyTacBHYT.objects.get()
        self.assertEqual(rule.phien_ban, 1)
        response = self.client.post(reverse('bhyt_rule_edit', args=[rule.pk]), rule_data(self.service, self.date, '55.50', dang_ap_dung=''), follow=True)
        rule.refresh_from_db()
        self.assertEqual((rule.ty_le, rule.phien_ban, rule.dang_ap_dung), (Decimal('55.50'), 2, False))
        self.assertEqual(LogHeThong.objects.filter(model_name='QuyTacBHYT').count(), 2)
        self.assertContains(response, '55.50', html=False)
        self.assertContains(self.client.get(reverse('custom_admin_dashboard')), reverse('bhyt_rule_list'))

    def test_doctor_patient_anonymous_cannot_configure_rules(self):
        for user in [self.doctor, self.patient_user]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('bhyt_rule_list')).status_code, 403)
            self.assertEqual(self.client.post(reverse('bhyt_rule_create'), rule_data(self.service, self.date)).status_code, 403)
            with self.assertRaises(PermissionDenied):
                save_rule(rule_data(self.service, self.date), user)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('bhyt_rule_list')).status_code, 302)
        self.assertFalse(QuyTacBHYT.objects.exists())

    def test_invalid_rates_and_dates_rejected(self):
        for rate in ['-1', '100.01', 'NaN', 'Infinity', '1.234', 'abc', '']:
            with self.subTest(rate=rate):
                saved, form = save_rule(rule_data(self.service, self.date, rate), self.admin)
                self.assertIsNone(saved)
                self.assertTrue(form.errors)
        data = rule_data(self.service, self.date, hieu_luc_den=(self.date - datetime.timedelta(days=50)).isoformat())
        self.assertIsNone(save_rule(data, self.admin)[0])
        self.assertFalse(QuyTacBHYT.objects.exists())

    def test_one_rule_per_service_and_immutable_service_binding(self):
        rule = self.rule()
        self.assertIsNone(save_rule(rule_data(self.service, self.date), self.admin)[0])
        saved, form = save_rule(rule_data(self.other, self.date, '50'), self.admin, rule.pk)
        self.assertIsNotNone(saved, form.errors)
        self.assertEqual(saved.dich_vu_id, self.service.pk)
        self.assertEqual(QuyTacBHYT.objects.count(), 1)

    def test_noop_save_does_not_increment_version(self):
        rule = self.rule()
        save_rule(rule_data(self.service, self.date), self.admin, rule.pk)
        rule.refresh_from_db()
        self.assertEqual(rule.phien_ban, 1)
        self.assertEqual(LogHeThong.objects.filter(model_name='QuyTacBHYT').count(), 1)

    def test_rule_audit_failure_rolls_back_config(self):
        with patch('core.insurance.services.audit_rule', side_effect=DatabaseError('audit down')):
            with self.assertRaises(DatabaseError):
                self.rule()
        self.assertFalse(QuyTacBHYT.objects.exists())

    def test_csrf_and_get_requests_cannot_change_config(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(reverse('bhyt_rule_create'), rule_data(self.service, self.date)).status_code, 403)
        self.assertEqual(self.client.get(reverse('bhyt_rule_create'), rule_data(self.service, self.date)).status_code, 200)
        self.assertFalse(QuyTacBHYT.objects.exists())

    def test_search_and_history_escape_html(self):
        self.rule(ten='<script>alert(1)</script>')
        response = self.client.get(reverse('bhyt_rule_list'), {'q': self.service.ma_dich_vu})
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        response = self.client.get(reverse('bhyt_rule_list'), {'q': 'does-not-exist'})
        self.assertContains(response, 'Chưa có quy tắc')

    def test_cannot_hard_delete_rules_and_database_enforces_limits(self):
        rule = self.rule()
        with self.assertRaises(ProtectedError), transaction.atomic():
            rule.delete()
        for values in [{'ty_le': -1}, {'ty_le': 101}, {'phien_ban': 0}, {'hieu_luc_den': self.date - datetime.timedelta(days=99)}]:
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                QuyTacBHYT.objects.filter(pk=rule.pk).update(**values)


@override_settings(**CONFIG)
class InsuranceConcurrencyTests(InsuranceFixtures, TransactionTestCase):
    def setUp(self):
        self.prepare()
        self.card()

    def workers(self, *functions):
        def isolated(function):
            close_old_connections()
            try:
                return function()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(functions)) as pool:
            futures = [pool.submit(isolated, function) for function in functions]
            return [future.result(timeout=25) for future in futures]

    def test_concurrent_zero_creation_settles_once(self):
        self.rule('100')
        self.record()
        barrier = Barrier(2)
        def create():
            barrier.wait(timeout=10)
            return bill_exam(self.phieu.pk, self.doctor).pk
        ids = self.workers(create, create)
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(HoaDon.objects.count(), 1)
        self.assertEqual(LogHeThong.objects.filter(model_name='HoaDon').count(), 1)
        self.assertFalse(GiaoDichThanhToan.objects.exists())

    def test_payment_racing_zero_revision_never_creates_zero_attempt(self):
        rule = self.rule('80')
        invoice = self.invoice()
        save_rule(rule_data(self.service, self.date, '100'), self.admin, rule.pk)
        barrier = Barrier(2)
        data = payload(self.phieu, [(self.service, 2, '')])
        def pay():
            barrier.wait(timeout=10)
            try:
                return start_payment(invoice.pk, self.admin, '127.0.0.1')[0].so_tien
            except PaymentError:
                return 'blocked'
        def edit():
            context = form_context(self.appointment, self.phieu, data)
            barrier.wait(timeout=10)
            try:
                sync_services(self.phieu, self.doctor, context['services_formset'], context['services_revision'])
                return 'zero'
            except ExamServiceError:
                return 'blocked'
        payment_result, edit_result = self.workers(pay, edit)
        invoice.refresh_from_db()
        if edit_result == 'zero':
            self.assertEqual(payment_result, 'blocked')
            self.assertTrue(invoice.hoan_tat_bhyt)
            self.assertFalse(GiaoDichThanhToan.objects.exists())
        else:
            self.assertEqual(payment_result, Decimal('20000'))
            self.assertEqual(invoice.so_tien, 20000)
            self.assertEqual(GiaoDichThanhToan.objects.get().trang_thai, 'pending')

    def test_rule_edit_waits_for_billing_snapshot_then_leaves_invoice_unchanged(self):
        rule = self.rule('80')
        self.record()
        locked, trying = Event(), Event()
        def invoice_worker():
            with transaction.atomic():
                invoice = bill_exam(self.phieu.pk, self.doctor)
                locked.set()
                if not trying.wait(timeout=10):
                    raise RuntimeError('Rule edit did not start')
                return invoice.pk
        def rule_worker():
            if not locked.wait(timeout=10):
                raise RuntimeError('Billing did not start')
            trying.set()
            return save_rule(rule_data(self.service, self.date, '100'), self.admin, rule.pk)[0].ty_le
        invoice_id, rate = self.workers(invoice_worker, rule_worker)
        self.assertEqual(rate, 100)
        invoice = HoaDon.objects.get(pk=invoice_id)
        self.assertEqual((invoice.so_tien, invoice.bhyt_ho_tro), (20000, 80000))
        self.assertEqual(invoice.chi_tiet_hien_hanh.get().ty_le_bhyt, 80)


class InsuranceMigrationTests(TransactionTestCase):
    def test_migration_preserves_existing_paid_invoice_payment_and_price_lines(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        target = [('core', '0022_hoa_don_dich_vu')]
        try:
            executor.migrate(target)
            apps = executor.loader.project_state(target).apps
            OldInvoice = apps.get_model('core', 'HoaDon')
            OldPayment = apps.get_model('core', 'GiaoDichThanhToan')
            OldLine = apps.get_model('core', 'ChiTietHoaDon')
            admin, _, _, appointment = payment_fixtures()
            phieu = PhieuKham.objects.get(lich_kham=appointment)
            service = apps.get_model('core', 'DanhMucDichVu').objects.create(ma_dich_vu='OLD', ten='Cũ', nhom='Khám', don_gia=100000)
            performed = apps.get_model('core', 'DichVuThucHien').objects.create(phieu_kham_id=phieu.pk, dich_vu_id=service.pk, nguoi_thuc_hien_id=appointment.bac_si_id)
            invoice = OldInvoice.objects.create(lich_kham_id=appointment.pk, phieu_kham_id=phieu.pk, nguon='services', so_tien=100000,
                                                trang_thai='paid', ngay_thanh_toan=timezone.now(), nguoi_tao_id=admin.pk)
            OldLine.objects.create(hoa_don_id=invoice.pk, dich_vu_id=service.pk, dich_vu_thuc_hien_id=performed.pk,
                                   ma_dich_vu='OLD', ten_dich_vu='Cũ', nhom_dich_vu='Khám', so_luong=1, don_gia=100000, thanh_tien=100000)
            OldPayment.objects.create(hoa_don_id=invoice.pk, so_tien=100000, tmn_code='TEST', ma_tham_chieu='task05-old',
                                      trang_thai='success', het_han_luc=timezone.now(), hoan_tat_luc=timezone.now())
            before = [list(model.objects.values()) for model in (OldInvoice, OldPayment, OldLine)]
            MigrationExecutor(connection).migrate([('core', '0023_bhyt_mo_phong')])
            self.assertEqual([list(model.objects.values()) for model in (OldInvoice, OldPayment, OldLine)], before)
            current = HoaDon.objects.get(pk=invoice.pk)
            self.assertEqual((current.tong_chi_phi, current.bhyt_ho_tro, current.hoan_tat_bhyt), (None, 0, False))
            validate_invoice_total(current)
            self.assertFalse(QuyTacBHYT.objects.exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
