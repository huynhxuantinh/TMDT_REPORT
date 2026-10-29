from django.shortcuts import render, get_object_or_404, redirect
import datetime
import math
import secrets
import re
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.forms import modelform_factory
from django.http import Http404, JsonResponse
from django.db import IntegrityError, transaction
from django.views.decorators.http import require_POST
from django.db.models.deletion import ProtectedError
from django.db.models import Max, Q
from django.db import models as db_models
from django.contrib.gis.geos import Point
from django.contrib.gis.db.models.functions import Distance
from .models import BaoHiemYTe

from .models import (
    BenhVien,
    BenhVienHinhAnh,
    BenhNhan,
    BacSi,
    Khoa,
    GioLamViecBenhVien,
    GioLamViecBacSi,
    LichKham,
    LichSuKhamBenh,
    PhieuKham,
    DichVuThucHien,
    PhieuKhamHinhAnh,
    LogLichKham,
    LogHeThong,
    ThongBao,
    NoiDungGioiThieu,
)

from .forms import (
    AdminBacSiForm,
    AdminBenhVienForm,
    AdminGioLamViecBacSiForm,
    AdminKhoaForm,
    AdminUserForm,
    ContactFeedbackForm,
    DatLichForm,
)
from .billing.services import billing_context as exam_billing_context
from .payments.access import can_admin


# ==========================
# TRANG CHỦ
# ==========================
def _patient_for_user(user):
    patient = BenhNhan.objects.filter(user=user).first()
    if patient is None:
        patient = BenhNhan.objects.filter(user__isnull=True, so_dien_thoai=user.username).first()
    return patient


def home(request):

    bvs = BenhVien.objects.all().prefetch_related("gio_lam_viecs")
    unread_count = 0
    display_name = None
    doctor_info = None
    doctor_today = []
    user_lat = None
    user_lon = None
    radius_km = None
    # GIS filters: open/emergency/BHYT + distance/radius + map markers
    filter_open = request.GET.get("open") == "1"
    filter_emergency = request.GET.get("emergency") == "1"
    filter_bhyt = request.GET.get("bhyt") == "1"
    filter_cap_cuu_24h = request.GET.get("cap_cuu_24h") == "1"
    loai_hinh = request.GET.get("loai_hinh")
    phuong_filter = (request.GET.get("phuong") or request.GET.get("quan") or "").strip()
    search_query = request.GET.get("q", "").strip()

    all_phuong = (
        BenhVien.objects.values_list("phuong", flat=True)
        .distinct()
        .order_by("phuong")
    )

    lat_str = request.GET.get("lat")
    lon_str = request.GET.get("lon")
    radius_str = request.GET.get("radius")

    user_point = None
    if lat_str and lon_str:
        try:
            user_lat = float(lat_str)
            user_lon = float(lon_str)
            if -90 <= user_lat <= 90 and -180 <= user_lon <= 180:
                # GIS: build user location (SRID 4326) for distance/radius filtering
                user_point = Point(user_lon, user_lat, srid=4326)
        except ValueError:
            user_point = None

    if radius_str:
        try:
            radius_km = float(radius_str)
            if radius_km <= 0:
                radius_km = None
        except ValueError:
            radius_km = None

    if loai_hinh in {"cong", "tu", "qt"}:
        bvs = bvs.filter(loai_hinh=loai_hinh)
    else:
        loai_hinh = None

    if phuong_filter:
        bvs = bvs.filter(phuong=phuong_filter)
    if search_query:
        bvs = bvs.filter(
            Q(ten__icontains=search_query)
            | Q(dia_chi__icontains=search_query)
            | Q(phuong__icontains=search_query)
        )

    if request.user.is_authenticated:
        unread_count = ThongBao.objects.filter(
            nguoi_nhan=request.user,
            da_doc=False,
        ).count()
        try:
            bac_si = request.user.bac_si
        except BacSi.DoesNotExist:
            bac_si = None
        if bac_si:
            display_name = bac_si.ho_ten
            doctor_info = {
                "khoa": bac_si.khoa.ten,
                "benh_vien": bac_si.benh_vien.ten,
            }
            today = timezone.localdate()
            doctor_today = list(
                LichKham.objects.filter(
                    bac_si=bac_si,
                    ngay_kham=today,
                )
                .select_related("benh_nhan")
                .order_by("gio_kham")
            )
            for lich in doctor_today:
                lich.ca_label = "Ca sáng" if lich.gio_kham < datetime.time(12, 0) else "Ca chiều"
        else:
            benh_nhan = _patient_for_user(request.user)
            display_name = benh_nhan.ho_ten if benh_nhan else request.user.username

    query_params = request.GET.copy()
    query_params.pop("page", None)
    query_string_without_page = query_params.urlencode()

    def paginate_hospitals(items):
        paginator = Paginator(items, 12)
        page_obj = paginator.get_page(request.GET.get("page"))
        return paginator, page_obj, list(page_obj.object_list)

    now = timezone.localtime()
    time_bucket = now.strftime("%Y%m%d%H%M")
    cache_key = (
        f"home_filter:"
        f"lat={user_lat}|lon={user_lon}|radius={radius_km}|"
        f"open={int(filter_open)}|emg={int(filter_emergency)}|"
        f"bhyt={int(filter_bhyt)}|capcuu247={int(filter_cap_cuu_24h)}|"
        f"loai={loai_hinh or 'all'}|phuong={phuong_filter or 'all'}|q={search_query.lower()}|t={time_bucket}"
    )
    cached = cache.get(cache_key)
    if cached:
        ids = cached.get("ids", [])
        computed_map = cached.get("computed", {})
        bvs_qs = BenhVien.objects.filter(id__in=ids).prefetch_related("gio_lam_viecs")
        bvs_map = {bv.id: bv for bv in bvs_qs}
        cached_bvs = []
        for bv_id in ids:
            bv = bvs_map.get(bv_id)
            if not bv:
                continue
            info = computed_map.get(str(bv_id)) or computed_map.get(bv_id, {})
            bv.map_lat = info.get("map_lat")
            bv.map_lon = info.get("map_lon")
            bv.is_open = info.get("is_open", False)
            bv.emergency_active = info.get("emergency_active", False)
            bv.distance_km = info.get("distance_km")
            cached_bvs.append(bv)
        bvs = cached_bvs

        total_bvs = len(bvs)
        paginator, page_obj, paged_bvs = paginate_hospitals(bvs)

        map_data = []
        for bv in paged_bvs:
            if bv.map_lat is None or bv.map_lon is None:
                continue
            map_data.append({
                "id": bv.id,
                "name": bv.ten,
                "phuong": bv.phuong,
                "lat": bv.map_lat,
                "lon": bv.map_lon,
                "open": bv.is_open,
                "emergency": bv.emergency_active,
                "distance_km": bv.distance_km,
            })

        return render(request, "core/home.html", {
            "bvs": paged_bvs,
            "total_bvs": total_bvs,
            "paginator": paginator,
            "page_obj": page_obj,
            "query_string_without_page": query_string_without_page,
            "unread_count": unread_count,
            "display_name": display_name,
            "doctor_info": doctor_info,
            "doctor_today": doctor_today,
            "user_lat": user_lat,
            "user_lon": user_lon,
            "radius_km": radius_km,
            "filter_bhyt": filter_bhyt,
            "filter_cap_cuu_24h": filter_cap_cuu_24h,
            "filter_open": filter_open,
            "filter_emergency": filter_emergency,
            "loai_hinh": loai_hinh,
            "phuong_filter": phuong_filter,
            "q": search_query,
            "all_phuong": all_phuong,
            "map_data": map_data,
        })

    if filter_bhyt:
        bvs = bvs.filter(co_bhyt=True)
    if filter_cap_cuu_24h:
        bvs = bvs.filter(cap_cuu_24h=True)

    if user_point:
        # GIS: annotate distance from user, filter by radius, sort nearest first
        bvs = bvs.annotate(distance_m=Distance("vi_tri", user_point, spheroid=False))
        if radius_km:
            bvs = bvs.filter(distance_m__lte=radius_km * 1000)
        bvs = bvs.order_by("distance_m")

    def point_to_latlon(point):
        # GIS: convert Point to lat/lon (handle Web Mercator or WGS84)
        if not point:
            return None, None
        x = point.x
        y = point.y
        is_web_mercator = point.srid == 3857 or abs(x) > 180 or abs(y) > 90
        if is_web_mercator:
            max_merc = 20037508.34
            world_width = max_merc * 2
            if x > max_merc or x < -max_merc:
                x = ((x + max_merc) % world_width) - max_merc
            if y > max_merc:
                y = max_merc
            elif y < -max_merc:
                y = -max_merc
            lon = x * 180.0 / 20037508.34
            lat = y * 180.0 / 20037508.34
            lat = 180.0 / math.pi * (2 * math.atan(math.exp(lat * math.pi / 180.0)) - math.pi / 2)
            if abs(lat) <= 90 and abs(lon) <= 180:
                return lat, lon
            return None, None
        return y, x

    thu = (now.weekday() + 1) % 7
    computed_bvs = []
    for bv in bvs:
        gio_lam = None
        for glv in bv.gio_lam_viecs.all():
            if glv.thu == thu:
                gio_lam = glv
                break

        if gio_lam:
            if gio_lam.nghi:
                is_open = False
            else:
                is_open = gio_lam.gio_mo <= now.time() <= gio_lam.gio_dong
        else:
            is_open = bv.gio_mo <= now.time() <= bv.gio_dong

        emergency_active = bv.cap_cuu_24h

        map_lat, map_lon = point_to_latlon(bv.vi_tri) if bv.vi_tri else (None, None)
        setattr(bv, "map_lat", map_lat)
        setattr(bv, "map_lon", map_lon)
        setattr(bv, "is_open", is_open)
        setattr(bv, "emergency_active", emergency_active)

        distance_km = None
        if hasattr(bv, "distance_m") and bv.distance_m is not None:
            if hasattr(bv.distance_m, "km"):
                distance_km = round(bv.distance_m.km, 2)
            elif hasattr(bv.distance_m, "m"):
                distance_km = round(bv.distance_m.m / 1000.0, 2)
            else:
                distance_km = round(float(bv.distance_m) / 1000.0, 2)
        setattr(bv, "distance_km", distance_km)
        if filter_open and not bv.is_open:
            continue
        if filter_emergency and not bv.emergency_active:
            continue
        computed_bvs.append(bv)

    if user_point:
        computed_bvs.sort(
            key=lambda item: (
                item.distance_km is None,
                item.distance_km if item.distance_km is not None else 1e9,
                item.ten.lower(),
            )
        )
    else:
        computed_bvs.sort(
            key=lambda item: (
                not getattr(item, "is_open", False),
                item.ten.lower(),
            )
        )
    bvs = computed_bvs

    total_bvs = len(bvs)
    paginator, page_obj, paged_bvs = paginate_hospitals(bvs)

    map_data = []
    for bv in paged_bvs:
        if bv.map_lat is None or bv.map_lon is None:
            continue
        map_data.append({
            "id": bv.id,
            "name": bv.ten,
            "phuong": bv.phuong,
            "lat": bv.map_lat,
            "lon": bv.map_lon,
            "open": bv.is_open,
            "emergency": bv.emergency_active,
            "distance_km": bv.distance_km,
        })

    computed_payload = {}
    for bv in bvs:
        computed_payload[str(bv.id)] = {
            "map_lat": getattr(bv, "map_lat", None),
            "map_lon": getattr(bv, "map_lon", None),
            "is_open": getattr(bv, "is_open", False),
            "emergency_active": getattr(bv, "emergency_active", False),
            "distance_km": getattr(bv, "distance_km", None),
        }
    cache.set(cache_key, {
        "ids": [bv.id for bv in bvs],
        "computed": computed_payload,
    }, timeout=60)

    return render(request, "core/home.html", {
        "bvs": paged_bvs,
        "total_bvs": total_bvs,
        "paginator": paginator,
        "page_obj": page_obj,
        "query_string_without_page": query_string_without_page,
        "unread_count": unread_count,
        "display_name": display_name,
        "doctor_info": doctor_info,
        "doctor_today": doctor_today,
        "user_lat": user_lat,
        "user_lon": user_lon,
        "radius_km": radius_km,
        "filter_bhyt": filter_bhyt,
        "filter_cap_cuu_24h": filter_cap_cuu_24h,
        "filter_open": filter_open,
        "filter_emergency": filter_emergency,
        "loai_hinh": loai_hinh,
        "phuong_filter": phuong_filter,
        "q": search_query,
        "all_phuong": all_phuong,
        "map_data": map_data,
    })


# ==========================
# GIOI THIEU
# ==========================
def gioi_thieu(request):
    content, _ = NoiDungGioiThieu.objects.get_or_create(thu_tu=1)
    bvs = BenhVien.objects.only("id", "ten", "phuong", "vi_tri").order_by("ten")

    def point_to_latlon(point):
        if not point:
            return None, None
        x = point.x
        y = point.y
        is_web_mercator = point.srid == 3857 or abs(x) > 180 or abs(y) > 90
        if is_web_mercator:
            max_merc = 20037508.34
            world_width = max_merc * 2
            if x > max_merc or x < -max_merc:
                x = ((x + max_merc) % world_width) - max_merc
            if y > max_merc:
                y = max_merc
            elif y < -max_merc:
                y = -max_merc
            lon = x * 180.0 / 20037508.34
            lat = y * 180.0 / 20037508.34
            lat = 180.0 / math.pi * (2 * math.atan(math.exp(lat * math.pi / 180.0)) - math.pi / 2)
            if abs(lat) <= 90 and abs(lon) <= 180:
                return lat, lon
            return None, None
        return y, x

    map_data = []
    for bv in bvs:
        lat, lon = point_to_latlon(bv.vi_tri) if bv.vi_tri else (None, None)
        if lat is None or lon is None:
            continue
        map_data.append({
            "id": bv.id,
            "name": bv.ten,
            "phuong": bv.phuong,
            "lat": lat,
            "lon": lon,
        })

    return render(request, "core/about.html", {
        "content": content,
        "features": [line.strip() for line in content.ds_chuc_nang.splitlines() if line.strip()],
        "gis_components": [line.strip() for line in content.ds_thanh_phan_gis.splitlines() if line.strip()],
        "tech_tags": [line.strip() for line in content.ds_cong_nghe.splitlines() if line.strip()],
        "map_data": map_data,
        "total_hospitals": len(map_data),
    })


# ==========================
# CHI TIẾT BỆNH VIỆN
# ==========================
def hospital_detail(request, id):

    bv = get_object_or_404(
        BenhVien.objects.prefetch_related("hinh_anhs"),
        id=id,
    )

    khoas = bv.khoas.all()
    bac_sis = bv.bac_sis.all()
    gio_lam_viec = GioLamViecBenhVien.objects.filter(benh_vien=bv).order_by("thu")
    hospital_images = list(bv.hinh_anhs.all())
    primary_image = next((img for img in hospital_images if img.la_anh_dai_dien), None)
    if not primary_image and hospital_images:
        primary_image = hospital_images[0]
    map_lat = None
    map_lon = None
    if bv.vi_tri:
        x = bv.vi_tri.x
        y = bv.vi_tri.y

        is_web_mercator = bv.vi_tri.srid == 3857 or abs(x) > 180 or abs(y) > 90
        if is_web_mercator:
            max_merc = 20037508.34
            world_width = max_merc * 2
            if x > max_merc or x < -max_merc:
                x = ((x + max_merc) % world_width) - max_merc
            if y > max_merc:
                y = max_merc
            elif y < -max_merc:
                y = -max_merc
            lon = x * 180.0 / 20037508.34
            lat = y * 180.0 / 20037508.34
            lat = 180.0 / math.pi * (2 * math.atan(math.exp(lat * math.pi / 180.0)) - math.pi / 2)
            map_lat, map_lon = lat, lon
        else:
            map_lat, map_lon = y, x

        if map_lat is not None and map_lon is not None:
            if abs(map_lat) > 90 or abs(map_lon) > 180:
                map_lat = None
                map_lon = None

    return render(request, "core/hospital_detail.html", {
        "bv": bv,
        "khoas": khoas,
        "bac_sis": bac_sis,
        "gio_lam_viec": gio_lam_viec,
        "hospital_images": hospital_images,
        "primary_image": primary_image,
        "map_lat": map_lat,
        "map_lon": map_lon,
    })


# ==========================
# ĐẶT LỊCH KHÁM (BỆNH NHÂN)
# ==========================
@login_required
def dat_lich(request, bv_id):

    benh_vien = get_object_or_404(BenhVien, id=bv_id)

    benh_nhan = _patient_for_user(request.user)

    if not benh_nhan:
        return render(request, "core/error.html", {
            "msg": "Bạn chưa có hồ sơ bệnh nhân"
        })

    khoas = benh_vien.khoas.all().order_by("ten")
    khoa_id = (request.POST.get("khoa") or request.GET.get("khoa") or "").strip()

    selected_khoa = None
    if khoa_id:
        selected_khoa = benh_vien.khoas.filter(id=khoa_id).first()
        if not selected_khoa:
            khoa_id = ""

    bac_sis = benh_vien.bac_sis.none()
    bac_si_schedules = []
    if selected_khoa:
        bac_sis = benh_vien.bac_sis.filter(khoa=selected_khoa).prefetch_related("gio_lam_viecs")
        bac_si_schedules = [
            {
                "bac_si": bs,
                "gio_lam_viecs": bs.gio_lam_viecs.all().order_by("thu"),
            }
            for bs in bac_sis
        ]

    if request.method == "POST":
        form = DatLichForm(request.POST)
        form.fields["bac_si"].queryset = bac_sis

        if not selected_khoa:
            form.add_error(None, "Vui lòng chọn khoa trước khi đặt lịch.")
            messages.error(request, "Không thể đặt lịch. Vui lòng kiểm tra lại thông tin.")
            return render(request, "core/appointment.html", {
                "form": form,
                "benh_vien": benh_vien,
                "benh_nhan": benh_nhan,
                "khoas": khoas,
                "bac_sis": bac_sis,
                "bac_si_schedules": bac_si_schedules,
                "khoa_id": khoa_id,
            })

        if form.is_valid():
            lich = form.save(commit=False)
            lich.benh_nhan = benh_nhan

            try:
                lich.full_clean()
                lich.save()
            except ValidationError as exc:
                for field, errors in exc.message_dict.items():
                    if field in form.fields:
                        for error in errors:
                            form.add_error(field, error)
                    else:
                        for error in errors:
                            form.add_error(None, error)
                messages.error(request, "Không thể đặt lịch. Vui lòng kiểm tra lại thông tin.")
                return render(request, "core/appointment.html", {
                    "form": form,
                    "benh_vien": benh_vien,
                    "benh_nhan": benh_nhan,
                    "khoas": khoas,
                    "bac_sis": bac_sis,
                    "bac_si_schedules": bac_si_schedules,
                    "khoa_id": khoa_id,
                })
            except IntegrityError:
                form.add_error("ngay_kham", "Bạn đã có lịch khám trong ngày này.")
                messages.error(request, "Không thể đặt lịch. Vui lòng kiểm tra lại thông tin.")
                return render(request, "core/appointment.html", {
                    "form": form,
                    "benh_vien": benh_vien,
                    "benh_nhan": benh_nhan,
                    "khoas": khoas,
                    "bac_sis": bac_sis,
                    "bac_si_schedules": bac_si_schedules,
                    "khoa_id": khoa_id,
                })

            messages.success(request, "Đặt lịch thành công.")
            ThongBao.objects.create(
                nguoi_nhan=request.user,
                loai="lich_kham",
                tieu_de="Đặt lịch thành công",
                noi_dung=(
                    f"Tạo lịch khám vào ngày {lich.ngay_kham} lúc {lich.gio_kham} với bác sĩ {lich.bac_si.ho_ten}."
                ),
                lien_ket="/lich-kham-sap-toi/",
            )
            return redirect("upcoming_appointments")

        messages.error(request, "Không thể đặt lịch. Vui lòng kiểm tra lại thông tin.")
    else:
        form = DatLichForm()
        form.fields["bac_si"].queryset = bac_sis

    return render(request, "core/appointment.html", {
        "form": form,
        "benh_vien": benh_vien,
        "benh_nhan": benh_nhan,
        "khoas": khoas,
        "bac_sis": bac_sis,
        "bac_si_schedules": bac_si_schedules,
        "khoa_id": khoa_id,
    })


@login_required
def upcoming_appointments(request):
    benh_nhan = _patient_for_user(request.user)

    if not benh_nhan:
        return render(request, "core/error.html", {
            "msg": "Bạn chưa có hồ sơ bệnh nhân"
        })

    today = timezone.localdate()
    all_upcoming = LichKham.objects.filter(
        benh_nhan=benh_nhan,
        ngay_kham__gte=today,
    ).order_by("ngay_kham", "gio_kham")

    lich_khams = all_upcoming.exclude(trang_thai="huy").exclude(trang_thai="xong")
    lich_huy = all_upcoming.filter(trang_thai="huy")

    now = timezone.localtime()
    window_end = now + datetime.timedelta(hours=24)
    tz = timezone.get_current_timezone()
    for lich in lich_khams.filter(trang_thai="cho"):
        lich_dt = datetime.datetime.combine(lich.ngay_kham, lich.gio_kham)
        lich_dt = timezone.make_aware(lich_dt, tz)
        if now <= lich_dt <= window_end:
            ThongBao.objects.get_or_create(
                nguoi_nhan=request.user,
                loai="nhac_nho",
                lien_ket=f"/lich-kham-sap-toi/?lich={lich.id}",
                defaults={
                    "tieu_de": "Nhắc lịch khám",
                    "noi_dung": (
                        f"Bạn có lịch khám vào ngày {lich.ngay_kham} lúc {lich.gio_kham} với bác sĩ {lich.bac_si.ho_ten}."
                    ),
                },
            )

    return render(request, "core/upcoming_appointments.html", {
        "benh_nhan": benh_nhan,
        "lich_khams": lich_khams,
        "lich_huy": lich_huy,
    })


# ==========================
# HỦY LỊCH KHÁM (BỆNH NHÂN)
# ==========================
@login_required
def cancel_appointment(request, lich_id):
    if request.method != "POST":
        return redirect("upcoming_appointments")

    benh_nhan = _patient_for_user(request.user)

    if not benh_nhan:
        return render(request, "core/error.html", {
            "msg": "Bạn chưa có hồ sơ bệnh nhân"
        })

    lich = get_object_or_404(LichKham, id=lich_id, benh_nhan=benh_nhan)

    if lich.trang_thai == "huy":
        messages.error(request, "Lịch khám đã bị hủy trước đó.")
        return redirect("upcoming_appointments")

    if lich.trang_thai in ("dang", "xong"):
        messages.error(request, "Không thể hủy lịch đang khám hoặc đã hoàn thành.")
        return redirect("upcoming_appointments")

    now = timezone.localtime()
    if lich.ngay_kham < now.date() or (
        lich.ngay_kham == now.date() and lich.gio_kham <= now.time()
    ):
        messages.error(request, "Không thể hủy lịch đã đến giờ khám.")
        return redirect("upcoming_appointments")

    lich.trang_thai = "huy"
    lich.save()
    ThongBao.objects.create(
        nguoi_nhan=request.user,
        loai="he_thong",
        tieu_de="Đã hủy lịch khám",
        noi_dung=(
            f"Hủy lịch khám vào ngày {lich.ngay_kham} lúc {lich.gio_kham} với bác sĩ {lich.bac_si.ho_ten}."
        ),
        lien_ket="/lich-kham-sap-toi/",
    )

    messages.success(request, "Đã hủy lịch khám thành công.")
    return redirect("upcoming_appointments")

# ==========================
# LỊCH SỬ KHÁM (BỆNH NHÂN)
# ==========================
@login_required
def medical_history(request):
    benh_nhan = _patient_for_user(request.user)

    if not benh_nhan:
        return render(request, "core/error.html", {
            "msg": "Bạn chưa có hồ sơ bệnh nhân"
        })

    lich_su = (
        LichSuKhamBenh.objects.filter(benh_nhan=benh_nhan)
        .select_related("bac_si", "benh_vien", "phieu_kham")
        .order_by("-ngay_kham", "-ngay_tao")
    )

    return render(request, "core/medical_history.html", {
        "benh_nhan": benh_nhan,
        "lich_su": lich_su,
    })


# ==========================
# CHI TIẾT PHIẾU KHÁM
# ==========================
@login_required
def phieu_kham_detail(request, phieu_id):
    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        bac_si = None

    if bac_si:
        phieu = get_object_or_404(
            PhieuKham.objects.prefetch_related("hinh_anhs", "dich_vu_thuc_hien__dich_vu"),
            id=phieu_id,
            lich_kham__bac_si=bac_si,
        )

        edit_logs = LogHeThong.objects.filter(
            model_name="PhieuKham",
            object_id=phieu.id,
            hanh_dong="update",
        ).select_related("nguoi_thuc_hien").order_by("-thoi_gian")

        return render(request, "core/exam_detail.html", {
            "benh_nhan": phieu.benh_nhan,
            "phieu": phieu,
            "edit_logs": edit_logs,
            "exam_images": list(phieu.hinh_anhs.all()),
        })

    benh_nhan = _patient_for_user(request.user)

    if not benh_nhan:
        return render(request, "core/error.html", {
            "msg": "Bạn chưa có hồ sơ bệnh nhân"
        })

    phieu = get_object_or_404(
        PhieuKham.objects.prefetch_related("hinh_anhs", "dich_vu_thuc_hien__dich_vu"),
        id=phieu_id,
        benh_nhan=benh_nhan,
    )

    edit_logs = LogHeThong.objects.filter(
        model_name="PhieuKham",
        object_id=phieu.id,
        hanh_dong="update",
    ).select_related("nguoi_thuc_hien").order_by("-thoi_gian")

    return render(request, "core/exam_detail.html", {
        "benh_nhan": benh_nhan,
        "phieu": phieu,
        "edit_logs": edit_logs,
        "exam_images": list(phieu.hinh_anhs.all()),
        **exam_billing_context(phieu),
    })


# ==========================
# DASHBOARD BÁC SĨ
# ==========================
@login_required
def doctor_dashboard(request):

    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")


    lich_khams = LichKham.objects.filter(
        bac_si=bac_si
    ).order_by("ngay_kham", "gio_kham")


    return render(request, "core/doctor_dashboard.html", {
        "bac_si": bac_si,
        "lich_khams": lich_khams
    })


# ==========================
# KHÁM BỆNH (TẠO PHIẾU)
# ==========================
@login_required
def doctor_exam(request, lich_id):

    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")


    lich = get_object_or_404(
        LichKham,
        id=lich_id,
        bac_si=bac_si
    )

    if lich.trang_thai == "huy":
        messages.error(request, "Lịch đã hủy, không thể khám.")
        return redirect("bac_si_home")

    if hasattr(lich, "phieu_kham"):
        if lich.trang_thai != "xong":
            lich.trang_thai = "xong"
            lich.save(update_fields=["trang_thai", "ngay_cap_nhat"])
        messages.info(request, "Lịch khám này đã có phiếu khám.")
        return redirect("bac_si_home")

    if request.method != "POST" and lich.trang_thai == "cho":
        lich.trang_thai = "dang"
        lich.save(update_fields=["trang_thai", "ngay_cap_nhat"])

    clinical_values = {name + "_value": request.POST.get(name, "") for name in ("trieu_chung", "chan_doan", "huong_dieu_tri")}

    def render_exam():
        return render(request, "core/doctor_exam.html", {"lich": lich, **clinical_values})

    if request.method == "POST":

        trieu_chung = request.POST.get("trieu_chung", "").strip()
        chan_doan = request.POST.get("chan_doan", "").strip()
        huong_dieu_tri = request.POST.get("huong_dieu_tri", "").strip()
        uploaded_images = request.FILES.getlist("images")

        if not trieu_chung or not chan_doan or not huong_dieu_tri:
            messages.error(request, "Vui lòng nhập đầy đủ thông tin phiếu khám.")
            return render_exam()
        try:
            with transaction.atomic():
                lich = get_object_or_404(LichKham.objects.select_for_update(), id=lich_id, bac_si=bac_si)
                if lich.trang_thai == "huy" or PhieuKham.objects.filter(lich_kham=lich).exists():
                    raise ValidationError("Lịch khám đã hủy hoặc đã có phiếu khám. Vui lòng tải lại trang.")
                phieu = PhieuKham.objects.create(
                    benh_nhan=lich.benh_nhan,
                    lich_kham=lich,
                    trieu_chung=trieu_chung,
                    chan_doan=chan_doan,
                    huong_dieu_tri=huong_dieu_tri
                )
                for idx, image in enumerate(uploaded_images):
                    hinh_anh = PhieuKhamHinhAnh(
                        phieu_kham=phieu,
                        hinh_anh=image,
                        thu_tu=idx,
                    )
                    hinh_anh.full_clean()
                    hinh_anh.save()

                lich.trang_thai = "xong"
                lich.save()
        except ValidationError:
            messages.error(request, "Ảnh tải lên không hợp lệ. Vui lòng dùng JPG, PNG hoặc WEBP.")
            return render_exam()

        messages.success(request, "Đã hoàn thành khám. Quản lý chi nhánh sẽ ghi nhận dịch vụ và tính chi phí.")

        return redirect("bac_si_home")

    return render_exam()

# ==========================
# ĐĂNG KÝ
# ==========================
def register(request):

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        name = request.POST.get("name", "").strip()
        email = request.POST.get("email", "").strip()

        if not name or not username or not password:
            messages.error(request, "Vui lòng nhập đầy đủ họ tên, tên người dùng và mật khẩu.")
            return redirect("register")

        # Keep phone-number usernames used by existing accounts/clients.
        legacy_phone = bool(re.fullmatch(r"[0-9]{9,15}", username))
        if len(username) > 150 or not (legacy_phone or re.fullmatch(r"(?=.*[A-Za-z])(?=.*[0-9])[A-Za-z0-9]+", username)):
            messages.error(request, "Tên người dùng phải gồm chữ và số không dấu, hoặc số điện thoại.")
            return redirect("register")

        phone = request.POST.get("phone", "").strip() or (username if legacy_phone else "")
        try:
            validate_email(email)
            validate_password(password, user=User(username=username, email=email))
            if User.objects.filter(email__iexact=email).exists():
                raise ValidationError("Email đã được sử dụng.")
            with transaction.atomic():
                user = User(username=username, email=email)
                user.set_password(password)
                user.full_clean()
                user.save()
                patient = BenhNhan(user=user, ho_ten=name, so_dien_thoai=phone,
                                   ngay_sinh="2000-01-01", gioi_tinh="nam", dia_chi="Chưa cập nhật")
                patient.full_clean()
                patient.save()
        except ValidationError as exc:
            for error in exc.messages:
                messages.error(request, error)
            return redirect("register")
        except IntegrityError:
            messages.error(request, "Tài khoản hoặc số điện thoại đã được sử dụng.")
            return redirect("register")

        messages.success(request, "Đăng ký thành công")

        return redirect("login")


    return render(request, "core/register.html")


# ==========================
# ĐĂNG NHẬP
# ==========================
def user_login(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        if not username:
            messages.error(request, "Vui lòng nhập tên người dùng.")
            return render(request, "core/login.html")

        user = authenticate(
            request,
            username=username,
            password=password
        )

        if user:
            login(request, user)

            if hasattr(user, "quan_ly_chi_nhanh"):
                return redirect("payment_list")

            if user.is_superuser or user.is_staff:
                return redirect("custom_admin_dashboard")
            # Nếu là bác sĩ
            if hasattr(user, "bac_si"):
                return redirect("bac_si_home")

            # Nếu là bệnh nhân
            return redirect("home")

        messages.error(request, "Sai tài khoản hoặc mật khẩu")

    return render(request, "core/login.html")


# ==========================
# ĐĂNG XUẤT
# ==========================
@login_required
def custom_admin_dashboard(request):
    if not can_admin(request.user):
        return redirect("home")

    status_counts = {
        "cho": LichKham.objects.filter(trang_thai="cho").count(),
        "dang": LichKham.objects.filter(trang_thai="dang").count(),
        "xong": LichKham.objects.filter(trang_thai="xong").count(),
        "huy": LichKham.objects.filter(trang_thai="huy").count(),
    }

    latest_appointments = (
        LichKham.objects.select_related("benh_nhan", "bac_si", "bac_si__benh_vien")
        .order_by("-ngay_tao", "-id")[:10]
    )
    latest_hospitals = BenhVien.objects.order_by("-id")[:10]
    latest_doctors = BacSi.objects.select_related("benh_vien", "khoa").order_by("-id")[:10]
    admin_models = [
        {"key": key, "label": cfg["title"], "count": cfg["model"].objects.count()}
        for key, cfg in ADMIN_MODEL_CONFIG.items()
    ]

    context = {
        "stats": {
            "benh_vien": BenhVien.objects.count(),
            "khoa": Khoa.objects.count(),
            "bac_si": BacSi.objects.count(),
            "benh_nhan": BenhNhan.objects.count(),
            "lich_kham": LichKham.objects.count(),
            "phieu_kham": PhieuKham.objects.count(),
        },
        "status_counts": status_counts,
        "latest_appointments": latest_appointments,
        "latest_hospitals": latest_hospitals,
        "latest_doctors": latest_doctors,
        "admin_models": admin_models,
    }
    return render(request, "core/custom_admin_dashboard.html", context)


ADMIN_MODEL_CONFIG = {
    "noi-dung-gioi-thieu": {
        "title": "Nội dung giới thiệu",
        "model": NoiDungGioiThieu,
        "list_display": ("tieu_de_trang", "ngay_cap_nhat"),
        "ordering": ("thu_tu",),
    },
    "benh-vien": {
        "title": "Bệnh viện",
        "model": BenhVien,
        "form_class": AdminBenhVienForm,
        "list_display": ("ten", "phuong", "loai_hinh", "cap_cuu_24h", "co_bhyt", "gio_mo", "gio_dong"),
        "search_fields": ("ten", "dia_chi", "phuong"),
        "list_filter": ("phuong", "loai_hinh", "cap_cuu_24h", "co_bhyt"),
        "ordering": ("ten",),
    },
    "gio-lam-viec-benh-vien": {
        "title": "Giờ làm việc bệnh viện",
        "model": GioLamViecBenhVien,
        "list_display": ("benh_vien", "thu", "gio_mo", "gio_dong", "nghi"),
        "list_filter": ("thu", "nghi", "benh_vien"),
        "ordering": ("benh_vien", "thu"),
    },
    "khoa": {
        "title": "Khoa",
        "model": Khoa,
        "form_class": AdminKhoaForm,
        "list_display": ("ten", "benh_vien"),
        "search_fields": ("ten", "benh_vien__ten"),
        "list_filter": ("benh_vien",),
        "ordering": ("ten",),
    },
    "bac-si": {
        "title": "Bác sĩ",
        "model": BacSi,
        "form_class": AdminBacSiForm,
        "list_display": ("ho_ten", "chuyen_khoa", "khoa", "benh_vien", "so_dien_thoai"),
        "search_fields": ("ho_ten", "chuyen_khoa", "so_dien_thoai", "khoa__ten", "benh_vien__ten"),
        "list_filter": ("benh_vien", "khoa", "chuyen_khoa"),
        "ordering": ("ho_ten",),
    },
    "tai-khoan": {
        "title": "Tài khoản",
        "model": User,
        "form_class": AdminUserForm,
        "list_display": ("username", "email", "first_name", "last_name", "is_staff", "is_superuser", "is_active"),
        "search_fields": ("username", "email", "first_name", "last_name"),
        "list_filter": ("is_staff", "is_superuser", "is_active"),
        "ordering": ("-date_joined",),
    },
    "gio-lam-viec-bac-si": {
        "title": "Giờ làm việc bác sĩ",
        "model": GioLamViecBacSi,
        "form_class": AdminGioLamViecBacSiForm,
        "list_display": ("bac_si", "thu", "gio_bat_dau", "gio_ket_thuc", "nghi"),
        "list_filter": ("thu", "nghi", "bac_si"),
        "ordering": ("bac_si", "thu"),
    },
    "bao-hiem-y-te": {
        "title": "Bảo hiểm y tế",
        "model": BaoHiemYTe,
        "list_display": ("ma_bhyt", "ngay_cap", "ngay_het_han"),
        "search_fields": ("ma_bhyt",),
        "list_filter": ("ngay_het_han",),
        "ordering": ("-ngay_het_han",),
    },
    "benh-nhan": {
        "title": "Bệnh nhân",
        "model": BenhNhan,
        "list_display": ("ho_ten", "ngay_sinh", "gioi_tinh", "so_dien_thoai", "bhyt"),
        "search_fields": ("ho_ten", "so_dien_thoai", "dia_chi", "bhyt__ma_bhyt"),
        "list_filter": ("gioi_tinh",),
        "ordering": ("ho_ten",),
    },
    "lich-kham": {
        "title": "Lịch khám",
        "model": LichKham,
        "list_display": ("benh_nhan", "bac_si", "ngay_kham", "gio_kham", "trang_thai"),
        "search_fields": ("benh_nhan__ho_ten", "bac_si__ho_ten", "ghi_chu"),
        "list_filter": ("trang_thai", "ngay_kham", "bac_si", "benh_nhan"),
        "ordering": ("-ngay_kham", "-gio_kham"),
    },
    "phieu-kham": {
        "title": "Phiếu khám",
        "model": PhieuKham,
        "list_display": ("benh_nhan", "lich_kham", "ngay_lap"),
        "search_fields": ("benh_nhan__ho_ten", "chan_doan", "trieu_chung"),
        "list_filter": ("ngay_lap",),
        "ordering": ("-ngay_lap",),
    },
    "phieu-kham-hinh-anh": {
        "title": "Hình ảnh phiếu khám",
        "model": PhieuKhamHinhAnh,
        "list_display": ("phieu_kham", "thu_tu", "ngay_tao"),
        "search_fields": ("phieu_kham__benh_nhan__ho_ten", "mo_ta"),
        "list_filter": ("ngay_tao",),
        "ordering": ("-ngay_tao",),
    },
    "lich-su-kham-benh": {
        "title": "Lịch sử khám bệnh",
        "model": LichSuKhamBenh,
        "list_display": ("benh_nhan", "bac_si", "benh_vien", "ngay_kham", "ngay_tao"),
        "search_fields": ("benh_nhan__ho_ten", "bac_si__ho_ten", "chan_doan", "trieu_chung"),
        "list_filter": ("benh_vien", "bac_si", "ngay_kham"),
        "ordering": ("-ngay_kham", "-ngay_tao"),
    },
    "log-lich-kham": {
        "title": "Log lịch khám",
        "model": LogLichKham,
        "list_display": ("lich_kham", "hanh_dong", "trang_thai_cu", "trang_thai_moi", "nguoi_thuc_hien", "thoi_gian"),
        "search_fields": ("mo_ta", "lich_kham__benh_nhan__ho_ten", "lich_kham__bac_si__ho_ten"),
        "list_filter": ("hanh_dong", "thoi_gian"),
        "ordering": ("-thoi_gian",),
    },
    "log-he-thong": {
        "title": "Log hệ thống",
        "model": LogHeThong,
        "list_display": ("model_name", "object_id", "hanh_dong", "nguoi_thuc_hien", "thoi_gian"),
        "search_fields": ("model_name", "object_id", "ghi_chu"),
        "list_filter": ("model_name", "hanh_dong", "thoi_gian"),
        "ordering": ("-thoi_gian",),
    },
    "thong-bao": {
        "title": "Thông báo",
        "model": ThongBao,
        "list_display": ("tieu_de", "loai", "nguoi_nhan", "da_doc", "thoi_gian"),
        "search_fields": ("tieu_de", "noi_dung", "nguoi_nhan__username"),
        "list_filter": ("loai", "da_doc", "thoi_gian"),
        "ordering": ("-thoi_gian",),
    },
}


def _admin_access_or_redirect(request):
    if not can_admin(request.user):
        return redirect("home")
    return None


def _require_account_superuser(request, target=None, *, deleting=False):
    """The custom account directory is reserved for superusers only."""
    if not request.user.is_superuser:
        raise PermissionDenied('Chỉ superuser được quản trị tài khoản.')
    if target and target.is_superuser:
        raise PermissionDenied('Không được sửa hoặc xóa tài khoản superuser qua màn hình này.')
    if deleting and target and target.pk == request.user.pk:
        raise PermissionDenied('Không được tự xóa tài khoản đang đăng nhập.')


def _delete_impact(model_key, obj):
    """Counts records that must be preserved before a destructive admin action."""
    if model_key == 'benh-vien':
        return [
            ('Khoa', Khoa.objects.filter(benh_vien=obj).count()),
            ('Bác sĩ', BacSi.objects.filter(benh_vien=obj).count()),
            ('Lịch khám', LichKham.objects.filter(bac_si__benh_vien=obj).count()),
            ('Phiếu khám', PhieuKham.objects.filter(lich_kham__bac_si__benh_vien=obj).count()),
            ('Ảnh bệnh viện', BenhVienHinhAnh.objects.filter(benh_vien=obj).count()),
        ]
    if model_key == 'khoa':
        return [
            ('Bác sĩ', BacSi.objects.filter(khoa=obj).count()),
            ('Lịch khám', LichKham.objects.filter(bac_si__khoa=obj).count()),
            ('Phiếu khám', PhieuKham.objects.filter(lich_kham__bac_si__khoa=obj).count()),
        ]
    if model_key == 'bac-si':
        return [
            ('Lịch khám', LichKham.objects.filter(bac_si=obj).count()),
            ('Phiếu khám', PhieuKham.objects.filter(lich_kham__bac_si=obj).count()),
            ('Lịch làm việc', GioLamViecBacSi.objects.filter(bac_si=obj).count()),
        ]
    if model_key == 'tai-khoan':
        doctor = BacSi.objects.filter(user=obj)
        return [
            ('Hồ sơ bác sĩ', doctor.count()),
            ('Lịch khám', LichKham.objects.filter(bac_si__user=obj).count()),
            ('Phiếu khám', PhieuKham.objects.filter(lich_kham__bac_si__user=obj).count()),
        ]
    if model_key == 'lich-kham':
        return [('Phiếu khám', PhieuKham.objects.filter(lich_kham=obj).count()), ('Nhật ký lịch khám', LogLichKham.objects.filter(lich_kham=obj).count())]
    if model_key == 'phieu-kham':
        return [('Dịch vụ thực hiện', DichVuThucHien.objects.filter(phieu_kham=obj).count()), ('Ảnh phiếu khám', PhieuKhamHinhAnh.objects.filter(phieu_kham=obj).count())]
    return []


def _get_admin_config(model_key):
    config = ADMIN_MODEL_CONFIG.get(model_key)
    if not config:
        raise Http404("Model không tồn tại")
    return config


def _get_admin_form_class(config):
    form_class = config.get("form_class")
    if form_class:
        return form_class
    return modelform_factory(config["model"], fields="__all__")


def _format_admin_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Có" if value else "Không"
    if isinstance(value, datetime.datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, datetime.date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, datetime.time):
        return value.strftime("%H:%M")
    return str(value)


def _resolve_path_value(obj, path):
    current = obj
    for part in path.split("__"):
        current = getattr(current, part, None)
        if current is None:
            return None
    return current


ADMIN_FIELD_LABELS = {
    "id": "ID",
    "ten": "Tên",
    "dia_chi": "Địa chỉ",
    "phuong": "Phường",
    "loai_hinh": "Loại hình",
    "cap_cuu_24h": "Cấp cứu 24/7",
    "co_bhyt": "Có BHYT",
    "gio_mo": "Giờ mở",
    "gio_dong": "Giờ đóng",
    "benh_vien": "Bệnh viện",
    "khoa": "Khoa",
    "bac_si": "Bác sĩ",
    "benh_nhan": "Bệnh nhân",
    "chuyen_khoa": "Chuyên khoa",
    "so_dien_thoai": "Số điện thoại",
    "thu": "Thứ",
    "gio_bat_dau": "Giờ bắt đầu",
    "gio_ket_thuc": "Giờ kết thúc",
    "nghi": "Nghỉ",
    "ma_bhyt": "Mã BHYT",
    "ngay_cap": "Ngày cấp",
    "ngay_het_han": "Ngày hết hạn",
    "ngay_sinh": "Ngày sinh",
    "gioi_tinh": "Giới tính",
    "bhyt": "BHYT",
    "ngay_kham": "Ngày khám",
    "gio_kham": "Giờ khám",
    "trang_thai": "Trạng thái",
    "lich_kham": "Lịch khám",
    "phieu_kham": "Phiếu khám",
    "hinh_anh": "Hình ảnh",
    "mo_ta": "Mô tả",
    "thu_tu": "Thứ tự",
    "ngay_lap": "Ngày lập",
    "ngay_tao": "Ngày tạo",
    "hanh_dong": "Hành động",
    "trang_thai_cu": "Trạng thái cũ",
    "trang_thai_moi": "Trạng thái mới",
    "nguoi_thuc_hien": "Người thực hiện",
    "thoi_gian": "Thời gian",
    "model_name": "Tên model",
    "object_id": "ID đối tượng",
    "ghi_chu": "Ghi chú",
    "tieu_de": "Tiêu đề",
    "loai": "Loại",
    "nguoi_nhan": "Người nhận",
    "da_doc": "Đã đọc",
    "user": "Tài khoản",
    "ho_ten": "Họ tên",
    "username": "Tên đăng nhập",
    "email": "Email",
    "first_name": "Tên",
    "last_name": "Họ",
    "is_staff": "Nhân viên",
    "is_superuser": "Quản trị cao nhất",
    "is_active": "Đang hoạt động",
    "date_joined": "Ngày tạo",
    "password": "Mật khẩu",
    "tieu_de_trang": "Tiêu đề trang",
    "nut_lien_he": "Nút liên hệ",
    "nut_kham_pha": "Nút khám phá",
    "tieu_de_gioi_thieu": "Tiêu đề giới thiệu",
    "noi_dung_gioi_thieu": "Nội dung giới thiệu",
    "tieu_de_van_de": "Tiêu đề vấn đề & mục tiêu",
    "noi_dung_van_de": "Nội dung vấn đề & mục tiêu",
    "tieu_de_chuc_nang": "Tiêu đề chức năng chính",
    "ds_chuc_nang": "Danh sách chức năng",
    "tieu_de_gis": "Tiêu đề thành phần GIS",
    "ds_thanh_phan_gis": "Danh sách thành phần GIS",
    "tieu_de_cong_nghe": "Tiêu đề công nghệ",
    "ds_cong_nghe": "Danh sách công nghệ",
    "tieu_de_cta": "Tiêu đề CTA",
    "mo_ta_cta": "Mô tả CTA",
    "thu_tu": "Thứ tự",
    "ngay_cap_nhat": "Ngày cập nhật",
}


def _label_from_path(path):
    parts = path.split("__")
    labels = [ADMIN_FIELD_LABELS.get(part, part.replace("_", " ").capitalize()) for part in parts]
    return " / ".join(labels)


def _list_column_label(model, field_name):
    if "__" in field_name:
        return _label_from_path(field_name)
    try:
        if field_name in ADMIN_FIELD_LABELS:
            return ADMIN_FIELD_LABELS[field_name]
        return str(model._meta.get_field(field_name).verbose_name).replace("_", " ").capitalize()
    except Exception:
        return ADMIN_FIELD_LABELS.get(field_name, field_name.replace("_", " ").capitalize())


def _list_column_value(obj, field_name):
    if "__" not in field_name:
        display_fn = f"get_{field_name}_display"
        if hasattr(obj, display_fn):
            try:
                return _format_admin_value(getattr(obj, display_fn)())
            except Exception:
                pass
    return _format_admin_value(_resolve_path_value(obj, field_name))


def _apply_admin_filters(queryset, model, list_filter, params):
    for filter_name in list_filter:
        raw_value = params.get(filter_name, "").strip()
        if not raw_value:
            continue
        try:
            field = model._meta.get_field(filter_name)
        except Exception:
            continue
        try:
            if isinstance(field, db_models.BooleanField):
                value = raw_value.lower() in {"1", "true", "yes", "co"}
                queryset = queryset.filter(**{filter_name: value})
            elif field.is_relation:
                queryset = queryset.filter(**{f"{filter_name}_id": raw_value})
            elif isinstance(field, db_models.DateTimeField):
                queryset = queryset.filter(**{f"{filter_name}__date": raw_value})
            else:
                queryset = queryset.filter(**{filter_name: raw_value})
        except Exception:
            continue
    return queryset


def _build_filter_meta(model, list_filter, params):
    filter_meta = []
    for filter_name in list_filter:
        try:
            field = model._meta.get_field(filter_name)
        except Exception:
            continue
        selected = params.get(filter_name, "").strip()
        item = {
            "name": filter_name,
            "label": _list_column_label(model, filter_name),
            "selected": selected,
            "type": "text",
            "options": [],
        }
        if isinstance(field, db_models.BooleanField):
            item["type"] = "select"
            item["options"] = [
                {"value": "1", "label": "Có"},
                {"value": "0", "label": "Không"},
            ]
        elif field.choices:
            item["type"] = "select"
            item["options"] = [{"value": str(v), "label": str(l)} for v, l in field.choices]
        elif field.is_relation:
            item["type"] = "select"
            rel_qs = field.related_model.objects.all().order_by("id")[:300]
            item["options"] = [{"value": str(obj.pk), "label": str(obj)} for obj in rel_qs]
        elif isinstance(field, (db_models.DateField, db_models.DateTimeField)):
            item["type"] = "date"
        filter_meta.append(item)
    return filter_meta


ALLOWED_HOSPITAL_IMAGE_EXTS = {"jpg", "jpeg", "png", "webp"}
MAX_HOSPITAL_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
ALLOWED_EXAM_IMAGE_EXTS = {"jpg", "jpeg", "png", "webp"}
MAX_EXAM_IMAGE_SIZE_BYTES = 5 * 1024 * 1024


def _parse_int_set(values):
    result = set()
    for value in values:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _validate_hospital_images(uploaded_images):
    errors = []
    for uploaded in uploaded_images:
        name = uploaded.name or "tep_khong_ten"
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED_HOSPITAL_IMAGE_EXTS:
            errors.append(f"Ảnh '{name}' không đúng định dạng (jpg, jpeg, png, webp).")
            continue
        if uploaded.size and uploaded.size > MAX_HOSPITAL_IMAGE_SIZE_BYTES:
            errors.append(f"Ảnh '{name}' vượt quá 5MB.")
            continue
        content_type = (getattr(uploaded, "content_type", "") or "").lower()
        if content_type and not content_type.startswith("image/"):
            errors.append(f"Tệp '{name}' không phải ảnh hợp lệ.")
    return errors


def _validate_exam_images(uploaded_images):
    errors = []
    for uploaded in uploaded_images:
        name = uploaded.name or "tep_khong_ten"
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED_EXAM_IMAGE_EXTS:
            errors.append(f"Ảnh '{name}' không đúng định dạng (jpg, jpeg, png, webp).")
            continue
        if uploaded.size and uploaded.size > MAX_EXAM_IMAGE_SIZE_BYTES:
            errors.append(f"Ảnh '{name}' vượt quá 5MB.")
            continue
        content_type = (getattr(uploaded, "content_type", "") or "").lower()
        if content_type and not content_type.startswith("image/"):
            errors.append(f"Tệp '{name}' không phải ảnh hợp lệ.")
    return errors


def _sync_hospital_images(hospital, uploaded_images, delete_ids=None, cover_image_id=None):
    delete_ids = delete_ids or set()

    if delete_ids:
        BenhVienHinhAnh.objects.filter(benh_vien=hospital, id__in=delete_ids).delete()

    if uploaded_images:
        current_max = (
            BenhVienHinhAnh.objects.filter(benh_vien=hospital)
            .aggregate(value=Max("thu_tu"))
            .get("value")
            or 0
        )
        new_items = []
        for idx, uploaded in enumerate(uploaded_images, start=1):
            new_items.append(
                BenhVienHinhAnh(
                    benh_vien=hospital,
                    hinh_anh=uploaded,
                    thu_tu=current_max + idx,
                    la_anh_dai_dien=False,
                )
            )
        BenhVienHinhAnh.objects.bulk_create(new_items)

    images = list(
        BenhVienHinhAnh.objects.filter(benh_vien=hospital).order_by("thu_tu", "id")
    )
    if not images:
        return

    valid_ids = {img.id for img in images}
    target_cover_id = cover_image_id if cover_image_id in valid_ids else None

    if target_cover_id is None:
        current_cover = next((img for img in images if img.la_anh_dai_dien), None)
        if current_cover:
            target_cover_id = current_cover.id
        else:
            target_cover_id = images[0].id

    BenhVienHinhAnh.objects.filter(benh_vien=hospital).update(la_anh_dai_dien=False)
    BenhVienHinhAnh.objects.filter(benh_vien=hospital, id=target_cover_id).update(la_anh_dai_dien=True)


def _sync_exam_images(phieu, uploaded_images, delete_ids=None):
    delete_ids = delete_ids or set()

    if delete_ids:
        PhieuKhamHinhAnh.objects.filter(phieu_kham=phieu, id__in=delete_ids).delete()

    if not uploaded_images:
        return

    current_max = (
        PhieuKhamHinhAnh.objects.filter(phieu_kham=phieu)
        .aggregate(value=Max("thu_tu"))
        .get("value")
        or 0
    )

    for idx, uploaded in enumerate(uploaded_images, start=1):
        item = PhieuKhamHinhAnh(
            phieu_kham=phieu,
            hinh_anh=uploaded,
            thu_tu=current_max + idx,
        )
        item.full_clean()
        item.save()


HOSPITAL_WEEKDAY_CHOICES = [
    (1, "Thứ 2"),
    (2, "Thứ 3"),
    (3, "Thứ 4"),
    (4, "Thứ 5"),
    (5, "Thứ 6"),
    (6, "Thứ 7"),
    (0, "Chủ nhật"),
]


def _parse_hhmm(value):
    if not value:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def _build_hospital_schedule_rows(request, hospital=None):
    existing = {}
    if hospital and hospital.pk:
        existing = {item.thu: item for item in hospital.gio_lam_viecs.all()}

    fallback_open = hospital.gio_mo if hospital and hospital.gio_mo else datetime.time(7, 0)
    fallback_close = hospital.gio_dong if hospital and hospital.gio_dong else datetime.time(17, 0)

    rows = []
    errors = []
    is_post = request.method == "POST"

    for thu, label in HOSPITAL_WEEKDAY_CHOICES:
        current = existing.get(thu)
        default_open = current.gio_mo if current else fallback_open
        default_close = current.gio_dong if current else fallback_close
        default_nghi = current.nghi if current else False

        if is_post:
            gio_mo_raw = request.POST.get(f"schedule_{thu}_gio_mo", "").strip() or default_open.strftime("%H:%M")
            gio_dong_raw = request.POST.get(f"schedule_{thu}_gio_dong", "").strip() or default_close.strftime("%H:%M")
            nghi = request.POST.get(f"schedule_{thu}_nghi") in {"1", "on", "true", "True"}
        else:
            gio_mo_raw = default_open.strftime("%H:%M")
            gio_dong_raw = default_close.strftime("%H:%M")
            nghi = default_nghi

        gio_mo = _parse_hhmm(gio_mo_raw)
        gio_dong = _parse_hhmm(gio_dong_raw)

        if not gio_mo or not gio_dong:
            errors.append(f"{label}: Giờ mở/giờ đóng không hợp lệ.")
        elif not nghi and gio_mo >= gio_dong:
            errors.append(f"{label}: Giờ đóng phải sau giờ mở.")

        rows.append({
            "thu": thu,
            "label": label,
            "gio_mo_raw": gio_mo_raw,
            "gio_dong_raw": gio_dong_raw,
            "nghi": nghi,
            "gio_mo": gio_mo,
            "gio_dong": gio_dong,
        })

    return rows, errors


def _save_hospital_schedule_rows(hospital, rows):
    for row in rows:
        GioLamViecBenhVien.objects.update_or_create(
            benh_vien=hospital,
            thu=row["thu"],
            defaults={
                "gio_mo": row["gio_mo"],
                "gio_dong": row["gio_dong"],
                "nghi": row["nghi"],
            },
        )


@login_required
def custom_admin_model_list(request, model_key):
    denied = _admin_access_or_redirect(request)
    if denied:
        return denied

    config = _get_admin_config(model_key)
    if model_key == 'tai-khoan':
        _require_account_superuser(request)
    model = config["model"]
    queryset = model.objects.all()

    keyword = request.GET.get("q", "").strip()
    search_fields = config.get("search_fields", ())
    if keyword and search_fields:
        search_q = Q()
        for field_name in search_fields:
            search_q |= Q(**{f"{field_name}__icontains": keyword})
        queryset = queryset.filter(search_q)

    list_filter = config.get("list_filter", ())
    queryset = _apply_admin_filters(queryset, model, list_filter, request.GET)

    ordering = config.get("ordering", ("-id",))
    queryset = queryset.order_by(*ordering)

    page_obj = Paginator(queryset, 25).get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    query_string = query_params.urlencode()
    list_display = config.get("list_display", ("id",))
    columns = [{"name": c, "label": _list_column_label(model, c)} for c in list_display]
    rows = [
        {
            "id": obj.pk,
            "values": [_list_column_value(obj, col) for col in list_display],
            "can_manage": not (model_key == 'tai-khoan' and obj.is_superuser),
        }
        for obj in page_obj.object_list
    ]

    context = {
        "title": f"Quản trị {config['title']}",
        "model_key": model_key,
        "model_label": config["title"],
        "q": keyword,
        "columns": columns,
        "rows": rows,
        "filter_meta": _build_filter_meta(model, list_filter, request.GET),
        "page_obj": page_obj,
        "query_string": query_string,
        "admin_models": [
            {"key": key, "label": cfg["title"]}
            for key, cfg in ADMIN_MODEL_CONFIG.items()
        ],
    }
    return render(request, "core/custom_admin_model_list.html", context)


@login_required
def custom_admin_model_create(request, model_key):
    denied = _admin_access_or_redirect(request)
    if denied:
        return denied
    config = _get_admin_config(model_key)
    if model_key == 'tai-khoan':
        _require_account_superuser(request)
    form_class = _get_admin_form_class(config)
    is_hospital = model_key == "benh-vien"
    is_exam_record = model_key == "phieu-kham"

    form = form_class(request.POST or None, request.FILES or None)
    schedule_rows = []
    schedule_errors = []
    uploaded_images = []
    upload_errors = []
    exam_uploaded_images = []
    exam_upload_errors = []
    if is_hospital:
        schedule_rows, schedule_errors = _build_hospital_schedule_rows(request, None)
        if request.method == "POST":
            uploaded_images = request.FILES.getlist("hospital_images")
            upload_errors = _validate_hospital_images(uploaded_images)
    elif is_exam_record and request.method == "POST":
        exam_uploaded_images = request.FILES.getlist("exam_images")
        exam_upload_errors = _validate_exam_images(exam_uploaded_images)

    if request.method == "POST":
        for err in schedule_errors:
            form.add_error(None, err)
        for err in upload_errors:
            form.add_error(None, err)
        for err in exam_upload_errors:
            form.add_error(None, err)

        if form.is_valid():
            with transaction.atomic():
                obj = form.save()
                if is_hospital:
                    _save_hospital_schedule_rows(obj, schedule_rows)
                    _sync_hospital_images(obj, uploaded_images)
                elif is_exam_record:
                    _sync_exam_images(obj, exam_uploaded_images)
            messages.success(request, f"Đã tạo {config['title'].lower()}.")
            return redirect("custom_admin_model_list", model_key=model_key)

    return render(request, "core/custom_admin_form.html", {
        "title": f"Tạo {config['title']}",
        "form": form,
        "back_href": f"/quan-tri/du-lieu/{model_key}/",
        "show_map_picker": is_hospital,
        "show_hospital_layout": is_hospital,
        "show_hospital_schedule": is_hospital,
        "show_hospital_image_manager": is_hospital,
        "show_exam_image_manager": is_exam_record,
        "hospital_images": [],
        "exam_images": [],
        "selected_delete_ids": set(),
        "selected_delete_exam_ids": set(),
        "selected_cover_id": "",
        "schedule_rows": schedule_rows,
        "google_maps_api_key": settings.GOOGLE_MAPS_API_KEY,
    })


@login_required
def custom_admin_model_edit(request, model_key, pk):
    denied = _admin_access_or_redirect(request)
    if denied:
        return denied
    config = _get_admin_config(model_key)
    model = config["model"]
    obj = get_object_or_404(model, pk=pk)
    if model_key == 'tai-khoan':
        _require_account_superuser(request, obj)
    form_class = _get_admin_form_class(config)
    is_hospital = model_key == "benh-vien"
    is_exam_record = model_key == "phieu-kham"

    form = form_class(request.POST or None, request.FILES or None, instance=obj)
    schedule_rows = []
    schedule_errors = []
    uploaded_images = []
    upload_errors = []
    selected_delete_ids = set()
    selected_cover_id = ""
    exam_uploaded_images = []
    exam_upload_errors = []
    selected_delete_exam_ids = set()
    if is_hospital:
        schedule_rows, schedule_errors = _build_hospital_schedule_rows(request, obj)
        if request.method == "POST":
            uploaded_images = request.FILES.getlist("hospital_images")
            upload_errors = _validate_hospital_images(uploaded_images)
            selected_delete_ids = _parse_int_set(request.POST.getlist("delete_image_ids"))
            selected_cover_id_raw = (request.POST.get("cover_image_id") or "").strip()
            if selected_cover_id_raw:
                try:
                    selected_cover_id = int(selected_cover_id_raw)
                except ValueError:
                    selected_cover_id = ""
                    form.add_error(None, "Ảnh đại diện không hợp lệ.")
                if selected_cover_id and selected_cover_id in selected_delete_ids:
                    form.add_error(None, "Không thể chọn ảnh vừa đánh dấu xóa làm ảnh đại diện.")
    elif is_exam_record and request.method == "POST":
        exam_uploaded_images = request.FILES.getlist("exam_images")
        exam_upload_errors = _validate_exam_images(exam_uploaded_images)
        selected_delete_exam_ids = _parse_int_set(request.POST.getlist("delete_exam_image_ids"))

    if request.method == "POST":
        for err in schedule_errors:
            form.add_error(None, err)
        for err in upload_errors:
            form.add_error(None, err)
        for err in exam_upload_errors:
            form.add_error(None, err)

        if form.is_valid():
            with transaction.atomic():
                updated_obj = form.save()
                if is_hospital:
                    _save_hospital_schedule_rows(updated_obj, schedule_rows)
                    _sync_hospital_images(
                        updated_obj,
                        uploaded_images,
                        delete_ids=selected_delete_ids,
                        cover_image_id=selected_cover_id if selected_cover_id else None,
                    )
                elif is_exam_record:
                    _sync_exam_images(
                        updated_obj,
                        exam_uploaded_images,
                        delete_ids=selected_delete_exam_ids,
                    )
            if isinstance(updated_obj, User) and request.user.pk == updated_obj.pk and form.cleaned_data.get("password"):
                # Keep current admin session alive when changing own password.
                update_session_auth_hash(request, updated_obj)
            messages.success(request, f"Đã cập nhật {config['title'].lower()}.")
            return redirect("custom_admin_model_list", model_key=model_key)

    return render(request, "core/custom_admin_form.html", {
        "title": f"Sửa {config['title']}",
        "form": form,
        "object_name": str(obj),
        "back_href": f"/quan-tri/du-lieu/{model_key}/",
        "show_map_picker": is_hospital,
        "show_hospital_layout": is_hospital,
        "show_hospital_schedule": is_hospital,
        "show_hospital_image_manager": is_hospital,
        "show_exam_image_manager": is_exam_record,
        "hospital_images": list(obj.hinh_anhs.all()) if is_hospital else [],
        "exam_images": list(obj.hinh_anhs.all()) if is_exam_record else [],
        "selected_delete_ids": selected_delete_ids,
        "selected_delete_exam_ids": selected_delete_exam_ids,
        "selected_cover_id": str(selected_cover_id) if selected_cover_id else "",
        "schedule_rows": schedule_rows,
        "google_maps_api_key": settings.GOOGLE_MAPS_API_KEY,
    })


@login_required
def custom_admin_model_delete(request, model_key, pk):
    denied = _admin_access_or_redirect(request)
    if denied:
        return denied
    config = _get_admin_config(model_key)
    obj = get_object_or_404(config["model"], pk=pk)
    if model_key == 'tai-khoan':
        _require_account_superuser(request, obj, deleting=True)
    try:
        impact = [(label, count) for label, count in _delete_impact(model_key, obj) if count]
        if request.method != "POST":
            return render(request, "core/custom_admin_delete_confirm.html", {
                "title": f"Xóa {config['title'].lower()}", "model_key": model_key,
                "object_name": str(obj), "impact": impact,
                "can_delete": not impact,
                "back_href": f"/quan-tri/du-lieu/{model_key}/",
            })
        obj.delete()
        messages.success(request, f"Đã xóa {config['title'].lower()}.")
    except ProtectedError:
        messages.error(request, "Không thể xóa vì bản ghi đang có dữ liệu liên quan cần được lưu giữ.")
    except Exception as exc:
        messages.error(request, "Không thể xóa bản ghi này.")
    return redirect("custom_admin_model_list", model_key=model_key)


@login_required
def custom_admin_hospitals(request):
    return custom_admin_model_list(request, "benh-vien")


@login_required
def custom_admin_hospital_create(request):
    return custom_admin_model_create(request, "benh-vien")


@login_required
def custom_admin_hospital_edit(request, pk):
    return custom_admin_model_edit(request, "benh-vien", pk)


@login_required
def custom_admin_hospital_delete(request, pk):
    return custom_admin_model_delete(request, "benh-vien", pk)


@login_required
def custom_admin_departments(request):
    return custom_admin_model_list(request, "khoa")


@login_required
def custom_admin_department_create(request):
    return custom_admin_model_create(request, "khoa")


@login_required
def custom_admin_department_edit(request, pk):
    return custom_admin_model_edit(request, "khoa", pk)


@login_required
def custom_admin_department_delete(request, pk):
    return custom_admin_model_delete(request, "khoa", pk)


@login_required
def custom_admin_doctors(request):
    return custom_admin_model_list(request, "bac-si")


@login_required
def custom_admin_doctor_create(request):
    return custom_admin_model_create(request, "bac-si")


@login_required
def custom_admin_doctor_edit(request, pk):
    return custom_admin_model_edit(request, "bac-si", pk)


@login_required
def custom_admin_doctor_delete(request, pk):
    return custom_admin_model_delete(request, "bac-si", pk)


@login_required
def custom_admin_departments_api(request):
    if not can_admin(request.user):
        return JsonResponse({"results": []}, status=403)

    benh_vien_id = (request.GET.get("benh_vien_id") or "").strip()
    if not benh_vien_id.isdigit():
        return JsonResponse({"results": []})

    departments = (
        Khoa.objects.filter(benh_vien_id=int(benh_vien_id))
        .order_by("ten")
        .values("id", "ten")
    )
    return JsonResponse({"results": list(departments)})


@require_POST
def user_logout(request):

    logout(request)

    return redirect("login")

# ==========================
# QUÊN MẬT KHẨU (OTP EMAIL)
# ==========================
FORGOT_PASSWORD_SESSION_KEY = "forgot_password_flow"
FORGOT_PASSWORD_OTP_EXPIRE_MINUTES = 10


def _clear_forgot_password_flow(request):
    request.session.pop(FORGOT_PASSWORD_SESSION_KEY, None)


def _mask_email(email):
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*" * (len(local) - 1)
    else:
        masked_local = local[:2] + "*" * (len(local) - 2)
    return f"{masked_local}@{domain}"


def _get_valid_forgot_password_flow(request):
    state = request.session.get(FORGOT_PASSWORD_SESSION_KEY)
    if not state:
        return None

    expires_at_raw = state.get("expires_at")
    if not expires_at_raw:
        _clear_forgot_password_flow(request)
        return None

    try:
        expires_at = datetime.datetime.fromisoformat(expires_at_raw)
    except (TypeError, ValueError):
        _clear_forgot_password_flow(request)
        return None

    if timezone.is_naive(expires_at):
        expires_at = timezone.make_aware(expires_at, timezone.get_current_timezone())

    if timezone.now() > expires_at:
        _clear_forgot_password_flow(request)
        return None

    return state


def _translate_password_validation_message(message):
    mapping = {
        "This password is too short. It must contain at least 8 characters.": "Mật khẩu quá ngắn. Mật khẩu phải có ít nhất 8 ký tự.",
        "This password is too common.": "Mật khẩu quá phổ biến, vui lòng chọn mật khẩu khác an toàn hơn.",
        "This password is entirely numeric.": "Mật khẩu không được chỉ gồm chữ số.",
        "The password is too similar to the username.": "Mật khẩu quá giống với tên người dùng.",
    }
    return mapping.get(message, message)


def forgot_password(request):
    if request.method == "POST":
        step = (request.POST.get("step") or "").strip()

        if step == "restart":
            _clear_forgot_password_flow(request)
            messages.success(request, "Mời bạn nhập lại tên người dùng.")
            return redirect("forgot_password")

        if step == "username":
            username = request.POST.get("username", "").strip()
            if not username:
                messages.error(request, "Vui lòng nhập tên người dùng.")
                return redirect("forgot_password")

            user = User.objects.filter(username=username).first()
            if not user:
                messages.error(request, "Tên người dùng không tồn tại.")
                return redirect("forgot_password")

            email = (user.email or "").strip()
            if not email:
                messages.error(
                    request,
                    "Tài khoản này chưa có email, không thể gửi mã xác thực.",
                )
                return redirect("forgot_password")

            otp_code = f"{secrets.randbelow(1000000):06d}"
            expires_at = timezone.now() + datetime.timedelta(
                minutes=FORGOT_PASSWORD_OTP_EXPIRE_MINUTES
            )

            subject = "[Hệ thống bệnh viện] Mã OTP đặt lại mật khẩu"
            message = (
                f"Xin chào {user.username},\n\n"
                f"Mã OTP đặt lại mật khẩu của bạn là: {otp_code}\n"
                f"Mã có hiệu lực trong {FORGOT_PASSWORD_OTP_EXPIRE_MINUTES} phút.\n\n"
                "Nếu bạn không yêu cầu đổi mật khẩu, vui lòng bỏ qua email này."
            )

            try:
                send_mail(
                    subject=subject,
                    message=message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                    fail_silently=False,
                )
            except Exception:
                _clear_forgot_password_flow(request)
                messages.error(
                    request,
                    "Không gửi được mã OTP lúc này. Vui lòng thử lại sau.",
                )
                return redirect("forgot_password")

            request.session[FORGOT_PASSWORD_SESSION_KEY] = {
                "user_id": user.id,
                "username": user.username,
                "email": email,
                "otp_code": otp_code,
                "verified": False,
                "expires_at": expires_at.isoformat(),
            }
            messages.success(request, "Đã gửi mã OTP 6 số đến email của bạn.")
            return redirect("forgot_password")

        if step == "otp":
            state = _get_valid_forgot_password_flow(request)
            if not state:
                messages.error(
                    request,
                    "Mã OTP đã hết hạn hoặc chưa được tạo. Vui lòng thực hiện lại.",
                )
                return redirect("forgot_password")

            otp_input = request.POST.get("otp", "").strip()
            if not (otp_input.isdigit() and len(otp_input) == 6):
                messages.error(request, "Vui lòng nhập đúng mã OTP gồm 6 chữ số.")
                return redirect("forgot_password")

            if not secrets.compare_digest(otp_input, str(state.get("otp_code", ""))):
                state["attempts"] = state.get("attempts", 0) + 1
                if state["attempts"] >= 5:
                    _clear_forgot_password_flow(request)
                else:
                    request.session[FORGOT_PASSWORD_SESSION_KEY] = state
                messages.error(request, "Mã OTP không đúng. Sau 5 lần sai, vui lòng yêu cầu mã mới.")
                return redirect("forgot_password")

            state["verified"] = True
            request.session[FORGOT_PASSWORD_SESSION_KEY] = state
            messages.success(request, "Xác thực OTP thành công. Mời bạn nhập mật khẩu mới.")
            return redirect("forgot_password")

        if step == "reset":
            state = _get_valid_forgot_password_flow(request)
            if not state:
                messages.error(
                    request,
                    "Phiên đổi mật khẩu đã hết hạn. Vui lòng thực hiện lại.",
                )
                return redirect("forgot_password")

            if not state.get("verified"):
                messages.error(request, "Vui lòng xác thực OTP trước khi đổi mật khẩu.")
                return redirect("forgot_password")

            new_password = request.POST.get("new_password", "").strip()
            confirm_password = request.POST.get("confirm_password", "").strip()

            if not new_password or not confirm_password:
                messages.error(request, "Vui lòng nhập đầy đủ mật khẩu mới.")
                return redirect("forgot_password")

            if new_password != confirm_password:
                messages.error(request, "Mật khẩu xác nhận không khớp.")
                return redirect("forgot_password")

            user = User.objects.filter(pk=state.get("user_id"), email=state.get("email"), is_active=True).first()
            if not user:
                _clear_forgot_password_flow(request)
                messages.error(request, "Tài khoản đã thay đổi. Vui lòng thực hiện lại.")
                return redirect("forgot_password")
            try:
                validate_password(new_password, user=user)
            except ValidationError as exc:
                for error in exc.messages:
                    messages.error(request, _translate_password_validation_message(error))
                return redirect("forgot_password")

            user.set_password(new_password)
            user.save(update_fields=["password"])
            _clear_forgot_password_flow(request)

            messages.success(request, "Đổi mật khẩu thành công vui lòng đăng nhập")
            return redirect("login")

        messages.error(request, "Yêu cầu không hợp lệ. Vui lòng thử lại.")
        return redirect("forgot_password")

    state = _get_valid_forgot_password_flow(request)
    if not state:
        step = "username"
    elif state.get("verified"):
        step = "reset"
    else:
        step = "otp"

    context = {
        "step": step,
        "flow_username": state.get("username") if state else "",
        "flow_email_masked": _mask_email(state.get("email")) if state else "",
        "otp_expire_minutes": FORGOT_PASSWORD_OTP_EXPIRE_MINUTES,
    }
    return render(request, "core/forgot_password.html", context)

# ==========================
# LIEN HE / GOP Y
# ==========================
def contact_feedback(request):
    initial = {}
    if request.user.is_authenticated:
        full_name = request.user.get_full_name().strip()
        initial["ho_ten"] = full_name or request.user.username
        if request.user.email:
            initial["email"] = request.user.email

    if request.method == "POST":
        form = ContactFeedbackForm(request.POST)
        if form.is_valid():
            cleaned = form.cleaned_data
            recipient = getattr(settings, "CONTACT_RECEIVER_EMAIL", "") or settings.DEFAULT_FROM_EMAIL
            subject = f"[GÓP Ý] {cleaned['chu_de']}"
            message = (
                f"Họ tên: {cleaned['ho_ten']}\n"
                f"Email: {cleaned['email']}\n"
                f"Thời gian: {timezone.localtime().strftime('%d/%m/%Y %H:%M:%S')}\n\n"
                f"Nội dung:\n{cleaned['noi_dung']}"
            )
            try:
                send_mail(
                    subject=subject,
                    message=message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[recipient],
                    fail_silently=False,
                )
            except Exception:
                messages.error(
                    request,
                    "Không gửi được góp ý lúc này. Vui lòng thử lại sau.",
                )
            else:
                messages.success(
                    request,
                    "Đã gửi góp ý thành công. Cảm ơn bạn!",
                )
                return redirect("contact_feedback")
        else:
            messages.error(request, "Vui lòng kiểm tra lại thông tin góp ý.")
    else:
        form = ContactFeedbackForm(initial=initial)

    return render(request, "core/contact_feedback.html", {"form": form})

# ==========================
# TRANG CHỦ BÁC SĨ
# ==========================
@login_required
def bac_si_home(request):

    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")

    status_filter = request.GET.get("status", "").strip()
    range_filter = request.GET.get("range", "").strip()
    shift_filter = request.GET.get("shift", "").strip()

    base_qs = bac_si.lich_khams.all()

    completed_appointment_ids = set(
        PhieuKham.objects.filter(lich_kham__bac_si=bac_si).values_list("lich_kham_id", flat=True)
    )
    if completed_appointment_ids:
        base_qs.filter(id__in=completed_appointment_ids).exclude(trang_thai="xong").update(
            trang_thai="xong"
        )

    lich_khams = base_qs

    today = timezone.localdate()
    stats_qs = base_qs.filter(ngay_kham__gte=today)
    stats = {
        "tong": stats_qs.count(),
        "cho": stats_qs.filter(trang_thai="cho").count(),
        "dang": stats_qs.filter(trang_thai="dang").count(),
        "xong": stats_qs.filter(trang_thai="xong").count(),
        "huy": stats_qs.filter(trang_thai="huy").count(),
    }
    if status_filter in {"cho", "dang", "xong", "huy"}:
        lich_khams = lich_khams.filter(trang_thai=status_filter)
    else:
        status_filter = ""

    if range_filter in {"week", "month"}:
        if range_filter == "week":
            end_date = today + datetime.timedelta(days=7)
        else:
            end_date = today + datetime.timedelta(days=30)
        lich_khams = lich_khams.filter(ngay_kham__gte=today, ngay_kham__lte=end_date)
    else:
        range_filter = ""

    if shift_filter in {"sang", "chieu"}:
        noon = datetime.time(12, 0)
        if shift_filter == "sang":
            lich_khams = lich_khams.filter(gio_kham__lt=noon)
        else:
            lich_khams = lich_khams.filter(gio_kham__gte=noon)
    else:
        shift_filter = ""

    lich_khams = lich_khams.order_by("-ngay_kham", "-gio_kham")
    phieu_map = {
        phieu.lich_kham_id: phieu
        for phieu in PhieuKham.objects.filter(lich_kham__in=lich_khams)
    }
    for lich in lich_khams:
        lich.phieu_kham_obj = phieu_map.get(lich.id)
        lich.ca_label = "Ca sáng" if lich.gio_kham < datetime.time(12, 0) else "Ca chiều"

    return render(request, "core/doctor_home.html", {
        "bac_si": bac_si,
        "lich_khams": lich_khams,
        "status_filter": status_filter,
        "range_filter": range_filter,
        "shift_filter": shift_filter,
        "stats": stats,
    })


# ==========================
# HỒ SƠ BỆNH NHÂN (BÁC SĨ)
# ==========================
@login_required
def bac_si_patient_detail(request, benh_nhan_id):

    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")

    benh_nhan = get_object_or_404(BenhNhan, id=benh_nhan_id)

    co_lich = LichKham.objects.filter(
        bac_si=bac_si,
        benh_nhan=benh_nhan
    ).exists()
    if not co_lich:
        messages.error(request, "Bác sĩ không có lịch khám với bệnh nhân này.")
        return redirect("bac_si_home")

    lich_su = LichSuKhamBenh.objects.filter(
        benh_nhan=benh_nhan
    ).select_related("bac_si", "benh_vien", "phieu_kham").order_by("-ngay_kham", "-ngay_tao")

    return render(request, "core/doctor_patient_detail.html", {
        "bac_si": bac_si,
        "benh_nhan": benh_nhan,
        "lich_su": lich_su,
    })


# ==========================
# BẮT ĐẦU KHÁM (BÁC SĨ)
# ==========================
@login_required
def start_exam(request, lich_id):
    if request.method != "POST":
        return redirect("bac_si_home")

    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")

    lich = get_object_or_404(
        LichKham,
        id=lich_id,
        bac_si=bac_si
    )

    if lich.trang_thai == "huy":
        messages.error(request, "Lịch đã hủy, không thể bắt đầu khám.")
        return redirect("bac_si_home")

    if lich.trang_thai == "xong":
        messages.info(request, "Lịch đã hoàn thành.")
        return redirect("bac_si_home")

    if lich.trang_thai != "dang":
        lich.trang_thai = "dang"
        lich.save()
        messages.success(request, "Đã chuyển trạng thái sang đang khám.")

    return redirect("bac_si_home")


# ==========================
# THÔNG BÁO BÁC SĨ
# ==========================
@login_required
def bac_si_notifications(request):
    try:
        request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "mark_all":
            ThongBao.objects.filter(
                nguoi_nhan=request.user,
                da_doc=False,
            ).update(da_doc=True)
            messages.success(request, "Đã đánh dấu tất cả thông báo là đã đọc.")
            return redirect("bac_si_notifications")
        if action == "mark_one":
            tb_id = request.POST.get("tb_id")
            if tb_id:
                ThongBao.objects.filter(
                    nguoi_nhan=request.user,
                    id=tb_id,
                ).update(da_doc=True)
            return redirect("bac_si_notifications")

    thong_baos = ThongBao.objects.filter(
        nguoi_nhan=request.user
    ).order_by("-thoi_gian")

    return render(request, "core/doctor_notifications.html", {
        "thong_baos": thong_baos,
    })
 
# ==========================
# LỊCH LÀM VIỆC BÁC SĨ
# ==========================
@login_required
def bac_si_schedule(request):
    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")

    thu_labels = [
        (0, "Chủ nhật"),
        (1, "Thứ 2"),
        (2, "Thứ 3"),
        (3, "Thứ 4"),
        (4, "Thứ 5"),
        (5, "Thứ 6"),
        (6, "Thứ 7"),
    ]

    if request.method == "POST":
        for thu, _ in thu_labels:
            nghi = request.POST.get(f"nghi_{thu}") == "on"
            gio_bat_dau = request.POST.get(f"gio_bat_dau_{thu}", "").strip()
            gio_ket_thuc = request.POST.get(f"gio_ket_thuc_{thu}", "").strip()

            if nghi:
                GioLamViecBacSi.objects.update_or_create(
                    bac_si=bac_si,
                    thu=thu,
                    defaults={
                        "nghi": True,
                        "gio_bat_dau": "00:00",
                        "gio_ket_thuc": "00:00",
                    },
                )
                continue

            if not gio_bat_dau or not gio_ket_thuc:
                messages.error(request, "Vui lòng nhập đầy đủ giờ làm việc.")
                return redirect("bac_si_schedule")

            try:
                start_time = datetime.time.fromisoformat(gio_bat_dau)
                end_time = datetime.time.fromisoformat(gio_ket_thuc)
            except ValueError:
                messages.error(request, "Định dạng giờ không hợp lệ.")
                return redirect("bac_si_schedule")

            if start_time >= end_time:
                messages.error(request, "Giờ bắt đầu phải nhỏ hơn giờ kết thúc.")
                return redirect("bac_si_schedule")

            GioLamViecBacSi.objects.update_or_create(
                bac_si=bac_si,
                thu=thu,
                defaults={
                    "nghi": False,
                    "gio_bat_dau": start_time,
                    "gio_ket_thuc": end_time,
                },
            )

        messages.success(request, "Đã cập nhật lịch làm việc.")
        return redirect("bac_si_schedule")

    schedules = {
        glv.thu: glv
        for glv in GioLamViecBacSi.objects.filter(bac_si=bac_si)
    }
    schedule_items = []
    for thu, label in thu_labels:
        glv = schedules.get(thu)
        schedule_items.append({
            "thu": thu,
            "label": label,
            "nghi": glv.nghi if glv else False,
            "gio_bat_dau": glv.gio_bat_dau.strftime("%H:%M") if glv and glv.gio_bat_dau else "",
            "gio_ket_thuc": glv.gio_ket_thuc.strftime("%H:%M") if glv and glv.gio_ket_thuc else "",
        })

    return render(request, "core/doctor_schedule.html", {
        "bac_si": bac_si,
        "schedule_items": schedule_items,
    })


# ==========================
# SỬA PHIẾU KHÁM (BÁC SĨ)
# ==========================
@login_required
def bac_si_phieu_kham_edit(request, phieu_id):
    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")

    phieu = get_object_or_404(
        PhieuKham.objects.prefetch_related("hinh_anhs"),
        id=phieu_id,
        lich_kham__bac_si=bac_si,
    )
    clinical_values = {name + "_value": request.POST.get(name, "") if request.method == "POST" else getattr(phieu, name)
                       for name in ("trieu_chung", "chan_doan", "huong_dieu_tri")}

    def render_exam_edit():
        return render(request, "core/doctor_exam_edit.html", {
            "phieu": phieu, "existing_images": list(phieu.hinh_anhs.all()), **clinical_values,
        })

    if request.method == "POST":
        trieu_chung = request.POST.get("trieu_chung", "").strip()
        chan_doan = request.POST.get("chan_doan", "").strip()
        huong_dieu_tri = request.POST.get("huong_dieu_tri", "").strip()
        remove_image_ids = request.POST.getlist("remove_image_ids")
        new_images = request.FILES.getlist("new_images")

        if not trieu_chung or not chan_doan or not huong_dieu_tri:
            messages.error(request, "Vui lòng nhập đầy đủ thông tin.")
            return render_exam_edit()
        old_data = {
            "trieu_chung": phieu.trieu_chung,
            "chan_doan": phieu.chan_doan,
            "huong_dieu_tri": phieu.huong_dieu_tri,
        }
        new_data = {
            "trieu_chung": trieu_chung,
            "chan_doan": chan_doan,
            "huong_dieu_tri": huong_dieu_tri,
        }

        try:
            with transaction.atomic():
                get_object_or_404(LichKham.objects.select_for_update(), pk=phieu.lich_kham_id, bac_si=bac_si)
                phieu = get_object_or_404(PhieuKham.objects.select_for_update(), pk=phieu_id, lich_kham__bac_si=bac_si)
                phieu.trieu_chung = trieu_chung
                phieu.chan_doan = chan_doan
                phieu.huong_dieu_tri = huong_dieu_tri
                phieu.save()

                if remove_image_ids:
                    PhieuKhamHinhAnh.objects.filter(
                        phieu_kham=phieu,
                        id__in=remove_image_ids,
                    ).delete()

                next_order = phieu.hinh_anhs.aggregate(max_order=Max("thu_tu")).get("max_order")
                next_order = (next_order + 1) if next_order is not None else 0
                for image in new_images:
                    hinh_anh = PhieuKhamHinhAnh(
                        phieu_kham=phieu,
                        hinh_anh=image,
                        thu_tu=next_order,
                    )
                    hinh_anh.full_clean()
                    hinh_anh.save()
                    next_order += 1
        except ValidationError:
            messages.error(request, "Ảnh tải lên không hợp lệ. Vui lòng dùng JPG, PNG hoặc WEBP.")
            return render_exam_edit()

        if old_data != new_data:
            LogHeThong.objects.create(
                model_name="PhieuKham",
                object_id=phieu.id,
                hanh_dong="update",
                du_lieu_cu=old_data,
                du_lieu_moi=new_data,
                nguoi_thuc_hien=request.user,
                ghi_chu="Cập nhật phiếu khám",
            )

        messages.success(request, "Đã cập nhật phiếu khám.")
        return redirect("phieu_kham_detail", phieu_id=phieu.id)

    return render_exam_edit()


@login_required
def profile(request):

    benh_nhan = _patient_for_user(request.user)

    if not benh_nhan:
        return render(request, "core/error.html", {
            "msg": "Không tìm thấy hồ sơ"
        })

    if request.method == "POST":
        action = request.POST.get("action", "update_profile")
        if action == "change_password":
            current_password = request.POST.get("current_password", "")
            new_password = request.POST.get("new_password", "")
            confirm_password = request.POST.get("confirm_password", "")

            if not current_password or not new_password or not confirm_password:
                messages.error(request, "Vui lòng nhập đầy đủ thông tin đổi mật khẩu.")
                return redirect("profile")

            if not request.user.check_password(current_password):
                messages.error(request, "Mật khẩu hiện tại không đúng.")
                return redirect("profile")

            if new_password != confirm_password:
                messages.error(request, "Mật khẩu xác nhận không khớp.")
                return redirect("profile")

            try:
                validate_password(new_password, user=request.user)
            except ValidationError as exc:
                for err in exc.messages:
                    messages.error(request, err)
                return redirect("profile")

            request.user.set_password(new_password)
            request.user.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, "Đổi mật khẩu thành công.")
            return redirect("profile")

        try:
            with transaction.atomic():
                benh_nhan = BenhNhan.objects.select_for_update().get(pk=benh_nhan.pk)
                for field in ("ho_ten", "ngay_sinh", "gioi_tinh", "dia_chi", "so_dien_thoai"):
                    setattr(benh_nhan, field, request.POST.get(field, "").strip())
                ma_bhyt = request.POST.get("ma_bhyt", "").strip()
                if ma_bhyt:
                    bhyt = BaoHiemYTe.objects.select_for_update().filter(ma_bhyt=ma_bhyt).first()
                    if bhyt and BenhNhan.objects.filter(bhyt=bhyt).exclude(pk=benh_nhan.pk).exists():
                        raise ValidationError("BHYT đã thuộc hồ sơ khác.")
                    if bhyt is None:
                        bhyt = BaoHiemYTe(ma_bhyt=ma_bhyt)
                    bhyt.ngay_cap = request.POST.get("ngay_cap", "").strip()
                    bhyt.ngay_het_han = request.POST.get("ngay_het_han", "").strip()
                    bhyt.full_clean()
                    bhyt.save()
                    benh_nhan.bhyt = bhyt
                # Link legacy profiles on successful update; phone changes do not rename login.
                benh_nhan.user = request.user
                benh_nhan.full_clean()
                benh_nhan.save()
        except ValidationError as exc:
            for error in exc.messages:
                messages.error(request, error)
            return redirect("profile")
        except IntegrityError:
            messages.error(request, "Số điện thoại hoặc BHYT đã được sử dụng.")
            return redirect("profile")

        messages.success(request, "Cập nhật thành công")

        return redirect("profile")

    return render(request, "core/profile.html", {
        "bn": benh_nhan
    })


# ==========================
# THÔNG BÁO (BỆNH NHÂN)
# ==========================
@login_required
def notifications(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "mark_all":
            ThongBao.objects.filter(
                nguoi_nhan=request.user,
                da_doc=False,
            ).update(da_doc=True)
            messages.success(request, "Đã đánh dấu tất cả thông báo là đã đọc.")
            return redirect("notifications")
        if action == "mark_one":
            tb_id = request.POST.get("tb_id")
            if tb_id:
                ThongBao.objects.filter(
                    nguoi_nhan=request.user,
                    id=tb_id,
                ).update(da_doc=True)
            return redirect("notifications")

    thong_baos = ThongBao.objects.filter(
        nguoi_nhan=request.user
    ).order_by("-thoi_gian")

    return render(request, "core/notifications.html", {
        "thong_baos": thong_baos,
    })


def custom_404(request, exception):
    return render(request, "404.html", status=404)


def custom_404_debug(request):
    return render(request, "404.html", status=404)



