# TASK 07 — Phiếu thanh toán PDF và Mailtrap Testing

## Sử dụng

Trang hóa đơn `/thanh-toan/lich-kham/<lich_id>/` có **Xem phiếu PDF**, **Tải PDF**, **In phiếu** và trạng thái email. Admin cũng xem được tại màn hình giám sát `/quan-tri/thu-ngan/hoa-don/<lich_id>/`.

| Chức năng | URL (ID hóa đơn, không phải ID lịch khám) |
|---|---|
| Xem PDF | `/thanh-toan/hoa-don/<invoice_id>/phieu/xem/` |
| Tải PDF | `/thanh-toan/hoa-don/<invoice_id>/phieu/tai/` |
| In | `/thanh-toan/hoa-don/<invoice_id>/phieu/in/` |
| Thử gửi lại (POST + CSRF) | `/thanh-toan/hoa-don/<invoice_id>/email/thu-lai/` |

PDF A4 nhúng font Noto Sans có dấu, dùng logo/tên MedCare GIS hiện có. Nội dung lấy từ hóa đơn và chi tiết giá đã chốt; không đưa chẩn đoán, bệnh án, số thẻ BHYT hoặc dữ liệu liên hệ vào PDF. Thu ngân chỉ truy cập trong bệnh viện được phân công; Admin xem toàn hệ thống. PDF sinh trong bộ nhớ, không lưu ở `media/`, không có link công khai, không cho cache. Xem/in/tải không gửi email.

Phiếu ghi rõ **PHIẾU THANH TOÁN NỘI BỘ — không phải hóa đơn điện tử có giá trị pháp lý**. Chưa trả/đang chờ IPN/cần đối soát được thể hiện rõ. Hóa đơn cũ thiếu chi tiết không tự tạo dòng dịch vụ giả. Hóa đơn 0đ vẫn xem/in/tải được, không tạo giao dịch và không gửi email xác nhận thu tiền.

Để in, mở trang In phiếu → **Mở PDF để in** → biểu tượng máy in hoặc **Ctrl+P** trong trình xem PDF → chọn A4. Không ép trình duyệt tự bật hộp thoại in.

## Email và cấu hình

Sau khi tiền mặt hoặc IPN VNPAY ghi nhận thanh toán thành công và transaction ngoài cùng đã commit, hệ thống gửi email tiếng Việt kèm PDF. ReturnURL không gửi. Địa chỉ nhận lấy từ `BenhNhan.user.email` đang đăng ký; thiếu/không hợp lệ thì ghi trạng thái bỏ qua, không tạo địa chỉ giả. Cập nhật email hợp lệ rồi dùng nút thử gửi lại.

Tái sử dụng SMTP trong `hospital/settings.py`, cấu hình bằng `.env`:

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=sandbox.smtp.mailtrap.io
EMAIL_PORT=2525
EMAIL_HOST_USER=DIEN_USERNAME_CUA_TESTING_INBOX
EMAIL_HOST_PASSWORD=DIEN_PASSWORD_CUA_TESTING_INBOX
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_TIMEOUT=20
DEFAULT_FROM_EMAIL=no-reply@qlbenhvien.local
```

Lấy thông số ở **Mailtrap Email Testing/Sandbox → Inbox → Integration → SMTP**, theo [hướng dẫn chính thức](https://docs.mailtrap.io/email-sandbox/setup/sandbox-smtp-integration). Không dùng Email Sending. Module chứng từ kiểm tra chính xác host Testing và bắt buộc backend SMTP; host khác/backend console hoặc thiếu thông số sẽ ghi lỗi cấu hình, không gửi. Email chỉ vào Inbox kiểm thử, không tới hộp thư bệnh nhân thật. Không cần gửi credentials trong chat.

Cấu hình trên máy hiện tại đã có host Testing, username/password và TLS/SSL hợp lệ về mặt cấu hình. Chưa kết nối SMTP thật hoặc xác nhận thư đã vào Inbox; test tự động đều dùng backend giả.

## Chống gửi trùng và xử lý lỗi

- Model **`EmailPhieuThanhToan`**, migration **`0025_email_phieu_thanh_toan`**, OneToOne hóa đơn. Lưu trạng thái, số lần xử lý, Message-ID ổn định, email nhận, thời gian và mã lỗi an toàn. Audit không ghi SMTP password hoặc nội dung exception.
- Chỉ một tiến trình được nhận quyền gửi bằng khóa bản ghi và kiểm tra số lần gửi. Trạng thái `sending` được commit trước SMTP; không giữ transaction database khi chờ SMTP. IPN/tiền mặt lặp không tạo bản ghi thứ hai.
- SMTP chạy bằng `transaction.on_commit`; rollback thanh toán không gửi. Gửi lỗi hoặc lỗi tạo bản ghi email không đảo trạng thái đã thanh toán. Nếu chưa tạo được bản ghi, nhân viên có thể gửi thủ công từ trang hóa đơn đã có giao dịch thành công.
- `failed`, `skipped`, `pending`: nút gửi lại có POST/CSRF, kiểm tra quyền và số lần xử lý. Hai request đồng thời hoặc một form cũ không được gửi lặp.
- `sent`: không gửi lại. `unknown` hoặc `sending` kéo dài: **không tự thử lại**, vì SMTP có thể đã nhận thư trước khi mất kết nối hoặc trước khi lưu kết quả vào DB. Message-ID chỉ hỗ trợ tra cứu, không được xem là bảo đảm server tự loại trùng.
- Người vận hành phải kiểm tra Testing Inbox theo mã hóa đơn/Message-ID. Sau ít nhất 2 phút từ lần gửi, dùng lệnh kiểm soát dưới đây; lệnh chỉ đối soát trạng thái, không gửi email:

```powershell
# Thay 123 và 1 bằng ID hóa đơn và số lần xử lý trên màn hình.
.\.venv\Scripts\python.exe manage.py reconcile_receipt_email 123 --attempt 1 --result received --note "Da kiem tra Inbox va thay thu"
# Chỉ khi chắc chắn Inbox chưa nhận: dùng --result not-received, sau đó bấm thử gửi lại trên web.
```

SMTP không cung cấp transaction chung với database; không thể bảo đảm gửi đúng một lần qua mọi tình huống mất kết nối bằng cách tự gửi lại. Module ưu tiên không gửi trùng, yêu cầu đối soát khi kết quả chưa rõ. Nếu process dừng trước callback, bản ghi `pending` có thể được gửi lại bằng nút trên web. Không thêm Celery/Redis.

## Cài đặt, file và kiểm thử

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check
# Khi chạy test PDF, cài thêm công cụ kiểm thử:
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

File chính: `core/receipts/{pdf,delivery,views}.py`, font và giấy phép trong `core/receipts/fonts/`; model/migration `0025`; hook trong `core/payments/services.py`; context email trong `core/payments/views.py`; `core/urls.py`; template chi tiết và `core/templates/core/receipts/print.html`; command `reconcile_receipt_email`; `core/test_receipts.py`; requirements và `.env.example`. HMAC/URL VNPAY, luồng tiền mặt, phép tính BHYT và `.env` thật giữ nguyên.

Đã chạy **279/279 bài đạt, 0 thất bại**: 238 bài hồi quy TASK 01–06 và 41 bài TASK 07. Test mới kiểm tra nội dung/font nhúng tiếng Việt/A4/nhiều trang, số tiền BHYT, phân quyền, commit/rollback, email đính kèm, lỗi SMTP, IPN lặp, gửi lại đồng thời và bảo toàn migration. SMTP trong test được thay bằng Django locmem/mock, không gửi mạng thật.

`manage.py check` đạt; `makemigrations --check --dry-run` không còn thay đổi thiếu. Migration `0025` đã áp dụng lên database local. So sánh toàn bộ cột cũ của **13 bảng nghiệp vụ/tài khoản/audit** trước/sau không đổi; không tạo hàng loạt bản ghi email cho hóa đơn cũ. Các trang PDF/xem/tải/in và chi tiết đã render HTTP 200. Đã xem trực quan PDF từ hóa đơn hiện có: tiếng Việt, logo và bố cục A4 hiển thị đúng. Không có email được gửi trong bước kiểm tra dữ liệu/render này.

Cần nghiệm thu thủ công tiếp: mở/in PDF trên trình duyệt sử dụng thực tế, một thanh toán tiền mặt và một VNPAY Sandbox để kiểm tra thư/PDF trong Mailtrap Inbox. Chưa xác minh kết nối SMTP thật, thao tác máy in hoặc toàn bộ giao diện bằng trình duyệt.
