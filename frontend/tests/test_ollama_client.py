"""Unit tests for direct OllamaService playground client with mocked HTTP interactions."""

import httpx
import pytest
from unittest.mock import MagicMock, patch

from frontend.services.ollama_client import OllamaService


@pytest.fixture
def service():
    return OllamaService(
        base_url="http://mock-ollama:11434",
        default_model="llama3.2:3b",
        timeout=5.0,
    )


def test_check_health_success(service):
    """Test successful health check against /api/tags."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "models": [{"name": "llama3.2:3b"}, {"name": "llama3:8b"}],
    }

    with patch.object(httpx.Client, "get", return_value=mock_resp):
        res = service.check_health()
        assert res["online"] is True
        assert "llama3.2:3b" in res["models"]
        assert res["model_present"] is True


def test_check_health_model_missing(service):
    """Test health check reports model_present=False when model is not installed."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "models": [{"name": "mistral:7b"}],
    }

    with patch.object(httpx.Client, "get", return_value=mock_resp):
        res = service.check_health()
        assert res["online"] is True
        assert res["model_present"] is False


def test_check_health_connection_failure(service):
    """Test Ollama connection failure handling."""
    with patch.object(httpx.Client, "get", side_effect=httpx.ConnectError("Connection refused")):
        res = service.check_health()
        assert res["online"] is False
        assert "Could not connect to Ollama" in res["error"]


def test_generate_success(service):
    """Test successful model generation and JSON parsing."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "response": '{"thought": {"reflection": "ready"}, "action": {"action_type": "click"}}',
        "eval_count": 42,
    }

    with patch.object(httpx.Client, "post", return_value=mock_resp):
        res = service.generate("Test prompt", format_json=True)
        assert res["success"] is True
        assert res["parsed_json"]["thought"]["reflection"] == "ready"
        assert res["parsed_json"]["action"]["action_type"] == "click"


def test_generate_model_not_found_404(service):
    """Test 404 response for nonexistent model returns clear pull instruction."""
    mock_resp = MagicMock(status_code=404, text="model not found")
    with patch.object(httpx.Client, "post", return_value=mock_resp):
        res = service.generate("Test prompt", model="nonexistent:model")
        assert res["success"] is False
        assert "not found in Ollama" in res["error"]
        assert "ollama pull" in res["error"]


def test_generate_timeout(service):
    """Test generation request timeout produces clean error."""
    with patch.object(httpx.Client, "post", side_effect=httpx.TimeoutException("Timed out")):
        res = service.generate("Long task", timeout=2.0)
        assert res["success"] is False
        assert "timed out" in res["error"].lower()


def test_test_structured_action_valid_schema(service):
    """Test schema verification passes when model produces compliant JSON."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "response": (
            '{"thought": {"reflection": "Observing invoice row", "reasoning": "Need to click view"}, '
            '"action": {"action_type": "click", "selector": "#view-btn", "description": "Click view button"}}'
        ),
    }

    with patch.object(httpx.Client, "post", return_value=mock_resp):
        res = service.test_structured_action("Find invoice")
        assert res["success"] is True
        assert res["schema_valid"] is True
        assert res["validation_error"] is None
        assert res["parsed_action"]["action_type"] == "click"


def test_test_structured_action_invalid_json(service):
    """Test schema verification handles malformed non-JSON output."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "response": "Here is what I think you should do: Click the button.",
    }

    with patch.object(httpx.Client, "post", return_value=mock_resp):
        res = service.test_structured_action("Find invoice")
        assert res["success"] is True
        assert res["schema_valid"] is False
        assert "not valid JSON" in res["validation_error"]


def test_test_structured_action_missing_keys(service):
    """Test schema verification flags JSON missing required 'thought' or 'action' keys."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "response": '{"output": "Click button"}',
    }

    with patch.object(httpx.Client, "post", return_value=mock_resp):
        res = service.test_structured_action("Find invoice")
        assert res["success"] is True
        assert res["schema_valid"] is False
        assert "missing required" in res["validation_error"]
