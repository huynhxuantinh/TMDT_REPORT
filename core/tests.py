import datetime

from django.core import mail
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.contrib.gis.geos import Point
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.forms import AdminBenhVienForm
from core.models import (
    BacSi,
    BenhNhan,
    BenhVien,
    GioLamViecBacSi,
    GioLamViecBenhVien,
    Khoa,
    LichKham,
    PhieuKham,
)


class DoctorFlowTests(TestCase):
    def setUp(self):
        self.hospital = BenhVien.objects.create(
            ten="Benh vien test",
            dia_chi="1 Duong Test",
            phuong="Quan 1",
            vi_tri=Point(106.7, 10.77, srid=4326),
            cap_cuu_24h=False,
            co_bhyt=True,
            gio_mo=datetime.time(7, 0),
            gio_dong=datetime.time(17, 0),
            loai_hinh="cong",
        )
        self.department = Khoa.objects.create(
            ten="Noi tong quat",
            benh_vien=self.hospital,
        )

        self.doctor_user = User.objects.create_user(
            username="doctor1",
            password="pass12345",
        )
        self.other_doctor_user = User.objects.create_user(
            username="doctor2",
            password="pass12345",
        )
        self.patient_user = User.objects.create_user(
            username="0909000999",
            password="pass12345",
        )

        self.doctor = BacSi.objects.create(
            user=self.doctor_user,
            ho_ten="Bac si A",
            chuyen_khoa="Noi tong quat",
            khoa=self.department,
            benh_vien=self.hospital,
            so_dien_thoai="0909000001",
        )
        self.other_doctor = BacSi.objects.create(
            user=self.other_doctor_user,
            ho_ten="Bac si B",
            chuyen_khoa="Noi tong quat",
            khoa=self.department,
            benh_vien=self.hospital,
            so_dien_thoai="0909000002",
        )
        self.patient = BenhNhan.objects.create(
            user=self.patient_user,
            ho_ten="Benh nhan A",
            ngay_sinh=datetime.date(1995, 1, 1),
            gioi_tinh="nam",
            so_dien_thoai=self.patient_user.username,
            dia_chi="TP.HCM",
        )

        self.appointment = LichKham.objects.create(
            benh_nhan=self.patient,
            bac_si=self.doctor,
            ngay_kham=timezone.localdate() + datetime.timedelta(days=1),
            gio_kham=datetime.time(9, 0),
            trang_thai="cho",
            ghi_chu="",
        )

    def test_start_exam_requires_post(self):
        self.client.login(username="doctor1", password="pass12345")

        response = self.client.get(reverse("start_exam", args=[self.appointment.id]))

        self.assertRedirects(response, reverse("bac_si_home"))
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, "cho")

    def test_start_exam_post_updates_status(self):
        self.client.login(username="doctor1", password="pass12345")

        response = self.client.post(reverse("start_exam", args=[self.appointment.id]))

        self.assertRedirects(response, reverse("bac_si_home"))
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, "dang")

    def test_start_exam_permission_enforced(self):
        self.client.login(username="doctor2", password="pass12345")

        response = self.client.post(reverse("start_exam", args=[self.appointment.id]))

        self.assertEqual(response.status_code, 404)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, "cho")

    def test_start_exam_patient_is_redirected_home(self):
        self.client.login(username=self.patient_user.username, password="pass12345")

        response = self.client.post(reverse("start_exam", args=[self.appointment.id]))

        self.assertRedirects(response, reverse("home"))

    def test_doctor_exam_creates_phieu_and_marks_done(self):
        from core.models import DanhMucDichVu
        from core.exam_services.services import revision
        self.client.login(username="doctor1", password="pass12345")
        self.client.get(reverse('doctor_exam', args=[self.appointment.pk]))
        service = DanhMucDichVu.objects.create(ma_dich_vu='FLOW_FEE', ten='Khám', nhom='Khám bệnh', don_gia=100000)
        payload = {
            "trieu_chung": "Dau bung",
            "chan_doan": "Viem da day",
            "huong_dieu_tri": "Uong thuoc",
            'services-TOTAL_FORMS': '1', 'services-INITIAL_FORMS': '0',
            'services_revision': revision([]), 'services-0-dich_vu': str(service.pk), 'services-0-so_luong': '1',
        }

        response = self.client.post(reverse("doctor_exam", args=[self.appointment.id]), payload)

        self.assertRedirects(response, reverse("bac_si_home"))
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, "xong")

        phieu = PhieuKham.objects.get(lich_kham=self.appointment)
        self.assertEqual(phieu.benh_nhan, self.patient)
        self.assertEqual(phieu.chan_doan, "Viem da day")

    def test_doctor_exam_rejects_empty_payload(self):
        self.client.login(username="doctor1", password="pass12345")

        response = self.client.post(
            reverse("doctor_exam", args=[self.appointment.id]),
            {"trieu_chung": "", "chan_doan": "", "huong_dieu_tri": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, "cho")

    def test_doctor_exam_permission_enforced(self):
        self.client.login(username="doctor2", password="pass12345")

        response = self.client.get(reverse("doctor_exam", args=[self.appointment.id]))

        self.assertEqual(response.status_code, 404)


class AppointmentBookingTests(TestCase):
    def setUp(self):
        self.hospital = BenhVien.objects.create(
            ten="Benh vien A",
            dia_chi="1 Duong A",
            phuong="Quan 1",
            vi_tri=Point(106.7, 10.77, srid=4326),
            cap_cuu_24h=False,
            co_bhyt=True,
            gio_mo=datetime.time(7, 0),
            gio_dong=datetime.time(17, 0),
            loai_hinh="cong",
        )
        self.other_hospital = BenhVien.objects.create(
            ten="Benh vien B",
            dia_chi="2 Duong B",
            phuong="Quan 3",
            vi_tri=Point(106.68, 10.79, srid=4326),
            cap_cuu_24h=False,
            co_bhyt=True,
            gio_mo=datetime.time(7, 0),
            gio_dong=datetime.time(17, 0),
            loai_hinh="cong",
        )
        self.department = Khoa.objects.create(ten="Noi", benh_vien=self.hospital)
        self.other_department = Khoa.objects.create(ten="Ngoai", benh_vien=self.other_hospital)

        self.doctor = BacSi.objects.create(
            ho_ten="Bac si A",
            chuyen_khoa="Noi",
            khoa=self.department,
            benh_vien=self.hospital,
            so_dien_thoai="0911000001",
        )
        self.other_doctor = BacSi.objects.create(
            ho_ten="Bac si B",
            chuyen_khoa="Ngoai",
            khoa=self.other_department,
            benh_vien=self.other_hospital,
            so_dien_thoai="0911000002",
        )

        # Thu 2 full-day working window so booking validation can pass.
        GioLamViecBacSi.objects.create(
            bac_si=self.doctor,
            thu=1,
            gio_bat_dau=datetime.time(7, 0),
            gio_ket_thuc=datetime.time(17, 0),
            nghi=False,
        )
        GioLamViecBenhVien.objects.create(
            benh_vien=self.hospital,
            thu=1,
            gio_mo=datetime.time(7, 0),
            gio_dong=datetime.time(17, 0),
            nghi=False,
        )

        self.user = User.objects.create_user(username="0911999999", password="pass12345")
        self.patient = BenhNhan.objects.create(
            user=self.user,
            ho_ten="Benh nhan test",
            ngay_sinh=datetime.date(1999, 1, 1),
            gioi_tinh="nam",
            so_dien_thoai=self.user.username,
            dia_chi="TP.HCM",
        )
        self.client.login(username=self.user.username, password="pass12345")

        # Find upcoming Monday for deterministic schedule validation.
        today = timezone.localdate()
        days_ahead = (0 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        self.next_monday = today + datetime.timedelta(days=days_ahead)

    def test_booking_requires_department(self):
        response = self.client.post(
            reverse("dat_lich", args=[self.hospital.id]),
            {
                "bac_si": self.doctor.id,
                "ngay_kham": self.next_monday.isoformat(),
                "gio_kham": "09:00",
                "ghi_chu": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertFalse(LichKham.objects.filter(benh_nhan=self.patient).exists())

    def test_booking_rejects_doctor_outside_selected_hospital(self):
        response = self.client.post(
            reverse("dat_lich", args=[self.hospital.id]),
            {
                "khoa": str(self.department.id),
                "bac_si": self.other_doctor.id,
                "ngay_kham": self.next_monday.isoformat(),
                "gio_kham": "09:00",
                "ghi_chu": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("bac_si", response.context["form"].errors)
        self.assertFalse(LichKham.objects.filter(benh_nhan=self.patient).exists())

    def test_booking_success_redirects_to_upcoming_appointments(self):
        response = self.client.post(
            reverse("dat_lich", args=[self.hospital.id]),
            {
                "khoa": str(self.department.id),
                "bac_si": self.doctor.id,
                "ngay_kham": self.next_monday.isoformat(),
                "gio_kham": "09:00",
                "ghi_chu": "Tai kham",
            },
        )

        self.assertRedirects(response, reverse("upcoming_appointments"))
        self.assertTrue(
            LichKham.objects.filter(
                benh_nhan=self.patient,
                bac_si=self.doctor,
                ngay_kham=self.next_monday,
                gio_kham=datetime.time(9, 0),
            ).exists()
        )

    def test_booking_can_reuse_slot_when_previous_was_cancelled(self):
        LichKham.objects.create(
            benh_nhan=self.patient,
            bac_si=self.doctor,
            ngay_kham=self.next_monday,
            gio_kham=datetime.time(10, 0),
            trang_thai="huy",
            ghi_chu="Da huy",
        )

        response = self.client.post(
            reverse("dat_lich", args=[self.hospital.id]),
            {
                "khoa": str(self.department.id),
                "bac_si": self.doctor.id,
                "ngay_kham": self.next_monday.isoformat(),
                "gio_kham": "10:00",
                "ghi_chu": "Dat lai",
            },
        )

        self.assertRedirects(response, reverse("upcoming_appointments"))
        self.assertEqual(
            LichKham.objects.filter(
                benh_nhan=self.patient,
                bac_si=self.doctor,
                ngay_kham=self.next_monday,
                gio_kham=datetime.time(10, 0),
            ).count(),
            2,
        )


class AdminDashboardTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin_demo",
            password="pass12345",
            email="admin@example.com",
        )
        self.normal_user = User.objects.create_user(
            username="user_demo",
            password="pass12345",
        )
        self.hospital = BenhVien.objects.create(
            ten="BV Admin Test",
            dia_chi="1 Duong Test",
            phuong="Quan 1",
            vi_tri=Point(106.7, 10.77, srid=4326),
            cap_cuu_24h=False,
            co_bhyt=True,
            gio_mo=datetime.time(7, 0),
            gio_dong=datetime.time(17, 0),
            loai_hinh="cong",
        )
        self.department = Khoa.objects.create(
            ten="Khoa Admin Test",
            benh_vien=self.hospital,
        )
        self.doctor = BacSi.objects.create(
            ho_ten="Bac Si Admin Test",
            chuyen_khoa="Noi tong quat",
            khoa=self.department,
            benh_vien=self.hospital,
            so_dien_thoai="0909555666",
        )

    def test_admin_login_redirects_to_custom_dashboard(self):
        response = self.client.post(
            reverse("login"),
            {"username": "admin_demo", "password": "pass12345"},
        )
        self.assertRedirects(response, reverse("custom_admin_dashboard"))

    def test_custom_dashboard_requires_admin(self):
        self.client.login(username="user_demo", password="pass12345")
        response = self.client.get(reverse("custom_admin_dashboard"))
        self.assertRedirects(response, reverse("home"))

    def test_admin_can_open_custom_dashboard(self):
        self.client.login(username="admin_demo", password="pass12345")
        response = self.client.get(reverse("custom_admin_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_admin_can_open_custom_management_pages(self):
        self.client.login(username="admin_demo", password="pass12345")
        self.assertEqual(self.client.get(reverse("custom_admin_hospitals")).status_code, 200)
        self.assertEqual(self.client.get(reverse("custom_admin_departments")).status_code, 200)
        self.assertEqual(self.client.get(reverse("custom_admin_doctors")).status_code, 200)
        self.assertEqual(self.client.get(reverse("custom_admin_model_list", args=["tai-khoan"])).status_code, 200)

    def test_admin_user_form_only_shows_role_field_for_permissions(self):
        self.client.login(username="admin_demo", password="pass12345")
        response = self.client.get(
            reverse("custom_admin_model_edit", args=["tai-khoan", self.admin_user.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="role"')
        self.assertNotContains(response, 'name="is_staff"')
        self.assertNotContains(response, 'name="is_superuser"')

    def test_admin_department_api_returns_departments_by_hospital(self):
        self.client.login(username="admin_demo", password="pass12345")
        response = self.client.get(
            reverse("custom_admin_departments_api"),
            {"benh_vien_id": self.hospital.id},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("results", data)
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["id"], self.department.id)

    def test_non_admin_cannot_use_department_api(self):
        self.client.login(username="user_demo", password="pass12345")
        response = self.client.get(
            reverse("custom_admin_departments_api"),
            {"benh_vien_id": self.hospital.id},
        )
        self.assertEqual(response.status_code, 403)

    def test_doctor_schedule_form_shows_hospital_in_doctor_option(self):
        self.client.login(username="admin_demo", password="pass12345")
        response = self.client.get(reverse("custom_admin_model_create", args=["gio-lam-viec-bac-si"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bac Si Admin Test - BV Admin Test")

    def test_admin_can_create_user_from_custom_admin(self):
        self.client.login(username="admin_demo", password="pass12345")
        response = self.client.post(
            reverse("custom_admin_model_create", args=["tai-khoan"]),
            {
                "username": "staff_from_custom",
                "email": "staff@example.com",
                "first_name": "Staff",
                "last_name": "Custom",
                "is_active": "on",
                "role": "admin",
                "password": "Staff@123456",
            },
        )
        self.assertEqual(response.status_code, 302)
        created_user = User.objects.get(username="staff_from_custom")
        self.assertTrue(created_user.check_password("Staff@123456"))
        self.assertTrue(created_user.is_staff)
        self.assertFalse(created_user.is_superuser)

    def test_admin_edit_own_password_keeps_session(self):
        self.client.login(username="admin_demo", password="pass12345")
        response = self.client.post(
            reverse("custom_admin_model_edit", args=["tai-khoan", self.admin_user.id]),
            {
                "username": "admin_demo",
                "email": "admin@example.com",
                "first_name": "",
                "last_name": "",
                "is_active": "on",
                "role": "admin",
                "password": "NewPass@12345",
            },
        )
        self.assertEqual(response.status_code, 302)
        # Still authenticated after changing own password.
        follow = self.client.get(reverse("custom_admin_model_list", args=["tai-khoan"]))
        self.assertEqual(follow.status_code, 200)
        self.admin_user.refresh_from_db()
        self.assertTrue(self.admin_user.check_password("NewPass@12345"))

    def test_django_admin_url_disabled(self):
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 404)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@test.local",
    CONTACT_RECEIVER_EMAIL="feedback@test.local",
)
class ContactFeedbackTests(TestCase):
    def test_contact_feedback_page_renders(self):
        response = self.client.get(reverse("contact_feedback"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Li&#234;n h&#7879; &amp; G&#243;p &#253;")

    def test_submit_feedback_sends_email(self):
        response = self.client.post(
            reverse("contact_feedback"),
            {
                "ho_ten": "Nguoi dung test",
                "email": "sender@example.com",
                "chu_de": "Gop y giao dien",
                "noi_dung": "Trang web de dung va can them mot vai tinh nang nho.",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "\u0110\u00e3 g\u1eedi g\u00f3p \u00fd th\u00e0nh c\u00f4ng")
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["feedback@test.local"])
        self.assertIn("[G\u00d3P \u00dd] Gop y giao dien", sent.subject)
        self.assertIn("Nguoi dung test", sent.body)
        self.assertIn("sender@example.com", sent.body)

    def test_submit_feedback_invalid_payload(self):
        response = self.client.post(
            reverse("contact_feedback"),
            {
                "ho_ten": "",
                "email": "invalid-email",
                "chu_de": "",
                "noi_dung": "ngan",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)


class CoordinateConstraintTests(TestCase):
    def _benh_vien_kwargs(self, vi_tri):
        return {
            "ten": "BV Toa Do Test",
            "dia_chi": "1 Duong Test",
            "phuong": "Sai Gon",
            "vi_tri": vi_tri,
            "cap_cuu_24h": False,
            "co_bhyt": True,
            "gio_mo": datetime.time(7, 0),
            "gio_dong": datetime.time(17, 0),
            "loai_hinh": "cong",
        }

    def test_benhvien_rejects_out_of_range_wgs84(self):
        benh_vien = BenhVien(**self._benh_vien_kwargs(Point(181, 10, srid=4326)))
        with self.assertRaises(ValidationError):
            benh_vien.full_clean()

    def test_benhvien_accepts_valid_wgs84(self):
        benh_vien = BenhVien(**self._benh_vien_kwargs(Point(106.7, 10.77, srid=4326)))
        benh_vien.full_clean()
        benh_vien.save()
        self.assertEqual(benh_vien.vi_tri.srid, 4326)
        self.assertTrue(-90 <= benh_vien.vi_tri.y <= 90)
        self.assertTrue(-180 <= benh_vien.vi_tri.x <= 180)

    def test_admin_benhvien_form_rejects_invalid_lat_lon(self):
        form = AdminBenhVienForm(data={
            "ten": "BV Form Test",
            "dia_chi": "2 Duong Test",
            "phuong": "Sai Gon",
            "lat": 95,
            "lon": 106.7,
            "co_bhyt": "on",
            "loai_hinh": "cong",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("lat", form.errors)


class AuthFlowTests(TestCase):
    def test_register_creates_patient_linked_to_user(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "0909777888",
                "password": "StrongPass@123",
                "name": "Nguoi Dang Ky",
                "email": "dangky@example.com",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        created_user = User.objects.get(username="0909777888")
        benh_nhan = BenhNhan.objects.get(so_dien_thoai="0909777888")
        self.assertEqual(benh_nhan.user_id, created_user.id)

    def test_forgot_password_rejects_weak_password(self):
        user = User.objects.create_user(
            username="0909555123",
            email="recover@example.com",
            password="OldPass@123",
        )
        response = self.client.post(
            reverse("forgot_password"),
            {
                "username": user.username,
                "email": user.email,
                "new_password": "123",
                "confirm_password": "123",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password("OldPass@123"))
