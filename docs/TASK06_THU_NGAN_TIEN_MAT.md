# TASK 06 — Thu ngân và thanh toán tiền mặt

## Cách dùng

- Admin mở **`/quan-tri/thu-ngan/`** → **Tạo tài khoản thu ngân**. Nhập tên đăng nhập có chữ, mật khẩu, chọn đúng một bệnh viện và bật **Đang hoạt động**. Nút **Sửa / phân công / kích hoạt** cho phép đổi bệnh viện, mật khẩu hoặc vô hiệu hóa. Dùng `User` hiện tại; không tự cấp quyền cho nhân viên cũ.
- Thu ngân đăng nhập tại **`/login/`**, được chuyển đến **`/thanh-toan/`**. Chỉ thấy hóa đơn thuộc bệnh viện mình, tìm theo mã hóa đơn/tên bệnh nhân và lọc trạng thái. Backend kiểm tra cả URL chi tiết và mọi POST.
- Mở hóa đơn → kiểm tra dịch vụ, tổng trước BHYT, hỗ trợ mô phỏng và số thực trả → nhận **đủ toàn bộ tiền mặt** → đánh dấu ô xác nhận → **Xác nhận đã nhận tiền**. Không nhập/sửa tiền, không thu từng phần. Nếu phiên bản hóa đơn thay đổi từ lúc mở trang, phải tải lại để kiểm tra tiền trước khi xác nhận.
- Chọn **Thanh toán VNPAY** khi bệnh nhân có mặt tại quầy. Bệnh nhân thao tác trên cổng Sandbox; thu ngân không nhập hộ thẻ/OTP. Chỉ IPN hợp lệ chốt VNPAY. Bấm **Tải lại trạng thái** tại chi tiết hoặc **Tìm / làm mới trạng thái** tại danh sách; không xác nhận tiền mặt cho VNPAY.
- Admin theo dõi toàn hệ thống ở **`/quan-tri/thu-ngan/hoa-don/`** (chỉ xem). Quyền thu tại quầy đã cấp trước đây được giữ; staff chưa có `core.thu_vnpay` không tự được phép thu.

## Model, migration và chống thu trùng

- **`ThuNgan`**: OneToOne với `User`, một FK bệnh viện bắt buộc. Tài khoản tạo tại đây không có quyền staff/superuser/bác sĩ. Form tài khoản chung không sửa vai trò thu ngân; form bác sĩ không cho liên kết tài khoản thu ngân.
- **`GiaoDichThanhToan`**: phương thức `VNPAY` / `CASH`; thêm bệnh viện ghi nhận. Tiền mặt lưu số tiền, nhân viên, bệnh viện, thời điểm, tham chiếu, hóa đơn và `LogHeThong`. Các trường phản hồi VNPAY để trống với tiền mặt; thời hạn không áp dụng.
- **Migration `0024_thu_ngan_tien_mat`** giữ giá trị hóa đơn/chi tiết/giao dịch VNPAY cũ; trường bệnh viện mới trên giao dịch cũ là NULL, không suy diễn/backfill. Không tính lại tiền, đổi trạng thái hoặc tạo thu ngân tự động.
- `transaction.atomic` và khóa lịch khám/hóa đơn/phân công; IPN dùng chung khóa hóa đơn. Ràng buộc database **một giao dịch thành công mỗi hóa đơn** áp dụng cả hai phương thức. Lưu giao dịch, trạng thái và audit cùng transaction.
- Chặn thu tiếp khi đã trả, pending/chưa rõ kết quả hoặc cần đối soát. Hết hạn theo đồng hồ local, đóng tab và ReturnURL không giải phóng khóa. Chỉ phản hồi VNPAY đã xác minh thất bại/hủy/hết hạn mới cho thử lại hoặc thu tiền mặt.
- IPN lặp không tạo thanh toán thứ hai; IPN mâu thuẫn đến muộn giữ giao dịch đã chốt, đánh dấu cần đối soát. IPN/Return không nhận mã giao dịch tiền mặt làm VNPAY. Return cũ sau khi đã thu tiền không báo hóa đơn chưa thanh toán.
- Hóa đơn 0đ do BHYT mô phỏng tự hoàn tất, không tạo giao dịch thu. **1–4.999đ thu đúng bằng tiền mặt**, VNPAY vẫn chặn dưới 5.000đ; không làm tròn.
- Hóa đơn đã thanh toán tiếp tục khóa chỉnh sửa theo TASK 04–05. Không lưu mật khẩu trong audit.

## File chính

- `core/models.py`, `core/migrations/0024_thu_ngan_tien_mat.py`.
- `core/payments/access.py`, `cashiers.py`, `services.py`, `views.py`.
- `core/billing/services.py`, `core/forms.py`, `core/views.py`, `core/urls.py`.
- `core/templates/core/payments/{list,detail,return,cashiers,cashier_form}.html`, `core/templates/core/custom_admin_dashboard.html`.
- `core/test_cashiers.py`, `README.md`, tài liệu này.

## Máy nhận project

Trong **Terminal PowerShell của VS Code**, mở thư mục project và nạp môi trường Python/GDAL theo [hướng dẫn máy mới](CHAY_VNPAY_MAY_MOI.md), sau đó:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Tiền mặt không cần VNPAY/ngrok. Với VNPAY, giữ `.env` hợp lệ và cửa sổ ngrok chạy. URL không đổi: `/thanh-toan/vnpay/ipn/` và `/thanh-toan/vnpay/return/`.

## Kiểm thử và giới hạn

Đã chạy trên PostgreSQL/PostGIS kiểm thử riêng, không reset database nghiệp vụ:

- Hồi quy TASK 01–06: **237/237 đạt**, gồm 198 bài cũ + 39 bài TASK 06 lúc chạy.
- Sau khi bổ sung tình huống ReturnURL cũ sau thanh toán tiền mặt: chạy lại `core.test_cashiers core.test_vnpay`, **78/78 đạt** (40 bài thu ngân/tiền mặt + 38 bài VNPAY).
- Tổng **238 bài khác nhau đạt, 0 thất bại còn lại**. Không cộng hai lượt thành 315 bài khác nhau. Có kiểm thử đồng thời bằng các kết nối PostgreSQL riêng cho cash/cash, cash/VNPAY, cash/IPN và cash/điều chỉnh dịch vụ.
- `manage.py check`: không có lỗi; `makemigrations --check --dry-run`: không phát sinh migration thiếu.
- Đã áp dụng migration `0024` lên database được cấu hình. So sánh số dòng và hash toàn bộ cột cũ của **11 bảng nghiệp vụ/tài khoản** trước/sau: không đổi; không tạo tài khoản thu ngân hay giao dịch tiền mặt trong dữ liệu thật.
- Render trực tiếp qua Django RequestFactory trả HTTP 200 cho dashboard Admin, danh sách/tạo thu ngân, giám sát hóa đơn và danh sách/chi tiết thanh toán hiện có. Đây không phải kiểm thử trình duyệt toàn hệ thống.

Lệnh hồi quy (chỉ chạy sau khi cấu hình database kiểm thử riêng):

```powershell
.\.venv\Scripts\python.exe manage.py test core.test_cashiers core.test_insurance core.test_billing core.test_exam_services core.test_service_catalog core.test_vnpay core.tests.DoctorFlowTests core.tests.AdminDashboardTests core.tests.AppointmentBookingTests core.tests.CoordinateConstraintTests --noinput
```

Phạm vi bệnh viện sử dụng quan hệ lịch khám → bác sĩ → bệnh viện sẵn có; giao dịch mới lưu bệnh viện tại thời điểm thu. Chưa có quy trình chuyển viện/chuyển lịch sử hóa đơn độc lập. VNPAY vẫn Sandbox, BHYT vẫn mô phỏng; tiền mặt được ghi nhận theo xác nhận của nhân viên. Đối soát mâu thuẫn cần kiểm tra với VNPAY, không có nút mở khóa tùy tiện. Không có PDF, email, hoàn tiền, thu từng phần hay VNPAY từ xa. Chưa nghiệm thu toàn bộ bằng trình duyệt và chưa thực hiện giao dịch ngân hàng thật.
