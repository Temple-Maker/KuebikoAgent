"""The command dispatches, and unwritten commands say so with exit code 4."""

from __future__ import annotations

import pytest

from kuebiko import __version__, cli


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_is_a_usage_error() -> None:
    assert cli.main([]) == cli.EXIT_USAGE


@pytest.mark.parametrize("command", sorted(cli.COMMANDS))
def test_unwritten_commands_exit_4(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    if cli._load(cli.COMMANDS[command][0]) is not None:
        pytest.skip(f"{command} is implemented")
    assert cli.main([command, "anything", "--flag"]) == cli.EXIT_NOT_IMPLEMENTED
    assert "not implemented yet" in capsys.readouterr().err
