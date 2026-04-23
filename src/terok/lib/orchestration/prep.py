# SPDX-FileCopyrightText: 2026 Magnus Therning
# SPDX-License-Identifier: Apache-2.0

"""Prep-task support: package-manager shims, log extraction, and L0 injection.

The prep workflow lets a user interactively install tools in an L2 container
while terok silently intercepts package-manager calls (apt-get, dnf, pip).
When the prep container is stopped, terok persists the captured package set
and injects it into all future L0 builds for the same base image — making
the installed tools available to every new project without manual Dockerfile
editing.

Shim mechanism
--------------
Shell scripts written to ``prep_state_dir()/<task_id>/shims/`` are bind-
mounted read-only into the container at ``/terok-shims/``.  A profile.d
snippet at ``/etc/profile.d/terok-prep.sh`` prepends that path to PATH so
every interactive shell sees the shims first.

Each shim script logs the invocation to ``/tmp/terok-prep-log/log.jsonl``
(which is bind-mounted from ``prep_state_dir()/<task_id>/log/`` on the host
so terok can read it without ``podman cp``), then execs the real binary from
the standard search path.

Persistence
-----------
Captured packages are stored in
``prep_state_dir()/packages/<base-tag>.json`` and are cumulative across
prep sessions for the same base image.

L0 injection
------------
``get_prep_packages(base_image)`` is called from
``orchestration.image.render_all_dockerfiles()`` and the result is used to
inject ``RUN apt-get install …`` and ``RUN pip3 install …`` layers into the
rendered L0 Dockerfile.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.project_model import ProjectConfig

# ---------- Shim scripts ----------

_SHIM_TEMPLATE = """\
#!/bin/sh
# terok prep shim — logs package-manager calls, then execs the real binary.
_cmd="$(basename "$0")"
mkdir -p /tmp/terok-prep-log 2>/dev/null || true
command -v python3 >/dev/null 2>&1 && \\
    python3 -c "import json,sys,os;\\
f=open('/tmp/terok-prep-log/log.jsonl','a');\\
f.write(json.dumps({'cmd':sys.argv[1],'args':sys.argv[2:]})+chr(10));\\
f.close()" "$_cmd" "$@" 2>/dev/null || true
for _dir in /usr/local/sbin /usr/local/bin /usr/sbin /usr/bin /sbin /bin; do
    [ -x "$_dir/$_cmd" ] && exec "$_dir/$_cmd" "$@"
done
echo "terok-prep: cannot find real '$_cmd'" >&2
exit 127
"""

_SHIM_NAMES = ("apt", "apt-get", "dnf", "pip", "pip3")
"""Package manager commands intercepted in a prep container."""

_PROFILE_D_CONTENT = 'export PATH="/terok-shims:$PATH"\n'
"""Profile.d snippet that prepends the shims directory to PATH."""

_CONTAINER_SHIMS_PATH = "/terok-shims"
_CONTAINER_PROFILE_SCRIPT = "/etc/profile.d/terok-prep.sh"
_CONTAINER_LOG_DIR = "/tmp/terok-prep-log"


def prep_dirs_for_task(task_id: str) -> tuple[Path, Path, Path]:
    """Return ``(shims_dir, profile_d_dir, log_dir)`` for *task_id*, creating them.

    All three directories live under ``prep_state_dir()/<task_id>/`` so they
    survive container restarts and can be cleaned up together.
    """
    from ..core.config import prep_state_dir

    session_dir = prep_state_dir() / task_id
    shims_dir = session_dir / "shims"
    profile_d_dir = session_dir / "profile_d"
    log_dir = session_dir / "log"
    for d in (shims_dir, profile_d_dir, log_dir):
        d.mkdir(parents=True, exist_ok=True)
    return shims_dir, profile_d_dir, log_dir


def write_shim_scripts(dest: Path) -> None:
    """Write package-manager shim scripts into *dest*.

    Each shim is an executable shell script named after the intercepted
    command (``apt``, ``apt-get``, ``dnf``, ``pip``, ``pip3``).  All shims
    share the same logic: log the invocation, then exec the real binary.
    """
    dest.mkdir(parents=True, exist_ok=True)
    executable = stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    for name in _SHIM_NAMES:
        script = dest / name
        script.write_text(_SHIM_TEMPLATE)
        script.chmod(executable)


def write_profile_d_script(profile_d_dir: Path) -> Path:
    """Write the profile.d PATH extension script and return its path."""
    script = profile_d_dir / "terok-prep.sh"
    script.write_text(_PROFILE_D_CONTENT)
    return script


# ---------- Log extraction ----------


def extract_prep_log(log_path: Path) -> dict[str, list[str]]:
    """Parse a prep-session JSONL log and return raw package buckets.

    Each line in *log_path* is a JSON object ``{"cmd": "...", "args": [...]}``.
    Only ``install`` sub-invocations are captured; flags (arguments starting
    with ``-``) and the ``"install"`` keyword itself are stripped.

    Returns a dict with keys ``"apt"``, ``"pip"``, ``"dnf"`` — each a list
    of package names (may contain duplicates; call :func:`normalize_packages`
    to deduplicate and sort).
    """
    result: dict[str, list[str]] = {"apt": [], "pip": [], "dnf": []}
    if not log_path.is_file():
        return result

    for raw_line in log_path.read_text().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        cmd = entry.get("cmd", "")
        args = entry.get("args", [])
        if not isinstance(args, list):
            continue

        # Only capture "install" subcommands.
        if "install" not in args:
            continue

        pkgs = [a for a in args if not a.startswith("-") and a != "install"]

        if cmd in ("apt", "apt-get"):
            result["apt"].extend(pkgs)
        elif cmd == "dnf":
            result["dnf"].extend(pkgs)
        elif cmd in ("pip", "pip3") or cmd.startswith("pip3."):
            result["pip"].extend(pkgs)

    return result


def normalize_packages(raw: dict[str, list[str]]) -> dict[str, list[str]]:
    """Deduplicate and sort each package bucket."""
    return {key: sorted(set(vals)) for key, vals in raw.items()}


# ---------- Persistence ----------


def _packages_file(base_image: str) -> Path:
    """Return the path to the JSON packages file for *base_image*."""
    from ..core.config import prep_state_dir
    from ..core.images import _base_tag

    pkg_dir = prep_state_dir() / "packages"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    return pkg_dir / f"{_base_tag(base_image)}.json"


def get_prep_packages(base_image: str) -> dict[str, list[str]]:
    """Return the accumulated prep packages for *base_image*, or empty buckets."""
    f = _packages_file(base_image)
    if not f.is_file():
        return {"apt": [], "pip": [], "dnf": []}
    try:
        return json.loads(f.read_text())
    except (json.JSONDecodeError, OSError):
        return {"apt": [], "pip": [], "dnf": []}


def merge_prep_packages(base_image: str, new_pkgs: dict[str, list[str]]) -> dict[str, list[str]]:
    """Merge *new_pkgs* into the persisted package set for *base_image*.

    The merge is cumulative and idempotent (package names are stored in a
    sorted deduplicated set).  Returns the full merged set.
    """
    existing = get_prep_packages(base_image)
    merged = {
        key: sorted(set(existing.get(key, [])) | set(new_pkgs.get(key, [])))
        for key in ("apt", "pip", "dnf")
    }
    f = _packages_file(base_image)
    f.write_text(json.dumps(merged, indent=2) + "\n")
    return merged


# ---------- Stop-time extraction ----------


def extract_and_merge_prep(project: ProjectConfig, task_id: str) -> None:
    """Read the prep log, merge packages, and print a summary.

    Called by ``_task_stop`` when stopping a prep-mode container.  Safe to
    call even if the log is absent or empty (the user may have stopped
    without installing anything).
    """
    from ..core.config import prep_state_dir

    log_path = prep_state_dir() / task_id / "log" / "log.jsonl"
    raw = extract_prep_log(log_path)
    new_pkgs = normalize_packages(raw)

    total_new = sum(len(v) for v in new_pkgs.values())
    if total_new == 0:
        print("Prep session: no package manager activity recorded.")
        return

    merged = merge_prep_packages(project.base_image, new_pkgs)

    parts = []
    for bucket, label in (("apt", "apt"), ("pip", "pip"), ("dnf", "dnf")):
        count = len(merged[bucket])
        if count:
            parts.append(f"{count} {label} package(s)")

    print(f"Prep session: captured {', '.join(parts)} for '{project.base_image}'.")
    print("These will be included in L0 on the next image build.")
    print(f"Rebuild images with: terok image build {project.id}")


# ---------- Task runner ----------


def task_run_prep(project_id: str, task_id: str) -> None:
    """Launch an interactive prep container with package-manager monitoring.

    The container is functionally identical to a CLI task but with three
    extra bind mounts:

    - ``shims_dir``     → ``/terok-shims``               (RO, package-manager shims)
    - ``profile_script``→ ``/etc/profile.d/terok-prep.sh``(RO, PATH extension)
    - ``log_dir``       → ``/tmp/terok-prep-log``         (RW, captured invocations)

    When the user stops the container with ``terok task stop``, the log is
    automatically extracted and merged into the persistent package set for
    the project's ``base_image``.
    """
    import shlex
    from datetime import UTC, datetime

    from terok_sandbox import Sharing, VolumeSpec

    from ..core import runtime as _rt
    from ..core.images import project_cli_image
    from ..core.projects import load_project
    from ..util.ansi import blue as _blue, green as _green, red as _red, supports_color as _color
    from ..util.yaml import dump as _yaml_dump
    from .environment import build_task_env_and_volumes, ensure_vault
    from .task_runners import _assert_running, _podman_start, _run_container
    from .tasks import container_name, load_task_meta

    project = load_project(project_id)
    meta, meta_path = load_task_meta(project.id, task_id, None)

    cname = container_name(project.id, "prep", task_id)
    container_state = _rt.get_runtime().container(cname).state

    if container_state is not None:
        ensure_vault()
        color = _color()
        if container_state == "running":
            print(f"Prep container {_green(cname, color)} is already running.")
        else:
            print(f"Resuming prep container {_green(cname, color)} …")
            _podman_start(cname)
            _assert_running(cname)
            meta["mode"] = "prep"
            meta["ready_at"] = datetime.now(UTC).isoformat()
            meta_path.write_text(_yaml_dump(meta))
        login_cmd = f"terok login {project_id} {task_id}"
        raw_cmd = shlex.join(_rt.get_runtime().container(cname).login_command(command=("bash",)))
        color = _color()
        print(f"Login with: {_blue(login_cmd, color)}")
        print(f"  (or:      {_blue(raw_cmd, color)})")
        return

    env, volumes = build_task_env_and_volumes(project, task_id)

    shims_dir, profile_d_dir, log_dir = prep_dirs_for_task(task_id)
    write_shim_scripts(shims_dir)
    profile_script = write_profile_d_script(profile_d_dir)

    volumes += [
        VolumeSpec(shims_dir, _CONTAINER_SHIMS_PATH, sharing=Sharing.PRIVATE),
        VolumeSpec(profile_script, _CONTAINER_PROFILE_SCRIPT, sharing=Sharing.PRIVATE),
        VolumeSpec(log_dir, _CONTAINER_LOG_DIR, sharing=Sharing.PRIVATE),
    ]

    task_dir = project.tasks_root / str(task_id)
    _run_container(
        cname=cname,
        image=project_cli_image(project.id),
        env=env,
        volumes=volumes,
        project=project,
        task_id=task_id,
        task_dir=task_dir,
        command=[
            "bash",
            "-lc",
            "init-ssh-and-repo.sh && echo __PREP_READY__; tail -f /dev/null",
        ],
    )

    _rt.get_runtime().container(cname).stream_initial_logs(
        ready_check=lambda line: "__PREP_READY__" in line or ">> init complete" in line,
        timeout_sec=60.0,
    )
    _assert_running(cname)

    meta["mode"] = "prep"
    meta["ready_at"] = datetime.now(UTC).isoformat()
    meta_path.write_text(_yaml_dump(meta))

    color = _color()
    print(f"\nPrep container {_green(cname, color)} is running.")
    login_cmd = f"terok login {project_id} {task_id}"
    raw_cmd = shlex.join(_rt.get_runtime().container(cname).login_command(command=("bash",)))
    print(f"Login with: {_blue(login_cmd, color)}")
    print(f"  (or:      {_blue(raw_cmd, color)})")
    print(
        f"\nInstall packages with apt-get/pip — they will be captured automatically."
        f"\nWhen done: {_red(f'terok task stop {project_id} {task_id}', color)}"
        f"  (captures the log and injects packages into L0)\n"
    )
