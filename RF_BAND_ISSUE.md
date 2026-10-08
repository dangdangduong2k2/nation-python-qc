# Set RF band trên web Nextwaves — 08/10/2026

## Kết quả kiểm tra

Log dịch vụ lúc 17:40–17:41 liên tục nhận frame:

```text
APP_TO_NATION 5A00010203000100A9CE
```

MID 0203, payload một byte `00`: yêu cầu đặt band 0. ZK ACK thành công; Get 0204 trả payload `00`, đúng yêu cầu thực nhận. Bridge không thể suy ra lựa chọn khác trên giao diện từ byte 00 này.

Trong lượt đối chiếu web trước đó, chọn band 1/3/4/5 nhưng log web ghi `Band [object Object]`; frame thực vẫn thành band 0. Kiểm tra raw Nation frame đúng qua COM52 đã Set/Get thành công band 0/1/3/4/5; xem `service-results.json`. Điều đó chưa có nghĩa nút Set Band trên web live đã được sửa.

## Chỗ cần sửa ở ứng dụng của Bình

Lấy **mã band số** từ lựa chọn UI trước khi gọi SDK/build frame, không truyền cả object lựa chọn vào vị trí band code. Ví dụ dưới đây là cách dựng frame theo API `NRNUtils` đã khai báo trong SDK; tên biến lựa chọn và cách gửi frame cần dùng source thực tế của ứng dụng:

```javascript
import { NRNUtils } from './duong-dan-sdk-hien-tai.js';

function buildSetRFBandFrame(bandCode) {
  // Caller phải đưa mã số, ví dụ 3 cho FCC; object bị từ chối.
  if (!Number.isInteger(bandCode) || bandCode < 0 || bandCode > 255) {
    throw new TypeError('RF band phải là mã số nguyên');
  }
  return NRNUtils.buildFrame(0x0203, Uint8Array.of(bandCode));
}
```

Sau gửi: kiểm tra ACK 0203 có result 0, gọi Get 0204 và đối chiếu byte trả về với mã đã chọn; chỉ báo thành công khi khớp. Chưa hỗ trợ ZK band 2/6/7/8 thì hiển thị lỗi thực, không tự đổi sang band 0.

Đây là hướng dẫn sửa caller; chưa chỉnh hoặc deploy website Nextwaves. Repo SDK đã cung cấp không chứa source giao diện đang triển khai. Cần file xử lý nút Set Band/source ứng dụng đó để làm bản vá chính xác.
