# TASK 03 — Bác sĩ ghi nhận dịch vụ thực hiện

> Báo cáo dưới đây ghi nhận thời điểm hoàn tất TASK 03. Từ TASK 04, hóa đơn được tính tự động và hóa đơn tự động chưa thanh toán có thể điều chỉnh có kiểm soát. Quy tắc hiện hành: [TASK04_HOA_DON_TU_DONG.md](TASK04_HOA_DON_TU_DONG.md).

Đã bổ sung phần **DỊCH VỤ Y TẾ ĐÃ THỰC HIỆN** vào form khám, sửa phiếu và xem phiếu của MedCare GIS. Danh sách được lưu cùng thao tác hoàn thành khám/lưu phiếu. Không tự tính tiền, tạo hóa đơn hoặc thay đổi số tiền thử nghiệm VNPAY.

## 1. File đã thêm/sửa

| File | Nội dung |
| --- | --- |
| `core/models.py` | Thêm `DichVuThucHien`; giữ nguyên các model cũ |
| `core/migrations/0021_dich_vu_thuc_hien.py` | Tạo bảng và ràng buộc mới, phụ thuộc 0020 |
| `core/exam_services/__init__.py` | Package nghiệp vụ mới |
| `core/exam_services/forms.py` | Formset dịch vụ, số lượng, ghi chú, kiểm tra trùng |
| `core/exam_services/services.py` | Kiểm tra quyền/trạng thái/hóa đơn, lưu bằng transaction và audit |
| `core/views.py` | Tích hợp `doctor_exam`, `bac_si_phieu_kham_edit`; prefetch dịch vụ trong `phieu_kham_detail` |
| `core/templates/core/doctor_exam.html` | Bổ sung khu vực dịch vụ, giữ nội dung nhập khi có lỗi |
| `core/templates/core/doctor_exam_edit.html` | Bổ sung chỉnh sửa hoặc xem dịch vụ bị khóa |
| `core/templates/core/exam_detail.html` | Hiển thị dịch vụ đã lưu |
| `core/templates/core/exam_services/editor.html` | Formset, tìm kiếm, thông báo khóa, CSS thích ứng màn hình |
| `core/templates/core/exam_services/row.html` | Một dòng chọn dịch vụ/số lượng/ghi chú/xóa |
| `core/templates/core/exam_services/read_only.html` | Bảng chỉ xem trên phiếu và khi bị khóa |
| `core/static/core/js/exam_services.js` | Tìm theo mã/tên, thêm/xóa dòng, hiển thị giá danh mục |
| `core/test_exam_services.py` | 31 test chức năng, phân quyền, giao dịch đồng thời, migration |
| `core/test_service_catalog.py` | Test migration TASK 02 khôi phục migration mới nhất sau khi kiểm tra, để không bỏ mất bảng 0021 trong các test kế tiếp |
| `README.md` | Liên kết hướng dẫn TASK 03 |
| `docs/TASK03_DICH_VU_THUC_HIEN.md` | Báo cáo và hướng dẫn này |

Không sửa URL, settings, signals, mã thanh toán, mã danh mục, các migration 0001–0020 hoặc test VNPAY. Đã đối chiếu hash các file bảo vệ và AST các model/view: model cũ giữ nguyên; chỉ ba view nêu trên thay đổi.

## 2. Model và quan hệ

`PhieuKham` → nhiều `DichVuThucHien` → mỗi dòng tham chiếu một `DanhMucDichVu`.

| Trường | Kiểu / ý nghĩa |
| --- | --- |
| `id` | Khóa chính |
| `phieu_kham` | FK PROTECT; truy cập ngược `phieu.dich_vu_thuc_hien.all()` |
| `dich_vu` | FK PROTECT đến danh mục chung TASK 02 |
| `so_luong` | Số nguyên dương, mặc định 1; form giới hạn 1–2.147.483.647 |
| `ghi_chu` | Không bắt buộc, tối đa 2.000 ký tự ở form/model validation |
| `nguoi_thuc_hien` | FK PROTECT đến bác sĩ được phân công khi thêm dòng |
| `thoi_gian_tao`, `thoi_gian_cap_nhat` | Thời gian tự động |

Database có UNIQUE `(phieu_kham, dich_vu)` và CHECK `so_luong >= 1`. Phiếu, danh mục và bác sĩ đang được tham chiếu bởi dịch vụ được bảo vệ khỏi xóa cứng.

**Chưa có đơn giá chốt hoặc thành tiền trong model này.** Giá hiển thị lấy từ danh mục hiện tại và có thể đổi khi Admin sửa bảng giá. TASK 04 sẽ chốt giá trong chi tiết hóa đơn. Gợi ý snapshot ở báo cáo TASK 02 được dành cho TASK 04 theo phạm vi yêu cầu mới.

## 3. URL và cách dùng

Tận dụng các URL có sẵn; không tạo endpoint mới:

| URL | Chức năng |
| --- | --- |
| `/bac-si/` | Trang bác sĩ, chọn lịch được phân công |
| `/bac-si/lich/<lich_id>/bat-dau/` | Bắt đầu khám, POST qua nút trên giao diện |
| `/bac-si/kham/<lich_id>/` | Form khám và chọn dịch vụ trước khi hoàn tất |
| `/bac-si/phieu-kham/<phieu_id>/sua/` | Điều chỉnh dịch vụ trên phiếu đã có, khi được phép |
| `/phieu-kham/<phieu_id>/` | Xem dịch vụ cùng thông tin phiếu, theo quyền sẵn có |

`lich_id` là ID lịch khám; `phieu_id` là ID phiếu, không nhất thiết giống nhau.

1. Đăng nhập bằng **tài khoản bác sĩ được phân công**, vào lịch và bấm bắt đầu/mở form khám như trước.
2. Nhập triệu chứng, chẩn đoán, hướng điều trị và ảnh nếu có.
3. Tại khu vực dịch vụ, tìm theo mã/tên (hỗ trợ gõ không dấu), bấm kết quả để chọn. Có thể dùng ô chọn dịch vụ và nút **Thêm dòng dịch vụ**.
4. Nhập số lượng và ghi chú. Cùng một dịch vụ chỉ có một dòng; cần nhiều lần thì tăng số lượng.
5. Bấm **Xóa dòng** để loại khỏi danh sách chuẩn bị lưu; sửa số lượng trực tiếp để điều chỉnh.
6. Kiểm tra danh sách rồi bấm **Hoàn thành khám**. Với phiếu đã lập, bấm **Lưu thay đổi**.

Việc chọn/xóa dòng trên trình duyệt chưa ghi database; rời trang trước khi lưu sẽ mất thay đổi. JavaScript phục vụ tìm kiếm/thêm nhiều dòng; khi tắt JavaScript vẫn có thể thao tác những dòng đang hiển thị. Tìm kiếm hiển thị tối đa 20 kết quả mỗi lần; nhập thêm để thu hẹp.

## 4. Quyền, trạng thái và bảo vệ thanh toán

- Backend kiểm tra đúng bác sĩ được phân công. Bệnh nhân, bác sĩ khác và Admin không được dùng quyền quản trị để sửa dịch vụ của bác sĩ.
- Chỉ thay đổi dịch vụ khi lịch ở `dang` (đang khám) hoặc `xong` (đã xong). Lịch `huy` hoặc chưa bắt đầu không được ghi nhận mới.
- Hành vi cũ mở form khám chuyển `cho` sang `dang` được giữ nguyên. POST trực tiếp khi lịch vẫn `cho` mà có thêm dịch vụ sẽ bị từ chối.
- Chỉ thêm dịch vụ đang áp dụng. Dòng đã ghi nhận trước khi dịch vụ bị ngừng vẫn được giữ, sửa số lượng/ghi chú hoặc xóa nếu phiếu còn được phép sửa; không thể thêm lại sau khi đã xóa và lưu.
- Không nhận tên/đơn giá do trình duyệt gửi để thay thế danh mục. Giá chỉ được đọc từ database.
- **Có bất kỳ hóa đơn nào là khóa thêm/sửa/xóa dịch vụ**, gồm hóa đơn chưa thanh toán, giao dịch đang xử lý hoặc đã thanh toán. Đây là giới hạn tạm thời chờ TASK 04 có cơ chế tính lại hóa đơn. Giao dịch hiện có tham chiếu hóa đơn nên cũng được bảo vệ bằng kiểm tra này.
- Phiếu đã hoàn tất nhưng chưa có hóa đơn được điều chỉnh dịch vụ bởi bác sĩ phụ trách. Các trường bệnh án/ảnh giữ luồng chỉnh sửa hiện có; khóa dịch vụ không tự mở rộng sang bệnh án.
- Khóa dòng lịch khám trước khi kiểm tra/lưu, cùng thứ tự khóa với luồng tạo hóa đơn VNPAY. Nếu hóa đơn được tạo trong lúc đang mở form, kiểm tra khi lưu vẫn chặn thay đổi.
- Có mã phiên bản danh sách để phát hiện chỉnh sửa từ tab khác; bản cũ không được ghi đè. Khi gặp thông báo này, tải lại trang, kiểm tra dữ liệu mới rồi nhập lại phần cần điều chỉnh.
- Lưu dịch vụ, log dịch vụ, phiếu, cập nhật trạng thái và signals khi hoàn tất khám nằm trong transaction. Lỗi validation ảnh hoặc lỗi ghi log làm hoàn tác phần ghi dữ liệu trong transaction.

Tái sử dụng `LogHeThong` với `model_name='DichVuThucHien'`, hành động create/update/delete, người thao tác, thời gian, ID phiếu/dịch vụ/bác sĩ, mã dịch vụ và số lượng trước/sau. Log dịch vụ không chứa nội dung ghi chú, triệu chứng hay chẩn đoán; sửa ghi chú chỉ ghi dấu có thay đổi. Lịch sử bệnh án và thông báo cũ tiếp tục qua signals có sẵn.

## 5. Kiểm thử đã chạy

Ngày 28/10/2026, Python 3.12.10, Django 6.0.1, PostgreSQL 18/PostGIS. Test chạy trên cluster riêng `127.0.0.1:55439`, không reset database nghiệp vụ.

| Bộ test | Kết quả |
| --- | --- |
| TASK 03: dịch vụ, transaction, đồng thời, migration | 31/31 đạt |
| TASK 02: danh mục/bảng giá | 27/27 đạt |
| TASK 01: VNPAY, gồm xử lý đồng thời | 38/38 đạt |
| Luồng bác sĩ cũ | 7/7 đạt |
| Dashboard/CRUD Admin cũ | 11/11 đạt |
| Đặt lịch cũ | 4/4 đạt |
| Ràng buộc tọa độ GIS cũ | 3/3 đạt |
| **Tổng** | **121/121 đạt** |

Test TASK 03 bao gồm thêm một/nhiều dòng, sửa/xóa, số lượng không hợp lệ, ngừng áp dụng, trùng dịch vụ, giả mạo giá, quyền truy cập, CSRF, lịch hủy/chưa bắt đầu, hóa đơn chưa trả/đang xử lý/đã trả, dữ liệu dịch vụ bị sửa ở tab khác, rollback ảnh/log, giữ signals lịch sử/thông báo, migration bảo toàn dữ liệu cũ và cạnh tranh với tạo hóa đơn.

Lệnh chạy lại sau khi trỏ biến môi trường DB sang **database kiểm thử riêng** có PostGIS:

```powershell
.\.venv\Scripts\python.exe manage.py test core.test_exam_services core.test_service_catalog core.test_vnpay core.tests.DoctorFlowTests core.tests.AdminDashboardTests core.tests.AppointmentBookingTests core.tests.CoordinateConstraintTests --noinput --verbosity 2
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
node --check core/static/core/js/exam_services.js
```

System check không có lỗi; không còn thay đổi model thiếu migration; JavaScript qua kiểm tra cú pháp. Log test tại `.vnpay-test/task03-tests.log` (gitignored). Không chạy lại thanh toán thật qua VNPAY/ngrok; 38 bài là kiểm thử tự động. Chưa kiểm tra thao tác JavaScript/responsive bằng trình duyệt tự động, nên phần kiểm thử thủ công bên dưới vẫn cần thực hiện.

Trên database nghiệp vụ máy hiện tại: đã áp dụng **0021 thành công**, bảng dịch vụ thực hiện có **0 dòng**, chưa nạp dịch vụ hay sửa phiếu thực tế. Đối chiếu hash toàn bộ nội dung `HoaDon`, `GiaoDichThanhToan`, `LichKham`, `PhieuKham`, `DanhMucDichVu` trước/sau: **không đổi**. Render bằng Django RequestFactory với bác sĩ đang có phiếu: `/bac-si/phieu-kham/9/sua/` và `/phieu-kham/9/` đều HTTP 200, không ghi dữ liệu nghiệp vụ.

Hai lỗi xác thực đã ghi nhận ở TASK 01 (test đăng ký username chỉ có số; biến `username` chưa gán trong quên mật khẩu) ngoài phạm vi, không sửa hoặc chạy lại suite đó ở TASK 03. Không tuyên bố toàn bộ test của project đều đạt.

## 6. Chạy và kiểm thử thủ công

Máy hiện tại đã migrate. Trong **Terminal PowerShell của VS Code**, mở thư mục project; giữ cấu hình `.env`, GDAL và PostgreSQL đã chạy được ở TASK 01–02. Nếu Django đang chạy, tải lại trang; khi chưa thấy giao diện mới thì Ctrl+C tại terminal Django và khởi động lại:

```powershell
cd C:\Y4_S1\TMDT\QL_BENHVIEN
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Trên máy khác, sau khi cài môi trường theo [hướng dẫn máy mới](CHAY_VNPAY_MAY_MOI.md), chạy `python manage.py migrate` trước khi chạy server. Không dùng `--fake`. TASK 03 không cần thay đổi ngrok/IPN hoặc thông số VNPAY.

1. Admin vào `/quan-tri/dich-vu/`, chuẩn bị ít nhất hai dịch vụ đang áp dụng với giá phù hợp dữ liệu kiểm thử.
2. Dùng lịch khám thử được phân cho bác sĩ; đăng nhập đúng bác sĩ tại `/bac-si/`, bắt đầu khám.
3. Chọn hai dịch vụ, đặt số lượng khác nhau, thêm ghi chú; thử xóa một dòng rồi chọn lại. Kiểm tra đơn giá không có ô nhập sửa.
4. Bấm **Hoàn thành khám**, mở phiếu: kiểm tra dịch vụ, số lượng, ghi chú, triệu chứng/chẩn đoán/điều trị, ảnh, lịch sử và thông báo bệnh nhân.
5. Khi chưa có hóa đơn, mở **Sửa phiếu**, tăng số lượng và xóa một dịch vụ; lưu rồi mở lại để đối chiếu.
6. Thử nhập 0/âm; thử hai dòng cùng một dịch vụ. Hệ thống phải báo lỗi, giữ nội dung nhập và không lưu một phần.
7. Mở hai tab sửa cùng phiếu. Lưu ở tab thứ nhất rồi lưu thay đổi khác ở tab thứ hai: tab thứ hai phải báo danh sách đã thay đổi.
8. Ngừng áp dụng một dịch vụ tại Admin: phiếu mới không chọn được; dòng cũ vẫn hiển thị và có thể điều chỉnh/xóa nếu chưa có hóa đơn.
9. Tại quầy `/thanh-toan/`, tạo hóa đơn thử theo chức năng hiện có. Quay lại phiếu: danh sách dịch vụ phải chỉ xem, kể cả hóa đơn chưa thanh toán. Phiếu có hóa đơn trước TASK 03 cũng sẽ khóa dù danh sách đang trống.
10. Với giao dịch Sandbox đang xử lý/đã trả, kiểm tra dịch vụ tiếp tục khóa và tiền/trạng thái hóa đơn không bị chức năng này thay đổi. Không dùng lại giao dịch đã trả để thử sửa tiền.
11. Dùng bác sĩ khác hoặc bệnh nhân mở URL chỉnh sửa: phải bị chặn theo quyền hiện có. Kiểm tra giao diện trên màn hình điện thoại, tìm kiếm/thêm/xóa dòng và nút lưu.

## 7. Phần tiếp tục ở TASK 04

Chọn phí khám từ danh mục như một dịch vụ nếu cần; TASK 03 chưa tự thêm phí khám. TASK 04 sẽ xác định phí khám, tính tổng từ các dòng thực hiện, chốt mã/tên/đơn giá vào chi tiết hóa đơn, tự tạo/cập nhật hóa đơn và cho điều chỉnh dịch vụ trước thanh toán kèm tính lại tiền/audit. Việc nhập số tiền thử nghiệm VNPAY vẫn giữ nguyên đến TASK 04.

Chưa triển khai BHYT mô phỏng, tiền mặt, thu ngân hoàn chỉnh, PDF, Mailtrap hay hoàn tiền.
