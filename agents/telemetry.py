from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from agents import IncidentState
from agents.llm import get_model, get_provider, llm_available


def ensure_trace_id(state: IncidentState) -> None:
    if not state.trace_id:
        state.trace_id = state.incident_id


def next_span_id(state: IncidentState, agent: str) -> str:
    ensure_trace_id(state)
    state.span_seq += 1
    return f"{state.trace_id}:{agent}:{state.span_seq}"


def record_invocation(
    state: IncidentState,
    *,
    agent: str,
    action: str,
    source: str,
    reasoning: str = "",
    findings: Optional[Dict[str, Any]] = None,
    input_refs: Optional[Dict[str, Any]] = None,
    output_refs: Optional[Dict[str, Any]] = None,
    latency_ms: Optional[float] = None,
    parent_span_id: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    span_id: str = next_span_id(state, agent)
    parent = parent_span_id or state.current_parent_span_id
    payload: Dict[str, Any] = {
        "agent": agent,
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "source": source,
        "trace_id": state.trace_id,
        "span_id": span_id,
        "parent_span_id": parent,
        "iteration": state.analysis_iterations,
    }
    if llm_available():
        payload["llm"] = {"provider": get_provider(), "model": get_model()}
    if reasoning:
        payload["reasoning"] = reasoning
    if findings is not None:
        payload["findings"] = findings
    if input_refs is not None:
        payload["input_refs"] = input_refs
    if output_refs is not None:
        payload["output_refs"] = output_refs
    if latency_ms is not None:
        payload["latency_ms"] = round(float(latency_ms), 2)
    if extra:
        payload.update(extra)
    state.agent_invocations.append(payload)
    state.current_parent_span_id = span_id
    return payload


@dataclass
class Timer:
    start: float

    @classmethod
    def begin(cls) -> "Timer":
        return cls(start=time.perf_counter())

    def ms(self) -> float:
        return (time.perf_counter() - self.start) * 1000.0
