# Chạy QL_BENHVIEN + VNPAY Sandbox từ đầu trên Windows

Hướng dẫn dành cho máy mới, dùng database demo mới. Các lệnh Django chạy trong **Terminal PowerShell của VS Code**. Ngrok chạy trong **một cửa sổ PowerShell Windows riêng**. Giữ cả hai cửa sổ hoạt động khi thanh toán.

## 1. Phần mềm cần cài trước

- Git, VS Code, Python **3.12 64-bit** (phiên bản đã dùng kiểm thử project).
- PostgreSQL kèm PostGIS đúng phiên bản PostgreSQL. Có thể cài PostGIS qua StackBuilder theo [hướng dẫn PostGIS Windows](https://postgis.net/documentation/getting_started/install_windows/released_versions/).
- OSGeo4W 64-bit có GDAL, GEOS và PROJ. Đề xuất thư mục `C:\OSGeo4W`. Xem [hướng dẫn GeoDjango trên Windows](https://docs.djangoproject.com/en/6.0/ref/contrib/gis/install/).
- [Ngrok cho Windows](https://ngrok.com/download/windows?tab=download), tài khoản ngrok và authtoken riêng.
- Terminal Code và Hash Secret VNPAY Sandbox hợp lệ, quyền truy cập trang SIT Testing để cập nhật IPN.

Không cần `pip install gdal`: GeoDjango của project nạp DLL từ OSGeo4W.

## 2. Clone và cài Python dependencies

Mở PowerShell rồi chạy:

```powershell
git clone https://github.com/huynhxuantinh/TMDT_REPORT.git QL_BENHVIEN
cd QL_BENHVIEN
code .
```

Trong VS Code: **Terminal → New Terminal → PowerShell**. Đảm bảo thư mục hiện tại có `manage.py`; nếu repository bọc thêm thư mục con, `cd` vào thư mục chứa file đó.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Dependencies đã gồm `python-dotenv` và `openpyxl`. Không bắt buộc activate `.venv`, vì mọi lệnh đều gọi thẳng interpreter của môi trường ảo.

Nếu nhận project dạng ZIP, giải nén, mở thư mục có `manage.py` trong VS Code rồi thực hiện từ bước tạo `.venv`. Không dùng `.venv` sao chép từ máy khác.

## 3. Tạo database demo riêng

Trong terminal VS Code, điều chỉnh đường dẫn PostgreSQL theo bản cài thực tế. Ví dụ với PostgreSQL 18, tài khoản `postgres`, cổng 5432:

```powershell
$pgBin = 'C:\Program Files\PostgreSQL\18\bin'
& "$pgBin\createdb.exe" -h 127.0.0.1 -p 5432 -U postgres -W ql_benhvien_demo
& "$pgBin\psql.exe" -h 127.0.0.1 -p 5432 -U postgres -W -d ql_benhvien_demo -c 'CREATE EXTENSION IF NOT EXISTS postgis;'
```

Nhập mật khẩu PostgreSQL đã đặt lúc cài phần mềm khi được hỏi. Nếu dùng tài khoản/cổng khác, thay tham số cho đúng. Chỉ tạo database một lần. Nếu báo extension không tồn tại, cài PostGIS cho đúng phiên bản PostgreSQL trước khi tiếp tục.

## 4. Tạo file cấu hình `.env`

File `.env` thật không đi theo Git. Trong terminal VS Code, tạo file từ mẫu (chỉ trên máy mới chưa có `.env`) và sinh khóa Django riêng:

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Mở `.env`, điền kết quả vừa sinh vào `SECRET_KEY` và sửa cấu hình database. Ví dụ:

```dotenv
SECRET_KEY='DAN_KHOA_DJANGO_VUA_SINH_VAO_DAY'
DB_NAME=ql_benhvien_demo
DB_USER=postgres
DB_PASSWORD='DIEN_MAT_KHAU_POSTGRESQL_TREN_MAY_NAY'
DB_HOST=127.0.0.1
DB_PORT=5432
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost
```

Thay mật khẩu, username và cổng bằng thông tin thật trên máy mới. Lưu Ctrl+S. Không dùng mật khẩu website admin hoặc mật khẩu VNPAY cho DB_PASSWORD.

## 5. Nạp thư viện GIS vào terminal Django

Chạy trong **terminal VS Code**; sửa `$gisBin` nếu cài OSGeo4W ở nơi khác:

```powershell
$gisBin = 'C:\OSGeo4W\bin'
$gdalDll = @(Get-ChildItem -LiteralPath $gisBin -File | Where-Object { $_.Name -match '^gdal\d+\.dll$' })
if ($gdalDll.Count -ne 1) { throw 'Can tim dung mot file gdalXXX.dll trong thu muc bin OSGeo4W.' }
if (-not (Test-Path (Join-Path $gisBin 'geos_c.dll'))) { throw 'Chua tim thay geos_c.dll.' }
$env:GDAL_PATH = $gdalDll[0].FullName
$env:GEOS_PATH = Join-Path $gisBin 'geos_c.dll'
$env:PATH = "$gisBin;$env:PATH"
$gisRoot = Split-Path $gisBin -Parent
$env:GDAL_DATA = Join-Path $gisRoot 'share\gdal'
$env:PROJ_LIB = Join-Path $gisRoot 'share\proj'
```

Đoạn này tìm đúng tên DLL đang được cài, không mặc định máy mới giống đường dẫn `C:\Users\hoang\...` trên máy cũ.

## 6. Tạo schema, tài khoản admin và dữ liệu để thử

Vẫn trong terminal VS Code:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser --username admin
```

Nhập email và tự đặt mật khẩu website admin. Password có thể không hiện ký tự khi nhập. Superuser được thu VNPAY mặc định, không phải cấp thêm quyền. Tài khoản admin trên máy cũ không tự xuất hiện khi clone source.

Chỉ trên **database demo mới**, chạy một lần:

```powershell
.\.venv\Scripts\python.exe manage.py seed_core --seed-only
.\.venv\Scripts\python.exe manage.py seed_demo --preset doctor --no-reset
.\.venv\Scripts\python.exe manage.py seed_dich_vu_demo
```

Các lệnh tạo dữ liệu mẫu, gồm một ca `xong` và phiếu khám để hiện trong trang thanh toán. Không cần import Excel để thử nhanh VNPAY. Dữ liệu/ảnh trên máy cũ không tự đi theo Git.

Danh mục `DEMO_*` dùng giá giả định cho đồ án. Từ TASK 04, bác sĩ cần ghi nhận dịch vụ để lập hóa đơn; ca mẫu đã xong nhưng chưa có dịch vụ không còn dùng cách nhập tiền thủ công.

Không chạy lại seed trên database đang sử dụng: dù có `--no-reset`, preset vẫn sửa ba lịch khám mẫu và phiếu khám liên quan. Không bỏ các cờ `--seed-only`/`--no-reset`, vì mặc định các command có bước purge dữ liệu.

## 7. Mở ngrok ở cửa sổ PowerShell Windows riêng

Mở Start → PowerShell. Thiết lập authtoken ngrok của người chạy máy mới một lần:

```powershell
$ngrokSecret = Read-Host 'Nhap authtoken ngrok cua ban' -AsSecureString
$ngrokToken = [System.Net.NetworkCredential]::new('', $ngrokSecret).Password
ngrok config add-authtoken $ngrokToken
Remove-Variable ngrokSecret, ngrokToken
ngrok http http://127.0.0.1:8000
```

Nếu ngrok đã được cấu hình authtoken, chỉ chạy dòng cuối. Sao chép địa chỉ **HTTPS** trong dòng Forwarding. Giữ cửa sổ ngrok mở. Dùng `127.0.0.1` để tránh lỗi chuyển `localhost` sang IPv6 `[::1]`.

Domain của máy mới có thể khác máy cũ: không mặc định dùng `spectacle-surname-emphases.ngrok-free.dev`.

## 8. Nạp VNPAY và chạy Django

Quay lại **terminal VS Code vừa nạp GIS**. Thực hiện từng nhóm lệnh, nhập dữ liệu khi được hỏi:

```powershell
$env:VNPAY_TMN_CODE = Read-Host 'Nhap Terminal Code VNPAY Sandbox'
```

```powershell
$secret = Read-Host 'Nhap Hash Secret VNPAY Sandbox' -AsSecureString
$env:VNPAY_HASH_SECRET = [System.Net.NetworkCredential]::new('', $secret).Password
Remove-Variable secret
```

```powershell
$env:VNPAY_PUBLIC_BASE_URL = (Read-Host 'Dan HTTPS Forwarding URL ngrok cua may nay').Trim().TrimEnd('/')
.\.venv\Scripts\python.exe manage.py vnpay_config
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Hash Secret là khóa tích hợp VNPAY, không phải mật khẩu đăng nhập VNPAY hay PostgreSQL. Terminal Code và Hash Secret phải cùng một terminal Sandbox.

Chờ thấy `Starting development server at http://127.0.0.1:8000/`. Nếu lệnh trước báo lỗi, xử lý lỗi trước khi chạy tiếp. `vnpay_config` chỉ kiểm tra cấu hình có đủ, chưa chứng minh credential hợp lệ tại VNPAY.

## 9. Kiểm tra và cấu hình IPN trên VNPAY

Mở trình duyệt vào:

```text
http://127.0.0.1:8000/thanh-toan/vnpay/ipn/
```

Sau đó mở:

```text
https://DOMAIN-NGROK-MOI/thanh-toan/vnpay/ipn/
```

Nếu ngrok có trang cảnh báo, chọn Visit Site. Cả hai địa chỉ phải trả:

```json
{"RspCode": "97", "Message": "Invalid Signature"}
```

Đây là kết quả đúng của request kiểm tra không có chữ ký, xác nhận đường truyền tới Django.

Mở https://sandbox.vnpayment.vn/vnpaygw-sit-testing/ipn, chọn đúng Terminal Code → **Sửa** → nhập IPN URL đầy đủ được lệnh `vnpay_config` in ra → **Cập nhật**:

```text
https://DOMAIN-NGROK-MOI/thanh-toan/vnpay/ipn/
```

ReturnURL riêng là `https://DOMAIN-NGROK-MOI/thanh-toan/vnpay/return/`, Django tự gửi; không dán vào ô IPN.

Nếu dùng chung một Terminal Code với máy cũ, cập nhật IPN sang máy mới sẽ chuyển nơi nhận callback của terminal đó. Chạy demo lần lượt, tránh giao dịch trên hai máy cùng lúc với một cấu hình IPN. Khi ngrok đổi domain, nạp lại biến URL, khởi động lại Django và cập nhật IPN trên VNPAY.

## 10. Thanh toán thử

Từ TASK 05, kiểm tra **phần bệnh nhân phải thanh toán** sau BHYT MÔ PHỎNG. Nếu 0đ thì hóa đơn đã tự hoàn tất, không có nút VNPAY. Không có quy tắc/thẻ đủ điều kiện thì hỗ trợ bằng 0; xem [cách Admin cấu hình TASK 05](TASK05_BHYT_MO_PHONG.md).

1. Đăng nhập bác sĩ demo `bsdemo` / `123456` (chỉ có sau seed trên DB demo mới), vào `http://127.0.0.1:8000/bac-si/`.
2. Mở lịch được phân công, chọn dịch vụ demo trong form khám, nhập số lượng và hoàn tất khám. Với ca mẫu đã có phiếu nhưng chưa có hóa đơn, mở **Sửa phiếu**, thêm dịch vụ và lưu. Hệ thống tự tạo hóa đơn từ chi phí dịch vụ.
3. Đăng xuất bác sĩ; đăng nhập `admin` với mật khẩu bạn đã tạo, mở `http://127.0.0.1:8000/thanh-toan/`, chọn ca vừa khám, kiểm tra chi tiết/tổng tiền rồi bấm **Thanh toán VNPAY**. Xem [hướng dẫn TASK 04](TASK04_HOA_DON_TU_DONG.md) để thử điều chỉnh chi phí trước thanh toán.
4. Chọn thẻ nội địa NCB trên Sandbox. Thẻ thử công bố trong [tài liệu VNPAY](https://sandbox.vnpayment.vn/apis/files/Guid_Plugin_Joomla%20VirtueMart_VNpay.pdf):

| Trường | Giá trị |
| --- | --- |
| Ngân hàng | NCB |
| Số thẻ | 9704198526191432198 |
| Chủ thẻ | NGUYEN VAN A |
| Ngày phát hành | 07/15 |
| OTP | 123456 |

Hóa đơn chỉ được thanh toán sau IPN hợp lệ. Nếu còn pending, kiểm tra Django/ngrok/IPN rồi tải lại; không tạo lần thu khác hoặc sửa trực tiếp database để đánh dấu thành công.

## 11. Những lần mở lại project

Không tạo lại `.venv`, database, superuser hoặc chạy seed.

- Terminal Windows: `ngrok http http://127.0.0.1:8000`.
- Terminal VS Code: vào thư mục có `manage.py`, chạy lại đoạn nạp GIS ở bước 5, nạp ba biến VNPAY ở bước 8, rồi chạy Django.
- `.env` tự nạp lại thông tin PostgreSQL. Các biến `$env:...` chỉ tồn tại trong terminal đã nhập, phải nạp lại nếu mở terminal mới.

## 12. Gửi project và xử lý lỗi thường gặp

Gửi source, migration đến 0019, requirements và tài liệu này. Không gửi `.env`, `.venv`, `.vnpay-test` hay log chứa thông tin riêng. Nếu gửi ZIP, tự bỏ các mục đó; `.gitignore` chỉ có tác dụng với Git. Thông tin VNPAY được chủ tài khoản cung cấp riêng nếu cho phép dùng chung, không commit Hash Secret.

| Hiện tượng | Kiểm tra |
| --- | --- |
| Không tìm thấy GDAL | OSGeo4W có DLL, đúng `$gisBin`, đã nạp GIS trong cùng terminal chạy Django |
| `no password supplied` | `.env` cùng cấp `manage.py`, DB_PASSWORD đã điền và lưu |
| `password authentication failed` | Username/mật khẩu PostgreSQL trên máy mới |
| `502`, `ERR_NGROK_8012` | Django đã chạy chưa; ngrok phải trỏ `http://127.0.0.1:8000` |
| `403` trang thanh toán | Dùng superuser mới tạo; admin/staff thường cần quyền `core.thu_vnpay` |
| Thiếu biến VNPAY | Nạp đủ ba biến trong cùng terminal chạy Django |
| Trang thu tiền trống | Chưa có lịch `xong` kèm phiếu; dùng dữ liệu demo ở bước 6 trên DB mới |
| `relation already exists` khi migrate | Có thể đã dùng DB/backup cũ, không phải DB mới; kiểm tra schema và lịch sử migration, không tự xóa bảng hay dùng `--fake` hàng loạt |

Danh sách đầy đủ file, ràng buộc và kiểm thử thanh toán: [VNPAY_SANDBOX.md](VNPAY_SANDBOX.md).
