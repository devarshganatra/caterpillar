---
doc_id: truck-loading-coordination
title: Truck Loading Coordination
tags: [IDLE_DEVIATION, ETA_SLIP]
---

## Overview

Truck loading tasks are unusual in that the excavator or loader's cycle
time is only half the story — overall task duration also depends on how
often a truck is actually present and how large the haul queue is. A
loading machine that is fast but frequently waiting on trucks will still
show a long total task time.

## Why Truck Availability Is A Site Cause, Not An Operator Cause

When no truck is present, there is genuinely nothing productive for the
loading machine to do — the correct attribution for that idle time is
site logistics, not the operator. This holds even if the operator could
theoretically reposition material or "look busy"; forcing unnecessary
machine movement to avoid an idle flag wastes fuel and adds wear for no
real benefit.

## Reading Hauler Queue Length

A queue length of zero with no truck present is unambiguous site-caused
idle. A queue length of one or more with a truck present but the machine
still idle, with no other explanation, is where operator-attributed idle
becomes the more likely explanation — the work was available and not
being done.

## Why ETA Should Include A Site-Queue Factor

An ETA prediction that ignores hauler availability will systematically
underestimate loading tasks in queue-constrained periods and
overestimate them when trucks are plentiful. Including a site-queue factor
in the estimate, and re-factoring it live as queue conditions change, is
what keeps the live ETA meaningful rather than a fixed guess from the
start of the task.

## Recommended Response

If a loading task's ETA is slipping and attribution shows SITE-caused idle
dominating, the fix is a logistics conversation about truck cycle times or
dispatch — not operator coaching.
