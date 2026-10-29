# QL_BENHVIEN — Hệ thống Quản lý Bệnh viện tích hợp GIS

Ứng dụng web Django + PostGIS để tìm kiếm bệnh viện trên bản đồ, đặt lịch khám và quản lý hồ sơ bệnh nhân.

**Clone từ GitHub:** `git clone https://github.com/huynhxuantinh/TMDT_REPORT.git`. File `.env` không được đưa lên repo. Trên máy mới, sao chép `.env.example` thành `.env`, tạo `SECRET_KEY` riêng và điền cấu hình PostgreSQL/VNPAY của người chạy. Xem [hướng dẫn chạy trên máy mới](docs/CHAY_VNPAY_MAY_MOI.md).

---

## Mục lục

1. [Tính năng chính](#1-tính-năng-chính)
2. [Tech Stack](#2-tech-stack)
3. [Cấu trúc dự án](#3-cấu-trúc-dự-án)
4. [Yêu cầu môi trường](#4-yêu-cầu-môi-trường)
5. [Cấu hình `.env`](#5-cấu-hình-env)
6. [Cài đặt & khởi chạy](#6-cài-đặt--khởi-chạy)
7. [Tạo tài khoản quản trị](#7-tạo-tài-khoản-quản-trị)
8. [Import dữ liệu từ Excel](#8-import-dữ-liệu-từ-excel)
9. [Liên kết lại ảnh bệnh viện](#9-liên-kết-lại-ảnh-bệnh-viện)
10. [Seed dữ liệu mẫu](#10-seed-dữ-liệu-mẫu)
11. [Các lệnh quản lý khác](#11-các-lệnh-quản-lý-khác)
12. [Luồng demo đề xuất](#12-luồng-demo-đề-xuất)
13. [Chạy tests](#13-chạy-tests)
14. [Công nghệ & Tham khảo](#14-công-nghệ--tham-khảo)

---

## 1) Tính năng chính

### Bệnh nhân (khách / đã đăng nhập)
- Xem danh sách & bản đồ bệnh viện, lọc theo: phường, loại hình (công/tư/quốc tế), BHYT, cấp cứu 24h, trạng thái đang mở.
- Tìm bệnh viện gần vị trí hiện tại (theo bán kính km).
- Xem chi tiết bệnh viện: thông tin, giờ làm việc, khoa, bác sĩ, ảnh gallery, bản đồ Leaflet, chỉ đường OSRM.
- Đặt lịch khám (chọn khoa → bác sĩ → ngày/giờ).
- Xem lịch khám sắp tới, hủy lịch.
- Xem lịch sử khám bệnh và chi tiết phiếu khám.
- Quản lý hồ sơ cá nhân & BHYT.
- Nhận thông báo (đặt lịch, nhắc lịch 24h, phiếu khám hoàn thành).
- Gửi góp ý/liên hệ qua email.
- Quên mật khẩu: xác thực qua OTP gửi email (3 bước).

### Bác sĩ (đã đăng nhập)
- Dashboard lịch khám: lọc theo trạng thái, khoảng thời gian, ca sáng/chiều.
- Bắt đầu khám → tạo phiếu khám (triệu chứng, chẩn đoán, hướng điều trị, ảnh đính kèm).
- Sửa phiếu khám (có log thay đổi).
- Xem hồ sơ bệnh nhân & lịch sử khám.
- Quản lý lịch làm việc (theo ngày trong tuần).
- Xem thông báo.

### Quản trị (staff / superuser)
- Giao diện admin tùy biến tại `/quan-tri/` (không phụ thuộc Django Admin mặc định).
- Dashboard: thống kê tổng quan, bệnh viện & bác sĩ mới nhất.
- CRUD đầy đủ cho: Bệnh viện (kèm bản đồ chọn tọa độ & quản lý ảnh), Khoa, Bác sĩ, Giờ làm việc, Tài khoản, Lịch khám, Phiếu khám, Thông báo, Log hệ thống...
- Django Admin chuẩn vẫn khả dụng tại `/admin/` cho superuser.

---

## 2) Tech Stack

| Thành phần | Công nghệ |
|---|---|
| Backend | Django 6.0.1 |
| Database | PostgreSQL + PostGIS |
| GIS | GeoDjango, GDAL, GEOS |
| Frontend | Django Templates, Leaflet.js, OpenStreetMap |
| Routing | OSRM (chỉ đường) |
| Geocoding | Nominatim |
| Email | Mailtrap SMTP (dev) |
| Image | Pillow 11.x |
| Cache | Django LocMemCache (in-process) |
| Animation | AOS, Anime.js |

---

## 3) Cấu trúc dự án

```
QL_BENHVIEN/                   ← thư mục gốc git
├── .env                        ← biến môi trường (không commit)
├── .gitignore
├── data.xlsx                   ← file Excel import chính
├── BENH_VIEN/                  ← thư mục ảnh gốc (theo tên/id cũ)
├── media/                      ← Django MEDIA_ROOT
│   ├── benh_vien/<pk>/         ← ảnh bệnh viện (theo PK hiện tại)
│   └── phieu_kham/<pk>/        ← ảnh phiếu khám
└── QL_BENHVIEN/                ← Django project root
    ├── manage.py
    ├── requirements.txt
    ├── hospital/               ← config module (settings, urls, wsgi, asgi)
    │   ├── settings.py
    │   └── urls.py
    └── core/                   ← app duy nhất chứa toàn bộ logic
        ├── models.py           ← ~720 dòng, 14 models
        ├── views.py            ← ~2658 dòng
        ├── forms.py            ← ~500 dòng
        ├── urls.py
        ├── admin.py
        ├── tests.py            ← ~586 dòng
        ├── templates/core/     ← tất cả HTML templates
        ├── static/core/        ← CSS, JS, ảnh tĩnh
        └── management/commands/
            ├── import_hospitals.py
            ├── relink_hospital_images.py
            ├── seed_core.py
            ├── seed_demo.py
            ├── send_test_email.py
            └── update_hospital_phuong.py
```

---

## 4) Yêu cầu môi trường

- **Python** 3.12+
- **PostgreSQL** 14+ với extension **PostGIS**
- **GDAL** và **GEOS** (cài qua OSGeo4W trên Windows)
- `pip install` các gói trong `requirements.txt` + `python-dotenv` + `openpyxl`

### Cài PostGIS (Windows — OSGeo4W)
1. Tải OSGeo4W: https://trac.osgeo.org/osgeo4w/
2. Cài gói `gdal`, `geos`.
3. Ghi lại đường dẫn `.dll` để điền vào `.env`.

### Tạo database PostgreSQL
```sql
CREATE DATABASE ql_benhvien001;
\c ql_benhvien001
CREATE EXTENSION postgis;
```

---

## 5) Cấu hình `.env`

Tạo file `.env` tại **thư mục gốc** (cùng cấp `manage.py`):

```env
# Database
DB_NAME=ql_benhvien001
DB_USER=postgres
DB_PASSWORD=your_password
DB_HOST=localhost
DB_PORT=5432

# App
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost

# GIS (Windows — điều chỉnh phiên bản gdal nếu khác)
GDAL_PATH=C:\Users\ASUS\AppData\Local\Programs\OSGeo4W\bin\gdal312.dll
GEOS_PATH=C:\Users\ASUS\AppData\Local\Programs\OSGeo4W\bin\geos_c.dll

# Email — Mailtrap SMTP
EMAIL_HOST=sandbox.smtp.mailtrap.io
EMAIL_PORT=2525
EMAIL_HOST_USER=your_mailtrap_user
EMAIL_HOST_PASSWORD=your_mailtrap_password
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=no-reply@qlbenhvien.local

# Google Maps (tùy chọn — dùng cho map picker trong admin)
GOOGLE_MAPS_API_KEY=
```

---

## 6) Cài đặt & khởi chạy

```powershell
# 1. Tạo môi trường ảo
python -m venv venv
venv\Scripts\activate

# 2. Cài dependencies
pip install -r requirements.txt
pip install python-dotenv openpyxl

# 3. Migrate database
venv\Scripts\python.exe manage.py migrate

# 4. Chạy server
venv\Scripts\python.exe manage.py runserver
```

Truy cập:
- Trang chủ: http://127.0.0.1:8000/
- Admin tùy biến: http://127.0.0.1:8000/quan-tri/
- Django Admin: http://127.0.0.1:8000/admin/

---

## 7) Tạo tài khoản quản trị

```powershell
venv\Scripts\python.exe manage.py createsuperuser
```

Test email Mailtrap:
```powershell
venv\Scripts\python.exe manage.py send_test_email --to your@email.com
```

---

## 8) Import dữ liệu từ Excel

File Excel cần có các sheet: `Hospitals`, `Departments`, `Doctors`, `DoctorSchedules`.

```powershell
# Import toàn bộ (tự nhận diện sheet)
venv\Scripts\python.exe manage.py import_hospitals data.xlsx

# Cập nhật bản ghi cũ theo tên + địa chỉ
venv\Scripts\python.exe manage.py import_hospitals data.xlsx --update-existing

# Chỉ import một sheet cụ thể
venv\Scripts\python.exe manage.py import_hospitals data.xlsx --sheet Hospitals
```

### Cấu trúc các sheet

**Hospitals** (bắt buộc: `ten`, `lat`, `lon`):

| Cột | Bắt buộc | Ghi chú |
|---|---|---|
| `ten` | ✅ | Tên bệnh viện |
| `lat`, `lon` | ✅ | Tọa độ WGS84 |
| `dia_chi` | | Địa chỉ |
| `phuong` | | Phường/quận |
| `loai_hinh` | | `cong` / `tu` / `qt` |
| `co_bhyt` | | `True`/`False` |
| `cap_cuu_24h` | | `True`/`False` |
| `gio_mo`, `gio_dong` | | Giờ mở/đóng (HH:MM) |
| `hinh_anh_paths` | | Đường dẫn ảnh, phân tách bằng `\|` hoặc `;` |

**Departments** (bắt buộc: `ten`, `benh_vien`):

| Cột | Ghi chú |
|---|---|
| `ten` | Tên khoa |
| `benh_vien` | Tên bệnh viện (phải khớp chính xác) |

**Doctors** (bắt buộc: `ho_ten`, `so_dien_thoai`, `benh_vien`):

| Cột | Ghi chú |
|---|---|
| `ho_ten` | Họ tên bác sĩ |
| `so_dien_thoai` | Dùng làm khóa định danh |
| `chuyen_khoa` | Chuyên khoa |
| `khoa` | Tên khoa (phải thuộc bệnh viện) |
| `benh_vien` | Tên bệnh viện |

**DoctorSchedules** (bắt buộc: `bac_si`, `thu`):

| Cột | Ghi chú |
|---|---|
| `bac_si` | SĐT hoặc tên bác sĩ |
| `thu` | 0=CN, 1=T2 ... 6=T7 |
| `gio_bat_dau`, `gio_ket_thuc` | Mặc định 07:30–16:30 |
| `nghi` | `True`/`False` |

> **Lưu ý ảnh:** Đường dẫn ảnh được resolve theo: (1) thư mục chứa `data.xlsx`, (2) thư mục cha project. Nếu không tìm thấy sẽ báo `Images skipped`.

---

## 9) Liên kết lại ảnh bệnh viện

Dùng khi ảnh đã có trong thư mục `media/benh_vien/<old_id>/` nhưng chưa có bản ghi `BenhVienHinhAnh` trong DB.

```powershell
# Relink với offset mặc định (new_id = old_id - 99)
venv\Scripts\python.exe manage.py relink_hospital_images

# Xem trước không ghi DB
venv\Scripts\python.exe manage.py relink_hospital_images --dry-run

# Tùy chỉnh offset
venv\Scripts\python.exe manage.py relink_hospital_images --offset 99

# Giữ nguyên ảnh cũ, chỉ thêm ảnh mới
venv\Scripts\python.exe manage.py relink_hospital_images --keep-existing
```

> Command tự động loại trùng ảnh theo MD5 hash, gán ảnh đầu tiên làm ảnh đại diện.

---

## 10) Seed dữ liệu mẫu

### Reset & seed cơ bản
```powershell
# Xóa dữ liệu nghiệp vụ (giữ lại BenhVien & ảnh)
venv\Scripts\python.exe manage.py seed_core --purge-only

# Tạo dữ liệu mẫu (bệnh nhân, bác sĩ, lịch khám)
venv\Scripts\python.exe manage.py seed_core --seed-only
```

### Demo presets (cho thuyết trình)
```powershell
# Demo trang chủ / bản đồ / lọc
venv\Scripts\python.exe manage.py seed_demo --preset home

# Demo đặt / hủy / đặt lại lịch
venv\Scripts\python.exe manage.py seed_demo --preset booking

# Demo luồng bác sĩ khám + phiếu khám
venv\Scripts\python.exe manage.py seed_demo --preset doctor

# Không reset dữ liệu trước khi áp preset
venv\Scripts\python.exe manage.py seed_demo --preset doctor --no-reset
```

**Tài khoản demo:**

| Vai trò | Username | Password |
|---|---|---|
| Bệnh nhân | `0909000001` | `123456` |
| Bác sĩ | `bsdemo` | `123456` |

---

## 11) Các lệnh quản lý khác

```powershell
# Cập nhật trường phuong hàng loạt
venv\Scripts\python.exe manage.py update_hospital_phuong

# Gửi test email
venv\Scripts\python.exe manage.py send_test_email --to test@example.com

# Kiểm tra cấu hình project
venv\Scripts\python.exe manage.py check
```

---

## 12) Luồng demo đề xuất

1. **Trang chủ** → lọc theo phường, loại hình, BHYT, tìm theo tên.
2. **Tìm gần vị trí** → nhập lat/lon hoặc dùng GPS, lọc theo bán kính.
3. **Chi tiết bệnh viện** → xem gallery ảnh, giờ làm việc, chỉ đường.
4. **Đăng nhập bệnh nhân** → đặt lịch khám (chọn khoa → bác sĩ → ngày/giờ).
5. **Lịch sắp tới** → hủy lịch, kiểm tra thông báo.
6. **Đăng nhập bác sĩ** → bắt đầu khám → tạo phiếu khám → upload ảnh.
7. **Bệnh nhân** → xem lịch sử khám & phiếu khám.
8. **Admin** `/quan-tri/` → xem dashboard, sửa thông tin bệnh viện, quản lý tài khoản.

---

## 13) Chạy tests

```powershell
venv\Scripts\python.exe -m pip install -r requirements-dev.txt
venv\Scripts\python.exe manage.py test
```

Test bao gồm: `DoctorFlowTests`, kiểm tra booking, tạo phiếu khám, chuyển trạng thái lịch khám, phân quyền view.

---

## 14) Công nghệ & Tham khảo

| Thư viện / Dịch vụ | Link |
|---|---|
| Django | https://docs.djangoproject.com/ |
| GeoDjango | https://docs.djangoproject.com/en/stable/ref/contrib/gis/ |
| PostGIS | https://postgis.net/ |
| Leaflet.js | https://leafletjs.com/ |
| OpenStreetMap | https://www.openstreetmap.org/ |
| OSRM (routing) | https://project-osrm.org/ |
| Nominatim (geocoding) | https://nominatim.org/ |
| Mailtrap SMTP | https://mailtrap.io/blog/django-send-email/ |
| AOS (animation) | https://michalsnik.github.io/aos/ |
| Anime.js | https://animejs.com/ |
| jsDelivr CDN | https://www.jsdelivr.com/ |

---

## VNPAY Sandbox tại quầy sau khám

**TASK 02 — Danh mục dịch vụ và bảng giá chung:** truy cập `/quan-tri/dich-vu/` bằng tài khoản Admin, hoặc chọn “Dịch vụ & bảng giá” trên dashboard. Xem [báo cáo và hướng dẫn TASK 02](docs/TASK02_DANH_MUC_DICH_VU.md). Module này chưa tự tính lại tiền hóa đơn; luồng VNPAY hiện có được giữ nguyên.

**TASK 03 — Bác sĩ ghi nhận dịch vụ:** vào `/bac-si/`, mở lịch được phân công để chọn dịch vụ, số lượng và ghi chú ngay trong form khám. Xem [báo cáo triển khai TASK 03](docs/TASK03_DICH_VU_THUC_HIEN.md).

**TASK 04 — Hóa đơn tự động:** hoàn tất khám sẽ chốt giá và lập hóa đơn từ dịch vụ. Bác sĩ được điều chỉnh hóa đơn tự động chưa thanh toán khi không có giao dịch đang xử lý/chưa rõ kết quả; mỗi lần đổi chi phí lưu phiên bản và lịch sử. Hóa đơn cũ được bảo toàn. Trang `/thanh-toan/` hiển thị chi tiết và dùng tổng đã lưu cho VNPAY, không còn nhập tiền thủ công. Xem [báo cáo và hướng dẫn kiểm thử TASK 04](docs/TASK04_HOA_DON_TU_DONG.md).

**TASK 05 — BHYT MÔ PHỎNG:** Admin cấu hình theo dịch vụ tại `/quan-tri/bhyt-mo-phong/`. Hóa đơn tách tổng chi phí, hỗ trợ mô phỏng và phần bệnh nhân trả; hỗ trợ toàn bộ thì tự hoàn tất 0đ, không tạo VNPAY. Hóa đơn cũ không tự thay đổi. Xem [báo cáo và cách cấu hình TASK 05](docs/TASK05_BHYT_MO_PHONG.md), gồm giới hạn phần phải trả dưới 5.000 VND.

**Máy mới / gửi project cho bạn:** xem [hướng dẫn từ clone đến thanh toán Sandbox trên Windows](docs/CHAY_VNPAY_MAY_MOI.md), gồm lệnh tạo database PostGIS, môi trường Python, tài khoản admin, dữ liệu demo, GDAL, ngrok và IPN.

Đã bổ sung module thu thử tại `/thanh-toan/`. Xem [hướng dẫn VNPAY Sandbox](docs/VNPAY_SANDBOX.md) để nạp biến môi trường, migrate, cấp quyền, chạy Django/ngrok cổng 8000, cập nhật IPN trên VNPAY SIT Testing và thanh toán bằng thẻ thử.

IPN: `/thanh-toan/vnpay/ipn/`. ReturnURL: `/thanh-toan/vnpay/return/`. Chỉ IPN hợp lệ xác nhận thanh toán VNPAY; giao dịch chưa rõ kết quả không được tự thử lại. Hóa đơn 0đ do BHYT MÔ PHỎNG được hoàn tất riêng, không qua VNPAY.

**TASK 06 — Thu ngân và tiền mặt:** Admin tạo/phân công thu ngân tại `/quan-tri/thu-ngan/`; thu ngân dùng `/thanh-toan/` trong phạm vi một bệnh viện. Hỗ trợ thu đủ bằng tiền mặt, kể cả số phải trả 1–4.999đ, dùng chung khóa chống thu trùng với VNPAY. Admin giám sát toàn hệ thống tại `/quan-tri/thu-ngan/hoa-don/`. Xem [hướng dẫn và báo cáo TASK 06](docs/TASK06_THU_NGAN_TIEN_MAT.md).

## Ghi chú kỹ thuật

**TASK 07 — PDF và Mailtrap Testing:** trang hóa đơn có nút xem/in/tải phiếu nội bộ A4 tiếng Việt và trạng thái email. Tiền mặt/IPN thành công sẽ gửi PDF sau commit, chỉ vào Mailtrap Testing; có khóa chống gửi trùng và thử lại có kiểm soát. Xem [hướng dẫn cấu hình, URL và đối soát email](docs/TASK07_PDF_MAILTRAP.md).

- **Username = số điện thoại**: bệnh nhân đăng nhập bằng `so_dien_thoai`, được lưu làm `User.username`.
- **Cache**: trang chủ cache kết quả filter 60 giây (LocMemCache, per-process).
- **GIS**: tọa độ lưu theo EPSG:4326 (WGS84). Tự động convert từ Web Mercator nếu cần.
- **Ảnh**: tối đa 5MB/file, chấp nhận jpg/jpeg/png/webp.
- **Phân quyền admin**: kiểm tra `is_staff` hoặc `is_superuser` trong mọi view `/quan-tri/`.
- **Quên mật khẩu**: flow 3 bước qua session + OTP email (không dùng token URL).
