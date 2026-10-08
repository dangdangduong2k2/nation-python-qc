# Python RS232 bridge QC — 0.2.2-pyqc.2

[Tải EXE / source ZIP](https://github.com/dangdangduong2k2/nation-python-qc/releases/tag/v0.2.2-pyqc.2) · [Ghi chú bản QC](RELEASE_NOTES.md) · [Kết quả kiểm thử](VERIFICATION.md)

Đây là repo riêng cho bộ cài **Python**, tách khỏi bridge C# và SDK Web Serial. Máy sử dụng chỉ cần tải EXE ở Release; kỹ sư có thể clone repo để đọc source/build. EXE phát hành là đúng file đã cung cấp, không build lại trong lần đăng này.

EXE cài dịch vụ Windows chạy ngầm. Web [Nextwaves](https://app.nextwaves.com/reader) giữ NATION và chọn cổng tương thích, mặc định **COM52**. Reader ZK thật dùng **COM50** trên máy QC này. Không mở trình duyệt riêng, không thay source website, không cần cài Python/Node.js/.NET hay DLL của hãng.

```text
Web / ứng dụng Nation → COM52 ⇄ COM53 nội bộ → Python → COM50 → ZK
                         frame Nation           frame Ex10
```

## Cài và QC

1. Disconnect Nation/ZK/web đang giữ cổng. Mở `NationPythonQCSetup.exe`.
2. Chọn COM của reader thật, bấm **Setup**, chấp nhận hộp UAC của Windows. Có thanh tiến trình đến khi kiểm tra frame Nation qua cổng công khai thành công.
3. Mở web Bình trong Chrome/Edge như cũ, giữ **NATION**, bấm Connect, chọn **COM52** theo thông báo Complete. Reader ZK được tự nhận diện. COM53 là đầu nội bộ, không chọn trên web.

Dịch vụ `NationPythonQC` tự chạy khi Windows khởi động, chỉ mở reader thật khi web mở COM52. Đóng kết nối web thì trả reader thật. Không cần để cửa sổ setup mở. Chỉ một chương trình được giữ cổng vật lý.

Máy này dùng lại cặp cổng đã cài. Máy Windows x64 mới: bộ cài kèm com0com đã ký và tạo cặp cổng trống; đường cài mới này **chưa thử trên máy Windows sạch**. Windows có thể yêu cầu restart hoặc từ chối driver theo chính sách máy; bộ cài báo lỗi và không tự thay thiết lập bảo mật. Không có driver USB-UART của thiết bị trong gói này.

Cài lại hỗ trợ cập nhật tại chỗ, không cần gỡ bản Python trước. Giữ cặp cổng và config; kiểm tra cổng đang được dùng trước khi đổi dịch vụ. EXE được lưu theo hash để không ghi đè file đang chạy. Dịch vụ bridge C# cũ được dừng và đổi sang khởi động thủ công **sau khi Python kiểm tra thành công**, không xóa. Bản Python ưu tiên ZK **1 anten**, chưa thay thế bản C# cho ZK 4 anten.

## Các chức năng đã chuyển

| Nation MID | Chức năng | Phạm vi ZK |
|---|---|---|
| 0100 / 0200 | Thông tin / khả năng | Thông tin thiếu không tự bịa; 1 anten |
| 010A | GPI | Chỉ IN1; không bật GPO |
| 0201 / 0202 | Set/Get power | ANT1, 0–30 dBm, readback |
| 0203 / 0204 | Set/Get RF band | 0, 1, 3, 4, 5 |
| 0205 / 0206 | Set/Get frequency | Full band hoặc danh sách liên tiếp duy nhất |
| 020B / 020C | EPC baseband | Speed/Q/Session/Flag, cho phép TLV từng phần |
| 0209 / 020A | Repeat filter | Đơn vị 10 ms; RSSI threshold chỉ 0 |
| 0210 | Inventory / đọc bộ nhớ | EPC/PC; TID/User/Reserved; phase/tần số; FastID khi có TID |
| 0211 | Write | EPC/User, selector bắt buộc, tối đa 64 byte, gửi đúng một lần |
| 02FF | Stop | ACK; thêm 1201 reason1 nếu đang quét |

Speed tự dùng profile gần nhất: `0→205, 1→146, 2→141, 3→203, 4→123, 255→146`, theo adapter Web Serial 0.2.2. Q 0–15, Session 0–3, Flag 0=A, 1=B, 2=luân phiên A/B. Flag là trạng thái phiên của adapter. Không gọi preset ZK146 là tự tối ưu RF dù ứng dụng yêu cầu Speed255.

Quét giữ các lượt EPC trùng, chỉ lọc nếu ứng dụng đặt repeat filter. PC đọc thật rồi cache trong phiên để tăng tốc. Nếu thẻ mất trước khi đọc PC, bỏ báo cáo đó, ghi log và tiếp tục; không tạo PC giả. RSSI chưa có thang quy đổi đã xác minh thì không thêm trường giả.

Bản `.2` bật yêu cầu phase/tần số của ZK trong mỗi lượt inventory. Frame Nation trả PID08 tần số kHz và PID09 phase cuối, lượng tử hóa theo thang Nation; chỉ thêm khi ZK thực sự trả phần mở rộng. API Python giữ cả phase bắt đầu/kết thúc raw. Xem [PHASE_FASTID.md](PHASE_FASTID.md) về thang phase và giới hạn độ chính xác.

Khi web bật đọc TID tối đa 6 word với password 0, adapter yêu cầu **FastID**: EPC+TID trong cùng một bản ghi nếu tag hỗ trợ. Tag không trả FastID thì đọc TID có selector như trước, không dùng TID giả hoặc cache từ lượt khác. TID dài hơn 6 word/password khác 0 dùng đường đọc cũ. Không cần thêm lệnh riêng vào web. Config dịch vụ có `phase` và `fast_tid` (mặc định `true`); muốn tắt từng tính năng, sửa config khi đã Disconnect rồi restart dịch vụ.

Lệnh chưa hỗ trợ trả lỗi Nation dạng `1000` có code và MID yêu cầu. Mã trả về của thao tác được map theo ngữ cảnh; không có ánh xạ một-một toàn bộ lỗi Nation/ZK. Lỗi ghi không tự retry. Timeout hoặc lỗi framing làm kết nối lỗi; người dùng Disconnect/Connect để bắt đầu phiên mới, không phát lại lệnh cũ.

## Giới hạn QC

Đây là bản QC, chưa phải hỗ trợ toàn bộ SDK hoặc mọi firmware. ZK 4 anten bị từ chối rõ ràng. Không có GPIO output/buzzer, thay baud/địa chỉ reader, network config, RS485, lock/kill/block-write hoặc sửa lỗi UI web. Nation thật truyền nguyên byte; phase/FastID của bản `.2` bổ sung cho nhánh ZK, không tự sửa cấu hình Nation. Đường Nation của bản dịch vụ mới chưa kiểm thử trên phần cứng Nation trong lượt này. Chạy service/đường cài mới cần xem [VERIFICATION.md](VERIFICATION.md).

Web live vẫn có các lỗi call đã ghi nhận trong [audit](https://github.com/dangdangduong2k2/nation-zk-webserial/blob/main/docs/NEXTWAVES-LIVE-AUDIT.md), ví dụ Set Band gửi sai giá trị, P0/P1/P2 gửi payload một byte, Fast Switch gửi sai MID. EXE không suy đoán ý định từ frame sai; log cho biết frame nhận thực tế.

## Log / trở lại bản cũ

Log: `C:\ProgramData\NationPythonQC\bridge.log`, xoay tối đa khoảng 50 MB. Config: `settings.json`; giá trị trước cập nhật: `settings.previous.json`; trạng thái dịch vụ cũ: `before-python.json`. Log có EPC/TID/data frame, cân nhắc trước khi chia sẻ.

Để dừng/gỡ dịch vụ Python và khôi phục cấu hình khởi động bridge cũ:

```powershell
.\NationPythonQCSetup.exe --uninstall
```

Windows hỏi quyền admin. Giữ lại cặp COM, driver, source và log; không gỡ driver dùng chung. Nếu dùng lại C# cho ZK 4 anten, gỡ dịch vụ Python theo cách trên trước.

## Source / build

Lõi dịch vụ và chuyển đổi đều Python. `src/zk_nation/vendor/nrn.py` là SDK Nation gốc không sửa; `src/qc_uart.py` chuyển frame, `src/service_bridge.py` quản lý COM, `src/app.py` setup/service. Không nhúng Node hay JS trong EXE này.

```powershell
python -m pip install -r requirements.txt
python -m unittest test_qc test_phase_fastid -v
powershell -ExecutionPolicy Bypass -File build.ps1
```

Không cần chạy lệnh build trên máy người dùng. Yêu cầu Python 3.14 trên máy build hiện tại; runtime EXE Windows x64. Kiểm thử đóng gói không dùng reader:

```powershell
.\NationPythonQCSetup.exe --self-test
```

Kết quả tại `%LOCALAPPDATA%\NationPythonQC-selftest.json`. `hardware_check.py` **có đổi thông số và ghi User word0 lên thẻ thử**, cần `--test-tag-authorized`; không dùng như bài tự động trên thẻ sản xuất.
