"""
Fetches and structures all FHIR resources needed to build a prior authorization request.

Returns a structured JSON with active conditions, prior treatment history (for step
therapy verification), active medications, and recent lab results.
"""
import json
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from fhir_client import FhirClient
from fhir_utilities import get_fhir_context, get_patient_id_if_context_exists
from mcp_utilities import create_json_response


def _coding_display(codings: list) -> str:
    for c in codings:
        if c.get("display"):
            return c["display"]
    return "Unknown"


def _icd_codes(codings: list) -> list[str]:
    return [
        c.get("code", "")
        for c in codings
        if "icd" in c.get("system", "").lower() or "snomed" in c.get("system", "").lower()
    ] or [c.get("code", "") for c in codings]


async def get_patient_pa_context(
    serviceRequested: Annotated[  # noqa: N803
        str,
        Field(description="Drug or procedure being requested for PA (e.g. 'adalimumab', 'MRI lumbar spine')"),
    ],
    patientId: Annotated[  # noqa: N803
        str | None,
        Field(description="FHIR patient ID. Optional when patient context already exists."),
    ] = None,
    ctx: Context = None,
) -> str:
    if not patientId:
        patientId = get_patient_id_if_context_exists(ctx)
        if not patientId:
            raise ValueError("No patient context found")

    fhir_context = get_fhir_context(ctx)
    if not fhir_context:
        raise ValueError("FHIR context could not be retrieved")

    client = FhirClient(base_url=fhir_context.url, token=fhir_context.token)

    patient = await client.read(f"Patient/{patientId}")
    if not patient:
        raise ValueError(f"Patient {patientId} not found on FHIR server")

    # Patient name
    names = patient.get("name", [])
    official = next((n for n in names if n.get("use") == "official"), names[0] if names else {})
    patient_name = f"{' '.join(official.get('given', []))} {official.get('family', '')}".strip() or "Unknown"

    # All conditions — active and historical (need history for clinical picture)
    cond_bundle = await client.search("Condition", {"patient": patientId, "_count": "100"})
    conditions = []
    for entry in (cond_bundle or {}).get("entry", []):
        res = entry.get("resource", {})
        code = res.get("code", {})
        codings = code.get("coding", [])
        clinical_status = ((res.get("clinicalStatus") or {}).get("coding") or [{}])[0].get("code", "unknown")
        raw_id = res.get("id")
        conditions.append({
            "resource_id": f"Condition/{raw_id}" if raw_id else None,
            "condition": code.get("text") or _coding_display(codings),
            "icd_codes": _icd_codes(codings),
            "clinical_status": clinical_status,
            "onset": res.get("onsetDateTime") or (res.get("onsetPeriod") or {}).get("start"),
            "recorded_date": res.get("recordedDate"),
        })

    # All MedicationRequests — active + stopped/completed for step therapy verification
    med_bundle = await client.search("MedicationRequest", {"patient": patientId, "_count": "100"})
    medications = []
    for entry in (med_bundle or {}).get("entry", []):
        res = entry.get("resource", {})
        med_concept = res.get("medicationCodeableConcept", {})
        med_codings = med_concept.get("coding", [])
        med_name = med_concept.get("text") or _coding_display(med_codings)
        dosage_list = [d.get("text", "") for d in res.get("dosageInstruction", [])]
        note_list = [n.get("text", "") for n in res.get("note", [])]
        raw_id = res.get("id")
        medications.append({
            "resource_id": f"MedicationRequest/{raw_id}" if raw_id else None,
            "medication": med_name,
            "status": res.get("status"),
            "dosage": dosage_list[0] if dosage_list else None,
            "authored_on": res.get("authoredOn"),
            "notes": note_list,
        })

    # Recent lab observations (most recent 30)
    obs_bundle = await client.search("Observation", {
        "patient": patientId,
        "category": "laboratory",
        "_sort": "-date",
        "_count": "30",
    })
    labs = []
    for entry in (obs_bundle or {}).get("entry", []):
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
            value = (res["valueCodeableConcept"].get("text")
                     or _coding_display(res["valueCodeableConcept"].get("coding", [])))
        raw_id = res.get("id")
        labs.append({
            "resource_id": f"Observation/{raw_id}" if raw_id else None,
            "observation": code.get("text") or _coding_display(codings),
            "loinc": loinc,
            "value": value,
            "unit": unit,
            "date": res.get("effectiveDateTime"),
        })

    active_conditions = [c for c in conditions if c["clinical_status"] == "active"]
    # Prior treatments = stopped/completed/cancelled — evidence for step therapy
    prior_treatments = [m for m in medications if m["status"] in ("stopped", "completed", "cancelled", "on-hold")]
    active_medications = [m for m in medications if m["status"] == "active"]

    return create_json_response({
        "patient_id": patientId,
        "patient_name": patient_name,
        "service_requested": serviceRequested,
        "active_conditions": active_conditions,
        "prior_treatments": prior_treatments,
        "active_medications": active_medications,
        "relevant_labs": labs,
    })
