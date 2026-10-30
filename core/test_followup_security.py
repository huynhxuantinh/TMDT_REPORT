"""Issues 08–16: upload limits, appointment transitions, booking and navigation."""
import datetime
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image
from django.contrib.auth.models import Permission, User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, IntegrityError, close_old_connections, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.appointments import book_appointment
from core.exam_uploads import MAX_IMAGE_BYTES, validate_exam_image, validate_exam_images
from core.models import (BacSi, BenhNhan, GioLamViecBacSi, LichKham, LichSuKhamBenh,
                         LogHeThong, LogLichKham, PhieuKham, PhieuKhamHinhAnh, ThongBao)
from core.test_exam_services import fixtures

NOW = datetime.datetime(2030, 1, 7, 10, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
CLINICAL = {'trieu_chung': 'Triệu chứng mới', 'chan_doan': 'Chẩn đoán mới', 'huong_dieu_tri': 'Theo dõi'}


def image_file(name='scan.png', size=(3, 3), format='PNG'):
    stream = BytesIO()
    Image.new('RGB', size, 'white').save(stream, format)
    return SimpleUploadedFile(name, stream.getvalue(), content_type='image/png')


class Fixture:
    def prepare(self):
        (self.admin, self.patient, self.appointment, self.doctor, self.other_doctor,
         self.service, self.other_service, _) = fixtures()
        self.exam = PhieuKham.objects.get(lich_kham=self.appointment)
        for day in range(7):
            GioLamViecBacSi.objects.create(bac_si=self.appointment.bac_si, thu=day,
                gio_bat_dau=datetime.time(7), gio_ket_thuc=datetime.time(17))

    def prepare_open(self, status='cho', date=None, hour=9):
        LichSuKhamBenh.objects.filter(phieu_kham=self.exam).delete()
        self.exam.delete()
        self.appointment.trang_thai = status
        self.appointment.ngay_kham = date or NOW.date()
        self.appointment.gio_kham = datetime.time(hour)
        self.appointment.save()


@override_settings(TIME_ZONE='Asia/Ho_Chi_Minh')
class ClinicalTransitionTests(Fixture, TestCase):
    def setUp(self):
        self.prepare()
        self.prepare_open()
        frozen = patch('django.utils.timezone.now', return_value=NOW)
        frozen.start()
        self.addCleanup(frozen.stop)
        self.client.force_login(self.doctor)

    def start(self, **data):
        return self.client.post(reverse('start_exam', args=[self.appointment.pk]), data)

    def finish(self, **data):
        return self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), {**CLINICAL, **data})

    def assert_state(self, status):
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, status)

    def test_get_and_refresh_do_not_start_finish_or_repair_state(self):
        for _ in range(2):
            self.client.get(reverse('doctor_exam', args=[self.appointment.pk]))
            self.client.get(reverse('bac_si_home'))
            self.assertEqual(self.client.get(reverse('start_exam', args=[self.appointment.pk])).status_code, 405)
            self.assert_state('cho')
        PhieuKham.objects.create(lich_kham=self.appointment, benh_nhan=self.appointment.benh_nhan, **CLINICAL)
        for url in (reverse('doctor_exam', args=[self.appointment.pk]), reverse('bac_si_home')):
            self.client.get(url)
            self.assert_state('cho')

    def test_post_cannot_skip_start_then_valid_start_finish_are_atomic(self):
        self.finish()
        self.assert_state('cho')
        self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())
        self.start()
        self.assert_state('dang')
        self.finish()
        self.assert_state('xong')
        self.assertEqual(PhieuKham.objects.filter(lich_kham=self.appointment).count(), 1)
        self.finish()
        self.assertEqual(PhieuKham.objects.filter(lich_kham=self.appointment).count(), 1)

    def test_future_start_and_completion_need_permission_and_reason(self):
        self.appointment.ngay_kham = NOW.date() + datetime.timedelta(days=1)
        self.appointment.save(update_fields=['ngay_kham'])
        self.start(exception_reason='Khách đến khám sớm')
        self.assert_state('cho')
        self.doctor.user_permissions.add(Permission.objects.get(codename='kham_som'))
        self.start()  # Permission alone is not sufficient for an exception.
        self.assert_state('cho')
        self.start(exception_reason='Khách đến khám sớm')
        self.assert_state('dang')
        self.doctor.user_permissions.clear()
        self.finish(exception_reason='Khách đến khám sớm')
        self.assert_state('dang')
        self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())
        self.doctor.user_permissions.add(Permission.objects.get(codename='kham_som'))
        self.finish(exception_reason='Khách đến khám sớm')
        self.assert_state('xong')
        logs = LogHeThong.objects.filter(model_name='LichKham', object_id=self.appointment.pk,
                                        du_lieu_moi__quyen_ngoai_le='core.kham_som')
        self.assertEqual(logs.count(), 2)
        self.assertTrue(all(log.nguoi_thuc_hien_id == self.doctor.pk for log in logs))

    def test_retrospective_permission_is_separate_from_early_permission(self):
        self.appointment.ngay_kham = NOW.date() - datetime.timedelta(days=1)
        self.appointment.save(update_fields=['ngay_kham'])
        self.doctor.user_permissions.add(Permission.objects.get(codename='kham_som'))
        self.start(exception_reason='Ghi bổ sung sau sự cố')
        self.assert_state('cho')
        self.doctor.user_permissions.add(Permission.objects.get(codename='kham_hoi_to'))
        self.start(exception_reason='Ghi bổ sung sau sự cố')
        self.finish(exception_reason='Ghi bổ sung sau sự cố')
        self.assert_state('xong')

    def test_cancelled_and_completed_cannot_restart(self):
        for status in ('huy', 'xong'):
            LichKham.objects.filter(pk=self.appointment.pk).update(trang_thai=status)
            self.start()
            self.finish()
            self.assert_state(status)

    def test_csrf_and_doctor_scope_are_checked(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.doctor)
        for route in ('start_exam', 'doctor_exam'):
            self.assertEqual(strict.post(reverse(route, args=[self.appointment.pk]), CLINICAL).status_code, 403)
        self.client.force_login(self.other_doctor)
        self.assertEqual(self.start().status_code, 404)
        self.assertEqual(self.finish().status_code, 404)
        self.assert_state('cho')


class UploadTests(Fixture, TestCase):
    def setUp(self):
        self.prepare()
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        storage = override_settings(PRIVATE_MEDIA_ROOT=Path(temp.name) / 'private', MEDIA_ROOT=Path(temp.name) / 'public')
        storage.enable()
        self.addCleanup(storage.disable)
        self.client.force_login(self.doctor)

    def test_rejects_fake_extension_wrong_format_oversized_and_dimensions(self):
        invalid = [
            SimpleUploadedFile('fake.jpg', b'<script>alert(1)</script>', content_type='image/jpeg'),
            SimpleUploadedFile('big.jpg', b'x' * (MAX_IMAGE_BYTES + 1), content_type='image/jpeg'),
            image_file('not-jpeg.jpg'), image_file(size=(4097, 1)),
        ]
        for upload in invalid:
            with self.subTest(name=upload.name), self.assertRaises(ValidationError):
                validate_exam_image(upload)
        with self.assertRaises(ValidationError):
            validate_exam_images([image_file() for _ in range(11)])
        with self.assertRaises(ValidationError):
            validate_exam_images([SimpleUploadedFile('a.jpg', b'x' * MAX_IMAGE_BYTES) for _ in range(5)])
        upload = image_file()
        validate_exam_image(upload)
        self.assertEqual(upload.tell(), 0)

    def test_doctor_edit_checks_remaining_count_and_rejects_all_changes_on_invalid_image(self):
        images = [PhieuKhamHinhAnh.objects.create(phieu_kham=self.exam, hinh_anh=image_file()) for _ in range(10)]
        for extra in ({'new_images': image_file()}, {'new_images': SimpleUploadedFile('fake.jpg', b'fake')}):
            response = self.client.post(reverse('bac_si_phieu_kham_edit', args=[self.exam.pk]), {**CLINICAL, **extra})
            self.assertEqual(response.status_code, 200)
            self.exam.refresh_from_db()
            self.assertEqual(self.exam.chan_doan, 'Test')
            self.assertEqual(self.exam.hinh_anhs.count(), 10)
        response = self.client.post(reverse('bac_si_phieu_kham_edit', args=[self.exam.pk]),
            {**CLINICAL, 'new_images': image_file(), 'remove_image_ids': [images[0].pk]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.exam.hinh_anhs.count(), 10)

    def test_doctor_create_bad_batch_rolls_back_completion_and_files(self):
        self.prepare_open('dang')
        with patch('django.utils.timezone.now', return_value=NOW):
            self.client.force_login(self.doctor)
            response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]),
                {**CLINICAL, 'images': [image_file(), SimpleUploadedFile('fake.jpg', b'not-an-image')]})
        self.assertEqual(response.status_code, 200)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, 'dang')
        self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())
        self.assertFalse(PhieuKhamHinhAnh.objects.exists())

    def test_single_image_model_and_admin_route_cannot_bypass_validator(self):
        with self.assertRaises(ValidationError):
            PhieuKhamHinhAnh.objects.create(phieu_kham=self.exam, hinh_anh=SimpleUploadedFile('fake.jpg', b'fake'))
        self.client.force_login(self.admin)
        response = self.client.post(reverse('custom_admin_model_create', args=['phieu-kham-hinh-anh']),
            {'phieu_kham': self.exam.pk, 'thu_tu': 0, 'hinh_anh': image_file(size=(4097, 1))})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PhieuKhamHinhAnh.objects.exists())

    def test_admin_exam_edit_rejects_count_overflow_and_preserves_clinical_text(self):
        self.client.force_login(self.admin)
        for _ in range(10):
            PhieuKhamHinhAnh.objects.create(phieu_kham=self.exam, hinh_anh=image_file())
        response = self.client.post(reverse('custom_admin_model_edit', args=['phieu-kham', self.exam.pk]),
            {**CLINICAL, 'benh_nhan': self.exam.benh_nhan_id, 'lich_kham': self.exam.lich_kham_id, 'exam_images': image_file()})
        self.assertEqual(response.status_code, 200)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.chan_doan, 'Test')
        self.assertEqual(self.exam.hinh_anhs.count(), 10)


@override_settings(TIME_ZONE='Asia/Ho_Chi_Minh')
class BookingAndScheduleTests(Fixture, TestCase):
    def setUp(self):
        self.prepare()

    def test_today_past_hour_and_seconds_are_rejected_but_future_slot_is_valid(self):
        with patch('django.utils.timezone.now', return_value=NOW):
            for hour, minute, second in ((9, 0, 0), (10, 0, 0), (11, 0, 1), (11, 15, 0)):
                appointment = LichKham(benh_nhan=self.appointment.benh_nhan, bac_si=self.appointment.bac_si,
                    ngay_kham=NOW.date(), gio_kham=datetime.time(hour, minute, second))
                with self.subTest(time=appointment.gio_kham), self.assertRaises(ValidationError):
                    appointment.full_clean()
            appointment.gio_kham = datetime.time(11)
            appointment.full_clean()

    def test_booking_notification_failure_rolls_back_appointment_and_log(self):
        appointment = LichKham(benh_nhan=self.appointment.benh_nhan, bac_si=self.appointment.bac_si,
            ngay_kham=NOW.date(), gio_kham=datetime.time(11))
        with patch('django.utils.timezone.now', return_value=NOW), patch.object(ThongBao.objects, 'create', side_effect=DatabaseError('failure')):
            with self.assertRaises(DatabaseError):
                book_appointment(appointment)
        self.assertFalse(LichKham.objects.filter(ngay_kham=NOW.date()).exists())

    def schedule_data(self):
        return {key: value for day in range(7) for key, value in
                ((f'gio_bat_dau_{day}', '08:00'), (f'gio_ket_thuc_{day}', '16:00'))}

    def test_invalid_last_day_does_not_save_first_days(self):
        self.client.force_login(self.doctor)
        before = list(GioLamViecBacSi.objects.filter(bac_si=self.appointment.bac_si).values())
        data = self.schedule_data()
        data['gio_ket_thuc_6'] = '07:00'
        self.client.post(reverse('bac_si_schedule'), data)
        self.assertEqual(list(GioLamViecBacSi.objects.filter(bac_si=self.appointment.bac_si).values()), before)

    def test_database_failure_rolls_back_all_schedule_days(self):
        self.client.force_login(self.doctor)
        before = list(GioLamViecBacSi.objects.filter(bac_si=self.appointment.bac_si).values())
        original = GioLamViecBacSi.objects.update_or_create
        calls = 0
        def failing_write(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 5:
                raise DatabaseError('simulated failure')
            return original(*args, **kwargs)
        with patch.object(GioLamViecBacSi.objects, 'update_or_create', side_effect=failing_write):
            with self.assertRaises(DatabaseError):
                self.client.post(reverse('bac_si_schedule'), self.schedule_data())
        self.assertEqual(list(GioLamViecBacSi.objects.filter(bac_si=self.appointment.bac_si).values()), before)

    def test_valid_schedule_saves_every_day(self):
        self.client.force_login(self.doctor)
        self.client.post(reverse('bac_si_schedule'), self.schedule_data())
        self.assertEqual(GioLamViecBacSi.objects.filter(bac_si=self.appointment.bac_si,
                         gio_bat_dau=datetime.time(8), gio_ket_thuc=datetime.time(16)).count(), 7)


class NavigationTests(Fixture, TestCase):
    def setUp(self):
        self.prepare()
        self.patient.set_password('Navigation-Test@2030')
        self.patient.save()

    def test_login_round_trip_keeps_booking_selection_and_failed_login_next(self):
        path = reverse('dat_lich', args=[self.appointment.bac_si.benh_vien_id]) + (
            f'?khoa={self.appointment.bac_si.khoa_id}&bac_si={self.appointment.bac_si_id}&ngay_kham=2030-01-08&gio_kham=11%3A00')
        response = self.client.get(path)
        self.assertEqual(response.status_code, 302)
        login_page = self.client.get(response.url)
        self.assertEqual(login_page.context['next'], path)
        failed = self.client.post(reverse('login'), {'username': self.patient.username, 'password': 'wrong', 'next': path})
        self.assertEqual(failed.context['next'], path)
        response = self.client.post(reverse('login'), {'username': self.patient.username,
            'password': 'Navigation-Test@2030', 'next': path})
        self.assertRedirects(response, path)
        booking = self.client.get(path)
        self.assertEqual(str(booking.context['form'].initial['bac_si']), str(self.appointment.bac_si_id))

    def test_external_and_protocol_relative_next_are_ignored(self):
        for path in ('https://attacker.invalid/', '//attacker.invalid/', 'javascript:alert(1)', '/\\attacker.invalid/'):
            self.client.logout()
            response = self.client.post(reverse('login'), {'username': self.patient.username,
                'password': 'Navigation-Test@2030', 'next': path})
            self.assertRedirects(response, reverse('home'))

    def test_old_notifications_resolve_to_authorized_real_pages(self):
        valid = ThongBao.objects.create(nguoi_nhan=self.doctor, loai='lich_kham', tieu_de='Old', noi_dung='Test',
                                        lien_ket=f'/lich-kham/{self.appointment.pk}/')
        invalid = ThongBao.objects.create(nguoi_nhan=self.doctor, loai='lich_kham', tieu_de='Bad', noi_dung='Test',
                                          lien_ket='/lich-kham/9999999/')
        self.client.force_login(self.doctor)
        response = self.client.get(reverse('bac_si_notifications'))
        links = {n.pk: n.safe_link for n in response.context['thong_baos']}
        self.assertEqual(links[valid.pk], reverse('phieu_kham_detail', args=[self.exam.pk]))
        self.assertEqual(self.client.get(links[valid.pk]).status_code, 200)
        self.assertEqual(links[invalid.pk], '')
        self.appointment.bac_si = self.other_doctor.bac_si
        self.appointment.save(update_fields=['bac_si'])
        response = self.client.get(reverse('bac_si_notifications'))
        self.assertEqual({n.pk: n.safe_link for n in response.context['thong_baos']}[valid.pk], '')

    def test_missing_coordinates_are_json_null_and_search_returns_result_coordinates(self):
        cache.clear()
        hospital = self.appointment.bac_si.benh_vien
        response = self.client.get(reverse('home'), {'q': hospital.ten})
        self.assertEqual([p['id'] for p in response.context['map_data']], [hospital.pk])
        with patch('core.views.point_to_latlon', return_value=(None, None)):
            response = self.client.get(reverse('hospital_detail', args=[hospital.pk]))
        self.assertContains(response, '"lat": null, "lon": null')
        self.assertNotContains(response, 'Number(null)')


class BookingConcurrencyTests(Fixture, TransactionTestCase):
    def setUp(self):
        self.prepare()
        self.second_patient = BenhNhan.objects.create(ho_ten='Bệnh nhân thứ hai', so_dien_thoai='0901000033',
            ngay_sinh=datetime.date(2000, 1, 1), gioi_tinh='nam', dia_chi='Test')

    def concurrent(self, function):
        barrier = Barrier(2)
        def worker(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return function(index)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(worker, i) for i in range(2)]
            return [job.result(timeout=25) for job in jobs]

    def test_two_bookings_same_doctor_slot_only_one_succeeds(self):
        def book(index):
            appointment = LichKham(benh_nhan_id=[self.appointment.benh_nhan_id, self.second_patient.pk][index],
                bac_si_id=self.appointment.bac_si_id, ngay_kham=NOW.date(), gio_kham=datetime.time(11))
            try:
                book_appointment(appointment)
                return 'saved'
            except (ValidationError, IntegrityError):
                return 'blocked'
        self.assertCountEqual(self.concurrent(book), ['saved', 'blocked'])
        self.assertEqual(LichKham.objects.filter(ngay_kham=NOW.date()).count(), 1)

    def test_same_patient_different_slots_still_respects_one_per_day(self):
        def book(index):
            appointment = LichKham(benh_nhan_id=self.appointment.benh_nhan_id, bac_si_id=self.appointment.bac_si_id,
                ngay_kham=NOW.date(), gio_kham=datetime.time(11 + index))
            try:
                book_appointment(appointment)
                return 'saved'
            except (ValidationError, IntegrityError):
                return 'blocked'
        self.assertCountEqual(self.concurrent(book), ['saved', 'blocked'])

    def test_database_constraint_alone_blocks_simultaneous_slot_inserts(self):
        def insert(index):
            try:
                with transaction.atomic():
                    LichKham.objects.create(benh_nhan_id=[self.appointment.benh_nhan_id, self.second_patient.pk][index],
                        bac_si_id=self.appointment.bac_si_id, ngay_kham=NOW.date(), gio_kham=datetime.time(11))
                return 'saved'
            except IntegrityError:
                return 'blocked'
        self.assertCountEqual(self.concurrent(insert), ['saved', 'blocked'])

    def test_two_http_booking_requests_show_one_success_and_one_form_error(self):
        second_user = User.objects.create_user('second-booking-user')
        self.second_patient.user = second_user
        self.second_patient.save(update_fields=['user'])
        def request(index):
            client = Client()
            client.force_login([self.patient, second_user][index])
            response = client.post(reverse('dat_lich', args=[self.appointment.bac_si.benh_vien_id]), {
                'khoa': self.appointment.bac_si.khoa_id, 'bac_si': self.appointment.bac_si_id,
                'ngay_kham': NOW.date().isoformat(), 'gio_kham': '11:00', 'ghi_chu': '',
            })
            return response.status_code
        self.assertCountEqual(self.concurrent(request), [302, 200])
        self.assertEqual(LichKham.objects.filter(ngay_kham=NOW.date()).count(), 1)

    def test_two_simultaneous_completions_create_one_exam(self):
        self.prepare_open('dang')
        def finish(index):
            client = Client()
            client.force_login(self.doctor)
            return client.post(reverse('doctor_exam', args=[self.appointment.pk]), CLINICAL).status_code
        with patch('django.utils.timezone.now', return_value=NOW):
            results = self.concurrent(finish)
        self.assertTrue(all(code in (200, 302) for code in results))
        self.assertEqual(PhieuKham.objects.filter(lich_kham=self.appointment).count(), 1)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, 'xong')
        self.assertEqual(LogHeThong.objects.filter(model_name='LichKham', object_id=self.appointment.pk,
                         du_lieu_moi__trang_thai='xong').count(), 1)

    def test_concurrent_images_cannot_exceed_per_exam_limit(self):
        with TemporaryDirectory() as temporary, override_settings(PRIVATE_MEDIA_ROOT=Path(temporary) / 'private'):
            for _ in range(9):
                PhieuKhamHinhAnh.objects.create(phieu_kham=self.exam, hinh_anh=image_file())
            def upload(index):
                try:
                    PhieuKhamHinhAnh.objects.create(phieu_kham_id=self.exam.pk, hinh_anh=image_file())
                    return 'saved'
                except ValidationError:
                    return 'blocked'
            self.assertCountEqual(self.concurrent(upload), ['saved', 'blocked'])
            self.assertEqual(self.exam.hinh_anhs.count(), 10)
            self.assertEqual(len(list(Path(temporary).rglob('*.png'))), 10)
