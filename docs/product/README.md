# Reclaim Product & Engineering Documents

This directory contains the repository-safe product direction, architecture
assessment, roadmap, architecture decisions, and evaluation strategy. It
contains no real personal paths, filenames, content, or migration artifacts.

## Reading order

1. [Product outlook](product-outlook.md) — purpose, users, editions, success
   criteria, non-goals.
2. [Architecture assessment](architecture-assessment.md) — CP1 fit/gap,
   contracts, extraction/semantic technology, security, and migration path.
3. [Roadmap](roadmap.md) — proposed milestones, dependencies, gates, and alpha
   definition.
4. [Architecture decisions](architecture-decisions.md) — consequential choices
   requiring operator review.
5. [Testing and benchmark strategy](testing-and-benchmarking.md) — corpus,
   metrics, configurations, and provisional gates.
6. [Immediate next assignment](next-assignment.md) — recommended first work
   package after CP1 corrective work.

Historical CP1 documents remain in [`../cp1/`](../cp1/). The CP1 roadmap is
superseded by the product roadmap; the CP1 audit and architecture remain useful
background.

M1 implementation documentation is in [`../m1/`](../m1/): architecture, data
model, operator guide, and the test/acceptance matrix. M2 desktop documentation
is in [`../m2/`](../m2/): architecture, operator guide, screenshots, test matrix,
and Qt packaging notes. M3 evidence documentation is in [`../m3/`](../m3/):
architecture, evidence contract, operator guide, benchmark, and dependencies.

## Status

The product direction is a proposal. Implemented and synthetically validated:
the CP1 inventory/analysis engine, M1/M1A planning and journaled execution, the
M2 PySide6 desktop shell, and the M3 bounded evidence subsystem. Real-data
migration, classification, naming intelligence, semantic search, installers,
and external AI remain unimplemented or separately authorized. See the
[roadmap](roadmap.md) and [ADRs](architecture-decisions.md) for current gates.
