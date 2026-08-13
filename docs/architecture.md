# Kiến trúc — CoC Mạnh Farmer

Tài liệu dành cho người đọc code. README mô tả bot dưới góc nhìn người dùng;
file này mô tả *bot chạy như thế nào*: một vòng lặp đọc màn hình rồi quyết
định chạm vào đâu.

Mọi đường dẫn đều tương đối với gốc repo. Việc đã chốt nhưng chưa làm nằm ở
[todo.md](todo.md).

---

## 1. Ý tưởng nền

Bot không đọc bộ nhớ game và không đụng vào gói tin mạng. Nó chỉ làm đúng
ba việc, lặp lại mãi:

1. **Chụp** một khung hình qua ADB (`screencap`).
2. **Đoán** đang ở màn hình nào (template matching + OCR).
3. **Chạm** vào vị trí phù hợp với màn hình đó.

Hệ quả của thiết kế này chi phối toàn bộ phần còn lại: mọi thứ đều là *phỏng
đoán theo xác suất*. Không có API nào xác nhận "đã vào trận" — chỉ có việc
một tấm ảnh mẫu khớp trên `0.80`. Vì vậy code có rất nhiều lớp dự phòng, và
gần như mọi hàm đều phải trả lời được câu hỏi "nếu không nhận ra thì sao?".

---

## 2. Luồng chạy tổng thể

```mermaid
flowchart TD
    Main["main.py"] --> MW["ui/main_window.py<br/>MainWindow"]
    MW -->|profile + mode| Engine["core/bot_engine.py<br/>BotEngine (QThread)"]
    Engine -->|screencap| ADB["core/adb_handler.py"]
    ADB --> Device["Thiết bị Android / emulator"]
    Engine -->|detect_state| SR["vision/screen_reader.py"]
    Engine --> SM["core/state_machine.py<br/>GameState"]
    Engine -->|handle(screenshot, state)| HV["logic/home_village.py"]
    Engine -->|handle(screenshot, state)| BB["logic/builder_base.py"]
    HV -->|nếu bật V2| V2["logic/smart_v2_logic.py<br/>→ v2_orchestrator.py"]
    V2 --> Rules["logic/rules/*"]
    Rules --> Skills["logic/skills/* + vision/skills/*"]
    Skills -->|tap / swipe / long_press| ADB
    HV -->|V2 tắt| L36["V36 trong home_village.py"]
    V2 -->|V2 bật nhưng hỏng| LV2["_legacy_run trong smart_v2_logic.py"]
    L36 --> ADB
    LV2 --> ADB
```

Hai luồng tấn công song song tồn tại trong repo và đây là điểm dễ nhầm nhất:

- **V36 (legacy)** — `HomeVillageLogic._execute_full_attack`. Đơn giản: tìm
  một "đường thả quân", dồn toàn bộ quân vào một cụm.
- **V2 / CSR (Config-Skills-Rules)** — hệ thống mới, bật riêng cho từng làng
  (`v2_enabled_hv`, `v2_enabled_bb`). Nhận diện vùng đỏ, lập kế hoạch, chọn
  chiến thuật.

V2 **luôn có đường lùi**: nếu không dựng được polygon vùng đỏ, không rule
nào chạy thành công, hoặc orchestrator ném exception, `V2Orchestrator.execute()`
trả `False` và `SmartV2Logic._legacy_run()` tự chạy bản deploy tối giản của
riêng nó (`_attack_smart` / `_attack_building` / `_attack_storage`, cơ chế
thả bằng tap thay vì swipe). Lưu ý: đây là *bản legacy trong
`smart_v2_logic.py`*, không phải hàm V36 trong `home_village.py` — hàm đó
chỉ chạy khi V2 tắt hẳn. Bot không bao giờ đứng im chỉ vì V2 hỏng.

---

## 3. Vòng tick của engine

`BotEngine` là một `QThread`. Vòng `run()` gọi `_tick()` rồi ngủ
`Settings().get("tick_interval")` giây (0.5–1.5s tùy preset). 10 lỗi liên
tiếp thì thread tự dừng.

Mỗi `_tick()` (`core/bot_engine.py:251`) làm theo thứ tự:

| # | Việc | Ghi chú |
|---|---|---|
| 1 | Bỏ qua nếu đang chạy sequence/macro | `_executing_sequence` |
| 2 | Kiểm tra game còn ở foreground | tối đa mỗi `game_check_interval` giây; tự `launch_app` nếu cần |
| 3 | `check_connection()` | mất kết nối → `DISCONNECTED`, ngủ 5s |
| 4 | `screencap()` | `None` → bỏ tick |
| 5 | `detect_state()` → `StateMachine.transition()` | phát signal `state_changed` |
| 6 | Phát hiện kẹt | ở `UNKNOWN`/`LOADING` quá `STUCK_TIMEOUT` (20s) → pause + `help_needed` |
| 7 | Điều phối theo state | `CONFIRMING` → action chain; còn lại → logic làng |

### Phát hiện kẹt và Interactive Assist

Khi kẹt, engine **tự pause** và bắn `help_needed(screenshot, reason)`.
`MainWindow` mở `ui/interactive_assist.py` để người dùng hoặc chạm tay vào
đúng chỗ, hoặc cắt một vùng ảnh và lưu thành template mới. Kết quả quay lại
qua `handle_assist_result()`, xóa cache template rồi `resume()`.

Đây là cơ chế chính để bot học màn hình mới mà không cần sửa code.

### Action chain (chống kẹt ở màn xác nhận)

`_handle_action_chain()` chạy khi state là `CONFIRMING`/`BB_CONFIRMING`.
Nó quét mọi nút xác nhận đang thấy rồi chạm theo thứ tự ưu tiên:

| Tier | Template | Vì sao |
|---|---|---|
| 0 | `normal_mode_btn` **hoặc** `ranked_mode_btn` | đúng tab người dùng chọn (`hv_match_mode`); tab còn lại bị loại thẳng |
| 1 | `attack_button2`, `confirm_button` | hai biến thể của cùng một nút "bắt đầu tìm trận" |
| 2 | `end_battle_confirm`, `reload_button` | popup phụ |

Sau khi chạm `attack_button2`, `_await_post_attack_confirm()` còn poll thêm
4 giây để bắt popup Confirm — luôn có ở Ranked, thỉnh thoảng có ở Normal.

---

## 4. State machine

`core/state_machine.py` định nghĩa 14 state (`GameState`) chia hai nhóm Home
Village và Builder Base. Bảng `_VALID_TRANSITIONS` **chỉ để log cảnh báo** —
nó không chặn chuyển state. Lý do: nhận diện màn hình vốn đã không chắc
chắn, chặn một chuyển tiếp "sai" sẽ làm bot kẹt cứng thay vì tự phục hồi.

Nhận diện state nằm ở `ScreenReader.detect_state()`
(`vision/screen_reader.py:569`), chạy theo thứ tự ưu tiên cố định — lỗi mất
kết nối trước, rồi Builder Base, rồi trận đấu, rồi Home. Vài lựa chọn ngưỡng
ở đây là kết quả sửa lỗi thật và được ghi chú tại chỗ:

- `surrender_button` được xét **trước** `lot_asseset`, vì nút surrender được
  vẽ trên thanh HUD chứ không nằm trên nền cỏ của làng địch → điểm khớp ổn
  định (1.00 trong trận, 0.64 khi đang do thám).
- Ngưỡng UI giữ ở mức bình thường thay vì hạ xuống 0.35–0.42; hạ thấp từng
  làm màn hình kết quả trận bị đọc nhầm thành `IN_BATTLE`.

---

## 5. Lớp thiết bị (`core/`)

### `adb_handler.py`
Toàn bộ giao tiếp với thiết bị. Nó là **module-level, không phải class** —
độ phân giải hiện hành là biến toàn cục (`_active_screen_width/height`),
nên `set_active_resolution()` ảnh hưởng tới mọi caller.

Điểm đáng chú ý:

- `ADB_EXE` được `_resolve_adb()` chọn: `2adb.exe` cạnh `main.py` trên
  Windows, biến môi trường `COC_ADB_PATH`, hoặc `adb` trên PATH (macOS).
- `screencap()` đọc thẳng bytes PNG về `numpy` array; `_repair_png_bytes()`
  xử lý trường hợp ADB làm hỏng ký tự xuống dòng.
- **Nhân hóa thao tác**: `_humanize_coord()` thêm jitter, `_human_delay()`
  thêm nghỉ ngẫu nhiên, và bộ nhớ toạ độ lần chạm trước (`_last_tap_x/y`)
  tránh chạm trùng khít một điểm hai lần. Dùng `tap_raw()` khi cần đúng
  toạ độ tuyệt đối.
- **Macro**: `start_recording()` bám `getevent` trên thiết bị,
  `_parse_getevent_to_taps()` dịch thành JSON, `play_recording()` phát lại.
  File nằm ở `recordings/`.

### `multi_touch.py`
Chạm nhiều điểm **cùng lúc** bằng cách ghi trực tiếp sự kiện MT protocol B
vào node input của cảm ứng. Cần root. Mặc định tắt, tự rơi về một ngón khi
không dùng được. Chi tiết đầy đủ (vì sao cần root, cách hiệu chỉnh
`swap_xy`/`invert_x`/`invert_y`) ở [multi-finger-deploy.md](multi-finger-deploy.md).

Hiện chỉ Ring Sweep dùng tính năng này.

### `settings.py`
Singleton thread-safe, đọc/ghi `profiles/settings.json` (file này bị
`.gitignore` — nó là cấu hình máy, không phải cấu hình dự án).

Có 5 preset hiệu năng. `smart_default` không phải bảng cứng: nó gọi
`detect_smart_profile()` để đọc số nhân CPU và độ phân giải thiết bị thật
rồi tự chọn `tick_interval`, `template_scales`, ngưỡng nhận diện.

> Lưu ý khi sửa file này: `_DEFAULTS` đang khai báo trùng
> `vision_building_threshold` và `vision_bb_card_threshold` hai lần
> (`core/settings.py:112-117`). Giá trị sau (0.40 / 0.42) ghi đè giá trị
> trước (0.34 / 0.34), nên hai dòng đầu là code chết.

---

## 6. Lớp thị giác (`vision/`)

### `screen_reader.py` — cỗ máy chính
Cache template trong `_template_cache` (BGR + alpha mask + category). Bốn
họ ngưỡng riêng biệt vì bốn loại đối tượng khác hẳn nhau:

| Hàm | Dùng cho | Ngưỡng mặc định |
|---|---|---|
| `_match_ui` | nút, popup — hình tĩnh, sắc nét | `0.80` |
| `_match_troop` | thẻ quân dưới HUD — bị scale theo màn | `0.30` |
| `_match_building` | công trình trên nền cỏ đủ kiểu | `0.34` |
| `_match_bb_card` | thẻ quân Builder Base | `0.34` |

`get_ui_cutoff()` là khái niệm quan trọng xuyên suốt: **đường ranh giới giữa
chiến trường và thanh HUD quân**. Mọi điểm thả quân đều phải nằm phía trên
đường này; mọi thẻ quân đều nằm phía dưới. Chiều cao thanh HUD được scale
theo màn hình hiện tại chứ không hardcode.

### `ocr_reader.py`
EasyOCR. Ba việc:
- `read_loot()` — cắt góc trên trái thành ba dải Gold / Elixir / Dark, phóng
  to CUBIC + ngưỡng Otsu rồi lọc lấy chữ số.
- `read_timer_v2()` — đọc đồng hồ trận (`2m 45s`, `02:45`, `45s`).
- `find_text_in_region()` — tìm nút theo **từ khóa** thay vì ảnh mẫu. Đây là
  lớp dự phòng cho nút Surrender khi template trượt, và nó không phụ thuộc
  ngôn ngữ giao diện game.

OCR đắt, nên có `ocr_min_interval` chặn tần suất, và có công tắc
`skip_loot_ocr` / `skip_timer_ocr` để tắt hẳn.

### `template_manager.py` + `assets/templates/manifest.json`
Không có đường dẫn ảnh nào hardcode trong code. `manifest.json` là nguồn sự
thật duy nhất, ánh xạ `key → {category, label, file, width, height}`. Thêm
template mới = thêm một entry (qua UI Asset Manager hoặc
`save_template()` / `import_template_from_file()` / `register_asset()`),
không cần sửa Python.

`get_sequence_readiness()` dùng manifest để cảnh báo trước khi chạy nếu
chuỗi thao tác tham chiếu tới ảnh chưa có — nhưng chỉ **cảnh báo**, bước
thiếu sẽ bị bỏ qua lúc chạy chứ không chặn bot khởi động.

### `vision/skills/`
Các đơn vị thị giác nhỏ, thuần hàm, dùng bởi hệ V2:

| Skill | Trả về |
|---|---|
| `red_zone_polygon.py` | polygon vùng cấm thả quân (HSV + morphology) và `centroid()` |
| `isometric_grid.py` | chiếu pixel phẳng ↔ lưới isometric của game |
| `safe_corridor.py` | các hành lang an toàn giữa biên đỏ và mép màn |
| `target_locator.py` | vị trí công trình mục tiêu (TH, kho tài nguyên) |
| `obstacle_detector.py` | vật cản che tầm nhìn |
| `corner_selector.py` | chọn góc/cạnh tiếp cận tốt nhất |
| `card_state.py` | thẻ quân còn quân hay đã rỗng, xét qua màu (dùng cho sweep-up) |

---

## 7. Hệ tấn công V2 (CSR)

### Vòng đời một trận

```
SmartV2Logic.execute(screenshot)
  └─ V2Orchestrator.execute(screenshot, profile, mode_key, engine)
       1. reload config nếu file config/*.json đổi mtime
       2. đợi v2_decoration_wait giây rồi chụp lại (chờ hiệu ứng vào trận tan)
       3. ui_cutoff = ScreenReader.get_ui_cutoff()
       4. polygon  = skills.red_zone.detect(...)      ← thất bại ⇒ trả False
       5. rule     = _select_rule(...)                ← không khớp ⇒ trả False
       6. dựng AttackContext rồi rule.execute(ctx)
       7. rule trả False ⇒ thử SmartDefaultRule ⇒ vẫn False ⇒ trả False

bất kỳ lần trả False nào ⇒ SmartV2Logic._legacy_run() chạy bản deploy tối giản
```

### Config — `config/`

Ba file JSON, **hot reload theo mtime**: sửa file trong lúc bot đang chạy là
tick sau đã dùng giá trị mới (hoặc bấm *Reload Config* trong panel V2).

| File | Nội dung |
|---|---|
| `v2_attack_rules.json` | tham số toàn cục: `stand_off_px`, `polygon` (HSV/morphology), `isometric`, `deploy_pattern`, `funnel`, `spell_path_fractions`, `multi_touch`, và block riêng cho từng rule (`perimeter_sweep`, `ring_sweep`…) |
| `v2_troop_profiles.json` | mỗi quân: `kind` (`ground`/`air`), `style` (`scout_pairs`/`funnel`/`fan_wide`/`cluster`), `deployment_spacing_ms` |
| `v2_spell_profiles.json` | cách đặt từng loại spell so với hướng tiến quân |

`kind` và `style` không chỉ là metadata — chúng là **đầu vào để chọn rule**
(xem bảng dưới).

### Skills — `logic/skills/`

Phần "làm thế nào" đã tách khỏi phần "làm gì". Rule không tự tính toạ độ:

| Skill | Vai trò |
|---|---|
| `human_touch.py` | mọi thao tác chạm/giữ của V2 đi qua đây: jitter, nghỉ, `pre_select_settle`, `post_deploy_settle`, `long_press` |
| `fan_planner.py` | rải quân đều theo hình quạt |
| `funnel_planner.py` | tính hai điểm funnel hai bên |
| `perimeter_planner.py` | đường vuốt quanh 4 hành lang mép màn |
| `ring_sweep_planner.py` | vòng bám polygon; `pick_drops()`, `one_point_per_side()`, `sides_covered()` |
| `hero_planner.py` | vị trí thả hero và thời điểm bấm kỹ năng |
| `spell_planner.py` | vị trí spell theo đường tiến của quân |

### Rules — `logic/rules/`

Tất cả kế thừa `AttackRule`, hợp đồng chỉ hai hàm: `matches()` và
`execute() → bool`. **`execute` trả `False` nghĩa là "tôi không thả được gì,
hãy chuyển tiếp"** — đây là điều kiện để chuỗi dự phòng hoạt động.

| Rule | priority | Chọn tự động khi | Cách đánh |
|---|---|---|---|
| `resource_raid` | 10 | có quân `style: scout_pairs` **và** có `target_key` | thả cạnh từng kho tài nguyên |
| `th_snipe` | 20 | `v2_mode = building` + có target | thả tại điểm an toàn gần Town Hall |
| `air_attack` | 30 | số quân `air` ≥ số quân `ground` | rải quân bay theo quạt trên hành lang |
| `ground_funnel` | 40 | có quân bộ **và** có quân `style: funnel` | tạo hai cánh funnel rồi đẩy giữa |
| `perimeter_sweep` | 80 | chỉ khi chọn tay | vuốt quanh 4 mép màn; **cần đủ 4 hành lang**, thiếu là bỏ |
| `ring_sweep` | 85 | chỉ khi chọn tay (`matches()` luôn `True`) | giữ `ring_sweep.hold_points` điểm quanh base (mặc định 4 = mỗi cạnh 1), dựng từ polygon nên chỉ cần 2 cạnh có chỗ |
| `smart_default` | 90 | mọi trường hợp còn lại | chọn hành lang rộng nhất, thả hỗn hợp |

`priority` chỉ dùng để sắp thứ tự danh sách rule; việc chọn rule thực tế do
`_select_rule()` quyết định — ưu tiên override thủ công (`v2_rule_hv` /
`v2_rule_bb` khác `"auto"`), rồi `v2_mode`, rồi đội hình quân.

`RingSweepRule` kế thừa `AirAttackRule` để dùng lại phần hero/spell/chờ giao
tranh, chỉ thay cách rải quân.

---

### Bỏ base thay vì lùi về V36

Mặc định, V2 đọc không ra base thì `_legacy_run()` vẫn đánh — dồn cả đội
quân vào một cụm. Bật `v2_skip_on_fallback` thì thay vào đó bot **bỏ base
đó**: `SmartV2Logic.execute()` trả `False`, `HomeVillageLogic._abandon_base()`
gỡ cờ tấn công rồi thoát ra.

Lối thoát tuỳ trạng thái, và hai lối có giá khác hẳn nhau:

| đang ở | cách ra | giá |
|---|---|---|
| `OPPONENT_FOUND` (màn do thám) | bấm Next | phí tìm trận |
| đã vào trận | `_end_battle()` đầu hàng | mất lượt đánh + cúp |

**Trần `fallback.max_consecutive_skips`** (mặc định 3) là nửa quan trọng của
tính năng. V2 bỏ cuộc vì lý do thuộc về *bot* chứ không thuộc về base —
ngưỡng polygon lệch máy thì base nào cũng hỏng như nhau. Không có trần thì
bot bỏ hết base này tới base khác, trả phí tìm trận mỗi lần và không bao giờ
đánh. Quá số lần đó thì nó chấp nhận đánh kiểu cũ và đặt lại bộ đếm.

Trận bị bỏ được `record_attack_cancelled()` chuyển từ cột "Trận" sang cột
"Bỏ qua" — bộ đếm được bật lúc bot quyết định vào base, tức trước khi planner
có tiếng nói.

### Thả nốt quân thừa (sweep-up)

Rule thả theo *kế hoạch* chứ không theo *kết quả*: Ring Sweep giữ mỗi cạnh
một cửa sổ thời gian cố định, cửa sổ ngắn hơn quân trong thẻ là còn thừa; thẻ
nào không nhận diện được thì bị bỏ qua luôn. Quân nằm lại trên thẻ là tài
nguyên đã tiêu mà không đánh đổi được gì.

Khi bật `sweep_up_enabled` (⚙ Cài đặt), sau khi rule chạy xong
`V2Orchestrator._sweep_up()` chụp lại màn hình, duyệt từng quân đã chọn:

1. `skills.target.find_one()` tìm thẻ. Không thấy thẻ = coi như hết quân.
2. `skills.card.has_troops_left()` xét màu thẻ. Thẻ hết quân bị CoC làm **xám
   và tối** — đúng tín hiệu `_is_hero_dead()` dùng cho hero. Thẻ bị coi là
   rỗng chỉ khi **cả hai** mức bão hoà màu và độ sáng cùng tụt: thẻ đang được
   chọn thì sáng hơn, thẻ nằm trong bóng thì tối hơn, mỗi phép đo đơn lẻ sẽ
   nhầm ở một trong hai trường hợp đó.
3. Còn quân thì bấm thẻ rồi giữ `hold_ms` trên một điểm của vòng ring — vòng
   dựng từ `ctx.polygon` nên hợp lệ với mọi rule, không riêng Ring Sweep.

Ba ràng buộc:

- **Trần `max_rounds`** (mặc định 2). Một thẻ bị đọc nhầm thành "còn quân"
  mãi sẽ chỉ tốn 2 lượt bấm chứ không giữ bot trong trận tới hết giờ.
- **Đóng lại mốc đếm ngược.** Rule đã stamp `_post_deploy_time` khi *nó* xong;
  `_restamp_post_deploy()` đẩy mốc về hiện tại sau mỗi vòng quét, nếu không
  thời gian quét bị trừ thẳng vào `deploy_timer_seconds`.
- **Crop lỗi thì coi như còn quân.** Đoán "hết" mà sai là bỏ rơi cả đội quân;
  đoán "còn" mà sai chỉ tốn một cú bấm game bỏ qua.

Ngưỡng màu nằm ở `config/v2_attack_rules.json` mục `sweep_up`, và **phải đo
theo máy** — mỗi lần xét thẻ đều in ra số đo được. Cách đo ở
[todo.md](todo.md).

## 8. Vào trận nhanh (`logic/fast_entry.py`)

Ba nút Attack → Find a Match → Attack! luôn nằm cùng chỗ và luôn nối tiếp
nhau, nhưng đường bình thường trả giá vision đầy đủ cho từng nút: đo trên
thiết bị đích là `screencap` 999ms + `detect_state` 1847ms + quét xác nhận
595ms + 1s chờ ổn định ≈ 4.4s mỗi nút, ~13s để bắt đầu một trận.

Fast entry chạm mù ba toạ độ đã đo sẵn. Đánh đổi là **không xác minh gì**:
một quảng cáo hay popup "quân chưa đủ" sẽ nuốt mất một cú chạm. Tick sau
engine đọc lại màn hình và phục hồi, nên cái giá là một lượt hỏng chứ không
phải cả phiên — nhưng vì thế nó mặc định tắt và **tự từ chối chạy nếu độ
phân giải khác `1350x1080`** đã hiệu chỉnh.

---

## 9. Profile và Settings — hai thứ khác nhau

Đây là điểm hay nhầm khi đọc code lần đầu:

| | Profile | Settings |
|---|---|---|
| Là gì | *đánh như thế nào* | *chạy trên máy này ra sao* |
| File | `profiles/*.json` (`default_profile.json`, `manh.json`…) | `profiles/settings.json` (bị gitignore) |
| Truy cập | truyền tay: `MainWindow` → `BotEngine` → logic → `AttackContext.profile` | singleton `Settings()`, gọi ở bất cứ đâu |
| Ví dụ khóa | `min_gold`, `selected_troops`, `retreat_time`, `hv_match_mode`, `deploy_timer_seconds`, `hv_random_skip_*` | `tick_interval`, `template_scales`, `v2_enabled_hv`, `v2_rule_hv`, `hv_fast_entry`, `multi_touch_enabled` |

Vì `Settings` là singleton nên bật/tắt V2 hay đổi rule có hiệu lực ngay,
không cần khởi động lại. Profile thì phải đi qua `update_profile()` để lan
xuống các lớp logic.

Đổi profile khi đang chạy: `BotEngine.update_profile()` gọi tiếp
`HomeVillageLogic.update_profile()` và `BuilderBaseLogic.update_profile()`,
hai hàm này lại gọi `SmartV2Logic.update_profile()`.

---

## 10. Bỏ qua làng ngẫu nhiên

Nhận mọi làng đạt ngưỡng là thói quen không người chơi nào có. Khi bật
`hv_random_skip_enabled`, mỗi làng ở màn do thám có `hv_random_skip_chance`
phần trăm khởi động một **đợt bỏ qua** dài `hv_random_skip_min`..`_max` làng
(mặc định 1–2). Đợt được đếm ngược bằng `_random_skips_left`, không gieo lại
xúc xắc giữa chừng — gieo lại mỗi làng sẽ cho phân bố đều đặn, khác hẳn kiểu
"thỉnh thoảng bỏ vài làng liền" mà tính năng này mô phỏng.

Kiểm tra nằm ở đầu `_handle_opponent_found()`, **trước** OCR loot: làng đã
quyết bỏ thì không đáng để đọc. Mỗi lần bỏ vẫn tính vào bộ đếm `record_skip()`.

Chỉ có tác dụng ở trận Thường. Ranked vào thẳng `IN_BATTLE`, không đi qua màn
do thám nên không có gì để bỏ qua.

**Không cộng dồn với kiểu bỏ do V2 bỏ cuộc.** Base ngay sau một lần
`_abandon_base()` luôn được đánh, bất kể xúc xắc, và đợt bỏ ngẫu nhiên còn nợ
cũng bị xoá (`_forced_skip_last`). Hai kiểu bỏ không liên quan gì nhau — một
là nhịp cố ý, một là planner hỏng — để chúng chồng lên nhau thì "bỏ một base"
biến thành "bỏ ba base", mỗi base một lần phí tìm trận. Chuỗi V2 bỏ cuộc liên
tiếp thì vẫn được bỏ tiếp, chỉ là xúc xắc không nối dài thêm.

## 11. Rút lui và kết thúc trận

`HomeVillageLogic` có bốn điều kiện kết thúc trận sớm, kiểm tra sau khi
`_battle_phase_done`:

1. **Deploy timer** (`deploy_timer_enabled`) — đếm từ lúc thả xong quân
   (`_post_deploy_time`), hết `deploy_timer_seconds` là kết thúc. Đây là
   cách đơn giản và đáng tin nhất, mặc định bật.
2. **Loot còn lại thấp** (`auto_retreat_enabled`) — OCR lại thanh loot, so
   với `retreat_gold`/`retreat_elixir`/`retreat_dark_elixir`.
3. **Đồng hồ trận** (`retreat_time > 0`) — OCR đồng hồ đếm ngược.
4. **Hero chết** (`retreat_heroes_dead`) — cảm biến ở `_is_hero_dead()`:
   hero **chết** khi *không còn thanh máu phía trên thẻ* **và** mặt thẻ tối
   (saturation < 60, brightness < 140). Điều kiện kép này để phân biệt với
   hero *đã dùng kỹ năng* — thẻ cũng xám nhưng vẫn còn thanh máu. Chỉ rút
   khi Grand Warden chết kèm ít nhất một hero khác, và chỉ sau 20s kể từ khi
   thả xong.

`_end_battle()` tìm nút Surrender qua bốn lớp, lần lượt:
`end_battle_button` → `surrender_button` → OCR từ khóa
(`"end battle"`, `"surrender"`, `"exit"`, `"yield"`) → **chạm cứng** vào
`(0.08w, 0.85h)`. Lớp cuối là bảo hiểm: thà chạm nhầm còn hơn kẹt vĩnh viễn
trong trận.

---

## 12. Giao diện (`ui/`)

`MainWindow` giữ 6 tab và dây nối signal của `BotEngine`:

| Tab | File | Việc |
|---|---|---|
| 🏠 Làng chính | `home_village_tab.py` | chọn quân/hero/spell (có thứ tự), ngưỡng loot, rút lui |
| 🔨 Làng thợ | `builder_base_tab.py` | tương tự cho Builder Base |
| 📋 Ảnh mẫu | `asset_manager_tab.py` | xem/thêm/xóa template trong manifest, thử khớp |
| 🔗 Chuỗi thao tác | `sequence_builder_tab.py` | dựng chuỗi chạm theo template (`hv_entry_sequence`) |
| 🎯 Ghi thao tác | `training_mode_tab.py` | ghi/phát macro từ `getevent` |
| ⚙ Cài đặt | `settings_tab.py` | preset hiệu năng, ngưỡng vision, fast entry, multi-touch, gói game |

Panel V2 (`smart_v2_panel.py`) nhúng trong tab làng, cho chọn rule/mode/target
và bấm Reload Config.

Hai chi tiết dễ bỏ sót:

- `wheel_guard.py` cài event filter toàn ứng dụng để **con lăn chuột cuộn
  trang thay vì sửa giá trị** của combobox/spinbox mà nó lướt qua — nếu
  không, cuộn trang cài đặt sẽ âm thầm đổi tham số.
- `console_widget.py` dịch mã màu ANSI trong log thành màu Qt, nên các hằng
  `C_GOLD`, `C_RED`… trong `home_village.py` hiển thị đúng màu trên UI.

Toàn bộ giao diện là tiếng Việt (commit `195e871`); log và comment trong code
vẫn là tiếng Anh.

---

## 13. Chạy, kiểm thử, đóng gói

```powershell
# chạy từ source
python main.py

# hoặc double-click
run_bot.bat            # Windows — tự tạo venv, cài deps rồi chạy
./run_bot.command      # macOS

# kiểm thử
python -m unittest discover -s tests -v

# đóng gói
build_exe.bat          # gọi pyinstaller coc_bot.spec
```

Về `coc_bot.spec`: build ở chế độ `--onedir`. `2adb.exe`, `assets/`,
`config/`, `profiles/` **không** được nhúng vào exe — code mở chúng theo
đường dẫn tương đối, nên `build_exe.bat` copy chúng cạnh exe sau khi build.
`collect_all` được gọi cho easyocr/torch/cv2 vì các gói này mang theo model
và DLL riêng mà PyInstaller không tự thấy.

Trong `main.py`, `import torch` phải đứng **trước** PyQt5 — PyTorch 2.9 trên
Windows cần nạp DLL của nó trước khi Qt khởi tạo. `tests/test_main_import_order.py`
canh đúng điều này.

### Test có gì

16 file trong `tests/`, chạy bằng `unittest`, không cần thiết bị thật — mọi
lớp ADB đều được mock. Chúng bám vào các phần *tính toán được*: planner
(`test_perimeter_planner`, `test_ring_sweep`, `test_spell_planner`), cử chỉ
(`test_deploy_gesture`, `test_multi_touch`, `test_human_touch`), scale theo
độ phân giải (`test_narrow_resolution`, `test_ui_cutoff_scaling`,
`test_ui_template_scale`), và các đường dự phòng
(`test_legacy_fallback_deployment`, `test_smart_default_order`).

Phần nhận diện màn hình không có test tự động — nó phụ thuộc ảnh thật.

---

## 14. Muốn sửa gì thì vào đâu

| Muốn | Sửa ở |
|---|---|
| Thêm chiến thuật mới | tạo file trong `logic/rules/`, export ở `logic/rules/__init__.py`, thêm vào `_build_rules()` trong `v2_orchestrator.py` |
| Chỉnh hành vi chiến thuật có sẵn | `config/v2_attack_rules.json` — hot reload, không cần sửa code |
| Thêm quân mới | thêm ảnh vào manifest (tab Ảnh mẫu) + entry trong `v2_troop_profiles.json` |
| Bot không nhận ra màn hình | thêm/thay template qua tab Ảnh mẫu, hoặc nới ngưỡng trong `detect_state()` |
| Đổi cách chạm cho "người" hơn | `logic/skills/human_touch.py` (V2) và `_humanize_coord()` trong `adb_handler.py` (mọi nơi) |
| Thêm bước vào chuỗi vào trận | tab Chuỗi thao tác → ghi vào `hv_entry_sequence` của profile |
| Thêm tham số cấu hình máy | `_DEFAULTS` trong `core/settings.py` + widget trong `ui/settings_tab.py` |

---

## 15. Những chỗ cần biết trước khi đụng vào

- **Không có bảo đảm chống phát hiện.** Jitter và delay ngẫu nhiên chỉ làm
  thao tác bớt máy móc, không phải cơ chế né phát hiện.
- **Toạ độ trong `fast_entry.py` gắn với đúng một màn hình.** Đừng "sửa cho
  chạy được trên máy tôi" bằng cách nới điều kiện kiểm tra độ phân giải —
  chạm mù sai chỗ có thể trúng nút Surrender.
- **Giá trị `multi_touch` đang commit trong config là mặc định chưa hiệu
  chỉnh** (cả ba boolean `false`), chưa ai xác nhận trên thiết bị nào. Hiệu
  chỉnh theo [multi-finger-deploy.md](multi-finger-deploy.md) trước khi bật.
- **`profiles/settings.json` bị gitignore** — đừng dựa vào nó để tái hiện
  lỗi của người khác; hỏi họ dán nội dung file.
- **Rule trả `False` là hợp đồng, không phải lỗi.** Rule nào thoát sớm mà
  quên trả `False` sẽ làm chuỗi dự phòng đứt và bot đứng yên trong trận.
