# Việc còn tồn

Ghi lại việc đã chốt nhưng chưa xong, kèm đủ ngữ cảnh để lần sau bắt tay vào
là làm được ngay chứ không phải điều tra lại.

---

## Tìm ra vì sao polygon vùng đỏ đọc hỏng

Đã thấy trên máy thật (09:48 ngày 2026-08-07):

```
RedZone polygon: detection FAILED on both HSV and inversion passes.
V2 polygon detection FAILED (HSV + inversion both rejected) — falling back to legacy V36.
```

Cả hai lượt HSV và lượt đảo màu đều bị loại. Hiện chỉ có cách chữa triệu
chứng (`v2_skip_on_fallback` — bỏ base đó, tìm trận khác), chưa biết vì sao.

Muốn chẩn đoán thì bật ảnh gỡ lỗi trong `config/v2_attack_rules.json`:

```json
"polygon": { "debug_dump": "assets/logs/redzone" }
```

Hot reload, không cần khởi động lại. Mỗi lần một lượt bị loại, bot lưu một
ảnh `redzone_<mode>_<ts>.png`: nền là màn hình thật, tô đỏ phần mask HSV bắt
được, viền xanh dương là các contour tìm thấy, viền xanh lá là hull lớn nhất.

Nhìn ảnh là phân biệt được hai nguyên nhân hoàn toàn khác nhau:

- **Mask trống** → `hsv_s_min: 150` / `hsv_v_min: 110` quá gắt cho màu viền
  đỏ trên máy này.
- **Mask đẹp nhưng vẫn bị loại** → vướng bộ lọc kích thước:
  `min_polygon_width_px: 500`, `min_polygon_area_ratio: 0.1`,
  `max_polygon_area_ratio: 0.9`, `min_polygon_y_px: 60`.

## Đo ngưỡng màu cho "thả nốt quân thừa"

Tính năng quét quân thừa sau khi V2 thả xong **đã làm** (xem
[architecture.md](architecture.md), mục "Thả nốt quân thừa"). Phần chưa xong
là ngưỡng màu: mã đang dùng đúng hai con số của cảm biến hero chết —
`empty_saturation: 60`, `empty_brightness: 140` — và **chưa ai đo trên thẻ
quân thật**. Thẻ quân không nhất thiết xám đi giống thẻ hero.

Đo mất một trận:

1. Bật **⚙ Cài đặt → "Thả nốt quân thừa"**, chạy một trận với quân thừa cố ý
   (giữ `hold_ms` ngắn trong `ring_sweep` để chắc chắn còn quân).
2. Mở Console, tìm các dòng:

   ```
   card 'baba' at (300,1000): sat=182 (<60?) val=214 (<140?) → still has troops
   card 'baba' at (300,1000): sat=24 (<60?) val=88 (<140?) → EMPTY
   ```

3. Hai cụm số đó phải tách nhau rõ. Đặt ngưỡng vào **giữa** hai cụm, sửa
   `sweep_up.empty_saturation` / `empty_brightness` trong
   `config/v2_attack_rules.json`. File hot reload, không cần khởi động lại.

Nếu hai cụm **không** tách nhau — thẻ hết quân vẫn sáng và rực màu như thẻ
đầy — thì hướng màu không dùng được cho thẻ quân, phải chuyển sang đọc con số
`x36` trên thẻ. Khi đó `vision/ocr_reader.py` cần thêm một hàm đọc số trên
thẻ; chữ số ở đây nhỏ hơn thanh loot nhiều nên nhiều khả năng phải phóng to
rồi mới OCR được.

Cho tới khi đo xong, để công tắc **tắt**: ngưỡng sai theo hướng "lúc nào cũng
còn quân" chỉ tốn 2 lượt bấm thừa (đã chặn bằng `max_rounds`), nhưng theo
hướng ngược lại thì tính năng im lặng không làm gì cả.

---

## Sweep-up không chạy trên đường lùi V36

`_sweep_up()` nằm trong `V2Orchestrator.execute()`, sau khi một rule chạy
thành công. Khi V2 bỏ cuộc và `SmartV2Logic._legacy_run()` đánh thay, quân
thừa không được quét — đúng lúc dễ thừa nhất, vì bộ thả cũ dồn một cụm.

Không dùng lại được nguyên xi: điểm thả của sweep-up dựng từ `ctx.polygon`,
mà đường lùi chạy chính vì polygon hỏng. Muốn làm thì phải lấy điểm thả từ
cụm mà `_attack_smart()` đã chọn.

Việc này chỉ đáng làm nếu vẫn còn dùng đường lùi. Bật `v2_skip_on_fallback`
là hầu như không đi vào đó nữa, nên hãy xử lý mục polygon ở trên trước.
