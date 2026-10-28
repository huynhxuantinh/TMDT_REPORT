# TASK 05 — BHYT MÔ PHỎNG

Đã triển khai và áp dụng migration **0023_bhyt_mo_phong** ngày **29/10/2026**. Chức năng chỉ mô phỏng phục vụ đồ án, không kết nối BHXH hoặc xác nhận quyền lợi bảo hiểm thực tế.

## 1. Chức năng và dữ liệu

- Admin quản lý quy tắc theo dịch vụ: tên, tỷ lệ 0–100% với tối đa hai chữ số thập phân, ngày bắt đầu/kết thúc, bật/tắt và lịch sử trước/sau. Không nạp sẵn tỷ lệ.
- Hóa đơn tự động tách tổng dịch vụ, BHYT MÔ PHỎNG hỗ trợ và phần bệnh nhân phải trả; lưu snapshot từng dòng cùng phiên bản quy tắc, lý do áp dụng/không áp dụng.
- Khi bác sĩ phụ trách lưu thay đổi dịch vụ trước thanh toán, tính lại BHYT theo dữ liệu hiện tại, đồng thời lưu phiên bản hóa đơn và audit. Chỉ mở phiếu hoặc Admin sửa quy tắc/thẻ không tự tính lại hóa đơn.
- Hóa đơn được hỗ trợ toàn bộ tự chuyển `paid`, có thời điểm hoàn tất và cờ `hoan_tat_bhyt=True`. Giao diện ghi rõ **BHYT MÔ PHỎNG**, không coi đây là giao dịch VNPAY thành công. Không tạo giao dịch 0đ.
- Hóa đơn paid/đã hoàn tất 0đ, có giao dịch pending/success hoặc cần đối soát tiếp tục khóa chỉnh sửa. Hết thời hạn chờ cục bộ không tự mở khóa.

Model mới `QuyTacBHYT`: OneToOne tới `DanhMucDichVu`, một quy tắc cho mỗi dịch vụ để không cộng chồng; có phiên bản và thời gian tạo/cập nhật. Không xóa cứng quy tắc, chỉ ngừng áp dụng.

`HoaDon` bổ sung `tong_chi_phi` (nullable để bảo toàn hóa đơn cũ), `bhyt_ho_tro`, `bhyt_snapshot`, `hoan_tat_bhyt`. `so_tien` là **phần bệnh nhân phải trả**, tiếp tục là nguồn tiền cho VNPAY. Hóa đơn cũ có tổng mới NULL, hỗ trợ 0, cờ False và vẫn đọc tổng qua `tong_truoc_bhyt`.

`ChiTietHoaDon` bổ sung FK `quy_tac_bhyt` PROTECT, `ty_le_bhyt`, `bhyt_ho_tro`, `bhyt_snapshot`; `thanh_tien` vẫn là chi phí dịch vụ trước hỗ trợ. `benh_nhan_tra` là thuộc tính tính từ thành tiền trừ hỗ trợ. Các phiên bản chi tiết cũ được giữ nguyên.

## 2. Công thức và điều kiện

```text
Hỗ trợ mỗi dòng = ROUND_HALF_UP(thành tiền × tỷ lệ / 100, đến 1 VND)
Tổng hỗ trợ = tổng hỗ trợ từng dòng
Bệnh nhân phải trả = tổng chi phí − tổng hỗ trợ
```

Tính bằng Decimal tại backend; giá/tỷ lệ/số tiền do trình duyệt gửi không được dùng để tính. Database kiểm tra tỷ lệ 0–100%, hỗ trợ không âm/không vượt thành tiền, tổng hỗ trợ không vượt tổng chi phí và số phải trả đúng phép trừ.

Điều kiện được xét theo **`LichKham.ngay_kham`**, bao gồm cả ngày đầu/ngày cuối hiệu lực:

1. Bệnh nhân có `BaoHiemYTe` liên kết, mã thẻ không trống và khoảng hiệu lực hợp lệ.
2. Ngày cấp ≤ ngày khám ≤ ngày hết hạn.
3. Bệnh viện của bác sĩ có `co_bhyt=True`.
4. Dịch vụ có quy tắc đang áp dụng, ngày khám nằm trong thời gian hiệu lực quy tắc.

Không đạt điều kiện thì hỗ trợ 0 và lưu lý do. Thẻ hết hạn hôm nay nhưng còn hiệu lực vào ngày khám cũ được xét theo ngày khám, không dùng ngày hiện tại thay thế. Snapshot không lưu số thẻ đầy đủ hoặc nội dung bệnh án vào log thanh toán.

Giá dịch vụ vẫn theo TASK 04: dòng đã có giữ giá chốt, dịch vụ mới lấy giá danh mục hiện tại. Khi điều chỉnh, tỷ lệ BHYT mô phỏng được tính lại bằng quy tắc hiện hành phù hợp ngày khám, lưu vào phiên bản mới.

Các thao tác tính tiền, hỗ trợ, chuyển 0đ, lưu chi tiết/audit đều trong transaction. Khóa lịch khám/hóa đơn đồng bộ với bắt đầu VNPAY/IPN; khóa danh mục/quy tắc đồng bộ với Admin sửa cấu hình. Tỷ lệ không bị thay giữa lúc tính và lưu.

## 3. Admin cấu hình

Vào **`http://127.0.0.1:8000/quan-tri/` → BHYT MÔ PHỎNG**, hoặc `/quan-tri/bhyt-mo-phong/`.

1. Bấm **Thêm quy tắc**, chọn dịch vụ, đặt tên và tự nhập tỷ lệ mô phỏng. Ngày hiệu lực phải bao phủ **ngày khám** cần kiểm thử; ngày kết thúc có thể để trống.
2. Chọn **Đang áp dụng**, lưu. Từ danh sách có thể vào **Sửa / bật tắt** hoặc **Chi tiết / lịch sử**. Dịch vụ liên kết giữ cố định sau khi tạo.
3. Kiểm tra bệnh viện có BHYT trong trang quản lý bệnh viện hiện có `/quan-tri/benh-vien/`.
4. Dùng chức năng có sẵn tại `/quan-tri/du-lieu/bao-hiem-y-te/` để quản lý thẻ và `/quan-tri/du-lieu/benh-nhan/` để liên kết thẻ với bệnh nhân. Hồ sơ bệnh nhân `/profile/` vẫn giữ chức năng BHYT cũ.
5. Bác sĩ chọn dịch vụ và hoàn tất ca mới; tài khoản thu tiền mở `/thanh-toan/` để xem chi tiết hỗ trợ và số phải thu. Hỗ trợ toàn bộ thì hóa đơn tự hoàn tất, không có nút VNPAY.

Quyền Admin theo cơ chế hiện có: tài khoản active có `is_staff` hoặc `is_superuser`. Bác sĩ/bệnh nhân thông thường không được xem hoặc sửa quy tắc. Form có CSRF, kiểm tra tỷ lệ/ngày/trùng dịch vụ và audit nguyên tử.

Các route mới: GET danh sách `/quan-tri/bhyt-mo-phong/`; GET/POST tạo `/tao/`; GET chi tiết `/<id>/`; GET/POST sửa `/<id>/sua/`. Không có route xóa. Các endpoint VNPAY giữ nguyên.

## 4. File thêm/sửa

- Thêm: `core/insurance/{__init__,forms,services,views}.py`; `core/templates/core/insurance/{list,detail,invoice_summary}.html`; `core/migrations/0023_bhyt_mo_phong.py`; `core/test_insurance.py`; báo cáo này.
- Sửa model/nghiệp vụ: `core/models.py`, `core/billing/services.py`, `core/views.py` (chỉ thông báo hoàn tất khám), `core/urls.py`.
- Sửa thanh toán tối thiểu: `core/payments/services.py` (chỉ kiểm tra số phải trả trong `start_payment`); `core/payments/views.py` (thông báo và điều kiện hiển thị nút).
- Sửa giao diện: `core/templates/core/custom_admin_dashboard.html`, `core/templates/core/payments/{detail,list}.html`, `core/templates/core/exam_services/{editor,read_only}.html`.
- Sửa `core/test_billing.py`: test dữ liệu tổng không khớp chi tiết phù hợp constraint mới; test migration đọc đúng model lịch sử 0022.
- Cập nhật README và ghi chú hướng dẫn TASK 04/VNPAY/máy mới.

Đối chiếu hash/AST xác nhận `GiaoDichThanhToan`, các model bệnh nhân/thẻ/bệnh viện/danh mục/dịch vụ thực hiện, signals, mã ký/xác minh HMAC, xử lý IPN/ReturnURL, settings/ngrok và các migration 0001–0022 giữ nguyên.

## 5. Kiểm thử thực tế

**198 bài đạt: 157 hồi quy + 41 bài mới**, chạy hai lượt trên PostgreSQL 18/PostGIS kiểm thử riêng ở `127.0.0.1:55439`.

| Bộ kiểm thử | Đạt |
| --- | ---: |
| BHYT MÔ PHỎNG: tính tiền, quyền Admin, 0đ, đồng thời, migration | 41 |
| TASK 04 | 36 |
| TASK 03 | 31 |
| TASK 02 | 27 |
| VNPAY TASK 01 | 38 |
| Bác sĩ / Admin / đặt lịch / ràng buộc tọa độ GIS | 25 |

Đã kiểm tra các trường hợp không có/hết hạn/chưa hiệu lực thẻ; bệnh viện không BHYT; không có/tắt/hết hiệu lực quy tắc; hỗ trợ một phần/toàn bộ; làm tròn; 0đ tự hoàn tất và không gửi VNPAY; điều chỉnh trước thanh toán; pending/paid/đối soát; callback lặp/mâu thuẫn; số tiền VNPAY sau hỗ trợ; bảo toàn snapshot và migration; rollback audit; request đồng thời; CSRF và giá trị giả mạo.

`manage.py check` không lỗi; `makemigrations --check --dry-run` không thiếu migration. Log: `.vnpay-test/task05-regression.log`, `.vnpay-test/task05-insurance-tests.log` (gitignored). Cluster kiểm thử được dừng sau khi hoàn tất.

Trên database hiện tại: đã migrate 0023; đối chiếu hash tất cả cột cũ của **9 bảng** (hóa đơn, chi tiết, giao dịch, lịch/phiếu khám, danh mục, dịch vụ thực hiện, thẻ, bệnh nhân) không đổi. Không nạp quy tắc hoặc tính lại hóa đơn cũ. RequestFactory render dashboard, danh sách/tạo quy tắc và trang hóa đơn cũ đều HTTP 200, không ghi thêm dữ liệu nghiệp vụ.

Lệnh chạy lại khi đã nạp GIS và trỏ DB sang môi trường kiểm thử riêng:

```powershell
.\.venv\Scripts\python.exe manage.py test core.test_insurance core.test_billing core.test_exam_services core.test_service_catalog core.test_vnpay core.tests.DoctorFlowTests core.tests.AdminDashboardTests core.tests.AppointmentBookingTests core.tests.CoordinateConstraintTests --noinput
```

## 6. Giới hạn còn lại

- **Số phải trả 1–4.999 VND:** hóa đơn giữ đúng số tiền và chưa thanh toán, chặn gửi VNPAY theo mức tối thiểu 5.000 VND đang có trong project; không làm tròn tăng tiền hoặc tự coi đã trả. 0đ là trường hợp hoàn tất riêng do hỗ trợ toàn bộ. Tổng chi phí dịch vụ vẫn theo giới hạn TASK 04: 5.000–9.999.999.999 VND.
- Hóa đơn manual cũ vẫn khóa/bảo toàn. Hóa đơn tự động cũ chỉ xét BHYT khi bác sĩ lưu điều chỉnh hợp lệ, không có chuyển đổi hàng loạt.
- Chưa kiểm thử thủ công toàn bộ giao diện hoặc tạo giao dịch Sandbox thật mới, theo phạm vi yêu cầu. Kết quả IPN ở trên là test có chữ ký bằng khóa giả lập.
- Hai lỗi xác thực cũ đã báo ở TASK 01 nằm ngoài phạm vi, không sửa hoặc chạy lại suite đó; không tuyên bố toàn bộ test project đều đạt.
- Chưa làm tiền mặt, PDF, Mailtrap, hoàn tiền, dashboard thu ngân hoàn chỉnh, BHYT thật hoặc TASK 06.

Máy hiện tại đã migrate; tải lại Django để dùng. Máy khác sau khi cài môi trường cần chạy `python manage.py migrate` trước `runserver`, giữ nguyên `.env` và ngrok đang sử dụng.
