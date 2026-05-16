Yes, you absolutely need a **"Dummy Legacy Infrastructure"** to test your system.

Why? Because your solution’s value proposition is **"automating ad-hoc relationships in existing ecosystems."** If you start with a perfect, clean database, there is no problem to solve. You need to simulate the **messiness** of real-world companies (different data formats, siloed systems, manual processes) so your Agent can demonstrate how it **connects, cleans, and optimizes** them.

Here is the exact **Mock Infrastructure** you should build. It is designed to be simple enough to code in 1 day but complex enough to show off your Dual-Graph + Agent architecture.

---

### 🏢 The Scenario: "KL Tech Accelerator"
You are simulating an accelerator program that manages:
1.  **Startups** (Companies)
2.  **Mentors** (Experts)
3.  **Programs** (Cohorts)

Currently, they use three disconnected, messy systems. Your Agent will bridge them.

---

### 1. The "Legacy" Data Sources (Graph A Inputs)

You need three distinct data sources to prove your **Connectors** work.

#### A. The "Old SQL" Database (PostgreSQL/SQLite)
*   **Represents:** The core registry of Startups.
*   **Data Structure:** `startups_table`
    *   `id`, `name`, `industry` (e.g., "Fintech"), `stage` (e.g., "Seed"), `founder_email`.
*   **The "Mess":** Some industries are spelled differently ("FinTech", "fintech", "Financial Tech"). Some emails are missing.
*   **Connector Needed:** `SQLConnector` (reads from SQLite/Postgres).

#### B. The "Manual" CSV/Excel Files
*   **Represents:** Mentor applications collected via Google Forms/Email.
*   **Data Structure:** `mentors_raw.csv`
    *   `Name`, `Expertise` (e.g., "AI, Python, Marketing"), `Availability` (e.g., "Weekends"), `LinkedIn_URL`.
*   **The "Mess":** Expertise is a single comma-separated string. Availability is unstructured text.
*   **Connector Needed:** `CSVConnector` (parses and normalizes text).

#### C. The "Historical" JSON Logs
*   **Represents:** Past matching results from last year’s program (stored in Cloud Storage/S3).
*   **Data Structure:** `matches_2025.json`
    *   `[{"startup_id": 1, "mentor_id": 101, "score": 2.5, "feedback": "Bad match"}, {"startup_id": 2, "mentor_id": 102, "score": 9.0, "feedback": "Great help"}]`
*   **The "Mess":** No standard schema. Some entries have `score`, others have `rating`.
*   **Connector Needed:** `JSONConnector` (handles schema variations).

---

### 2. The "Current" Functional Infrastructure (Graph B Inputs)

You need to define the **existing, sub-optimal workflows** that your Agent will try to improve.

#### A. The "Bad" Existing Flow (`flow_legacy_v1.yaml`)
*   **Logic:** Randomly assigns mentors to startups based on industry only.
*   **Skills Used:**
    *   `filter_by_industry` (exact match only).
    *   `random_shuffle` (no intelligence).
*   **Why it fails:** It ignores expertise nuances and historical feedback. It produces low scores (2.0–4.0).

#### B. The "Server" Infrastructure
*   **Represents:** The compute resources available.
*   **Data:** `servers.json`
    *   `server_1`: `load: 90%`, `error_rate: 5%` (Overloaded/Buggy).
    *   `server_2`: `load: 10%`, `error_rate: 0%` (Healthy).
*   **Why it matters:** Your Agent should detect that `server_1` is bad and propose moving flows to `server_2`.

---

### 3. How to Build This Mock Infrastructure (Step-by-Step)

Create a folder called `mock_infrastructure/` in your project.

#### Step 1: Create the SQLite Database (`data/startups.db`)
```python
import sqlite3

conn = sqlite3.connect('data/startups.db')
c = conn.cursor()
c.execute('''CREATE TABLE startups 
             (id INTEGER PRIMARY KEY, name TEXT, industry TEXT, stage TEXT, founder_email TEXT)''')
# Insert messy data
c.executemany('INSERT INTO startups VALUES (?,?,?,?,?)', [
    (1, 'PayFast', 'Fintech', 'Seed', 'ceo@payfast.com'),
    (2, 'HealthAI', 'Healthtech', 'Series A', 'founder@healthai.com'),
    (3, 'ShopEasy', 'E-commerce', 'Pre-seed', None), # Missing email
    (4, 'CryptoKing', 'FinTech', 'Seed', 'info@crypto.com'), # Different casing
])
conn.commit()
conn.close()
```

#### Step 2: Create the Raw CSV (`data/mentors_raw.csv`)
```csv
Name,Expertise,Availability,LinkedIn_URL
Alice Smith,"AI, Python, Django","Weekends",linkedin.com/in/alice
Bob Jones,"Marketing, Sales","Full-time",linkedin.com/in/bob
Charlie Lee,"Python, AI, Cloud","Evenings",linkedin.com/in/charlie
```

#### Step 3: Create the Historical JSON (`data/matches_2025.json`)
```json
[
  {"startup_id": 1, "mentor_name": "Alice Smith", "outcome_score": 2.0, "note": "Too technical"},
  {"startup_id": 2, "mentor_name": "Charlie Lee", "outcome_score": 9.5, "note": "Perfect fit"},
  {"startup_id": 4, "mentor_name": "Bob Jones", "outcome_score": 1.5, "note": "Wrong industry"}
]
```

#### Step 4: Define the Legacy Flow (`flows/flow_legacy_v1.yaml`)
```yaml
flow_id: legacy_matcher_v1
description: "Old random matcher"
steps:
  - skill: filter_by_industry_exact
  - skill: random_shuffle
output: matches
```

---

### 4. How Your Agent Will Interact With This

1.  **Ingestion (Flow 1):**
    *   Agent uses `SQLConnector` to read `startups.db`.
    *   Agent uses `CSVConnector` to read `mentors_raw.csv`.
    *   Agent uses `JSONConnector` to read `matches_2025.json`.
    *   **Result:** All data is normalized and stored in **Neo4j (Graph A)**.

2.  **Analysis (Flow 2):**
    *   Agent queries Graph A: *"Why did 'PayFast' get a low score with Alice?"*
    *   Agent sees: Alice has "AI/Django" skills, but PayFast is "Fintech". Mismatch.
    *   Agent queries Graph B: *"What flow was used?"* -> `legacy_matcher_v1`.
    *   Agent sees: `legacy_matcher_v1` uses `random_shuffle`.

3.  **Optimization (Flow 3):**
    *   Agent proposes `flow_new_v2`:
        *   Replace `random_shuffle` with `semantic_similarity_skill`.
        *   Replace `filter_by_industry_exact` with `fuzzy_industry_match`.
    *   Agent sends `flow_new_v2` to **Sandbox**.

4.  **Simulation (Sandbox):**
    *   Sandbox runs `flow_new_v2` against the historical data.
    *   Result: Score improves from 2.0 to 8.5.

5.  **Proposal:**
    *   Agent updates Graph B: Adds `flow_new_v2` as a "Proposed" node.
    *   Admin approves it.

---

### ✅ Checklist for Your MVP Demo

1.  [ ] **SQLite DB** with 10–20 messy startup records.
2.  [ ] **CSV File** with 10–20 messy mentor records.
3.  [ ] **JSON File** with 20–30 historical match outcomes (mix of good/bad).
4.  [ ] **One "Bad" Flow YAML** (simple logic).
5.  [ ] **One "Good" Skill** (e.g., a simple Python function that calculates keyword overlap).

This mock infrastructure is **small enough to build in 2 hours** but **rich enough to demonstrate every single feature** of your Dual-Graph Agentic System.