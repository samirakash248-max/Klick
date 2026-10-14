"""Helper functions and workflow resolvers for BrowserPilot AgentLoop.

Modularized routines for:
1. Destination URL inference from natural language goals
2. Search query regex extraction
3. Specialized workflow routing (YouTube, Google/Bing Search, Typing Tests)
4. Anti-stagnation target click diverters
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from .schemas import ActionType, AgentRunState, BrowserAction, PageObservation

logger = logging.getLogger("browserpilot.agent_helpers")


def infer_initial_url(goal: str, start_url: Optional[str] = None) -> Optional[str]:
    """Infer the initial target URL from the user's goal if start_url is omitted or default mock."""
    target_url = start_url
    goal_lower = (goal or "").lower()

    is_mock_default = not target_url or "localhost:8080" in target_url or "127.0.0.1:8080" in target_url
    if is_mock_default:
        # Check for explicit domain or URL in goal (e.g. "monkeytype.com", "typingtest.com", "https://...")
        domain_match = re.search(
            r'(https?://[^\s,]+|(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|io|co|ai|edu|gov|dev|app|gg|in|me)[^\s,]*)',
            goal,
            re.IGNORECASE,
        )
        if domain_match:
            found = domain_match.group(1).strip().rstrip(".,;\"'")
            if not found.startswith("http://") and not found.startswith("https://"):
                target_url = f"https://{found}"
            else:
                target_url = found
        elif any(w in goal_lower for w in ("map", "maps", "route", "direction")):
            target_url = "https://www.google.com/maps"
        elif "youtube" in goal_lower:
            target_url = "https://www.youtube.com"
        elif "wikipedia" in goal_lower:
            target_url = "https://www.wikipedia.org"
        elif "flipkart" in goal_lower:
            target_url = "https://www.flipkart.com"
        elif "amazon" in goal_lower:
            target_url = "https://www.amazon.com"
        elif any(k in goal_lower for k in ("invoice", "apexflow", "task", "inv-")):
            target_url = "http://127.0.0.1:8000/mock/tasks.html"
        elif not target_url:
            target_url = "https://www.google.com"

    return target_url


def extract_search_query(goal: str) -> str:
    """Extract search terms from natural language commands (e.g. 'search for X and click Y')."""
    query_match = re.search(
        r"(?:search|look\s+for|find|query)\s+(?:for\s+)?['\"]?([^'\"\n]+?)['\"]?(?:\s+and\b|\s+on\b|\s+in\b|$)",
        goal,
        re.IGNORECASE,
    )
    if not query_match:
        query_match = re.search(r"search (?:for )?['\"]?([^'\"\n]+?)['\"]?(?:\s+and\b|$)", goal, re.IGNORECASE)

    query_term = query_match.group(1).strip() if query_match else ""
    if query_term.lower().endswith(" on youtube"):
        query_term = query_term[:-11].strip()
    elif query_term.lower().endswith(" in youtube"):
        query_term = query_term[:-11].strip()

    return query_term


def apply_smart_workflows(
    action: BrowserAction,
    goal: str,
    page_url: str,
    observation: PageObservation,
    query_term: str,
) -> None:
    """Specialize and adapt proposed browser actions for known web flows (YouTube, Search, Typing)."""
    curr_p_url = (page_url or "").lower()
    goal_lower = (goal or "").lower()

    # Ensure action has text if typing search query from goal
    if action.action_type == ActionType.TYPE and not action.text and query_term:
        action.text = query_term

    # 1. YouTube Workflow
    if "youtube.com" in curr_p_url and "results" not in curr_p_url and "watch" not in curr_p_url and "shorts" not in curr_p_url and query_term:
        tgt = "id-search"
        sel = "[data-agent-id='id-search']"
        for el in observation.interactive_elements:
            el_desc = f"{el.id or ''} {el.text or ''} {el.aria_label or ''} {el.selector or ''}".lower()
            if el.tag_name in ("input", "textarea") and any(k in el_desc for k in ("search", "query")):
                tgt = el.id or "id-search"
                sel = el.selector or f"[data-agent-id='{tgt}']"
                break

        action.action_type = ActionType.TYPE
        action.target = tgt
        action.selector = sel
        action.text = query_term
        action.description = f"Type '{query_term}' into search input to search YouTube"

    elif "youtube.com" in curr_p_url and "results" in curr_p_url:
        if any(w in goal_lower for w in ("video", "watch", "play", "first")):
            action.action_type = ActionType.CLICK
            action.target = "id-video-title"
            action.selector = "[data-agent-id='id-video-title']"
            action.description = "Click first video title link to open and play video"
        else:
            action.action_type = ActionType.FINISH
            action.description = f"Search for '{query_term or 'query'}' completed and results are displayed"

    # 2. Google / Search Engine Workflow
    elif ("google.com" in curr_p_url or "bing.com" in curr_p_url) and "/search" not in curr_p_url and query_term:
        tgt = None
        sel = "textarea[name='q'], input[name='q']"
        for el in observation.interactive_elements:
            el_desc = f"{el.id or ''} {el.text or ''} {el.aria_label or ''} {el.selector or ''}".lower()
            if el.tag_name in ("input", "textarea") and any(k in el_desc for k in ("search", "query", "q")):
                tgt = el.id
                sel = el.selector or f"[data-agent-id='{tgt}']"
                break
        action.action_type = ActionType.TYPE
        action.target = tgt or "id-search"
        action.selector = sel
        action.text = query_term
        action.description = f"Type '{query_term}' into search box"

    elif ("google.com" in curr_p_url or "bing.com" in curr_p_url) and "/search" in curr_p_url:
        if any(w in goal_lower for w in ("open", "website", "test", "first", "link", "click")):
            result_tgt = None
            for el in observation.interactive_elements:
                if el.tag_name == "a" and el.text and len(el.text.strip()) > 3:
                    el_txt = el.text.lower()
                    if not any(skip in el_txt for skip in ("tools", "all", "images", "videos", "news", "sign in", "settings", "feedback")):
                        result_tgt = el.id
                        break
            if result_tgt:
                action.action_type = ActionType.CLICK
                action.target = result_tgt
                action.selector = f"[data-agent-id='{result_tgt}']"
                action.description = f"Click search result link '{result_tgt}' to open website"
        else:
            action.action_type = ActionType.FINISH
            action.description = f"Search for '{query_term}' completed successfully"

    # 3. Typing Test & Monkeytype Workflow
    elif any(tt in curr_p_url for tt in ("monkeytype.com", "typingtest.com", "10fastfingers.com", "typing.com")):
        if any(w in goal_lower for w in ("type", "typing", "words", "test")):
            snippet = observation.page_text_snippet or ""
            extracted_words = "the quick brown fox jumps over the lazy dog and types with speed"
            if snippet:
                word_candidates = [
                    w for w in re.findall(r'\b[a-z]{2,10}\b', snippet.lower())
                    if w not in ("monkeytype", "cookies", "privacy", "leaderboard", "settings", "about", "restart", "test")
                ]
                if len(word_candidates) >= 5:
                    extracted_words = " ".join(word_candidates[:12])

            action.action_type = ActionType.TYPE
            action.target = "words"
            action.selector = "#wordsInput, #words, .word, input[type='text'], body"
            action.text = extracted_words
            action.description = f"Start typing test words: '{extracted_words[:35]}...'"

    # 4. Convert Click on Input to Type
    elif action.action_type == ActionType.CLICK and query_term:
        is_input = False
        for el in observation.interactive_elements:
            if el.id == action.target and el.tag_name in ("input", "textarea"):
                is_input = True
                break
        if is_input:
            action.action_type = ActionType.TYPE
            action.text = query_term
            action.description = f"Type '{query_term}' into '{action.target}'"


def prevent_target_click_loop(
    action: BrowserAction,
    state: AgentRunState,
    page_url: str,
    observation: PageObservation,
    query_term: str,
) -> None:
    """Detect repetitive clicks on unchanged elements and divert to inputs or alternate links."""
    if not hasattr(state, "_target_clicks"):
        state._target_clicks = {}

    if action.target and action.action_type == ActionType.CLICK:
        tgt_key = f"{page_url}::{action.target}"
        state._target_clicks[tgt_key] = state._target_clicks.get(tgt_key, 0) + 1
        if state._target_clicks[tgt_key] >= 3:
            logger.warning(
                f"Target '{action.target}' clicked {state._target_clicks[tgt_key]} times without URL advance. Diverting action."
            )
            is_input = any(
                (el.id == action.target or getattr(el, 'agent_id', None) == action.target)
                and el.tag_name in ("input", "textarea")
                for el in observation.interactive_elements
            )
            if is_input and query_term:
                action.action_type = ActionType.TYPE
                action.text = query_term
                action.description = f"Repetitive click diverted: typing '{query_term}' into input"
            else:
                alt_elem = None
                for el in observation.interactive_elements:
                    if el.id != action.target and getattr(el, 'agent_id', None) != action.target and el.tag_name in ("a", "button"):
                        alt_elem = el.id
                        break
                if alt_elem:
                    action.target = alt_elem
                    action.selector = f"[data-agent-id='{alt_elem}']"
                    action.description = f"Repetitive click diverted: clicking alternate element '{alt_elem}'"
                else:
                    action.action_type = ActionType.SCROLL
                    action.scroll_delta_y = 500
                    action.description = "Repetitive click diverted: scrolling page down"
