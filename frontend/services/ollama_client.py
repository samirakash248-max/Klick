"""Direct Ollama API service for Model Playground and health checks.

Connects to local Ollama instance (default: http://localhost:11434) to:
1. Check service and model availability.
2. Query models with prompt and system directives.
3. Validate structured AgentResponse output against schema.
"""

import json
import os
import time
from typing import Any, Dict, List, Optional
import httpx
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_OLLAMA_MODEL = (
    os.getenv("BROWSERPILOT_MODEL")
    or os.getenv("OLLAMA_MODEL")
    or "llama3.2:3b"
)

SAMPLE_PLAYGROUND_OBSERVATION = {
    "url": "http://localhost:8080/index.html#invoices",
    "title": "Invoices & Billing - ApexFlow",
    "dom_summary": "[0] <input> 'Search invoices...' -> selector: `#invoice-search`\n[1] <button> 'View INV-1001' -> selector: `[data-agent-id='view-invoice-1001']`",
    "interactive_elements": [
        {"tag": "input", "agent_id": "invoice-search", "text": "", "role": "textbox"},
        {"tag": "button", "agent_id": "view-invoice-1001", "text": "View", "role": "button"},
    ],
}


class OllamaService:
    """Synchronous client for direct Ollama API testing."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
        timeout: float = 60.0,
    ) -> None:
        raw_url = base_url or DEFAULT_OLLAMA_URL
        self.base_url = raw_url.rstrip("/")
        self.default_model = default_model or DEFAULT_OLLAMA_MODEL
        self.timeout = timeout

    def _client(self, timeout: Optional[float] = None) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=timeout or self.timeout)

    def check_health(self) -> Dict[str, Any]:
        """Check if Ollama server is running and return available models."""
        try:
            with self._client(timeout=3.0) as client:
                res = client.get("/api/tags")
                if res.status_code == 200:
                    data = res.json()
                    models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                    return {
                        "online": True,
                        "models": models,
                        "default_model": self.default_model,
                        "model_present": self.default_model in models,
                    }
                return {
                    "online": False,
                    "error": f"Ollama returned HTTP status {res.status_code}",
                    "models": [],
                }
        except httpx.ConnectError:
            return {
                "online": False,
                "error": f"Could not connect to Ollama at {self.base_url}. Ensure Ollama is running (`ollama serve`).",
                "models": [],
            }
        except httpx.TimeoutException:
            return {
                "online": False,
                "error": f"Connection to Ollama at {self.base_url} timed out.",
                "models": [],
            }
        except Exception as exc:
            return {"online": False, "error": str(exc), "models": []}

    def list_models(self) -> List[str]:
        """Retrieve installed model names from Ollama."""
        health = self.check_health()
        return health.get("models", [])

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        format_json: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send a generation prompt directly to Ollama."""
        target_model = model or self.default_model
        payload = {
            "model": target_model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system
        if format_json:
            payload["format"] = "json"

        start_time = time.time()
        try:
            with self._client(timeout=timeout) as client:
                res = client.post("/api/generate", json=payload)
                elapsed_ms = (time.time() - start_time) * 1000

                if res.status_code == 404:
                    return {
                        "success": False,
                        "error": f"Model '{target_model}' not found in Ollama. Pull it using `ollama pull {target_model}`.",
                        "duration_ms": elapsed_ms,
                    }
                if res.status_code != 200:
                    return {
                        "success": False,
                        "error": f"Ollama HTTP {res.status_code}: {res.text}",
                        "duration_ms": elapsed_ms,
                    }

                data = res.json()
                raw_response = data.get("response", "")
                parsed_json = None
                if format_json:
                    try:
                        parsed_json = json.loads(raw_response)
                    except json.JSONDecodeError:
                        pass

                return {
                    "success": True,
                    "response": raw_response,
                    "parsed_json": parsed_json,
                    "model": target_model,
                    "duration_ms": elapsed_ms,
                    "eval_count": data.get("eval_count"),
                }

        except httpx.TimeoutException:
            return {
                "success": False,
                "error": f"Request to Ollama timed out after {timeout or self.timeout}s.",
                "duration_ms": (time.time() - start_time) * 1000,
            }
        except httpx.ConnectError:
            return {
                "success": False,
                "error": f"Connection refused by Ollama at {self.base_url}.",
                "duration_ms": (time.time() - start_time) * 1000,
            }
        except Exception as exc:
            return {
                "success": False,
                "error": f"Ollama request error: {str(exc)}",
                "duration_ms": (time.time() - start_time) * 1000,
            }

    def test_structured_action(
        self,
        goal: str,
        sample_obs: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Test model ability to output a schema-compliant BrowserAction JSON."""
        obs = sample_obs or SAMPLE_PLAYGROUND_OBSERVATION
        system_prompt = (
            "You are BrowserPilot AI. Analyze the page observation and output a JSON decision.\n"
            "Schema: {\"thought\": {\"reflection\": str, \"reasoning\": str}, "
            "\"action\": {\"action_type\": str, \"selector\": str|null, \"text\": str|null, \"description\": str}}\n"
            "Respond strictly with valid JSON."
        )
        user_prompt = (
            f"User Goal: {goal}\n"
            f"Page URL: {obs.get('url')}\n"
            f"Page Title: {obs.get('title')}\n"
            f"Elements: {json.dumps(obs.get('interactive_elements', []))}\n"
            "Propose the next action."
        )

        gen_result = self.generate(
            prompt=user_prompt,
            model=model,
            system=system_prompt,
            format_json=True,
        )

        if not gen_result["success"]:
            return gen_result

        # Validate structured adherence
        parsed = gen_result.get("parsed_json")
        if not parsed:
            return {
                **gen_result,
                "schema_valid": False,
                "validation_error": "Model response was not valid JSON.",
            }

        has_thought = isinstance(parsed.get("thought"), dict)
        has_action = isinstance(parsed.get("action"), dict)
        action_type = parsed.get("action", {}).get("action_type") if has_action else None

        valid = bool(has_thought and has_action and action_type)
        return {
            **gen_result,
            "schema_valid": valid,
            "parsed_action": parsed.get("action"),
            "parsed_thought": parsed.get("thought"),
            "validation_error": None if valid else "JSON missing required 'thought' or 'action.action_type' keys.",
        }
