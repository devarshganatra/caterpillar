"""
Deterministic operator/task assignment for the simulator.

The simulator previously generated random operator_id/task_id per machine on
every run, so telemetry never matched a real Task or User row and the warm
path (ETA, idle attribution) had nothing to look up. This module maps demo
machines to the operator/task rows created by seed_db.py.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class DemoAssignment:
    operator_id: str
    task_id: str | None


# Keys are UUIDs from seed_db.py; task ids match the seeded Task rows.
_OPERATOR_ID = "11111111-1111-1111-1111-111111111111"

DEMO_ASSIGNMENTS: dict[str, DemoAssignment] = {
    "EXC001": DemoAssignment(operator_id=_OPERATOR_ID, task_id="TASK-001"),
    "LDR001": DemoAssignment(operator_id=_OPERATOR_ID, task_id="TASK-002"),
}

UNASSIGNED = DemoAssignment(operator_id="UNASSIGNED", task_id=None)


def get_assignment(machine_id: str) -> DemoAssignment:
    return DEMO_ASSIGNMENTS.get(machine_id, UNASSIGNED)
