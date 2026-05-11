"""
PA Navigator — Agent definition.

Drafts prior authorization justification letters by reading the patient's FHIR
record and matching clinical data against payer criteria. Every claim in the
generated letter is traceable to a FHIR resource ID.

FHIR credentials are injected via A2A message metadata (by Prompt Opinion) and
extracted into session state by extract_fhir_context before every LLM call.
They never appear in the prompt.
"""
import os

from google.adk.agents import Agent
from google.adk.models import Gemini

from pa_navigator.tools import (
    draft_pa_letter,
    get_criteria_gap_advice,
    get_patient_pa_context,
    match_criteria,
    search_payer_criteria,
)
from shared.fhir_hook import extract_fhir_context

_model_name = os.getenv("PA_NAVIGATOR_MODEL", "gemini-2.5-flash")
if "/" in _model_name:
    _model_name = _model_name.split("/", 1)[1]
_model = Gemini(model=_model_name)

root_agent = Agent(
    name="pa_navigator",
    model=_model,
    description=(
        "Clinical assistant that drafts prior authorization justification letters. "
        "Reads the patient's FHIR record, matches clinical data against payer criteria, "
        "and produces a physician-reviewable PA letter with every claim cited to a FHIR resource."
    ),
    instruction=(
        "You are PA Navigator, a clinical assistant that helps physicians complete prior "
        "authorization requests. When a physician describes a patient and the service being "
        "requested, follow this workflow:\n\n"
        "1. Call get_patient_pa_context with the service_requested to fetch the patient's FHIR data.\n"
        "2. Call search_payer_criteria with the payer name, service, and diagnosis codes from step 1.\n"
        "3. Call match_criteria with the JSON outputs from steps 1 and 2.\n"
        "4. If any criteria are not met, call get_criteria_gap_advice with the not_met array and "
        "   present the advice to the physician before continuing.\n"
        "5. Call draft_pa_letter with the outputs from steps 1 and 3, and the provider's name.\n"
        "6. Present the drafted letter to the physician for review. Clearly highlight:\n"
        "   - Which criteria are met (with the FHIR evidence)\n"
        "   - Which criteria are not met (with the gaps)\n"
        "   - The full draft letter with citation index\n\n"
        "CRITICAL RULES:\n"
        "- Never submit the letter autonomously — always present for physician review.\n"
        "- Never invent clinical facts — use only what the FHIR tools return.\n"
        "- If FHIR context is missing, tell the physician the session needs to be started "
        "  with patient context from the EHR.\n"
        "- Pass tool outputs as JSON strings between tools (stringify dicts with json.dumps if needed)."
    ),
    tools=[
        get_patient_pa_context,
        search_payer_criteria,
        match_criteria,
        draft_pa_letter,
        get_criteria_gap_advice,
    ],
    before_model_callback=extract_fhir_context,
)
