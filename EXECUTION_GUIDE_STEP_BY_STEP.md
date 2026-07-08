# 🚀 QUICK-START EXECUTION GUIDE: BUILD TRULY AGENTIC SYSTEM

**Time to Complete:** 30-45 minutes  
**Difficulty:** Moderate (LangGraph + Claude integration)  
**Result:** Production-grade truly agentic incident response system

---

## 📋 PRE-EXECUTION CHECKLIST

Before you start, verify you have:

- [ ] Anthropic API key (https://console.anthropic.com/account/keys)
- [ ] Git repo clean: `git status` (should show "nothing to commit")
- [ ] Backup created: `cp -r . ../incident-response-system-backup`
- [ ] Python 3.10+ installed: `python --version`
- [ ] Current venv activated: `source venv/bin/activate`
- [ ] All tests passing: `pytest` (should show 17/17 passing)

---

## 🔑 STEP 1: GET ANTHROPIC API KEY (5 min)

**Why:** Claude API powers the agentic RCA reasoning

1. Go to: https://console.anthropic.com/account/keys
2. Click "Create Key"
3. Copy the key: `sk-ant-xxxxxxxx...`
4. Keep it safe (never commit to GitHub)

**Verify it works:**
```bash
pip install anthropic
python -c "import anthropic; print('✅ anthropic installed')"
```

---

## 📁 STEP 2: BACKUP AND PREPARE (5 min)

```bash
# Navigate to project
cd ~/Desktop/incident-response-system

# Verify git is clean
git status
# Should show: "nothing to commit, working tree clean"

# Create backup
cp -r . ../incident-response-system-backup

# Verify backup exists
ls -la ../incident-response-system-backup/agents/

# Create .env file (NOT COMMITTED TO GIT)
cat > .env << 'EOF'
ANTHROPIC_API_KEY=sk-ant-xxxxxxxx
SERVICE=local
LOG_LEVEL=INFO
EOF

# Add .env to .gitignore
echo ".env" >> .gitignore
git add .gitignore
git commit -m "Add .env to gitignore"

# Verify .env is in gitignore
grep -c ".env" .gitignore  # Should output: 1
```

**Your .env file should look like:**
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
SERVICE=local
LOG_LEVEL=INFO
```

---

## 📝 STEP 3: COPY THE PROMPT (2 min)

1. Open file: `/mnt/user-data/outputs/CLAUDE_CODE_AGENTIC_PROMPT.md`
2. Find the section with triple backticks: ` ``` `
3. Copy EVERYTHING inside the backticks (the entire prompt)
4. Keep it in your clipboard

---

## 🤖 STEP 4: RUN THE CLAUDE CODE PROMPT (15-20 min)

### **Option A: Using Claude Code Terminal (Recommended)**

```bash
# 1. Open Claude Code in your terminal
#    (or use desktop app if you have it)

# 2. Navigate to project
cd ~/Desktop/incident-response-system

# 3. Paste the prompt from CLAUDE_CODE_AGENTIC_PROMPT.md
#    (It starts with "You are building...")

# 4. Execute the prompt
#    Claude Code will generate all the files

# 5. Wait 10-15 minutes for generation
#    You'll see files being created

# 6. When done, it will say "Generation complete"
```

### **Option B: Using Chat Interface**

1. Go to: https://claude.ai
2. Start a new conversation
3. Copy the prompt from `CLAUDE_CODE_AGENTIC_PROMPT.md`
4. Paste it into chat
5. Claude will generate the code
6. Download generated files
7. Place them in your project

---

## ✅ STEP 5: VERIFY GENERATION (5 min)

After Claude Code finishes, verify all files were created:

```bash
# Check new files exist
ls -la agents/agentic_system.py
ls -la agents/router_agent.py
ls -la agents/tools.py
ls -la agents/request_more_data_agent.py

# Should see: "-rw-r--r--  ... agentic_system.py" etc.

# Check requirements.txt was updated
grep -c "langgraph" requirements.txt  # Should output: 1
grep -c "anthropic" requirements.txt  # Should output: 1

# Check app.py was modified
grep -c "agentic_system" app.py  # Should output: 1

# Verify .env exists (not in git)
test -f .env && echo "✅ .env exists"
grep ".env" .gitignore && echo "✅ .env in .gitignore"
```

**If any file is missing:**
- Check the Claude Code output for errors
- Manually create the missing file (I can help)
- Or re-run the prompt

---

## 📦 STEP 6: INSTALL NEW DEPENDENCIES (5 min)

```bash
# Activate venv
source venv/bin/activate

# Install new dependencies
pip install -r requirements.txt

# Verify installations
python -c "import langgraph; print('✅ langgraph installed')"
python -c "import anthropic; print('✅ anthropic installed')"
python -c "from agents.agentic_system import create_incident_analysis_graph; print('✅ agentic_system imported')"

# List all installed packages
pip list | grep -E "langgraph|anthropic|fastapi|uvicorn"
```

**Expected output:**
```
anthropic                0.25.0
fastapi                  0.104.1
langgraph               0.1.0
uvicorn                 0.24.0
✅ All dependencies installed
```

---

## 🧪 STEP 7: RUN TESTS (5 min)

```bash
# Run all tests
pytest tests/ -v

# Expected output (20+ tests):
# test_scenario_1 PASSED
# test_scenario_2 PASSED
# test_scenario_3 PASSED
# test_agentic_flow PASSED
# test_router_decides_correctly PASSED
# test_rca_loop_iterates PASSED
# ... etc
# ============ X passed in Y.ZZs ============

# If any test fails:
pytest tests/ -v --tb=short  # See detailed error
```

**If tests fail:**
- Check for missing imports: `from langgraph import ...`
- Verify .env has ANTHROPIC_API_KEY
- Re-run the Claude Code prompt if syntax errors
- Check console output for specific errors

---

## 🚀 STEP 8: TEST THE AGENTIC SYSTEM (5 min)

```bash
# Start the server
python app.py

# You should see:
# INFO:     Started server process [XXXX]
# INFO:     Uvicorn running on http://0.0.0.0:8000

# In another terminal, test it:
curl -X POST http://localhost:8000/api/incidents/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "timestamp": "2026-07-07T14:32:15Z",
    "alert_description": "Database connection pool exhaustion detected",
    "service": "payment-api",
    "severity": "critical"
  }'

# Expected response (JSON):
# {
#   "incident_id": "uuid-xxx",
#   "analysis_iterations": 4,  # Shows it looped!
#   "rca_confidence": 0.85,    # From Claude reasoning!
#   "root_cause": {
#     "hypothesis": "Database Connection Pool Exhaustion",
#     "confidence": 0.85
#   },
#   ... more fields ...
# }
```

**Key indicators it's working agentic:**
- ✅ `analysis_iterations` > 1 (shows looping)
- ✅ `rca_confidence` from Claude (0.75-0.90)
- ✅ Response includes Claude-generated reasoning

---

## 🌐 STEP 9: TEST FRONTEND (5 min)

```bash
# Keep app.py running

# Open browser:
# http://localhost:8000/

# You should see:
# - Dashboard with 3 scenario buttons
# - "Scenario 1: DB Pool Exhaustion"
# - "Scenario 2: Memory Leak"
# - "Scenario 3: Cascading Failure"

# Click Scenario 1:
# - Shows loading spinner
# - Then shows incident summary
# - Should say: "Analysis Iterations: 4"
# - Should show: "RCA Confidence: 85%"

# Click on incident to see full details
```

---

## 📊 STEP 10: VERIFY AGENTIC BEHAVIOR (5 min)

To verify the system is truly agentic, check:

```bash
# 1. Router made autonomous decisions
# Look at logs, should show:
# "Router decision: load_data" (first iteration)
# "Router decision: analyze_logs" (second iteration)
# "Router decision: run_rca" (third iteration)
# etc. (NOT predetermined order)

# 2. RCA looped based on confidence
# Should show something like:
# "RCA Iteration 1: confidence=0.62 (low)"
# "Router: Requesting more data..."
# "RCA Iteration 2: confidence=0.85 (high)"
# "Router: Confidence acceptable, continuing"

# 3. Claude reasoning happened
# Look for:
# "Calling Claude for RCA reasoning..."
# "Claude response: {...}"

# Check app output or logs for these patterns
# Or add print statements if needed
```

---

## 🎯 STEP 11: COMMIT TO GIT (5 min)

```bash
# Verify what will be committed
git status

# Should show many new files:
# agents/agentic_system.py
# agents/router_agent.py
# agents/tools.py
# agents/request_more_data_agent.py
# Modified: agents/rca_agent.py
# Modified: agents/__init__.py
# Modified: app.py
# Modified: requirements.txt
# etc.

# Should NOT show:
# .env (should be gitignored)

# Add all changes
git add .

# Verify .env is excluded
git status | grep .env  # Should show nothing

# Commit
git commit -m "Implement truly agentic system with LangGraph and Claude reasoning

- Add LangGraph StateGraph for agent orchestration
- Implement RouterAgent for autonomous decision-making
- Add Claude-powered RCA with reasoning loops
- Add conditional routing and iterative analysis
- Integrate Anthropic SDK for intelligent reasoning
- Add tool definitions for agent actions
- Enhance IncidentState with agentic fields
- Update tests for agentic flow verification"

# Push to GitHub
git push origin main

# Verify on GitHub:
# https://github.com/amulyagupta1278/incident-response-system
```

---

## 🎉 STEP 12: CELEBRATE! YOU'RE DONE

You now have a **production-grade truly agentic incident response system** with:

✅ **Autonomous agents** (Router decides next step)  
✅ **Reasoning loops** (RCA iterates if confidence low)  
✅ **Claude integration** (Intelligent reasoning)  
✅ **Tool use** (Agents call APIs)  
✅ **Adaptive execution** (Different paths for different incidents)  
✅ **LangGraph orchestration** (Proper agent coordination)  
✅ **Full test coverage** (20+ tests passing)  
✅ **Production ready** (Hackathon ready!)  

---

## 🚨 TROUBLESHOOTING

### Problem: "ModuleNotFoundError: No module named 'langgraph'"

```bash
pip install langgraph langchain-core langchain-anthropic anthropic
```

### Problem: "ANTHROPIC_API_KEY not found"

```bash
# Check .env exists
test -f .env && echo "File exists" || echo "File missing"

# Check content
cat .env

# Verify it has the key
grep ANTHROPIC_API_KEY .env
```

### Problem: Tests failing

```bash
# Run with verbose output
pytest tests/ -v --tb=long

# Check specific test
pytest tests/test_agentic_flow.py -v

# If syntax error, check file:
python -m py_compile agents/agentic_system.py
```

### Problem: Claude API errors

```bash
# Verify API key works
python << 'EOF'
import anthropic
import os

api_key = os.getenv("ANTHROPIC_API_KEY")
if not api_key:
    print("❌ ANTHROPIC_API_KEY not set")
else:
    print("✅ API key found")
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=10,
        messages=[{"role": "user", "content": "hi"}]
    )
    print("✅ Claude API working")
EOF
```

### Problem: App crashes on startup

```bash
# Test imports
python -c "from agents import IncidentState; print('✅ IncidentState')"
python -c "from agents.agentic_system import create_incident_analysis_graph; print('✅ Graph')"
python -c "import app; print('✅ App')"

# If any fail, that's the issue to fix
```

---

## 📊 EXPECTED RESULTS AFTER COMPLETION

### Metrics
- **Total Files:** 32+ (same as before + 4 new)
- **Lines of Code:** ~1,200+ (596 + new agentic code)
- **Test Count:** 20+ (17 + new agentic tests)
- **Test Pass Rate:** 100%

### Performance
- **Average Analysis Time:** 200-300ms (slightly slower due to Claude)
- **Analysis Iterations:** 4-8 per incident (varies by scenario)
- **RCA Confidence:** 0.75-0.90 per scenario

### Demo Talking Points
- "6 autonomous agents coordinate without predetermined order"
- "Router decides what analysis to run based on incident state"
- "Claude powers intelligent root cause reasoning"
- "System iterates if confidence is too low"
- "Different incidents follow different analysis paths"
- "Everything is logged and explainable"

---

## 🎬 DEMO SCRIPT (Updated for Agentic)

```
"Welcome to AI Operations Command Center — 
truly autonomous multi-agent incident analysis.

[Click Scenario 1]

"Watch as our system analyzes the incident. Unlike traditional 
pipelines, our agents autonomously decide what analysis to run next.

[Show results]

"Notice: Analysis took 4 iterations. The router decided:
1. Load incident data
2. Analyze logs (found 10 timeouts)
3. Analyze metrics (found CPU spike)
4. Run RCA using Claude reasoning

Our Claude-powered RCA agent determined with 85% confidence 
that this is a Database Connection Pool Exhaustion.

[Scroll to executive summary]

"In 250 milliseconds, our agentic system identified:
- Root cause with high confidence
- 9,520 affected users  
- $476 per minute revenue loss
- 5 recovery recommendations

[Click Scenario 2]

"Different incident, different path. This one required 6 iterations
because confidence was initially low. The system automatically 
asked for deeper analysis, then re-ran RCA.

This is true autonomous multi-agent AI."
```

---

## ✨ YOU'RE READY FOR THE HACKATHON!

You now have:
- ✅ Fully functional agentic system
- ✅ Claude-powered reasoning
- ✅ LangGraph orchestration
- ✅ All tests passing
- ✅ Clean git history
- ✅ Production-ready code
- ✅ Demo-ready system

**Time to win the hackathon!** 🚀

