# CLAUDE.md

Hướng dẫn cho Claude Code khi làm việc trong repository này.

## Project

Bot tự động farm **Clash of Clans** trên Windows. Bot chụp màn hình thiết bị Android
qua ADB (`2adb.exe` trong project root), nhận diện UI bằng OpenCV template matching +
EasyOCR, rồi mô phỏng tap/swipe để tìm trận và triển khai quân. Giao diện PyQt5.

Không sửa APK, không đọc memory, không can thiệp network — thuần UI automation.

## Commands

```powershell
# Chạy ứng dụng (luôn dùng python trong venv)
.\venv\Scripts\python.exe main.py

# Chạy toàn bộ test
.\venv\Scripts\python.exe -m unittest discover -s tests -v

# Chạy một test file
.\venv\Scripts\python.exe -m unittest tests.test_session_tally -v

# Kiểm tra thiết bị ADB
.\2adb.exe devices
```

Không có linter/formatter/CI được cấu hình. Python 3.10/3.11, deps trong `requirements.txt`.

## Kiến trúc

Luồng chính: **UI → BotEngine (QThread) → ScreenReader/OCR → Village Logic → V2 Orchestrator → ADB**

```
main.py                  Khởi tạo logger → QSS dark theme → MainWindow
core/bot_engine.py       QThread; vòng lặp _tick(): screencap → detect_state →
                         dispatch cho home_logic hoặc bb_logic. Kèm stuck detection,
                         game-presence check, action chain, macro/sequence executor,
                         session tally (record_attack / record_skip).
core/state_machine.py    GameState enum + bảng transition hợp lệ (chỉ log cảnh báo,
                         không chặn). HV: HOME→CONFIRMING→SEARCHING→OPPONENT_FOUND→
                         IN_BATTLE→BATTLE_ENDED. BB có nhánh riêng BB_*.
core/adb_handler.py      screencap/tap/swipe qua subprocess 2adb.exe + humanization
                         (jitter, hesitation, chống tap trùng tọa độ), macro record/replay.
                         `tap_batch()` nối nhiều `input tap` trong MỘT lệnh shell để bỏ
                         round-trip ADB (~38ms/lần); `input` trên máy (~120ms) là sàn.
core/adb_gestures.py     pinch zoom-out, pan camera (multi-finger qua nhiều swipe song song).
core/settings.py         Singleton `Settings()` → `profiles/settings.json` (gitignored).
                         PRESETS: ultra/high/medium/low/smart_default; smart_default
                         tự dò CPU count + độ phân giải/tablet để chỉnh scale & threshold.
```

### Vision

`vision/screen_reader.py` là engine chính: `detect_state(screenshot, mode)` quyết định GameState
(truyền `mode="home_village"` để bỏ qua template Builder Base — mọi fast path ở đây đều
fail-safe, miss thì rơi về đường quét đầy đủ), có `_ui_scale_memo` (nhớ scale từng template)
và cache kết quả `scan_for_confirmations()` theo frame,
`get_ui_cutoff()` tách vùng battlefield khỏi thanh HUD quân (scale theo chiều cao màn hình),
và có 4 ngưỡng match riêng theo category (`_ui_thr`, `_troop_thr`, `_building_thr`,
`_bb_card_thr`) đọc từ Settings. Template được cache trong bộ nhớ; sau khi đổi asset phải
gọi `clear_cache()`.

`vision/ocr_reader.py` đọc loot (Gold/Elixir/DE ở góc trên trái) và timer trận qua EasyOCR.
`vision/smart_vision_v2.py` + `vision/skills/*` lo phần hình học: HSV mask đường biên đỏ →
polygon → isometric grid → safe corridor / corner / target locator.

### CSR — hệ thống tấn công V2

`logic/v2_orchestrator.py` là **entry point duy nhất** của V2. `logic/smart_v2_logic.py`
chỉ delegate sang nó và giữ đường lui legacy.

1. `_ConfigLoader` hot-reload 3 file `config/v2_*.json` theo mtime — sửa JSON khi bot
   đang chạy sẽ có hiệu lực ngay, không cần restart.
2. Dựng `SkillBundle` (vision skills + logic planners) và danh sách Rule, sort theo `priority`.
3. Zoom-out thích ứng cho tới khi bề rộng polygon nằm trong `zoom_target_red_ratio`.
4. Chọn Rule: manual override từ Settings (`v2_rule_hv` / `v2_rule_bb`), nếu `auto` thì
   suy từ `v2_mode_*` và thành phần quân (`kind`: air/ground, `style`: scout_pairs/funnel)
   đọc từ `config/v2_troop_profiles.json`.
5. Dựng `AttackContext` rồi gọi `rule.execute(ctx)`.

**Chuỗi fallback** (quan trọng, đừng phá): rule đã chọn → `SmartDefaultRule` →
legacy V36 trong `smart_v2_logic._legacy_run()`. Vì vậy `execute()` **phải trả về `False`**
khi rule thoát sớm mà chưa thả quân — trả `True` sai chỗ sẽ nuốt mất fallback.

Bậc cuối cùng do village logic quyết định: khi setting `v2_skip_on_fallback` bật (mặc định),
HV gọi `SmartV2Logic.execute(..., allow_legacy=False)`; nếu trả `False` thì
`_skip_unplannable_base()` bấm `next_button` bỏ qua làng đó và đổi tally từ attack sang skip
(`BotEngine.record_attack_skipped()`). Ranked không có nút Next giữa trận nên vẫn chạy
`run_legacy()`. Tắt setting = giữ nguyên hành vi cũ (luôn đánh bằng V36).

Rules nằm ở `logic/rules/*`, kế thừa `AttackRule` (`base_rule.py`) với hợp đồng
`matches(profile, screenshot) -> bool` và `execute(ctx) -> bool`. Rule truy cập
vision/logic qua `ctx.skills`, tham số qua `ctx.config`. Rule nên deterministic
(sai khác chỉ do jitter của `HumanTouchSkill`).

**Cách thả quân** — hai đường, chọn bằng `deploy_pattern.hold_until_empty`
(hoặc `deploy_mode: "hold"|"tap"` từng quân trong `v2_troop_profiles.json`):

- *Tap burst* (mặc định): rule tính TRƯỚC toàn bộ điểm thả (đã lọc red-zone) rồi bắn
  theo chunk qua `HumanTouchSkill.tap_burst` → `adb_handler.tap_batch`. Không còn pause
  ngẫu nhiên giữa từng tap; `stagger_ms` của quân vẫn được tôn trọng nhưng chạy bằng
  `sleep` trên device (`tap_burst_gap_ms`), không phải `time.sleep` trong Python.
- *Hold-to-dump* (`AttackRule._hold_dump`): CoC thả quân liên tục khi giữ ngón tay, nên
  giữ theo chunk `hold_chunk_ms` và **kiểm tra bằng vision** giữa các chunk — thẻ quân hết
  sẽ biến khỏi thanh HUD, nên `skills.target.find_one()` trả `None` = đã thả xong.
  `hold_max_ms` chỉ là lưới an toàn.

Vì thẻ quân hết sẽ biến mất và các thẻ còn lại dồn chỗ, **mọi rule phải `screencap()` lại
trước khi tìm thẻ quân kế tiếp** — dùng tọa độ từ screenshot trước sẽ chọn nhầm quân.

**Chọn điểm thả** — `RingPlannerSkill` (`logic/skills/ring_planner.py`) là đường mặc định:
nong polygon vùng đỏ ra ngoài `deploy_ring.offset_px` rồi đi dọc đường viền đó, gom điểm
theo cạnh. Lý do phải bỏ mô hình hành lang cũ: `SafeCorridorSkill` dựng 4 hình chữ nhật
thẳng trục từ **bounding box** của polygon, trong khi base là hình thoi nên vành đất dùng
được chạy **chéo** — tức nằm *bên trong* bbox, hình chữ nhật ngoài bbox không thể chứa.
Đo trên một khung hình thật: hull 678k px, bbox 892k px, mất 24% chính là vành chéo; và
3/4 hành lang ra chiều rộng **âm** vì bbox đã chạm mép màn hình. Hành lang cũ còn kéo tới
sát mép màn hình, mà sau khi zoom out chỗ đó là **viền rừng ngoài bản đồ chơi được** — tap
vào đó game báo "You cannot deploy troops on the red area!". Không có chỗ nào trong code
biết bản đồ kết thúc ở đâu, nên ring phải ôm sát base thay vì bò ra xa.

`ring_planner.py` cố tình **không import gì từ `vision/`** (toàn số học thuần) để test hình
học chạy được mà không kéo cv2 vào — xem `tests/test_ring_planner.py`.

Đường hành lang cũ vẫn còn làm fallback. `FanPlannerSkill.plan()` xác định hướng quạt theo
**tên cạnh** (`side`), không theo tỉ lệ
khung hình chữ nhật: hành lang trái/phải trên màn 16:9 thường rộng hơn cao, đọc nhầm thành
"horizontal" sẽ rải quân từ base hướng ra ngoài và các điểm đầu rơi trúng cây cối sát base.
`edge_bias` (config `deploy_edge_bias`, 0 = giữa hành lang, 1 = sát rìa map) đẩy cả quạt ra
mép ngoài — rìa không thể có công trình nên điểm thả luôn hợp lệ, đổi lại quân đi bộ xa hơn.

### Assets — manifest-driven

Không hardcode đường dẫn ảnh. `assets/templates/manifest.json` là single source of truth
map `key → {category, label, file, width, height}`. Thao tác qua `vision/template_manager.py`
(`save_template`, `import_template_from_file`, `register_asset`, `load_template`,
`delete_template`, `get_sequence_readiness`). Thêm template mới = thêm file + entry manifest;
category quyết định ngưỡng match nào được dùng.

### Profiles & Config

- `profiles/settings.json` — settings toàn cục (gitignored, tạo lúc runtime).
- `profiles/*.json` — profile người dùng: ngưỡng loot, quân/hero/spell đã chọn,
  retreat rules, deploy timer, entry sequence. `default_profile.json` là mẫu.
  Deploy timer là một **khoảng** `deploy_timer_seconds` … `deploy_timer_seconds_max`;
  `HomeVillageLogic._resolve_deploy_deadline()` bốc số MỘT lần mỗi trận rồi giữ nguyên —
  bốc lại mỗi tick sẽ biến khoảng đó thành cận dưới (tick đầu tiên bốc trúng số nhỏ hơn
  thời gian đã trôi là kết thúc trận). Bỏ trống `_max` = timer cố định như trước.
- `config/v2_attack_rules.json` — tham số CSR toàn cục (stand_off_px, polygon HSV,
  isometric, deploy_pattern, funnel, spell_path_fractions, rule_priorities, và block
  riêng cho từng rule như `perimeter_sweep`).
- `config/v2_troop_profiles.json` / `v2_spell_profiles.json` — hành vi từng quân/spell.
- `strategies/*.json`, `recordings/*.json` — sequence và macro do người dùng tạo.

## Quy ước khi sửa code

**Tests không được import PyQt5 / cv2 / torch.** Test suite chạy trong vài chục ms vì
nó parse source bằng `ast` và exec riêng hàm cần kiểm tra (xem `tests/test_session_tally.py`),
hoặc dùng fake/stub. Giữ nguyên cách này — import thật sẽ kéo cả torch vào và làm test
không chạy được trên máy không có device.

Một số test pin luôn **call site** (ví dụ đếm số lần `self._count_attack()` xuất hiện
trong `home_village.py`). Nếu refactor các đường vào trận, phải cập nhật cả test đó.

Khác:
- Logging qua `BotLogger.get("<scope>")`, không dùng `print`.
- Mọi vòng lặp dài phải kiểm tra interrupt (`engine._running` / `engine._paused`) — xem
  `AttackRule._interrupted` và `V2Orchestrator._is_interrupted`.
- Tọa độ phải scale theo độ phân giải thực tế, không hardcode pixel cho 1080p.
- Commit message theo conventional commits, chữ thường: `feat:`, `fix:`, `refactor:`.
- README trong mỗi thư mục (`core/`, `logic/`, `vision/`, `ui/`, `config/`, `assets/`)
  mô tả chi tiết từng file — cập nhật khi đổi hành vi module.

## Lưu ý

`2adb.exe`, `AdbWinApi.dll`, `AdbWinUsbApi.dll` được commit kèm repo và bot gọi thẳng
`2adb.exe` từ project root; thiếu file này thì UI vẫn mở nhưng mọi lệnh device sẽ fail.
