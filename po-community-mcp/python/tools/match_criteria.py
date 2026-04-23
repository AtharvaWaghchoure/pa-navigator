"""
Matches patient FHIR data against payer PA criteria using Claude.

Takes structured patient context (from GetPatientPAContext) and criteria chunks
(from SearchPayerCriteria) and returns a structured JSON diff of which criteria
are met vs. unmet, with every evidence item traced to a FHIR resource ID.
"""
import json
import os
from typing import Annotated

import litellm
from mcp.server.fastmcp import Context
from pydantic import Field

from mcp_utilities import create_json_response

_MODEL = os.getenv("MCP_MODEL", "gemini/gemini-2.5-flash")


SYSTEM_PROMPT = """You are a clinical prior authorization specialist. Your job is to match a
patient's clinical data against payer PA criteria and produce a structured, evidence-based assessment.

Rules:
- Only reference data explicitly present in the patient context. Never invent or assume clinical facts.
- For each criterion marked "met", you MUST cite a specific fhir_resource_id from the patient context. If no resource supports it, mark as "not_met".
- Absence-of-evidence is NOT evidence of absence. "No contraindications documented" does not mean criterion is met — you need an explicit FHIR resource (e.g., a screening note or assessment).
- If a criterion cannot be assessed due to missing data, mark it as "not_met" with gap="Insufficient data in record".
- A criterion must appear in either "met" OR "not_met", never both.
- Be conservative: if evidence is ambiguous or any required fhir_resource_id would be null, mark as not_met.
- Output ONLY valid JSON matching the schema below. No prose, no markdown, no explanation outside the JSON.

Output schema:
{
  "met": [
    {
      "criterion": "<criterion label from the policy>",
      "evidence": "<specific clinical finding from patient data that satisfies this criterion>",
      "fhir_resource_id": "<FHIR resource ID, e.g. Condition/abc123 or MedicationRequest/xyz>"
    }
  ],
  "not_met": [
    {
      "criterion": "<criterion label>",
      "gap": "<specific missing evidence or unmet requirement>"
    }
  ],
  "confidence": "high" | "medium" | "low"
}

Confidence levels:
- high: all criteria clearly assessable from the record
- medium: some criteria require clinical judgment or have borderline evidence
- low: significant data gaps prevent confident assessment"""


async def match_criteria(
    paContext: Annotated[  # noqa: N803
        str,
        Field(description="JSON string output from GetPatientPAContext tool"),
    ],
    criteriaChunks: Annotated[  # noqa: N803
        str,
        Field(description="JSON string output from SearchPayerCriteria tool"),
    ],
    ctx: Context = None,
) -> str:
    try:
        pa_context = json.loads(paContext)
        criteria_data = json.loads(criteriaChunks)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON input: {e}") from e

    criteria_text = "\n\n---\n\n".join(
        c["text"] for c in criteria_data.get("criteria_chunks", [])
    )

    user_message = f"""PAYER CRITERIA:
{criteria_text}

PATIENT CLINICAL DATA:
{json.dumps(pa_context, indent=2)}

SERVICE REQUESTED: {pa_context.get('service_requested', 'unknown')}

Assess each criterion against the patient data and return the structured JSON."""

    response = await litellm.acompletion(
        model=_MODEL,
        max_tokens=8192,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    # Extract the outermost JSON object robustly
    start = raw.find("{")
    if start != -1:
        raw = raw[start:]

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError(f"LLM returned non-JSON response: {raw[:300]}")

    # If a criterion appears in both met and not_met, remove it from not_met
    # (met takes precedence — LLM sometimes splits one criterion into partial assessments)
    met_names = {m["criterion"] for m in result.get("met", [])}
    result["not_met"] = [nm for nm in result.get("not_met", []) if nm["criterion"] not in met_names]

    return create_json_response(result)
