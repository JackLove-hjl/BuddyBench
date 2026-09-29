"""computer use 底层驱动:截屏 + 鼠标键盘输入(Windows 优先,跨平台尽力)。

设计要点(配套探针结论见 `plan/computer-use-replication.md`):

1. **坐标契约**:模型永远在「截图坐标系」里工作。`capture()` 把屏幕按 `max_width`
   等比缩放后落盘,并在结果里回传 `coordinate_space`(截图宽高)与 `scale`;所有输入
   动作在驱动层换算回真实屏幕像素 —— 模型不需要知道真实分辨率。
2. **确定性换算**:截图宽度恒为 `min(屏幕宽, max_width)`,所以 `scale = 屏幕宽 / 截图宽`
   可以直接算出来,输入动作不依赖"上一张截图"的状态,避免状态漂移。
3. **DPI 感知**:Windows 上先 `SetProcessDpiAwareness(2)` 再截屏/取坐标,保证截图尺寸与
   鼠标坐标系一致(实测本机 2560x1440 前后一致;换机器或改缩放比例时这一步是必需的)。
4. **非 ASCII 文本**:pyautogui 直接 `write` 中文不可靠,走「保存剪贴板 → 写入 → Ctrl+V →
   恢复剪贴板」,这是参考实现(Claude Code computer use)同款做法。
5. **急停**:保留 `pyautogui.FAILSAFE`(鼠标移到屏幕角落立刻抛异常中止),再叠加工具层的
   动作上限与界面的停止按钮。
6. **错误可读**:依赖缺失 / 无显示设备 / 坐标越界 / 参数不组合法,一律抛 `DriverError`
   并带中文说明,由工具层转成 `{"status":"error","error":"…"}` 交给模型。

本模块**只做机械动作**,不做权限判断与审批(那些在工具层与中间件层)。
"""
from __future__ import annotations

import base64
import io
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# pyautogui 的拖动/双击等动作的默认节奏(秒);太快部分应用会漏事件
MOVE_DURATION = 0.25
WRITE_INTERVAL = 0.02
KEY_HOLD_PAUSE = 0.05
CLIPBOARD_PAUSE = 0.08
MAX_WAIT_SECONDS = 10.0  # wait 动作上限,防止模型 sleep 很久占住整轮

_dpi_ready = False
_last_action_at = 0.0


class DriverError(Exception):
    """可读的驱动错误(工具层直接转 JSON,不暴露堆栈)。

    与 `app.tools.permissions.PermissionDenied` 保持同一形状(带 `.message`),
    工具层统一用 `e.message` 取中文说明。
    """

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@dataclass
class Capture:
    """一次截屏的结果与坐标契约。"""

    path: Path
    url: str  # 形如 /images/<uuid>.jpg,前端静态挂载直接可用
    image_b64: str  # data url,注入给模型看
    coord_w: int
    coord_h: int
    screen_w: int
    screen_h: int
    scale: float  # 真实像素 = 截图坐标 * scale
    frame: bytes  # 粗粒度灰度指纹,只用于"画面有没有变",不进 JSON
    foreground: str  # 截屏时的前台窗口标题

    def geometry(self) -> dict:
        return {
            "coordinate_space": {"width": self.coord_w, "height": self.coord_h},
            "screen": {"width": self.screen_w, "height": self.screen_h},
            "scale": round(self.scale, 4),
        }


# 画面变化检测:把整屏缩到很小的灰度图再比"平均差"。
# 为什么要这么粗:直接哈希原图时,一个闪烁的光标、秒针、进度动画都会让画面"变化",
# 于是永远判不出"点了没反应";降到 32x18 灰度后,这类细微抖动被平均掉,
# 真正发生变化的(窗口切换、新内容、弹窗)整片像素都会动。
FRAME_SIZE = (32, 18)
# 平均像素差阈值(0-255):低于它视为"画面没有实质变化"
FRAME_CHANGED_THRESHOLD = 1.5


def frame_signature(image) -> bytes:
    """截图的粗粒度灰度指纹(PIL 图像 → bytes)。"""
    return image.convert("L").resize(FRAME_SIZE).tobytes()


def frame_changed(before: bytes | None, after: bytes | None) -> bool:
    """两张指纹是否有实质差异;缺任一张时保守返回 True(不误报"没变化")。"""
    if not before or not after or len(before) != len(after):
        return True
    total = sum(abs(a - b) for a, b in zip(before, after))
    return total / len(before) >= FRAME_CHANGED_THRESHOLD


def foreground_window_title() -> str:
    """当前前台窗口标题。

    这是判断"点击到底落到哪个窗口"最直接的信息 —— 模型经常在点自己所在的窗口
    却以为在点别的应用,把它回传过去,模型当场就能发现自己搞错了目标。
    """
    if os.name != "nt":
        return ""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value.strip()
    except Exception:  # noqa: BLE001  取不到就算了,不影响动作本身
        logger.debug("读取前台窗口标题失败", exc_info=True)
        return ""


def probe_frame() -> bytes | None:
    """只抓一帧指纹,不落盘、不编码 —— 动作后判断"画面变没变"用。"""
    try:
        from PIL import ImageGrab

        return frame_signature(ImageGrab.grab())
    except Exception:  # noqa: BLE001
        return None


def _pyautogui():
    """延迟导入:未安装时给出可读错误,而不是 ImportError 冒到 SSE。"""
    try:
        import pyautogui
    except Exception as exc:  # noqa: BLE001  缺依赖/无显示设备都会在这里暴露
        raise DriverError(
            f"电脑操作依赖不可用({type(exc).__name__}: {exc})。"
            "请先安装:uv pip install pyautogui(需要图形界面环境)。"
        ) from exc
    return pyautogui


def ensure_display_aware() -> None:
    """Windows DPI 感知:必须在截屏/取坐标之前调用一次。"""
    global _dpi_ready
    if _dpi_ready:
        return
    _dpi_ready = True
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:  # noqa: BLE001  老系统/权限不足时退回默认行为
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            logger.debug("DPI 感知设置失败,继续使用系统默认", exc_info=True)


def screen_size() -> tuple[int, int]:
    """真实屏幕尺寸(主屏)。"""
    ensure_display_aware()
    w, h = _pyautogui().size()
    return int(w), int(h)


def capture_geometry(max_width: int) -> tuple[int, int, int, int, float]:
    """(屏幕宽, 屏幕高, 截图宽, 截图高, scale) —— 输入动作与截屏共用同一套换算。"""
    screen_w, screen_h = screen_size()
    coord_w = min(screen_w, max(320, int(max_width)))
    coord_h = max(1, round(screen_h * coord_w / screen_w))
    return screen_w, screen_h, coord_w, coord_h, screen_w / coord_w


def _to_screen(x: int, y: int, *, coord_w: int, coord_h: int, scale: float) -> tuple[int, int]:
    """截图坐标 → 真实屏幕像素,并做越界检查。"""
    if not (0 <= x < coord_w and 0 <= y < coord_h):
        raise DriverError(
            f"坐标 ({x}, {y}) 超出截图范围:截图坐标系为 {coord_w}x{coord_h}。"
            "请按最近一次截图结果里的 coordinate_space 给坐标(左上角为 0,0)。"
        )
    return int(round(x * scale)), int(round(y * scale))


def capture(
    image_dir: Path,
    *,
    max_width: int,
    image_format: str = "jpeg",
    quality: int = 80,
) -> Capture:
    """截屏 → 缩放 → 落盘 → 返回坐标契约与可直接给模型看的 data url。"""
    ensure_display_aware()
    try:
        from PIL import ImageGrab
    except Exception as exc:  # noqa: BLE001
        raise DriverError(f"截屏依赖 Pillow 的 ImageGrab 不可用:{exc}") from exc

    screen_w, screen_h, coord_w, coord_h, scale = capture_geometry(max_width)
    try:
        shot = ImageGrab.grab()
    except Exception as exc:  # noqa: BLE001
        raise DriverError(
            f"截屏失败({type(exc).__name__}: {exc})。请确认当前会话有可用的图形桌面。"
        ) from exc

    if (shot.width, shot.height) != (coord_w, coord_h):
        shot = shot.resize((coord_w, coord_h))

    fmt = "PNG" if str(image_format).lower() == "png" else "JPEG"
    suffix = "png" if fmt == "PNG" else "jpg"
    image_dir.mkdir(parents=True, exist_ok=True)
    path = image_dir / f"{uuid.uuid4()}.{suffix}"
    buf = io.BytesIO()
    if fmt == "JPEG":
        shot.convert("RGB").save(buf, format=fmt, quality=int(quality), optimize=True)
    else:
        shot.save(buf, format=fmt, optimize=True)
    raw = buf.getvalue()
    path.write_bytes(raw)
    logger.info("computer use 截屏 %dx%d → %dx%d(%d KB)", screen_w, screen_h, coord_w, coord_h, len(raw) // 1024)
    return Capture(
        path=path,
        url=f"/images/{path.name}",
        image_b64="data:image/" + ("png" if fmt == "PNG" else "jpeg") + ";base64," + base64.b64encode(raw).decode(),
        coord_w=coord_w,
        coord_h=coord_h,
        screen_w=screen_w,
        screen_h=screen_h,
        scale=scale,
        frame=frame_signature(shot),
        foreground=foreground_window_title(),
    )


def _throttle(interval: float) -> None:
    """动作最小间隔:避免连续动作把目标应用打爆(也为人工急停留出反应时间)。"""
    global _last_action_at
    if interval > 0:
        wait = interval - (time.monotonic() - _last_action_at)
        if wait > 0:
            time.sleep(wait)
    _last_action_at = time.monotonic()


def _clipboard_paste(text: str) -> None:
    """非 ASCII 文本走剪贴板粘贴,并恢复原剪贴板内容。"""
    pag = _pyautogui()
    try:
        import pyperclip
    except Exception as exc:  # noqa: BLE001
        raise DriverError(f"输入中文需要 pyperclip(随 pyautogui 安装),当前不可用:{exc}") from exc
    try:
        prev = pyperclip.paste()
    except Exception:  # noqa: BLE001  剪贴板读不到就只写不改
        prev = None
    pyperclip.copy(text)
    time.sleep(CLIPBOARD_PAUSE)
    try:
        pag.hotkey("ctrl", "v")
    finally:
        time.sleep(CLIPBOARD_PAUSE)
        if prev is not None:
            try:
                pyperclip.copy(prev)
            except Exception:  # noqa: BLE001
                logger.debug("剪贴板恢复失败", exc_info=True)


def _with_keys(keys: list[str] | None, action) -> None:  # noqa: ANN001
    """按住修饰键执行动作(如 ctrl+click)。"""
    pag = _pyautogui()
    pressed: list[str] = []
    try:
        for key in keys or []:
            pag.keyDown(key)
            pressed.append(key)
            time.sleep(KEY_HOLD_PAUSE)
        action()
    finally:
        for key in reversed(pressed):
            try:
                pag.keyUp(key)
            except Exception:  # noqa: BLE001
                logger.debug("释放按键失败:%s", key, exc_info=True)


def _point(x: int | None, y: int | None, *, coord_w: int, coord_h: int, scale: float) -> tuple[int, int]:
    if x is None or y is None:
        raise DriverError("该动作需要 x 与 y(坐标以最近一次截图的 coordinate_space 为准)。")
    return _to_screen(int(x), int(y), coord_w=coord_w, coord_h=coord_h, scale=scale)


def perform(
    action: str,
    *,
    max_width: int,
    x: int | None = None,
    y: int | None = None,
    text: str | None = None,
    keys: list[str] | None = None,
    path: list[list[int]] | None = None,
    scroll_x: int | None = None,
    scroll_y: int | None = None,
    seconds: float | None = None,
    interval: float = 0.4,
) -> dict:
    """执行一个输入动作(不含截图)。

    返回可直接作为工具结果片段的小体积字典;不合法/失败时抛 `DriverError`。

    `max_width` 必须与截屏时一致 —— 坐标换算是 `真实像素 = 截图坐标 × scale`,
    而 `scale` 由「屏幕宽 / 截图宽」决定;两处不一致会导致点击偏移(最经典的坑)。
    """
    pag = _pyautogui()
    screen_w, screen_h, coord_w, coord_h, scale = capture_geometry(max_width)
    geometry = {
        "coordinate_space": {"width": coord_w, "height": coord_h},
        "screen": {"width": screen_w, "height": screen_h},
        "scale": round(scale, 4),
    }
    _throttle(interval)

    if action in {"click", "double_click", "right_click"}:
        sx, sy = _point(x, y, coord_w=coord_w, coord_h=coord_h, scale=scale)
        button = "right" if action == "right_click" else "left"
        clicks = 2 if action == "double_click" else 1
        _with_keys(keys, lambda: pag.click(sx, sy, clicks=clicks, interval=0.05, button=button))
        return {"status": "ok", "action": action, "detail": f"({x}, {y}) → 屏幕 ({sx}, {sy})", **geometry}

    if action == "move":
        sx, sy = _point(x, y, coord_w=coord_w, coord_h=coord_h, scale=scale)
        pag.moveTo(sx, sy, duration=MOVE_DURATION)
        return {"status": "ok", "action": action, "detail": f"({x}, {y}) → 屏幕 ({sx}, {sy})", **geometry}

    if action == "drag":
        if not path or len(path) < 2 or len(path[0]) < 2 or len(path[1]) < 2:
            raise DriverError('drag 需要 path=[[x1,y1],[x2,y2]](坐标以截图坐标系为准)。')
        sx1, sy1 = _to_screen(int(path[0][0]), int(path[0][1]), coord_w=coord_w, coord_h=coord_h, scale=scale)
        sx2, sy2 = _to_screen(int(path[1][0]), int(path[1][1]), coord_w=coord_w, coord_h=coord_h, scale=scale)

        def _drag() -> None:
            pag.moveTo(sx1, sy1, duration=MOVE_DURATION)
            pag.mouseDown()
            try:
                pag.moveTo(sx2, sy2, duration=MOVE_DURATION * 2)
            finally:
                pag.mouseUp()

        _with_keys(keys, _drag)
        return {"status": "ok", "action": action, "detail": f"{path[0]} → {path[1]}", **geometry}

    if action == "scroll":
        vx = int(scroll_x or 0)
        vy = int(scroll_y or 0)
        if vx == 0 and vy == 0:
            raise DriverError("scroll 需要 scroll_x 或 scroll_y(正负决定方向;正值向上/向右)。")
        if vy:
            pag.scroll(vy)
        if vx:
            pag.hscroll(vx)
        return {"status": "ok", "action": action, "detail": f"scroll_x={vx}, scroll_y={vy}", **geometry}

    if action == "type":
        if not text:
            raise DriverError("type 需要 text。")
        if text.isascii():
            pag.write(text, interval=WRITE_INTERVAL)
            how = "直接键入"
        else:
            _clipboard_paste(text)
            how = "剪贴板粘贴(中文等非 ASCII)"
        return {"status": "ok", "action": action, "detail": f"{len(text)} 字符,{how}", **geometry}

    if action == "keypress":
        if not keys:
            raise DriverError('keypress 需要 keys,如 ["ctrl","c"] 或 ["enter"]。')
        if len(keys) == 1:
            pag.press(keys[0])
        else:
            pag.hotkey(*keys)
        return {"status": "ok", "action": action, "detail": "+".join(keys), **geometry}

    if action == "wait":
        secs = float(seconds if seconds is not None else 1.0)
        if secs <= 0:
            raise DriverError("wait 需要 seconds > 0。")
        waited = min(secs, MAX_WAIT_SECONDS)
        time.sleep(waited)
        return {"status": "ok", "action": action, "detail": f"等待 {waited}s", **geometry}

    raise DriverError(
        f"不支持的动作:{action}。可用:click / double_click / right_click / move / drag / "
        "scroll / type / keypress / wait。"
    )
