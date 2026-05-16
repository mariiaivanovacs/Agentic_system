# 🤝 EcoLink NeuroCore — Team Collaboration Guide
**Hackathon: Build With AI 2026 KL — MyHack | 16–17 May 2026**

> **One rule above all:** If you didn't create the file, ask before editing it.

---

## 👥 Team Roles & Responsibilities

| Role | Person | Owns | Does NOT touch |
|------|--------|------|----------------|
| **Backend / Neo4j Dev** | TBD | Graph schema, seed data, Cypher queries, Neo4j connection | Frontend, Gemini calls |
| **AI / GraphRAG Dev** | Leila | `retriever.py`, `generator.py`, `prompt_engine.py`, `validator.py`, `main_graphrag.py` | Frontend, seed scripts |
| **Frontend Dev** | TBD | React UI, graph visualization, approval dashboard | Any Python backend files |
| **Cloud / Sandbox Dev** | TBD | `seed_graph.py`, deployment, `.env` setup, requirements | Core GraphRAG logic |

---

## 📁 Folder Ownership (Who Owns What)

```
MyHack/
├── scripts/
│   └── seed_graph.py         ← 🟡 Cloud/Sandbox Dev ONLY
│
├── src/
│   └── graphrag/
│       ├── retriever.py      ← 🔴 AI/GraphRAG Dev (Leila) ONLY
│       ├── generator.py      ← 🔴 AI/GraphRAG Dev (Leila) ONLY
│       ├── prompt_engine.py  ← 🔴 AI/GraphRAG Dev (Leila) ONLY
│       └── validator.py      ← 🔴 AI/GraphRAG Dev (Leila) ONLY
│
├── frontend/
│   ├── src/components/       ← 🔵 Frontend Dev ONLY
│   └── src/pages/            ← 🔵 Frontend Dev ONLY
│
├── main_graphrag.py          ← 🔴 AI/GraphRAG Dev (Leila) ONLY
├── .env                      ← ⚠️ EVERYONE — never commit this to GitHub!
├── requirements.txt          ← 🟡 Cloud Dev manages, others request additions
└── description.md            ← 📖 Read only — reference document
```

---

## 🌿 Git & GitHub Workflow

### Branch Naming
```
main              ← stable working code only — never push directly here
dev               ← integration branch — merge into this first

feature/leila-graphrag-retriever
feature/leila-gemini-generator
feature/[name]-seed-graph
feature/[name]-frontend-ui
feature/[name]-visualization
fix/[name]-neo4j-connection
```

### Daily Workflow (Every Team Member)
```bash
# Start of day — get latest code
git checkout dev
git pull origin dev

# Create your branch
git checkout -b feature/leila-graphrag-retriever

# Work on your files only
# ... make changes ...

# Save your work
git add .
git commit -m "feat: add success pattern retriever for Healthtech"
git push origin feature/leila-graphrag-retriever

# When ready — open a Pull Request into dev (not main!)
# Tell team in WhatsApp/Telegram before merging
```

### ⚠️ Golden Rules
- **Never `git push origin main` directly**
- **Never commit `.env`** — add it to `.gitignore`
- Always pull from `dev` before starting new work
- One PR = one feature. Keep it small.

---

## 🔌 API Contracts (How Components Talk to Each Other)

### Contract 1: GraphRAG Engine → Frontend
Leila's `main_graphrag.py` must return this JSON structure so Frontend can display it:

```json
{
  "goal": "Optimize Fintech matching",
  "industry": "Fintech",
  "reasoning_trace": "Successful matches relied on semantic alignment between pain_points and expertise...",
  "proposed_flow": {
    "flow_id": "fintech_optimized_v1",
    "steps": [
      { "skill": "semantic_similarity", "params": { "source": "company.pain_points", "target": "mentor.expertise" } },
      { "skill": "sort_by_score_desc", "params": {} }
    ]
  },
  "status": "valid",
  "errors": []
}
```

### Contract 2: Neo4j → GraphRAG Retriever
Backend must ensure these nodes exist in Neo4j for Leila's retriever to work:

```cypher
// Company node must have these fields:
(:Company { id, name, industry, stage, pain_points, revenue })

// Mentor node must have these fields:
(:Mentor { id, name, expertise, success_score, available })

// Relationship must have:
(c:Company)-[:MATCHED_WITH { outcome_score, feedback, date }]->(m:Mentor)

// Skills must exist:
(:Skill { name, description, input_schema })
```

### Contract 3: Frontend → GraphRAG Engine
Frontend calls the backend with this format:

```json
POST /api/optimize
{
  "goal": "Improve mentor matching for Healthtech startups",
  "industry": "Healthtech",
  "output_file": "healthtech_flow.yaml"
}
```

---

## 📋 MVP Feature Checklist (Priority Order)

### 🔴 Must Have (Demo blockers — finish these first)
- [ ] Neo4j seeded with at least 5 companies, 5 mentors, 5 relationships
- [ ] `retriever.py` pulls historical matches from Neo4j
- [ ] `generator.py` calls Gemini API and gets a response
- [ ] `main_graphrag.py` runs end-to-end and prints a recommendation
- [ ] Basic UI shows the recommendation output

### 🟡 Should Have (Makes demo impressive)
- [ ] Graph visualization showing nodes and relationships
- [ ] Reasoning trace displayed in UI
- [ ] At least 3 industries working (Fintech, Healthtech, EdTech)
- [ ] Proposed YAML flow displayed and validated
- [ ] Approve/Reject buttons in UI

### 🟢 Nice to Have (Only if time allows)
- [ ] Real-time graph updates when new match is approved
- [ ] Multiple flow proposals compared side by side
- [ ] Admin dashboard with metrics

---

## ⏰ Integration Checkpoints

### Checkpoint 1 — Day 1, 12:00 PM
**Goal:** Everyone can connect to Neo4j
- [ ] Backend: Neo4j seeded with test data
- [ ] AI Dev: `.env` file configured, can run `retriever.py`
- [ ] All: Can run `python scripts/seed_graph.py` without errors

### Checkpoint 2 — Day 1, 6:00 PM
**Goal:** GraphRAG engine works end-to-end in terminal
- [ ] `python main_graphrag.py --goal "Optimize Fintech" --industry Fintech` prints output
- [ ] Frontend: Basic React app running locally
- [ ] Cloud: All dependencies in `requirements.txt` working

### Checkpoint 3 — Day 2, 10:00 AM
**Goal:** Frontend connected to backend
- [ ] API endpoint `/api/optimize` returns JSON
- [ ] Frontend displays recommendation from real GraphRAG engine
- [ ] Graph visualization shows at least nodes and edges

### Checkpoint 4 — Day 2, 2:00 PM ← DEMO PREP
**Goal:** Full demo flow works
- [ ] Complete user journey works: input → GraphRAG → recommendation → UI
- [ ] README updated with how to run the project
- [ ] Demo script rehearsed by all team members

---

## 💬 Communication Workflow

```
🔴 BLOCKING ISSUE (can't continue working)
→ Message team group chat immediately with error screenshot

🟡 NEED SOMETHING FROM TEAMMATE (their file/API not ready)
→ Message them directly, set a max 30-min wait before finding workaround

🟢 FINISHED A FEATURE
→ Push branch + message group chat: "✅ retriever.py done, PR open"

📋 DAILY STANDUP (5 mins, every morning)
→ Each person says: Done / Doing / Blocked
```

---

## 🚨 Emergency Debugging Workflow

When something breaks during the hackathon:

```
Step 1: Screenshot the error
Step 2: Identify WHICH file caused the error
Step 3: Only the OWNER of that file fixes it
Step 4: If owner is stuck > 15 mins → call for help
Step 5: Never fix someone else's file without telling them
```

### Common Errors & Quick Fixes

| Error | Likely Cause | Fix |
|-------|-------------|-----|
| `NEO4J_URI not set` | `.env` file missing or wrong path | Check `.env` exists in root folder |
| `Connection refused` | Neo4j AuraDB paused | Go to console.neo4j.io and resume instance |
| `Gemini API error 403` | Wrong API key | Check `GEMINI_API_KEY` in `.env` |
| `No nodes found` | Seed script not run | Run `python scripts/seed_graph.py` |
| `Module not found` | Missing package | Run `pip install -r requirements.txt` |
| `YAML validation error` | Gemini generated bad YAML | Check `validator.py` error message, retry |

---

## 🚀 Deployment Ownership

| What | Who Deploys | How |
|------|-------------|-----|
| Neo4j Database | Cloud/Sandbox Dev | Neo4j AuraDB (already set up) |
| Python Backend | Cloud/Sandbox Dev | Run locally or `python main_graphrag.py` |
| React Frontend | Frontend Dev | `npm run dev` locally |
| Demo environment | Cloud/Sandbox Dev | Ensure all services running before demo |

---

## 🔐 Secrets Management

**The `.env` file contains passwords. Follow these rules:**

```bash
# Add to .gitignore immediately
echo ".env" >> .gitignore

# Share credentials ONLY via WhatsApp/Telegram DM — never in code or GitHub
# Current shared credentials:
# NEO4J_URI=neo4j+s://fe272a21.databases.neo4j.io
# NEO4J_USERNAME=fe272a21
# NEO4J_PASSWORD= [share privately]
# GEMINI_API_KEY= [share privately]
```

---

## 📞 Quick Reference — Who to Ask

| Question | Ask |
|----------|-----|
| "Why is Neo4j empty?" | Backend Dev |
| "Why is Gemini not responding?" | Leila (AI Dev) |
| "Why is the UI broken?" | Frontend Dev |
| "How do I install dependencies?" | Cloud Dev |
| "What does the graph schema look like?" | Backend Dev |
| "What does the API return?" | Leila (AI Dev) |

---

*Last updated: 16 May 2026 — EcoLink NeuroCore Team*