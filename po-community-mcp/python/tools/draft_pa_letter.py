"""
Generates a physician-reviewable prior authorization justification letter.

Uses only the structured criteria match output as the factual basis — every
clinical claim in the letter is traceable to a FHIR resource ID. The letter
is a draft for physician review, never submitted autonomously.
"""
import json
import os
from typing import Annotated

import litellm
from mcp.server.fastmcp import Context
from pydantic import Field

from mcp_utilities import create_json_response

_MODEL = os.getenv("MCP_MODEL", "gemini/gemini-2.5-flash")


SYSTEM_PROMPT = """You are a clinical prior authorization specialist drafting a medical necessity
justification letter on behalf of a treating physician.

Rules:
- Base every clinical statement ONLY on the provided criteria match results. Do not invent facts.
- Write in formal medical letter style, suitable for submission to an insurance payer.
- Structure the letter clearly: patient info, clinical summary, step therapy history, medical necessity argument, conclusion.
- After generating the letter, produce a citations array mapping each key clinical sentence (by 0-based index) to the FHIR resource ID that supports it.
- Output ONLY valid JSON. No prose outside the JSON.

Output schema:
{
  "letter_text": "<full formal letter as a multi-line string>",
  "citations": [
    {
      "sentence_index": <0-based index of the sentence in the letter>,
      "claim": "<short quote of the clinical claim>",
      "fhir_resource_id": "<e.g. Condition/abc123 or MedicationRequest/xyz>"
    }
  ]
}"""


async def draft_pa_letter(
    paContext: Annotated[  # noqa: N803
        str,
        Field(description="JSON string output from GetPatientPAContext tool"),
    ],
    matchResult: Annotated[  # noqa: N803
        str,
        Field(description="JSON string output from MatchCriteria tool"),
    ],
    providerName: Annotated[  # noqa: N803
        str,
        Field(description="Name of the treating/prescribing physician (e.g. 'Dr. Jane Smith, MD, Rheumatology')"),
    ],
    ctx: Context = None,
) -> str:
    try:
        pa_context = json.loads(paContext)
        match_result = json.loads(matchResult)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON input: {e}") from e

    user_message = f"""Draft a prior authorization justification letter with:

PATIENT: {pa_context.get('patient_name', 'Unknown')}
PATIENT ID: {pa_context.get('patient_id', '')}
PRESCRIBING PROVIDER: {providerName}
SERVICE REQUESTED: {pa_context.get('service_requested', 'unknown')}
PAYER: Aetna

CRITERIA MET:
{json.dumps(match_result.get('met', []), indent=2)}

CRITERIA NOT MET:
{json.dumps(match_result.get('not_met', []), indent=2)}

FULL PATIENT CONTEXT (for clinical details):
{json.dumps(pa_context, indent=2)}

Generate the letter and citations array. Today's date: use [DATE] as placeholder."""

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
    except json.JSONDecodeError:
        raise ValueError(f"LLM returned non-JSON response: {raw[:300]}")

    return create_json_response(result)
