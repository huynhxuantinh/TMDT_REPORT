# Rà soát và sửa lỗi 08–16

Ngày kiểm tra: 30/10/2026. Bản đối chiếu trước thay đổi: `262ebe4`.

## Kết quả rà soát

| Mục | Phát hiện và xử lý |
| --- | --- |
| 08 — Ảnh khám | Luồng bác sĩ chưa dùng đầy đủ bộ kiểm tra chung. Đã gom kiểm tra vào `core/exam_uploads.py`, dùng ở tạo/sửa phiếu, CRUD quản trị và model ảnh. Kiểm tra nội dung thật bằng Pillow, giới hạn dung lượng, số lượng và kích thước trước khi lưu. |
| 09 — Thời điểm khám | Đã bắt buộc chuyển `cho → dang → xong`, kiểm tra bác sĩ phụ trách, ngày/giờ và quyền ngoại lệ trong transaction có khóa lịch khám. Mỗi chuyển trạng thái có audit. |
| 10 — Giờ đặt lịch | Model kiểm tra cả ngày và giờ theo múi giờ Django khi tạo hoặc đổi slot; từ chối giờ đã qua trong ngày hiện tại và giờ có giây lẻ. Sửa thông tin lịch sử mà không đổi slot không bị coi là đặt lịch mới. |
| 11 — Đặt đồng thời | DB **đã có** unique constraint có điều kiện cho slot bác sĩ và slot bệnh nhân, loại lịch đã hủy. Giữ nguyên các constraint; thêm service atomic khóa bệnh nhân rồi bác sĩ, kiểm tra lại dưới khóa. Khóa bệnh nhân còn bảo vệ quy tắc một lịch/ngày của luồng đặt lịch. View xử lý xung đột thành thông báo chọn lại giờ. |
| 12 — GET đổi trạng thái | Đã bỏ tự bắt đầu khám khi mở phiếu và tự sửa sang `xong` khi mở danh sách. Bắt đầu/hủy dùng POST; hoàn tất nằm trong nhánh POST, giữ CSRF và kiểm tra quyền. GET/refresh chỉ đọc nghiệp vụ. |
| 13 — Lịch làm việc | Trước đây lưu từng ngày trước khi kiểm tra ngày sau. Nay kiểm tra đủ 7 ngày rồi lưu trong một transaction có khóa bác sĩ; bất kỳ lỗi lưu nào cũng rollback cả đợt. |
| 14 — Sau đăng nhập | Giữ `next` qua cả đăng nhập sai; chỉ chấp nhận URL an toàn cùng host. Quay lại đúng đường dẫn đặt lịch và giữ khoa/bác sĩ/ngày/giờ được truyền trong query. |
| 15 — Liên kết | Signal tạo thông báo dùng route thực tế. Link cũ `/lich-kham/<id>/` được ánh xạ khi hiển thị, kiểm tra sở hữu và chuyển sang phiếu nếu đã khám; ẩn link không hợp lệ/ngoài quyền. Trang bệnh nhân của bác sĩ đã được giới hạn phạm vi trong P1, không sửa lại không cần thiết. |
| 16 — Bản đồ | Dùng chung bộ chuyển tọa độ, kiểm tra hữu hạn và phạm vi; thiếu tọa độ truyền JSON `null`, không ép thành `(0,0)`. Trang chủ `fitBounds()` theo kết quả; vị trí người dùng ban đầu không kéo bản đồ khỏi kết quả tìm kiếm. Tọa độ số 0 hợp lệ vẫn được xử lý như tọa độ thật. |

## Chính sách ảnh và thời gian

- Mỗi phiếu tối đa **10 ảnh**, tính cả ảnh giữ lại; mỗi ảnh tối đa **5 MB**, mỗi lượt upload tối đa **20 MB**.
- Chấp nhận JPEG/JPG, PNG, WEBP tĩnh; nội dung phải khớp đuôi file, Pillow phải xác minh và giải mã được. Mỗi chiều tối đa **4096 px**, tổng tối đa **16 triệu pixel**.
- Kiểm tra toàn bộ ảnh trước khi thay đổi phiếu; khóa phiếu khi kiểm tra tổng số ảnh để hai lượt upload đồng thời không vượt giới hạn.
- Khám bình thường: cùng ngày hẹn, tại hoặc sau giờ hẹn. Lịch tương lai cần quyền `core.kham_som`; lịch của ngày trước cần `core.kham_hoi_to`.
- Ngoại lệ cần lý do **10–500 ký tự**, được lưu cùng người thao tác trong nhật ký. Vẫn chỉ bác sĩ phụ trách được thao tác. Không tự cấp hai quyền này cho tài khoản/nhóm hiện có; superuser có quyền theo cơ chế chuẩn Django.
- Cả lúc bắt đầu và hoàn tất đều kiểm tra lại điều kiện. Không cho POST hoàn tất trực tiếp khi còn trạng thái chờ.

## File thay đổi

- Nghiệp vụ và validation: `core/views.py`, `core/models.py`; mới `core/appointments.py`, `core/exam_uploads.py`, `core/geo.py`.
- Migration: `core/migrations/0031_exam_upload_and_time_permissions.py`.
- Template trong `core/templates/core/`: `doctor_exam.html`, `doctor_exam_edit.html`, `doctor_home.html`, `doctor_notifications.html`, `home.html`, `hospital_detail.html`, `login.html`; mới `exam_time_exception.html`.
- Kiểm thử: `core/test_followup_security.py`, cập nhật các test luồng khám trong `core/tests.py`; kiểm tra Edge tại `.tools/qa_followup_browser.py`.

Không sửa cấu hình VNPAY/Mailtrap, secret, dữ liệu tiền hoặc logic hóa đơn. Các bảo vệ P1 được giữ lại và chạy hồi quy.

## Cập nhật trên máy khác

Trong PowerShell đã kích hoạt `.venv` và nạp môi trường GIS của project:

```powershell
python manage.py migrate
python manage.py check
python manage.py runserver 127.0.0.1:8000
```

Migration 0031 cập nhật trạng thái model/validator và khai báo hai quyền; không có SQL thay đổi bảng nghiệp vụ. Trên máy local hiện tại đã sao lưu DB vào `.vnpay-test/p2-local-before.dump`, áp dụng thành công và đối chiếu hash toàn bộ cột dữ liệu nghiệp vụ/tài khoản cũ cùng `.env`: không thay đổi.

## Bằng chứng kiểm thử

Kiểm thử chạy trên PostgreSQL/PostGIS riêng tại cổng 55439, không dùng DB ứng dụng để tạo dữ liệu thử. Không gọi thanh toán hoặc gửi email thật.

- **132/132 test liên quan và hồi quy đạt**, gồm 26 test mới. Bao phủ upload giả/quá dung lượng/quá kích thước/quá số lượng, rollback phiếu và lịch làm việc, quyền ngoại lệ, ngày giờ, POST/CSRF, link theo quyền, an toàn `next`.
- Test đồng thời gồm: hai HTTP request đặt cùng slot; hai bệnh nhân cùng bác sĩ/slot; cùng bệnh nhân đặt hai giờ; ghi trực tiếp kiểm tra unique constraint; hai lần hoàn tất một lịch; hai lượt upload vào phiếu đang có 9 ảnh. Chỉ một thao tác được chấp nhận ở các xung đột tương ứng.
- **Django system check đạt**; `makemigrations --check --dry-run`: không thiếu migration; `git diff --check`: không có lỗi whitespace.
- **Edge đạt**: 6 kiểm tra bản đồ ở 1440/768/390 px, đăng nhập quay lại đặt lịch, GET/refresh không đổi trạng thái; không có lỗi JavaScript. Leaflet thật được tải; tile bản đồ bị chặn trong test vì kiểm tra bounds/marker không cần tile.
- **Suite toàn bộ chưa xanh**: bản trước sửa chạy 352 test, có 28 failure + 182 error; bản sau chạy 378 test, có 30 failure + 180 error. Cả hai có 210 lượt failure/error thuộc cùng 182 tên ca kiểm thử (có subtest); **không có tên ca thất bại mới**, cũng chưa giải quyết các lỗi tồn tại đó. Không coi kết quả này là 378/378 đạt.

Log và ảnh chụp lưu trong thư mục được Git bỏ qua `.vnpay-test/`: `p2-focused-final.log`, `p2-baseline.log`, `p2-full-final.log`, `p2-regression-comparison.json`, `p2-browser.log`, `followup-browser/`.

Để chạy riêng 26 test mới trên môi trường test PostgreSQL/PostGIS đã cấu hình:

```powershell
python manage.py test core.test_followup_security --noinput
```

Script Edge yêu cầu settings test riêng và từ chối DB ngoài cổng 55439. Khi nghiệm thu thực tế, nên kiểm tra thêm tải ảnh từ điện thoại và chính sách cấp quyền khám sớm/hồi tố cho đúng nhân sự; không cần thử bằng hồ sơ thật.
