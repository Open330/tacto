## Before you start: is `tacto` there?

```bash
command -v tacto || ls -l ~/.local/bin/tacto
```

`tacto` is the signed launcher from the Tacto checkout, linked into `~/.local/bin`
by that checkout's `skill/install.sh`. If neither command finds it, Tacto is not
installed on this machine — **stop and tell the user**: it is installed by
running `skill/install.sh` inside their Tacto checkout (after building it; the
script says what is missing, and `skill/install.sh --check` changes nothing).
Do not download or install an `tacto` from anywhere else, and do not look for a
substitute — a different program by that name is not this one.


## Select the signed native backend

For macOS app work, use the installed signed launcher with `--helper` on
`apps`, `observe`, `run`, `replay`, and `do`. Launching the signed executable alone
still selects the Python direct backend for these commands when the flag is
omitted. The direct backend has different capabilities and requires its own
permissions; it is an explicit development route, not a fallback for a failed
signed helper connection. A wheel's Python console script cannot create the
signed channel even when passed `--helper`.

```bash
tacto doctor --check                         # check without opening permission settings
tacto apps --helper                         # app metadata through the signed helper
tacto observe com.apple.calculator --helper  # observe through that same backend
tacto run flows/<name>.json --helper
tacto replay <name> --helper
tacto do '<task>' --helper
```

If `tacto` is absent from PATH but `~/.local/bin/tacto` links to this checkout's
launcher, use that absolute command path for subsequent calls. If PATH resolves
to another installation, inspect the link and run this checkout's
`skill/install.sh --check` before choosing the entry point. Do not use `uv run tacto`
or `.venv/bin/tacto` as a substitute for the signed native launcher.

`doctor` checks the runtime's permission boundary itself; it does not need
`--helper`. `eval` selects backends by scenario, so choose the documented signed
scenario rather than adding `--helper` to evaluator arguments. Static flow
commands such as `list`, `show`, `parameterize` and `promote` need no native flag.

Driving apps needs the permissions granted to Tacto's signed helper. If
`tacto apps --helper` reports an error or no apps, run `tacto doctor --check` and
report its result. Do not grant permissions or change the helper's signing
requirement yourself. See [installation and backend paths](../../docs/installation.md)
when a launcher or helper connection fails.


## Check the actual sandbox profile

Tacto needs a real GUI session. An actual agent sandbox can block screen and
accessibility APIs; Tacto then reports "NLC가 샌드박스 안에서 실행됐습니다"
(exit code 3). This is separate from macOS app permissions.

- With `danger-full-access` or unrestricted filesystem execution, run normally.
  Do not request escalation or add `sandbox_permissions` when the current tool
  policy forbids that argument. An approval policy of `never` does not itself
  mean the process is sandboxed.
- If execution is actually sandboxed and the current tool policy permits it,
  use `sandbox_permissions: "require_escalated"` with a task-specific
  justification to run outside that sandbox.
- If an actual sandbox blocks Tacto and escalation is unavailable, report the
  blocker and ask the user to run in a GUI-capable environment, such as
  `--sandbox danger-full-access`. Do not change permissions to fix a sandbox.

Reading and writing recipes (`tacto list`, `tacto show`, `tacto candidates`,
`tacto promote`) works inside a sandbox; only commands that touch the screen
(`apps`, `observe`, `run`, `replay`, `do`, `doctor`, `eval`) need it.
