# TASK 04 — Tính tiền và lập hóa đơn sau khám

> Báo cáo tại thời điểm hoàn tất TASK 04. TASK 05 đã bổ sung BHYT MÔ PHỎNG: `HoaDon.so_tien` là phần bệnh nhân trả sau hỗ trợ, có xử lý hóa đơn 0đ. Xem [quy tắc hiện hành và kết quả TASK 05](TASK05_BHYT_MO_PHONG.md).

Đã tích hợp trên QL_BENHVIEN/MedCare GIS. Khi bác sĩ hoàn tất ca có dịch vụ hợp lệ, hệ thống tạo hóa đơn từ dịch vụ và giá danh mục. Khi điều chỉnh dịch vụ trên hóa đơn tự động chưa thanh toán, hệ thống tính lại chi phí, lưu phiên bản chi tiết mới và lịch sử trước/sau. VNPAY tiếp tục dùng số tiền trong database.

Migration **0022_hoa_don_dich_vu đã áp dụng trên máy hiện tại**. Các cột dữ liệu cũ được đối chiếu trước/sau và không thay đổi. Không chuyển đổi hàng loạt hóa đơn thử nghiệm cũ.

## 1. File thêm/sửa

| File | Thay đổi |
| --- | --- |
| `core/models.py` | Thêm ChiTietHoaDon, nguồn/phiên bản/liên kết phiếu của HoaDon, cờ hủy dịch vụ và các ràng buộc |
| `core/migrations/0022_hoa_don_dich_vu.py` | Migration bổ sung sau 0021, không có bước tính lại dữ liệu cũ |
| `core/billing/__init__.py`, `core/billing/services.py` | Tính tiền, chốt giá, tạo/điều chỉnh hóa đơn, kiểm tra toàn vẹn và audit |
| `core/exam_services/services.py` | Nới khóa có kiểm soát với hóa đơn tự động; tính lại tiền trong cùng transaction; hủy mềm dòng đã lập hóa đơn |
| `core/views.py` | `doctor_exam` tạo hóa đơn khi hoàn tất; `phieu_kham_detail` hiển thị giá chốt/thành tiền/tổng |
| `core/payments/services.py` | Kiểm tra tổng và dịch vụ trước khi thu; xử lý callback giao dịch kết thúc sau khi hóa đơn đổi phiên bản |
| `core/payments/views.py` | Bỏ form tiền thủ công; trang chi tiết có dịch vụ/audit; endpoint lập hóa đơn dùng chi phí backend |
| `core/templates/core/exam_services/editor.html`, `row.html`, `read_only.html` | Đơn giá, thành tiền, tổng dự kiến/đã chốt, thông báo khóa |
| `core/templates/core/exam_detail.html` | Dùng dữ liệu dịch vụ hiện hành và giá đã chốt |
| `core/static/core/js/exam_services.js` | Tổng dự kiến bằng BigInt, thay đổi theo chọn/xóa/số lượng |
| `core/templates/core/payments/detail.html`, `list.html` | Hiển thị hóa đơn dịch vụ và lịch sử chi phí; bỏ nhập số tiền thử |
| `core/test_billing.py` | 36 test TASK 04, gồm đồng thời và migration |
| `core/test_exam_services.py`, `core/test_service_catalog.py`, `core/test_vnpay.py`, `core/tests.py` | Điều chỉnh test theo quy tắc mới và dùng model lịch sử khi test migration cũ |
| `README.md`, `docs/VNPAY_SANDBOX.md`, `docs/CHAY_VNPAY_MAY_MOI.md`, `docs/TASK03_DICH_VU_THUC_HIEN.md` | Cập nhật hướng dẫn theo luồng mới |
| `docs/TASK04_HOA_DON_TU_DONG.md` | Báo cáo này |

Đã đối chiếu source: không thay đổi thuật toán ký/xác minh trong `core/payments/vnpay.py`, cấu hình `hospital/settings.py`, URL, mã quản lý danh mục hoặc migration 0001–0021. Model GiaoDichThanhToan và các signals cũ giữ nguyên. Trong `core/views.py`, chỉ hai hàm nêu trên thay đổi so với TASK 03.

## 2. Model và quan hệ dữ liệu

```text
LichKham ── 1 HoaDon ── nhiều ChiTietHoaDon (chia theo phien_ban)
   │            │                   │
   └─ PhieuKham ┘                   ├─ DanhMucDichVu
          └─ nhiều DichVuThucHien ───┘
```

`HoaDon` giữ OneToOne với lịch khám, số tiền, trạng thái và toàn bộ trường thanh toán cũ; bổ sung:

- `nguon`: `manual` cho hóa đơn thử nghiệm cũ, `services` cho hóa đơn tính từ dịch vụ.
- `phieu_kham`: OneToOne PROTECT, bắt buộc đối với hóa đơn `services`; để NULL cho hóa đơn cũ, không backfill.
- `phien_ban`: số nguyên dương, bắt đầu từ 1.
- `chi_tiet_hien_hanh`: các dòng thuộc đúng phiên bản hiện hành.

`ChiTietHoaDon` lưu hóa đơn, dịch vụ danh mục, dịch vụ thực hiện gốc, phiên bản, snapshot mã/tên/nhóm dịch vụ, số lượng, đơn giá, thành tiền, thời gian tạo. Ràng buộc UNIQUE `(hoa_don, phien_ban, dich_vu)`, số lượng/phiên bản dương, giá/thành tiền không âm và `thanh_tien = don_gia × so_luong`.

Các dòng chi tiết đã chốt không bị ghi đè hoặc xóa qua luồng nghiệp vụ. Phiên bản mới được thêm, phiên bản cũ vẫn truy vấn được bằng `invoice.chi_tiets.filter(phien_ban=...)`. FK PROTECT và pre_delete bảo vệ dữ liệu đã dùng trong hóa đơn.

`DichVuThucHien.da_huy` mặc định False. Khi bác sĩ xóa dòng đã dùng trong hóa đơn, đánh dấu True, giữ bản ghi gốc cho các phiên bản cũ. UNIQUE chỉ áp dụng cho dòng chưa hủy; có thể chọn lại dịch vụ đang áp dụng thành một dòng mới. Dòng chưa từng được lập hóa đơn có thể xóa như TASK 03.

## 3. Cách tính và chốt giá

```text
Thành tiền mỗi dòng = đơn giá đã chốt × số lượng
Tổng chi phí = tổng thành tiền của các dòng thuộc phiên bản hiện hành
HoaDon.so_tien = tổng chi phí trước BHYT
```

Backend dùng Decimal và lấy dữ liệu từ database. Giá/tên/thành tiền giả mạo trong POST không được dùng để tính tiền. Trình duyệt chỉ hiển thị dự kiến bằng BigInt, không có ô sửa đơn giá.

Quy tắc giá:

1. Lập hóa đơn lần đầu: lấy và chốt đơn giá, mã, tên, nhóm từ danh mục tại lúc lưu.
2. Đổi bảng giá sau đó: không tác động hóa đơn đã lập.
3. Sửa số lượng dịch vụ đang có: giữ đơn giá đã chốt của dòng đó.
4. Thêm dịch vụ mới: lấy giá danh mục hiện tại. Dịch vụ đã xóa và lưu rồi chọn lại là dòng thực hiện mới, dùng giá tại lúc thêm lại.
5. Chỉ sửa ghi chú: giữ tiền và phiên bản hóa đơn; vẫn ghi log thay đổi dịch vụ theo TASK 03.

Phí khám được chọn như một dịch vụ thực tế. Nhóm **Khám** / **Khám bệnh** được nhận diện để gắn nhãn phí khám trong tìm kiếm, không hard-code mã dịch vụ. Hệ thống không tự cộng một khoản phí khám ngoài các dòng đã chọn, nên không tính hai lần. Không bắt buộc mọi ca có nhóm Khám; bác sĩ phải chọn đúng dịch vụ thực tế của ca đó.

Không có dịch vụ thì không được hoàn tất để tạo hóa đơn rỗng. Tổng phải nằm trong giới hạn đã có của HoaDon/VNPAY project: **5.000–9.999.999.999 VND**. Dòng miễn phí 0đ được phép nếu tổng cả hóa đơn hợp lệ. Tổng 0, dưới giới hạn hoặc vượt giới hạn sẽ báo lỗi và hoàn tác; không tự nâng tiền cho đủ mức tối thiểu.

## 4. Tạo hóa đơn và điều chỉnh chi phí

Khi hoàn tất khám, trong cùng transaction: khóa lịch khám → tạo phiếu → lưu dịch vụ/ảnh → chuyển lịch sang `xong` → lập hóa đơn và các dòng giá chốt → ghi log. Validation ảnh hoặc lỗi ghi hóa đơn/log làm hoàn tác dữ liệu trong transaction, gồm lịch sử/thông báo do signals tạo.

Gửi request hoàn tất lại không tạo thêm hóa đơn. Dịch vụ lập hóa đơn trả lại hóa đơn đã có khi không yêu cầu tính lại. OneToOne ở database và khóa dòng lịch khám ngăn hai request đồng thời tạo trùng.

Khi bác sĩ phụ trách sửa dịch vụ trên phiếu, việc thay đổi dịch vụ, tăng phiên bản, thêm chi tiết, cập nhật tổng và ghi log cùng nằm trong transaction. LogHeThong `model_name='HoaDon'` lưu người thao tác, thời gian, phiếu, phiên bản, tổng trước/sau và từng dòng số lượng/giá/thành tiền. Không ghi triệu chứng, chẩn đoán, hướng điều trị hoặc ghi chú dịch vụ vào log thanh toán.

| Tình trạng | Điều chỉnh dịch vụ ảnh hưởng tiền |
| --- | --- |
| Hóa đơn tự động unpaid, chưa có giao dịch | Cho phép bác sĩ phụ trách |
| Chỉ có giao dịch đã được IPN xác nhận failed/cancelled/expired | Cho phép; lần thanh toán sau lấy tổng mới |
| Có giao dịch pending, chưa rõ kết quả, kể cả hết thời gian cục bộ | Khóa |
| Có success hoặc hóa đơn paid | Khóa |
| Có `can_doi_soat` | Khóa, cần đối soát |
| Hóa đơn manual cũ, kể cả unpaid | Giữ khóa; không tự chuyển đổi hoặc tính lại |

Không cho xóa hết dịch vụ để biến hóa đơn thành 0đ. Phiếu khám/ảnh giữ quyền sửa cũ; khóa chi phí không tự mở rộng sang các trường bệnh án.

Hóa đơn cũ trước TASK 04 được gắn nguồn `manual`, phiên bản 1, không có chi tiết giả định. Tiền, trạng thái và giao dịch giữ nguyên. Ca cũ hoàn tất có dịch vụ nhưng chưa có hóa đơn có thể dùng nút **Lập hóa đơn từ dịch vụ** tại quầy; đây là thao tác riêng có chủ đích, không phải chuyển đổi hàng loạt. Bác sĩ sửa dịch vụ thực tế trên ca cũ chưa có hóa đơn cũng sẽ tạo hóa đơn khi lưu thành công. Chỉ mở/xem phiếu không tạo hóa đơn.

## 5. Kết nối VNPAY và đồng thời

Bắt đầu thanh toán vẫn khóa lịch khám rồi hóa đơn, lấy `HoaDon.so_tien` làm snapshot giao dịch và ký URL như trước. Với hóa đơn tự động, trước khi thu kiểm tra thêm tổng chi tiết, dịch vụ thực hiện, số lượng và liên kết phiếu; dữ liệu không khớp thì chặn thu.

Khi sửa dịch vụ, giữ khóa lịch khám và hóa đơn đến hết transaction. IPN giữ khóa hóa đơn rồi giao dịch. Vì vậy, thanh toán bắt đầu trước thì chỉnh sửa bị chặn; chỉnh sửa lưu trước thì giao dịch dùng tổng mới. IPN thành công trong lúc thao tác chỉnh sửa chờ khóa sẽ khiến việc chỉnh sửa bị từ chối.

Điều chỉnh tối thiểu trong xử lý IPN: vẫn kiểm tra chữ ký, terminal và tiền của **giao dịch gốc** trước. Với giao dịch đã kết thúc, xử lý callback lặp/mâu thuẫn trước khi so tiền với hóa đơn hiện hành. Điều này cần thiết vì một giao dịch thất bại cũ có thể nhận callback sau khi hóa đơn đã đổi tiền:

- Callback thất bại lặp đúng: trả `02`, giữ nguyên dữ liệu.
- Callback mâu thuẫn, chẳng hạn thất bại rồi báo thành công: trả `99`, bật cờ đối soát, khóa sửa/thu tiếp; không ghi đè kết quả cũ.
- Giao dịch pending vẫn phải khớp số tiền hóa đơn hiện hành trước khi cập nhật.

Không sửa HMAC SHA512, cách mã hóa tham số, domain ngrok, ReturnURL, IPN URL hoặc quy tắc phân loại mã kết quả. Hàm tạo hóa đơn nhập tay cũ còn trong module để dựng dữ liệu tương thích/hồi quy, nhưng không còn được gọi bởi view hiện hành. Endpoint lập hóa đơn không nhận giá trị nhập tay.

## 6. Giao diện và URL

Không thêm hoặc đổi route:

| URL | Cách dùng |
| --- | --- |
| `/bac-si/` | Đăng nhập bác sĩ, chọn lịch được phân công |
| `/bac-si/kham/<lich_id>/` | Chọn dịch vụ, xem đơn giá/thành tiền/tổng dự kiến, hoàn tất khám |
| `/bac-si/phieu-kham/<phieu_id>/sua/` | Điều chỉnh dịch vụ và hóa đơn khi được phép |
| `/phieu-kham/<phieu_id>/` | Xem dịch vụ hiện hành và tổng đã chốt theo quyền phiếu hiện có |
| `/thanh-toan/` | Danh sách thu tiền sau khám |
| `/thanh-toan/lich-kham/<lich_id>/` | Bệnh nhân, bệnh viện, bác sĩ, ngày khám, chi tiết, tổng, trạng thái, lịch sử chi phí và nút VNPAY |
| POST `/thanh-toan/lich-kham/<lich_id>/hoa-don/` | Lập hóa đơn từ dịch vụ cho ca cũ chưa có hóa đơn; không sửa hóa đơn đã có |
| POST `/thanh-toan/hoa-don/<invoice_id>/vnpay/` | Bắt đầu thanh toán |
| `/thanh-toan/vnpay/ipn/`, `/thanh-toan/vnpay/return/` | Giữ nguyên callback |

Trang quầy vẫn cần superuser hoặc staff có quyền `core.thu_vnpay`. Bệnh nhân và bác sĩ thông thường không có quyền truy cập quầy. Bác sĩ chỉ được điều chỉnh chi phí của lịch được phân công; Admin không được dùng quyền thu tiền để sửa dịch vụ hộ bác sĩ.

## 7. Kiểm thử thực tế

Ngày 28/10/2026, Django 6.0.1, Python 3.12.10, PostgreSQL 18/PostGIS; dùng cluster kiểm thử riêng `127.0.0.1:55439`. Không reset database nghiệp vụ.

| Bộ kiểm tra | Kết quả |
| --- | --- |
| TASK 04 — tính tiền, snapshot, audit, khóa, IPN, đồng thời, migration | **36/36 đạt** |
| Hồi quy TASK 03 | **31/31 đạt** |
| Hồi quy TASK 02 | **27/27 đạt** |
| Hồi quy VNPAY TASK 01 | **38/38 đạt** |
| Luồng bác sĩ cũ | **7/7 đạt** |
| Dashboard Admin | **11/11 đạt** |
| Đặt lịch | **4/4 đạt** |
| Ràng buộc tọa độ GIS | **3/3 đạt** |
| **Tổng hai lượt chạy: 121 hồi quy + 36 mới** | **157 bài đạt** |

Các test cũ được cập nhật ở những điểm yêu cầu mới thay đổi hành vi: hoàn tất cần dịch vụ; hoàn tất tạo hóa đơn; không xóa dịch vụ cuối cùng; dòng đã lập hóa đơn được hủy mềm; endpoint lập hóa đơn dùng dịch vụ thay cho số tiền nhập tay. Test migration TASK 02/03 dùng model lịch sử để truy vấn đúng schema cũ. Các test chữ ký/callback VNPAY cũ giữ nguyên.

Test TASK 04 kiểm tra đủ luồng một/nhiều dịch vụ, không cộng trùng phí khám, Decimal và giới hạn tiền, snapshot giá/tên, phiên bản/audit, bảo vệ dữ liệu cũ, hoàn tất lặp/đồng thời, cạnh tranh sửa với thanh toán/IPN, thất bại rồi thử lại, callback cũ sau khi đổi tiền, quyền/giá giả mạo, rollback và migration giữ hóa đơn đã trả.

Kiểm tra bổ sung:

- `manage.py check`: không lỗi.
- `manage.py makemigrations --check --dry-run`: không còn thay đổi model thiếu migration.
- `node --check core/static/core/js/exam_services.js`: đạt.
- Edge headless với profile riêng, HTML render từ database test: **10 kiểm tra ở mỗi chiều rộng nội dung 1256px và 492px đều đạt**, gồm tìm không dấu, chọn thêm, đổi số lượng/tổng, chống chọn lặp qua tìm kiếm, xóa dòng lỗi không chặn submit, thêm dòng động, không tràn ngang toàn trang. Đây là kiểm tra DOM/JS trên dữ liệu giả lập, chưa phải thao tác thanh toán end-to-end qua ngrok.
- Migration 0022 trên database hiện tại: đối chiếu hash tất cả cột đã có của HoaDon, GiaoDichThanhToan, PhieuKham, LichKham, DanhMucDichVu, DichVuThucHien trước/sau đều không đổi. Bảng ChiTietHoaDon ban đầu rỗng; không tự sinh hóa đơn cho ca cũ.
- Django RequestFactory render bằng tài khoản phù hợp: trang sửa/xem phiếu #9, `/thanh-toan/`, trang thu tiền lịch #21 đều HTTP 200; không ghi thêm giao dịch nghiệp vụ.

Log tại `.vnpay-test/task04-regression.log` và `.vnpay-test/task04-billing-tests.log` (gitignored). Cluster kiểm thử được dừng sau khi hoàn tất. Chưa tạo một giao dịch mới trên cổng VNPAY Sandbox thật ở TASK 04; IPN trong test được ký bằng khóa giả lập.

Lệnh chạy lại sau khi nạp GIS và trỏ DB sang môi trường kiểm thử riêng có PostGIS:

```powershell
.\.venv\Scripts\python.exe manage.py test core.test_billing core.test_exam_services core.test_service_catalog core.test_vnpay core.tests.DoctorFlowTests core.tests.AdminDashboardTests core.tests.AppointmentBookingTests core.tests.CoordinateConstraintTests --noinput --verbosity 2
```

Hai lỗi AuthFlowTests đã ghi nhận từ TASK 01 vẫn ngoài phạm vi: test username chỉ có số không khớp rule đăng ký; biến `username` chưa gán trong quên mật khẩu. Không sửa hoặc chạy lại suite xác thực đó; không tuyên bố mọi test của toàn project đều đạt.

## 8. Hướng dẫn chạy và kiểm thử local

Máy hiện tại đã migrate. Giữ `.env`, PostgreSQL và cấu hình GIS đã chạy ổn. Trong **Terminal PowerShell của VS Code**, tại thư mục project:

```powershell
cd C:\Y4_S1\TMDT\QL_BENHVIEN
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Nếu Django đang chạy thì tải lại trang; nếu cần khởi động lại, Ctrl+C ở terminal Django rồi chạy dòng runserver. Trên máy khác, chạy `python manage.py migrate` sau khi cài môi trường, trước runserver; không dùng `--fake`. Xem [hướng dẫn máy mới](CHAY_VNPAY_MAY_MOI.md) cho cấu hình đầy đủ.

Kiểm thử theo thứ tự:

1. Admin vào `/quan-tri/dich-vu/`, chuẩn bị phí khám và dịch vụ đang áp dụng. Dùng dữ liệu kiểm thử, không sửa giá thật chỉ để ép tổng.
2. Đăng nhập bác sĩ phụ trách, chọn một lịch thử chưa hoàn tất; bắt đầu khám và nhập bệnh án như trước.
3. Chọn phí khám 100.000 × 1 và dịch vụ 50.000 × 2 (nếu bạn đã tạo các giá thử này): tổng dự kiến phải là 200.000 VND. Bấm **Hoàn thành khám**.
4. Kiểm tra thông báo có mã hóa đơn và tổng. Mở phiếu, xác nhận dịch vụ/giá chốt; kiểm tra ảnh, lịch sử và thông báo như trước.
5. Đăng nhập tài khoản thu tiền tại `/thanh-toan/`, mở đúng ca: phải có chi tiết và tổng 200.000; không có ô nhập sửa số tiền.
6. Trước khi bấm VNPAY, bác sĩ sửa số lượng dịch vụ từ 2 thành 3: tổng thành 250.000, phiên bản tăng, lịch sử chi phí có tổng trước/sau.
7. Admin đổi giá danh mục: hóa đơn đã lập giữ giá. Sửa số lượng dòng cũ vẫn dùng giá chốt; thêm dịch vụ mới dùng giá hiện tại.
8. Bấm **Thanh toán VNPAY**: quay lại sửa phiếu để xác nhận dịch vụ bị khóa khi pending. Không kết luận thất bại chỉ vì đóng tab/quá hạn cục bộ.
9. Hoàn tất thanh toán Sandbox theo [hướng dẫn VNPAY](VNPAY_SANDBOX.md), giữ ngrok chạy ở cửa sổ PowerShell Windows riêng. IPN hợp lệ chuyển paid; phiếu tiếp tục khóa chi phí và không thể thu lần hai.
10. Với ca thử khác nhận IPN thất bại xác định: kiểm tra tiền chưa đổi, được thử lại; bác sĩ có thể điều chỉnh trước lần thu mới. Không tự sửa trạng thái database để mở khóa.
11. Thử bỏ hết dịch vụ, số lượng 0/âm, sửa phiếu của bác sĩ khác và mở đồng thời hai tab: phải báo lỗi/chặn, không lưu dở hoặc tạo hóa đơn trùng.
12. Mở lại hóa đơn thử nghiệm cũ, kể cả đã thanh toán: tiền/trạng thái giữ nguyên; không có chi tiết tự bịa hoặc chuyển nguồn sang services.

## 9. Giới hạn và chuẩn bị TASK 05

TASK 04 đã hoàn thành phần triển khai và kiểm thử nêu trên. Phần còn cần bạn xác nhận là một lượt thanh toán Sandbox mới từ hóa đơn tự động qua cấu hình ngrok/VNPAY của bạn và thao tác trên thiết bị thực tế.

Giới hạn có chủ đích: bảo toàn và khóa chi phí hóa đơn manual cũ; tổng theo giới hạn tiền hiện tại; không tự mở khóa giao dịch chưa rõ kết quả; chưa có đối soát tự động. Phiếu hoàn tất trước TASK 04 không được tự chuyển đổi hàng loạt.

TASK 05 có thể bổ sung tổng trước bảo hiểm, số tiền BHYT mô phỏng chi trả, phần bệnh nhân thanh toán và bản chốt quy tắc hưởng theo phiên bản. ChiTietHoaDon hiện giữ chi phí gốc để làm đầu vào. Khi triển khai cần phân biệt tổng dịch vụ với số tiền phải thu và mở rộng kiểm tra trước thanh toán tương ứng; không sửa lịch sử giao dịch đã chốt. Chưa triển khai các trường/tính toán BHYT ở TASK 04.

Không bổ sung tiền mặt, dashboard thu ngân hoàn chỉnh, PDF, Mailtrap, hoàn tiền hoặc MoMo.
