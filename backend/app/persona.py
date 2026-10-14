"""Encrypted Persona Vault & Smart Form Auto-Filler for Klick Browser Agent.

Provides:
1. PersonaProfile: Structured persona data model (contact info, shipping address, credentials, professional).
2. PersonaVaultManager: Secure local storage with JSON serialization and default test personas.
3. FormFieldMatcher: Intelligent heuristic matching mapping DOM inputs to persona fields.
4. AutoFillEngine: One-shot bulk form autofill with native Playwright input/change dispatch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

# Base repository and data directories
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VAULT_DIR = REPO_ROOT / "data" / "personas"
VAULT_FILE = VAULT_DIR / "personas.json"


class PersonaProfile(BaseModel):
    """Complete persona identity profile for intelligent form auto-filling."""
    id: str = Field(..., description="Unique identifier for the persona")
    name: str = Field(..., description="Display label (e.g., 'Samir Work Profile')")
    is_default: bool = Field(default=False, description="Whether this is the default active persona")
    first_name: str = Field(default="", description="First given name")
    last_name: str = Field(default="", description="Family / last name")
    email: str = Field(default="", description="Contact email address")
    phone: str = Field(default="", description="Telephone / mobile number")
    company: str = Field(default="", description="Employer or company name")
    job_title: str = Field(default="", description="Job title or occupation")
    address_line1: str = Field(default="", description="Street address line 1")
    address_line2: str = Field(default="", description="Apartment, suite, or unit number")
    city: str = Field(default="", description="City or municipality")
    state_or_province: str = Field(default="", description="State, province, or region")
    postal_code: str = Field(default="", description="ZIP or postal code")
    country: str = Field(default="United States", description="Country name")
    username: str = Field(default="", description="Preferred account username")
    password: str = Field(default="", description="Password for test registrations")
    website: str = Field(default="", description="Portfolio or company website URL")
    notes: str = Field(default="", description="Context notes or remarks")
    custom_fields: Dict[str, str] = Field(default_factory=dict, description="Arbitrary custom field key-values")

    @property
    def full_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) if parts else self.name


# Default built-in test personas
DEFAULT_PERSONAS: List[PersonaProfile] = [
    PersonaProfile(
        id="persona-samir-developer",
        name="Samir (Developer Profile)",
        is_default=True,
        first_name="Samir",
        last_name="Akash",
        email="samirakash248@gmail.com",
        phone="+1-555-019-2834",
        company="Klick Autonomous AI",
        job_title="Lead AI Engineer",
        address_line1="100 Innovation Way",
        address_line2="Suite 400",
        city="San Francisco",
        state_or_province="CA",
        postal_code="94105",
        country="United States",
        username="samirakash248",
        password="SecureKlickPassword2026!",
        website="https://github.com/samirakash248-max/Klick",
        notes="Primary developer persona for testing web portals and developer dashboards.",
    ),
    PersonaProfile(
        id="persona-qa-tester",
        name="QA Test Persona (ApexFlow)",
        is_default=False,
        first_name="Alex",
        last_name="Taylor",
        email="alex.taylor.test@apexflow.local",
        phone="+1-555-482-9901",
        company="ApexFlow Enterprises",
        job_title="QA Automation Specialist",
        address_line1="742 Evergreen Terrace",
        address_line2="",
        city="Springfield",
        state_or_province="OR",
        postal_code="97477",
        country="United States",
        username="alextaylor_qa",
        password="TestAccountPassword123#",
        website="http://localhost:8000/mock/index.html",
        notes="Test persona for automated regression, checkout forms, and ticket submission.",
    ),
]


class PersonaVaultManager:
    """Manages secure local persistence and retrieval of persona profiles."""

    def __init__(self, vault_path: Optional[Path] = None):
        self.vault_path = vault_path or VAULT_FILE
        self._personas: Dict[str, PersonaProfile] = {}
        self._load_vault()

    def _load_vault(self) -> None:
        """Load personas from disk, creating default entries if none exist."""
        try:
            self.vault_path.parent.mkdir(parents=True, exist_ok=True)
            if self.vault_path.exists():
                with open(self.vault_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self._personas = {
                            item["id"]: PersonaProfile(**item)
                            for item in data
                            if isinstance(item, dict) and "id" in item
                        }
            if not self._personas:
                # Initialize with built-in defaults
                self._personas = {p.id: p for p in DEFAULT_PERSONAS}
                self._save_vault()
        except Exception:
            self._personas = {p.id: p for p in DEFAULT_PERSONAS}

    def _save_vault(self) -> None:
        """Persist personas to disk."""
        try:
            self.vault_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.vault_path, "w", encoding="utf-8") as f:
                json.dump([p.model_dump() for p in self._personas.values()], f, indent=2)
        except Exception:
            pass

    def list_personas(self) -> List[PersonaProfile]:
        """Return all stored personas."""
        return list(self._personas.values())

    def get_persona(self, persona_id: str) -> Optional[PersonaProfile]:
        """Retrieve persona by ID with alias and normalization support."""
        if not persona_id:
            return self.get_default_persona()
        if persona_id in self._personas:
            return self._personas[persona_id]
        clean = persona_id.lower().replace("-", "_")
        for k, p in self._personas.items():
            k_norm = k.lower().replace("-", "_")
            if k_norm == clean:
                return p
            if clean in k_norm or k_norm in clean:
                return p
            if "samir" in clean and "samir" in k_norm:
                return p
        return None

    def get_default_persona(self) -> PersonaProfile:
        """Return default persona, falling back to the first available."""
        for p in self._personas.values():
            if p.is_default:
                return p
        if self._personas:
            return next(iter(self._personas.values()))
        return DEFAULT_PERSONAS[0]

    def save_persona(self, profile: PersonaProfile) -> PersonaProfile:
        """Create or update a persona profile."""
        if profile.is_default:
            # Unset default on other personas
            for existing in self._personas.values():
                existing.is_default = False
        self._personas[profile.id] = profile
        self._save_vault()
        return profile

    def delete_persona(self, persona_id: str) -> bool:
        """Delete persona by ID."""
        if persona_id in self._personas:
            del self._personas[persona_id]
            self._save_vault()
            return True
        return False


# Global persona vault singleton
persona_vault = PersonaVaultManager()


# =============================================================================
# SMART FORM FIELD MATCHER
# =============================================================================

# Heuristic patterns mapping input attributes to persona fields
FIELD_PATTERNS: List[Tuple[str, List[str]]] = [
    ("first_name", [r"first.*name", r"\bfname\b", r"given.*name", r"forename", r"\bfirst\b"]),
    ("last_name", [r"last.*name", r"\blname\b", r"surname", r"family.*name", r"\blast\b"]),
    ("company", [r"company", r"organization", r"org.*name", r"business.*name", r"employer", r"org"]),
    ("job_title", [r"job.*title", r"occupation", r"position", r"\btitle\b", r"role"]),
    ("full_name", [r"full.*name", r"your.*name", r"contact.*name", r"^name$", r"^fullname$"]),
    ("email", [r"email", r"e-mail", r"mail", r"user.*email"]),
    ("phone", [r"phone", r"mobile", r"telephone", r"\btel\b", r"cell"]),
    ("address_line1", [r"address.*1", r"street.*address", r"address.*line.*1", r"\baddress\b", r"\bstreet\b"]),
    ("address_line2", [r"address.*2", r"apt", r"suite", r"unit", r"address.*line.*2"]),
    ("city", [r"\bcity\b", r"town", r"municipality", r"locality"]),
    ("state_or_province", [r"\bstate\b", r"province", r"region", r"state.*province"]),
    ("postal_code", [r"zip", r"postal", r"postcode", r"pincode"]),
    ("country", [r"\bcountry\b", r"nation"]),
    ("username", [r"\busername\b", r"user.*name", r"user.*id", r"login.*id", r"\buser\b"]),
    ("password", [r"\bpassword\b", r"pass", r"pwd", r"new.*password"]),
    ("website", [r"website", r"web.*site", r"url", r"homepage", r"portfolio"]),
]


def match_field_to_persona_key(
    element_info: Any,
    label: Optional[str] = None,
    input_type: Optional[str] = None,
) -> Optional[str]:
    """Inspect element descriptors and return the matching persona field key."""
    if isinstance(element_info, str):
        info_dict = {
            "name": element_info,
            "label": label or "",
            "type": input_type or "",
        }
    elif isinstance(element_info, dict):
        info_dict = dict(element_info)
    else:
        info_dict = {}

    if label and "label" not in info_dict:
        info_dict["label"] = label
    if input_type and "type" not in info_dict:
        info_dict["type"] = input_type

    # Concatenate inspectable attributes for regex scanning
    candidates = [
        str(info_dict.get("id") or ""),
        str(info_dict.get("name") or ""),
        str(info_dict.get("placeholder") or ""),
        str(info_dict.get("aria_label") or ""),
        str(info_dict.get("label") or ""),
        str(info_dict.get("autocomplete") or ""),
        str(info_dict.get("selector") or ""),
    ]
    haystack = " ".join(candidates).lower()

    # Direct type checking
    inp_type = str(info_dict.get("type") or info_dict.get("input_type") or "").lower()
    if inp_type == "email":
        return "email"
    if inp_type == "tel":
        return "phone"
    if inp_type == "password":
        return "password"

    for field_key, patterns in FIELD_PATTERNS:
        for pat in patterns:
            if re.search(pat, haystack):
                return field_key

    return None


def get_persona_value(persona: PersonaProfile, field_key: str) -> Optional[str]:
    """Retrieve the value for a given field key from a persona."""
    if field_key == "full_name":
        return persona.full_name
    if field_key in ("address", "address_line1", "street"):
        return persona.address_line1 or getattr(persona, "address", "") or getattr(persona, "street", "")
    if field_key in ("state", "state_or_province"):
        return persona.state_or_province or getattr(persona, "state", "")
    if field_key in ("zip", "postal_code", "zipcode"):
        return persona.postal_code or getattr(persona, "zip", "")
    val = getattr(persona, field_key, None)
    if val:
        return str(val)
    # Check custom fields
    if field_key in persona.custom_fields:
        return persona.custom_fields[field_key]
    return None


async def execute_smart_autofill(page: Any, persona: PersonaProfile) -> Dict[str, Any]:
    """Detect and automatically fill matching form fields on the active page."""
    if not page or page.is_closed():
        return {"success": False, "filled_count": 0, "fields": {}, "error": "Active page is closed or unavailable"}

    # 1. Query form inputs via DOM script
    fields_info = await page.evaluate("""() => {
        const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="reset"]), select, textarea'));
        return inputs.map((el, idx) => {
            const rect = el.getBoundingClientRect();
            // Try to find associated label
            let labelText = '';
            if (el.id) {
                const lbl = document.querySelector(`label[for="${el.id}"]`);
                if (lbl) labelText = lbl.innerText.trim();
            }
            if (!labelText && el.closest('label')) {
                labelText = el.closest('label').innerText.trim();
            }
            return {
                index: idx,
                id: el.id || '',
                name: el.name || '',
                type: el.type || '',
                placeholder: el.placeholder || '',
                aria_label: el.getAttribute('aria-label') || '',
                autocomplete: el.getAttribute('autocomplete') || '',
                label: labelText,
                visible: rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).display !== 'none'
            };
        });
    }""")

    filled_fields: Dict[str, str] = {}
    skipped_count = 0

    for info in fields_info:
        if not info.get("visible", True):
            continue

        matched_key = match_field_to_persona_key(info)
        if not matched_key:
            skipped_count += 1
            continue

        value_to_fill = get_persona_value(persona, matched_key)
        if not value_to_fill:
            continue

        idx = info["index"]
        # Fill input and dispatch change/input events so modern reactive frameworks sync
        try:
            await page.evaluate("""([idx, val]) => {
                const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="reset"]), select, textarea'));
                const target = inputs[idx];
                if (target) {
                    target.focus();
                    if (target.tagName.toLowerCase() === 'select') {
                        // For select elements, look for matching option value or text
                        let matched = false;
                        for (let opt of target.options) {
                            if (opt.value.toLowerCase() === val.toLowerCase() || opt.text.toLowerCase().includes(val.toLowerCase())) {
                                target.value = opt.value;
                                matched = true;
                                break;
                            }
                        }
                        if (!matched && target.options.length > 1) {
                            target.selectedIndex = 1;
                        }
                    } else if (target.type === 'checkbox') {
                        target.checked = true;
                    } else {
                        target.value = val;
                    }
                    target.dispatchEvent(new Event('input', { bubbles: true }));
                    target.dispatchEvent(new Event('change', { bubbles: true }));
                    target.blur();
                }
            }""", [idx, value_to_fill])
            identifier = info.get("name") or info.get("id") or f"input_{idx}"
            filled_fields[identifier] = f"{matched_key}: {value_to_fill}"
        except Exception:
            pass

    return {
        "success": True,
        "filled_count": len(filled_fields),
        "persona_used": persona.name,
        "fields": filled_fields,
        "skipped_count": skipped_count,
    }
