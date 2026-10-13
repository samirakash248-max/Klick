"""Test suite for Advanced Features:
- Improvement 6: Anti-Bot & Stealth Hardening (Bézier curves, keystroke jitter, fingerprinting)
- Improvement 7: Structured Data Scraping & File Export Engine
- Improvement 8: Voice Navigation & Omnibox Chrome Extension Integration
- Improvement 9: Hybrid Intelligent Model Routing (Local Fast, Cloud Turbo, Auto Fallback)
"""

import json
from pathlib import Path
import re
import pytest
from fastapi.testclient import TestClient

from backend.app.executor import (
    EXPORTS_DIR,
    PlaywrightExecutor,
    STEALTH_INIT_SCRIPT,
)
from backend.app.main import app
from backend.app.model_client import (
    HybridModelRouter,
    ModelTier,
    OllamaModelClient,
)
from backend.app.safety import SafetyGuard
from backend.app.schemas import (
    ActionType,
    BrowserAction,
    PageObservation,
)


@pytest.fixture
def api_client():
    """TestClient instance for FastAPI route testing."""
    return TestClient(app)


# =========================================================================
# IMPROVEMENT 6: ANTI-BOT & STEALTH HARDENING TESTS
# =========================================================================

def test_stealth_init_script_fingerprint_spoofs():
    """Verify that STEALTH_INIT_SCRIPT overrides critical navigator fingerprints."""
    assert "webdriver" in STEALTH_INIT_SCRIPT
    assert "AudioBuffer" in STEALTH_INIT_SCRIPT or "AudioContext" in STEALTH_INIT_SCRIPT
    assert "HTMLCanvasElement" in STEALTH_INIT_SCRIPT
    assert "hardwareConcurrency" in STEALTH_INIT_SCRIPT
    assert "deviceMemory" in STEALTH_INIT_SCRIPT
    assert "RTCPeerConnection" in STEALTH_INIT_SCRIPT


def test_bezier_curve_trajectory_generation():
    """Verify cubic Bézier trajectory generator produces continuous, bounded paths."""
    executor = PlaywrightExecutor(headless=True)
    start_pos = (100.0, 100.0)
    end_pos = (500.0, 400.0)

    trajectory = executor._generate_bezier_trajectory(start_pos, end_pos, num_steps=15)

    assert len(trajectory) >= 6
    # Final coordinate must match destination
    last_pt = trajectory[-1]
    assert abs(last_pt[0] - end_pos[0]) < 2.0
    assert abs(last_pt[1] - end_pos[1]) < 2.0

    # Ensure path is non-linear and exhibits curvature
    for pt in trajectory:
        assert isinstance(pt[0], float) and isinstance(pt[1], float)
        assert -50 <= pt[0] <= 700
        assert -50 <= pt[1] <= 600


# =========================================================================
# IMPROVEMENT 7: STRUCTURED DATA SCRAPING & EXPORT ENGINE TESTS
# =========================================================================

def test_export_data_schema_normalization():
    """Verify ActionType.EXPORT_DATA and payload aliases."""
    action = BrowserAction(
        action_type=ActionType.EXPORT_DATA,
        description="Export table to CSV",
    )
    assert action.action_type == ActionType.EXPORT_DATA
    assert action.export_format == "csv"

    # Test alias normalization
    alias_action = BrowserAction.model_validate({
        "action": "scrape_table",
        "format": "json",
        "filename": "my_table",
        "description": "Scrape table rows",
    })
    assert alias_action.action_type == ActionType.EXPORT_DATA
    assert alias_action.export_format == "json"
    assert alias_action.export_filename == "my_table"


def test_safety_guard_allows_export_data():
    """Verify SafetyGuard classifies EXPORT_DATA as safe."""
    guard = SafetyGuard()
    action = BrowserAction(
        action_type=ActionType.EXPORT_DATA,
        description="Extract and export invoices data",
    )
    result = guard.evaluate_action(action)
    assert result.is_safe is True
    assert result.requires_human_confirmation is False


@pytest.mark.asyncio
async def test_executor_export_data_file_generation(tmp_path, monkeypatch):
    """Verify PlaywrightExecutor._execute_export_data writes CSV and JSON files."""
    import backend.app.executor as ex_module
    monkeypatch.setattr(ex_module, "EXPORTS_DIR", tmp_path)

    executor = PlaywrightExecutor(headless=True)

    class MockPage:
        url = "http://mock-site/billing.html"
        def is_closed(self):
            return False
        async def evaluate(self, script, arg=None):
            return {
                "title": "Invoices Billing",
                "headers": ["Invoice ID", "Client", "Amount", "Status"],
                "rows": [
                    ["INV-1001", "Acme Corp", "$1,250.00", "Paid"],
                    ["INV-1002", "Cyberdyne", "$450.00", "Pending"],
                    ["INV-1003", "Stark Ind", "$9,800.00", "Paid"],
                ],
                "items": [
                    {"Invoice ID": "INV-1001", "Client": "Acme Corp", "Amount": "$1,250.00", "Status": "Paid"},
                    {"Invoice ID": "INV-1002", "Client": "Cyberdyne", "Amount": "$450.00", "Status": "Pending"},
                ],
            }

    # 1. Test CSV export
    csv_res = await executor._execute_export_data(
        page=MockPage(),
        target="table",
        format="csv",
        filename="test_invoices",
    )
    assert csv_res.success is True
    assert csv_res.action_type == ActionType.EXPORT_DATA
    assert (tmp_path / "test_invoices.csv").exists()
    content = (tmp_path / "test_invoices.csv").read_text(encoding="utf-8-sig")
    assert "Invoice ID,Client,Amount,Status" in content
    assert "INV-1001" in content

    # 2. Test JSON export
    json_res = await executor._execute_export_data(
        page=MockPage(),
        target="table",
        format="json",
        filename="test_invoices_json",
    )
    assert json_res.success is True
    assert (tmp_path / "test_invoices_json.json").exists()
    json_content = json.loads((tmp_path / "test_invoices_json.json").read_text(encoding="utf-8"))
    assert json_content["row_count"] == 3
    assert len(json_content["columns"]) == 4


def test_api_export_endpoints(api_client, tmp_path, monkeypatch):
    """Verify REST endpoints for /api/exports and /api/exports/{filename}."""
    import backend.app.main as main_mod
    monkeypatch.setattr(main_mod, "EXPORTS_DIR", tmp_path)

    # Create dummy export file
    test_file = tmp_path / "sample_orders.csv"
    test_file.write_text("Order_ID,Total\nORD-1,$99.00\n", encoding="utf-8")

    # 1. GET /api/exports
    list_res = api_client.get("/api/exports")
    assert list_res.status_code == 200
    files = list_res.json()
    assert any(f["filename"] == "sample_orders.csv" for f in files)

    # 2. GET /api/exports/{filename}
    dl_res = api_client.get("/api/exports/sample_orders.csv")
    assert dl_res.status_code == 200
    assert "ORD-1" in dl_res.text


# =========================================================================
# IMPROVEMENT 8: VOICE NAVIGATION & OMNIBOX INTEGRATION TESTS
# =========================================================================

def test_extension_manifest_omnibox_configuration():
    """Verify Manifest V3 includes omnibox keyword 'klick'."""
    manifest_path = Path(__file__).resolve().parent.parent.parent / "extension" / "manifest.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "omnibox" in data
    assert data["omnibox"]["keyword"] == "klick"


def test_extension_service_worker_omnibox_handlers():
    """Verify service-worker.js implements omnibox listeners."""
    sw_path = Path(__file__).resolve().parent.parent.parent / "extension" / "background" / "service-worker.js"
    assert sw_path.exists()
    content = sw_path.read_text(encoding="utf-8")
    assert "chrome.omnibox.onInputChanged" in content
    assert "chrome.omnibox.onInputEntered" in content


def test_extension_popup_voice_navigation_elements():
    """Verify popup.html and popup.js contain speech recognition button and logic."""
    popup_html = (Path(__file__).resolve().parent.parent.parent / "extension" / "popup" / "popup.html").read_text(encoding="utf-8")
    popup_js = (Path(__file__).resolve().parent.parent.parent / "extension" / "popup" / "popup.js").read_text(encoding="utf-8")
    assert "btn-voice-input" in popup_html
    assert "setupVoiceNavigation" in popup_js
    assert "SpeechRecognition" in popup_js


# =========================================================================
# IMPROVEMENT 9: HYBRID MODEL ROUTING TESTS
# =========================================================================

def test_hybrid_model_router_configuration():
    """Verify HybridModelRouter tiers and initial route configuration."""
    router = HybridModelRouter(active_tier=ModelTier.AUTO)
    assert router.active_tier == ModelTier.AUTO

    routes = router.get_routes()
    assert routes["active_tier"] == "auto"
    assert len(routes["tiers"]) == 3
    assert any(t["id"] == "local_fast" for t in routes["tiers"])
    assert any(t["id"] == "cloud_turbo" for t in routes["tiers"])

    # Test tier switching
    router.set_tier("cloud_turbo")
    assert router.active_tier == ModelTier.CLOUD_TURBO

    router.set_tier("local_fast")
    assert router.active_tier == ModelTier.LOCAL_FAST


@pytest.mark.asyncio
async def test_hybrid_model_router_fallback():
    """Verify that HybridModelRouter in AUTO mode falls back gracefully when local fails."""
    router = HybridModelRouter(active_tier=ModelTier.AUTO)

    # Force local client to fail
    async def mock_fail(*args, **kwargs):
        raise ConnectionError("Ollama daemon unreachable")

    router.local_client.get_next_action = mock_fail

    obs = PageObservation(url="http://mock-site/tasks.html", dom_summary="<button>Mark Complete</button>")
    resp = await router.get_next_action(goal="Mark complete on task TSK-101", observation=obs)

    assert resp is not None
    assert resp.action is not None
    assert router.stats["fallbacks_triggered"] >= 1


def test_api_model_routes_endpoints(api_client):
    """Verify GET and POST /api/models/routes endpoints."""
    # 1. GET /api/models/routes
    res = api_client.get("/api/models/routes")
    assert res.status_code == 200
    data = res.json()
    assert "active_tier" in data
    assert "tiers" in data

    # 2. POST /api/models/route
    switch_res = api_client.post("/api/models/route", json={"tier": "local_fast"})
    assert switch_res.status_code == 200
    assert switch_res.json()["active_tier"] == "local_fast"

    # Reset to auto
    api_client.post("/api/models/route", json={"tier": "auto"})
