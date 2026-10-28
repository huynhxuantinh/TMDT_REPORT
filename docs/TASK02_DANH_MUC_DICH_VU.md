# TASK 02 — Danh mục dịch vụ y tế và bảng giá chung

Đã triển khai trực tiếp trên QL_BENHVIEN hiện tại. Một danh mục và một đơn giá chung cho toàn hệ thống; không có khóa bệnh viện hoặc bảng giá theo bệnh viện. Chưa nối danh mục vào phiếu khám hoặc tính tiền hóa đơn.

## 1. File đã tạo/chỉnh sửa

| File | Nội dung |
| --- | --- |
| `core/models.py` | Thêm `DanhMucDichVu`, validator, constraint và chặn xóa cứng qua ORM |
| `core/migrations/0020_danh_muc_dich_vu.py` | Chỉ tạo bảng danh mục mới; phụ thuộc 0019 |
| `core/service_catalog/__init__.py` | Package quản lý danh mục |
| `core/service_catalog/forms.py` | Form tiếng Việt, chuẩn hóa mã, khóa mã khi chỉnh sửa, tiền nguyên VND |
| `core/service_catalog/services.py` | Phân quyền, lưu/cập nhật trạng thái, khóa bản ghi và audit trong cùng transaction |
| `core/service_catalog/views.py` | Danh sách, form, chi tiết, tìm kiếm/lọc và cập nhật trạng thái |
| `core/urls.py` | Bổ sung năm route quản lý dịch vụ |
| `core/templates/core/custom_admin_dashboard.html` | Thêm liên kết “Dịch vụ & bảng giá” ở thanh danh mục |
| `core/templates/core/service_catalog/base.html` | Giao diện cùng font, màu, thẻ nội dung và nút của khu vực quản trị hiện tại |
| `core/templates/core/service_catalog/list.html` | Danh sách, tìm kiếm, bộ lọc và phân trang |
| `core/templates/core/service_catalog/detail.html` | Chi tiết dịch vụ và lịch sử trước/sau cập nhật |
| `core/templatetags/__init__.py`, `core/templatetags/catalog_money.py` | Định dạng tiền `100.000 VND` bằng Decimal |
| `core/management/commands/seed_dich_vu_demo.py` | Command dữ liệu giả định, chạy lặp không trùng/ghi đè |
| `core/test_service_catalog.py` | 27 test mới cho danh mục, isolation thanh toán và migration |
| `README.md`, `docs/TASK02_DANH_MUC_DICH_VU.md` | Hướng dẫn sử dụng và báo cáo |

Form thêm/sửa tái sử dụng nguyên bản `core/templates/core/custom_admin_form.html`; không sửa template dùng chung này. Không đưa danh mục vào CRUD tổng quát có nút xóa cứng. Dashboard chỉ thêm một liên kết, không thêm truy vấn vào dashboard hoặc đổi logic quản trị hiện có.

Đã đối chiếu SHA-256 trước/sau: toàn bộ `core/payments/*.py`, template thanh toán, `core/test_vnpay.py`, migration 0001–0019, `core/views.py` và `hospital/settings.py` không thay đổi. Hai model thanh toán cũ giữ nguyên định nghĩa. Workspace hiện không có `.git`, nên kiểm tra bằng hash và test thay cho `git diff`.

## 2. Model và migration

`DanhMucDichVu` gồm:

- `id`: khóa chính ổn định để module sau tham chiếu.
- `ma_dich_vu`: tối đa 32 ký tự, unique, chuẩn hóa trim/in hoa; cho phép chữ A–Z, số, `_`, `-`, bắt đầu bằng chữ hoặc số. Form sửa giữ cố định mã và không nhận mã thay thế từ POST.
- `ten`: tên bắt buộc, tối đa 255 ký tự.
- `nhom`: nhóm bắt buộc, tối đa 100 ký tự, nhập tự do để Admin tạo nhóm mới mà không sửa source.
- `mo_ta`: mô tả tùy chọn.
- `don_gia`: DecimalField(10, 0), từ 0 đến 9.999.999.999 VND; không dùng float để tính/lưu giá. Validation từ chối giá âm, phần lẻ, NaN và Infinity trước khi ghi.
- `dang_ap_dung`: đang áp dụng/ngừng áp dụng.
- `ngay_tao`, `ngay_cap_nhat`: tự động cập nhật theo thời gian hệ thống.

Ràng buộc database: unique mã; `dichvu_don_gia_nonnegative`; `dichvu_ma_canonical`; `dichvu_required_text`. Nhóm và trạng thái được đánh index.

Không có chức năng xóa cứng. `pre_delete` chặn cả `instance.delete()` lẫn `QuerySet.delete()` cho danh mục, kể cả dịch vụ chưa sử dụng; thao tác được hỗ trợ là ngừng áp dụng rồi áp dụng lại. Điều này giữ ID và lịch sử cho các liên kết nghiệp vụ sau này. TASK 03 vẫn cần dùng `on_delete=PROTECT` tại các ForeignKey tham chiếu dịch vụ.

Migration `0020_danh_muc_dich_vu` chỉ có CreateModel cho danh mục; không ALTER/UPDATE hóa đơn, giao dịch, lịch khám, phiếu khám hoặc bảng GIS. Không có data migration tự nạp dữ liệu mẫu.

## 3. URL và thao tác Admin

| Phương thức | URL | Chức năng |
| --- | --- | --- |
| GET | `/quan-tri/dich-vu/` | Danh sách, tìm kiếm/lọc, 25 dòng/trang |
| GET/POST | `/quan-tri/dich-vu/tao/` | Thêm dịch vụ |
| GET | `/quan-tri/dich-vu/<id>/` | Xem chi tiết và lịch sử, 20 bản ghi/trang |
| GET/POST | `/quan-tri/dich-vu/<id>/sua/` | Sửa tên, nhóm, mô tả, đơn giá, trạng thái |
| POST | `/quan-tri/dich-vu/<id>/trang-thai/` | Đặt trạng thái áp dụng/ngừng áp dụng |

Tham số tìm kiếm/lọc: `q` tìm mã hoặc tên; `nhom` chọn nhóm; `trang_thai=1` đang áp dụng, `trang_thai=0` ngừng áp dụng. Có thể kết hợp các bộ lọc; phân trang giữ nguyên bộ lọc. Nhóm không phải danh sách cố định trong source.

Đăng nhập → `/quan-tri/` → **Dịch vụ & bảng giá** → **Thêm dịch vụ**. Nhập mã/tên/nhóm/giá, kiểm tra trạng thái rồi lưu. Sau khi lưu, trang chi tiết hiển thị thông tin và lịch sử. Có thể bật/tắt từ bảng danh sách hoặc trang chi tiết.

## 4. Phân quyền và audit

Giữ nguyên định nghĩa Admin hiện tại: tài khoản đang hoạt động và có `is_staff=True` hoặc `is_superuser=True`. Tài khoản `admin` hiện tại sử dụng được ngay, không cần cấp thêm quyền VNPAY hoặc tạo role mới.

Tất cả view mới kiểm tra quyền ở backend; service ghi dữ liệu cũng kiểm tra quyền. Người chưa đăng nhập chuyển tới login; bệnh nhân/bác sĩ/người dùng thông thường nhận 403. Chỉ GET để đọc, POST để ghi; CSRF vẫn được kiểm tra. Không cho sửa các trường ngày tạo/cập nhật/người thực hiện bằng dữ liệu frontend.

Sử dụng lại `LogHeThong`: lưu người thực hiện, thời gian, IP trực tiếp (`REMOTE_ADDR`), thao tác, dữ liệu trước/sau (mã, tên, nhóm, mô tả, giá, trạng thái). Giá trong JSON là chuỗi chính xác. Seed ghi rõ nguồn command, không giả mạo một tài khoản Admin.

Lưu dịch vụ và audit cùng transaction; lỗi ghi log sẽ rollback cập nhật danh mục. Chỉnh sửa khóa hàng bằng `select_for_update()` để lấy đúng dữ liệu trước thay đổi. Thao tác bật/tắt gửi trạng thái đích thay vì đảo trạng thái, nên gửi lặp không vô tình bật lại; thay đổi không có khác biệt không tạo lịch sử giả. Đây là audit theo cơ chế hệ thống hiện có, không phải kho log bất biến trước quản trị database.

## 5. Tương thích VNPAY

Không có ForeignKey/signal từ danh mục sang hóa đơn trong TASK 02. Thay đổi giá hoặc ngừng dịch vụ không cập nhật, tạo lại hoặc xóa `HoaDon`/`GiaoDichThanhToan`.

Giữ nguyên cách nhập số tiền thử nghiệm, chữ ký, IPN, ReturnURL, tham chiếu giao dịch, khóa chống thu trùng và toàn bộ URL thanh toán. Các test mới đối chiếu tất cả field của hóa đơn chưa thanh toán, giao dịch đang chờ, hóa đơn đã thanh toán và giao dịch thành công trước/sau sửa giá để xác nhận độc lập dữ liệu.

## 6. Kiểm thử thực tế

Ngày kiểm thử: **28/10/2026**. Python 3.12.10, Django 6.0.1, PostgreSQL 18/PostGIS. Chạy trên cluster kiểm thử riêng chỉ nghe `127.0.0.1:55439`, database Django test tách khỏi nghiệp vụ; không reset database nghiệp vụ.

```powershell
.\.venv\Scripts\python.exe manage.py test core.test_service_catalog core.test_vnpay core.tests.AdminDashboardTests --noinput --verbosity 2
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py check
```

| Bộ kiểm tra | Kết quả |
| --- | --- |
| Danh mục, bảo vệ thanh toán, migration | **27/27 đạt** |
| Hồi quy VNPAY TASK 01, gồm truy cập đồng thời | **38/38 đạt** |
| Dashboard/CRUD quản trị hiện có | **11/11 đạt** |
| Tổng test đã chạy | **76/76 đạt** |
| Django system check | Không có lỗi |
| Kiểm tra model/migration | No changes detected |

Các test gồm tạo/sửa/ngừng/áp dụng lại, trùng mã khác kiểu chữ, giá âm/phần lẻ/không hợp lệ, giá 0/tối đa, trường bắt buộc, sửa mã giả mạo, tìm kiếm/bộ lọc/phân trang, định dạng tiền/XSS, quyền bệnh nhân/bác sĩ, CSRF, rollback audit, chống xóa, seed idempotent và bảo vệ toàn bộ dữ liệu hóa đơn/giao dịch khi sửa danh mục. Test migration đi từ 0019 lên 0020 trên database test có sẵn hóa đơn và xác nhận hóa đơn được giữ nguyên, danh mục ban đầu rỗng.

Log chạy test cuối nằm ở `.vnpay-test/task02-tests.log` (đã được gitignore). Cluster kiểm thử được dừng sau khi hoàn tất.

Trên database nghiệp vụ hiện tại: đã áp dụng **0020 thành công**, danh mục ban đầu **0 bản ghi**, không nạp seed. Đối chiếu hash toàn bộ nội dung hai bảng hóa đơn/giao dịch ngay trước và sau migration: **không thay đổi**. Kiểm tra bằng Django test client với tài khoản `admin`: `/quan-tri/`, `/quan-tri/dich-vu/`, `/quan-tri/dich-vu/tao/`, `/thanh-toan/` đều trả **HTTP 200**. Kiểm tra HTTP này không tạo dịch vụ hoặc giao dịch nghiệp vụ.

Không thực hiện lại giao dịch trực tiếp trên cổng VNPAY/ngrok trong TASK 02. Kết quả hồi quy ở đây là test tự động, không phải một giao dịch Sandbox mới.

## 7. Chạy trên máy hiện tại và máy khác

Máy hiện tại đã migrate. Giữ Django/ngrok hoạt động như trước; tải lại `/quan-tri/` rồi mở **Dịch vụ & bảng giá**, hoặc truy cập trực tiếp:

```text
http://127.0.0.1:8000/quan-tri/dich-vu/
```

Trên máy khác, sau khi cấu hình `.env` và GDAL theo hướng dẫn project, chạy trong terminal PowerShell VS Code:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Không dùng `--fake` cho migration 0020 trên database chưa có bảng danh mục.

Muốn tự nạp sáu dịch vụ giả định sau khi đã quyết định dùng dữ liệu demo:

```powershell
.\.venv\Scripts\python.exe manage.py seed_dich_vu_demo
```

Các mã `DEMO_*` là dữ liệu đồ án, không phải giá chính thức bệnh viện. Command không ghi đè tên/giá/trạng thái của mã đã có; chạy lại không trùng bản ghi. Command này chỉ được chạy trong test tự động trong TASK 02, chưa chạy trên database nghiệp vụ.

## 8. Vấn đề còn tồn tại và chuẩn bị TASK 03

Không có lỗi trong 76 test đã chạy cho phạm vi này. Hai lỗi AuthFlowTests đã ghi nhận từ TASK 01 (test đăng ký username chỉ có số và biến `username` chưa gán trong quên mật khẩu) nằm ngoài phạm vi; không sửa và không chạy lại toàn bộ suite xác thực trong TASK 02. Không sửa schema giới thiệu có khác biệt được phát hiện ở các lượt trước.

TASK 03 có thể thêm `DichVuThucHien` liên kết phiếu khám và `DanhMucDichVu`, chỉ cho chọn dịch vụ đang áp dụng. Khi ghi nhận cần lưu snapshot mã/tên/đơn giá và số lượng, dùng Decimal cho thành tiền; các bản ghi sử dụng tham chiếu danh mục bằng ForeignKey PROTECT. `ChiTietHoaDon` sau đó sử dụng snapshot để lịch sử không phụ thuộc giá hiện tại. Giữ định danh và cơ chế xác nhận VNPAY của hóa đơn đã có.

Chưa thực hiện lựa chọn dịch vụ cho bác sĩ, tính tiền tự động, tạo hóa đơn từ dịch vụ, BHYT mô phỏng, tiền mặt, dashboard thu ngân, PDF, email hoặc hoàn tiền.
