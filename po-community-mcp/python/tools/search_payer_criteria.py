"""
RAG lookup — retrieves relevant payer PA criteria chunks for a given service request.

Queries the ChromaDB collection populated by vector_store/embed_policies.py.
Returns the top-k most relevant criterion blocks ranked by semantic similarity.
"""
import os
from typing import Annotated

import chromadb
from mcp.server.fastmcp import Context
from pydantic import Field

from mcp_utilities import create_json_response

CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./vector_store/chroma_db")
COLLECTION_NAME = "payer_criteria"

_chroma_client: chromadb.ClientAPI | None = None


def _get_collection():
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    return _chroma_client.get_or_create_collection(COLLECTION_NAME)


async def search_payer_criteria(
    payer: Annotated[
        str,
        Field(description="Payer/insurance company name (e.g. 'Aetna', 'UnitedHealthcare')"),
    ],
    serviceRequested: Annotated[  # noqa: N803
        str,
        Field(description="Drug or procedure name (e.g. 'adalimumab', 'adalimumab Humira')"),
    ],
    diagnosisCodes: Annotated[  # noqa: N803
        str,
        Field(description="Comma-separated ICD-10 diagnosis codes (e.g. 'M05.79, M06.09')"),
    ],
    ctx: Context = None,
) -> str:
    collection = _get_collection()

    query = (
        f"{payer} prior authorization criteria for {serviceRequested}. "
        f"Diagnosis codes: {diagnosisCodes}. "
        "Step therapy DMARD requirements, TB screening, contraindications, clinical response."
    )

    results = collection.query(
        query_texts=[query],
        n_results=8,
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    for doc, meta, dist in zip(docs, metas, dists):
        chunks.append({
            "text": doc,
            "source": meta.get("source", "unknown"),
            "payer": meta.get("payer", ""),
            "criterion": meta.get("criterion", ""),
            "relevance_score": round(1 - dist, 3),
        })

    return create_json_response({
        "payer": payer,
        "service": serviceRequested,
        "diagnosis_codes": diagnosisCodes,
        "criteria_chunks": chunks,
    })
