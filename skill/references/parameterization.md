# Digit sequences and derived values

To parameterize individual Calculator digit clicks, use `--digits`:

```sh
tacto parameterize flows/calc_12x34.json -p 12=a -p 34=b -p 408=result \
  --digits --derived='result={{a}} * {{b}}'
```

This transforms recipe IR without running the UI or calling a model. A derived
value uses bounded arithmetic, not Python evaluation. Read `tacto show <name>`
to inspect the saved recipe; replay with requested parameter values.

## Literal data

When screen text literally contains `{{name}}`, keep it out of the template
namespace. A saved Flow uses schema 4 and a read-only `literal_params` map:
`"literal_params":{"label":"{{name}}"}`, with `"{{label}}"` in the selector
or payload. Use a different alias from task inputs/captures; callers cannot
override it. Ordinary `params` remain reusable inputs. Schema 4 requires a
runtime that supports it; do not lower the schema to make an old runtime accept it.

The model compiler accepts `"literals"` for this map in its authoring response
and exports `literal_params` in the saved Flow. This spelling is specific to the
compiler response; manual Flow JSON uses `literal_params`.
