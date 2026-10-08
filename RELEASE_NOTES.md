# 0.2.2-pyqc.2 — Python RS232 bridge QC

Phát hành thử nghiệm ngày 08/10/2026. Source lấy từ thư mục `NationPythonQC` được người dùng cung cấp; EXE phát hành giữ nguyên file `NationPythonQCSetup.exe` đã cài/QC.

## Dùng cho người vận hành

Tải `NationPythonQCSetup.exe` trong [Release](https://github.com/dangdangduong2k2/nation-python-qc/releases/tag/v0.2.2-pyqc.2). Disconnect các ứng dụng đang giữ COM, mở EXE, chọn cổng reader thật và bấm Setup. Chấp nhận UAC; đợi Complete. Web giữ model NATION và chọn cổng tương thích được bộ cài báo. Máy QC đang dùng COM52 → Python → ZK COM50; số COM có thể khác trên máy khác.

## Nội dung

- Dịch vụ Windows chạy ngầm, dịch frame Nation ↔ ZK Ex10 1 anten.
- Inventory EPC/PC, đọc TID/User/Reserved, ghi EPC/User có selector, Stop.
- Set/Get power, band được map, kênh liên tiếp, Speed/Q/Session/Flag.
- Phase/tần số theo record ZK và native FastID khi thẻ hỗ trợ; fallback đọc TID có selector.
- Cài đè bản Python, thanh tiến trình và giữ bản C# cũ để khôi phục.

## Bằng chứng và giới hạn

21 bài kiểm thử tự động đã qua; EXE frozen self-test, direct COM50 và đường dịch vụ COM52 đã có kết quả PASS. Xem [VERIFICATION.md](VERIFICATION.md) và các file JSON bằng chứng. Lần đăng GitHub chỉ kiểm tra lại source/package, không chạy lại phần cứng.

Người dùng đang phản ánh tốc độ đọc chậm trong một số lượt QC; nguyên nhân chưa được xác định/sửa trong EXE này. Số count ngắn trong báo cáo không phải cam kết hiệu năng.

ZK 4 anten chưa được hỗ trợ trong bản Python QC này. Chưa nghiệm thu cài mới trên Windows sạch, reboot và chạy dài giờ. Không hỗ trợ toàn bộ SDK Nation. Nút Set RF band trên web hiện có lỗi truyền tham số với cả Nation/ZK; xem [RF_BAND_ISSUE.md](RF_BAND_ISSUE.md). Bản EXE không sửa website đó.

EXE SHA256:

```text
5c250758a090aa80b292047724c93289c04b432cc7e2f2c07ac3b1aac6df1bd3
```

Giấy phép/thành phần đi kèm: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), `assets/licenses`, GPL/source com0com tại `assets/`.
