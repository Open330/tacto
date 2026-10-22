# Resident helper experiment — not shipped

Warm reuse is the shipping opt-in, documented in [helper execution](../../docs/helper-execution.md).
Production helper/launcher compile inputs contain no NSXPCConnection, resident
agent flag or SMAppService registration. The source fragments here preserve the
resident experiment; they are not independently buildable shipping components.

To reproduce the complete signed A/B/C experiment, use a separate checkout of
branch `spike/helper-modes` at commit `34b6b23`. Its README contains the build,
registration and cleanup instructions. This directory's host builder deliberately
refuses production helper sources without `--resident-agent`.

Keep registration confined to the experiment, unregister in cleanup, compare
all modes on the same launcher and exclude fallback samples. Warm/resident
showed similar command latency in one-machine tests; neither result establishes
all app/permission/concurrency cases or a root cause for the earlier transient
Safari AX stall. Default per-command execution remains available.
