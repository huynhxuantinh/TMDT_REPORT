# VNPAY Sandbox — QL_BENHVIEN / MedCare GIS

> Cập nhật TASK 05: số gửi VNPAY là phần bệnh nhân phải trả sau **BHYT MÔ PHỎNG**. Hỗ trợ toàn bộ thì hóa đơn tự hoàn tất 0đ, không tạo giao dịch VNPAY; phần phải trả 1–4.999 VND được giữ nguyên và chặn gửi theo giới hạn hiện có. Xem [TASK 05](TASK05_BHYT_MO_PHONG.md). Các hướng dẫn IPN/ngrok bên dưới giữ nguyên.

Tài liệu cấu hình VNPAY Sandbox tại quầy sau khám. Từ TASK 04, hóa đơn mới được tính từ dịch vụ bác sĩ ghi nhận; hóa đơn thử nghiệm cũ được bảo toàn. Xem [báo cáo TASK 04](TASK04_HOA_DON_TU_DONG.md) cho quy tắc giá chốt, điều chỉnh chi phí và kết quả kiểm thử mới nhất.

## 1. Luồng đã tích hợp

1. Bác sĩ chọn dịch vụ và hoàn thành phiếu khám. Hệ thống tính chi phí và tạo hóa đơn; `LichKham.trang_thai` phải là `xong`, có `PhieuKham` và bệnh nhân trên phiếu phải khớp lịch khám.
2. Nhân viên mở `/thanh-toan/`, chọn ca đã hoàn tất.
3. Kiểm tra dịch vụ, đơn giá đã chốt, số lượng và tổng hóa đơn (số nguyên VND, từ 5.000 đến 9.999.999.999). Không nhập số tiền thủ công. Hóa đơn tự động chưa thanh toán được bác sĩ phụ trách điều chỉnh nếu không có giao dịch đang xử lý/chưa xác minh.
4. Bấm **Thanh toán VNPAY** tại quầy khi bệnh nhân có mặt. Backend đọc số tiền từ database, tạo giao dịch tham chiếu mới, ký URL rồi chuyển sang Sandbox.
5. Chọn phương thức thanh toán trên trang VNPAY; không ép ngân hàng bằng `vnp_BankCode`.
6. ReturnURL hiển thị trạng thái đã lưu. Chỉ IPN hợp lệ được cập nhật giao dịch và hóa đơn.

Superuser được thu tiền mặc định. Tài khoản Admin/staff (`is_staff=True`) cần thêm quyền `core.thu_vnpay`. Bệnh nhân, bác sĩ thông thường và tài khoản chưa được cấp quyền không được truy cập quầy hoặc bắt đầu thanh toán. Bác sĩ lập hóa đơn tự động thông qua hoàn tất khám của mình. Việc bệnh nhân có mặt tại quầy là quy trình vận hành của nhân viên; ứng dụng kiểm soát quyền tài khoản, không xác minh vị trí vật lý.

## 2. File bổ sung ở TASK 01 (lịch sử triển khai)

| File | Nội dung |
| --- | --- |
| `hospital/settings.py` | Biến môi trường VNPAY, địa chỉ Sandbox cố định, thêm đúng host/origin ngrok vào ALLOWED_HOSTS và CSRF_TRUSTED_ORIGINS |
| `core/models.py` | `HoaDon`, `GiaoDichThanhToan`, quyền thu tiền và các ràng buộc |
| `core/migrations/0019_vnpay_sandbox.py` | Migration bổ sung, phụ thuộc migration 0018 hiện có |
| `core/payments/__init__.py` | Package thanh toán |
| `core/payments/vnpay.py` | Mã hóa tham số, ký/xác minh HMAC SHA512, URL và phân loại kết quả |
| `core/payments/services.py` | Lập hóa đơn, tạo giao dịch, xử lý IPN với transaction và khóa bản ghi |
| `core/payments/views.py` | Form số tiền, phân quyền, các endpoint và xử lý lỗi |
| `core/urls.py` | Đăng ký sáu đường dẫn thanh toán |
| `core/templates/core/payments/base.html` | Giao diện chung theo màu sắc trang quản trị hiện có |
| `core/templates/core/payments/list.html` | Danh sách ca hoàn tất, phân trang |
| `core/templates/core/payments/detail.html` | Khoản phải thu, nút thanh toán, lịch sử và URL cấu hình |
| `core/templates/core/payments/return.html` | Kết quả chỉ đọc, không tiết lộ thông tin bệnh nhân trên callback công khai |
| `core/templates/core/custom_admin_dashboard.html` | Liên kết đến trang thu tiền cho tài khoản có quyền |
| `core/management/commands/vnpay_config.py` | In chính xác IPN/Return URL sau khi nạp cấu hình, không in bí mật |
| `core/test_vnpay.py` | 38 test giao thức, nghiệp vụ, quyền, CSRF, rollback và truy cập đồng thời |
| `.gitignore` | Bỏ qua `.venv/` và `.vnpay-test/` chứa database/log kiểm thử riêng |
| `README.md`, `docs/VNPAY_SANDBOX.md` | Liên kết và hướng dẫn triển khai/kiểm thử |

Ở TASK 01 không sửa `core/views.py`, luồng khám, GIS, đăng nhập/đăng ký hoặc migration cũ. Các thay đổi tích hợp hóa đơn tự động sau này được liệt kê riêng trong báo cáo TASK 04.

## 3. Dữ liệu và chống thu trùng

`HoaDon` liên kết OneToOne với `LichKham`: database không cho hai hóa đơn cùng lịch khám. Có số tiền, trạng thái `unpaid/paid`, cờ `can_doi_soat` khóa thu khi nhận kết quả mâu thuẫn, người tạo, ngày tạo/cập nhật/thanh toán. Liên kết dùng `PROTECT` để không mất hóa đơn khi xóa lịch khám.

`GiaoDichThanhToan` lưu snapshot số tiền, tham chiếu UUID duy nhất, phương thức VNPAY, terminal code, người tạo, trạng thái, mã phản hồi, trạng thái và mã giao dịch của VNPAY, thời gian thanh toán phía VNPAY, thời điểm tạo/cập nhật/hết hạn/hoàn tất. Hash Secret không được lưu trong model.

Các constraint database:

- `hoadon_valid_amount`, `giaodich_valid_amount`: khoảng tiền hợp lệ.
- `hoadon_valid_state`, `giaodich_valid_state`: trạng thái và thời gian hoàn tất phải nhất quán.
- `giaodich_vnpay_only`: chỉ phương thức VNPAY.
- `giaodich_one_pending_invoice`: tối đa một giao dịch đang chờ cho một hóa đơn.
- `giaodich_one_success_invoice`: tối đa một giao dịch thành công cho một hóa đơn.
- Unique cho `HoaDon.lich_kham` và `GiaoDichThanhToan.ma_tham_chieu`.

Tạo hóa đơn khóa lịch khám; bắt đầu thanh toán khóa lịch khám rồi hóa đơn. IPN khóa hóa đơn rồi giao dịch. Các cập nhật nằm trong `transaction.atomic()`. Số tiền gửi từ trình duyệt ở bước bắt đầu thanh toán bị bỏ qua. Không cho thu thêm nếu hóa đơn đã trả hoặc còn giao dịch chờ xác nhận.

Quy trình xác minh IPN: kiểm tra HMAC → tìm tham chiếu → kiểm tra terminal và số tiền × 100 → kiểm tra dữ liệu/mã kết quả → kiểm tra trạng thái hiện tại → cập nhật nguyên tử. Chữ ký sắp xếp khóa, URL-encode kiểu `quote_plus`, bỏ trường hash và trường rỗng; so sánh bằng `hmac.compare_digest`. Tham số VNPAY bị lặp bị từ chối.

Phản hồi JSON IPN dùng `RspCode` và `Message`: `00` đã ghi nhận, `02` đã xác nhận trước đó, `01` không tìm thấy, `04` sai tiền, `97` sai chữ ký/terminal, `99` dữ liệu chưa xử lý được hoặc cần retry. GET công khai, không yêu cầu đăng nhập. Các thao tác của nhân viên dùng POST và CSRF.

Kết quả `00/00` mới được ghi thành công. Khi transaction status là `02`, response `24` là hủy, `11` là hết hạn, các mã thất bại xác định `09/10/12/13/51/65/75/79` là thất bại và được thử lại. Với giao dịch đang chờ, mã chưa rõ, `99`, nghi ngờ gian lận, đảo giao dịch hoặc kết quả mâu thuẫn vẫn giữ chờ. Nếu nhận kết quả mâu thuẫn sau khi giao dịch đã kết thúc, hệ thống giữ bản ghi cũ và bật `can_doi_soat` để khóa lần thu mới. Đây là cách xử lý thận trọng của module, không suy diễn mọi mã khác `00` thành thất bại. [Đặc tả VNPAY](https://sandbox.vnpayment.vn/apis/docs/thanh-toan-pay/pay.html), [bảng mã](https://sandbox.vnpayment.vn/apis/docs/bang-ma-loi/).

**Đóng trình duyệt, mất ngrok hoặc quá hạn không cho phép thu lại.** `pending` và `timeout` đều khóa VNPAY, tiền mặt và sửa dịch vụ. IPN hợp lệ đến muộn vẫn tất toán. Admin có thể đối soát bằng `querydr` tại màn giám sát hóa đơn: chỉ kết quả cuối cùng đã xác minh mới xử lý hóa đơn; `01` (chưa hoàn tất), mã 91 hoặc không truy vấn được đều giữ khóa. Không sửa trực tiếp trạng thái database để thu lại. Chi tiết trong [báo cáo P1](P1_BAO_MAT_THANH_TOAN.md).

## 4. URL chính xác trong source

| Phương thức | Đường dẫn | Mục đích |
| --- | --- | --- |
| GET | `/thanh-toan/` | Danh sách ca khám đã hoàn tất |
| GET | `/thanh-toan/lich-kham/<lich_id>/` | Xem khoản phải thu/lịch sử |
| POST | `/thanh-toan/lich-kham/<lich_id>/hoa-don/` | Lập hóa đơn từ dịch vụ cho ca hoàn tất chưa có hóa đơn |
| POST | `/thanh-toan/hoa-don/<invoice_id>/vnpay/` | Bắt đầu thanh toán |
| GET | `/thanh-toan/vnpay/return/` | Trình duyệt quay lại, chỉ hiển thị kết quả |
| GET | `/thanh-toan/vnpay/ipn/` | VNPAY gọi server-to-server để xác nhận |

Không có cổng riêng được thiết lập trong source. `manage.py runserver` và README hiện dùng mặc định **8000**. Hướng dẫn dưới đây chỉ định rõ 8000 cho cả Django và ngrok. Nếu bạn đổi cổng thì đổi cả hai lệnh cùng một giá trị.

## 5. Chuẩn bị Django trên Windows

Mở PowerShell tại thư mục có `manage.py`:

```powershell
Set-Location C:\Y4_S1\TMDT\QL_BENHVIEN
# Dùng interpreter đã có của project:
.\.venv\Scripts\python.exe --version
```

Nạp cấu hình PostgreSQL/PostGIS **của bạn** từ `.env` hiện có hoặc biến môi trường `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`. Không sử dụng database kiểm thử của tác vụ này làm dữ liệu nghiệp vụ. Nếu chưa có `.env`, làm theo phần cấu hình database trong README; không xóa database hoặc chạy seed có purge.

Khi cần nạp thông tin DB bằng PowerShell:

```powershell
$env:DB_NAME = Read-Host 'Ten database QL_BENHVIEN hien co'
$env:DB_USER = Read-Host 'Tai khoan PostgreSQL'
$dbSecret = Read-Host 'Mat khau PostgreSQL' -AsSecureString
$env:DB_PASSWORD = [System.Net.NetworkCredential]::new('', $dbSecret).Password
Remove-Variable dbSecret
$env:DB_HOST = '127.0.0.1'
$env:DB_PORT = '5432' # Dieu chinh neu PostgreSQL cua ban dung cong khac
```

Trên máy đã tìm thấy OSGeo4W ở đường dẫn dưới đây. Nếu terminal chưa có cấu hình GIS, nạp:

```powershell
$gisBin = Join-Path $env:LOCALAPPDATA 'Programs\OSGeo4W\bin'
$env:GDAL_PATH = Join-Path $gisBin 'gdal312.dll'
$env:GEOS_PATH = Join-Path $gisBin 'geos_c.dll'
$env:PATH = "$gisBin;$env:PATH"
```

Không cần thay đổi code GIS. Nếu cài lại OSGeo4W ở nơi khác, điều chỉnh đường dẫn DLL theo bản cài thực tế. Với môi trường mới, cài dependencies của project và `python-dotenv`; môi trường `.venv` hiện có đã chạy được các kiểm thử.

## 6. Chạy ngrok, nạp VNPAY và chạy Django

Trong terminal thứ hai:

```powershell
ngrok http 8000
```

Sao chép địa chỉ **HTTPS** ở dòng Forwarding của ngrok đang chạy cho project này. Không dùng URL SportStore cũ.

Trong terminal Django, nạp các biến sau; không đưa giá trị bí mật vào source, log hoặc ảnh chụp màn hình:

```powershell
$env:VNPAY_TMN_CODE = Read-Host 'Nhap vnp_TmnCode'
$secret = Read-Host 'Nhap vnp_HashSecret' -AsSecureString
$env:VNPAY_HASH_SECRET = [System.Net.NetworkCredential]::new('', $secret).Password
Remove-Variable secret
$env:VNPAY_PUBLIC_BASE_URL = (Read-Host 'Dan HTTPS Forwarding URL cua ngrok').Trim().TrimEnd('/')

.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py vnpay_config
.\.venv\Scripts\python.exe manage.py runserver 8000
```

Chạy Django trong **cùng terminal đã nạp biến**. Lệnh migrate bổ sung schema và quyền mới; không xóa dữ liệu cũ. Tại thời điểm bàn giao, migration chỉ được chạy trên database kiểm thử riêng, chưa chạy trên database nghiệp vụ của bạn.

Settings thêm đúng hostname ngrok vào `ALLOWED_HOSTS` và origin HTTPS vào `CSRF_TRUSTED_ORIGINS`, không dùng wildcard. Thiếu hoặc sai cấu hình thì giao diện chặn bắt đầu thanh toán và giải thích rõ. Payment URL chỉ trỏ đến Sandbox.

Nếu chưa có superuser, dùng `manage.py createsuperuser`. Nếu muốn cấp quyền cho tài khoản Admin/staff sẵn có, dừng runserver, chạy `manage.py shell` rồi nhập:

```python
from django.contrib.auth.models import User, Permission
u = User.objects.get(username=input('Username nhan vien: ').strip())
assert u.is_active and u.is_staff, 'Tai khoan phai la nhan vien dang hoat dong'
p = Permission.objects.get(content_type__app_label='core', codename='thu_vnpay')
u.user_permissions.add(p)
exit()
```

Khởi động lại runserver. Truy cập `http://127.0.0.1:8000/login/`, đăng nhập bằng tài khoản có quyền, rồi mở `http://127.0.0.1:8000/thanh-toan/`. Dashboard `/quan-tri/` cũng có liên kết. Source hiện tại không đăng ký `/admin/` mặc dù README cũ có nhắc đến; hãy dùng shell trên để cấp quyền, không dựa vào `/admin/`.

## 7. Cập nhật IPN trên VNPAY SIT Testing — bắt buộc

Lệnh `manage.py vnpay_config` in URL đầy đủ theo domain hiện tại. Công thức chính xác:

```text
IPN URL    = <VNPAY_PUBLIC_BASE_URL>/thanh-toan/vnpay/ipn/
ReturnURL  = <VNPAY_PUBLIC_BASE_URL>/thanh-toan/vnpay/return/
```

Domain chưa tồn tại khi triển khai, nên chưa thể ghi một hostname thực tế. Bạn có thể in chính xác địa chỉ sau khi chạy ngrok bằng PowerShell:

```powershell
"$($env:VNPAY_PUBLIC_BASE_URL)/thanh-toan/vnpay/ipn/"
"$($env:VNPAY_PUBLIC_BASE_URL)/thanh-toan/vnpay/return/"
```

1. Mở [VNPAY SIT Testing — IPN](https://sandbox.vnpayment.vn/vnpaygw-sit-testing/ipn), đăng nhập tài khoản Sandbox của bạn.
2. Kiểm tra **Terminal Code** đúng terminal đã nạp vào Django.
3. Chọn **Sửa** tại IPN URL đang trỏ SportStore.
4. Dán URL đầy đủ có đuôi **`/thanh-toan/vnpay/ipn/`**, giữ dấu `/` cuối.
5. Chọn **Cập nhật**, kiểm tra lại URL đã lưu.
6. ReturnURL có đuôi **`/thanh-toan/vnpay/return/`** được Django gửi qua `vnp_ReturnUrl` trong từng yêu cầu thanh toán. **Không nhập ReturnURL vào ô IPN URL.**

Có thể kiểm tra endpoint công khai trước khi giao dịch:

```powershell
Invoke-RestMethod "$($env:VNPAY_PUBLIC_BASE_URL)/thanh-toan/vnpay/ipn/" -Headers @{'ngrok-skip-browser-warning'='1'}
```

Request không có chữ ký phải trả `RspCode: 97`, `Message: Invalid Signature`; đây là dấu hiệu route đã tới Django, không phải lỗi giao dịch. Lệnh này chưa chứng minh VNPAY đã gọi IPN thành công.

Mỗi lần ngrok đổi domain: cập nhật `VNPAY_PUBLIC_BASE_URL` → khởi động lại Django → chạy `vnpay_config` → **Sửa → Cập nhật** IPN trên SIT Testing. Giữ ngrok và Django hoạt động trong suốt giao dịch. Nếu giao dịch cũ đang pending thì không tạo lần thu khác, dù đã đổi domain.

## 8. Thanh toán thử bằng thẻ Sandbox

1. Hoàn tất một ca khám bằng tài khoản bác sĩ, chọn các dịch vụ thực tế để tự tạo hóa đơn.
2. Đăng nhập tài khoản thu tiền, mở ca trong `/thanh-toan/`.
3. Kiểm tra chi tiết dịch vụ và tổng đã chốt, rồi bấm **Thanh toán VNPAY**.
4. Trên Sandbox, chọn thẻ nội địa/ngân hàng **NCB** và nhập thẻ thử:

| Trường | Giá trị thử nghiệm |
| --- | --- |
| Số thẻ | `9704198526191432198` |
| Chủ thẻ | `NGUYEN VAN A` |
| Ngày phát hành | `07/15` |
| OTP | `123456` |

Đây là thẻ test được công bố trong [hướng dẫn Sandbox của VNPAY](https://sandbox.vnpayment.vn/apis/files/Guid_Plugin_Joomla%20VirtueMart_VNpay.pdf). Chỉ sử dụng tại Sandbox.

5. Sau khi quay lại, nếu còn **chờ xác nhận IPN**, tải lại trạng thái; chưa được coi hóa đơn đã trả.
6. Kiểm tra lịch sử giao dịch và hóa đơn tại quầy: `success`, mã phản hồi `00`, trạng thái VNPAY `00`, mã giao dịch, thời điểm thanh toán.
7. Nếu lúc đầu đăng nhập bằng localhost, cookie đăng nhập không chuyển sang domain ngrok. Trang Return vẫn hiển thị trạng thái của callback có chữ ký; quay lại tab localhost để xem đầy đủ hóa đơn.

Thử hủy và hết hạn trên các ca/hóa đơn khác. Chỉ khi IPN xác nhận `cancelled/expired/failed` mới có nút thử lại; mã tham chiếu lần mới phải khác lần trước. Thử ngắt ngrok: hệ thống phải giữ pending/unpaid, không tự báo thành công. Khôi phục kết nối và kiểm tra IPN trên hệ thống Sandbox trước khi thao tác tiếp.

Có thể đối chiếu read-only trong Django shell:

```python
from core.models import HoaDon, GiaoDichThanhToan
invoice_id = int(input('ID hoa don: '))
print(list(HoaDon.objects.filter(pk=invoice_id).values('id', 'so_tien', 'trang_thai', 'ngay_thanh_toan')))
print(list(GiaoDichThanhToan.objects.filter(hoa_don_id=invoice_id).values(
    'ma_tham_chieu', 'so_tien', 'trang_thai', 'ma_phan_hoi', 'trang_thai_vnpay', 'ma_giao_dich_vnpay', 'hoan_tat_luc')))
```

## 9. Kiểm thử đã thực hiện

Kiểm thử ngày 28/10/2026 bằng Python 3.12.10, Django 6.0.1, PostgreSQL 18/PostGIS cài trên máy. Dùng cluster riêng trong `.vnpay-test/pgdata`, chỉ bind `127.0.0.1:55439`; không kết nối dữ liệu nghiệp vụ. Django tạo rồi hủy database test do nó quản lý. Cluster kiểm thử được dừng sau khi kiểm tra.

| Kiểm tra | Kết quả |
| --- | --- |
| `manage.py test core.test_vnpay --noinput --verbosity 2` | **38/38 PASS**, gồm 3 test truy cập đồng thời trên PostgreSQL thật |
| `manage.py test --noinput` (trước khi bổ sung test lệnh in cấu hình và khóa đối soát) | 66 test: 64 PASS, 2 ERROR trong `core.tests.AuthFlowTests` hiện có |
| `manage.py migrate --noinput` trên DB riêng | Áp dụng toàn bộ migration, bao gồm 0019, thành công |
| `manage.py makemigrations --check --dry-run` | Không phát sinh thay đổi model/migration |
| `manage.py check` với GIS được nạp | Không có lỗi |

Hai lỗi phần xác thực không được sửa trong task thanh toán:

- `test_register_creates_patient_linked_to_user`: test gửi username chỉ có số `0909777888`; `register()` hiện yêu cầu username gồm chữ và số nên không tạo User.
- `test_forgot_password_rejects_weak_password`: `forgot_password()` ở `core/views.py:2117` truy cập `username` chưa được gán, gây `UnboundLocalError`.

Coverage thanh toán gồm: chưa khám xong/thiếu phiếu, tiền không hợp lệ, hóa đơn trùng, URL/chữ ký/tiền từ DB, thiếu cấu hình, IPN sai chữ ký/tiền/terminal/tham chiếu, callback sai cấu trúc, thành công/thất bại/hủy/hết hạn, Return không ghi DB, mất IPN, IPN muộn/lặp/mâu thuẫn, rollback khi DB lỗi, CSRF/phân quyền, ràng buộc SQL và các tình huống race.

Các callback trong test được ký bằng **secret giả chỉ dành cho test**, mô phỏng VNPAY. Chưa kiểm thử giao dịch Sandbox thực tế, kết nối ngrok từ VNPAY hoặc migration trên database nghiệp vụ. Khi bạn đã nạp cấu hình thật, chạy lại test bằng:

```powershell
.\.venv\Scripts\python.exe manage.py test core.test_vnpay --noinput
```

Django cần quyền tạo database test và PostGIS; không trỏ `TEST.NAME` vào database nghiệp vụ.

## 10. Hóa đơn tự động và bước tiếp theo

TASK 04 đã thay bước nhập tiền thử bằng tổng dịch vụ trên server, thêm ChiTietHoaDon và phiên bản chi phí, giữ một hóa đơn cho mỗi lịch khám. Số tiền giao dịch vẫn là snapshot của hóa đơn tại lúc bắt đầu thanh toán. Chưa triển khai BHYT mô phỏng; hướng mở rộng TASK 05 được trình bày trong [báo cáo TASK 04](TASK04_HOA_DON_TU_DONG.md).

Chưa bổ sung tiền mặt, BHYT mô phỏng, hoàn tiền, PDF, email, MoMo hoặc dashboard thu ngân đầy đủ.
