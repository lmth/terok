# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the CLI entry point and ``terok tui`` subcommand."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

import pytest


def _run_cli(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run the terok CLI in a subprocess."""
    return subprocess.run(
        [sys.executable, "-m", "terok.cli.main", *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=10,
    )


class TestCliProgName:
    """Verify the CLI identifies itself as ``terok`` (not ``terokctl``)."""

    def test_help_shows_terok(self) -> None:
        """Root --help uses ``terok`` as the program name."""
        result = _run_cli("--help")
        assert result.returncode == 0
        assert "terok" in result.stdout
        assert "terokctl" not in result.stdout

    def test_version_shows_terok(self) -> None:
        """--version output starts with ``terok``."""
        result = _run_cli("--version")
        assert result.returncode == 0
        assert result.stdout.startswith("terok ")


class TestTuiSubcommand:
    """Verify the ``terok tui`` subcommand dispatches correctly."""

    def test_tui_subcommand_execs_terok_tui(self) -> None:
        """``terok tui`` calls os.execlp with ``terok-tui``."""
        with patch("os.execlp") as mock_exec:
            # Import after patching to avoid actual exec
            from terok.cli.main import main

            with patch("sys.argv", ["terok", "tui"]):
                main()

            mock_exec.assert_called_once()
            args = mock_exec.call_args[0]
            assert args[0] == "terok-tui"
            assert args[1] == "terok-tui"

    def test_tui_subcommand_forwards_args(self) -> None:
        """``terok tui --tmux`` forwards --tmux to terok-tui."""
        with patch("os.execlp") as mock_exec:
            from terok.cli.main import main

            with patch("sys.argv", ["terok", "tui", "--tmux"]):
                main()

            args = mock_exec.call_args[0]
            assert args == ("terok-tui", "terok-tui", "--tmux")

    def test_tui_subcommand_forwards_multiple_args(self) -> None:
        """``terok tui --no-tmux --experimental`` forwards all args."""
        with patch("os.execlp") as mock_exec:
            from terok.cli.main import main

            with patch("sys.argv", ["terok", "tui", "--no-tmux", "--experimental"]):
                main()

            args = mock_exec.call_args[0]
            assert args == ("terok-tui", "terok-tui", "--no-tmux", "--experimental")

    def test_tui_listed_in_help(self) -> None:
        """``terok --help`` lists ``tui`` as a subcommand."""
        result = _run_cli("--help")
        assert "tui" in result.stdout


class TestEmojiFlag:
    """Verify --no-emoji flag propagation."""

    def test_no_emoji_flag_accepted(self) -> None:
        """``terok --no-emoji config paths`` does not error on the flag."""
        result = _run_cli("--no-emoji", "config", "paths")
        assert result.returncode == 0


class TestCompletionHint:
    """Verify epilog mentions completion install command."""

    def test_epilog_mentions_terok_completions(self) -> None:
        """Help epilog references ``terok completions install``."""
        result = _run_cli("--help")
        assert "terok completions install" in result.stdout


@pytest.mark.parametrize(
    "subcmd",
    ["task", "project", "config", "sickbay", "tui"],
    ids=["task", "project", "config", "sickbay", "tui"],
)
def test_known_subcommands_appear_in_help(subcmd: str) -> None:
    """Core subcommands are listed in ``terok --help``."""
    result = _run_cli("--help")
    assert subcmd in result.stdout


def _run_terokctl(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI as ``terokctl`` by invoking the dedicated entry point."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from terok.cli.main import terokctl_main; terokctl_main()",
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestTerokctlSurface:
    """``terokctl`` is the scriptable surface — same tree, different branding."""

    def test_help_shows_terokctl(self) -> None:
        """``terokctl --help`` uses ``terokctl`` as the program name."""
        result = _run_terokctl("--help")
        assert result.returncode == 0
        assert "terokctl" in result.stdout

    def test_version_shows_terokctl(self) -> None:
        """``terokctl --version`` starts with ``terokctl``."""
        result = _run_terokctl("--version")
        assert result.returncode == 0
        assert result.stdout.startswith("terokctl ")

    def test_epilog_uses_terokctl_prog(self) -> None:
        """Quick-start examples in the epilog reference ``terokctl``, not ``terok``."""
        result = _run_terokctl("--help")
        assert "terokctl setup" in result.stdout
        assert "terokctl completions install" in result.stdout

    def test_known_subcommands_appear(self) -> None:
        """Core subcommands are listed — command tree is identical to ``terok``."""
        result = _run_terokctl("--help")
        for subcmd in ("task", "project", "config", "sickbay", "tui"):
            assert subcmd in result.stdout


class TestTuiOnNoArgs:
    """Bare ``terok`` in a terminal execs ``terok-tui``; scripts get help."""

    def test_tty_no_args_execs_terok_tui(self) -> None:
        """``terok`` with no args and a TTY on stdin/stdout execs the TUI."""
        with (
            patch("os.execlp") as mock_exec,
            patch("sys.argv", ["terok"]),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            from terok.cli.main import main

            main()

            mock_exec.assert_called_once_with("terok-tui", "terok-tui")

    def test_non_tty_no_args_falls_through_to_argparse(self) -> None:
        """Without a TTY, bare ``terok`` errors out — automation-safe default."""
        with (
            patch("os.execlp") as mock_exec,
            patch("sys.argv", ["terok"]),
            patch("sys.stdin.isatty", return_value=False),
            patch("sys.stdout.isatty", return_value=False),
            pytest.raises(SystemExit),
        ):
            from terok.cli.main import main

            main()

        # Assert outside the ``with`` — pytest.raises short-circuits the block
        # the moment SystemExit fires, so anything above this line never ran.
        mock_exec.assert_not_called()

    def test_missing_terok_tui_falls_through_to_argparse(self) -> None:
        """If ``terok-tui`` isn't on PATH, argparse's usage error is the fallback."""
        with (
            patch("os.execlp", side_effect=FileNotFoundError) as mock_exec,
            patch("sys.argv", ["terok"]),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            pytest.raises(SystemExit),
        ):
            from terok.cli.main import main

            main()

        mock_exec.assert_called_once_with("terok-tui", "terok-tui")

    def test_terokctl_no_args_never_launches_tui(self) -> None:
        """``terokctl`` is the stable surface — no-args always prints usage."""
        with (
            patch("os.execlp") as mock_exec,
            patch("sys.argv", ["terokctl"]),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            pytest.raises(SystemExit),
        ):
            from terok.cli.main import terokctl_main

            terokctl_main()

        mock_exec.assert_not_called()
