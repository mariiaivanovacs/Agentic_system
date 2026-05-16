"""
embedder.py — Google Generative AI text embeddings for GraphRAG.

generate_embedding(text) → embedding float list
embed_graph_nodes(labels) → writes embeddings back to Neo4j, returns count
"""
from __future__ import annotations

import logging
import os
from typing import List

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = os.environ.get("GRAPHRAG_EMBEDDING_MODEL", "models/gemini-embedding-001")
_EMBEDDING_DIM = int(os.environ.get("GRAPHRAG_EMBEDDING_DIM", "3072"))


def generate_embedding(text: str) -> List[float]:
    """Return an embedding for *text* via the configured Google embedding model.

    Requires the GOOGLE_API_KEY environment variable to be set.

    Raises:
        RuntimeError: if GOOGLE_API_KEY is missing or the API call fails.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY environment variable is not set. "
            "Embedding generation requires a valid Google API key."
        )
    try:
        import google.generativeai as genai  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "google-generativeai is not installed. "
            "Run: pip install google-generativeai"
        ) from exc

    genai.configure(api_key=api_key)
    result = genai.embed_content(
        model=_EMBEDDING_MODEL,
        content=text,
        task_type="retrieval_document",
    )
    embedding: List[float] = result["embedding"]
    if len(embedding) != _EMBEDDING_DIM:
        raise RuntimeError(
            f"Expected {_EMBEDDING_DIM}-dim embedding, got {len(embedding)}."
        )
    return embedding


def embed_graph_nodes(labels: List[str] | None = None) -> int:
    """Generate and write embeddings for nodes that are missing them.

    Queries Neo4j for nodes of the given labels that have a `description`
    field but no `embedding` yet. Generates embeddings and writes them back.

    Args:
        labels: Node labels to embed. Defaults to ["Skill", "Company", "Mentor"].

    Returns:
        Number of nodes successfully embedded.
    """
    if labels is None:
        labels = ["Skill", "Company", "Mentor"]

    import os as _os  # noqa: PLC0415

    from neo4j import GraphDatabase  # noqa: PLC0415

    driver = GraphDatabase.driver(
        _os.environ["NEO4J_URI"],
        auth=(
            _os.environ.get("NEO4J_USERNAME", "neo4j"),
            _os.environ["NEO4J_PASSWORD"],
        ),
    )
    db = _os.environ.get("NEO4J_DATABASE", "neo4j")

    embedded_count = 0
    try:
        with driver.session(database=db) as session:
            for label in labels:
                rows = session.run(
                    f"""
                    MATCH (n:{label})
                    WHERE n.description IS NOT NULL
                      AND n.description <> ''
                      AND n.embedding IS NULL
                    RETURN n.id AS id, n.description AS description
                    """
                ).data()

                for row in rows:
                    node_id = row["id"]
                    text = row["description"]
                    try:
                        embedding = generate_embedding(text)
                        session.run(
                            f"MATCH (n:{label} {{id: $id}}) SET n.embedding = $embedding",
                            id=node_id,
                            embedding=embedding,
                        )
                        embedded_count += 1
                        logger.debug("Embedded %s node %s.", label, node_id)
                    except Exception as exc:
                        logger.warning(
                            "Skipping embedding for %s %s: %s", label, node_id, exc
                        )
    finally:
        driver.close()

    logger.info("Embedded %d nodes across labels %s.", embedded_count, labels)
    return embedded_count
