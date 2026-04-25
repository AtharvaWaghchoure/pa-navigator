"""
PA Navigator — A2A application entry point.

Start the server:
    uvicorn pa_navigator.app:a2a_app --host 0.0.0.0 --port 8004

Agent card (public):
    GET http://localhost:8004/.well-known/agent-card.json

All other endpoints require X-API-Key.
"""
import os

from a2a.types import AgentSkill
from shared.app_factory import create_a2a_app

from .agent import root_agent

a2a_app = create_a2a_app(
    agent=root_agent,
    name="pa_navigator",
    description=(
        "Drafts prior authorization justification letters by reading the patient's FHIR record "
        "and matching clinical data against payer criteria. Every claim in the letter is cited "
        "to a specific FHIR resource ID for physician verification before submission."
    ),
    url=os.getenv("PA_NAVIGATOR_URL", os.getenv("BASE_URL", "http://localhost:8004")),
    port=8004,
    fhir_extension_uri=f"{os.getenv('PO_PLATFORM_BASE_URL', 'http://localhost:5139')}/schemas/a2a/v1/fhir-context",
    fhir_scopes=[
        {"name": "patient/Patient.rs",           "required": True},
        {"name": "patient/Condition.rs",         "required": True},
        {"name": "patient/MedicationRequest.rs", "required": True},
        {"name": "patient/Observation.rs",       "required": True},
    ],
    skills=[
        AgentSkill(
            id="draft-pa-letter",
            name="draft-pa-letter",
            description=(
                "Draft a complete, cited prior authorization justification letter from the "
                "patient's FHIR record matched against payer criteria."
            ),
            tags=["prior-auth", "fhir", "clinical", "rheumatology"],
        ),
        AgentSkill(
            id="criteria-gap-analysis",
            name="criteria-gap-analysis",
            description=(
                "Identify which payer PA criteria are unmet and what clinical documentation "
                "the physician needs to gather before resubmission."
            ),
            tags=["prior-auth", "clinical"],
        ),
    ],
)
