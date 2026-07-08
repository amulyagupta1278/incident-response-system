You are building a production-grade truly agentic multi-agent incident response system. Complete the entire transformation in one execution. Follow all steps precisely.

===== CURRENT STATE =====
- Linear 6-agent pipeline (incident_commander → log_analysis → metrics_analysis → rca → business_impact → executive_summary)
- All tests passing (17/17)
- FastAPI working
- Mock data ready
- NOT agentic (predetermined order, no agent autonomy)

===== TARGET STATE =====
- LangGraph-based orchestration with autonomous agents
- Router agent decides next action (not predetermined)
- Claude-powered RCA with reasoning loops
- Agents iterate if confidence too low
- Truly agentic system ready for hackathon

===== CRITICAL SUCCESS REQUIREMENTS =====
1. NO comments in Python code
2. NO "Claude" signatures anywhere
3. NO hardcoded API keys
4. Type hints on every function
5. PEP 8 compliant (4-space, 100 char max)
6. All tests pass (20+ tests)
7. Graph visualization works
8. .env file excluded from git
9. All dependencies in requirements.txt

===== EXECUTION PHASES =====

PHASE 1: CREATE NEW FILES (4 files)
PHASE 2: MODIFY EXISTING FILES (5 files)
PHASE 3: INSTALL DEPENDENCIES
PHASE 4: RUN TESTS
PHASE 5: VERIFY SYSTEM
PHASE 6: COMMIT TO GIT

===== PHASE 1: CREATE 4 NEW FILES =====

FILE 1: agents/agentic_system.py
PURPOSE: LangGraph StateGraph setup with all nodes and edges
REQUIREMENTS:
- Import: langgraph, IncidentState, all agent functions
- Create StateGraph with IncidentState
- Define 8 nodes: route_next_action, load_data, analyze_logs, analyze_metrics, run_rca, request_more_data, calculate_business_impact, generate_summary
- Define conditional edges for routing
- Define loop edge for RCA confidence check
- Compile graph and export create_incident_analysis_graph()

CODE STRUCTURE:
```python
from langgraph.graph import StateGraph, END
from typing import Literal
from agents import IncidentState
from agents.router_agent import route_next_action, should_request_more_data
from agents.load_data_agent import load_data
from agents.log_analysis_agent import log_analysis
from agents.metrics_analysis_agent import metrics_analysis
from agents.rca_agent import rca_analysis_with_claude
from agents.business_impact_agent import business_impact
from agents.executive_summary_agent import executive_summary
from agents.request_more_data_agent import request_more_data

def create_incident_analysis_graph():
    graph = StateGraph(IncidentState)
    
    # Add nodes
    graph.add_node("route_next_action", route_next_action)
    graph.add_node("load_data", load_data)
    graph.add_node("analyze_logs", log_analysis)
    graph.add_node("analyze_metrics", metrics_analysis)
    graph.add_node("run_rca", rca_analysis_with_claude)
    graph.add_node("request_more_data", request_more_data)
    graph.add_node("calculate_business_impact", business_impact)
    graph.add_node("generate_summary", executive_summary)
    
    # Add edges from router (conditional routing)
    graph.add_conditional_edges(
        "route_next_action",
        lambda state: route_next_action(state),
        {
            "load_data": "load_data",
            "analyze_logs": "analyze_logs",
            "analyze_metrics": "analyze_metrics",
            "run_rca": "run_rca",
            "request_more_data": "request_more_data",
            "calculate_business_impact": "calculate_business_impact",
            "generate_summary": "generate_summary",
            "complete": END
        }
    )
    
    # All agents route back to router
    for node in ["load_data", "analyze_logs", "analyze_metrics", "request_more_data", "calculate_business_impact"]:
        graph.add_edge(node, "route_next_action")
    
    # RCA has conditional edge (confidence check)
    graph.add_conditional_edges(
        "run_rca",
        should_request_more_data,
        {
            "low_confidence": "request_more_data",
            "high_confidence": "route_next_action"
        }
    )
    
    # Summary goes to end
    graph.add_edge("generate_summary", END)
    
    # Set entry point
    graph.set_entry_point("route_next_action")
    
    return graph.compile()
```

FILE 2: agents/router_agent.py
PURPOSE: Router decides what to do next
REQUIREMENTS:
- Function route_next_action(state: IncidentState) -> str
- Check completed_steps set
- Return: "load_data", "analyze_logs", "analyze_metrics", "run_rca", "request_more_data", "calculate_business_impact", "generate_summary", or "complete"
- Logic: IF/ELSE tree checking what's been done
- Log decision (use print for debugging)

CODE STRUCTURE:
```python
from agents import IncidentState

def route_next_action(state: IncidentState) -> str:
    state.analysis_iterations += 1
    
    if not state.raw_logs:
        return "load_data"
    elif "log_analysis" not in state.completed_steps:
        return "analyze_logs"
    elif "metrics_analysis" not in state.completed_steps:
        return "analyze_metrics"
    elif "rca_analysis" not in state.completed_steps:
        return "run_rca"
    elif state.rca_confidence < 0.7 and state.analysis_iterations < state.max_iterations:
        return "request_more_data"
    elif "business_impact" not in state.completed_steps:
        return "calculate_business_impact"
    elif "summary" not in state.completed_steps:
        return "generate_summary"
    else:
        return "complete"

def should_request_more_data(state: IncidentState) -> str:
    if state.rca_confidence < 0.7 and state.analysis_iterations < state.max_iterations:
        return "low_confidence"
    else:
        return "high_confidence"
```

FILE 3: agents/tools.py
PURPOSE: Tool definitions for agents to use
REQUIREMENTS:
- Import: langchain_core.tools
- Define 5+ tools: fetch_logs, fetch_metrics, fetch_deployments, get_service_config, call_claude
- Each tool has @tool decorator
- Each tool has docstring
- Tools load from mock_data

CODE STRUCTURE:
```python
from langchain_core.tools import tool
from typing import List, Dict, Any
from agents.mock_data import load_logs, load_metrics, load_deployments, load_service_config

@tool
def fetch_logs(service: str, limit: int = 100) -> List[Dict[str, Any]]:
    logs = load_logs(service)
    return logs[:limit]

@tool
def fetch_metrics(service: str) -> List[Dict[str, Any]]:
    return load_metrics(service)

@tool
def fetch_deployments(service: str) -> List[Dict[str, Any]]:
    return load_deployments(service)

@tool
def get_service_config(service: str) -> Dict[str, Any]:
    return load_service_config(service)

@tool
async def call_claude_for_rca(logs_str: str, metrics_str: str, deployments_str: str) -> Dict[str, Any]:
    import json
    from anthropic import Anthropic
    import os
    
    api_key = os.getenv("ANTHROPIC_API_KEY")
    client = Anthropic(api_key=api_key)
    
    prompt = f"""Analyze this incident and determine the root cause.

Log anomalies: {logs_str}
Metric anomalies: {metrics_str}
Deployment changes: {deployments_str}

Respond with JSON only (no markdown):
{{
  "hypothesis": "...",
  "confidence": 0.85,
  "supporting_evidence": ["...", "...", "..."]
}}"""
    
    message = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    
    response_text = message.content[0].text.strip()
    if response_text.startswith("```json"):
        response_text = response_text[7:]
    if response_text.startswith("```"):
        response_text = response_text[3:]
    if response_text.endswith("```"):
        response_text = response_text[:-3]
    
    result = json.loads(response_text.strip())
    return result
```

FILE 4: agents/request_more_data_agent.py
PURPOSE: Request deeper analysis when confidence too low
REQUIREMENTS:
- Function request_more_data(state: IncidentState) -> IncidentState
- Perform deeper analysis on logs
- Update completed_steps but keep rca_analysis status incomplete
- Return updated state

CODE STRUCTURE:
```python
from agents import IncidentState
from agents.log_analysis_agent import log_analysis

def request_more_data(state: IncidentState) -> IncidentState:
    state.current_status = "requesting_deeper_analysis"
    
    state.log_anomalies = []
    state = log_analysis(state)
    
    state.completed_steps.discard("rca_analysis")
    
    state.agent_invocations.append({
        "agent": "request_more_data_agent",
        "action": "request_deeper_analysis",
        "iteration": state.analysis_iterations
    })
    
    return state
```

===== PHASE 2: MODIFY 5 EXISTING FILES =====

FILE 1: agents/__init__.py
MODIFICATION: Enhance IncidentState with agentic fields
ADD THESE IMPORTS AT TOP:
```python
from dataclasses import dataclass, field
from typing import List, Dict, Any, Set
```

MODIFY IncidentState class - ADD AFTER EXISTING FIELDS:
```python
    completed_steps: Set[str] = field(default_factory=set)
    analysis_iterations: int = 0
    rca_confidence: float = 0.0
    max_iterations: int = 5
    current_status: str = "initial"
```

FILE 2: agents/rca_agent.py
MODIFICATION: Add Claude integration and confidence loop
REPLACE the entire function with:
```python
async def rca_analysis_with_claude(state: "IncidentState") -> "IncidentState":
    from agents.tools import call_claude_for_rca
    import json
    
    logs_str = json.dumps(state.log_anomalies, default=str)
    metrics_str = json.dumps(state.metric_anomalies, default=str)
    deployments_str = json.dumps(state.deployment_changes, default=str)
    
    result = await call_claude_for_rca(logs_str, metrics_str, deployments_str)
    
    state.root_cause = {
        "hypothesis": result.get("hypothesis", "Unknown"),
        "confidence": result.get("confidence", 0.5),
        "supporting_evidence": result.get("supporting_evidence", [])
    }
    
    state.rca_confidence = result.get("confidence", 0.5)
    state.completed_steps.add("rca_analysis")
    
    state.agent_invocations.append({
        "agent": "rca_agent",
        "action": "run_rca_with_claude",
        "hypothesis": state.root_cause["hypothesis"],
        "confidence": state.rca_confidence,
        "iteration": state.analysis_iterations
    })
    
    return state
```

FILE 3: app.py
MODIFICATION: Use agentic graph instead of linear calls
REPLACE the /api/incidents/trigger endpoint with:
```python
@app.post("/api/incidents/trigger")
async def trigger_incident(request: Dict[str, Any] = Body(...)):
    from agents.agentic_system import create_incident_analysis_graph
    import uuid
    
    state = IncidentState(
        incident_id=str(uuid.uuid4()),
        timestamp=request.get("timestamp", ""),
        alert_description=request.get("alert_description", ""),
        service=request.get("service", "unknown"),
        severity=request.get("severity", "unknown")
    )
    
    graph = create_incident_analysis_graph()
    result = await graph.ainvoke(state)
    
    return {
        "incident_id": result.incident_id,
        "timestamp": result.timestamp,
        "service": result.service,
        "severity": result.severity,
        "analysis_iterations": result.analysis_iterations,
        "rca_confidence": result.rca_confidence,
        "root_cause": result.root_cause,
        "affected_users": result.affected_users,
        "estimated_revenue_impact_per_minute": result.estimated_revenue_impact_per_minute,
        "engineering_summary": result.engineering_summary,
        "executive_summary": result.executive_summary,
        "recovery_recommendations": result.recovery_recommendations,
        "agent_invocations": result.agent_invocations
    }
```

FILE 4: requirements.txt
MODIFICATION: Add new dependencies
ADD THESE LINES (keep existing dependencies):
```
langgraph==0.1.0
langchain-core>=0.1.0
langchain-anthropic>=0.1.0
anthropic>=0.25.0
```

FILE 5: .env (NEW FILE, NOT COMMITTED)
CREATE this file (DO NOT COMMIT TO GIT):
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
SERVICE=local
LOG_LEVEL=INFO
```

===== PHASE 3: FIX IMPORTS =====

UPDATE: agents/__init__.py
ADD import for async types:
```python
from typing import Awaitable
```

UPDATE: agents/rca_agent.py
REPLACE: `def rca_analysis(...)` with `async def rca_analysis_with_claude(...)`

UPDATE: agents/log_analysis_agent.py
ENSURE: Returns IncidentState properly

UPDATE: agents/metrics_analysis_agent.py
ENSURE: Returns IncidentState properly

UPDATE: agents/business_impact_agent.py
ENSURE: Returns IncidentState properly

UPDATE: agents/executive_summary_agent.py
ENSURE: Returns IncidentState properly

UPDATE: app.py
ADD: `from typing import Dict, Any`
ADD: `from pydantic import Body`
ADD: `import json`

===== PHASE 4: CREATE/UPDATE TESTS =====

FILE: tests/test_agentic_flow.py (NEW)
CREATE with these test cases:
```python
import pytest
from agents import IncidentState
from agents.agentic_system import create_incident_analysis_graph
from agents.router_agent import route_next_action, should_request_more_data

def test_router_decides_next_action():
    state = IncidentState(
        incident_id="test1",
        timestamp="2026-07-07T14:32:15Z",
        alert_description="Test",
        service="payment-api",
        severity="critical"
    )
    
    action = route_next_action(state)
    assert action == "load_data"

def test_router_with_completed_steps():
    state = IncidentState(
        incident_id="test2",
        timestamp="2026-07-07T14:32:15Z",
        alert_description="Test",
        service="payment-api",
        severity="critical",
        raw_logs=["log1"],
        completed_steps={"load_data"}
    )
    
    action = route_next_action(state)
    assert action == "analyze_logs"

def test_should_request_more_data_low_confidence():
    state = IncidentState(
        incident_id="test3",
        timestamp="2026-07-07T14:32:15Z",
        alert_description="Test",
        service="payment-api",
        severity="critical",
        rca_confidence=0.62,
        analysis_iterations=2,
        max_iterations=5
    )
    
    result = should_request_more_data(state)
    assert result == "low_confidence"

def test_should_request_more_data_high_confidence():
    state = IncidentState(
        incident_id="test4",
        timestamp="2026-07-07T14:32:15Z",
        alert_description="Test",
        service="payment-api",
        severity="critical",
        rca_confidence=0.85,
        analysis_iterations=2
    )
    
    result = should_request_more_data(state)
    assert result == "high_confidence"

@pytest.mark.asyncio
async def test_agentic_graph_completes():
    from agents.agentic_system import create_incident_analysis_graph
    
    graph = create_incident_analysis_graph()
    assert graph is not None

def test_analysis_iterations_increment():
    state = IncidentState(
        incident_id="test5",
        timestamp="2026-07-07T14:32:15Z",
        alert_description="Test",
        service="payment-api",
        severity="critical"
    )
    
    initial_iterations = state.analysis_iterations
    route_next_action(state)
    assert state.analysis_iterations > initial_iterations
```

===== PHASE 5: INSTALLATION & VERIFICATION =====

STEP 1: Install new dependencies
```bash
pip install -r requirements.txt
```

STEP 2: Verify imports
```bash
python -c "import langgraph; print('✅ langgraph')"
python -c "import anthropic; print('✅ anthropic')"
python -c "from agents.agentic_system import create_incident_analysis_graph; print('✅ agentic_system')"
python -c "from agents.router_agent import route_next_action; print('✅ router_agent')"
```

STEP 3: Run pytest
```bash
pytest tests/ -v
```

STEP 4: Verify all tests pass (should see 20+ passing)

STEP 5: Start server and test
```bash
python app.py &
sleep 2
curl -X POST http://localhost:8000/api/incidents/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "timestamp": "2026-07-07T14:32:15Z",
    "alert_description": "Database connection pool exhaustion detected",
    "service": "payment-api",
    "severity": "critical"
  }'
```

EXPECTED RESPONSE (JSON with these fields):
- incident_id: uuid
- analysis_iterations: 4-6 (shows looping!)
- rca_confidence: 0.75-0.90 (from Claude!)
- root_cause: {hypothesis, confidence, supporting_evidence}
- affected_users: number
- estimated_revenue_impact_per_minute: number
- engineering_summary: string
- executive_summary: string
- agent_invocations: array of agent actions

===== PHASE 6: GIT COMMIT =====

COMMANDS:
```bash
git add .
git status  # Verify .env is NOT listed (should be gitignored)
git commit -m "Implement truly agentic incident response system with LangGraph

- Add LangGraph StateGraph for autonomous agent orchestration
- Implement RouterAgent for dynamic decision-making
- Add Claude-powered RCA with reasoning loops
- Implement conditional routing and iterative analysis
- Integrate Anthropic SDK for intelligent reasoning
- Add tool definitions for agent actions
- Enhance IncidentState with agentic fields
- Create new agents: RouterAgent, RequestMoreDataAgent
- Update tests for agentic flow validation
- All 20+ tests passing, fully functional"
git push origin main
```

===== VERIFICATION CHECKLIST =====

After completion, verify:
- [ ] All 4 new files created (agentic_system.py, router_agent.py, tools.py, request_more_data_agent.py)
- [ ] All 5 files modified correctly
- [ ] .env file exists with ANTHROPIC_API_KEY
- [ ] .env in .gitignore
- [ ] requirements.txt updated
- [ ] pip install -r requirements.txt succeeds
- [ ] All 20+ tests pass
- [ ] Server starts: python app.py
- [ ] API call returns analysis_iterations (shows agentic behavior)
- [ ] API call returns rca_confidence from Claude (0.75-0.90)
- [ ] Git commit succeeds
- [ ] .env not in git history

===== EXPECTED RESULTS =====

Analysis Iteration Counts:
- Scenario 1 (DB Pool): 4-5 iterations
- Scenario 2 (Memory Leak): 6-8 iterations (loops once)
- Scenario 3 (Cascading): 5-6 iterations

RCA Confidence (from Claude):
- All scenarios: 0.75-0.90 range

Agent Invocations (logged):
- Sample: [
    {"agent": "load_data_agent", "action": "load_data", "iteration": 1},
    {"agent": "log_analysis_agent", "action": "analyze_logs", "iteration": 2},
    {"agent": "metrics_analysis_agent", "action": "analyze_metrics", "iteration": 3},
    {"agent": "rca_agent", "action": "run_rca_with_claude", "confidence": 0.85, "iteration": 4},
    ...
  ]

===== SUCCESS INDICATORS =====

System is truly agentic when:
✅ analysis_iterations > 1 (shows router looping)
✅ rca_confidence between 0.75-0.90 (Claude reasoning)
✅ Different scenarios show different iteration counts
✅ RCA loops when confidence < 0.7
✅ All tests pass (20+)
✅ No hardcoded keys in code
✅ .env excluded from git

===== NOW EXECUTE =====

1. Create all 4 new files with exact code above
2. Modify all 5 existing files as specified
3. Add .env file with ANTHROPIC_API_KEY
4. Run: pip install -r requirements.txt
5. Run: pytest tests/ -v (should show 20+ passing)
6. Run: python app.py
7. Test: curl to /api/incidents/trigger
8. Verify agentic behavior (iterations, confidence)
9. Commit: git add . && git commit && git push
10. Done! System is truly agentic ✨

BEGIN EXECUTION NOW.
