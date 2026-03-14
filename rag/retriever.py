"""Retriever: query-time RAG lookup against the pre-built Fusion 360 API doc index."""

import numpy as np

from .store import load_index, index_exists
from .embedder import embed_query

# Module-level cache so we only load the index once per process
_cached_chunks = None
_cached_vectors = None
_cache_loaded = False


def _ensure_loaded():
    """Load the index into module-level cache on first call."""
    global _cached_chunks, _cached_vectors, _cache_loaded
    if _cache_loaded:
        return
    _cache_loaded = True
    if index_exists():
        _cached_chunks, _cached_vectors = load_index()


def _cosine_similarity(query_vec: np.ndarray, all_vecs: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between a query vector and all index vectors.

    Args:
        query_vec: shape (dim,)
        all_vecs: shape (N, dim)

    Returns:
        shape (N,) array of similarity scores in [-1, 1].
    """
    query_norm = np.linalg.norm(query_vec)
    if query_norm < 1e-10:
        return np.zeros(all_vecs.shape[0])
    doc_norms = np.linalg.norm(all_vecs, axis=1)
    doc_norms = np.maximum(doc_norms, 1e-10)
    return (all_vecs @ query_vec) / (doc_norms * query_norm)


def retrieve(query: str, client, k: int = 6) -> list[str]:
    """Retrieve the top-k most relevant API doc chunks for a natural-language query.

    Args:
        query: the user's intent description or search text.
        client: OpenAI client (used to embed the query).
        k: number of chunks to return.

    Returns:
        List of formatted text strings (one per chunk), ready to inject into prompts.
        Returns [] if the index is not built or the query fails.
    """
    _ensure_loaded()

    if _cached_chunks is None or _cached_vectors is None:
        return []

    if not _cached_chunks:
        return []

    try:
        query_vec = embed_query(query, client)
    except Exception:
        return []

    similarities = _cosine_similarity(query_vec, _cached_vectors)

    # Get top-k indices (descending similarity)
    top_k = min(k, len(_cached_chunks))
    top_indices = np.argsort(similarities)[::-1][:top_k]

    results = []
    for idx in top_indices:
        score = float(similarities[idx])
        if score < 0.15:  # skip very low similarity chunks
            continue
        chunk = _cached_chunks[idx]
        text = chunk["text"]
        source = chunk.get("source_url", "")
        formatted = f"[API Doc] {text}"
        if source:
            formatted += f"\n  Source: {source}"
        results.append(formatted)

    return results


def clear_cache():
    """Force reload of the index on next retrieve() call."""
    global _cached_chunks, _cached_vectors, _cache_loaded
    _cached_chunks = None
    _cached_vectors = None
    _cache_loaded = False
