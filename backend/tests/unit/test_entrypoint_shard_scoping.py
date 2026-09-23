"""
Regression test for a real cross-shard concurrency bug found manually while
verifying Batch 3E: the idle-flush loop in backend.app.worker.entrypoint
originally iterated ALL machines in the shared `hot_states` dict regardless
of which shard task was running it. With 4 concurrent shard tasks, this let
two different tasks call flush_buffers() on the same MachineHotState at the
same time, racing on its mutable buffers — some events got XADDed to the
events stream (from one task's copy) but never actually committed to the DB
(lost between the two racing transactions), which the correlator later
discovered as a foreign-key violation trying to link a "phantom" event.
"""
from unittest.mock import AsyncMock

import pytest

from backend.app.worker.entrypoint import flush_idle_machines_for_shard
from backend.app.worker.hot import MachineHotState
from contracts.shard import shard_for
from contracts.machine_config import MACHINES


@pytest.mark.asyncio
async def test_idle_flush_only_touches_machines_on_its_own_shard(monkeypatch):
    # Pick two real machines that land on different shards (guaranteed to
    # exist among the 6 configured machines, since NUM_SHARDS=4).
    machine_ids = list(MACHINES.keys())
    by_shard: dict[int, list[str]] = {}
    for mid in machine_ids:
        by_shard.setdefault(shard_for(mid), []).append(mid)
    shards_with_machines = [s for s, ms in by_shard.items() if ms]
    assert len(shards_with_machines) >= 2, "test needs machines spread across >=2 shards"
    shard_a, shard_b = shards_with_machines[0], shards_with_machines[1]
    machine_on_a = by_shard[shard_a][0]
    machine_on_b = by_shard[shard_b][0]

    hot_states = {
        machine_on_a: MachineHotState(machine_id=machine_on_a),
        machine_on_b: MachineHotState(machine_id=machine_on_b),
    }
    # Both have "lingering" buffered work.
    hot_states[machine_on_a].unacked_msg_ids = [b"1-0"]
    hot_states[machine_on_b].unacked_msg_ids = [b"1-0"]

    flushed_machines = []

    async def fake_flush_buffers(session, machine_id, state, redis_client, stream_key, group_name):
        flushed_machines.append(machine_id)

    monkeypatch.setattr("backend.app.worker.entrypoint.flush_buffers", fake_flush_buffers)

    redis_mock = AsyncMock()
    await flush_idle_machines_for_shard(
        hot_states, shard_index=shard_a, redis_client=redis_mock,
        stream_key=f"telemetry:{shard_a}", group_name="hot-worker",
    )

    # Only the shard-a task's own machine should have been flushed — never
    # the shard-b machine, even though both were present in the shared dict.
    assert flushed_machines == [machine_on_a]
