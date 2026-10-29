# Same-app window concurrency: local runtime measurement

Measured 2026-10-29 with `examples/benchmark_parallel_windows.py` on macOS.
Candidate source: `c3c88c0`; baseline: installed v1.0.0-rc.12. This measures the
concurrency implementation before the subsequent approved-credential changes.
Binary hashes and source identity were checked before and after the run.

One owned AppKit process exposes four independent windows. The fixed workload
edits one field and invokes AXPress in each window. Each arm ran five timed
repeats in seeded shuffled order, after warmup. All 80 timed jobs received their
exact expected values. These are local runtime timings, not agent benchmarks.

| Arm | Concurrent jobs | Median time for four jobs | Entire resident warm pool RSS |
| --- | ---: | ---: | ---: |
| Installed rc.12, serial | 1 | 2.457 s | 52.9 MiB |
| Candidate, serial | 1 | 2.471 s | 201.1 MiB |
| Candidate | 2 | 1.406 s | 201.5 MiB |
| Candidate | 4 | 0.915 s | 201.1 MiB |

Four-way execution took about 63% less time than the serial baseline (2.68x
throughput for this fixed workload). Serial execution did not improve. After
warmup, all four candidate workers remain resident even during serial runs;
the table includes those idle workers rather than reporting only active RAM.

Runtime model calls: zero. Agent input/output tokens: **not measured**. This is
not evidence of new token savings or equivalent speedups in arbitrary apps.
Five samples per arm and one local fixture limit generalization.

Reproduce with the harness above and separately run the opt-in
`tests/test_window_parallel_native.py` receiving test. Window scopes use live
process-start/window identity and AX descendants. Same-window/app-wide conflicts
wait before dispatch, bounded by the request deadline; connection loss or user
stop cancels the wait. Pointer/menu/key operations remain serialized. Actions
with an uncertain dispatch are not automatically resent.
