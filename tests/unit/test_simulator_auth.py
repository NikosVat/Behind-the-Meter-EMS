from unittest.mock import MagicMock

from simulator.cli import run_simulation


def test_simulator_uses_environment_key_without_logging_it(monkeypatch):
    monkeypatch.setenv("API_KEY", "test-secret")
    client = MagicMock()
    client.post.return_value.status_code = 200
    logs = []
    run_simulation(url="http://localhost:8000/api/v1/telemetry", max_readings=1,
                   http_client=client, sleep_fn=lambda _: None, log_fn=logs.append)
    assert client.post.call_args.kwargs["headers"]["X-API-Key"] == "test-secret"
    assert all("test-secret" not in line for line in logs)
