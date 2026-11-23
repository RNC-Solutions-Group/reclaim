# M2A — Review Table Benchmark

## Method

`benchmarks/m2_review_filter_benchmark.py` builds synthetic `ReviewRow`
collections in memory, attaches the real `ReviewTableModel` and
`ReviewFilterProxy`, and measures:

- model population (`set_rows`);
- unfiltered proxy invalidation (`rowCount`);
- search filter (`needle` query);
- copy-only filter;
- traced peak memory (rows + model + proxy), using `tracemalloc`.

The benchmark is outside the unit-test suite and is not a regression timing
gate. Run:

```powershell
$env:PYTHONPATH = "src"
python benchmarks/m2_review_filter_benchmark.py --sizes 10000,50000,100000
```

## Results

Environment: Windows 11 / NTFS / Python 3.13.5 / PySide6 6.9.3. Medians of five
filter repetitions. Search/filter latency varies substantially between runs
with Qt filter invalidation and host load; these are indicative, not guarantees.

| Rows | Model population | Filter all (median / p95) | Search (median / p95) | Copy filter (median) | Peak traced memory |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 10,000 | 0.23 ms | 194.48 / 301.81 ms | 293.29 / 564.54 ms | 251.07 ms | 4.47 MiB |
| 50,000 | 0.16 ms | 814.37 / 1769.57 ms | 1584.88 / 2578.76 ms | 804.63 ms | 22.40 MiB |
| 100,000 | 0.13 ms | 1340.94 / 1414.81 ms | 2193.38 / 2413.26 ms | 1431.62 ms | 44.72 MiB |

## Observations

- **No full-list copy in the hot path.** `ReviewFilterProxy` uses
  `ReviewTableModel.count()` and `row_at(index)`, and selection uses the same
  indexed access. `rows()` remains a compatibility accessor but is not used by
  filtering or selection.
- **Model population is effectively constant** (list assignment/reset).
- **Peak memory is linear** in synthetic rows: roughly 4.5 MiB / 10k,
  22.4 MiB / 50k, and 44.7 MiB / 100k, including rows and Qt model/proxy state.
- **Filtering remains synchronous on the GUI thread.** At 100k rows the Qt proxy
  invalidation is in the ~1–2 second range on this host, which is a noticeable
  pause. The table itself is virtualized and does not create per-row widgets, but
  this is a remaining M2 alpha limitation rather than a claim of instantaneous
  filtering.
- `QSortFilterProxyModel` sorting was disabled for the benchmark; the GUI still
  enables table sorting for user interaction, which can add latency at very
  large row counts.
- A future optimization (debounced filtering or a precomputed search index)
  would need its own memory/latency tradeoff review; it is not required to fix
  the M2A full-list-copy defect.

## Reproducibility

- No source tree or project database is touched.
- Rows are synthetic and deterministic.
- No private data is used.
- Benchmark output should be written outside the repository or into the
  gitignored `benchmark-output/` path.
