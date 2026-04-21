from mcp.server.fastmcp import FastMCP
from tools.draft_pa_letter import draft_pa_letter
from tools.get_criteria_gap_advice import get_criteria_gap_advice
from tools.get_patient_pa_context import get_patient_pa_context
from tools.match_criteria import match_criteria
from tools.patient_age_tool import get_patient_age
from tools.patient_allergies_tool import get_patient_allergies
from tools.patient_id_tool import find_patient_id
from tools.search_payer_criteria import search_payer_criteria

mcp = FastMCP("PA Navigator MCP", stateless_http=True, host="0.0.0.0")

_original_get_capabilities = mcp._mcp_server.get_capabilities


def _patched_get_capabilities(notification_options, experimental_capabilities):
    caps = _original_get_capabilities(notification_options, experimental_capabilities)
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
    return caps


mcp._mcp_server.get_capabilities = _patched_get_capabilities

# ── PA Navigator tools ────────────────────────────────────────────────────────
mcp.tool(
    name="GetPatientPAContext",
    description=(
        "Fetches and structures all FHIR resources needed for a prior authorization request: "
        "active conditions (with ICD-10 codes), prior treatment history for step therapy "
        "verification, active medications, and recent lab results."
    ),
)(get_patient_pa_context)

mcp.tool(
    name="SearchPayerCriteria",
    description=(
        "Retrieves the most relevant payer PA criteria chunks for a given drug or procedure "
        "using semantic search over embedded clinical policy documents."
    ),
)(search_payer_criteria)

mcp.tool(
    name="MatchCriteria",
    description=(
        "Matches the patient's clinical data against payer PA criteria. Returns a structured "
        "JSON with criteria met (with FHIR resource citations) and criteria not met (with gaps). "
        "Input must be JSON strings from GetPatientPAContext and SearchPayerCriteria."
    ),
)(match_criteria)

mcp.tool(
    name="DraftPALetter",
    description=(
        "Generates a physician-reviewable prior authorization justification letter. Every "
        "clinical claim is grounded in the criteria match output and cited to a FHIR resource. "
        "Input must be JSON strings from GetPatientPAContext and MatchCriteria."
    ),
)(draft_pa_letter)

mcp.tool(
    name="GetCriteriaGapAdvice",
    description=(
        "Given the unmet criteria from MatchCriteria, advises the physician on what clinical "
        "documentation to gather or what steps to take to strengthen the PA request."
    ),
)(get_criteria_gap_advice)

# ── Template tools (kept from base repo) ─────────────────────────────────────
mcp.tool(name="GetPatientAge",       description="Gets the age of a patient.")(get_patient_age)
mcp.tool(name="GetPatientAllergies", description="Gets the known allergies of a patient.")(get_patient_allergies)
mcp.tool(name="FindPatientId",       description="Finds a patient ID given a first and last name.")(find_patient_id)
