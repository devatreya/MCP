"""Typed-ish intermediate representation for structured CAD plans."""

from dataclasses import dataclass, field
from typing import Any, Dict, List

from cad_capabilities import CAPABILITY_VERSION


@dataclass
class PlanOperation:
    id: str
    op: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanIR:
    version: str = CAPABILITY_VERSION
    operations: List[PlanOperation] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


def plan_from_dict(data):
    if not isinstance(data, dict):
        raise ValueError("Plan must be a JSON object.")

    raw_ops = data.get("operations")
    if not isinstance(raw_ops, list):
        raise ValueError("Plan must include an operations list.")

    operations = []
    for idx, raw_op in enumerate(raw_ops, start=1):
        if not isinstance(raw_op, dict):
            raise ValueError(f"Operation {idx} must be an object.")
        op_name = str(raw_op.get("op") or "").strip()
        if not op_name:
            raise ValueError(f"Operation {idx} is missing 'op'.")
        op_id = str(raw_op.get("id") or f"op_{idx}").strip() or f"op_{idx}"
        params = raw_op.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError(f"Operation {op_id} params must be an object.")
        operations.append(PlanOperation(id=op_id, op=op_name, params=params))

    metadata = data.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    return PlanIR(
        version=str(data.get("version") or CAPABILITY_VERSION).strip() or CAPABILITY_VERSION,
        operations=operations,
        metadata=metadata,
    )


def plan_to_dict(plan):
    return {
        "version": plan.version,
        "operations": [
            {
                "id": op.id,
                "op": op.op,
                "params": op.params,
            }
            for op in plan.operations
        ],
        "metadata": plan.metadata or {},
    }

