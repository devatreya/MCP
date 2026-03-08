"""Structured plan generation (LLM-first, heuristic fallback)."""

import json
import re

from cad_capabilities import CAPABILITY_VERSION, list_supported_operations


_UNIT_TO_CM = {
    "mm": 0.1,
    "cm": 1.0,
    "m": 100.0,
    "in": 2.54,
    "inch": 2.54,
    "inches": 2.54,
}


def _to_cm(value, unit):
    return float(value) * _UNIT_TO_CM.get(unit, 1.0)


def _extract_json_object(raw_text):
    if not raw_text:
        return None

    text = raw_text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return None


def _find_measure_cm(text, keywords, default_cm=None):
    keyword_group = "|".join(re.escape(k) for k in keywords)
    patterns = [
        rf"\b(?:{keyword_group})\b\s*(?:of\s*)?([0-9]+(?:\.[0-9]+)?)\s*(mm|cm|m|in|inch|inches)\b",
        rf"([0-9]+(?:\.[0-9]+)?)\s*(mm|cm|m|in|inch|inches)\s*\b(?:{keyword_group})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _to_cm(match.group(1), match.group(2))

    # If the user gave a plain number near a keyword, default to cm.
    for pattern in [
        rf"\b(?:{keyword_group})\b\s*(?:of\s*)?([0-9]+(?:\.[0-9]+)?)\b",
        rf"([0-9]+(?:\.[0-9]+)?)\s*\b(?:{keyword_group})\b",
    ]:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))

    return default_cm


def _find_int_near_keyword(text, keywords, default_value):
    keyword_group = "|".join(re.escape(k) for k in keywords)
    patterns = [
        rf"\b([0-9]+)\s*(?:x|holes?)?\s*(?:{keyword_group})\b",
        rf"\b(?:{keyword_group})\b\s*(?:count\s*)?(?:of\s*)?([0-9]+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                return default_value
    return default_value


def _extract_bracket_dimensions_cm(text):
    # Accept compact forms like "100x60x60x6 mm".
    compact = re.search(
        r"\b([0-9]+(?:\.[0-9]+)?)\s*[x×]\s*([0-9]+(?:\.[0-9]+)?)\s*[x×]\s*([0-9]+(?:\.[0-9]+)?)\s*[x×]\s*([0-9]+(?:\.[0-9]+)?)\s*(mm|cm|m|in|inch|inches)\b",
        text,
    )
    if compact:
        unit = compact.group(5)
        return {
            "width_cm": _to_cm(compact.group(1), unit),
            "leg_depth_cm": _to_cm(compact.group(2), unit),
            "leg_height_cm": _to_cm(compact.group(3), unit),
            "thickness_cm": _to_cm(compact.group(4), unit),
        }

    return {
        "width_cm": _find_measure_cm(text, ["width", "wide"], default_cm=8.0),
        "leg_depth_cm": _find_measure_cm(text, ["depth", "base", "leg depth", "length"], default_cm=6.0),
        "leg_height_cm": _find_measure_cm(text, ["height", "vertical", "leg height"], default_cm=6.0),
        "thickness_cm": _find_measure_cm(text, ["thickness", "thick", "wall"], default_cm=1.0),
    }


def _heuristic_plan(user_prompt):
    text = (user_prompt or "").lower()
    operations = []

    if any(token in text for token in ["mounting bracket", "mount bracket", "l bracket", "angle bracket"]):
        dims = _extract_bracket_dimensions_cm(text)
        hole_diameter_cm = _find_measure_cm(text, ["hole diameter", "diameter"], default_cm=None)
        if hole_diameter_cm is None:
            metric_hole = re.search(r"\bm\s*([0-9]+(?:\.[0-9]+)?)\b", text)
            if metric_hole:
                hole_diameter_cm = float(metric_hole.group(1)) / 10.0
            else:
                hole_diameter_cm = 0.6

        if "no hole" in text or "without hole" in text:
            base_hole_count = 0
            wall_hole_count = 0
        else:
            base_hole_count = _find_int_near_keyword(text, ["base hole", "bottom hole"], default_value=2)
            wall_hole_count = _find_int_near_keyword(text, ["wall hole", "side hole", "vertical hole"], default_value=2)
            if "one hole" in text or "single hole" in text:
                base_hole_count = 1
                wall_hole_count = 1

        hole_spacing_cm = _find_measure_cm(text, ["hole spacing", "spacing", "pitch"], default_cm=3.0)
        operations.append(
            {
                "id": "op_1",
                "op": "create_mounting_bracket",
                "params": {
                    **dims,
                    "hole_diameter_cm": hole_diameter_cm,
                    "hole_spacing_cm": hole_spacing_cm,
                    "base_hole_count": base_hole_count,
                    "wall_hole_count": wall_hole_count,
                },
            }
        )
        return {
            "version": CAPABILITY_VERSION,
            "operations": operations,
            "metadata": {"planner": "heuristic"},
        }

    if "cube" in text or "box" in text:
        size_cm = _find_measure_cm(text, ["size", "cube", "box", "side"], default_cm=10.0)
        operations.append(
            {
                "id": f"op_{len(operations) + 1}",
                "op": "create_cube",
                "params": {"size_cm": size_cm},
            }
        )

    if "hole" in text or "drill" in text or "bore" in text:
        diameter_cm = _find_measure_cm(text, ["diameter"], default_cm=None)
        if diameter_cm is None:
            radius_cm = _find_measure_cm(text, ["radius"], default_cm=None)
            if radius_cm is not None:
                diameter_cm = radius_cm * 2.0
        if diameter_cm is None:
            m_match = re.search(r"\bm\s*([0-9]+(?:\.[0-9]+)?)\b", text)
            if m_match:
                diameter_cm = float(m_match.group(1)) / 10.0
        if diameter_cm is None:
            diameter_cm = 0.4

        through_all = any(token in text for token in ["through all", "through", "all the way", "thru"])
        params = {
            "diameter_cm": diameter_cm,
            "target": "selected_face_center",
            "extent_mode": "through_all" if through_all else "distance",
        }
        if not through_all:
            params["depth_cm"] = _find_measure_cm(text, ["depth", "deep"], default_cm=2.0)

        operations.append(
            {
                "id": f"op_{len(operations) + 1}",
                "op": "create_hole",
                "params": params,
            }
        )

    if "chamfer" in text or "bevel" in text:
        distance_cm = _find_measure_cm(text, ["chamfer", "bevel", "distance"], default_cm=0.2)
        target = "selected_face_edges" if "face" in text else "selected_edges"
        operations.append(
            {
                "id": f"op_{len(operations) + 1}",
                "op": "create_chamfer",
                "params": {"distance_cm": distance_cm, "target": target, "mode": "equal_distance"},
            }
        )

    if "extrude" in text and not operations:
        height_cm = _find_measure_cm(text, ["height", "distance", "extrude"], default_cm=2.0)
        operation = "new_body"
        if "cut" in text:
            operation = "cut"
        elif "join" in text:
            operation = "join"
        operations.append(
            {
                "id": "op_1",
                "op": "extrude_profile",
                "params": {"height_cm": height_cm, "operation": operation},
            }
        )

    if not operations:
        # Conservative fallback: avoid hallucinating unsupported operations.
        operations.append(
            {
                "id": "op_1",
                "op": "create_cube",
                "params": {"size_cm": 10.0},
            }
        )

    return {
        "version": CAPABILITY_VERSION,
        "operations": operations,
        "metadata": {"planner": "heuristic"},
    }


def _llm_plan(client, model, user_prompt, fusion_state_text, selection_state_text):
    supported = ", ".join(list_supported_operations())
    system_prompt = (
        "You are a CAD operation planner for Fusion 360.\n"
        "Output ONLY strict JSON with this shape:\n"
        "{\n"
        '  "version": "v1",\n'
        '  "operations": [\n'
        '    {"id":"op_1","op":"create_cube","params":{"size_cm":10.0}}\n'
        "  ],\n"
        '  "metadata": {"planner":"llm"}\n'
        "}\n"
        "Rules:\n"
        f"- Supported operations: {supported}\n"
        "- Use centimeters for all lengths.\n"
        "- Do not output Python.\n"
        "- Prefer selected-face targets when prompt references selected geometry.\n"
        "- Keep plan short and deterministic.\n"
    )

    user_content = (
        f"Prompt: {user_prompt}\n\n"
        f"Fusion model state:\n{fusion_state_text}\n\n"
        f"Selection context:\n{selection_state_text}\n"
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.0,
        max_tokens=600,
    )
    raw = (response.choices[0].message.content or "").strip()
    raw_json = _extract_json_object(raw)
    if not raw_json:
        raise ValueError("Planner did not return JSON.")
    parsed = json.loads(raw_json)
    if not isinstance(parsed, dict):
        raise ValueError("Planner JSON must be an object.")
    parsed.setdefault("metadata", {})
    if isinstance(parsed["metadata"], dict):
        parsed["metadata"]["planner"] = "llm"
    return parsed


def generate_plan(user_prompt, fusion_state_text, selection_state_text, client=None, model="gpt-4o"):
    if client is not None:
        try:
            return _llm_plan(client, model, user_prompt, fusion_state_text, selection_state_text)
        except Exception:
            pass
    return _heuristic_plan(user_prompt)
