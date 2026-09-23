---
doc_id: fuel-efficiency
title: Fuel Efficiency
tags: [OPERATIONAL_ANOMALY]
---

## Overview

Fuel consumption per work cycle is one of the most sensitive indicators of
how a machine is actually being operated. Two machines doing the same task
under the same conditions should use roughly the same fuel per cycle; a
sustained deviation from that baseline is usually explainable, but rarely
random.

## Common Causes Of High Fuel Per Cycle

Excess idling at high RPM between cycles, working at a higher engine speed
than the task requires, an engine or hydraulic system that is not
operating efficiently (e.g. dragging brakes, worn hydraulic seals), or a
task that is genuinely harder than a "typical" instance of that task type
(denser material, longer travel distance) can all raise fuel-per-cycle.

## Why This Is Flagged As An Anomaly, Not A Fault

Fuel-per-cycle alone does not identify which of these causes applies — it
only identifies that something is different from the machine's normal
operating pattern. Pairing it with other signals (engine RPM variability,
cycle time consistency, temperature trend) usually narrows down whether
the cause looks mechanical, behavioral, or task-related.

## Erratic Cycle Times

Cycle time that varies a lot from one cycle to the next (rather than being
fairly consistent) often points to a different cause than steady high fuel
use: obstacles requiring repositioning, an operator who is still learning
the task, or material that varies significantly in difficulty across the
work area.

## Recommended Response

When an operational anomaly is flagged with fuel or cycle-time drivers,
check recent maintenance history first, then compare against what the task
and conditions would predict before assuming an operator behavior issue.
