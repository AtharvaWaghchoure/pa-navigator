"""
One-time script: chunks payer criteria text files and embeds them into ChromaDB.

Run once before starting the MCP server:
    cd po-community-mcp/python
    python vector_store/embed_policies.py

Uses ChromaDB's default embedding function (all-MiniLM-L6-v2 via onnxruntime).
No separate sentence-transformers install needed.
"""
import os
import re
import sys

import chromadb

CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./vector_store/chroma_db")
COLLECTION_NAME = "payer_criteria"
POLICIES_DIR = os.getenv("POLICIES_DIR", "./policies")


def chunk_criteria_file(filepath: str) -> list[dict]:
    """Split a criteria text file into one chunk per criterion block."""
    with open(filepath) as f:
        text = f.read()

    filename = os.path.basename(filepath)
    # Detect payer name from filename (e.g. aetna_adalimumab_criteria.txt → Aetna)
    payer = filename.split("_")[0].capitalize()
    service = filename.split("_")[1].capitalize() if "_" in filename else "unknown"

    # Split on "---" section dividers, keeping non-empty blocks
    raw_blocks = [b.strip() for b in re.split(r"\n---+\n", text) if b.strip()]

    chunks = []
    for block in raw_blocks:
        # Extract criterion number if present
        match = re.match(r"^(Criterion\s+\d+[:\s][^\n]+)", block, re.IGNORECASE)
        criterion_label = match.group(1).strip() if match else ""
        chunks.append({
            "text": block,
            "source": filename,
            "payer": payer,
            "service": service,
            "criterion": criterion_label,
        })

    return chunks


def embed_all():
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

    # Recreate the collection to avoid stale data
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(COLLECTION_NAME)

    all_documents, all_metadatas, all_ids = [], [], []

    policy_files = [
        os.path.join(POLICIES_DIR, f)
        for f in os.listdir(POLICIES_DIR)
        if f.endswith(".txt") or f.endswith(".md")
    ]

    if not policy_files:
        print(f"No policy files found in {POLICIES_DIR}", file=sys.stderr)
        sys.exit(1)

    for filepath in policy_files:
        chunks = chunk_criteria_file(filepath)
        for i, chunk in enumerate(chunks):
            doc_id = f"{os.path.basename(filepath)}_chunk_{i}"
            all_documents.append(chunk["text"])
            all_metadatas.append({
                "source": chunk["source"],
                "payer": chunk["payer"],
                "service": chunk["service"],
                "criterion": chunk["criterion"],
            })
            all_ids.append(doc_id)
        print(f"  Chunked {len(chunks)} blocks from {os.path.basename(filepath)}")

    collection.add(documents=all_documents, metadatas=all_metadatas, ids=all_ids)
    print(f"\nEmbedded {len(all_documents)} chunks into '{COLLECTION_NAME}' at {CHROMA_DB_PATH}")


if __name__ == "__main__":
    embed_all()
