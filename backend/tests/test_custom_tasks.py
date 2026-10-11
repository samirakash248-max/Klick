"""Tests for custom task API endpoints and execution."""

import pytest
from httpx import AsyncClient, ASGITransport
from backend.app.main import app


@pytest.mark.asyncio
async def test_list_builtin_custom_tasks():
    """Verify built-in custom task recipes are returned."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/tasks/custom")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        assert len(data) >= 6
        recipe_ids = [item["id"] for item in data]
        assert "recipe-summarize" in recipe_ids
        assert "recipe-extract-data" in recipe_ids


@pytest.mark.asyncio
async def test_create_and_delete_custom_task():
    """Verify creating, querying, and deleting a user custom task."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create
        new_task = {
            "name": "E2E Invoice Extractor",
            "goal": "Click invoice tab and extract totals",
            "max_steps": 5,
        }
        res_post = await client.post("/api/tasks/custom", json=new_task)
        assert res_post.status_code == 200
        created = res_post.json()
        assert created["name"] == "E2E Invoice Extractor"
        assert created["id"].startswith("custom-")

        # Verify listed
        res_list = await client.get("/api/tasks/custom")
        assert any(t["id"] == created["id"] for t in res_list.json())

        # Delete
        res_del = await client.delete(f"/api/tasks/custom/{created['id']}")
        assert res_del.status_code == 200

        # Verify deleted
        res_list_after = await client.get("/api/tasks/custom")
        assert not any(t["id"] == created["id"] for t in res_list_after.json())


@pytest.mark.asyncio
async def test_extension_in_tab_session_start_and_step():
    """Verify extension in-tab endpoints execute without launching a separate Playwright browser."""
    from unittest.mock import AsyncMock
    from backend.app.main import agent_loop
    from backend.app.schemas import AgentResponse, AgentThought, BrowserAction, ActionType

    # Mock model client next action
    mock_resp = AgentResponse(
        thought=AgentThought(reasoning="Click search button", reflection="Looking at page"),
        action=BrowserAction(action_type=ActionType.CLICK, selector="[data-agent-id='elem-1']", description="Click search"),
    )
    agent_loop.model_client.get_next_action = AsyncMock(return_value=mock_resp)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Start extension in-tab session
        start_payload = {
            "goal": "Search for documentation",
            "start_url": "https://example.com/docs",
            "max_steps": 5,
        }
        res_start = await client.post("/api/extension/start", json=start_payload)
        assert res_start.status_code == 200
        start_data = res_start.json()
        assert start_data["status"] == "running"
        run_id = start_data["run_id"]

        # 2. Process in-tab observation step
        step_payload = {
            "run_id": run_id,
            "goal": "Search for documentation",
            "step": 1,
            "max_steps": 5,
            "observation": {
                "url": "https://example.com/docs",
                "title": "Documentation Hub",
                "dom_summary": "button#elem-1 'Search'",
                "interactive_elements": [
                    {
                        "tag_name": "button",
                        "selector": "[data-agent-id='elem-1']",
                        "data_agent_id": "elem-1",
                        "text": "Search",
                        "is_visible": True,
                    }
                ],
                "page_text_snippet": "Welcome to documentation",
            },
        }
        res_step = await client.post("/api/extension/step", json=step_payload)
        assert res_step.status_code == 200
        step_data = res_step.json()
        assert step_data["step"] == 1
        assert step_data["action"]["action_type"] == "click"
        assert step_data["thought"]["reasoning"] == "Click search button"
        assert step_data["safety_passed"] is True

        # 3. Stop extension session
        res_stop = await client.post("/api/extension/stop")
        assert res_stop.status_code == 200
        assert res_stop.json()["message"] == "Extension run stopped"

