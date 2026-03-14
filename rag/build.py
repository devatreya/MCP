#!/usr/bin/env python3
"""CLI tool: build the RAG index from Fusion 360 API documentation.

Usage:
    python -m rag.build              # full pipeline: scrape → chunk → embed → save
    python -m rag.build --skip-scrape # re-chunk and re-embed from cached HTML
    python -m rag.build --stats      # show index statistics only
"""

import argparse
import os
import sys
import time

# Add project root to path so imports work when run as module
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _create_openai_client():
    """Create an OpenAI client using the project's environment setup."""
    # Try to load .env file
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip("'\"")
                    if key and value:
                        os.environ.setdefault(key, value)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not found in environment or .env file.")
        print("Set it with: export OPENAI_API_KEY=sk-...")
        sys.exit(1)

    from openai import OpenAI
    return OpenAI(api_key=api_key)


def cmd_build(skip_scrape: bool = False):
    """Run the full build pipeline: scrape → chunk → embed → save."""
    from rag.scraper import scrape_api_docs
    from rag.chunker import chunk_manifest
    from rag.embedder import embed_chunks
    from rag.store import save_index

    total_start = time.time()

    # ── Step 1: Scrape ────────────────────────────────────────────────
    if skip_scrape:
        print("\n[1/4] Scrape: SKIPPED (--skip-scrape)")
        # Build manifest from cached files
        cache_dir = os.path.join(PROJECT_ROOT, "rag_cache")
        if not os.path.exists(cache_dir):
            print(f"  ERROR: Cache directory not found: {cache_dir}")
            print("  Run without --skip-scrape first to download the docs.")
            sys.exit(1)
        manifest = []
        base_url = "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/"
        for f in sorted(os.listdir(cache_dir)):
            if f.endswith(".html"):
                htm_name = f.replace(".html", ".htm")
                manifest.append({
                    "url": base_url + htm_name,
                    "filepath": os.path.join(cache_dir, f),
                    "filename": htm_name,
                    "cached": True,
                })
        print(f"  Found {len(manifest)} cached pages")
    else:
        print("\n[1/4] Scraping Fusion 360 API docs…")
        manifest = scrape_api_docs(verbose=True)

    if not manifest:
        print("  ERROR: No pages scraped. Check network connection.")
        sys.exit(1)

    # ── Step 2: Chunk ─────────────────────────────────────────────────
    print("\n[2/4] Chunking HTML pages…")
    chunks = chunk_manifest(manifest, verbose=True)

    if not chunks:
        print("  ERROR: No chunks produced. HTML pages may be empty or unparseable.")
        sys.exit(1)

    # ── Step 3: Embed ─────────────────────────────────────────────────
    print("\n[3/4] Embedding chunks…")
    client = _create_openai_client()
    vectors = embed_chunks(chunks, client, verbose=True)

    # ── Step 4: Save ──────────────────────────────────────────────────
    print("\n[4/4] Saving index…")
    save_index(chunks, vectors)

    elapsed = time.time() - total_start
    print(f"\n✓ RAG index built in {elapsed:.1f}s")
    print(f"  {len(chunks)} chunks, {vectors.shape[1]}-dimensional vectors")
    print(f"  Ready for retrieval in code_generator.py")


def cmd_stats():
    """Show statistics about the existing index."""
    from rag.store import load_index, index_exists

    if not index_exists():
        print("No RAG index found. Run: python -m rag.build")
        return

    chunks, vectors = load_index()
    if chunks is None:
        print("Failed to load index.")
        return

    print(f"\nRAG Index Statistics:")
    print(f"  Chunks: {len(chunks)}")
    print(f"  Vectors: {vectors.shape}")
    print(f"  Embedding dim: {vectors.shape[1]}")

    # Class distribution
    classes = {}
    for chunk in chunks:
        cls = chunk.get("class_name", "unknown")
        classes[cls] = classes.get(cls, 0) + 1

    print(f"  Unique classes: {len(classes)}")
    print(f"\n  Top 15 classes by chunk count:")
    for cls, count in sorted(classes.items(), key=lambda x: -x[1])[:15]:
        print(f"    {cls}: {count}")

    # Member type distribution
    types = {}
    for chunk in chunks:
        t = chunk.get("member_type", "unknown")
        types[t] = types.get(t, 0) + 1
    print(f"\n  By type: {types}")


def main():
    parser = argparse.ArgumentParser(description="Build the Fusion 360 API RAG index")
    parser.add_argument("--skip-scrape", action="store_true",
                        help="Skip scraping, use cached HTML only")
    parser.add_argument("--stats", action="store_true",
                        help="Show index statistics only")
    args = parser.parse_args()

    if args.stats:
        cmd_stats()
    else:
        cmd_build(skip_scrape=args.skip_scrape)


if __name__ == "__main__":
    main()
