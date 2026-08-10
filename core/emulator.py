"""Điều khiển giả lập LDPlayer qua ``ldconsole.exe``.

Vì sao cần: khi giả lập treo, ADB vẫn "xanh". ``adb devices`` hỏi ADB
server chạy trên PC, không hỏi máy ảo — server sống thì nó báo `device`
kể cả lúc bên trong đã đơ cứng. Bot vì thế không có cách nào tự biết mà
gỡ; nó chỉ có thể nện lệnh vào một con máy không còn trả lời.

``ldconsole.exe`` nằm ngoài đường ADB nên nó vẫn nghe lời khi Android bên
trong đã chết. Đó là lối thoát duy nhất còn lại: tắt hẳn máy ảo rồi bật
lại.

Module này CHỈ biết bật/tắt/hỏi trạng thái. Việc "khi nào thì nên khởi
động lại" là của ``BotEngine`` — tách ra để test được mà không cần máy
ảo thật.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from core.logger import BotLogger
from core.settings import Settings

log = BotLogger.get("emulator")

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# Nơi LDPlayer hay tự cài. Chỉ dùng khi Settings không chỉ đường dẫn.
_COMMON_PATHS = (
    r"C:\LDPlayer\LDPlayer9\ldconsole.exe",
    r"D:\LDPlayer\LDPlayer9\ldconsole.exe",
    r"C:\Program Files\LDPlayer\LDPlayer9\ldconsole.exe",
    r"C:\Program Files (x86)\LDPlayer\LDPlayer9\ldconsole.exe",
    r"C:\LDPlayer\LDPlayer4.0\dnconsole.exe",
    r"D:\LDPlayer\LDPlayer4.0\dnconsole.exe",
)


def console_path() -> str | None:
    """Đường dẫn tới ldconsole.exe, hoặc None nếu không tìm ra.

    Settings thắng mọi thứ — người dùng cài ở đâu chỉ họ mới biết chắc.
    """
    override = str(Settings().get("emulator_console_path", "") or "").strip()
    if override:
        return override if os.path.isfile(override) else None

    for candidate in _COMMON_PATHS:
        if os.path.isfile(candidate):
            return candidate
    return None


def is_available() -> bool:
    return console_path() is not None


def _run(args: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess | None:
    exe = console_path()
    if exe is None:
        log.warning("Không tìm thấy ldconsole.exe — bỏ qua lệnh %s.", args)
        return None
    try:
        return subprocess.run(
            [exe] + args,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            creationflags=_SUBPROCESS_FLAGS,
        )
    except subprocess.TimeoutExpired:
        log.error("ldconsole quá hạn: %s", " ".join(args))
        return None
    except OSError as exc:
        log.error("Không chạy được ldconsole: %s", exc)
        return None


def list_instances() -> list[dict]:
    """Danh sách máy ảo. Mỗi dòng ``list2`` là CSV:

    ``index,name,top_hwnd,bind_hwnd,android_started,pid,vbox_pid,w,h,dpi``
    """
    res = _run(["list2"], timeout=15.0)
    if res is None:
        return []

    out: list[dict] = []
    text = res.stdout.decode("utf-8", errors="ignore")
    for line in text.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 6:
            continue
        try:
            out.append({
                "index": int(parts[0]),
                "name": parts[1],
                "android_started": parts[4] == "1",
                "pid": int(parts[5]) if parts[5].lstrip("-").isdigit() else -1,
            })
        except (ValueError, IndexError):
            continue
    return out


def _target_args() -> list[str]:
    """Chọn máy ảo theo tên nếu có, không thì theo index.

    Tên bền hơn index: thêm/xoá một máy ảo là index xô hết, mà lúc đó bot
    sẽ lặng lẽ khởi động lại nhầm máy.
    """
    s = Settings()
    name = str(s.get("emulator_name", "") or "").strip()
    if name:
        return ["--name", name]
    return ["--index", str(int(s.get("emulator_index", 0)))]


def is_running() -> bool:
    """Android bên trong máy ảo đã bật chưa (không phải: có trả lời không)."""
    s = Settings()
    name = str(s.get("emulator_name", "") or "").strip()
    index = int(s.get("emulator_index", 0))
    for inst in list_instances():
        if (name and inst["name"] == name) or (not name and inst["index"] == index):
            return inst["android_started"]
    return False


def quit_instance() -> bool:
    log.info("Đang tắt giả lập…")
    return _run(["quit"] + _target_args(), timeout=30.0) is not None


def launch_instance() -> bool:
    log.info("Đang bật lại giả lập…")
    return _run(["launch"] + _target_args(), timeout=30.0) is not None


def restart(
    boot_timeout: float = 180.0,
    poll: float = 3.0,
    clock=time.time,
    sleep=time.sleep,
) -> bool:
    """Tắt hẳn rồi bật lại máy ảo, chờ tới khi Android báo đã lên.

    Trả True khi máy ảo đã khởi động xong. Trả True **không** có nghĩa là
    ADB đã dùng được — ADB nối lại sau đó vài giây nữa, và người gọi phải
    tự chờ (bot đã có sẵn vòng chờ ``_wait_for_game_ready``).

    Dùng quit + launch chứ không dùng ``reboot``: reboot khởi động lại
    Android *bên trong* máy ảo, mà khi cả máy ảo đã ngộp thì chính lớp đó
    mới là thứ cần dựng lại.
    """
    if not is_available():
        log.error("Không khởi động lại được: chưa tìm thấy ldconsole.exe.")
        return False

    quit_instance()

    # Chờ tắt hẳn. Không tắt được thì vẫn bật tiếp — ldconsole tự xử lý
    # trường hợp đang chạy, và đứng chờ mãi ở đây thì tệ hơn.
    deadline = clock() + 60.0
    while clock() < deadline and is_running():
        sleep(poll)

    sleep(3.0)
    launch_instance()

    deadline = clock() + boot_timeout
    while clock() < deadline:
        if is_running():
            log.info("Giả lập đã khởi động lại xong.")
            return True
        sleep(poll)

    log.error("Giả lập không lên sau %.0f giây.", boot_timeout)
    return False
