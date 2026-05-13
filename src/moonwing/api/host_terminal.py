"""In-browser host shell (WebSocket + PTY) — optional, admin-gated."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import logging
import os
import select
import signal
import struct
from uuid import UUID

try:
    import fcntl
    import termios
except ImportError:  # Windows — module still loads; PTY path stays disabled
    fcntl = None  # type: ignore[assignment, misc]
    termios = None  # type: ignore[assignment, misc]

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from moonwing.api.deps import _get_session_factory, _get_settings
from moonwing.db.models import User
from moonwing.services.auth import parse_session_token
from moonwing.services.permissions import can

logger = logging.getLogger(__name__)


def terminal_supported() -> bool:
    """PTY shell bridge is POSIX-only (typical Linux manager host)."""
    if os.name != "posix" or not hasattr(os, "forkpty"):
        return False
    if fcntl is None or termios is None:
        return False
    return True


async def _authenticate_terminal_ws(websocket: WebSocket) -> User | None:
    settings = _get_settings()
    raw = websocket.cookies.get("moonwing_session")
    payload = parse_session_token(raw, secret=settings.session_secret)
    if not payload:
        return None
    try:
        uid = UUID(payload["sub"])
    except (KeyError, ValueError):
        return None
    session = _get_session_factory()()
    try:
        user = session.get(User, uid)
        if user is None or user.status != "active":
            return None
        if user.is_bootstrap:
            return None
        if not can(user.role, "system_updates"):
            return None
        return user
    finally:
        session.close()


def _set_winsize(master_fd: int, rows: int, cols: int) -> None:
    if fcntl is None or termios is None:
        return
    if rows <= 0 or cols <= 0:
        return
    try:
        winsz = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsz)
    except OSError as exc:
        logger.debug("TIOCSWINSZ failed: %s", exc)


async def _host_terminal_session(websocket: WebSocket) -> None:
    settings = _get_settings()
    if not settings.web_terminal_enabled:
        await websocket.close(code=4403, reason="Web terminal disabled")
        return
    if not terminal_supported():
        await websocket.close(code=4403, reason="Web terminal unsupported on this platform")
        return

    user = await _authenticate_terminal_ws(websocket)
    if user is None:
        await websocket.close(code=4401, reason="Authentication or permission denied")
        return

    await websocket.accept()
    shell = settings.web_terminal_shell.strip() or "/bin/sh"
    if not os.path.isfile(shell):
        await websocket.send_text(
            json.dumps(
                {
                    "type": "moonwing_error",
                    "message": f"Configured shell not found: {shell!r} (set MOONWING_WEB_TERMINAL_SHELL)",
                }
            )
        )
        await websocket.close(code=1011)
        return

    pid: int | None = None
    master_fd: int | None = None
    try:
        pid, master_fd = os.forkpty()  # type: ignore[attr-defined]
    except AttributeError:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "moonwing_error",
                    "message": "forkpty() is not available on this Python build",
                }
            )
        )
        await websocket.close(code=1011)
        return
    except OSError as exc:
        logger.warning("forkpty failed: %s", exc)
        await websocket.send_text(json.dumps({"type": "moonwing_error", "message": str(exc)}))
        await websocket.close(code=1011)
        return

    if pid == 0:
        try:
            os.environ.setdefault("TERM", "xterm-256color")
            argv0 = os.path.basename(shell) or shell
            # dash/sh reject -i; bash accepts interactive login-ish session on a PTY.
            if argv0 == "bash":
                os.execl(shell, argv0, "-il")
            else:
                os.execl(shell, argv0)
        except OSError as exc:
            print(f"moonwing: exec shell failed: {exc}", flush=True)
        os._exit(127)

    assert master_fd is not None and pid is not None
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    loop = asyncio.get_running_loop()

    async def pump_pty_to_ws() -> None:
        assert master_fd is not None
        while True:
            try:
                ready, _, _ = await loop.run_in_executor(None, lambda: select.select([master_fd], [], [], 0.25))
            except (ValueError, OSError):
                break
            if not ready:
                continue
            try:
                chunk = os.read(master_fd, 65536)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    continue
                break
            if not chunk:
                break
            try:
                await websocket.send_bytes(chunk)
            except (WebSocketDisconnect, RuntimeError):
                break

    try:
        pump = asyncio.create_task(pump_pty_to_ws())
        while True:
            try:
                msg = await websocket.receive()
            except WebSocketDisconnect:
                break
            mtype = msg.get("type")
            if mtype == "websocket.disconnect":
                break
            if mtype != "websocket.receive":
                continue
            if "bytes" in msg and msg["bytes"] is not None:
                data: bytes = msg["bytes"]
                try:
                    os.write(master_fd, data)
                except OSError:
                    break
            elif "text" in msg and msg["text"] is not None:
                try:
                    payload: dict[str, Any] = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                if payload.get("type") == "resize":
                    rows = int(payload.get("rows") or 0)
                    cols = int(payload.get("cols") or 0)
                    _set_winsize(master_fd, rows, cols)
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
    finally:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.close()


def register_host_terminal(app: FastAPI) -> None:
    """Attach WebSocket route (HTTP auth middleware does not run for WebSockets)."""

    @app.websocket("/ws/system/terminal")
    async def _terminal_ws(websocket: WebSocket) -> None:
        await _host_terminal_session(websocket)
