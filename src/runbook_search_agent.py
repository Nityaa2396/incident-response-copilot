"""
Runbook Search Agent for Incident Response Copilot.

Indexes the runbook knowledge base into Qdrant.
Given an incident's diagnostics findings, retrieves the most relevant runbooks.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

load_dotenv()

COLLECTION_NAME = "incident_runbooks"
VECTOR_DIM = 128
RUNBOOKS_PATH = Path("src/runbooks.json")


def _get_qdrant() -> QdrantClient:
    return QdrantClient(
        url=os.environ.get("QDRANT_URL"),
        api_key=os.environ.get("QDRANT_API_KEY"),
    )


def _embed(text: str, dim: int = VECTOR_DIM) -> list[float]:
    """
    Trigram n-gram embedding — same approach as PolicyCopilot.
    Deterministic, no API calls needed, good enough for keyword-level retrieval.
    """
    vector = [0.0] * dim
    text = text.lower()
    for i in range(len(text) - 2):
        trigram = text[i:i + 3]
        idx = int(hashlib.md5(trigram.encode()).hexdigest(), 16) % dim
        vector[idx] += 1.0
    magnitude = math.sqrt(sum(v * v for v in vector))
    if magnitude > 0:
        vector = [v / magnitude for v in vector]
    return vector


def _runbook_to_text(runbook: dict) -> str:
    """Convert a runbook dict into a single searchable text blob."""
    parts = [
        runbook.get("title", ""),
        " ".join(runbook.get("tags", [])),
        " ".join(runbook.get("symptoms", [])),
        " ".join(runbook.get("root_causes", [])),
    ]
    return " ".join(parts)


@dataclass
class RunbookMatch:
    id: str
    title: str
    tags: list[str]
    symptoms: list[str]
    root_causes: list[str]
    resolution_steps: list[str]
    prevention: list[str]
    escalation: str
    resolution_time_estimate: str
    score: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "tags": self.tags,
            "symptoms": self.symptoms,
            "root_causes": self.root_causes,
            "resolution_steps": self.resolution_steps,
            "prevention": self.prevention,
            "escalation": self.escalation,
            "resolution_time_estimate": self.resolution_time_estimate,
            "score": self.score,
        }


def load_runbooks() -> list[dict]:
    """Load runbooks from JSON file."""
    return json.loads(RUNBOOKS_PATH.read_text(encoding="utf-8"))


def is_indexed() -> bool:
    """Check if runbooks are already indexed in Qdrant."""
    try:
        client = _get_qdrant()
        collections = [c.name for c in client.get_collections().collections]
        if COLLECTION_NAME not in collections:
            return False
        result = client.scroll(collection_name=COLLECTION_NAME, limit=1)
        return len(result[0]) > 0
    except Exception:
        return False


def index_runbooks(force: bool = False) -> int:
    """
    Index all runbooks into Qdrant.
    Returns number of runbooks indexed.
    Set force=True to reindex even if already indexed.
    """
    if is_indexed() and not force:
        print("  Runbooks already indexed. Skipping.")
        return 0

    client = _get_qdrant()
    runbooks = load_runbooks()

    # create or recreate collection
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME in collections:
        client.delete_collection(COLLECTION_NAME)

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
    )

    points = []
    for i, runbook in enumerate(runbooks):
        text = _runbook_to_text(runbook)
        vector = _embed(text)
        point_id = i + 1

        points.append(PointStruct(
            id=point_id,
            vector=vector,
            payload={
                "id": runbook["id"],
                "title": runbook["title"],
                "tags": runbook.get("tags", []),
                "symptoms": runbook.get("symptoms", []),
                "root_causes": runbook.get("root_causes", []),
                "resolution_steps": runbook.get("resolution_steps", []),
                "prevention": runbook.get("prevention", []),
                "escalation": runbook.get("escalation", ""),
                "resolution_time_estimate": runbook.get("resolution_time_estimate", ""),
            }
        ))

    client.upsert(collection_name=COLLECTION_NAME, points=points)
    print(f"  Indexed {len(points)} runbooks into Qdrant.")
    return len(points)


def search_runbooks(
    query: str,
    top_k: int = 3,
) -> list[RunbookMatch]:
    """
    Search for the most relevant runbooks given an incident query.
    Query should be a combination of error type, symptoms, and affected components.
    Returns top_k RunbookMatch objects sorted by relevance score.
    """
    client = _get_qdrant()

    if not is_indexed():
        print("  Runbooks not indexed. Indexing now...")
        index_runbooks()

    query_vector = _embed(query)

    results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=top_k,
    )

    matches = []
    for hit in results.points:
        matches.append(RunbookMatch(
            id=hit.payload["id"],
            title=hit.payload["title"],
            tags=hit.payload["tags"],
            symptoms=hit.payload["symptoms"],
            root_causes=hit.payload["root_causes"],
            resolution_steps=hit.payload["resolution_steps"],
            prevention=hit.payload["prevention"],
            escalation=hit.payload["escalation"],
            resolution_time_estimate=hit.payload["resolution_time_estimate"],
            score=round(hit.score, 3),
        ))

    return matches


def build_search_query(diagnostics: dict) -> str:
    """
    Build a search query from diagnostics output.
    Combines error type, root cause, and affected components for best retrieval.
    """
    parts = [
        diagnostics.get("error_type", ""),
        diagnostics.get("root_cause", ""),
        " ".join(diagnostics.get("affected_components", [])),
        diagnostics.get("summary", ""),
    ]
    return " ".join(p for p in parts if p)


if __name__ == "__main__":
    print("Indexing runbooks...")
    index_runbooks(force=True)

    # smoke test — search using the same incident from diagnostics agent
    test_query = (
        "DatabaseConnectionError PostgreSQL connection refused "
        "order-service db-prod-01 deployment v2.4.1"
    )

    print(f"\nSearching for: {test_query[:80]}...")
    matches = search_runbooks(test_query, top_k=3)

    print(f"\nTop {len(matches)} runbooks found:\n")
    for i, match in enumerate(matches, 1):
        print(f"  {i}. [{match.id}] {match.title} (score: {match.score})")
        print(f"     Tags: {', '.join(match.tags)}")
        print(f"     First step: {match.resolution_steps[0] if match.resolution_steps else 'N/A'}")
        print()