"""
Adds RA-specific clinical data to 3 demo patients on HAPI FHIR.

Creates 3 distinct scenarios for the PA Navigator demo:
  Patient A (PATIENT_IDS[0]): Clear approval — active RA + 6-month MTX trial (stopped, inadequate response)
  Patient B (PATIENT_IDS[1]): Step therapy gap — active RA + only 2-month MTX trial (too short)
  Patient C (PATIENT_IDS[2]): Borderline — active RA + MTX contraindicated (hepatotoxicity), TB screen missing

Usage:
    python data/enrich_patients.py
    # or specify patient IDs directly:
    python data/enrich_patients.py pid1 pid2 pid3
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

HAPI_BASE = "https://hapi.fhir.org/baseR4"
HEADERS = {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}


def fhir_put(resource_type: str, resource_id: str, body: dict) -> dict:
    url = f"{HAPI_BASE}/{resource_type}/{resource_id}"
    r = httpx.put(url, json=body, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def fhir_post(resource_type: str, body: dict) -> str:
    """POST a resource and return its new ID."""
    r = httpx.post(f"{HAPI_BASE}/{resource_type}", json=body, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def add_ra_condition(patient_id: str, clinical_status: str = "active") -> str:
    resource = {
        "resourceType": "Condition",
        "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": clinical_status}]},
        "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "confirmed"}]},
        "code": {
            "coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "M05.79", "display": "Seropositive rheumatoid arthritis, multiple sites"}],
            "text": "Seropositive rheumatoid arthritis, multiple sites",
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "onsetDateTime": (date.today() - timedelta(days=730)).isoformat(),
        "recordedDate": (date.today() - timedelta(days=700)).isoformat(),
    }
    return fhir_post("Condition", resource)


def add_medication_request(patient_id: str, drug: str, status: str, dose: str, start_days_ago: int, end_days_ago: int | None, notes: list[str]) -> str:
    resource = {
        "resourceType": "MedicationRequest",
        "status": status,
        "intent": "order",
        "medicationCodeableConcept": {
            "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "display": drug}],
            "text": drug,
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "authoredOn": (date.today() - timedelta(days=start_days_ago)).isoformat(),
        "dosageInstruction": [{"text": dose}],
        "note": [{"text": n} for n in notes],
    }
    if end_days_ago is not None:
        resource["dispenseRequest"] = {
            "validityPeriod": {
                "start": (date.today() - timedelta(days=start_days_ago)).isoformat(),
                "end": (date.today() - timedelta(days=end_days_ago)).isoformat(),
            }
        }
    return fhir_post("MedicationRequest", resource)


def add_lab_observation(patient_id: str, loinc: str, display: str, value: float, unit: str, days_ago: int) -> str:
    resource = {
        "resourceType": "Observation",
        "status": "final",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
        "code": {
            "coding": [{"system": "http://loinc.org", "code": loinc, "display": display}],
            "text": display,
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "effectiveDateTime": (date.today() - timedelta(days=days_ago)).isoformat(),
        "valueQuantity": {"value": value, "unit": unit, "system": "http://unitsofmeasure.org"},
    }
    return fhir_post("Observation", resource)


def enrich_patient_a(patient_id: str):
    """Clear approval: active RA + 6-month MTX trial (stopped, inadequate response) + negative TB screen."""
    print(f"\nPatient A ({patient_id}): Clear approval scenario")
    add_ra_condition(patient_id, "active")
    print("  + Active RA condition (M05.79)")

    add_medication_request(
        patient_id, "Methotrexate 20 mg", "stopped", "Methotrexate 20 mg orally once weekly",
        start_days_ago=270, end_days_ago=90,
        notes=["Inadequate response after 6 months. DAS28-CRP remained 4.8. Discontinued due to persistent disease activity."],
    )
    print("  + Methotrexate stopped after 6 months (inadequate response)")

    add_lab_observation(patient_id, "71773-5", "QuantiFERON-TB Gold", 0.1, "IU/mL", days_ago=30)
    print("  + Negative QuantiFERON-TB Gold (0.1 IU/mL, 30 days ago)")

    add_lab_observation(patient_id, "14647-2", "Rheumatoid Factor", 82.0, "IU/mL", days_ago=45)
    print("  + RF positive (82 IU/mL)")


def enrich_patient_b(patient_id: str):
    """Step therapy gap: active RA + only 2-month MTX trial (criteria require 3 months)."""
    print(f"\nPatient B ({patient_id}): Step therapy gap scenario")
    add_ra_condition(patient_id, "active")
    print("  + Active RA condition (M05.79)")

    add_medication_request(
        patient_id, "Methotrexate 15 mg", "stopped", "Methotrexate 15 mg orally once weekly",
        start_days_ago=120, end_days_ago=60,
        notes=["Stopped after 2 months due to nausea. Duration insufficient for step therapy requirement."],
    )
    print("  + Methotrexate stopped after 2 months (below 3-month requirement)")

    add_lab_observation(patient_id, "71773-5", "QuantiFERON-TB Gold", 0.05, "IU/mL", days_ago=20)
    print("  + Negative QuantiFERON-TB Gold")


def enrich_patient_c(patient_id: str):
    """Borderline: active RA + MTX contraindicated (hepatotoxicity) — no TB screen on file."""
    print(f"\nPatient C ({patient_id}): MTX contraindicated, TB screen missing")
    add_ra_condition(patient_id, "active")
    print("  + Active RA condition (M05.79)")

    add_medication_request(
        patient_id, "Methotrexate 15 mg", "stopped", "Methotrexate 15 mg orally once weekly",
        start_days_ago=200, end_days_ago=185,
        notes=["Discontinued after 2 weeks due to grade 3 hepatotoxicity (ALT 3x ULN). Methotrexate now contraindicated."],
    )
    print("  + Methotrexate stopped (hepatotoxicity contraindication)")

    add_lab_observation(patient_id, "1742-6", "ALT [Enzymatic activity/volume] in Serum or Plasma", 127.0, "U/L", days_ago=185)
    print("  + ALT elevation confirming hepatotoxicity (127 U/L)")
    # No TB screen added — intentional gap for demo


def load_patient_ids() -> list[str]:
    if len(sys.argv) > 1:
        return sys.argv[1:]
    ids_file = Path("data/uploaded_patient_ids.txt")
    if ids_file.exists():
        ids = [line.strip() for line in ids_file.read_text().splitlines() if line.strip()]
        return ids[:3]
    print("No patient IDs provided. Pass IDs as arguments or run load_to_hapi.py first.")
    sys.exit(1)


def main():
    patient_ids = load_patient_ids()
    if len(patient_ids) < 3:
        print(f"Need at least 3 patient IDs, got {len(patient_ids)}")
        sys.exit(1)

    print(f"Enriching patients on {HAPI_BASE}")
    enrich_patient_a(patient_ids[0])
    enrich_patient_b(patient_ids[1])
    enrich_patient_c(patient_ids[2])

    print("\nDone. Demo patient IDs:")
    print(f"  Patient A (clear approval):    {patient_ids[0]}")
    print(f"  Patient B (step therapy gap):  {patient_ids[1]}")
    print(f"  Patient C (MTX contraindicated): {patient_ids[2]}")

    summary_file = Path("data/demo_patients.json")
    summary_file.write_text(json.dumps({
        "fhir_base": HAPI_BASE,
        "patient_a_clear_approval": patient_ids[0],
        "patient_b_step_therapy_gap": patient_ids[1],
        "patient_c_mtx_contraindicated": patient_ids[2],
    }, indent=2))
    print(f"\nSaved demo patient summary to {summary_file}")


if __name__ == "__main__":
    main()
