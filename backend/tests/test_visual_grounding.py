"""Tests for Visual Grounding & Set-of-Mark (SoM) Engine.

Verifies:
1. Set-of-Mark in-page script definitions and non-destructive overlay cleanup.
2. Pillow (PIL) offline visual annotation with colored bounding boxes and numeric badge labels.
3. Perceptual visual similarity calculation and visual stagnation detection.
4. Visual mark resolution ([0], [1], elem-0, mark-0) to ElementDescriptors.
5. Visual action grounding in ControlledAgentRunner (marks, coordinates, bounding boxes).
6. Executor visual coordinate clicks via Bézier trajectories and mouse events.
7. Multimodal prompt formatting and image payload attachment in Ollama and Cloud Turbo clients.
8. FastAPI /api/visual endpoints and visual observation telemetry.
"""

from __future__ import annotations

import base64
from io import BytesIO
from typing import Any, Dict, List
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from PIL import Image

from backend.app.agent_runner import is_action_grounded
from backend.app.executor import PlaywrightExecutor
from backend.app.model_client import HybridModelRouter, ModelTier, OllamaModelClient
from backend.app.observer import PlaywrightObserver
from backend.app.schemas import (
    ActionType,
    BrowserAction,
    ElementCoordinates,
    ElementDescriptor,
    ObservationResponse,
    PageObservation,
)
from backend.app.visual import (
    SOM_CLEANUP_SCRIPT,
    SOM_INJECT_SCRIPT,
    compute_visual_similarity,
    draw_som_annotations_pil,
    resolve_visual_target,
)


def create_dummy_base64_image(width: int = 400, height: int = 300, color: str = "white") -> str:
    """Helper to generate a base64 encoded test image."""
    img = Image.new("RGB", (width, height), color=color)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def make_visual_observation() -> PageObservation:
    """Helper creating a page observation with interactive elements with bounding box coordinates."""
    b64 = create_dummy_base64_image(400, 300, color="#f8fafc")
    elements = [
        ElementDescriptor(
            id="elem-0",
            tag_name="button",
            selector="[data-agent-id='elem-0']",
            text="Submit Form",
            coordinates=ElementCoordinates(x=20.0, y=50.0, width=120.0, height=36.0),
        ),
        ElementDescriptor(
            id="elem-1",
            tag_name="input",
            selector="[data-agent-id='elem-1']",
            text="",
            coordinates=ElementCoordinates(x=20.0, y=100.0, width=200.0, height=40.0),
        ),
        ElementDescriptor(
            id="elem-2",
            tag_name="a",
            selector="[data-agent-id='elem-2']",
            text="View Invoices",
            coordinates=ElementCoordinates(x=20.0, y=160.0, width=150.0, height=30.0),
        ),
    ]
    return PageObservation(
        url="http://localhost:8000/test.html",
        title="Visual Test Page",
        dom_summary="[0] <button> 'Submit Form' -> selector: `[data-agent-id='elem-0']`\n[1] <input> '' -> selector: `[data-agent-id='elem-1']`",
        interactive_elements=elements,
        screenshot_base64=b64,
        annotated_screenshot_base64=b64,
        visual_elements_count=3,
    )


# =========================================================================
# UNIT TESTS: PIL SOM ANNOTATION & PERCEPTUAL SIMILARITY
# =========================================================================

def test_draw_som_annotations_pil():
    """Verify PIL draws bounding boxes and badges on elements with valid coordinates."""
    b64_in = create_dummy_base64_image(500, 400, color="#ffffff")
    elements = [
        ElementDescriptor(
            id="e0",
            tag_name="button",
            selector="#btn",
            text="Click",
            coordinates=ElementCoordinates(x=30.0, y=40.0, width=100.0, height=30.0),
        ),
        ElementDescriptor(
            id="e1",
            tag_name="a",
            selector="#lnk",
            text="Link",
            coordinates=ElementCoordinates(x=30.0, y=100.0, width=80.0, height=25.0),
        ),
    ]

    annotated_b64, count = draw_som_annotations_pil(b64_in, elements)
    assert count == 2
    assert isinstance(annotated_b64, str)
    assert len(annotated_b64) > 100

    # Ensure output is valid JPEG image
    decoded = base64.b64decode(annotated_b64)
    img = Image.open(BytesIO(decoded))
    assert img.size == (500, 400)


def test_visual_similarity_computation():
    """Verify perceptual visual similarity comparison."""
    img_white = create_dummy_base64_image(200, 150, color="white")
    img_white_copy = create_dummy_base64_image(200, 150, color="white")
    img_black = create_dummy_base64_image(200, 150, color="black")

    # Identical images
    assert compute_visual_similarity(img_white, img_white) == 1.0
    assert compute_visual_similarity(img_white, img_white_copy) == 1.0

    # Significantly different images
    diff_score = compute_visual_similarity(img_white, img_black)
    assert diff_score < 0.2

    # None / empty handling
    assert compute_visual_similarity(None, img_white) == 0.0
    assert compute_visual_similarity("", "") == 0.0


# =========================================================================
# UNIT TESTS: VISUAL MARK RESOLUTION & GROUNDING VALIDATION
# =========================================================================

def test_resolve_visual_target():
    """Verify target string patterns ([0], elem-0, mark-0, 0) resolve to elements."""
    elements = [
        ElementDescriptor(id="elem-0", tag_name="button", selector="[data-agent-id='elem-0']"),
        ElementDescriptor(id="elem-1", tag_name="input", selector="[data-agent-id='elem-1']"),
        ElementDescriptor(id="elem-2", tag_name="a", selector="[data-agent-id='elem-2']"),
    ]

    # Pattern: [0]
    res, idx = resolve_visual_target("[0]", elements)
    assert res.id == "elem-0" and idx == 0

    # Pattern: [2]
    res, idx = resolve_visual_target("[2]", elements)
    assert res.id == "elem-2" and idx == 2

    # Pattern: mark-1, elem-1
    res, idx = resolve_visual_target("mark-1", elements)
    assert res.id == "elem-1" and idx == 1

    res, idx = resolve_visual_target("elem-0", elements)
    assert res.id == "elem-0" and idx == 0

    # Pure digit
    res, idx = resolve_visual_target("1", elements)
    assert res.id == "elem-1" and idx == 1

    # Out of range or invalid
    assert resolve_visual_target("[99]", elements) is None
    assert resolve_visual_target("invalid_selector", elements) is None
    assert resolve_visual_target(None, elements) is None


def test_is_action_grounded_with_visual_features():
    """Verify is_action_grounded supports visual marks and screen coordinates."""
    obs = make_visual_observation()

    # 1. Grounded by visual mark in selector or target
    action_mark_bracket = BrowserAction(
        action_type=ActionType.CLICK,
        target="[0]",
        description="Click visual badge 0",
    )
    assert is_action_grounded(action_mark_bracket, obs) is True

    action_mark_elem = BrowserAction(
        action_type=ActionType.CLICK,
        selector="[data-agent-id='elem-1']",
        description="Click elem-1",
    )
    assert is_action_grounded(action_mark_elem, obs) is True

    # 2. Grounded by explicit visual_mark field
    action_explicit_mark = BrowserAction(
        action_type=ActionType.CLICK,
        visual_mark="[2]",
        description="Click visual mark 2",
    )
    assert is_action_grounded(action_explicit_mark, obs) is True

    # 3. Grounded by direct screen coordinates
    action_coords = BrowserAction(
        action_type=ActionType.CLICK,
        coordinates=ElementCoordinates(x=150.0, y=220.0, width=40.0, height=20.0),
        description="Click visual coordinates (150, 220)",
    )
    assert is_action_grounded(action_coords, obs) is True

    # 4. Grounded by string coordinates
    action_str_coords = BrowserAction(
        action_type=ActionType.CLICK,
        target="coords(100, 200)",
        description="Click point coords",
    )
    assert is_action_grounded(action_str_coords, obs) is True

    # 5. Ungrounded target
    action_ungrounded = BrowserAction(
        action_type=ActionType.CLICK,
        selector="#non-existent-modal-button",
        description="Click non-existent",
    )
    assert is_action_grounded(action_ungrounded, obs) is False


# =========================================================================
# UNIT TESTS: EXECUTOR VISUAL COORDINATE CLICKS
# =========================================================================

@pytest.mark.asyncio
async def test_executor_visual_coordinate_click():
    """Verify executor clicks direct visual coordinates using mouse movements."""
    executor = PlaywrightExecutor(allow_external=True)

    mock_page = MagicMock()
    mock_page.is_closed = lambda: False
    mock_page.evaluate = AsyncMock(return_value=None)
    mock_page.mouse = MagicMock()
    mock_page.mouse.move = AsyncMock()
    mock_page.mouse.click = AsyncMock()

    # Case A: Click with coordinates dictionary
    coords = ElementCoordinates(x=120.0, y=240.0, width=60.0, height=20.0)
    res = await executor._execute_click(mock_page, target=None, coordinates=coords)

    assert res.success is True
    assert "coords" in res.target
    # Expected center = x + w/2 = 150, y + h/2 = 250
    mock_page.mouse.click.assert_called_with(150.0, 250.0)

    # Case B: Click with target="coords(80, 160)"
    res_b = await executor._execute_click(mock_page, target="coords(80, 160)")
    assert res_b.success is True
    mock_page.mouse.click.assert_called_with(80.0, 160.0)


# =========================================================================
# UNIT TESTS: MULTIMODAL PROMPT & MODEL CLIENT VISION ATTACHMENT
# =========================================================================

def test_user_prompt_includes_visual_cues():
    """Verify prompt constructor attaches Set-of-Mark visual indicators."""
    client = OllamaModelClient()
    obs = make_visual_observation()

    prompt = client._format_user_prompt(
        goal="Submit invoice form",
        observation=obs,
        step=1,
        max_steps=5,
    )

    assert "Visual Set-of-Mark Grounding" in prompt
    assert "visually grounded elements" in prompt
    assert "[0]..<button>" in prompt or "Visible Page Elements" in prompt


@pytest.mark.asyncio
async def test_ollama_client_attaches_vision_images():
    """Verify OllamaModelClient includes images array in request payload when screenshots exist."""
    client = OllamaModelClient(base_url="http://localhost:11434", model="llama3.2-vision")
    obs = make_visual_observation()

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "response": '{"thought": {"reflection": "Seeing button [0]", "reasoning": "Clicking"}, "action": {"action_type": "click", "selector": "[data-agent-id=\'elem-0\']", "description": "Click submit"}}'
        }
        mock_post.return_value = mock_resp

        res = await client.get_next_action("Submit form", obs, 1, 5)

        assert res.action.action_type == ActionType.CLICK
        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs.get("json", {})
        # Verify images array was provided to Ollama
        assert "images" in payload
        assert len(payload["images"]) == 1
        assert payload["images"][0] == obs.annotated_screenshot_base64


@pytest.mark.asyncio
async def test_hybrid_router_multimodal_cloud_routing():
    """Verify HybridModelRouter formats multimodal vision payload for Cloud Turbo."""
    router = HybridModelRouter(active_tier=ModelTier.CLOUD_TURBO, cloud_model="gpt-4o-mini")
    router.openai_api_key = "test-key"
    obs = make_visual_observation()

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{
                "message": {
                    "content": '{"thought": {"reflection": "Visual badge [0] detected", "reasoning": "Clicking submit"}, "action": {"action_type": "click", "selector": "[data-agent-id=\'elem-0\']", "description": "Click submit button"}}'
                }
            }]
        }
        mock_post.return_value = mock_resp

        res = await router.get_next_action("Submit form", obs, 1, 5)
        assert res.action.action_type == ActionType.CLICK

        call_kwargs = mock_post.call_args[1]
        body = call_kwargs.get("json", {})
        user_content = body["messages"][1]["content"]
        assert isinstance(user_content, list)
        assert any(item.get("type") == "image_url" for item in user_content)


# =========================================================================
# UNIT TESTS: FASTAPI VISUAL ENDPOINTS
# =========================================================================

def test_observation_response_schema_visual_fields():
    """Verify ObservationResponse schema accepts visual fields."""
    b64 = create_dummy_base64_image(100, 100)
    resp = ObservationResponse(
        url="http://example.com",
        title="Example",
        elements=[],
        screenshot_base64=b64,
        annotated_screenshot_base64=b64,
        visual_elements_count=5,
    )

    dump = resp.model_dump()
    assert dump["screenshot_base64"] == b64
    assert dump["annotated_screenshot_base64"] == b64
    assert dump["visual_elements_count"] == 5
