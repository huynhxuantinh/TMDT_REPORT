"""Regression tests for the custom admin delete-impact preview."""
from django.test import TestCase
from django.urls import reverse

from core.models import DichVuThucHien, PhieuKham
from core.test_exam_services import fixtures


class AdminDeleteImpactTests(TestCase):
    def setUp(self):
        (self.admin, self.patient, self.appointment, self.doctor_user, self.other_user,
         self.service, self.other_service, self.inactive) = fixtures()
        self.phieu = PhieuKham.objects.get(lich_kham=self.appointment)
        DichVuThucHien.objects.create(phieu_kham=self.phieu, dich_vu=self.service, so_luong=1,
                                      nguoi_thuc_hien=self.appointment.bac_si)
        self.client.force_login(self.admin)

    def test_delete_confirm_page_lists_exam_services(self):
        response = self.client.get(
            reverse('custom_admin_model_delete', args=['phieu-kham', self.phieu.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Dịch vụ thực hiện')

    def test_delete_confirm_page_renders_for_appointment(self):
        response = self.client.get(
            reverse('custom_admin_model_delete', args=['lich-kham', self.appointment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nhật ký lịch khám')

    def test_post_cannot_delete_exam_with_related_rows(self):
        response = self.client.post(
            reverse('custom_admin_model_delete', args=['phieu-kham', self.phieu.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(PhieuKham.objects.filter(pk=self.phieu.pk).exists())
