from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from core.models import (BacSi, DanhMucDichVu, DichVuThucHien, GiaoDichThanhToan, HoaDon,
                         LichKham, LichSuKhamBenh, LogHeThong, PhieuKham, ThongBao)
from core.exam_services.services import ExamServiceError, form_context, revision, sync_services
from core.payments.services import create_invoice, start_payment
from core.test_vnpay import CONFIG, fixtures as payment_fixtures, sign_callback


def fixtures():
    admin, _, patient, appointment = payment_fixtures()
    doctor_user = User.objects.create_user('exam_doctor')
    doctor = appointment.bac_si
    doctor.user = doctor_user
    doctor.save()
    other_user = User.objects.create_user('other_exam_doctor')
    BacSi.objects.create(user=other_user, ho_ten='Bác sĩ khác', chuyen_khoa=doctor.chuyen_khoa,
                         khoa=doctor.khoa, benh_vien=doctor.benh_vien, so_dien_thoai='0901000099')
    service = DanhMucDichVu.objects.create(ma_dich_vu='KHAM', ten='Khám chuyên khoa', nhom='Khám', don_gia=100000)
    other = DanhMucDichVu.objects.create(ma_dich_vu='XN', ten='Xét nghiệm', nhom='Xét nghiệm', don_gia=50000)
    inactive = DanhMucDichVu.objects.create(ma_dich_vu='STOP', ten='Dịch vụ ngừng', nhom='Khám', don_gia=0, dang_ap_dung=False)
    return admin, patient, appointment, doctor_user, other_user, service, other, inactive


def payload(phieu, items, current=None):
    if current is None:
        current = list(DichVuThucHien.objects.filter(phieu_kham=phieu, da_huy=False)) if phieu else []
    data = {'trieu_chung': 'Triệu chứng thử', 'chan_doan': 'Chẩn đoán thử', 'huong_dieu_tri': 'Theo dõi',
            'services-TOTAL_FORMS': str(len(items)), 'services-INITIAL_FORMS': str(len(current)),
            'services_revision': revision(current)}
    for index, item in enumerate(items):
        service, quantity, note, *delete = item
        data.update({f'services-{index}-dich_vu': str(service.pk), f'services-{index}-so_luong': str(quantity),
                     f'services-{index}-ghi_chu': note})
        if delete and delete[0]:
            data[f'services-{index}-DELETE'] = 'on'
    return data


@override_settings(**CONFIG)
class DoctorServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        (cls.admin, cls.patient, cls.appointment, cls.doctor_user, cls.other_user,
         cls.service, cls.other_service, cls.inactive) = fixtures()
        cls.phieu = PhieuKham.objects.get(lich_kham=cls.appointment)

    def setUp(self):
        self.client.force_login(self.doctor_user)

    def add(self, service=None, quantity=1, note=''):
        return DichVuThucHien.objects.create(phieu_kham=self.phieu, dich_vu=service or self.service,
                                            so_luong=quantity, ghi_chu=note, nguoi_thuc_hien=self.appointment.bac_si)

    def edit(self, items, **extra):
        data = payload(self.phieu, items)
        data.update(extra)
        return self.client.post(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]), data)

    def prepare_new_exam(self, state='dang'):
        self.phieu.delete()
        LichKham.objects.filter(pk=self.appointment.pk).update(trang_thai=state)

    def test_owner_opens_existing_exam_with_services_and_catalog(self):
        self.add()
        response = self.client.get(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]))
        self.assertContains(response, 'DỊCH VỤ Y TẾ ĐÃ THỰC HIỆN')
        self.assertContains(response, self.service.ten)
        self.assertContains(response, 'service-search')
        self.assertNotContains(response, self.inactive.ten)
        self.assertEqual(response.context['services_formset'].initial_form_count(), 1)

    def test_open_new_exam_keeps_existing_start_behavior(self):
        self.prepare_new_exam('cho')
        response = self.client.get(reverse('doctor_exam', args=[self.appointment.pk]))
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, 'dang')
        self.assertFalse(response.context['services_lock_reason'])
        self.assertContains(response, 'name="trieu_chung"')
        self.assertContains(response, 'name="services-TOTAL_FORMS"')

    def test_add_service_to_completed_exam_without_invoice(self):
        response = self.edit([(self.service, 2, 'Ghi chú dịch vụ')])
        self.assertEqual(response.status_code, 302)
        row = DichVuThucHien.objects.get(phieu_kham=self.phieu)
        self.assertEqual(row.so_luong, 2)
        self.assertEqual(row.ghi_chu, 'Ghi chú dịch vụ')
        self.assertEqual(row.nguoi_thuc_hien_id, self.appointment.bac_si_id)
        log = LogHeThong.objects.get(model_name='DichVuThucHien')
        self.assertEqual(log.nguoi_thuc_hien_id, self.doctor_user.pk)
        self.assertEqual(log.du_lieu_moi['so_luong'], 2)
        self.assertNotIn('ghi_chu', log.du_lieu_moi)

    def test_complete_exam_saves_multiple_services_and_original_signals(self):
        self.prepare_new_exam()
        notifications = ThongBao.objects.filter(loai='phieu_kham').count()
        response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]),
                                    payload(None, [(self.service, 1, ''), (self.other_service, 3, 'Ghi chú')]))
        self.assertEqual(response.status_code, 302)
        phieu = PhieuKham.objects.get(lich_kham=self.appointment)
        self.assertEqual(phieu.dich_vu_thuc_hien.count(), 2)
        self.assertEqual(LichSuKhamBenh.objects.filter(phieu_kham=phieu).count(), 1)
        self.assertEqual(ThongBao.objects.filter(loai='phieu_kham').count(), notifications + 1)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, 'xong')
        self.assertEqual(HoaDon.objects.get(lich_kham=self.appointment).so_tien, Decimal('250000'))

    def test_update_quantity_and_note_creates_minimal_audit(self):
        row = self.add(quantity=1, note='Ghi chú cũ')
        self.edit([(self.service, 5, 'Nội dung riêng tư')])
        row.refresh_from_db()
        self.assertEqual(row.so_luong, 5)
        log = LogHeThong.objects.get(model_name='DichVuThucHien')
        self.assertEqual(log.du_lieu_cu['so_luong'], 1)
        self.assertEqual(log.du_lieu_moi['so_luong'], 5)
        self.assertNotIn('Nội dung riêng tư', str(log.du_lieu_moi) + log.ghi_chu)

    def test_delete_keeps_audit_and_catalog(self):
        row = self.add(quantity=2)
        self.add(service=self.other_service)
        data = payload(self.phieu, [(self.service, 2, '', True), (self.other_service, 1, '')])
        # JavaScript disables deleted-row inputs to avoid hidden browser validation.
        for field in ('dich_vu', 'so_luong', 'ghi_chu'):
            del data[f'services-0-{field}']
        response = self.client.post(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(DichVuThucHien.objects.filter(pk=row.pk).exists())
        log = LogHeThong.objects.get(model_name='DichVuThucHien')
        self.assertEqual(log.hanh_dong, 'delete')
        self.assertEqual(log.du_lieu_cu['so_luong'], 2)
        self.assertIsNone(log.du_lieu_moi)
        self.assertTrue(DanhMucDichVu.objects.filter(pk=self.service.pk).exists())

    def test_invalid_quantity_blocks_whole_exam_and_preserves_inputs(self):
        self.prepare_new_exam()
        for quantity in [0, -1, '1.5', 'abc', '2147483648']:
            with self.subTest(quantity=quantity):
                response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]),
                                            payload(None, [(self.service, quantity, 'Ghi chú giữ lại')]))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['services_formset'].errors)
                self.assertContains(response, 'Triệu chứng thử')
                self.assertContains(response, 'Ghi chú giữ lại')
                self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, 'dang')

    def test_inactive_service_cannot_be_added(self):
        response = self.edit([(self.inactive, 1, '')])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['services_formset'].errors)
        self.assertFalse(DichVuThucHien.objects.exists())

    def test_previously_recorded_inactive_service_can_be_retained_or_removed(self):
        self.add(service=self.inactive)
        self.add(service=self.other_service)
        response = self.edit([(self.inactive, 2, ''), (self.other_service, 1, '')])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DichVuThucHien.objects.get(dich_vu=self.inactive).so_luong, 2)
        self.edit([(self.inactive, 2, '', True), (self.other_service, 1, '')])
        self.assertFalse(DichVuThucHien.objects.filter(dich_vu=self.inactive, da_huy=False).exists())
        self.assertTrue(DichVuThucHien.objects.get(dich_vu=self.inactive).da_huy)

    def test_duplicate_service_rows_rejected(self):
        response = self.edit([(self.service, 1, ''), (self.service, 2, '')])
        self.assertContains(response, 'Mỗi dịch vụ chỉ chọn một lần')
        self.assertFalse(DichVuThucHien.objects.exists())

    def test_unknown_catalog_id_rejected(self):
        response = self.edit([(self.service, 1, '')], **{'services-0-dich_vu': '999999'})
        self.assertTrue(response.context['services_formset'].errors)
        self.assertFalse(DichVuThucHien.objects.exists())

    def test_patient_other_doctor_and_admin_cannot_edit_services(self):
        for user, expected in [(self.patient, 302), (self.other_user, 404), (self.admin, 302)]:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.edit([(self.service, 1, '')]).status_code, expected)
                context = form_context(self.appointment, self.phieu, payload(self.phieu, [(self.service, 1, '')]))
                with self.assertRaises(PermissionDenied):
                    sync_services(self.phieu, user, context['services_formset'], context['services_revision'])
        self.assertFalse(DichVuThucHien.objects.exists())

    def test_cancelled_appointment_cannot_add_services(self):
        LichKham.objects.filter(pk=self.appointment.pk).update(trang_thai='huy')
        response = self.edit([(self.service, 1, '')])
        self.assertContains(response, 'lịch đã hủy không được chỉnh sửa')
        self.assertFalse(DichVuThucHien.objects.exists())

    def test_not_started_direct_post_cannot_add_services(self):
        self.prepare_new_exam('cho')
        response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), payload(None, [(self.service, 1, '')]))
        self.assertContains(response, 'sau khi bắt đầu khám')
        self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())

    def test_unpaid_invoice_blocks_add_edit_delete_without_changing_money(self):
        self.add()
        create_invoice(self.appointment.pk, 230000, self.admin)
        before = list(HoaDon.objects.values())
        for items in [[(self.service, 3, '')], [(self.other_service, 1, '')], []]:
            with self.subTest(items=items):
                response = self.edit(items)
                self.assertContains(response, 'Đã có hóa đơn')
                self.assertEqual(DichVuThucHien.objects.get().so_luong, 1)
                self.assertEqual(list(HoaDon.objects.values()), before)

    def test_pending_and_paid_payments_remain_unchanged_when_edit_blocked(self):
        self.add()
        invoice = create_invoice(self.appointment.pk, 230000, self.admin)
        payment, _ = start_payment(invoice.pk, self.admin, '127.0.0.1')
        for status in ['pending', 'paid']:
            if status == 'paid':
                params = sign_callback({'vnp_TmnCode': CONFIG['VNPAY_TMN_CODE'], 'vnp_TxnRef': payment.ma_tham_chieu,
                                        'vnp_Amount': '23000000', 'vnp_ResponseCode': '00', 'vnp_TransactionStatus': '00',
                                        'vnp_TransactionNo': '12345678'})
                self.assertEqual(self.client.get(reverse('vnpay_ipn'), params).json()['RspCode'], '00')
            with self.subTest(status=status):
                invoices = list(HoaDon.objects.values())
                payments = list(GiaoDichThanhToan.objects.values())
                self.assertContains(self.edit([(self.service, 8, '')]), 'Đã có hóa đơn')
                self.assertEqual(list(HoaDon.objects.values()), invoices)
                self.assertEqual(list(GiaoDichThanhToan.objects.values()), payments)

    def test_locked_ui_is_read_only_and_clinical_legacy_edit_preserves_services(self):
        row = self.add()
        create_invoice(self.appointment.pk, 230000, self.admin)
        response = self.client.get(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]))
        self.assertNotContains(response, 'name="services-TOTAL_FORMS"')
        self.assertContains(response, 'name="trieu_chung"')
        self.assertContains(response, '100.000 VND')
        response = self.client.post(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]),
                                    {'trieu_chung': 'Cập nhật', 'chan_doan': 'Cập nhật', 'huong_dieu_tri': 'Cập nhật'})
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.so_luong, 1)

    def test_submitted_price_and_name_cannot_modify_catalog(self):
        self.edit([(self.service, 1, '')], **{'services-0-don_gia': '1', 'services-0-ten': 'Giá giả'})
        self.service.refresh_from_db()
        self.assertEqual(self.service.don_gia, Decimal('100000'))
        self.assertEqual(self.service.ten, 'Khám chuyên khoa')
        self.assertEqual(DichVuThucHien.objects.get().dich_vu_id, self.service.pk)

    def test_stale_form_is_rejected_instead_of_overwriting_new_rows(self):
        data = payload(self.phieu, [(self.service, 1, '')])
        self.add(service=self.other_service)
        response = self.client.post(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]), data)
        self.assertContains(response, 'Danh sách dịch vụ đã thay đổi')
        self.assertEqual(DichVuThucHien.objects.get().dich_vu_id, self.other_service.pk)

    def test_catalog_disabled_after_form_validation_is_rechecked(self):
        context = form_context(self.appointment, self.phieu, payload(self.phieu, [(self.service, 1, '')]))
        self.assertTrue(context['services_formset'].is_valid())
        DanhMucDichVu.objects.filter(pk=self.service.pk).update(dang_ap_dung=False)
        with self.assertRaises(ExamServiceError):
            sync_services(self.phieu, self.doctor_user, context['services_formset'], context['services_revision'])
        self.assertFalse(DichVuThucHien.objects.exists())

    def test_audit_failure_rolls_back_exam_services_and_signals(self):
        self.prepare_new_exam()
        notifications = ThongBao.objects.count()
        with patch('core.exam_services.services.audit_line', side_effect=DatabaseError('audit unavailable')):
            with self.assertRaises(DatabaseError):
                self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), payload(None, [(self.service, 1, '')]))
        self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())
        self.assertFalse(DichVuThucHien.objects.exists())
        self.assertFalse(LichSuKhamBenh.objects.filter(benh_nhan=self.appointment.benh_nhan).exists())
        self.assertEqual(ThongBao.objects.count(), notifications)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, 'dang')

    def test_missing_management_form_is_invalid_when_rows_are_posted(self):
        data = payload(self.phieu, [(self.service, 1, '')])
        del data['services-TOTAL_FORMS']
        response = self.client.post(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]), data)
        self.assertTrue(response.context['services_formset'].non_form_errors())
        self.assertFalse(DichVuThucHien.objects.exists())

    def test_invalid_image_rolls_back_new_exam_and_services(self):
        self.prepare_new_exam()
        data = payload(None, [(self.service, 1, '')])
        data['images'] = SimpleUploadedFile('invalid.txt', b'invalid image', content_type='text/plain')
        notifications = ThongBao.objects.count()
        response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), data)
        self.assertContains(response, 'Ảnh tải lên không hợp lệ')
        self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())
        self.assertFalse(DichVuThucHien.objects.exists())
        self.assertFalse(LogHeThong.objects.filter(model_name='DichVuThucHien').exists())
        self.assertEqual(ThongBao.objects.count(), notifications)

    def test_invalid_image_rolls_back_service_edit_and_clinical_changes(self):
        row = self.add(quantity=1)
        previous_diagnosis = self.phieu.chan_doan
        response = self.edit([(self.service, 4, '')],
                             new_images=SimpleUploadedFile('invalid.txt', b'invalid image', content_type='text/plain'))
        self.assertContains(response, 'Ảnh tải lên không hợp lệ')
        row.refresh_from_db()
        self.phieu.refresh_from_db()
        self.assertEqual(row.so_luong, 1)
        self.assertEqual(self.phieu.chan_doan, previous_diagnosis)
        self.assertFalse(LogHeThong.objects.filter(model_name='DichVuThucHien').exists())

    def test_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.doctor_user)
        response = client.post(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]), payload(self.phieu, [(self.service, 1, '')]))
        self.assertEqual(response.status_code, 403)

    def test_detail_displays_services_without_edit_controls(self):
        self.add(note='<script>bad()</script>')
        response = self.client.get(reverse('phieu_kham_detail', args=[self.phieu.pk]))
        self.assertContains(response, '100.000 VND')
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>bad()</script>')
        self.assertNotContains(response, 'name="services-TOTAL_FORMS"')

    def test_database_positive_quantity_and_unique_service_constraints(self):
        row = self.add()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.add()
        for quantity in [0, -1]:
            with self.subTest(quantity=quantity), self.assertRaises(IntegrityError), transaction.atomic():
                DichVuThucHien.objects.filter(pk=row.pk).update(so_luong=quantity)

    def test_referenced_exam_and_catalog_are_protected(self):
        self.add()
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.phieu.delete()
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.service.delete()


@override_settings(**CONFIG)
class DoctorServiceConcurrencyTests(TransactionTestCase):
    def setUp(self):
        (self.admin, _, self.appointment, self.doctor_user, _, self.service, _, _) = fixtures()
        self.phieu = PhieuKham.objects.get(lich_kham=self.appointment)

    def test_two_updates_from_same_revision_cannot_overwrite_each_other(self):
        barrier = Barrier(2)
        data = payload(self.phieu, [(self.service, 1, '')])
        def worker():
            close_old_connections()
            try:
                context = form_context(self.appointment, self.phieu, data)
                barrier.wait(timeout=10)
                try:
                    sync_services(self.phieu, self.doctor_user, context['services_formset'], context['services_revision'])
                    return 'saved'
                except ExamServiceError:
                    return 'stale'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            self.assertCountEqual([f.result(timeout=20) for f in futures], ['saved', 'stale'])
        self.assertEqual(DichVuThucHien.objects.count(), 1)
        self.assertEqual(LogHeThong.objects.filter(model_name='DichVuThucHien').count(), 1)

    def test_invoice_committed_while_edit_waits_blocks_service_changes(self):
        locked = Event()
        trying_edit = Event()
        data = payload(self.phieu, [(self.service, 1, '')])
        def invoice_worker():
            close_old_connections()
            try:
                with transaction.atomic():
                    LichKham.objects.select_for_update().get(pk=self.appointment.pk)
                    create_invoice(self.appointment.pk, 100000, self.admin)
                    locked.set()
                    if not trying_edit.wait(timeout=10):
                        raise RuntimeError('Edit thread did not arrive')
            finally:
                close_old_connections()
        def edit_worker():
            close_old_connections()
            try:
                context = form_context(self.appointment, self.phieu, data)
                if not locked.wait(timeout=10):
                    raise RuntimeError('Invoice thread did not arrive')
                trying_edit.set()
                try:
                    sync_services(self.phieu, self.doctor_user, context['services_formset'], context['services_revision'])
                    return 'saved'
                except ExamServiceError:
                    return 'blocked'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            invoice_future = pool.submit(invoice_worker)
            edit_future = pool.submit(edit_worker)
            invoice_future.result(timeout=20)
            self.assertEqual(edit_future.result(timeout=20), 'blocked')
        self.assertFalse(DichVuThucHien.objects.exists())
        self.assertEqual(HoaDon.objects.get().so_tien, Decimal('100000'))


class DoctorServiceMigrationTests(TransactionTestCase):
    def test_migration_preserves_existing_exam_and_invoice(self):
        latest = MigrationExecutor(connection).loader.graph.leaf_nodes()
        try:
            MigrationExecutor(connection).migrate([('core', '0020_danh_muc_dich_vu')])
            admin, _, _, appointment = payment_fixtures()
            historical_invoice = MigrationExecutor(connection).loader.project_state([('core', '0020_danh_muc_dich_vu')]).apps.get_model('core', 'HoaDon')
            invoice = historical_invoice.objects.create(lich_kham_id=appointment.pk, so_tien=111000, nguoi_tao_id=admin.pk)
            before = historical_invoice.objects.filter(pk=invoice.pk).values().get()
            phieu_before = PhieuKham.objects.get(lich_kham=appointment)
            MigrationExecutor(connection).migrate([('core', '0021_dich_vu_thuc_hien')])
            self.assertEqual(DichVuThucHien.objects.count(), 0)
            self.assertEqual(historical_invoice.objects.filter(pk=invoice.pk).values().get(), before)
            self.assertTrue(PhieuKham.objects.filter(pk=phieu_before.pk).exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
