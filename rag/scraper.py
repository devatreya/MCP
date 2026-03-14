"""Scraper for Autodesk Fusion 360 API reference HTML pages."""

import os
import re
import ssl
import time
import urllib.error
import urllib.request
from collections import deque

BASE_URL = "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/"

_DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "rag_cache")

# Seed class names covering the core Fusion 360 modeling API surface.
# The scraper will also discover linked member pages automatically.
SEED_CLASSES = [
    # ── Features ──────────────────────────────────────────
    "ExtrudeFeatures", "ExtrudeFeatureInput",
    "RevolveFeatures", "RevolveFeatureInput",
    "SweepFeatures", "SweepFeatureInput",
    "LoftFeatures", "LoftFeatureInput",
    "FilletFeatures", "FilletFeatureInput", "FilletEdgeSetInputs",
    "ChamferFeatures", "ChamferFeatureInput", "ChamferEdgeSets",
    "HoleFeatures", "HoleFeatureInput",
    "ShellFeatures", "ShellFeatureInput",
    "ThreadFeatures", "ThreadFeatureInput",
    "RectangularPatternFeatures", "RectangularPatternFeatureInput",
    "CircularPatternFeatures", "CircularPatternFeatureInput",
    "MirrorFeatures", "MirrorFeatureInput",
    "MoveFeatures", "MoveFeatureInput",
    "CombineFeatures", "CombineFeatureInput",
    "SplitBodyFeatures", "SplitFaceFeatures",
    "OffsetFeatures", "ScaleFeatures",
    "RibFeatures", "WebFeatures",
    "RuleFilletFeatures",
    "Features",
    # ── Sketch ────────────────────────────────────────────
    "Sketches", "Sketch",
    "SketchCurves", "SketchLines", "SketchArcs", "SketchCircles",
    "SketchEllipses", "SketchFittedSplines",
    "SketchPoints", "SketchPoint",
    "SketchDimensions", "SketchConstraints",
    "Profiles", "Profile",
    # ── BRep ──────────────────────────────────────────────
    "BRepBodies", "BRepBody",
    "BRepFaces", "BRepFace",
    "BRepEdges", "BRepEdge",
    "BRepVertices", "BRepVertex",
    "BRepLoops", "BRepShells",
    # ── Construction ──────────────────────────────────────
    "ConstructionPlanes", "ConstructionPlane", "ConstructionPlaneInput",
    "ConstructionAxes", "ConstructionAxis", "ConstructionAxisInput",
    "ConstructionPoints", "ConstructionPoint", "ConstructionPointInput",
    # ── Geometry primitives ───────────────────────────────
    "Point3D", "Vector3D", "Matrix3D",
    "Line3D", "InfiniteLine3D",
    "Arc3D", "Circle3D", "NurbsCurve3D",
    "Plane", "Sphere", "Cylinder", "Cone", "Torus",
    "BoundingBox3D", "OrientedBoundingBox3D",
    # ── Utility ───────────────────────────────────────────
    "ObjectCollection", "ValueInput",
    "Path", "PathEntity",
    "Application", "UserInterface", "Selection", "Selections",
    "Design", "Component", "Occurrence", "Occurrences",
    # ── Extent definitions ────────────────────────────────
    "DistanceExtentDefinition", "ThroughAllExtentDefinition",
    "ToEntityExtentDefinition", "OffsetStartDefinition",
    "SymmetricExtentDefinition",
    # ── Enums ─────────────────────────────────────────────
    "FeatureOperations", "ExtentDirections",
    "PatternDistanceType", "ChainedCurveOptions",
    "SurfaceTypes", "CurveTypes",
]

_LINK_RE = re.compile(r'href=["\']([A-Za-z0-9_]+\.htm)["\']', re.IGNORECASE)


def _make_ssl_context() -> ssl.SSLContext:
    """Create an SSL context that works with Autodesk's certificate chain.

    Falls back to unverified context if the default context fails on first call.
    """
    if not hasattr(_make_ssl_context, "_ctx"):
        try:
            ctx = ssl.create_default_context()
            # Quick probe to see if default context works
            req = urllib.request.Request(
                BASE_URL + "Application.htm",
                headers={"User-Agent": "FusionCADAssistant/1.0"},
            )
            urllib.request.urlopen(req, timeout=10, context=ctx)
            _make_ssl_context._ctx = ctx
        except (ssl.SSLError, urllib.error.URLError, OSError):
            # Default certs can't verify Autodesk chain — fall back
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            _make_ssl_context._ctx = ctx
    return _make_ssl_context._ctx


def _fetch_url(url: str, timeout: int = 15) -> str | None:
    """Fetch a URL and return HTML text, or None on failure."""
    try:
        ctx = _make_ssl_context()
        req = urllib.request.Request(url, headers={"User-Agent": "FusionCADAssistant/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return None


def _extract_links(html: str) -> list[str]:
    """Find all .htm file links in the same directory."""
    return list(set(_LINK_RE.findall(html)))


def scrape_api_docs(
    cache_dir: str = _DEFAULT_CACHE_DIR,
    max_pages: int = 800,
    delay: float = 0.3,
    verbose: bool = True,
) -> list[dict]:
    """Crawl Fusion 360 API docs starting from seed classes.

    Returns a list of {url, filepath, filename} for each successfully scraped page.
    """
    os.makedirs(cache_dir, exist_ok=True)

    # Build initial queue from seed class names
    queue = deque()
    visited = set()
    manifest = []

    for cls_name in SEED_CLASSES:
        filename = f"{cls_name}.htm"
        if filename not in visited:
            queue.append(filename)
            visited.add(filename)

    pages_fetched = 0

    while queue and pages_fetched < max_pages:
        filename = queue.popleft()
        filepath = os.path.join(cache_dir, filename.replace(".htm", ".html"))

        # Use cached version if available
        if os.path.exists(filepath):
            with open(filepath, "r", errors="replace") as f:
                html = f.read()
            if html.strip():
                manifest.append({
                    "url": BASE_URL + filename,
                    "filepath": filepath,
                    "filename": filename,
                    "cached": True,
                })
                # Still discover links from cached pages
                for linked in _extract_links(html):
                    if linked not in visited:
                        visited.add(linked)
                        queue.append(linked)
                continue

        # Fetch from web
        url = BASE_URL + filename
        html = _fetch_url(url)
        pages_fetched += 1

        if verbose and pages_fetched % 20 == 0:
            print(f"  Scraped {pages_fetched} pages ({len(queue)} in queue)…")

        if not html:
            continue

        # Save to cache
        with open(filepath, "w") as f:
            f.write(html)

        manifest.append({
            "url": url,
            "filepath": filepath,
            "filename": filename,
            "cached": False,
        })

        # Discover linked pages
        for linked in _extract_links(html):
            if linked not in visited:
                visited.add(linked)
                queue.append(linked)

        time.sleep(delay)

    if verbose:
        cached = sum(1 for m in manifest if m.get("cached"))
        fetched = sum(1 for m in manifest if not m.get("cached"))
        print(f"  Done: {len(manifest)} pages total ({fetched} fetched, {cached} cached)")

    return manifest
