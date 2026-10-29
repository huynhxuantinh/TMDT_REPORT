# Rà soát P1 — thanh toán, bảo mật và bệnh án

Ngày kiểm tra: 29–30/10/2026. Cả 7 vấn đề đều còn tồn tại trong code trước lần sửa này.

| Mục | Xử lý |
| --- | --- |
| 01. Timeout có thể thu lại | `pending` và `timeout` luôn khóa VNPAY, tiền mặt và sửa dịch vụ. Hết thời gian chờ chỉ đánh dấu cần đối soát. Admin phải gọi QueryDR; kết quả chưa rõ giữ khóa. IPN thành công muộn vẫn tất toán; dữ liệu cũ thu trùng vẫn được phát hiện để hoàn tiền. |
| 02. CRUD nhật ký | Chặn GET/POST tạo, sửa, xóa `LogHeThong`, `LogLichKham`, kể cả superuser. Giữ danh sách để xem. Django ModelAdmin cũng cấm thêm/sửa/xóa và bulk delete. |
| 03. Ảnh bệnh án công khai | `PrivateExamStorage` lưu ngoài `MEDIA_ROOT`, sinh URL `/benh-an/anh/<đường-dẫn-ảnh>`. Mỗi request kiểm tra tài khoản hoạt động và quyền đối với phiếu; dùng `no-store`, `nosniff`. Chặn URL `/media/phieu_kham/...` cũ. |
| 04. XSS bản đồ | Hai popup trong `about.html`, `hospital_detail.html` dùng DOM và `textContent`/`createTextNode`; tên bệnh viện/phường không còn được ghép thành HTML. |
| 05. Lịch sử không đồng bộ | Mỗi `PhieuKham.save()` đồng bộ lịch sử từ dữ liệu đã lưu trong cùng transaction. Audit giữ nội dung trước/sau và người sửa từ giao diện bác sĩ/admin. Lỗi ghi audit làm rollback cả phiếu và lịch sử. `update_fields` không làm lọt giá trị chưa lưu vào lịch sử. |
| 06. Bác sĩ xem ngoài phạm vi | Lịch sử chỉ chứa phiếu do chính bác sĩ phụ trách, đúng bệnh nhân và bệnh viện. Lịch hẹn đã hủy không đủ để mở hồ sơ. Không tự cho phép chia sẻ bệnh án giữa bác sĩ/cơ sở. |
| 07. Thiếu dịch vụ trên phiếu | Nhánh bác sĩ nhận cùng `exam_billing_context` với bệnh nhân, gồm dịch vụ, số lượng, giá đã chốt và chi phí hóa đơn. |

## Quy tắc đối soát

Theo [tài liệu QueryDR chính thức của VNPAY](https://sandbox.vnpayment.vn/apis/docs/truy-van-hoan-tien/querydr%26refund.html), `vnp_TransactionStatus=01` là giao dịch **chưa hoàn tất**. Không dùng kết quả này để cho thu lại.

- Kiểm tra chữ ký, terminal, mã tham chiếu, loại giao dịch và số tiền.
- `vnp_ResponseCode=00`, trạng thái `00`: đã thu tiền, ghi nhận tất toán.
- `00/02`: xác nhận thất bại; chỉ cho thu lại nếu mọi giao dịch đã được giải quyết và hóa đơn chưa trả.
- Mã 91, `01`, kết quả không rõ, sai chữ ký hoặc lỗi mạng: giữ khóa.
- Kiểm tra lại trạng thái và danh sách giao dịch dưới khóa DB sau khi gọi mạng, tránh dùng kết quả đối soát đã lỗi thời.
- Nút **Đối soát kết quả với VNPAY** ở trang giám sát hóa đơn áp dụng cả `timeout` cũ chưa có cờ đối soát và `pending` đã quá hạn chờ.

## Chính sách ảnh và bệnh án

Bệnh nhân xem phiếu/ảnh của mình. Bác sĩ xem phiếu/ảnh mình phụ trách. Quyền admin vận hành không tự cấp quyền mở ảnh riêng tư. Bệnh nhân vẫn xem được toàn bộ lịch sử của bản thân; bác sĩ không xem các lần khám của bác sĩ khác chỉ vì từng có một lịch hẹn với bệnh nhân.

Ảnh bệnh viện công khai vẫn dùng media hiện có. **Không cấu hình web server/CDN phục vụ `private_media/`**. Với production đang phục vụ media trực tiếp, chặn `/media/phieu_kham/` tại web server trước khi chuyển ảnh cũ. Endpoint Django không thể kiểm soát file đã được Nginx/IIS/CDN phục vụ trực tiếp; cần xóa cache công khai cũ nếu từng có.

## Cập nhật máy khác

Sau khi nhận code, chạy trong terminal đã kích hoạt venv và nạp GDAL/GEOS như hướng dẫn hiện tại:

```powershell
python manage.py migrate
python manage.py privatize_exam_images
python manage.py privatize_exam_images --apply
python manage.py check
```

Lệnh chuyển ảnh mặc định chỉ kiểm tra. `--apply` giữ nguyên tên file trong DB, kiểm tra SHA-256 trước khi bỏ bản công khai, không ghi đè file đích khác nội dung và có thể chạy lại. Sao lưu và cấp quyền đọc/ghi `private_media/` cho tiến trình Django. Khởi động lại Django sau cập nhật.

Trên máy local hiện tại đã sao lưu DB vào `.vnpay-test/p1-local-before.dump`, áp dụng các migration còn thiếu `0026–0030`, so sánh toàn bộ cột dữ liệu nghiệp vụ/tài khoản cũ trước/sau và xác nhận `.env` không đổi. Không có ảnh bệnh án cũ trong media công khai để chuyển. Migration `0030` chỉ thay cấu hình storage, SQL là no-op.

## Kiểm thử và giới hạn

- Nhóm P1, VNPAY, phân quyền quản lý chi nhánh, đăng nhập và tiền mặt sau timeout: **95/95 đạt**. Có test IPN muộn, bấm lại nhiều lần, hai lần tạo giao dịch đồng thời, IPN muộn chạy đồng thời với thu tiền mặt; test admin/nhân viên gọi trực tiếp CRUD log; ảnh của người lạ; đồng bộ lịch sử; giá đã chốt trên phiếu. Có thêm test giữ tranh chấp của giao dịch khác khi IPN thất bại đến muộn.
- Edge: **6/6** lượt kiểm tra hai đoạn dựng popup thật ở 1440/768/390px. Chuỗi chứa `img onerror`, `svg onload`, dấu nháy và tiếng Việt được hiển thị như văn bản, không tạo phần tử độc hại, không có lỗi JavaScript.
- `manage.py check`, `makemigrations --check --dry-run`, `git diff --check`: đạt.
- Đối chứng nguyên bản HEAD: **335 test, 29 failure + 184 error**. Phần lớn test còn giả định bác sĩ được chỉnh dịch vụ/lập hóa đơn hoặc chưa thích nghi với quan hệ `PROTECT` và phân quyền hiện hành. Không nới quyền backend để làm những test cũ này đạt.
- Toàn bộ suite: **351 test, 141 đạt, 28 failure + 182 error**. Cả 210 test lỗi đều có trong tập lỗi của nguyên bản HEAD; không có test đang đạt trước đó bị lỗi sau sửa. Một test bổ sung về tranh chấp nhiều giao dịch được chạy trong nhóm trọng tâm sau lượt toàn bộ này. Log đối chứng và so sánh nằm trong `.vnpay-test/p1-*.log`, `.vnpay-test/p1-regression-comparison.json` (Git bỏ qua). Không khẳng định full suite đạt.
- Không thực hiện giao dịch VNPAY thật hoặc gửi email thật. Phản hồi provider được mô phỏng và ký bằng khóa test. Cần kiểm tra thanh toán Sandbox trực tiếp với IPN/ngrok của môi trường triển khai.
- Đồng bộ lịch sử áp dụng cho đường `save()` của model và các form hiện có. Không dùng SQL trực tiếp, `QuerySet.update()` hoặc `bulk_update()` để sửa nội dung phiếu vì các lệnh đó bỏ qua signal/audit. Dữ liệu lịch sử đã lệch từ trước chưa được backfill hàng loạt trên DB thật.
