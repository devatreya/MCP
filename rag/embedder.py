"""Embedder: converts text chunks into embedding vectors using OpenAI."""

import numpy as np

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
BATCH_SIZE = 100  # OpenAI allows up to 2048 texts per batch


def embed_texts(texts: list[str], client, model: str = EMBEDDING_MODEL, batch_size: int = BATCH_SIZE) -> np.ndarray:
    """Embed a list of text strings using OpenAI's embedding API.

    Returns an (N, dim) numpy array of float32 vectors.
    """
    all_vectors = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = client.embeddings.create(input=batch, model=model)
        batch_vectors = [item.embedding for item in response.data]
        all_vectors.extend(batch_vectors)

    return np.array(all_vectors, dtype=np.float32)


def embed_chunks(chunks: list[dict], client, verbose: bool = True) -> np.ndarray:
    """Embed all chunk texts and return an (N, dim) numpy array.

    Args:
        chunks: list of chunk dicts with 'text' field.
        client: OpenAI client.
        verbose: print progress.

    Returns:
        numpy array of shape (len(chunks), EMBEDDING_DIM).
    """
    texts = [chunk["text"] for chunk in chunks]

    if verbose:
        print(f"  Embedding {len(texts)} chunks with {EMBEDDING_MODEL}…")

    vectors = embed_texts(texts, client)

    if verbose:
        print(f"  Done: {vectors.shape[0]} vectors × {vectors.shape[1]} dimensions")

    return vectors


def embed_query(query: str, client, model: str = EMBEDDING_MODEL) -> np.ndarray:
    """Embed a single query string. Returns a (dim,) numpy vector."""
    response = client.embeddings.create(input=[query], model=model)
    return np.array(response.data[0].embedding, dtype=np.float32)
