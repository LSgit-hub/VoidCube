from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from VoidCube_cli.entrypoint_operations import cmd_body
from VoidCube_cli.main import main as cli_main


@pytest.mark.unit
def test_body_status_command_uses_executor_ops_client(capsys):
    client = Mock()
    client.get_body_registry.return_value = {"registry": {"active_slot": "slot-A"}}
    client.get_active_body_target.return_value = {"slot_id": "slot-A"}

    with patch("VoidCube_cli.ops.executor.ExecutorOpsClient", return_value=client):
        cmd_body(
            SimpleNamespace(
                body_action="status",
                gateway_url="http://gateway.local",
                timeout=5.0,
            )
        )

    output = capsys.readouterr().out
    assert '"active_slot": "slot-A"' in output
    assert '"slot_id": "slot-A"' in output
    client.get_body_registry.assert_called_once_with()
    client.get_active_body_target.assert_called_once_with()


@pytest.mark.unit
def test_body_upgrade_command_uses_executor_ops_client(capsys):
    client = Mock()
    client.execute_body_upgrade.return_value = {"status": "upgrade_awaiting_user_consent"}

    with patch("VoidCube_cli.ops.executor.ExecutorOpsClient", return_value=client):
        cmd_body(
            SimpleNamespace(
                body_action="upgrade",
                body_version="v2",
                watch_window_seconds=120,
                gateway_url="http://gateway.local",
                timeout=5.0,
            )
        )

    output = capsys.readouterr().out
    assert '"status": "upgrade_awaiting_user_consent"' in output
    client.execute_body_upgrade.assert_called_once_with(
        {
            "body_version": "v2",
            "watch_window_seconds": 120,
        }
    )


@pytest.mark.unit
def test_body_consent_command_uses_executor_ops_client(capsys):
    client = Mock()
    client.confirm_body_switch.return_value = {"status": "body_switch_activated"}

    with patch("VoidCube_cli.ops.executor.ExecutorOpsClient", return_value=client):
        cmd_body(
            SimpleNamespace(
                body_action="consent",
                slot_id="slot-B",
                watch_window_seconds=120,
                gateway_url="http://gateway.local",
                timeout=5.0,
            )
        )

    output = capsys.readouterr().out
    assert '"status": "body_switch_activated"' in output
    client.confirm_body_switch.assert_called_once_with(
        {
            "slot_id": "slot-B",
            "approved": True,
            "watch_window_seconds": 120,
        }
    )


@pytest.mark.unit
def test_cli_help_describes_gateway_executor_as_required_surface(capsys):
    with patch.object(sys, "argv", ["VoidCube", "body", "--help"]):
        with pytest.raises(SystemExit):
            cli_main()

    output = capsys.readouterr().out
    assert "gateway /api/executor" in output
    assert "supervisor fallback" not in output


@pytest.mark.unit
def test_body_command_fails_closed_with_clear_executor_error(capsys):
    client = Mock()
    client.get_body_registry.side_effect = requests.ConnectionError("executor route unavailable")

    with patch("VoidCube_cli.ops.executor.ExecutorOpsClient", return_value=client):
        with pytest.raises(SystemExit) as excinfo:
            cmd_body(
                SimpleNamespace(
                    body_action="status",
                    gateway_url="http://gateway.local",
                    timeout=5.0,
                )
            )

    assert excinfo.value.code == 1
    error_output = capsys.readouterr().err
    assert "Body command failed: executor route unavailable." in error_output
    assert "Check the gateway / executor chain and try again." in error_output
