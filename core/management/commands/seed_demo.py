import datetime

from django.contrib.auth.models import User
from django.core.management import BaseCommand, CommandError, call_command
from django.db import transaction
from django.utils import timezone

from core.models import BacSi, BenhNhan, LichKham, PhieuKham


class Command(BaseCommand):
    help = "Create stable demo presets for presentation."

    def add_arguments(self, parser):
        parser.add_argument(
            "--preset",
            choices=["home", "booking", "doctor"],
            default="home",
            help="Demo preset to build: home, booking, doctor.",
        )
        parser.add_argument(
            "--no-reset",
            action="store_true",
            help="Do not purge/seed base data before applying preset.",
        )

    def handle(self, *args, **options):
        preset = options["preset"]
        no_reset = options["no_reset"]

        if not no_reset:
            self.stdout.write("Resetting core data...")
            call_command("seed_core", purge_only=True, verbosity=0)
            call_command("seed_core", seed_only=True, verbosity=0)

        with transaction.atomic():
            appointments = self._ensure_demo_appointments()
            self._prepare_common(appointments)
            doctor_user = self._ensure_doctor_login()

            if preset == "home":
                self._preset_home(appointments)
            elif preset == "booking":
                self._preset_booking(appointments)
            elif preset == "doctor":
                self._preset_doctor(appointments)
            else:
                raise CommandError("Unsupported preset.")

        self.stdout.write(self.style.SUCCESS(f"Demo preset '{preset}' is ready."))
        self.stdout.write("Demo users:")
        self.stdout.write("- Patient login: 0909000001 / 123456")
        self.stdout.write(f"- Doctor login: {doctor_user.username} / 123456")
        self.stdout.write("Recommended pages:")
        self.stdout.write("- /")
        self.stdout.write("- /lich-kham-sap-toi/")
        self.stdout.write("- /bac-si/")

    def _next_working_day(self):
        day = timezone.localdate() + datetime.timedelta(days=1)
        while (day.weekday() + 1) % 7 == 0:
            day += datetime.timedelta(days=1)
        return day

    def _ensure_demo_appointments(self):
        existing = list(
            LichKham.objects.order_by("ngay_kham", "gio_kham", "id").select_related("benh_nhan", "bac_si")[:3]
        )
        if len(existing) >= 3:
            return existing

        patients = list(BenhNhan.objects.order_by("id")[:3])
        doctors = list(BacSi.objects.order_by("id")[:2])
        if len(patients) < 3 or len(doctors) < 2:
            raise CommandError("Need at least 3 patients and 2 doctors to build demo presets.")

        target_date = self._next_working_day()
        slots = [
            (patients[0], doctors[0], datetime.time(9, 0)),
            (patients[1], doctors[0], datetime.time(10, 0)),
            (patients[2], doctors[1], datetime.time(11, 0)),
        ]
        created = []
        for patient, doctor, preferred in slots:
            hour = self._pick_available_time(doctor, target_date, preferred)
            appt = LichKham(
                benh_nhan=patient,
                bac_si=doctor,
                ngay_kham=target_date,
                gio_kham=hour,
                trang_thai="cho",
                ghi_chu="Demo",
            )
            appt.full_clean()
            appt.save()
            created.append(appt)
        return created

    def _prepare_common(self, appointments):
        target_date = self._next_working_day()
        preferred_times = [datetime.time(9, 0), datetime.time(10, 0), datetime.time(11, 0)]
        for idx, appt in enumerate(appointments):
            safe_time = self._pick_available_time(
                appt.bac_si,
                target_date,
                preferred_times[idx],
                exclude_id=appt.id,
            )
            appt.ngay_kham = target_date
            appt.gio_kham = safe_time
            appt.ghi_chu = "Demo presentation"
            appt.trang_thai = "cho"
            appt.full_clean()
            appt.save(update_fields=["ngay_kham", "gio_kham", "ghi_chu", "trang_thai", "ngay_cap_nhat"])

        PhieuKham.objects.filter(lich_kham__in=appointments).delete()

    def _pick_available_time(self, doctor, date_value, preferred_time, exclude_id=None):
        candidate = datetime.datetime.combine(date_value, preferred_time)
        for _ in range(24):
            time_value = candidate.time().replace(second=0, microsecond=0)
            clash = LichKham.objects.filter(
                bac_si=doctor,
                ngay_kham=date_value,
                trang_thai__in=["cho", "dang", "xong"],
                gio_kham=time_value,
            )
            if exclude_id:
                clash = clash.exclude(id=exclude_id)
            if not clash.exists():
                return time_value
            candidate += datetime.timedelta(minutes=30)
        return preferred_time

    def _ensure_doctor_login(self):
        doctor = BacSi.objects.order_by("id").first()
        if not doctor:
            raise CommandError("No doctor found in database.")

        username = "bsdemo"
        user = User.objects.filter(username=username).first()
        if not user:
            user = User.objects.create_user(
                username=username,
                password="123456",
                first_name="Bác sĩ",
                last_name="Demo",
                email="bsdemo@example.com",
            )
        else:
            user.set_password("123456")
            user.save(update_fields=["password"])

        doctor.user = user
        doctor.save(update_fields=["user"])
        return user

    def _preset_home(self, appointments):
        # Keep all appointments in waiting state for map + listing demo.
        for appt in appointments:
            appt.trang_thai = "cho"
            appt.save(update_fields=["trang_thai", "ngay_cap_nhat"])

    def _preset_booking(self, appointments):
        # 2 waiting, 1 cancelled to demo booking/cancel flow.
        appointments[0].trang_thai = "cho"
        appointments[1].trang_thai = "cho"
        appointments[2].trang_thai = "huy"
        for appt in appointments:
            appt.save(update_fields=["trang_thai", "ngay_cap_nhat"])

    def _preset_doctor(self, appointments):
        # 1 waiting, 1 in-progress, 1 done with exam sheet.
        appointments[0].trang_thai = "cho"
        appointments[1].trang_thai = "dang"
        appointments[2].trang_thai = "xong"
        for appt in appointments:
            appt.save(update_fields=["trang_thai", "ngay_cap_nhat"])

        if not hasattr(appointments[2], "phieu_kham"):
            PhieuKham.objects.create(
                benh_nhan=appointments[2].benh_nhan,
                lich_kham=appointments[2],
                trieu_chung="Đau đầu nhẹ, mất ngủ",
                chan_doan="Rối loạn giấc ngủ",
                huong_dieu_tri="Điều chỉnh giấc ngủ, theo dõi 7 ngày",
            )
