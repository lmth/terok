# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for SSH project initialization helpers."""

from __future__ import annotations

import os
import tempfile
import unittest.mock
from pathlib import Path

from terok.lib.core.projects import load_project
from terok.lib.domain.project import make_ssh_manager
from tests.test_utils import mock_git_config, write_project


def make_ssh_project(base: Path, project_id: str) -> tuple[Path, Path]:
    """Create a project config and SSH host directory for tests."""
    config_base = base / "config"
    projects_root = config_base / "projects"
    ssh_dir = base / "ssh"
    projects_root.mkdir(parents=True, exist_ok=True)
    ssh_dir.mkdir(parents=True, exist_ok=True)
    write_project(
        projects_root,
        project_id,
        f"project:\n  id: {project_id}\nssh:\n  host_dir: {ssh_dir}\n",
    )
    return config_base, ssh_dir


def write_keypair(ssh_dir: Path, key_name: str) -> None:
    """Write a dummy SSH keypair to the given directory."""
    (ssh_dir / key_name).write_text("dummy", encoding="utf-8")
    (ssh_dir / f"{key_name}.pub").write_text("dummy", encoding="utf-8")


class TestRegisterSshKey:
    """Tests for register_ssh_key() in the domain facade."""

    def test_register_calls_update_ssh_keys_json(self) -> None:
        """register_ssh_key must delegate to update_ssh_keys_json with the right args."""
        fake_result = {
            "private_key": "/tmp/terok-testing/ssh/id_ed25519_proj",
            "dir": "/tmp/terok-testing/ssh",
        }
        with (
            unittest.mock.patch("terok_sandbox.update_ssh_keys_json") as m_update,
            unittest.mock.patch("terok.lib.core.config.make_sandbox_config") as m_cfg,
        ):
            m_cfg.return_value.ssh_keys_json_path = Path("/tmp/terok-testing/ssh-keys.json")
            from terok.lib.domain.facade import register_ssh_key

            register_ssh_key("myproj", fake_result)

        m_update.assert_called_once_with(
            Path("/tmp/terok-testing/ssh-keys.json"), "myproj", fake_result
        )

    def test_register_propagates_errors(self) -> None:
        """Errors from update_ssh_keys_json must propagate (no silent swallowing)."""
        with (
            unittest.mock.patch(
                "terok_sandbox.update_ssh_keys_json", side_effect=OSError("disk full")
            ),
            unittest.mock.patch("terok.lib.core.config.make_sandbox_config") as m_cfg,
        ):
            m_cfg.return_value.ssh_keys_json_path = Path("/tmp/terok-testing/ssh-keys.json")
            import pytest

            from terok.lib.domain.facade import register_ssh_key

            with pytest.raises(OSError, match="disk full"):
                register_ssh_key("proj", {"private_key": "/tmp/terok-testing/k"})


class TestSsh:
    """Tests for SSHManager init behavior."""

    def test_init_project_ssh_uses_existing_keys(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_id = "proj5"
            config_root, ssh_dir = make_ssh_project(Path(td), project_id)
            key_name = "id_test"
            write_keypair(ssh_dir, key_name)

            with (
                unittest.mock.patch.dict(os.environ, {"TEROK_CONFIG_DIR": str(config_root)}),
                mock_git_config(),
                unittest.mock.patch("terok_sandbox.credentials.ssh.subprocess.run") as run_mock,
            ):
                result = make_ssh_manager(load_project(project_id)).init(key_name=key_name)

            run_mock.assert_not_called()
            config_path = Path(result["config_path"])
            assert config_path.is_file()
            assert f"IdentityFile ~/.ssh/{key_name}" in config_path.read_text(encoding="utf-8")

    def test_init_project_ssh_without_key_name_does_not_print_default_warning(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_id = "proj6"
            config_root, ssh_dir = make_ssh_project(Path(td), project_id)
            write_keypair(ssh_dir, f"id_ed25519_{project_id}")

            with (
                unittest.mock.patch.dict(os.environ, {"TEROK_CONFIG_DIR": str(config_root)}),
                mock_git_config(),
                unittest.mock.patch("terok_sandbox.credentials.ssh.subprocess.run") as run_mock,
                unittest.mock.patch("builtins.print") as print_mock,
            ):
                make_ssh_manager(load_project(project_id)).init()

            run_mock.assert_not_called()
            printed_lines = [
                " ".join(str(part) for part in call.args) for call in print_mock.call_args_list
            ]
            assert not any("does not define ssh.key_name" in line for line in printed_lines)
