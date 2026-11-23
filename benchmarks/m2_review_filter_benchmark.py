"""Reproducible M2 review-filter benchmark (not part of unit-test timing).

Run from the repository root:

    $env:PYTHONPATH = "src"
    python benchmarks/m2_review_filter_benchmark.py --sizes 10000,50000,100000

The benchmark builds synthetic rows in memory, attaches the real review model
and filter proxy, and reports population/filter/search latency plus traced peak
memory.  It writes no project data.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import tracemalloc
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QModelIndex  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from reclaim.gui.models import ReviewFilterProxy, ReviewTableModel  # noqa: E402
from reclaim.gui.services import ReviewRow  # noqa: E402


def make_rows(count: int) -> list[ReviewRow]:
    rows: list[ReviewRow] = []
    for index in range(count):
        rel = f"Documents/folder-{index // 500:04d}/record-{index:06d}.pdf"
        if index % 997 == 0:
            rel = f"Documents/needle/{index:06d}.pdf"
        rows.append(
            ReviewRow(
                entry_id=index + 1,
                source_id=f"source-{(index % 4) + 1}",
                source_label=f"Source {(index % 4) + 1}",
                rel_path=rel,
                size_bytes=1024 + index,
                kind="pdf",
                hash_status="ok",
                recommended="retain",
                recommended_plan="copy",
                effective_plan="copy",
                decision_source="recommendation",
                override_id="",
                override_reason="",
                duplicate_role="canonical" if index % 17 == 0 else "unique",
                duplicate_group=f"dup-{index // 17:06d}" if index % 17 == 0 else "",
                rationale="Synthetic benchmark row",
            )
        )
    return rows


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index]


def measure(count: int, repeats: int = 5) -> dict[str, Any]:
    app = QApplication.instance() or QApplication([])
    tracemalloc.start()
    rows = make_rows(count)
    started = time.perf_counter()
    model = ReviewTableModel()
    model.set_rows(rows)
    populated = (time.perf_counter() - started) * 1000
    proxy = ReviewFilterProxy()
    proxy.setSourceModel(model)
    app.processEvents()

    filter_all: list[float] = []
    filter_search: list[float] = []
    filter_copy: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        proxy.set_query("")
        proxy.set_mode("all")
        proxy.rowCount()
        filter_all.append((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        proxy.set_query("needle")
        proxy.set_mode("all")
        proxy.rowCount()
        filter_search.append((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        proxy.set_query("")
        proxy.set_mode("copy")
        proxy.rowCount()
        filter_copy.append((time.perf_counter() - started) * 1000)

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    # Force filter evaluation and verify indexed access works.
    assert proxy.filterAcceptsRow(0, QModelIndex())
    return {
        "rows": count,
        "model_population_ms": round(populated, 2),
        "filter_all_ms_median": round(statistics.median(filter_all), 2),
        "filter_all_ms_p95": round(percentile(filter_all, 0.95), 2),
        "search_ms_median": round(statistics.median(filter_search), 2),
        "search_ms_p95": round(percentile(filter_search, 0.95), 2),
        "filter_copy_ms_median": round(statistics.median(filter_copy), 2),
        "peak_memory_mib": round(peak / (1024 * 1024), 2),
        "current_memory_mib": round(current / (1024 * 1024), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sizes",
        default="10000,50000,100000",
        help="Comma-separated synthetic row counts",
    )
    parser.add_argument("--out", default=None, help="Optional JSON output path")
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    results = []
    for size in [int(item) for item in args.sizes.split(",") if item.strip()]:
        result = measure(size, repeats=max(1, args.repeats))
        results.append(result)
        print(json.dumps(result, sort_keys=True), flush=True)
    if args.out:
        Path(args.out).write_text(
            json.dumps(results, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
