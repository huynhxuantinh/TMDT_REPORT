from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse

from core.billing.services import bill_exam
from core.exam_services.services import form_context, sync_services
from core.models import BenhVien, DanhMucDichVu, DichVuThucHien, PhieuKham, QuanLyChiNhanh
from core.test_exam_services import payload
from core.test_vnpay import CONFIG, fixtures as payment_fixtures


@override_settings(**CONFIG)
class BranchManagerPermissionsTests(TestCase):
    def setUp(self):
        self.admin, _, self.patient, self.appointment = payment_fixtures()
        self.doctor_user = User.objects.create_user('doctor-role')
        self.appointment.bac_si.user = self.doctor_user
        self.appointment.bac_si.save(update_fields=['user'])
        self.manager = User.objects.create_user('branch-manager')
        QuanLyChiNhanh.objects.create(user=self.manager, benh_vien=self.appointment.bac_si.benh_vien)
        self.service = DanhMucDichVu.objects.create(ma_dich_vu='MANAGER_TEST', ten='Manager test', nhom='Khám bệnh', don_gia=100000)
        self.phieu = PhieuKham.objects.get(lich_kham=self.appointment)

    def service_form(self):
        context = form_context(self.appointment, self.phieu)
        return context, payload(self.phieu, [(self.service, 1, '')])

    def test_doctor_cannot_edit_services_or_bill(self):
        context, data = self.service_form()
        context = form_context(self.appointment, self.phieu, data)
        with self.assertRaises(PermissionDenied):
            sync_services(self.phieu, self.doctor_user, context['services_formset'], context['services_revision'])
        with self.assertRaises(PermissionDenied):
            bill_exam(self.phieu.pk, self.doctor_user)

    def test_manager_saves_services_and_creates_invoice(self):
        self.client.force_login(self.manager)
        context, data = self.service_form()
        response = self.client.post(reverse('payment_services_save', args=[self.appointment.pk]), data)
        self.assertRedirects(response, reverse('payment_detail', args=[self.appointment.pk]))
        self.assertTrue(DichVuThucHien.objects.filter(phieu_kham=self.phieu, dich_vu=self.service).exists())
        self.assertTrue(hasattr(self.appointment, 'hoa_don'))

    def test_manager_cannot_edit_unfinished_or_other_hospital(self):
        context, data = self.service_form()
        self.appointment.trang_thai = 'dang'
        self.appointment.save(update_fields=['trang_thai'])
        context = form_context(self.appointment, self.phieu, data)
        with self.assertRaises(Exception):
            sync_services(self.phieu, self.manager, context['services_formset'], context['services_revision'])

        self.appointment.trang_thai = 'xong'
        self.appointment.save(update_fields=['trang_thai'])
        other = BenhVien.objects.create(
            ten='Bệnh viện khác', dia_chi='Khác', phuong='Khác', vi_tri=self.appointment.bac_si.benh_vien.vi_tri,
            gio_mo='07:00', gio_dong='17:00', loai_hinh='cong',
        )
        QuanLyChiNhanh.objects.filter(user=self.manager).update(benh_vien=other)
        context = form_context(self.appointment, self.phieu, data)
        with self.assertRaises(PermissionDenied):
            sync_services(self.phieu, self.manager, context['services_formset'], context['services_revision'])

    def test_doctor_page_hides_service_prices_and_monitor_cannot_save(self):
        self.client.force_login(self.doctor_user)
        response = self.client.get(reverse('bac_si_phieu_kham_edit', args=[self.phieu.pk]))
        self.assertNotContains(response, 'DỊCH VỤ Y TẾ ĐÃ THỰC HIỆN')
        self.assertNotContains(response, 'service-estimated-total')

        monitor = User.objects.create_user('monitor', is_staff=True)
        self.client.force_login(monitor)
        _, data = self.service_form()
        self.assertEqual(self.client.post(reverse('payment_services_save', args=[self.appointment.pk]), data).status_code, 403)
