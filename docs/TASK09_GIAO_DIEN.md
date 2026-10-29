# TASK09 — Đồng bộ giao diện với website gốc

Đã hoàn tất rà soát ngày 29/10/2026. Chỉ sửa template/CSS của các trang mới; giữ tên, logo, thương hiệu và giao diện gốc. Không thay đổi backend, database, migration, phân quyền, JavaScript nghiệp vụ, VNPAY, PDF hay Mailtrap.

## Chuẩn đối chiếu

- Quản trị gốc: `custom_admin_dashboard.html`, `custom_admin_model_list.html`, `custom_admin_form.html`; Segoe UI, nền xanh nhạt, màu chính `#0f7d69`, card trắng, nút bo tròn.
- Bác sĩ: `doctor_home.html`, `doctor_exam.html`, `doctor_exam_edit.html`; Work Sans/Playfair, nền và card hiện có. Không ép các trang này sang kiểu quản trị.
- Các form dịch vụ/BHYT đã tái sử dụng form quản trị gốc và đang hiển thị tốt: giữ nguyên.

## Trang và trạng thái đã kiểm tra

- Danh mục dịch vụ: danh sách, tìm/lọc, thêm, sửa, chi tiết/lịch sử.
- BHYT mô phỏng: danh sách, thêm, sửa, chi tiết/lịch sử.
- Thu ngân: danh sách, thêm, sửa, form báo lỗi, giám sát hóa đơn và chi tiết.
- Bác sĩ: trang chính, khám mới, sửa phiếu, dịch vụ đang sửa/đã khóa, chi tiết phiếu.
- Thanh toán: danh sách, bộ lọc, chi tiết chưa thanh toán/đang chờ/đã thanh toán, lần giao dịch thất bại, nút PDF, trang in, thông tin email.
- ReturnURL công khai: chữ ký sai, callback giả lập hợp lệ, người thu tiền đăng nhập.
- Trang gốc đối chứng: dashboard, danh sách bệnh viện, form thêm bệnh viện và các form/bảng của bác sĩ.

## Lỗi hiển thị và thay đổi

| Bằng chứng trước sửa | Thay đổi |
| --- | --- |
| Ô chọn thanh toán/thu ngân dùng giao diện mặc định, nút thanh toán bo góc khác mẫu gốc | Dùng chung CSS lấy màu/font/kiểu nút, viền, focus và thông báo từ giao diện quản trị gốc. |
| Form thu ngân có label/input đặt cùng dòng, chiều rộng không đều; hai hướng dẫn mặc định bằng tiếng Anh | Căn mỗi trường thành một khối, checkbox riêng, help text rõ ràng bằng tiếng Việt; giữ nguyên widget, trường, lỗi, CSRF và POST. |
| Bảng hóa đơn mobile ép cột khiến số tiền xuống dòng, hàng cao và khó đọc | Giữ chiều rộng tối thiểu của bảng trong vùng cuộn ngang; số tiền không xuống dòng, có hướng dẫn cuộn trên mobile. |
| Liên kết PDF nhỏ, khó phân biệt các thao tác và trạng thái thanh toán | Hiển thị nút PDF theo kiểu nút gốc; trạng thái vẫn giữ nguyên chữ và được bổ sung màu tương ứng. |

Baseline không có tràn toàn trang hoặc lỗi JavaScript; không báo cáo đây là lỗi đã sửa. Form dịch vụ bác sĩ đã responsive tốt nên không chỉnh sửa.

## File thay đổi trong TASK09

1. `core/static/core/css/operations.css` — CSS dùng chung, giới hạn trong `.operations-page`.
2. `core/templates/core/payments/base.html` — nạp CSS cho layout thanh toán.
3. `core/templates/core/service_catalog/base.html` — nạp cùng CSS cho danh mục/BHYT.
4. `core/templates/core/payments/cashier_form.html` — căn trường và help text.
5. `core/templates/core/payments/list.html` — áp dụng bố cục bộ lọc.
6. `core/templates/core/payments/detail.html` — nút PDF, cách hiển thị tiền và trạng thái.
7. `docs/TASK09_GIAO_DIEN.md` — báo cáo này.

README, requirements-dev và `.tools/qa_browser.py` đã thay đổi từ TASK08; TASK09 không sửa thêm các file đó và không bổ sung thư viện frontend.

## Kết quả kiểm thử

- **Edge 1440 / 768 / 390px:** 83 lượt kiểm tra trang/trạng thái trước và sau sửa; thêm 9 lượt ReturnURL. Sau sửa, **92 lượt đạt**, không tràn toàn trang và không có lỗi JavaScript.
- Đối chiếu **83/83 cấu trúc form**: method, action, tên/type trường, required/disabled và sự hiện diện CSRF không đổi. Không thay đổi các điều kiện phân quyền.
- Trang gốc và form dùng lại giữ nguyên font/màu/kiểu control theo đo đạc trước–sau. Ảnh mobile/desktop của form thu ngân, bảng dịch vụ và hóa đơn đã xem trực quan.
- Tìm kiếm dịch vụ và form thu ngân báo lỗi đã thao tác bằng browser. Luồng Edge đầy đủ đăng ký → đặt lịch → khám/dịch vụ → BHYT → thu tiền mặt → PDF đạt **1/1**, SMTP giả lập.
- Django system check: **không có vấn đề**.
- Toàn bộ suite: **302/302 đạt, 0 lỗi, 0 bỏ qua**, khoảng 138 giây.
- Đối chiếu trước–sau xác nhận các bảng nghiệp vụ/tài khoản/migration, `.env` và source tính hóa đơn/BHYT/ký VNPAY không đổi. Git không có thay đổi Python/backend, migration hay JavaScript nghiệp vụ.

Tất cả dùng tài khoản/dữ liệu giả và PostgreSQL QA riêng; không gửi thêm email thật hoặc tạo giao dịch qua cổng thanh toán. Ảnh và số đo lưu ở `.vnpay-test/task09/before/`, `.vnpay-test/task09/after/`; log hồi quy ở `.vnpay-test/task09-regression.log`. Đây là artifact local, không đưa dữ liệu QA vào Git.

## Người dùng kiểm tra thêm

Mở các trang mới, nhấn Ctrl+F5 để tải CSS mới. Trên điện thoại thật, thử cuộn bảng sang phải, chọn bộ lọc, nhập form và mở PDF; kiểm tra mức zoom/font theo thiết bị thường dùng. Kiểm thử trên Edge bằng viewport chưa thay thế thao tác cảm ứng hoặc in bằng máy in vật lý. Giao dịch VNPAY Sandbox thực tế và kiểm tra Mailtrap Inbox vẫn theo phần nghiệm thu còn lại của [TASK08](TASK08_NGHIEM_THU.md).
