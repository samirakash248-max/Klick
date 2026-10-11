"""Event pub/sub system for real-time WebSocket communication.

Broadcasts agent lifecycle transitions, cognitive steps, actions,
and safety flags to connected dashboard frontends.
"""

import asyncio
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
from fastapi import WebSocket
from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Event classification types emitted across agent execution."""
    STATUS_CHANGE = "status_change"
    OBSERVATION_CAPTURED = "observation_captured"
    AGENT_THINKING = "agent_thinking"
    ACTION_PROPOSED = "action_proposed"
    SAFETY_ALERT = "safety_alert"
    ACTION_EXECUTED = "action_executed"
    STEP_COMPLETED = "step_completed"
    RUN_FINISHED = "run_finished"
    LOG_MESSAGE = "log_message"
    ERROR = "error"


class AgentEvent(BaseModel):
    """Structured event envelope streamed over WebSockets."""
    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this event"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Event creation timestamp"
    )
    run_id: Optional[str] = Field(default=None, description="Associated agent run ID")
    type: EventType = Field(..., description="Classification category")
    message: str = Field(..., description="Human-readable event summary")
    data: Dict[str, Any] = Field(default_factory=dict, description="Structured payload")


class EventManager:
    """Manages active WebSocket connections and historical event logging."""

    def __init__(self, max_history: int = 200) -> None:
        self.active_connections: List[WebSocket] = []
        self._history: List[AgentEvent] = []
        self._max_history = max_history
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Register a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a disconnected WebSocket."""
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)

    async def broadcast(self, event: AgentEvent) -> None:
        """Store in history and broadcast an event to all connected clients."""
        async with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history.pop(0)
            connections = list(self.active_connections)

        payload_json = event.model_dump_json()
        dead_connections = []
        for connection in connections:
            try:
                await asyncio.wait_for(connection.send_text(payload_json), timeout=1.0)
            except Exception:
                dead_connections.append(connection)

        if dead_connections:
            async with self._lock:
                for dead in dead_connections:
                    if dead in self.active_connections:
                        self.active_connections.remove(dead)

    async def emit(
        self,
        event_type: EventType,
        message: str,
        data: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None
    ) -> AgentEvent:
        """Convenience method to construct and broadcast an event."""
        event = AgentEvent(
            type=event_type,
            message=message,
            data=data or {},
            run_id=run_id
        )
        await self.broadcast(event)
        return event

    def get_history(self, limit: int = 50) -> List[AgentEvent]:
        """Retrieve recent events from the in-memory buffer."""
        return self._history[-limit:]

    def clear_history(self) -> None:
        """Clear the historical event buffer."""
        self._history.clear()


# Global singleton instance for the backend service
event_manager = EventManager()
