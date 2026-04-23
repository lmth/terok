# SPDX-FileCopyrightText: 2026 Magnus
# SPDX-License-Identifier: Apache-2.0

"""Non-systemd daemon management: start, stop, and status for vault and gate.

``terok daemon start``  — Start vault and gate server directly as background
                          processes.  No systemd required.
``terok daemon stop``   — Stop vault and gate server.
``terok daemon status`` — Show whether vault and gate are currently running.

Use this when systemd is not available (e.g. inside a container, or on a
workstation that hasn't run ``terok setup``).
"""

from __future__ import annotations

import argparse

_SENTINEL_START = "daemon_start"
_SENTINEL_STOP = "daemon_stop"
_SENTINEL_STATUS = "daemon_status"


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the ``daemon`` subcommand group."""
    p = sub.add_parser(
        "daemon",
        help="Start/stop/status host daemons without systemd",
    )
    daemon_sub = p.add_subparsers(dest="daemon_cmd", required=True)

    p_start = daemon_sub.add_parser("start", help="Start vault and gate server")
    p_start.set_defaults(_terok_local_cmd=_SENTINEL_START)

    p_stop = daemon_sub.add_parser("stop", help="Stop vault and gate server")
    p_stop.set_defaults(_terok_local_cmd=_SENTINEL_STOP)

    p_status = daemon_sub.add_parser("status", help="Show vault and gate server status")
    p_status.set_defaults(_terok_local_cmd=_SENTINEL_STATUS)


def dispatch(args: argparse.Namespace) -> bool:
    """Dispatch daemon sub-commands.  Returns True if handled."""
    cmd = getattr(args, "_terok_local_cmd", None)
    if cmd == _SENTINEL_START:
        _cmd_start()
        return True
    if cmd == _SENTINEL_STOP:
        _cmd_stop()
        return True
    if cmd == _SENTINEL_STATUS:
        _cmd_status()
        return True
    return False


# ---------------------------------------------------------------------------
# Sub-command implementations
# ---------------------------------------------------------------------------


def _cmd_start() -> None:
    """Start vault then gate server as background daemons."""
    from terok_sandbox import is_daemon_running, is_vault_running, start_daemon, start_vault

    from ...lib.core.config import make_sandbox_config

    cfg = make_sandbox_config()

    if is_vault_running(cfg):
        print("vault   already running — skipped")
    else:
        print("vault   starting … ", end="", flush=True)
        start_vault(cfg)
        print("ok" if is_vault_running(cfg) else "FAILED")

    if is_daemon_running(cfg):
        print("gate    already running — skipped")
    else:
        print("gate    starting … ", end="", flush=True)
        start_daemon(cfg=cfg)
        print("ok" if is_daemon_running(cfg) else "FAILED")


def _cmd_stop() -> None:
    """Stop gate server then vault."""
    from terok_sandbox import is_daemon_running, is_vault_running, stop_daemon, stop_vault

    from ...lib.core.config import make_sandbox_config

    cfg = make_sandbox_config()

    if is_daemon_running(cfg):
        print("gate    stopping … ", end="", flush=True)
        stop_daemon(cfg)
        print("ok" if not is_daemon_running(cfg) else "FAILED")
    else:
        print("gate    not running — skipped")

    if is_vault_running(cfg):
        print("vault   stopping … ", end="", flush=True)
        stop_vault(cfg)
        print("ok" if not is_vault_running(cfg) else "FAILED")
    else:
        print("vault   not running — skipped")


def _cmd_status() -> None:
    """Print running state of vault and gate server."""
    from terok_sandbox import is_daemon_running, is_vault_running

    from ...lib.core.config import make_sandbox_config

    cfg = make_sandbox_config()

    vault_state = "running" if is_vault_running(cfg) else "stopped"
    gate_state = "running" if is_daemon_running(cfg) else "stopped"
    print(f"vault   {vault_state}")
    print(f"gate    {gate_state}")
