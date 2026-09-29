"""终端 WebSocket:浏览器里的 xterm.js 与真实 shell(ConPTY)之间的双向管道。

为什么是 WebSocket:真终端必须双向流 —— 按键要实时送进 shell(stdin)、输出要实时
回来(stdout/stderr)、还要能改窗口尺寸;`POST /exec` 那种「一问一答的管道」做不到
回显、Ctrl+C、cd 持久化。

认证:浏览器的 WebSocket 构造函数不允许自定义请求头,拿不到 `Authorization`,
所以 token 走查询串(`?token=`),这里手工调用 `verify_token`,不走 `get_current_user`。
token 会出现在后端访问日志里,这是本机模式下的取舍(不落库、不外发)。

协议(文本帧 = JSON 控制消息,二进制帧 = 终端原始字节):
  server → client  ready / exit / error / pong
  client → server  {"type":"input","data":...} / {"type":"resize","cols":..,"rows":..} / ping
"""

import asyncio
import json
import logging
import uuid

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, WebSocket

from app.api.deps import get_current_user
from app.api.workspaces import _get_conversation_or_404, _resolve_rel, _resolve_workspace
from app.core.security import verify_token
from app.db.base import SessionLocal
from app.db.models import User
from app.services import terminal as terminal_service
from app.services.terminal import (
    MAX_COLS,
    MAX_INPUT_CHARS,
    MAX_ROWS,
    MAX_SESSIONS_PER_USER,
    MIN_COLS,
    MIN_ROWS,
    clamp,
)
from app.tools.shell import _scrub_env

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["terminal"])

# 关闭码:策略违规(档位不允许 / 会话过多),前端据此提示
WS_POLICY_VIOLATION = 1008


async def _refuse(websocket: WebSocket, message: str) -> None:
    """已 accept 后拒绝:先发一条可读原因再关闭(浏览器的 close 事件读不到理由)。"""
    try:
        await websocket.send_json({"type": "error", "message": message})
        await websocket.close(code=WS_POLICY_VIOLATION)
    except Exception:  # noqa: BLE001  对端可能已经走了
        pass


def _int_param(raw: str | None, default: int) -> int:
    try:
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


async def _authorize(conversation_id: uuid.UUID, token: str) -> tuple[str | None, str | None, str]:
    """校验 token 与会话档位。

    返回 `(user_id, workspace_path, error)` —— 失败时 user_id 为 None、error 是给用户看的原因。
    """
    user_id = verify_token(token)
    if not user_id:
        return None, None, "登录状态已失效,请刷新页面后重试"
    try:
        conv_id = uuid.UUID(str(conversation_id))
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        return None, None, "会话标识无效"
    async with SessionLocal() as session:
        try:
            conv = await _get_conversation_or_404(session, conv_id, user_uuid)
        except HTTPException:
            return None, None, "会话不存在或无权访问"
        if conv.permission == "read_only":
            return None, None, "当前会话是「只读」档位,不允许打开终端(请先在顶部切换到可写或全部权限)"
        if not conv.workspace_path:
            return None, None, "当前会话未设置工作区,终端需要先有工作目录"
        return user_id, conv.workspace_path, ""


@router.websocket("/{conversation_id}/terminal")
async def terminal_socket(websocket: WebSocket, conversation_id: uuid.UUID) -> None:
    """打开一个终端会话。查询串参数:token / shell / cwd / rows / cols。"""
    await websocket.accept()
    query = websocket.query_params

    user_id, workspace_path, problem = await _authorize(conversation_id, query.get("token", ""))
    if user_id is None:
        await _refuse(websocket, problem or "无法打开终端")
        return

    try:
        root = await to_thread.run_sync(_resolve_workspace, str(workspace_path))
        cwd = await to_thread.run_sync(_resolve_rel, root, (query.get("cwd") or "").strip())
    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, dict) else {}
        await _refuse(websocket, str(detail.get("message") or "工作目录不可用"))
        return

    if terminal_service.registry.count_for(user_id) >= MAX_SESSIONS_PER_USER:
        await _refuse(websocket, f"最多同时打开 {MAX_SESSIONS_PER_USER} 个终端,请先关掉一些")
        return

    spec = terminal_service.find_shell(query.get("shell") or "")
    rows = clamp(_int_param(query.get("rows"), 24), MIN_ROWS, MAX_ROWS)
    cols = clamp(_int_param(query.get("cols"), 80), MIN_COLS, MAX_COLS)
    session = terminal_service.TerminalSession(
        spec=spec,
        cwd=str(cwd),
        # 与 run_shell_command 同一套环境变量剔除规则(不把 KEY/TOKEN/SECRET 交给 shell)
        env=_scrub_env(),
        user_id=user_id,
        rows=rows,
        cols=cols,
    )
    try:
        await to_thread.run_sync(session.start)
    except OSError as e:
        await _refuse(websocket, f"无法启动 {spec.label}:{e}")
        return

    terminal_service.registry.add(session)
    await websocket.send_json(
        {
            "type": "ready",
            "session_id": session.id,
            "shell": spec.key,
            "label": spec.label,
            "cwd": str(cwd),
            "rows": session.rows,
            "cols": session.cols,
        }
    )

    output_task = asyncio.create_task(_pump_output(websocket, session))
    input_task = asyncio.create_task(_pump_input(websocket, session))
    try:
        _done, pending = await asyncio.wait({output_task, input_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        terminal_service.registry.remove(session)
        await to_thread.run_sync(session.close)
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001  对端已断开
            pass


@router.get("/shells")
async def list_shells(user: User = Depends(get_current_user)) -> dict:
    """本机可用的 shell 列表(前端的「+ 新建终端」据此出菜单)。

    Windows 上通常是 cmd + Windows PowerShell,装了 PowerShell 7 还会有 pwsh;
    没装的东西不列出来,菜单里就不会出现点了打不开的项。
    """
    shells = [{"key": spec.key, "label": spec.label} for spec in terminal_service.available_shells()]
    return {"shells": shells, "default": shells[0]["key"] if shells else ""}


async def _pump_output(websocket: WebSocket, session: terminal_service.TerminalSession) -> None:
    """PTY → 浏览器(二进制帧)。"""
    while True:
        payload = await session.read()
        if payload is None:
            break
        await websocket.send_bytes(payload)
    await websocket.send_json({"type": "exit", "code": session.exit_code})


async def _pump_input(websocket: WebSocket, session: terminal_service.TerminalSession) -> None:
    """浏览器 → PTY:按键输入、改尺寸、心跳。"""
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            return
        chunk = message.get("bytes")
        if chunk is not None:
            # 二进制帧当作原始输入(粘贴大段内容时前端会走这条路)
            session.write(chunk.decode("utf-8", "replace"))
            continue
        text = message.get("text")
        if text is None:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            # 不是 JSON 就当成纯按键输入,容错
            session.write(text[:MAX_INPUT_CHARS])
            continue
        kind = payload.get("type")
        if kind == "input":
            data = str(payload.get("data") or "")
            session.write(data[:MAX_INPUT_CHARS])
        elif kind == "resize":
            session.resize(
                _int_param(str(payload.get("rows")), session.rows),
                _int_param(str(payload.get("cols")), session.cols),
            )
        elif kind == "ping":
            await websocket.send_json({"type": "pong"})
