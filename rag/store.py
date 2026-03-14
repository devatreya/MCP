"""Persistence layer for the RAG index — saves/loads chunk metadata + embedding vectors."""

import json
import os

import numpy as np

_DEFAULT_INDEX_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "rag_index")


def save_index(chunks: list, vectors: np.ndarray, index_dir: str = _DEFAULT_INDEX_DIR):
    """Save chunk metadata (JSON) and embedding vectors (numpy) to disk."""
    os.makedirs(index_dir, exist_ok=True)

    chunks_path = os.path.join(index_dir, "chunks.json")
    vectors_path = os.path.join(index_dir, "vectors.npy")

    with open(chunks_path, "w") as f:
        json.dump(chunks, f, indent=2)

    np.save(vectors_path, vectors)

    print(f"  Saved {len(chunks)} chunks → {chunks_path}")
    print(f"  Saved {vectors.shape} vectors → {vectors_path}")


def load_index(index_dir: str = _DEFAULT_INDEX_DIR):
    """Load chunk metadata and embedding vectors from disk.

    Returns (chunks, vectors) or (None, None) if index doesn't exist.
    """
    chunks_path = os.path.join(index_dir, "chunks.json")
    vectors_path = os.path.join(index_dir, "vectors.npy")

    if not os.path.exists(chunks_path) or not os.path.exists(vectors_path):
        return None, None

    with open(chunks_path, "r") as f:
        chunks = json.load(f)

    vectors = np.load(vectors_path)
    return chunks, vectors


def index_exists(index_dir: str = _DEFAULT_INDEX_DIR) -> bool:
    """Check if a built index exists on disk."""
    chunks_path = os.path.join(index_dir, "chunks.json")
    vectors_path = os.path.join(index_dir, "vectors.npy")
    return os.path.exists(chunks_path) and os.path.exists(vectors_path)
