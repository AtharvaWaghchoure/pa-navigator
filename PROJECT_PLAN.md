# PA Navigator — Prior Authorization Intelligence Agent
## Project Plan: Agents Assemble Hackathon

---

## Problem Validated (Research Summary)

| Metric | Value |
|---|---|
| PA requests per physician/week | 39 (AMA 2024) |
| Hours consumed per physician/week | 13 hrs ≈ ⅓ of work week |
| Physicians reporting care delays | 94% |
| Physicians reporting serious adverse events | 29% |
| Annual US admin cost | ~$35 billion |
| Payer AI denial overturn rate on appeal | 82% |
| CMS-0057-F mandate | FHIR Prior Auth APIs required by Jan 1, 2027 |

---

## What We're Building

**PA Navigator** — an open, explainable, provider-facing Prior Authorization Intelligence Agent.

A physician says: *"My patient with RA needs adalimumab. Payer is Aetna."*

Within 2 minutes, the agent:
1. Reads the patient's FHIR record (diagnoses, meds, labs, prior treatments)
2. Retrieves Aetna's PA criteria for adalimumab via RAG
3. Matches patient data to each criterion, flagging met vs. unmet
4. Drafts a PA justification letter — every factual claim traced to a FHIR resource ID
5. Presents the draft to the physician for review, edit, and sign-off

**Submission path: BOTH Superpower (MCP) + Full Agent (A2A)** — strongest possible entry.

---

## What We Are NOT Building

- Payer-side denial decisioning
- Autonomous PA submission (always physician-reviewed)
- Real EHR integration — demo uses Synthea synthetic patients on HAPI FHIR
- Multi-payer support — one payer (Aetna), one drug (adalimumab) for the demo

---

## Platform Architecture (Exact Patterns from Repos)

### Repository Choices

| Component | Based on | Language |
|---|---|---|
| MCP Server | `po-community-mcp/python/` | Python (FastAPI + FastMCP) |
| A2A Agent | `po-adk-python/healthcare_agent/` | Python (Google ADK + a2a-sdk) |
| FHIR client | `po-community-mcp/python/fhir_client.py` | httpx async |
| Criteria RAG | Custom (Chroma + sentence-transformers) | Python |
| LLM | LiteLLM → `anthropic/claude-sonnet-4-6` | via `ANTHROPIC_API_KEY` |

### FHIR Context Propagation

**In MCP** — arrives as HTTP headers per SHARP-on-MCP spec:
```
x-fhir-server-url:    https://hapi.fhir.org/baseR4
x-fhir-access-token:  (optional bearer token)
x-patient-id:         patient-uuid (fallback; JWT claim preferred)
```

**In A2A** — arrives in `params.message.metadata` with workspace-specific key:
```json
"https://your-workspace.promptopinion.ai/schemas/a2a/v1/fhir-context": {
  "fhirUrl": "https://hapi.fhir.org/baseR4",
  "fhirToken": "...",
  "patientId": "patient-uuid"
}
```
The `before_model_callback` (`fhir_hook.py` pattern) extracts these into session state before every LLM call. FHIR credentials are NEVER passed through the LLM prompt.

### SMART Scopes (declared in capability extensions)
```python
fhir_scopes = [
    {"name": "patient/Patient.rs",          "required": True},
    {"name": "patient/Condition.rs",        "required": True},
    {"name": "patient/MedicationRequest.rs","required": True},
    {"name": "patient/Observation.rs",      "required": True},
]
```

---

## MCP Server — 5 PA Tools

Registered at `POST /mcp` using StreamableHTTP (stateless). Each tool reads FHIR context from request headers via `get_fhir_context(ctx)` and `get_patient_id_if_context_exists(ctx)`.

### Tool 1: `GetPatientPAContext`
Fetches and structures FHIR resources for a patient relevant to a PA request.
```
Input:  patientId (optional if FHIR context present), serviceRequested (string)
Output: JSON { primary_diagnoses, requested_service, prior_treatments,
               relevant_labs, active_conditions, patient_name }
FHIR calls: Patient/{id}, Condition?patient={id}, MedicationRequest?patient={id},
            Observation?patient={id}&category=laboratory
```

### Tool 2: `SearchPayerCriteria`
RAG lookup over embedded payer policy PDF chunks.
```
Input:  payer (string), serviceRequested (string), diagnosisCodes (string[])
Output: JSON { criteria_chunks: [{ text, source, relevance_score }] }
Source: Pre-embedded Aetna adalimumab PA criteria PDF (public provider portal)
```

### Tool 3: `MatchCriteria`
LLM-driven structured matching of patient data against payer criteria.
```
Input:  paContext (object), criteriaChunks (object[])
Output: JSON { met: [{ criterion, evidence, fhirResourceId }],
               notMet: [{ criterion, gap }],
               confidence: "high"|"medium"|"low" }
Constraint: Only references data explicitly present in paContext — no hallucination
```

### Tool 4: `DraftPALetter`
Generates physician-reviewable PA justification letter with FHIR citations.
```
Input:  patientName, providerName, payer, serviceRequested, matchResult
Output: JSON { letterText, citations: [{ sentenceIndex, fhirResourceId }] }
```

### Tool 5: `GetCriteriaGapAdvice`
Given unmet criteria, suggests what clinical documentation to gather.
```
Input:  notMetCriteria (object[])
Output: string — plain-language advice for the physician
```

### MCP Capability Extension Declaration
```python
# mcp_instance.py — monkey-patch pattern from po-community-mcp/python/
caps.model_extra["extensions"] = {
    "ai.promptopinion/fhir-context": {
        "scopes": [
            {"name": "patient/Patient.rs",           "required": True},
            {"name": "patient/Condition.rs",         "required": True},
            {"name": "patient/MedicationRequest.rs", "required": True},
            {"name": "patient/Observation.rs",       "required": True},
        ]
    }
}
```

---

## A2A Agent — PA Navigator

Built on `po-adk-python` pattern. Single agent with FHIR tools. The `before_model_callback` extracts FHIR context from A2A metadata into session state.

### `app.py` (A2A entry point)
```python
a2a_app = create_a2a_app(
    agent=root_agent,
    name="pa_navigator",
    description="Clinical assistant that drafts prior authorization justification letters "
                "by reading the patient's FHIR record and matching against payer criteria.",
    url=os.getenv("PA_NAVIGATOR_URL", "http://localhost:8001"),
    port=8001,
    fhir_extension_uri=f"{os.getenv('PO_PLATFORM_BASE_URL')}/schemas/a2a/v1/fhir-context",
    fhir_scopes=[
        {"name": "patient/Patient.rs",          "required": True},
        {"name": "patient/Condition.rs",        "required": True},
        {"name": "patient/MedicationRequest.rs","required": True},
        {"name": "patient/Observation.rs",      "required": True},
    ],
    skills=[
        AgentSkill(id="draft-pa-letter",
                   name="draft-pa-letter",
                   description="Draft a complete PA justification letter from the patient's FHIR record.",
                   tags=["prior-auth", "fhir", "clinical"]),
        AgentSkill(id="criteria-gap-analysis",
                   name="criteria-gap-analysis",
                   description="Identify which payer criteria are unmet and what documentation is needed.",
                   tags=["prior-auth", "clinical"]),
    ],
)
```

### `agent.py`
```python
root_agent = Agent(
    name="pa_navigator",
    model=LiteLlm(model=os.getenv("PA_NAVIGATOR_MODEL", "anthropic/claude-sonnet-4-6")),
    instruction="""You are PA Navigator, a clinical assistant that helps physicians
    complete prior authorization requests. When given a patient context and a service
    to authorize, you:
    1. Read the patient's FHIR record using get_patient_pa_context
    2. Search for the payer's criteria using search_payer_criteria
    3. Match the patient data to each criterion using match_criteria
    4. Draft a justification letter using draft_pa_letter
    5. Present the letter with criteria met/unmet clearly labeled
    Never submit autonomously — always present for physician review.""",
    before_model_callback=extract_fhir_context,  # from shared/fhir_hook.py pattern
    tools=[
        get_patient_pa_context,    # reads FHIR
        search_payer_criteria,     # RAG lookup
        match_criteria,            # LLM matching
        draft_pa_letter,           # letter generation
        get_criteria_gap_advice,   # gap advice
    ],
)
```

---

## Data Layer

```
1. Generate synthetic patients with Synthea:
   java -jar synthea.jar -p 10 --exporter.fhir.export true

2. Manually enrich 3 patients via FHIR PUT to HAPI:
   Patient A — RA + failed methotrexate 6mo → clear approval
   Patient B — RA + only 2mo methotrexate → step therapy gap
   Patient C — RA + contraindication to MTX → borderline (needs extra doc)

3. Load to HAPI FHIR R4 public server:
   POST https://hapi.fhir.org/baseR4 (unauthenticated, no credentials needed)

4. Curate payer policy:
   Download Aetna Clinical Policy Bulletin for adalimumab (public PDF)
   Chunk → embed → store in Chroma (local)
```

---

## File Structure

```
promptopinion/
├── mcp_server/                   # MCP Superpower
│   ├── main.py                   # FastAPI + FastMCP entry point
│   ├── mcp_instance.py           # FastMCP setup + capability extension patch
│   ├── fhir_context.py
│   ├── fhir_client.py            # httpx async FHIR client
│   ├── fhir_utilities.py         # header extraction, JWT patient claim
│   ├── mcp_constants.py
│   ├── mcp_utilities.py
│   ├── vector_store/
│   │   ├── embed_policies.py     # one-time: chunk+embed payer PDFs → Chroma
│   │   └── chroma_db/            # persisted embeddings
│   ├── policies/
│   │   └── aetna_adalimumab.pdf  # public payer criteria PDF
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── get_patient_pa_context.py
│   │   ├── search_payer_criteria.py
│   │   ├── match_criteria.py
│   │   ├── draft_pa_letter.py
│   │   └── get_criteria_gap_advice.py
│   └── requirements.txt
│
├── a2a_agent/                    # A2A Full Agent
│   ├── agent.py
│   ├── app.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── fhir.py               # FHIR tool functions (read from session state)
│   │   ├── criteria.py           # criteria matching + letter drafting
│   │   └── gap_advice.py
│   ├── shared/                   # from po-adk-python pattern
│   │   ├── app_factory.py        # create_a2a_app() with AgentCardV1 shims
│   │   ├── middleware.py         # API key + FHIR metadata bridging
│   │   ├── fhir_hook.py          # before_model_callback
│   │   └── logging_utils.py
│   └── requirements.txt
│
├── data/                         # Synthea + FHIR data setup scripts
│   ├── generate_patients.sh
│   ├── enrich_patients.py        # FHIR PUT to add specific observations
│   └── load_to_hapi.py
│
├── .env.example
├── docker-compose.yml
└── PROJECT_PLAN.md
```

---

## Tech Stack (Exact Versions)

```
# MCP Server
fastapi>=0.115.0
uvicorn>=0.32.0
mcp>=1.9.0
httpx>=0.28.0
PyJWT>=2.10.0
chromadb>=0.5.0
sentence-transformers>=3.0.0   # for embedding payer policy chunks
anthropic>=0.40.0              # direct SDK for criteria matching + letter drafting

# A2A Agent
google-adk>=1.25.0
a2a-sdk[http-server]>=0.3.26,<1.0.0
litellm==1.83.7                # routes to anthropic/claude-sonnet-4-6
httpx>=0.28.0
python-dotenv>=1.0.0
uvicorn>=0.41.0
```

---

## Ethical Guardrails (All Non-Negotiable)

| Risk | Guardrail |
|---|---|
| Autonomous PA submission | Draft-only; physician reviews before sign-off |
| LLM hallucination in letter | `match_criteria` output is the only allowed source for letter facts |
| FHIR credentials in LLM prompt | Stored in session state / read from headers — never in prompt |
| Bias (ACA Section 1557) | Matching uses only ICD-10, RxNorm, LOINC, CPT — no demographics as inputs |
| PHI exposure | Synthea synthetic data for demo; BAA pathway documented for production |
| FDA SaMD classification | Physician-review gate + explainability → falls under 21st Century Cures CDS exclusion |

---

## Day-by-Day Build Plan

### Day 1: Data + MCP Foundation
**Morning (3h)**
- [ ] Generate Synthea patients + enrich 3 targeted ones
- [ ] Load to HAPI FHIR R4; verify with `GET /Patient/{id}`
- [ ] Download Aetna adalimumab criteria PDF; run `embed_policies.py` → Chroma

**Afternoon (4h)**
- [ ] Build MCP server scaffold (copy `po-community-mcp/python/` pattern)
- [ ] Implement `GetPatientPAContext` tool (Patient + Condition + MedicationRequest + Observation queries)
- [ ] Implement `SearchPayerCriteria` tool (Chroma RAG)
- [ ] Test: `GetPatientPAContext` for Patient A returns correct structured JSON

**Success criteria:** `GetPatientPAContext` + `SearchPayerCriteria` return correct data for all 3 patients.

---

### Day 2: Intelligence + A2A Assembly
**Morning (3h)**
- [ ] Implement `MatchCriteria` tool (Claude claude-sonnet-4-6 with structured output, no hallucination constraint)
- [ ] Implement `DraftPALetter` tool (letter text + citation index)
- [ ] Implement `GetCriteriaGapAdvice` tool
- [ ] Test all 5 tools end-to-end for Patient A (all criteria met)

**Afternoon (4h)**
- [ ] Scaffold A2A agent from `po-adk-python/healthcare_agent/` pattern
- [ ] Implement FHIR tool functions for A2A (reads from `tool_context.state`)
- [ ] Wire `before_model_callback` for FHIR context extraction
- [ ] Run all 3 patient scenarios; verify letters are clinically coherent

**Success criteria:** End-to-end A2A conversation for Patient A produces complete cited PA letter. Patient B shows clear "step therapy gap" flag. Patient C shows gap advice.

---

### Day 3: Polish + Demo
**Morning (2h)**
- [ ] Register MCP server on Prompt Opinion platform
- [ ] Register A2A agent on Prompt Opinion platform
- [ ] Verify agent card served at `/.well-known/agent-card.json`
- [ ] Publish both to Prompt Opinion Marketplace

**Afternoon (3h)**
- [ ] Record 3-minute demo video (script below)
- [ ] Submit

---

## 3-Minute Demo Script

| Time | Content |
|---|---|
| 0:00–0:25 | Problem: "39 PA requests/week, 13 hours. Here's what a physician does manually." (show blank PA form) |
| 0:25–1:20 | Live demo, Patient A: physician asks → FHIR pull → criteria match (all green) → PA letter draft with FHIR citations highlighted |
| 1:20–2:00 | Live demo, Patient B: same flow → 1 criterion unmet (MTX duration too short) → letter flags gap + gives gap advice |
| 2:00–2:35 | Architecture: FHIR R4 → 5 MCP tools + A2A agent → SHARP context propagation → physician review. Highlight CMS-0057-F 2027 alignment. |
| 2:35–3:00 | "PA Navigator gives physicians 13 hours back — without removing them from the loop." |

---

## Known Risks and Mitigations

| Risk | Mitigation |
|---|---|
| `a2a-sdk` v0.3.x not fully A2A v1 | Use `AgentCardV1`/`AgentExtensionV1` shims from `app_factory.py` pattern |
| `adk web` bypasses A2A middleware | Use `uvicorn` directly for all real testing |
| Synthea missing specific PA data elements | Manually add Observations via FHIR PUT (script in `data/`) |
| LLM hallucination of clinical facts | `MatchCriteria` prompt explicitly instructs: only reference data in structured input |
| Chroma embedding quality | Test with multiple query phrasings; add reranking pass if retrieval misses |
| Prompt Opinion registration delays | Allocate Day 1 evening as buffer for platform setup |
| `PO_PLATFORM_BASE_URL` workspace-specific URI | Set env var from actual Prompt Opinion workspace URL before testing A2A context |
