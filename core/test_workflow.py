"""HTTP integration: registration -> booking -> doctor -> cashier -> PDF/email.

No live SMTP or gateway calls; provider callbacks are independently signed fixtures.
"""
import datetime
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth.models import User
from django.contrib.gis.geos import Point
from django.core import mail
from django.core.cache import cache
from django.core.mail.backends.locmem import EmailBackend
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (BacSi, BenhNhan, BenhVien, DanhMucDichVu, EmailPhieuThanhToan,
                         GiaoDichThanhToan, GioLamViecBacSi, GioLamViecBenhVien,
                         HoaDon, Khoa, LichKham, PhieuKham, QuyTacBHYT, ThuNgan)
from core.test_billing import callback
from core.test_exam_services import payload
from core.test_receipts import MAIL_SETTINGS, pdf_text


class WorkflowFixture:
    password = 'Workflow-Test@2026!'

    def prepare_workflow(self):
        cache.clear()  # DB flush does not clear the process-local hospital cache.
        today = timezone.localdate()
        self.date = today + datetime.timedelta(days=(7 - today.weekday()) % 7 or 7)
        self.hospital = BenhVien.objects.create(ten='Bệnh viện QA', dia_chi='Địa chỉ thử nghiệm',
            phuong='Phường thử', vi_tri=Point(106.7, 10.77, srid=4326), co_bhyt=True,
            gio_mo=datetime.time(7), gio_dong=datetime.time(17), loai_hinh='cong')
        self.department = Khoa.objects.create(ten='Nội tổng quát', benh_vien=self.hospital)
        self.doctor_user = User.objects.create_user('qaDoctor123', password=self.password)
        self.doctor = BacSi.objects.create(user=self.doctor_user, ho_ten='Bác Sĩ Thử Nghiệm',
            benh_vien=self.hospital, khoa=self.department, chuyen_khoa='Nội', so_dien_thoai='0901000001')
        GioLamViecBacSi.objects.create(bac_si=self.doctor, thu=1, gio_bat_dau=datetime.time(7), gio_ket_thuc=datetime.time(17))
        GioLamViecBenhVien.objects.create(benh_vien=self.hospital, thu=1, gio_mo=datetime.time(7), gio_dong=datetime.time(17))
        self.cashier = User.objects.create_user('qaCashier123', password=self.password)
        ThuNgan.objects.create(user=self.cashier, benh_vien=self.hospital)
        self.admin = User.objects.create_superuser('qaAdmin123', 'qa-admin@example.invalid', self.password)
        self.service = DanhMucDichVu.objects.create(ma_dich_vu='QA_KHAM', ten='Khám chuyên khoa QA',
            nhom='Khám', don_gia=Decimal('100000'))
        self.rule = QuyTacBHYT.objects.create(dich_vu=self.service, ten='Quy tắc QA mô phỏng',
            ty_le=Decimal('80'), hieu_luc_tu=today, dang_ap_dung=True)


@override_settings(**MAIL_SETTINGS)
class CompleteWorkflowTests(WorkflowFixture, TransactionTestCase):
    def setUp(self):
        self.prepare_workflow()
        self.smtp = patch('core.receipts.delivery.get_connection', side_effect=lambda **kw: EmailBackend())
        self.smtp.start()
        self.addCleanup(self.smtp.stop)

    def login(self, username):
        self.client.get(reverse('logout'))
        response = self.client.post(reverse('login'), {'username': username, 'password': self.password})
        self.assertEqual(response.status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)

    def complete_exam(self):
        self.client.post(reverse('register'), dict(username='qaPatient123', phone='0901000002',
            name='Nguyễn Thị Thử Nghiệm', email='qa-patient@example.invalid', password=self.password))
        self.patient = BenhNhan.objects.get(user__username='qaPatient123')
        self.login('qaPatient123')
        response = self.client.get(reverse('home'))
        self.assertContains(response, self.hospital.ten)
        self.assertEqual(self.client.get(reverse('hospital_detail', args=[self.hospital.pk])).status_code, 200)
        self.client.post(reverse('profile'), dict(ho_ten=self.patient.ho_ten, so_dien_thoai=self.patient.so_dien_thoai,
            ngay_sinh='2000-01-01', gioi_tinh='nu', dia_chi='Địa chỉ thử', ma_bhyt='QA-CARD',
            ngay_cap=timezone.localdate().isoformat(), ngay_het_han=(self.date + datetime.timedelta(days=30)).isoformat()))
        response = self.client.post(reverse('dat_lich', args=[self.hospital.pk]), dict(khoa=self.department.pk,
            bac_si=self.doctor.pk, ngay_kham=self.date.isoformat(), gio_kham='09:00', ghi_chu='QA'))
        self.assertEqual(response.status_code, 302)
        self.appointment = LichKham.objects.get(benh_nhan=self.patient)
        self.assertFalse(HoaDon.objects.filter(lich_kham=self.appointment).exists())
        self.assertEqual(self.client.get(reverse('payment_list')).status_code, 403)
        self.login('qaCashier123')
        self.client.post(reverse('invoice_create', args=[self.appointment.pk]), {'amount': '1'})
        self.assertFalse(HoaDon.objects.filter(lich_kham=self.appointment).exists())
        self.login('qaDoctor123')
        self.client.post(reverse('start_exam', args=[self.appointment.pk]))
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, 'dang')
        response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), payload(None, [(self.service, 2, '')]))
        self.assertEqual(response.status_code, 302)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, 'xong')
        self.phieu = PhieuKham.objects.get(lich_kham=self.appointment)
        self.invoice = HoaDon.objects.get(lich_kham=self.appointment)
        self.assertEqual(self.invoice.tong_chi_phi, Decimal('200000'))
        self.assertEqual(self.invoice.bhyt_ho_tro, Decimal('160000'))
        self.assertEqual(self.invoice.so_tien, Decimal('40000'))

    def assert_pdf_email_and_locks(self):
        invoice = self.invoice
        invoice.refresh_from_db()
        self.assertEqual(invoice.trang_thai, 'paid')
        self.assertEqual(EmailPhieuThanhToan.objects.get(hoa_don=invoice).trang_thai, 'sent')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].attachments[0].mimetype, 'application/pdf')
        response = self.client.get(reverse('receipt_download', args=[invoice.pk]))
        self.assertEqual(response.status_code, 200)
        text = pdf_text(response.content)
        for value in ('Nguyễn Thị Thử Nghiệm', 'Đã thanh toán', 'MÔ PHỎNG', 'NỘI BỘ'):
            self.assertIn(value, text)
        self.assertNotIn('Chẩn đoán thử', text)
        self.assertEqual(self.client.get(reverse('receipt_print', args=[invoice.pk])).status_code, 200)
        self.client.get(reverse('payment_detail', args=[self.appointment.pk]))
        self.client.get(reverse('payment_detail', args=[self.appointment.pk]))
        self.assertEqual(len(mail.outbox), 1)
        self.login('qaDoctor123')
        self.client.post(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]), payload(self.phieu, [(self.service, 9, '')]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.so_tien, self.paid_amount)
        self.assertEqual(invoice.giao_dichs.filter(trang_thai='success').count(), 1)
        self.login('qaPatient123')
        self.assertContains(self.client.get(reverse('medical_history')), 'Bệnh viện QA')
        self.assertEqual(self.client.get(reverse('phieu_kham_detail', args=[self.phieu.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('receipt_view', args=[invoice.pk])).status_code, 403)

    def test_cash_workflow_revision_and_snapshots(self):
        self.complete_exam()
        self.client.post(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]), payload(self.phieu, [(self.service, 3, '')]))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.phien_ban, 2)
        self.assertEqual(self.invoice.so_tien, Decimal('60000'))
        self.assertEqual(self.invoice.chi_tiets.count(), 2)
        self.service.don_gia = 999999
        self.service.save()
        self.login('qaCashier123')
        data = {'received_full': 'yes', 'version': 2, 'amount': 1}
        self.client.post(reverse('payment_cash', args=[self.invoice.pk]), {'version': 2})
        self.assertFalse(GiaoDichThanhToan.objects.exists())
        self.client.post(reverse('payment_cash', args=[self.invoice.pk]), data)
        self.client.post(reverse('payment_cash', args=[self.invoice.pk]), data)
        self.paid_amount = Decimal('60000')
        self.assertEqual(GiaoDichThanhToan.objects.get().so_tien, self.paid_amount)
        self.assert_pdf_email_and_locks()

    def test_vnpay_workflow_return_then_verified_ipn(self):
        self.complete_exam()
        self.login('qaCashier123')
        response = self.client.post(reverse('payment_start', args=[self.invoice.pk]), {'amount': 1})
        self.assertEqual(parse_qs(urlsplit(response.url).query)['vnp_Amount'], ['4000000'])
        attempt = GiaoDichThanhToan.objects.get()
        signed = callback(attempt)
        self.assertEqual(self.client.get(reverse('vnpay_return'), signed).status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.trang_thai, 'unpaid')
        self.assertFalse(EmailPhieuThanhToan.objects.exists())
        response = self.client.get(reverse('vnpay_ipn'), signed)
        self.assertEqual(response.json()['RspCode'], '00')
        self.assertEqual(self.client.get(reverse('vnpay_ipn'), signed).json()['RspCode'], '02')
        self.paid_amount = Decimal('40000')
        self.assert_pdf_email_and_locks()
