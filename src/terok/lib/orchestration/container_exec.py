# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Container-based command execution for agent workspaces.

Runs git (and other commands) **inside** task containers via the
terok-sandbox ``sandbox_exec`` API instead of on the host, eliminating
the risk of poisoned git hooks or scripts executing with host privileges.
"""

from subprocess import TimeoutExpired

from terok_sandbox import container_start, container_stop, get_container_state, sandbox_exec

from ..core.task_display import container_name as _container_name
from ..util.logging_utils import _log_debug


def _podman_start(cname: str) -> bool:
    """Start a stopped container, returning ``True`` on success."""
    try:
        result = container_start(cname)
    except FileNotFoundError:
        _log_debug(f"container_exec._podman_start({cname}): podman not found")
        return False
    if result.returncode != 0:
        _log_debug(f"container_exec._podman_start({cname}): rc={result.returncode}")
        return False
    return True


def _podman_stop(cname: str, timeout: int = 10) -> None:
    """Stop a container best-effort."""
    try:
        container_stop(cname, timeout=timeout)
    except FileNotFoundError:
        pass


def container_git_diff(
    project_id: str,
    task_id: str,
    mode: str,
    *args: str,
    timeout: int = 30,
) -> str | None:
    """Run ``git diff`` inside a task container and return stdout.

    Args:
        project_id: Project identifier.
        task_id: Task identifier.
        mode: Container mode (``"cli"``, ``"web"``, ``"run"``, ``"toad"``).
        *args: Additional arguments passed to ``git diff`` (e.g.
            ``"--stat"``, ``"HEAD@{1}..HEAD"``).
        timeout: Subprocess timeout in seconds.

    Returns:
        The diff output on success, or ``None`` on any failure.

    If the container is stopped/exited it is temporarily restarted for the
    exec, then stopped again.  All git commands target the container-internal
    ``/workspace`` path — the host ``workspace-dangerous`` path is never
    passed to any subprocess.
    """
    cname = _container_name(project_id, mode, task_id)
    state = get_container_state(cname)

    if state is None:
        _log_debug(f"container_git_diff: container {cname} not found")
        return None

    restarted = False
    if state != "running":
        if mode == "run":
            # Never restart exited headless containers — podman start replays
            # the original entrypoint (the agent command), causing duplicate
            # commits, network calls, and other side effects.
            _log_debug(f"container_git_diff: refusing to restart exited headless container {cname}")
            return None
        if not _podman_start(cname):
            return None
        restarted = True

    try:
        result = sandbox_exec(cname, ["git", "-C", "/workspace", "diff", *args], timeout=timeout)
        if result.returncode != 0:
            _log_debug(f"container_git_diff: git diff failed rc={result.returncode}")
            return None
        return result.stdout
    except (FileNotFoundError, TimeoutExpired) as exc:
        _log_debug(f"container_git_diff: {exc}")
        return None
    finally:
        if restarted:
            _podman_stop(cname)
