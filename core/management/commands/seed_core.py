import datetime

from django.contrib.auth.models import User
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import (
    BacSi,
    BaoHiemYTe,
    BenhNhan,
    BenhVien,
    GioLamViecBacSi,
    GioLamViecBenhVien,
    Khoa,
    LichKham,
    LichSuKhamBenh,
    LogHeThong,
    LogLichKham,
    PhieuKham,
    ThongBao,
)


class Command(BaseCommand):
    help = "Purge core data and seed sample dataset."

    def add_arguments(self, parser):
        parser.add_argument(
            "--purge-only",
            action="store_true",
            help="Only delete core data, do not seed.",
        )
        parser.add_argument(
            "--seed-only",
            action="store_true",
            help="Only seed data, do not delete existing core data.",
        )

    def handle(self, *args, **options):
        purge_only = options.get("purge_only")
        seed_only = options.get("seed_only")

        if purge_only and seed_only:
            self.stdout.write(self.style.ERROR("Choose only one of --purge-only or --seed-only."))
            return

        if not seed_only:
            self._purge_core()
            if purge_only:
                return

        self._seed()
        self.stdout.write(self.style.SUCCESS("Seed completed."))

    def _purge_core(self):
        self.stdout.write("Deleting core data...")
        for model in [
            LogHeThong,
            LogLichKham,
            ThongBao,
            LichSuKhamBenh,
            PhieuKham,
            LichKham,
            GioLamViecBacSi,
            BacSi,
            GioLamViecBenhVien,
            Khoa,
            BenhVien,
        ]:
            model.objects.all().delete()
        self.stdout.write(self.style.SUCCESS("Core data deleted (patients preserved)."))

    def _seed(self):
        self.stdout.write("Seeding sample data...")

        hospitals = [
            {
                "ten": "Bệnh viện Chợ Rẫy",
                "dia_chi": "201B Nguyễn Chí Thanh",
                "phuong": "Quận 5",
                "coords": (106.6581, 10.7531),
                "cap_cuu_24h": True,
                "co_bhyt": True,
                "gio_mo": datetime.time(6, 0),
                "gio_dong": datetime.time(18, 0),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Thống Nhất",
                "dia_chi": "95 Lý Thường Kiệt",
                "phuong": "Tân Bình",
                "coords": (106.6536, 10.7873),
                "cap_cuu_24h": False,
                "co_bhyt": True,
                "gio_mo": datetime.time(6, 0),
                "gio_dong": datetime.time(18, 0),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Đại học Y Dược",
                "dia_chi": "215 Hồng Bàng",
                "phuong": "Quận 5",
                "coords": (106.6542, 10.7555),
                "cap_cuu_24h": False,
                "co_bhyt": True,
                "gio_mo": datetime.time(7, 0),
                "gio_dong": datetime.time(17, 0),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Nhân dân 115",
                "dia_chi": "527 Sư Vạn Hạnh",
                "phuong": "Quận 10",
                "coords": (106.6676, 10.7746),
                "cap_cuu_24h": True,
                "co_bhyt": True,
                "gio_mo": datetime.time(0, 0),
                "gio_dong": datetime.time(23, 59),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Nhi Đồng 1",
                "dia_chi": "341 Sư Vạn Hạnh",
                "phuong": "Quận 10",
                "coords": (106.6659, 10.7719),
                "cap_cuu_24h": True,
                "co_bhyt": True,
                "gio_mo": datetime.time(6, 0),
                "gio_dong": datetime.time(18, 0),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Nhi Đồng 2",
                "dia_chi": "14 Lý Tự Trọng",
                "phuong": "Quận 1",
                "coords": (106.7036, 10.7813),
                "cap_cuu_24h": False,
                "co_bhyt": True,
                "gio_mo": datetime.time(7, 0),
                "gio_dong": datetime.time(17, 0),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Từ Dũ",
                "dia_chi": "284 Cống Quỳnh",
                "phuong": "Quận 1",
                "coords": (106.6869, 10.7694),
                "cap_cuu_24h": False,
                "co_bhyt": True,
                "gio_mo": datetime.time(6, 0),
                "gio_dong": datetime.time(18, 0),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Bình Dân",
                "dia_chi": "371 Điện Biên Phủ",
                "phuong": "Quận 3",
                "coords": (106.6888, 10.7794),
                "cap_cuu_24h": False,
                "co_bhyt": True,
                "gio_mo": datetime.time(6, 0),
                "gio_dong": datetime.time(18, 0),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Đa khoa Sài Gòn",
                "dia_chi": "125 Lê Lợi",
                "phuong": "Quận 1",
                "coords": (106.7005, 10.7717),
                "cap_cuu_24h": False,
                "co_bhyt": False,
                "gio_mo": datetime.time(7, 30),
                "gio_dong": datetime.time(20, 0),
                "loai_hinh": "tu",
            },
            {
                "ten": "Bệnh viện Nhân dân Gia Định",
                "dia_chi": "1 Nơ Trang Long",
                "phuong": "Bình Thạnh",
                "coords": (106.6942, 10.8125),
                "cap_cuu_24h": True,
                "co_bhyt": True,
                "gio_mo": datetime.time(0, 0),
                "gio_dong": datetime.time(23, 59),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Quân y 175",
                "dia_chi": "786 Nguyễn Kiệm",
                "phuong": "Gò Vấp",
                "coords": (106.6718, 10.8136),
                "cap_cuu_24h": True,
                "co_bhyt": True,
                "gio_mo": datetime.time(0, 0),
                "gio_dong": datetime.time(23, 59),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện 115 (Phối hợp)",
                "dia_chi": "99 Trần Quang Khải",
                "phuong": "Quận 1",
                "coords": (106.6931, 10.7895),
                "cap_cuu_24h": False,
                "co_bhyt": True,
                "gio_mo": datetime.time(7, 0),
                "gio_dong": datetime.time(17, 0),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Mắt TP.HCM",
                "dia_chi": "280 Điện Biên Phủ",
                "phuong": "Quận 3",
                "coords": (106.6860, 10.7849),
                "cap_cuu_24h": False,
                "co_bhyt": True,
                "gio_mo": datetime.time(7, 0),
                "gio_dong": datetime.time(16, 30),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Đa khoa Vạn Hạnh",
                "dia_chi": "624 Sư Vạn Hạnh",
                "phuong": "Quận 10",
                "coords": (106.6664, 10.7732),
                "cap_cuu_24h": False,
                "co_bhyt": False,
                "gio_mo": datetime.time(7, 0),
                "gio_dong": datetime.time(20, 0),
                "loai_hinh": "tu",
            },
            {
                "ten": "Bệnh viện Hoàn Mỹ Sài Gòn",
                "dia_chi": "60 Phan Xích Long",
                "phuong": "Phú Nhuận",
                "coords": (106.6798, 10.7971),
                "cap_cuu_24h": False,
                "co_bhyt": False,
                "gio_mo": datetime.time(7, 0),
                "gio_dong": datetime.time(20, 0),
                "loai_hinh": "tu",
            },
            {
                "ten": "Bệnh viện FV",
                "dia_chi": "6 Nguyễn Lương Bằng",
                "phuong": "Quận 7",
                "coords": (106.7185, 10.7289),
                "cap_cuu_24h": True,
                "co_bhyt": False,
                "gio_mo": datetime.time(0, 0),
                "gio_dong": datetime.time(23, 59),
                "loai_hinh": "qt",
            },
            {
                "ten": "Bệnh viện Tâm Anh",
                "dia_chi": "2B Phổ Quang",
                "phuong": "Tân Bình",
                "coords": (106.6746, 10.8057),
                "cap_cuu_24h": False,
                "co_bhyt": False,
                "gio_mo": datetime.time(7, 0),
                "gio_dong": datetime.time(20, 0),
                "loai_hinh": "tu",
            },
            {
                "ten": "Bệnh viện Hùng Vương",
                "dia_chi": "128 Hồng Bàng",
                "phuong": "Quận 5",
                "coords": (106.6652, 10.7521),
                "cap_cuu_24h": False,
                "co_bhyt": True,
                "gio_mo": datetime.time(6, 0),
                "gio_dong": datetime.time(18, 0),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Đa khoa Thủ Đức",
                "dia_chi": "64 Lê Văn Chí",
                "phuong": "Thủ Đức",
                "coords": (106.7615, 10.8543),
                "cap_cuu_24h": True,
                "co_bhyt": True,
                "gio_mo": datetime.time(0, 0),
                "gio_dong": datetime.time(23, 59),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Quận 2",
                "dia_chi": "130 Lê Văn Thịnh",
                "phuong": "Thủ Đức",
                "coords": (106.7591, 10.7907),
                "cap_cuu_24h": False,
                "co_bhyt": True,
                "gio_mo": datetime.time(7, 0),
                "gio_dong": datetime.time(17, 0),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Quận 11",
                "dia_chi": "72 Số 5",
                "phuong": "Quận 11",
                "coords": (106.6506, 10.7615),
                "cap_cuu_24h": False,
                "co_bhyt": True,
                "gio_mo": datetime.time(7, 0),
                "gio_dong": datetime.time(17, 0),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Lê Văn Thịnh",
                "dia_chi": "130 Lê Văn Thịnh",
                "phuong": "Thủ Đức",
                "coords": (106.7591, 10.7907),
                "cap_cuu_24h": False,
                "co_bhyt": True,
                "gio_mo": datetime.time(7, 0),
                "gio_dong": datetime.time(17, 0),
                "loai_hinh": "cong",
            },
            {
                "ten": "Bệnh viện Đa khoa Quận 4",
                "dia_chi": "65 Bến Vân Đồn",
                "phuong": "Quận 4",
                "coords": (106.7021, 10.7544),
                "cap_cuu_24h": False,
                "co_bhyt": True,
                "gio_mo": datetime.time(7, 0),
                "gio_dong": datetime.time(17, 0),
                "loai_hinh": "cong",
            },
        ]

        department_names = ["Nội tổng quát", "Ngoại tổng quát", "Tim mạch"]

        all_benh_vien = []
        all_khoa = []
        all_bac_si = []

        for data in hospitals:
            bv = BenhVien.objects.create(
                ten=data["ten"],
                dia_chi=data["dia_chi"],
                phuong=data["phuong"],
                vi_tri=Point(data["coords"][0], data["coords"][1], srid=4326),
                cap_cuu_24h=data["cap_cuu_24h"],
                co_bhyt=data["co_bhyt"],
                gio_mo=data["gio_mo"],
                gio_dong=data["gio_dong"],
                loai_hinh=data["loai_hinh"],
            )
            all_benh_vien.append(bv)

            for thu in range(7):
                nghi = thu == 0
                GioLamViecBenhVien.objects.create(
                    benh_vien=bv,
                    thu=thu,
                    gio_mo=datetime.time(0, 0) if nghi else data["gio_mo"],
                    gio_dong=datetime.time(0, 0) if nghi else data["gio_dong"],
                    nghi=nghi,
                )

            for dept_name in department_names:
                all_khoa.append(Khoa.objects.create(ten=dept_name, benh_vien=bv))

        phone_base = 907000000
        for idx, khoa in enumerate(all_khoa, start=1):
            for n in range(2):
                phone = str(phone_base + idx * 10 + n)
                bac_si = BacSi.objects.create(
                    ho_ten=f"BS {khoa.ten} {n + 1}",
                    chuyen_khoa=khoa.ten,
                    khoa=khoa,
                    benh_vien=khoa.benh_vien,
                    so_dien_thoai=phone,
                )
                all_bac_si.append(bac_si)

                for thu in range(7):
                    nghi = thu == 0
                    GioLamViecBacSi.objects.create(
                        bac_si=bac_si,
                        thu=thu,
                        gio_bat_dau=datetime.time(0, 0) if nghi else datetime.time(8, 0),
                        gio_ket_thuc=datetime.time(0, 0) if nghi else datetime.time(17, 0),
                        nghi=nghi,
                    )

        today = timezone.localdate()
        patient_data = [
            ("Nguyễn Văn An", "0909000001", datetime.date(1995, 5, 12), "nam", "TP.HCM"),
            ("Trần Thị Bích", "0909000002", datetime.date(1998, 9, 3), "nu", "TP.HCM"),
            ("Lê Hoàng Long", "0909000003", datetime.date(1992, 2, 20), "nam", "TP.HCM"),
            ("Phạm Minh Châu", "0909000004", datetime.date(1990, 12, 1), "nu", "TP.HCM"),
            ("Đoàn Quốc Huy", "0909000005", datetime.date(1988, 7, 8), "nam", "TP.HCM"),
        ]

        all_benh_nhan = []
        for i, (name, phone, birth, gender, address) in enumerate(patient_data, start=1):
            email = f"bn{i}@example.com"
            user = User.objects.filter(username=phone).first()
            if not user:
                user = User.objects.create_user(username=phone, password="123456", email=email)
            elif not user.email:
                user.email = email
                user.save()

            bhyt = None
            if i <= 3:
                bhyt, _ = BaoHiemYTe.objects.update_or_create(
                    ma_bhyt=f"BHYT{i:04d}",
                    defaults={
                        "ngay_cap": today - datetime.timedelta(days=30),
                        "ngay_het_han": today + datetime.timedelta(days=365),
                    },
                )

            benh_nhan, _ = BenhNhan.objects.update_or_create(
                so_dien_thoai=phone,
                defaults={
                    "user": user,
                    "ho_ten": name,
                    "ngay_sinh": birth,
                    "gioi_tinh": gender,
                    "dia_chi": address,
                    "bhyt": bhyt,
                },
            )
            all_benh_nhan.append(benh_nhan)

        appointment_date = today + datetime.timedelta(days=1)
        while (appointment_date.weekday() + 1) % 7 == 0:
            appointment_date += datetime.timedelta(days=1)

        sample_appointments = [
            (all_benh_nhan[0], all_bac_si[0], datetime.time(9, 0)),
            (all_benh_nhan[1], all_bac_si[0], datetime.time(9, 30)),
            (all_benh_nhan[2], all_bac_si[1], datetime.time(10, 0)),
        ]

        for benh_nhan, bac_si, gio in sample_appointments:
            lich = LichKham(
                benh_nhan=benh_nhan,
                bac_si=bac_si,
                ngay_kham=appointment_date,
                gio_kham=gio,
                ghi_chu="Kham tong quat",
            )
            lich.full_clean()
            lich.save()
 
 