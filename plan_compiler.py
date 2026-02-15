"""Deterministic compiler from Plan IR to Fusion Python script body."""

from executors.chamfer_executor import compile_create_chamfer
from executors.bracket_executor import compile_create_mounting_bracket
from executors.cube_executor import compile_create_cube
from executors.extrude_executor import compile_extrude_profile
from executors.hole_executor import compile_create_hole


_OP_COMPILERS = {
    "create_cube": compile_create_cube,
    "extrude_profile": compile_extrude_profile,
    "create_hole": compile_create_hole,
    "create_chamfer": compile_create_chamfer,
    "create_mounting_bracket": compile_create_mounting_bracket,
}


def compile_plan_to_code(plan):
    lines = []
    for op in plan.operations:
        compiler = _OP_COMPILERS.get(op.op)
        if not compiler:
            raise ValueError(f"No compiler registered for operation '{op.op}'.")
        lines.append(f"# {op.id}: {op.op}")
        lines.append(compiler(op))
        lines.append("")
    return "\n".join(lines).strip()
