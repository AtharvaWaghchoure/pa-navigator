"""
PA Navigator tool functions for the A2A agent.

These tools mirror the 5 MCP tools but use the ADK ToolContext pattern:
FHIR credentials are read from tool_context.state (injected by fhir_hook),
never from LLM-visible arguments.

All functions are synchronous (ADK requirement). FHIR calls use httpx sync.
LLM calls for criteria matching and letter drafting use the Anthropic SDK sync client.
"""
import json
import logging
import os

import chromadb
import httpx
import litellm
from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

_FHIR_TIMEOUT = 15
_CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./vector_store/chroma_db")
_COLLECTION_NAME = "payer_criteria"

_MODEL = os.getenv("PA_NAVIGATOR_MODEL", "gemini/gemini-2.5-flash")
_chroma_client: chromadb.ClientAPI | None = None


def _get_collection():
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=_CHROMA_DB_PATH)
    return _chroma_client.get_or_create_collection(_COLLECTION_NAME)


def _get_fhir_creds(tool_context: ToolContext):
    """Read FHIR credentials from session state. Token may be empty (unauthenticated FHIR)."""
    fhir_url = tool_context.state.get("fhir_url", "").rstrip("/")
    fhir_token = tool_context.state.get("fhir_token", "")
    patient_id = tool_context.state.get("patient_id", "")

    if not fhir_url:
        return {"status": "error", "error_message": "fhir_url missing from session state. Include FHIR context in the A2A message metadata."}
    if not patient_id:
        return {"status": "error", "error_message": "patient_id missing from session state. Include FHIR context in the A2A message metadata."}

    return fhir_url, fhir_token, patient_id


def _fhir_get(fhir_url: str, token: str, path: str, params: dict | None = None) -> dict:
    headers = {"Accept": "application/fhir+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = httpx.get(f"{fhir_url}/{path}", params=params, headers=headers, timeout=_FHIR_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _coding_display(codings: list) -> str:
    for c in codings:
        if c.get("display"):
            return c["display"]
    return "Unknown"


# ── Tool 1: GetPatientPAContext ────────────────────────────────────────────────

def get_patient_pa_context(service_requested: str, tool_context: ToolContext) -> dict:
    """
    Fetches and structures all FHIR resources needed for a prior authorization request.

    Retrieves active conditions (with ICD-10 codes), full medication history for
    step therapy verification, and recent laboratory observations.

    Args:
        service_requested: Drug or procedure being requested (e.g. 'adalimumab').
    """
    creds = _get_fhir_creds(tool_context)
    if isinstance(creds, dict):
        return creds
    fhir_url, token, patient_id = creds

    logger.info("tool_get_patient_pa_context patient_id=%s service=%s", patient_id, service_requested)

    try:
        patient = _fhir_get(fhir_url, token, f"Patient/{patient_id}")
    except httpx.HTTPStatusError as e:
        return {"status": "error", "error_message": f"FHIR Patient fetch failed: {e.response.status_code}"}
    except Exception as e:
        return {"status": "error", "error_message": f"Could not reach FHIR server: {e}"}

    names = patient.get("name", [])
    official = next((n for n in names if n.get("use") == "official"), names[0] if names else {})
    patient_name = f"{' '.join(official.get('given', []))} {official.get('family', '')}".strip() or "Unknown"

    try:
        cond_bundle = _fhir_get(fhir_url, token, "Condition", {"patient": patient_id, "_count": "100"})
        med_bundle = _fhir_get(fhir_url, token, "MedicationRequest", {"patient": patient_id, "_count": "100"})
        obs_bundle = _fhir_get(fhir_url, token, "Observation", {
            "patient": patient_id, "category": "laboratory", "_sort": "-date", "_count": "30",
        })
    except httpx.HTTPStatusError as e:
        return {"status": "error", "error_message": f"FHIR search failed: {e.response.status_code}"}
    except Exception as e:
        return {"status": "error", "error_message": f"Could not reach FHIR server: {e}"}

    conditions = []
    for entry in cond_bundle.get("entry", []):
        res = entry.get("resource", {})
        code = res.get("code", {})
        codings = code.get("coding", [])
        clinical_status = ((res.get("clinicalStatus") or {}).get("coding") or [{}])[0].get("code", "unknown")
        icd = [c.get("code", "") for c in codings if "icd" in c.get("system", "").lower()] or [c.get("code", "") for c in codings]
        conditions.append({
            "id": res.get("id"),
            "condition": code.get("text") or _coding_display(codings),
            "icd_codes": icd,
            "clinical_status": clinical_status,
            "onset": res.get("onsetDateTime") or (res.get("onsetPeriod") or {}).get("start"),
        })

    medications = []
    for entry in med_bundle.get("entry", []):
        res = entry.get("resource", {})
        med_concept = res.get("medicationCodeableConcept", {})
        med_name = med_concept.get("text") or _coding_display(med_concept.get("coding", []))
        dosage_list = [d.get("text", "") for d in res.get("dosageInstruction", [])]
        note_list = [n.get("text", "") for n in res.get("note", [])]
        medications.append({
            "id": res.get("id"),
            "medication": med_name,
            "status": res.get("status"),
            "dosage": dosage_list[0] if dosage_list else None,
            "authored_on": res.get("authoredOn"),
            "notes": note_list,
        })

    labs = []
    for entry in obs_bundle.get("entry", []):
        res = entry.get("resource", {})
        code = res.get("code", {})
        codings = code.get("coding", [])
        loinc = next((c.get("code") for c in codings if "loinc" in c.get("system", "").lower()), None)
        value, unit = None, None
        if "valueQuantity" in res:
            vq = res["valueQuantity"]
            value = vq.get("value")
            unit = vq.get("unit") or vq.get("code")
        elif "valueCodeableConcept" in res:
            value = (res["valueCodeableConcept"].get("text") or _coding_display(res["valueCodeableConcept"].get("coding", [])))
        labs.append({
            "id": res.get("id"),
            "observation": code.get("text") or _coding_display(codings),
            "loinc": loinc,
            "value": value,
            "unit": unit,
            "date": res.get("effectiveDateTime"),
        })

    return {
        "status": "success",
        "patient_id": patient_id,
        "patient_name": patient_name,
        "service_requested": service_requested,
        "active_conditions": [c for c in conditions if c["clinical_status"] == "active"],
        "prior_treatments": [m for m in medications if m["status"] in ("stopped", "completed", "cancelled", "on-hold")],
        "active_medications": [m for m in medications if m["status"] == "active"],
        "relevant_labs": labs,
    }


# ── Tool 2: SearchPayerCriteria ────────────────────────────────────────────────

def search_payer_criteria(payer: str, service_requested: str, diagnosis_codes: str, tool_context: ToolContext) -> dict:
    """
    Retrieves the most relevant payer PA criteria for a given drug or procedure.

    Performs semantic search over embedded clinical policy documents and returns
    the top matching criterion blocks with relevance scores.

    Args:
        payer: Insurance company name (e.g. 'Aetna').
        service_requested: Drug or procedure name (e.g. 'adalimumab').
        diagnosis_codes: Comma-separated ICD-10 codes (e.g. 'M05.79, M06.09').
    """
    logger.info("tool_search_payer_criteria payer=%s service=%s", payer, service_requested)

    try:
        collection = _get_collection()
    except Exception as e:
        return {"status": "error", "error_message": f"Could not load criteria database: {e}. Run embed_policies.py first."}

    query = (
        f"{payer} prior authorization criteria for {service_requested}. "
        f"Diagnosis codes: {diagnosis_codes}. "
        "Step therapy DMARD requirements, TB screening, contraindications, clinical response."
    )

    results = collection.query(
        query_texts=[query],
        n_results=8,
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for doc, meta, dist in zip(
        results.get("documents", [[]])[0],
        results.get("metadatas", [[]])[0],
        results.get("distances", [[]])[0],
    ):
        chunks.append({
            "text": doc,
            "source": meta.get("source", ""),
            "criterion": meta.get("criterion", ""),
            "relevance_score": round(1 - dist, 3),
        })

    return {"status": "success", "payer": payer, "service": service_requested, "criteria_chunks": chunks}


# ── Tool 3: MatchCriteria ──────────────────────────────────────────────────────

_MATCH_SYSTEM = """You are a clinical prior authorization specialist. Match the patient's clinical
data against the payer PA criteria and return a structured JSON assessment.

Rules:
- Only reference data explicitly present in the patient context. Never invent facts.
- Cite the specific FHIR resource ID for each met criterion.
- If evidence is ambiguous or data is missing, mark as not_met.
- Output ONLY valid JSON. No prose outside the JSON.

Output schema:
{
  "met": [{"criterion": "...", "evidence": "...", "fhir_resource_id": "..."}],
  "not_met": [{"criterion": "...", "gap": "..."}],
  "confidence": "high" | "medium" | "low"
}"""


def match_criteria(pa_context_json: str, criteria_chunks_json: str, tool_context: ToolContext) -> dict:
    """
    Matches the patient's FHIR data against payer PA criteria using Claude.

    Returns structured JSON with criteria met (evidence + FHIR resource IDs)
    and criteria not met (with specific gaps). Input must be the JSON outputs
    from get_patient_pa_context and search_payer_criteria.

    Args:
        pa_context_json: JSON string from get_patient_pa_context.
        criteria_chunks_json: JSON string from search_payer_criteria.
    """
    logger.info("tool_match_criteria")

    try:
        pa_context = json.loads(pa_context_json) if isinstance(pa_context_json, str) else pa_context_json
        criteria_data = json.loads(criteria_chunks_json) if isinstance(criteria_chunks_json, str) else criteria_chunks_json
    except json.JSONDecodeError as e:
        return {"status": "error", "error_message": f"Invalid JSON input: {e}"}

    criteria_text = "\n\n---\n\n".join(c["text"] for c in criteria_data.get("criteria_chunks", []))

    user_msg = (
        f"PAYER CRITERIA:\n{criteria_text}\n\n"
        f"PATIENT CLINICAL DATA:\n{json.dumps(pa_context, indent=2)}\n\n"
        f"SERVICE REQUESTED: {pa_context.get('service_requested', 'unknown')}\n\n"
        "Assess each criterion and return the structured JSON."
    )

    response = litellm.completion(
        model=_MODEL,
        max_tokens=8192,
        messages=[
            {"role": "system", "content": _MATCH_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    start = raw.find("{")
    if start != -1:
        raw = raw[start:]

    try:
        result = json.loads(raw)
        result["status"] = "success"
        return result
    except json.JSONDecodeError:
        return {"status": "error", "error_message": f"LLM returned non-JSON: {raw[:200]}"}


# ── Tool 4: DraftPALetter ──────────────────────────────────────────────────────

_LETTER_SYSTEM = """You are a clinical prior authorization specialist drafting a medical necessity
justification letter for a treating physician.

Rules:
- Base every clinical statement ONLY on the provided criteria match results. Never invent facts.
- Write in formal medical letter style suitable for payer submission.
- Include: patient info header, clinical summary, step therapy history, medical necessity argument, conclusion.
- After the letter, include a citations array mapping key clinical claims to FHIR resource IDs.
- Output ONLY valid JSON. No prose outside the JSON.

Output schema:
{
  "letter_text": "<full formal letter>",
  "citations": [{"sentence_index": <int>, "claim": "<short quote>", "fhir_resource_id": "<id>"}]
}"""


def draft_pa_letter(pa_context_json: str, match_result_json: str, provider_name: str, tool_context: ToolContext) -> dict:
    """
    Generates a physician-reviewable prior authorization justification letter.

    Every clinical claim is grounded in the criteria match output and cited to a
    FHIR resource ID. This is a DRAFT for physician review — never submitted autonomously.

    Args:
        pa_context_json: JSON string from get_patient_pa_context.
        match_result_json: JSON string from match_criteria.
        provider_name: Treating physician name and specialty (e.g. 'Dr. Jane Smith, MD, Rheumatology').
    """
    logger.info("tool_draft_pa_letter provider=%s", provider_name)

    try:
        pa_context = json.loads(pa_context_json) if isinstance(pa_context_json, str) else pa_context_json
        match_result = json.loads(match_result_json) if isinstance(match_result_json, str) else match_result_json
    except json.JSONDecodeError as e:
        return {"status": "error", "error_message": f"Invalid JSON input: {e}"}

    user_msg = (
        f"Draft a PA justification letter:\n\n"
        f"PATIENT: {pa_context.get('patient_name', 'Unknown')}\n"
        f"PATIENT ID: {pa_context.get('patient_id', '')}\n"
        f"PRESCRIBING PROVIDER: {provider_name}\n"
        f"SERVICE REQUESTED: {pa_context.get('service_requested', 'unknown')}\n"
        f"PAYER: Aetna\n\n"
        f"CRITERIA MET:\n{json.dumps(match_result.get('met', []), indent=2)}\n\n"
        f"CRITERIA NOT MET:\n{json.dumps(match_result.get('not_met', []), indent=2)}\n\n"
        f"FULL PATIENT CONTEXT:\n{json.dumps(pa_context, indent=2)}\n\n"
        "Generate the letter and citations. Use [DATE] for today's date."
    )

    response = litellm.completion(
        model=_MODEL,
        max_tokens=8192,
        messages=[
            {"role": "system", "content": _LETTER_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    start = raw.find("{")
    if start != -1:
        raw = raw[start:]

    try:
        result = json.loads(raw)
        result["status"] = "success"
        return result
    except json.JSONDecodeError:
        return {"status": "error", "error_message": f"LLM returned non-JSON: {raw[:200]}"}


# ── Tool 5: GetCriteriaGapAdvice ───────────────────────────────────────────────

def get_criteria_gap_advice(not_met_criteria_json: str, tool_context: ToolContext) -> dict:
    """
    Given unmet PA criteria, advises the physician on what documentation to gather.

    Returns plain-language, actionable guidance on how to strengthen the PA request
    before resubmission.

    Args:
        not_met_criteria_json: JSON array string from the 'not_met' field of match_criteria output.
    """
    logger.info("tool_get_criteria_gap_advice")

    try:
        not_met = json.loads(not_met_criteria_json) if isinstance(not_met_criteria_json, str) else not_met_criteria_json
    except json.JSONDecodeError as e:
        return {"status": "error", "error_message": f"Invalid JSON input: {e}"}

    if not not_met:
        return {"status": "success", "advice": "All criteria are met. No gaps to address — proceed to draft the PA letter."}

    gaps_text = "\n".join(
        f"- {item.get('criterion', 'Unknown')}: {item.get('gap', 'No detail')}"
        for item in not_met
    )

    response = litellm.completion(
        model=_MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a prior authorization specialist advising a treating physician. "
                    "For each unmet PA criterion, give specific, actionable advice on what documentation "
                    "to gather or what steps to take. Be concise and practical. Use bullet points."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"The following PA criteria are not met:\n\n{gaps_text}\n\n"
                    "What should the physician do to address each gap before resubmitting?"
                ),
            },
        ],
    )

    return {"status": "success", "advice": response.choices[0].message.content.strip()}
