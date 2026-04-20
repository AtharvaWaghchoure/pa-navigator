"""
Loads all Synthea FHIR R4 bundles from data/fhir_output/fhir/ into the HAPI FHIR public test server.

Usage:
    python data/load_to_hapi.py

Prints each uploaded patient's ID for use in enrich_patients.py.
"""
import json
import os
import sys
from pathlib import Path

import httpx

HAPI_BASE = os.getenv("FHIR_BASE_URL", "https://hapi.fhir.org/baseR4")
FHIR_DIR = Path("data/fhir_output/fhir")


def upload_bundle(bundle_path: Path) -> list[str]:
    """Upload a transaction bundle and return created Patient IDs."""
    with open(bundle_path) as f:
        bundle = json.load(f)

    if bundle.get("resourceType") != "Bundle":
        return []

    # Convert to transaction bundle if it's a collection
    if bundle.get("type") == "collection":
        bundle["type"] = "transaction"
        for entry in bundle.get("entry", []):
            if "request" not in entry:
                resource_type = entry.get("resource", {}).get("resourceType", "Unknown")
                resource_id = entry.get("resource", {}).get("id", "")
                entry["request"] = {
                    "method": "PUT" if resource_id else "POST",
                    "url": f"{resource_type}/{resource_id}" if resource_id else resource_type,
                }

    response = httpx.post(
        HAPI_BASE,
        json=bundle,
        headers={"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"},
        timeout=60,
    )

    if response.status_code not in (200, 201):
        print(f"  ERROR {response.status_code}: {response.text[:200]}")
        return []

    result = response.json()
    patient_ids = []
    for entry in result.get("entry", []):
        location = (entry.get("response") or {}).get("location", "")
        if location.startswith("Patient/"):
            patient_id = location.split("/")[1].split("/_")[0]
            patient_ids.append(patient_id)

    return patient_ids


def main():
    if not FHIR_DIR.exists():
        print(f"FHIR output directory not found: {FHIR_DIR}")
        print("Run data/generate_patients.sh first.")
        sys.exit(1)

    bundle_files = sorted(FHIR_DIR.glob("*.json"))
    if not bundle_files:
        print(f"No JSON files found in {FHIR_DIR}")
        sys.exit(1)

    print(f"Uploading {len(bundle_files)} bundle(s) to {HAPI_BASE}\n")
    all_patient_ids = []

    for bundle_file in bundle_files[:5]:  # limit to 5 for demo
        print(f"Uploading {bundle_file.name}...")
        patient_ids = upload_bundle(bundle_file)
        if patient_ids:
            print(f"  Patient IDs: {patient_ids}")
            all_patient_ids.extend(patient_ids)
        else:
            print("  No patients created (may already exist or bundle had no Patients)")

    print(f"\nAll uploaded patient IDs:")
    for pid in all_patient_ids:
        print(f"  {pid}")

    # Save IDs to file for enrich_patients.py
    ids_file = Path("data/uploaded_patient_ids.txt")
    ids_file.write_text("\n".join(all_patient_ids))
    print(f"\nSaved patient IDs to {ids_file}")
    print("Next: run  python data/enrich_patients.py  to add RA clinical data")


if __name__ == "__main__":
    main()
