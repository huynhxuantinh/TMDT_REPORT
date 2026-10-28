from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin
from django.contrib.gis.forms import OSMWidget

from .models import (
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
    NoiDungGioiThieu,
    PhieuKham,
    ThongBao,
)

admin.site.site_header = "Quản trị QL Bệnh viện"
admin.site.site_title = "QL Bệnh viện Admin"
admin.site.index_title = "Bảng điều khiển quản trị"


class GioLamViecBenhVienInline(admin.TabularInline):
    model = GioLamViecBenhVien
    extra = 0


class GioLamViecBacSiInline(admin.TabularInline):
    model = GioLamViecBacSi
    extra = 0


class BenhVienOSMWidget(OSMWidget):
    map_srid = 4326
    default_lat = 10.77
    default_lon = 106.7
    default_zoom = 12


@admin.register(BenhVien)
class BenhVienAdmin(GISModelAdmin):
    class Media:
        js = ("core/js/benhvien_admin_map.js",)

    gis_widget = BenhVienOSMWidget
    gis_widget_kwargs = {
        "attrs": {
            "default_lat": 10.77,
            "default_lon": 106.7,
            "default_zoom": 12,
        }
    }
    list_display = (
        "ten",
        "phuong",
        "loai_hinh",
        "cap_cuu_24h",
        "co_bhyt",
        "gio_mo",
        "gio_dong",
    )
    search_fields = ("ten", "dia_chi", "phuong")
    list_filter = ("phuong", "loai_hinh", "cap_cuu_24h", "co_bhyt")
    ordering = ("ten",)
    inlines = (GioLamViecBenhVienInline,)


@admin.register(GioLamViecBenhVien)
class GioLamViecBenhVienAdmin(admin.ModelAdmin):
    list_display = ("benh_vien", "thu", "gio_mo", "gio_dong", "nghi")
    list_filter = ("thu", "nghi", "benh_vien")
    ordering = ("benh_vien", "thu")


@admin.register(Khoa)
class KhoaAdmin(admin.ModelAdmin):
    list_display = ("ten", "benh_vien")
    search_fields = ("ten", "benh_vien__ten")
    list_filter = ("benh_vien",)
    ordering = ("ten",)


@admin.register(BacSi)
class BacSiAdmin(admin.ModelAdmin):
    list_display = ("ho_ten", "chuyen_khoa", "khoa", "benh_vien", "so_dien_thoai")
    search_fields = (
        "ho_ten",
        "chuyen_khoa",
        "so_dien_thoai",
        "khoa__ten",
        "benh_vien__ten",
    )
    list_filter = ("benh_vien", "khoa", "chuyen_khoa")
    ordering = ("ho_ten",)
    inlines = (GioLamViecBacSiInline,)


@admin.register(GioLamViecBacSi)
class GioLamViecBacSiAdmin(admin.ModelAdmin):
    list_display = ("bac_si", "thu", "gio_bat_dau", "gio_ket_thuc", "nghi")
    list_filter = ("thu", "nghi", "bac_si")
    ordering = ("bac_si", "thu")


@admin.register(BaoHiemYTe)
class BaoHiemYTeAdmin(admin.ModelAdmin):
    list_display = ("ma_bhyt", "ngay_cap", "ngay_het_han")
    search_fields = ("ma_bhyt",)
    list_filter = ("ngay_het_han",)
    date_hierarchy = "ngay_het_han"
    ordering = ("-ngay_het_han",)


@admin.register(BenhNhan)
class BenhNhanAdmin(admin.ModelAdmin):
    list_display = ("ho_ten", "ngay_sinh", "gioi_tinh", "so_dien_thoai", "bhyt")
    search_fields = ("ho_ten", "so_dien_thoai", "dia_chi", "bhyt__ma_bhyt")
    list_filter = ("gioi_tinh",)
    ordering = ("ho_ten",)


@admin.register(LichKham)
class LichKhamAdmin(admin.ModelAdmin):
    list_display = ("benh_nhan", "bac_si", "ngay_kham", "gio_kham", "trang_thai")
    search_fields = ("benh_nhan__ho_ten", "bac_si__ho_ten", "ghi_chu")
    list_filter = ("trang_thai", "ngay_kham", "bac_si", "benh_nhan")
    date_hierarchy = "ngay_kham"
    ordering = ("-ngay_kham", "-gio_kham")


@admin.register(PhieuKham)
class PhieuKhamAdmin(admin.ModelAdmin):
    list_display = ("benh_nhan", "lich_kham", "ngay_lap")
    search_fields = ("benh_nhan__ho_ten", "chan_doan", "trieu_chung")
    list_filter = ("ngay_lap",)
    date_hierarchy = "ngay_lap"
    ordering = ("-ngay_lap",)


@admin.register(LichSuKhamBenh)
class LichSuKhamBenhAdmin(admin.ModelAdmin):
    list_display = ("benh_nhan", "bac_si", "benh_vien", "ngay_kham", "ngay_tao")
    search_fields = ("benh_nhan__ho_ten", "bac_si__ho_ten", "chan_doan", "trieu_chung")
    list_filter = ("benh_vien", "bac_si", "ngay_kham")
    date_hierarchy = "ngay_kham"
    ordering = ("-ngay_kham", "-ngay_tao")


@admin.register(LogLichKham)
class LogLichKhamAdmin(admin.ModelAdmin):
    list_display = (
        "lich_kham",
        "hanh_dong",
        "trang_thai_cu",
        "trang_thai_moi",
        "nguoi_thuc_hien",
        "thoi_gian",
    )
    search_fields = (
        "mo_ta",
        "lich_kham__benh_nhan__ho_ten",
        "lich_kham__bac_si__ho_ten",
    )
    list_filter = ("hanh_dong", "thoi_gian")
    date_hierarchy = "thoi_gian"
    ordering = ("-thoi_gian",)


@admin.register(LogHeThong)
class LogHeThongAdmin(admin.ModelAdmin):
    list_display = ("model_name", "object_id", "hanh_dong", "nguoi_thuc_hien", "thoi_gian")
    search_fields = ("model_name", "object_id", "ghi_chu")
    list_filter = ("model_name", "hanh_dong", "thoi_gian")
    date_hierarchy = "thoi_gian"
    ordering = ("-thoi_gian",)


@admin.register(ThongBao)
class ThongBaoAdmin(admin.ModelAdmin):
    list_display = ("tieu_de", "loai", "nguoi_nhan", "da_doc", "thoi_gian")
    search_fields = ("tieu_de", "noi_dung", "nguoi_nhan__username")
    list_filter = ("loai", "da_doc", "thoi_gian")
    date_hierarchy = "thoi_gian"
    ordering = ("-thoi_gian",)


@admin.register(NoiDungGioiThieu)
class NoiDungGioiThieuAdmin(admin.ModelAdmin):
    list_display = ("tieu_de_trang", "ngay_cap_nhat")
    ordering = ("thu_tu",)
