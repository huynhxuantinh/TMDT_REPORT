"""P1 regressions: real HTTP authorization, immutable audit, clinical consistency."""
import datetime
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from django.contrib import admin
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import DatabaseError, transaction
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from core.billing.services import bill_exam, invoice_lock_reason
from core.models import (BacSi, BenhNhan, BenhVien, DichVuThucHien, GiaoDichThanhToan,
                         HoaDon, Khoa, LichKham, LichSuKhamBenh, LogHeThong, LogLichKham,
                         PhieuKham, PhieuKhamHinhAnh)
from core.payments import services, state, vnpay
from core.test_exam_services import fixtures
from core.test_vnpay import CONFIG, query_response


class ClinicalFixture(TestCase):
    def setUp(self):
        (self.admin, self.patient, self.appointment, self.doctor, self.other_doctor,
         self.service, self.other_service, _) = fixtures()
        self.exam = PhieuKham.objects.get(lich_kham=self.appointment)


class AuditProtectionTests(ClinicalFixture):
    def test_admin_and_staff_cannot_mutate_logs_even_by_direct_requests(self):
        system = LogHeThong.objects.create(model_name='P1', object_id=1, hanh_dong='update', ghi_chu='ORIGINAL')
        appointment_log = LogLichKham.objects.get(lich_kham=self.appointment)
        staff = User.objects.create_user('audit-staff', is_staff=True)
        for user in (self.admin, staff):
            self.client.force_login(user)
            for key, record in (('log-he-thong', system), ('log-lich-kham', appointment_log)):
                with self.subTest(user=user.username, key=key):
                    listing = self.client.get(reverse('custom_admin_model_list', args=[key]))
                    self.assertEqual(listing.status_code, 200)
                    self.assertNotContains(listing, reverse('custom_admin_model_create', args=[key]))
                    self.assertNotContains(listing, reverse('custom_admin_model_delete', args=[key, record.pk]))
                    for route, args in (
                        ('custom_admin_model_create', [key]),
                        ('custom_admin_model_edit', [key, record.pk]),
                        ('custom_admin_model_delete', [key, record.pk]),
                    ):
                        url = reverse(route, args=args)
                        self.assertEqual(self.client.get(url).status_code, 403)
                        self.assertEqual(self.client.post(url, {'ghi_chu': 'TAMPERED'}).status_code, 403)
                    self.assertTrue(type(record).objects.filter(pk=record.pk).exists())
        system.refresh_from_db()
        self.assertEqual(system.ghi_chu, 'ORIGINAL')

    def test_django_admin_disables_add_edit_delete_and_bulk_delete(self):
        request = RequestFactory().get('/')
        request.user = self.admin
        for model in (LogHeThong, LogLichKham):
            controller = admin.site._registry[model]
            self.assertTrue(controller.has_view_permission(request))
            self.assertFalse(controller.has_add_permission(request))
            self.assertFalse(controller.has_change_permission(request))
            self.assertFalse(controller.has_delete_permission(request))
            self.assertNotIn('delete_selected', controller.get_actions(request))


class PrivateImageTests(ClinicalFixture):
    def setUp(self):
        super().setUp()
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.roots = override_settings(MEDIA_ROOT=Path(self.directory.name) / 'public',
                                      PRIVATE_MEDIA_ROOT=Path(self.directory.name) / 'private')
        self.roots.enable()
        self.addCleanup(self.roots.disable)
        stream = BytesIO()
        Image.new('RGB', (2, 2), 'white').save(stream, 'PNG')
        self.content = stream.getvalue()
        self.image = PhieuKhamHinhAnh.objects.create(phieu_kham=self.exam,
            hinh_anh=SimpleUploadedFile('exam.png', self.content, content_type='image/png'))

    def test_private_storage_and_each_request_rechecks_ownership(self):
        from django.conf import settings
        url = self.image.hinh_anh.url
        self.assertTrue(url.startswith('/benh-an/anh/'))
        self.assertFalse((Path(settings.MEDIA_ROOT) / self.image.hinh_anh.name).exists())
        self.assertEqual(self.client.get(url).status_code, 302)
        stranger = User.objects.create_user('other-patient')
        BenhNhan.objects.create(user=stranger, ho_ten='Khác', ngay_sinh=datetime.date(2001, 1, 1),
            gioi_tinh='nam', so_dien_thoai='0901999999', dia_chi='Test')
        for user in (stranger, self.other_doctor, self.admin):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 404)
        for user in (self.patient, self.doctor):
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response['Cache-Control'])
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(b''.join(response.streaming_content), self.content)
        self.appointment.bac_si = self.other_doctor.bac_si
        self.appointment.save(update_fields=['bac_si'])
        self.client.force_login(self.doctor)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_old_public_url_is_blocked_even_in_debug(self):
        from django.conf import settings
        public_file = Path(settings.MEDIA_ROOT) / self.image.hinh_anh.name
        public_file.parent.mkdir(parents=True)
        public_file.write_bytes(self.content)
        for debug in (True, False):
            with override_settings(DEBUG=debug):
                self.assertEqual(self.client.get('/media/' + self.image.hinh_anh.name).status_code, 404)

    def test_legacy_move_verifies_bytes_is_idempotent_and_keeps_db_name(self):
        from django.conf import settings
        public_file = Path(settings.MEDIA_ROOT) / self.image.hinh_anh.name
        public_file.parent.mkdir(parents=True)
        public_file.write_bytes(self.content)
        self.image.hinh_anh.storage.delete(self.image.hinh_anh.name)
        before = self.image.hinh_anh.name
        call_command('privatize_exam_images', stdout=StringIO())
        self.assertTrue(public_file.exists())
        for _ in range(2):
            call_command('privatize_exam_images', apply=True, stdout=StringIO())
        self.assertFalse(public_file.exists())
        self.image.refresh_from_db()
        self.assertEqual(self.image.hinh_anh.name, before)
        with self.image.hinh_anh.open('rb') as stream:
            self.assertEqual(stream.read(), self.content)


class ClinicalConsistencyTests(ClinicalFixture):
    def test_doctor_edit_updates_patient_history_and_retains_previous_values(self):
        self.client.force_login(self.doctor)
        data = {'trieu_chung': 'Triệu chứng mới', 'chan_doan': 'Chẩn đoán mới', 'huong_dieu_tri': 'Điều trị mới'}
        response = self.client.post(reverse('bac_si_phieu_kham_edit', args=[self.exam.pk]), data)
        self.assertEqual(response.status_code, 302)
        history = LichSuKhamBenh.objects.get(phieu_kham=self.exam)
        self.assertEqual(history.chan_doan, data['chan_doan'])
        log = LogHeThong.objects.get(model_name='PhieuKham', object_id=self.exam.pk)
        self.assertEqual(log.du_lieu_cu['chan_doan'], 'Test')
        self.assertEqual(log.du_lieu_moi['chan_doan'], data['chan_doan'])
        self.assertEqual(log.nguoi_thuc_hien, self.doctor)
        for user, route, args in (
            (self.patient, 'medical_history', []),
            (self.doctor, 'bac_si_patient_detail', [self.appointment.benh_nhan_id]),
        ):
            self.client.force_login(user)
            self.assertContains(self.client.get(reverse(route, args=args)), data['chan_doan'])
        self.client.force_login(self.doctor)
        self.client.post(reverse('bac_si_phieu_kham_edit', args=[self.exam.pk]), data)
        self.assertEqual(LogHeThong.objects.filter(model_name='PhieuKham', object_id=self.exam.pk).count(), 1)

    def test_partial_save_syncs_only_persisted_fields(self):
        self.exam.chan_doan = 'CHƯA LƯU'
        self.exam.trieu_chung = 'ĐÃ LƯU'
        self.exam.save(update_fields=['trieu_chung'])
        history = LichSuKhamBenh.objects.get(phieu_kham=self.exam)
        self.assertEqual(history.chan_doan, 'Test')
        self.assertEqual(history.trieu_chung, 'ĐÃ LƯU')

    def test_audit_failure_rolls_back_exam_and_history(self):
        self.exam.chan_doan = 'Không được ghi'
        with self.assertRaises(DatabaseError):
            with patch.object(LogHeThong.objects, 'create', side_effect=DatabaseError('audit failed')):
                self.exam.save()
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.chan_doan, 'Test')
        self.assertEqual(LichSuKhamBenh.objects.get(phieu_kham=self.exam).chan_doan, 'Test')

    def test_history_does_not_leak_another_doctors_exam_in_same_or_other_hospital(self):
        other = self.other_doctor.bac_si
        appointment = LichKham.objects.create(bac_si=other, benh_nhan=self.appointment.benh_nhan,
            ngay_kham=timezone.localdate(), gio_kham=datetime.time(15), trang_thai='xong')
        restricted = PhieuKham.objects.create(lich_kham=appointment, benh_nhan=appointment.benh_nhan,
            trieu_chung='RESTRICTED SYMPTOM', chan_doan='RESTRICTED DIAGNOSIS', huong_dieu_tri='RESTRICTED TREATMENT')
        self.client.force_login(self.doctor)
        url = reverse('bac_si_patient_detail', args=[appointment.benh_nhan_id])
        for different_hospital in (False, True):
            if different_hospital:
                source = other.benh_vien
                source.pk = None
                source.ten = 'Cơ sở khác'
                source.save()
                department = Khoa.objects.create(ten='Khoa khác', benh_vien=source)
                third = BacSi.objects.create(ho_ten='Bác sĩ cơ sở khác', chuyen_khoa='Nội',
                    khoa=department, benh_vien=source, so_dien_thoai='0901999988')
                third_appointment = LichKham.objects.create(bac_si=third, benh_nhan=appointment.benh_nhan,
                    ngay_kham=timezone.localdate(), gio_kham=datetime.time(16), trang_thai='xong')
                PhieuKham.objects.create(lich_kham=third_appointment, benh_nhan=appointment.benh_nhan,
                    trieu_chung='RESTRICTED OTHER SITE', chan_doan='RESTRICTED OTHER SITE', huong_dieu_tri='Test')
            response = self.client.get(url)
            self.assertNotContains(response, 'RESTRICTED')
            self.assertEqual(list(response.context['lich_su'].values_list('phieu_kham_id', flat=True)), [self.exam.pk])
            self.assertEqual(self.client.get(reverse('phieu_kham_detail', args=[restricted.pk])).status_code, 404)
        self.client.force_login(self.patient)
        self.assertContains(self.client.get(reverse('phieu_kham_detail', args=[restricted.pk])), 'RESTRICTED DIAGNOSIS')

    def test_cancelled_appointment_alone_does_not_authorize_patient_record(self):
        self.client.force_login(self.other_doctor)
        LichKham.objects.create(bac_si=self.other_doctor.bac_si, benh_nhan=self.appointment.benh_nhan,
            ngay_kham=timezone.localdate(), gio_kham=datetime.time(15), trang_thai='huy')
        response = self.client.get(reverse('bac_si_patient_detail', args=[self.appointment.benh_nhan_id]))
        self.assertEqual(response.status_code, 302)

    def test_doctor_and_patient_see_same_services_and_frozen_invoice_prices(self):
        DichVuThucHien.objects.create(phieu_kham=self.exam, dich_vu=self.service, so_luong=2,
                                      nguoi_thuc_hien=self.appointment.bac_si)
        invoice = bill_exam(self.exam.pk, self.admin)
        self.service.don_gia = 900000
        self.service.save(update_fields=['don_gia'])
        contexts = []
        for user in (self.doctor, self.patient):
            self.client.force_login(user)
            response = self.client.get(reverse('phieu_kham_detail', args=[self.exam.pk]))
            self.assertContains(response, self.service.ten)
            self.assertNotContains(response, 'Chưa ghi nhận dịch vụ.')
            self.assertEqual(response.context['billing_invoice'].pk, invoice.pk)
            contexts.append([(r.display_price, r.so_luong, r.line_total) for r in response.context['performed_services']])
        self.assertEqual(contexts[0], contexts[1])
        self.assertEqual(contexts[0], [(100000, 2, 200000)])


@override_settings(**CONFIG)
class UncertainPaymentTests(ClinicalFixture):
    def setUp(self):
        super().setUp()
        self.invoice = services.create_invoice(self.appointment.pk, 100000, self.admin)
        self.attempt, _ = services.start_payment(self.invoice.pk, self.admin, '127.0.0.1')
        GiaoDichThanhToan.objects.filter(pk=self.attempt.pk).update(het_han_luc=timezone.now() - datetime.timedelta(days=2))
        self.attempt.refresh_from_db()

    def test_overdue_and_legacy_timeout_both_block_all_money_and_service_changes(self):
        self.client.force_login(self.admin)
        for status in ('pending', 'timeout'):
            GiaoDichThanhToan.objects.filter(pk=self.attempt.pk).update(
                trang_thai=status, hoan_tat_luc=timezone.now() if status == 'timeout' else None)
            self.attempt.refresh_from_db()
            self.assertTrue(state.blocks_new_charge(self.attempt, timezone.now()))
            self.assertTrue(invoice_lock_reason(self.invoice))
            detail = self.client.get(reverse('payment_detail', args=[self.appointment.pk]))
            self.assertFalse(detail.context['can_start'])
            self.assertFalse(detail.context['can_cash'])
            self.assertFalse(detail.context['can_edit_services'])
            monitor = self.client.get(reverse('cashier_monitor_detail', args=[self.appointment.pk]))
            self.assertTrue(monitor.context['can_resolve_reconciliation'])
            for _ in range(3):
                self.client.post(reverse('payment_start', args=[self.invoice.pk]))
                with self.assertRaises(vnpay.PaymentError):
                    services.collect_cash(self.invoice.pk, self.admin, expected_version=self.invoice.phien_ban)
            self.assertEqual(self.invoice.giao_dichs.count(), 1)

    def test_incomplete_query_does_not_release_overdue_invoice_without_flag(self):
        with patch.object(vnpay, 'http_post_json', return_value=query_response(self.attempt, '01')):
            with self.assertRaises(vnpay.PaymentError):
                services.resolve_reconciliation(self.invoice.pk, self.admin,
                    reason='Kiểm tra giao dịch quá hạn', reference=self.attempt.ma_tham_chieu)
        self.assertTrue(invoice_lock_reason(self.invoice))

    def test_reconciliation_rejects_attempt_list_changed_during_network_query(self):
        services.expire_pending_payment(self.attempt.pk)
        self.invoice.refresh_from_db()
        checks = services._query_all_attempts
        with patch.object(vnpay, 'http_post_json', return_value=query_response(self.attempt, '02')):
            evidence = checks(self.invoice, self.attempt.ma_tham_chieu, '127.0.0.1')
        GiaoDichThanhToan.objects.create(hoa_don=self.invoice, so_tien=self.invoice.so_tien,
            tmn_code=CONFIG['VNPAY_TMN_CODE'], het_han_luc=timezone.now() + datetime.timedelta(minutes=10))
        with self.assertRaises(vnpay.PaymentError):
            services._apply_reconciliation(self.invoice.pk, self.admin, 'Kiểm tra kết quả', self.attempt.ma_tham_chieu, evidence)
        self.invoice.refresh_from_db()
        self.assertTrue(self.invoice.can_doi_soat)

    def test_late_failure_cannot_clear_a_conflict_on_another_attempt(self):
        older = GiaoDichThanhToan.objects.create(hoa_don=self.invoice, so_tien=self.invoice.so_tien,
            tmn_code=CONFIG['VNPAY_TMN_CODE'], trang_thai='failed',
            hoan_tat_luc=timezone.now(), ma_giao_dich_vnpay='111', het_han_luc=timezone.now())
        services.expire_pending_payment(self.attempt.pk)
        base = {'vnp_TmnCode': CONFIG['VNPAY_TMN_CODE'], 'vnp_Amount': '10000000'}
        self.assertEqual(services.process_ipn({**base, 'vnp_TxnRef': older.ma_tham_chieu,
            'vnp_ResponseCode': '00', 'vnp_TransactionStatus': '00', 'vnp_TransactionNo': '222'})[0], '99')
        self.assertEqual(services.process_ipn({**base, 'vnp_TxnRef': self.attempt.ma_tham_chieu,
            'vnp_ResponseCode': '24', 'vnp_TransactionStatus': '02', 'vnp_TransactionNo': '333'})[0], '00')
        self.invoice.refresh_from_db()
        self.assertTrue(self.invoice.can_doi_soat)
        with self.assertRaises(vnpay.PaymentError):
            services.collect_cash(self.invoice.pk, self.admin, expected_version=self.invoice.phien_ban)
