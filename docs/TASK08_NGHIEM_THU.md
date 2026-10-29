# TASK 08 — Báo cáo nghiệm thu ngày 29/10/2026

## Kết quả kiểm chứng

| Hạng mục | Kết quả |
| --- | --- |
| Suite ban đầu | 284 bài: 282 đạt, 2 lỗi đăng ký/quên mật khẩu |
| Suite sau sửa ở phiên trước | 302/302 đạt |
| Suite chạy lại trong phiên tiếp tục | **302/302 đạt, 0 lỗi, 0 bỏ qua**, 137,367 giây; exit code 0 |
| Edge tại `.tools/qa_browser.py` | 1/1 kịch bản đạt, 15,113 giây; không có lỗi JavaScript trong các trang đã kiểm tra |
| Django system check | Không có vấn đề |
| `makemigrations --check --dry-run` | Không có thay đổi model |
| `migrate --check` | Không có migration chờ; migration mới nhất vẫn là 0025 |
| `pip check` | Không có dependency bị lỗi |
| Bảo toàn dữ liệu/cấu hình | Đối chiếu trước–sau: các bảng `core_*`, `auth_user`, `django_migrations`, `.env` và source ký VNPAY/tính hóa đơn/BHYT không đổi |

Test sử dụng PostgreSQL/PostGIS riêng tại `127.0.0.1:55439`; browser dùng database thử riêng. Không xóa/bỏ qua test cũ, không reset database nghiệp vụ. Log lần chạy tiếp tục: `.vnpay-test/task08-resume-tests.log`, `.vnpay-test/task08-resume-browser.log`.

## Lỗi TASK08 đã sửa và giữ nguyên khi tiếp tục

| Nguyên nhân | Cách sửa và bằng chứng |
| --- | --- |
| Đăng ký chặn username SĐT cũ, nhưng lại ghi username chữ/số vào trường SĐT | Hỗ trợ tài khoản SĐT cũ, thêm ô SĐT riêng, kiểm tra email/hồ sơ; lưu User và bệnh nhân trong transaction. Test đăng ký, dữ liệu sai và rollback đạt. |
| Đăng nhập từ chối username chỉ gồm số | Bỏ điều kiện chặn; kiểm thử đăng nhập/đăng xuất tài khoản cũ đạt. |
| Tìm hồ sơ bằng username = SĐT thay vì quan hệ User | Ưu tiên `BenhNhan.user`; fallback chỉ dành cho hồ sơ cũ chưa liên kết. Test tài khoản chữ/số, hồ sơ cũ và chống đọc nhầm hồ sơ đạt. |
| Quên mật khẩu dùng biến chưa gán, đặt sai thụt lề | Đổi mật khẩu dựa trên session đã xác minh OTP, kiểm tra hạn/độ mạnh; xóa session sau thành công. Dùng OTP bảo mật, hủy flow sau 5 mã sai. Test reset/hết hạn/mật khẩu yếu đạt. |
| Form đổi mật khẩu ở profile nhưng xử lý trong register | Chuyển xử lý về profile, kiểm tra mật khẩu hiện tại và giữ session; test POST vô danh/đổi mật khẩu đạt. |
| Cập nhật hồ sơ đổi username trước khi kiểm tra BHYT, thiếu transaction/validation | Giữ tên đăng nhập khi đổi SĐT; kiểm tra và lưu hồ sơ/BHYT nguyên khối, từ chối thẻ thuộc hồ sơ khác. Test rollback/truy cập chéo đạt. |

Source/test đã lưu trước phiên tiếp tục: `core/views.py`, `core/templates/core/register.html`, `core/test_auth_regressions.py` (16 bài), `core/test_workflow.py` (2 bài). Không sửa lại các file này trong phiên tiếp tục.

## Phần còn dở đã hoàn thiện

- Tạo `.tools/qa_browser.py` từ script đã kiểm chứng; giữ nguyên `.vnpay-test/task08_browser.py`. Mặc định SMTP giả lập; chỉ gửi Mailtrap Testing khi chủ động thêm `--mailtrap`.
- Bổ sung Playwright 1.64.1 vào `requirements-dev.txt`, đúng phiên bản đã dùng trên máy; không nâng cấp framework.
- Sửa đúng ghi chú username đã lỗi thời trong README và thêm liên kết báo cáo này; giữ nội dung khác.
- Tạo `docs/TASK08_NGHIEM_THU.md`. Không thay đổi `.env`, cấu hình VNPAY/ngrok/Mailtrap hay migration.

Git sạch ở đầu phiên tiếp tục, HEAD `3911970`. Các thay đổi mới của phiên tiếp tục chỉ gồm bốn file tài liệu/công cụ kể trên; không tự commit/push.

## Phạm vi đã kiểm thử

- Suite bao phủ xác thực, chức năng gốc, danh mục dịch vụ, snapshot giá/phiên bản hóa đơn, BHYT mô phỏng/0đ, tiền mặt 1–4.999đ, phân quyền và chéo bệnh viện, thao tác đồng thời/chống thu trùng, migration, VNPAY callback, PDF và email sau commit/lỗi/thử lại.
- Hai bài HTTP đi xuyên suốt đăng ký → đặt lịch → khám/dịch vụ → hóa đơn/BHYT → tiền mặt hoặc VNPAY giả lập → PDF/email; xác minh không thu trước khám, điều chỉnh trước thu và khóa sau thanh toán.
- Edge thao tác form có CSRF: đăng ký/đăng nhập, bản đồ Leaflet/marker, hồ sơ/BHYT, chọn bệnh viện/khoa/bác sĩ/ngày/giờ, khám, tìm dịch vụ và đổi số lượng bằng JavaScript, thu tiền mặt, xem/tải PDF/trang in, lịch sử bệnh nhân và các trang quản trị.
- PDF đã xem trực quan ở phiên trước: A4, tiếng Việt có dấu, logo cũ, tổng 200.000đ − BHYT mô phỏng 160.000đ = thực trả 40.000đ. Chưa thử máy in vật lý.

## Tích hợp ngoài và phần cần nghiệm thu

**VNPAY:** chưa có giao dịch Sandbox mới qua ngrok. Kiểm tra lại trong phiên tiếp tục: ba biến `VNPAY_TMN_CODE`, `VNPAY_HASH_SECRET`, `VNPAY_PUBLIC_BASE_URL` chưa có giá trị trong tiến trình QA; cổng local 8000 và 4040 chưa mở. Không sửa cấu hình. Callback ký bằng khóa thử trong suite không phải giao dịch qua cổng Sandbox thật.

**Mailtrap:** ở phiên trước, SMTP Testing đã chấp nhận **một thư thử có PDF**, trạng thái `sent`, số lần thử 1; metadata lưu tại `.vnpay-test/task08-mailtrap-result.json`. Phiên tiếp tục dùng SMTP giả lập, không gửi thêm thư. Chưa có quyền xem Testing Inbox nên chưa xác nhận thư hiển thị trong Inbox.

Nghiệm thu thủ công:

1. Trong **PowerShell của VS Code**, tại thư mục project, kích hoạt `.venv`, nạp cấu hình đang dùng theo [hướng dẫn local](CHAY_VNPAY_MAY_MOI.md), chạy `python manage.py vnpay_config`, rồi `python manage.py runserver 127.0.0.1:8000`. Giữ cửa sổ này chạy.
2. Mở **PowerShell thứ hai trên cùng máy**, chạy `ngrok http http://127.0.0.1:8000`. Cập nhật biến `VNPAY_PUBLIC_BASE_URL` bằng HTTPS ngrok hiện tại trong cửa sổ Django rồi khởi động lại Django. Tại VNPAY SIT Testing, IPN là `<HTTPS-ngrok>/thanh-toan/vnpay/ipn/`; Return là `<HTTPS-ngrok>/thanh-toan/vnpay/return/`. IPN trống trả JSON `97` chỉ chứng minh endpoint thông, chưa phải thanh toán thành công.
3. Dùng hồ sơ thử: đặt lịch → khám/dịch vụ → hoàn tất → thu ngân đúng bệnh viện → một hóa đơn VNPAY Sandbox. Xác minh số tiền thực trả, hóa đơn `paid` sau IPN hợp lệ, tải lại không tạo giao dịch/email thứ hai.
4. Mở **Mailtrap Email Testing Inbox**, tìm “Xác nhận thanh toán dịch vụ khám chữa bệnh”; kiểm tra người nhận thử, một PDF đính kèm, tiếng Việt, số tiền và trạng thái. Mở PDF, thử in A4. Chỉ đánh dấu nghiệm thu tích hợp hoàn tất khi cả Sandbox và Inbox đều đúng.

## Chạy lại QA khi cần

Sau khi cài `requirements-dev.txt`, nạp GDAL/GEOS và trỏ các biến DB tới **PostgreSQL QA riêng**:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --noinput
```

Kịch bản browser cần Microsoft Edge và PostgreSQL QA đã chuẩn bị trên cổng **55439**; script tự tạo/hủy database `test_task08_browser`:

```powershell
python .tools/qa_browser.py
```

Chỉ khi muốn gửi **thêm một thư thử** với cấu hình Mailtrap Testing hợp lệ, thêm `--mailtrap`. Không coi `failed`/`unknown` là thành công; trường hợp chưa rõ kết quả phải kiểm tra Inbox trước khi thử lại.

**Kết luận:** phần sửa lỗi và kiểm thử tự động TASK08 đã hoàn tất, đủ điều kiện chuyển sang nghiệm thu thực tế. Chưa xác nhận hoàn tất 100% vì còn VNPAY Sandbox và Mailtrap Inbox.
