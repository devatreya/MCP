"""Chunker: parses Autodesk API HTML pages into structured text chunks for embedding."""

import os
import re

from bs4 import BeautifulSoup


def _clean_text(text: str) -> str:
    """Collapse whitespace and strip."""
    return re.sub(r"\s+", " ", text).strip()


def _extract_class_name(filename: str) -> str:
    """Derive class name from filename: 'ExtrudeFeatureInput_setOneSideExtent.htm' → 'ExtrudeFeatureInput'."""
    base = filename.replace(".htm", "").replace(".html", "")
    parts = base.split("_", 1)
    return parts[0]


def _extract_member_name(filename: str) -> str | None:
    """Derive member name from filename: 'ExtrudeFeatureInput_setOneSideExtent.htm' → 'setOneSideExtent'."""
    base = filename.replace(".htm", "").replace(".html", "")
    parts = base.split("_", 1)
    return parts[1] if len(parts) > 1 else None


def _parse_page(html: str, filename: str, source_url: str) -> list[dict]:
    """Parse a single API doc HTML page into chunks."""
    soup = BeautifulSoup(html, "html.parser")
    chunks = []

    class_name = _extract_class_name(filename)
    member_name = _extract_member_name(filename)

    # Extract page title
    title_tag = soup.find("title")
    title = _clean_text(title_tag.get_text()) if title_tag else class_name

    # Extract all meaningful text from the body
    body = soup.find("body")
    if not body:
        return chunks

    # Strategy 1: Look for structured content (tables, code blocks, descriptions)
    sections = []

    # Get the main description paragraph(s)
    description_parts = []
    for p in body.find_all("p"):
        text = _clean_text(p.get_text())
        if text and len(text) > 10:
            description_parts.append(text)
    description = " ".join(description_parts[:3])  # first 3 paragraphs

    # Extract code samples
    code_blocks = []
    for code_tag in body.find_all(["code", "pre"]):
        code_text = code_tag.get_text().strip()
        if code_text and len(code_text) > 5:
            code_blocks.append(code_text)

    # Extract tables (parameter tables, property tables)
    table_rows = []
    for table in body.find_all("table"):
        for row in table.find_all("tr"):
            cells = [_clean_text(td.get_text()) for td in row.find_all(["td", "th"])]
            if any(c for c in cells):
                table_rows.append(" | ".join(c for c in cells if c))

    # Build the chunk text
    if member_name:
        # This is a member page (method/property)
        chunk_text = f"Class: {class_name}\nMember: {member_name}\n"
        if description:
            chunk_text += f"Description: {description}\n"
        if code_blocks:
            # Usually the first code block is the signature
            chunk_text += f"Signature: {code_blocks[0]}\n"
        if table_rows:
            chunk_text += "Details:\n" + "\n".join(f"  {row}" for row in table_rows[:10])
    else:
        # This is a class overview page
        chunk_text = f"Class: {class_name}\n"
        if description:
            chunk_text += f"Description: {description}\n"

        # Extract member listings (methods, properties)
        member_names = []
        for link in body.find_all("a"):
            href = link.get("href", "")
            if href.startswith(f"{class_name}_"):
                name = href.replace(".htm", "").split("_", 1)[-1]
                member_names.append(name)

        if member_names:
            chunk_text += f"Members: {', '.join(member_names[:30])}"
        elif table_rows:
            chunk_text += "Details:\n" + "\n".join(f"  {row}" for row in table_rows[:15])

    # Only keep chunks with meaningful content
    if len(chunk_text.strip()) > 30:
        chunks.append({
            "text": chunk_text.strip(),
            "source_url": source_url,
            "class_name": class_name,
            "member_name": member_name,
            "member_type": "method" if member_name and member_name[0].islower() else "property" if member_name else "class",
        })

    return chunks


def chunk_manifest(manifest: list[dict], verbose: bool = True) -> list[dict]:
    """Process all scraped HTML pages and return a list of text chunks.

    Args:
        manifest: list of {url, filepath, filename} from scraper.
        verbose: print progress.

    Returns:
        list of chunk dicts: {text, source_url, class_name, member_name, member_type}
    """
    all_chunks = []
    skipped = 0

    for entry in manifest:
        filepath = entry["filepath"]
        if not os.path.exists(filepath):
            skipped += 1
            continue

        with open(filepath, "r", errors="replace") as f:
            html = f.read()

        if not html.strip():
            skipped += 1
            continue

        page_chunks = _parse_page(
            html=html,
            filename=entry["filename"],
            source_url=entry["url"],
        )
        all_chunks.extend(page_chunks)

    if verbose:
        print(f"  Chunked {len(manifest)} pages → {len(all_chunks)} chunks (skipped {skipped})")

    return all_chunks
