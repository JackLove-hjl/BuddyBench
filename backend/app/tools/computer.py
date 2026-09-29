"""computer 工具:让模型「看见屏幕并操作电脑」。

设计对齐 OpenAI computer 工具:**单一工具 + `action` 参数**(动作集与字段名保持一致),
这样模型既有认知能直接命中;参数组合的校验放在工具体内,错误以可读中文返回
(而不是让模型吃 pydantic 校验错误)。

安全(详见 `plan/computer-use-replication.md`):
- 仅「全部权限」(full_access)档可用 —— 工具组按权限装配(见 registry.py),工具体内**再次**校验;
- 标记为 `mutating`,于是计划模式会自动摘掉它(plan_mode_hidden_tools 取 mutating_names);
- 审批由 `ComputerUseMiddleware` 在**每轮首次**调用时挂起一次,之后连续动作不再打断;
- 保留 pyautogui 的 FAILSAFE(鼠标移到屏幕角落即抛异常),再叠加动作上限与界面停止按钮。

截图链路:
- 落盘到 image_dir,回传 `/images/xxx.jpg` → 前端工具卡直接显示(现有图片分支);
- 模型侧"看到"屏幕靠中间件注入带 base64 图片的消息 —— **工具结果里不放 base64**,保持体积恒定。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from app.tools import computer_driver as drv
from app.tools.permissions import PERMISSION_FULL_ACCESS

logger = logging.getLogger(__name__)

COMPUTER_TOOL_NAME = "computer"

# 注入截图消息的标记文本:中间件据此识别"哪条消息是截图",注入新图时删掉旧的
SCREEN_MARKER = "[当前屏幕截图]"

ACTIONS = (
    "screenshot",
    "click",
    "double_click",
    "right_click",
    "move",
    "drag",
    "scroll",
    "type",
    "keypress",
    "wait",
)


# 这些动作"应该"让画面发生变化:若没变,说明没生效(点空、窗口没激活、坐标错),
# 必须当场告诉模型,否则它会换个坐标反复点、一路道歉到步数耗尽。
SCREEN_EXPECTED_ACTIONS = frozenset({"click", "double_click", "right_click", "type", "keypress", "drag"})


def _json(status: str, **kw) -> str:
    """结果序列化:体积恒定的小 JSON(截图只给 URL,base64 由中间件另行注入)。"""
    return json.dumps({"status": status, **kw}, ensure_ascii=False)


def _no_change_warning(action: str, foreground: str) -> str:
    """动作没让画面变化时的可执行指引(不是泛泛的"再试一次")。"""
    return (
        f"{action} 执行后画面没有变化(当前前台窗口:{foreground or '未知'})。按顺序排查:\n"
        "1) 目标窗口可能不在前台 —— Windows 下点击后台窗口通常只是把它激活。先用 action=\"screenshot\" "
        "看 foreground_window 是不是你要操作的应用;不是的话,先点一次该窗口的标题栏/标签把它激活,再点目标。\n"
        "2) 坐标可能不对 —— 必须按最近一次截图返回的 coordinate_space(已缩放的坐标系)给坐标,"
        "不要用屏幕真实分辨率。\n"
        "3) 目标也可能需要时间响应 —— 改 action=\"wait\" 等 1~2 秒再截图。\n"
        "**不要用同样的坐标重复点击**:先截图看清当前状态,再决定下一步。"
    )


def make_computer_tool(
    image_dir: Path,
    *,
    permission: str,
    enabled: bool = True,
    max_width: int = 1280,
    image_format: str = "jpeg",
    jpeg_quality: int = 80,
    action_interval: float = 0.4,
) -> BaseTool:
    """返回绑定 image_dir / 权限 / 显示参数的 @tool(工厂,每次请求按会话配置重建)。"""

    # 上一次已知的画面指纹(用可变容器持有,便于在闭包里更新)。
    # 用"动作前后指纹是否变化"来判断动作有没有生效 —— 这是模型自己无法感知、
    # 却决定它会不会原地打转的信息。
    last_frame: list[bytes | None] = [None]

    @tool
    def computer(
        action: str,
        x: int | None = None,
        y: int | None = None,
        text: str | None = None,
        keys: list[str] | None = None,
        path: list[list[int]] | None = None,
        scroll_x: int | None = None,
        scroll_y: int | None = None,
        seconds: float | None = None,
    ) -> str:
        """操作电脑屏幕(截屏 / 鼠标 / 键盘)。用于需要图形界面的任务(点击应用、填写表单、看运行结果)。

        坐标契约:所有 x/y 都按**最近一次截图**返回的 coordinate_space(截图宽高)给,
        左上角为 (0,0);不要用真实分辨率或估算的像素。

        动作与必填参数:
        - screenshot:无参数。**每次操作前后都应截图**,确认界面状态与结果。
        - click / double_click / right_click:x, y;可选 keys 传修饰键(如 ["ctrl"])。
        - move:x, y。
        - drag:path=[[x1,y1],[x2,y2]];可选 keys。
        - scroll:scroll_x 或 scroll_y(正值向上/向右,负值向下/向左;单位为一格滚动)。
        - type:text(中英文均可;中文会自动走剪贴板粘贴)。
        - keypress:keys,如 ["ctrl","c"]、["enter"]、["esc"]。
        - wait:seconds(等界面响应,最多 10 秒)。

        每个动作的结果里都会带回两样关键信息:
        - `foreground_window`:动作后当前的前台窗口。**操作别的应用前先看它**——如果它不是你的目标,
          说明你的点击落在别的窗口上,先把它激活再操作。
        - `screen_changed`:这一下是否让画面发生了变化。**为 false 时不要用同样的坐标重试**,
          先截图看清状态,按结果里的 warning 逐条排查(窗口未激活 / 坐标错 / 需要等待)。

        注意:动作本身不返回画面 —— 做完动作后请再调用 action="screenshot" 看结果;
        不要用它执行系统级危险快捷键(如注销/关机);要中断请让用户把鼠标甩到屏幕角落。
        """
        if not enabled:
            return _json("error", error="电脑操作能力已关闭(COMPUTER_USE_ENABLED=0)。")
        if permission != PERMISSION_FULL_ACCESS:
            return _json(
                "error",
                error=(
                    "电脑操作需要「全部权限」档位(它会真实控制鼠标键盘)。"
                    "请让用户把会话权限切到「全部权限」后再试。"
                ),
            )

        name = (action or "").strip().lower()
        if name not in ACTIONS:
            return _json("error", error=f"不支持的动作 {action!r};可用:{', '.join(ACTIONS)}。")

        try:
            if name == "screenshot":
                shot = drv.capture(
                    image_dir, max_width=max_width, image_format=image_format, quality=jpeg_quality
                )
                logger.info("computer screenshot: %s", shot.path.name)
                changed = drv.frame_changed(last_frame[0], shot.frame)
                last_frame[0] = shot.frame
                return _json(
                    "ok",
                    action=name,
                    image=shot.url,
                    foreground_window=shot.foreground,
                    screen_changed=changed,
                    note="这是当前界面;坐标请按 coordinate_space 给。",
                    **shot.geometry(),
                )
            before = last_frame[0]
            info = drv.perform(
                name,
                max_width=max_width,
                x=x,
                y=y,
                text=text,
                keys=keys,
                path=path,
                scroll_x=scroll_x,
                scroll_y=scroll_y,
                seconds=seconds,
                interval=action_interval,
            )
            info.pop("status", None)  # 驱动层已带 status,避免与 _json 的第一个参数重名
            # 动作后立刻抓一帧指纹(不落盘、不注入图片):回答"这一下到底生效没有"
            after = drv.probe_frame()
            changed = drv.frame_changed(before, after)
            last_frame[0] = after or before
            foreground = drv.foreground_window_title()
            info["screen_changed"] = changed
            info["foreground_window"] = foreground
            info["note"] = "动作已执行;请再截一张图确认结果。"
            if not changed and name in SCREEN_EXPECTED_ACTIONS:
                info["warning"] = _no_change_warning(name, foreground)
            return _json("ok", **info)
        except drv.DriverError as e:
            return _json("error", action=name, error=e.message)
        except Exception as e:  # noqa: BLE001  兜底:任何驱动异常都转成可读错误
            logger.exception("computer 动作失败")
            return _json("error", action=name, error=f"电脑操作失败:{type(e).__name__}: {e}")

    return computer
