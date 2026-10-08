# Phase và Fast TID — 0.2.2-pyqc.2

## Wire ZK → Nation

Không dùng MID 0214 vì tài liệu Nation mô tả đó là thao tác Monza QT, không phải lệnh lấy phase. Giữ inventory Nation 0210. Các record Nation 1200 vẫn có EPC/PC/ANT/result như trước, thêm các PID đã có trong parser của Bình.

| Trường | ZK | Nation |
|---|---|---|
| Phase | Q bit4=1; length bit6=1; sau RSSI có begin U16 + end U16 + frequency U24, big endian | PID09 U8, lấy phase **end**, bước 360/128=2.8125° |
| Tần số | U24 kHz của chính record có phase | PID08 U32 big endian kHz |
| Fast TID | Q bit5=1; length bit7=1; 12 byte TID nối sau EPC | PID03 + BE16 byte length + TID |

Thang phase hiển thị theo mã demo ZK đi kèm SDK: `(raw * 0.087) % 180` độ, sau đó làm tròn `degrees * 128 / 360` để đóng gói Nation PID09. Đây là phép lượng tử hóa tương thích hiển thị demo, không phải phép đo được hiệu chuẩn hay ánh xạ không mất dữ liệu. Nation chỉ có một phase; chọn phase end. Chênh lệch làm tròn tối đa khoảng 1.40625°. Cả begin/end raw và độ vẫn có trong API Python để kỹ sư dùng trực tiếp. Không suy ra khoảng cách/vị trí từ phase này.

Parser không tạo phase nếu record không có bit6. Tần số lấy từ reader, thay cho giá trị mặc định UI nếu parser web sử dụng PID08. Checkbox Phase của web có thể chỉ điều khiển hiển thị; Python yêu cầu phase theo config dịch vụ nên không phụ thuộc checkbox đó có gửi lệnh hay không.

## Fast TID và fallback

FastID native chỉ dùng khi ứng dụng yêu cầu TID ở word0, tối đa 6 word, password 00000000. Chỉ dùng TID của **chính record hiện tại**. Tag không có bit7 thì gọi selected Read TID; TID dài/password khác 0 cũng đọc như trước. EPC-only không bật FastID. PC vẫn đọc một lần mỗi EPC trong phiên và cache như bản cũ.

FastID có thể trả record thường và record có TID trong cùng một batch. Không được lấy 12 byte cuối EPC làm TID nếu bit7 không bật. Parser kiểm tra độ dài và phần phase, không nuốt phần dư/cắt record lỗi.

## Q/Session trong Fast inventory Parameter của ZK

CFG9 có Q cơ sở ở 4 bit thấp; hỗ trợ cờ phase bit4 và FastID bit5. Python trả `q` 0–15 và các trường `phase`, `fast_tid`, `q_flags_raw` riêng. Set Q/Session từng phần giữ lại các cờ, tránh hiểu Q=0x14 thành Q20. API mở rộng: `reader.query(phase=True, persist=False)`. Inventory của bridge đặt cờ riêng trong command01, không tự lưu thay đổi CFG9 vào flash.

## Nguồn đối chiếu / QC

- Ex10 SDK manual V2.25, Inventory_G2: Q bit4/5; 12-byte FastID TID; phần phase 7 byte.
- Tài liệu giao thức ZK, Tag Inventory 8.2.1: cờ length bit6/7.
- Source Ex10 `RWDev.cs`: begin/end/frequency parse big endian; `Form1.cs` normal EPC display nhân 0.087 và modulo180.
- Giao thức Nation trang 68: PID08 kHz U32 và PID09 phase U8, `(value/128)*2π`.
- `src/zk_nation/vendor/nrn.py` giữ nguyên; parser gốc đọc PID03/08/09.

`test_phase_fastid.py` có 8 bài: record kết hợp, parser Nation gốc, fallback không FastID, tắt cờ, thiếu phase, EPC-only, TID ngắn, record cắt cụt và CFG9 giữ cờ. Cộng 13 bài cũ là 21 bài. Bằng chứng phần cứng lưu trong `phase-fastid-probe.json`, `phase-fastid-results.json` và bài qua dịch vụ `phase-fastid-service-results.json` khi đã cài bản mới. Xem trường status trong từng file; không dùng dữ liệu mô phỏng để khẳng định reader hỗ trợ.
