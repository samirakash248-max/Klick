"""Unit and integration tests for Persona Vault and Smart Form Autofill."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from pathlib import Path

from backend.app.persona import (
    PersonaProfile,
    PersonaVaultManager,
    match_field_to_persona_key,
    execute_smart_autofill,
)
from backend.app.main import app
from backend.app.schemas import ActionType, BrowserAction


def test_persona_profile_model():
    """Verify PersonaProfile field initialization and dictionary serialization."""
    profile = PersonaProfile(
        id="test_user",
        name="Test User",
        first_name="Jane",
        last_name="Doe",
        email="jane.doe@example.com",
        phone="+1-555-0144",
        company="Acme Corp",
        job_title="Lead Architect",
        address="789 Market St",
        city="Seattle",
        state="WA",
        postal_code="98101",
        country="USA",
        username="janedoe",
        password="SuperSecretPassword!",
        custom_fields={"tax_id": "12-3456789", "favorite_color": "Blue"},
    )
    assert profile.id == "test_user"
    assert profile.first_name == "Jane"
    assert profile.custom_fields["tax_id"] == "12-3456789"
    dump = profile.model_dump()
    assert dump["email"] == "jane.doe@example.com"


def test_persona_vault_manager_crud(tmp_path):
    """Test PersonaVaultManager CRUD lifecycle and storage persistence."""
    vault_file = tmp_path / "test_personas.json"
    mgr = PersonaVaultManager(vault_path=vault_file)

    # Initial built-ins should exist
    assert len(mgr.list_personas()) >= 2
    samir = mgr.get_persona("samir_developer")
    assert samir is not None
    assert samir.first_name == "Samir"

    # Create new profile
    new_profile = PersonaProfile(
        id="dev_qa",
        name="QA Automation Bot",
        first_name="Bot",
        last_name="Tester",
        email="qa.bot@test.org",
        phone="555-9988",
        company="QA Labs",
        city="Austin",
        state="TX",
    )
    saved = mgr.save_persona(new_profile)
    assert saved.id == "dev_qa"

    # Check persistence reload
    mgr2 = PersonaVaultManager(vault_path=vault_file)
    reloaded = mgr2.get_persona("dev_qa")
    assert reloaded is not None
    assert reloaded.email == "qa.bot@test.org"

    # Delete profile
    deleted = mgr2.delete_persona("dev_qa")
    assert deleted is True
    assert mgr2.get_persona("dev_qa") is None

    # Deleting nonexistent returns False
    assert mgr2.delete_persona("nonexistent") is False


def test_match_field_to_persona_key_heuristics():
    """Verify intelligent heuristic matching of DOM field descriptors to persona keys."""
    # First name patterns
    assert match_field_to_persona_key("user_fname", "First Name", "text") == "first_name"
    assert match_field_to_persona_key("first-name", "", "text") == "first_name"
    assert match_field_to_persona_key("givenName", "", "text") == "first_name"

    # Last name patterns
    assert match_field_to_persona_key("user_lname", "Last Name", "text") == "last_name"
    assert match_field_to_persona_key("family_name", "", "text") == "last_name"

    # Email patterns
    assert match_field_to_persona_key("user_email", "Enter your email address", "email") == "email"
    assert match_field_to_persona_key("contact_mail", "", "text") == "email"

    # Phone patterns
    assert match_field_to_persona_key("phone_number", "Mobile phone", "tel") == "phone"
    assert match_field_to_persona_key("cellphone", "", "text") == "phone"

    # Company / Org
    assert match_field_to_persona_key("organization", "Company Name", "text") == "company"
    assert match_field_to_persona_key("employer_input", "", "text") == "company"

    # Address / City / Postal code
    assert match_field_to_persona_key("billing_address", "Street Address", "text") in ("address", "address_line1")
    assert match_field_to_persona_key("user_city", "City / Municipality", "text") == "city"
    assert match_field_to_persona_key("zipcode_input", "ZIP or Postal Code", "text") == "postal_code"

    # Password
    assert match_field_to_persona_key("pwd", "Enter secure password", "password") == "password"


@pytest.mark.asyncio
async def test_execute_smart_autofill_mock_page():
    """Verify execute_smart_autofill dispatches values and reactive events."""
    mock_page = MagicMock()
    mock_page.is_closed = MagicMock(return_value=False)
    mock_page.evaluate = AsyncMock(return_value=[
        {"index": 0, "name": "fname", "label": "First Name", "type": "text", "visible": True},
        {"index": 1, "name": "user_email", "label": "Email", "type": "email", "visible": True},
        {"index": 2, "name": "phone", "label": "Phone", "type": "tel", "visible": True},
        {"index": 3, "name": "company", "label": "Company", "type": "text", "visible": True},
        {"index": 4, "name": "city", "label": "City", "type": "text", "visible": True},
    ])

    persona = PersonaProfile(
        id="test_autofill",
        name="Test",
        first_name="Samir",
        email="samir.dev@example.com",
        phone="555-0199",
        company="Acme Cloud Inc.",
        city="San Francisco",
    )

    result = await execute_smart_autofill(mock_page, persona)
    assert result["success"] is True
    assert result["filled_count"] == 5
    assert len(result["fields"]) == 5
    assert mock_page.evaluate.called


def test_persona_vault_api_endpoints():
    """Verify Persona Vault REST API endpoints via TestClient."""
    client = TestClient(app)

    # 1. List personas
    res = client.get("/api/personas")
    assert res.status_code == 200
    personas = res.json()
    assert isinstance(personas, list)
    assert len(personas) >= 1

    # 2. Get specific persona
    pid = personas[0]["id"]
    res_single = client.get(f"/api/personas/{pid}")
    assert res_single.status_code == 200
    assert res_single.json()["id"] == pid

    # 3. Save new persona via API
    new_data = {
        "id": "api_test_persona",
        "name": "API Test Persona",
        "first_name": "API",
        "last_name": "Tester",
        "email": "api.tester@example.com",
        "phone": "555-1234",
    }
    res_save = client.post("/api/personas", json=new_data)
    assert res_save.status_code == 200
    assert res_save.json()["id"] == "api_test_persona"

    # 4. Verify in list
    res_check = client.get("/api/personas/api_test_persona")
    assert res_check.status_code == 200
    assert res_check.json()["email"] == "api.tester@example.com"

    # 5. Delete persona
    res_del = client.delete("/api/personas/api_test_persona")
    assert res_del.status_code == 200
    assert res_del.json()["success"] is True

    # 6. Delete again -> 404
    res_del_404 = client.delete("/api/personas/api_test_persona")
    assert res_del_404.status_code == 404
