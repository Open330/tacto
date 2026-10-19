# Save and reuse only when repetition needs it

For a new reusable task, start `tacto session <bundle-id> --helper --reuse-guide
--save /absolute/new.json` with the normal interactive tools. The one guide
includes the parameter format and observation. Send one Flow: the successful
run saves that same unbound template privately. Do not write a duplicate Flow
file or run the completed task again to save it. Existing output paths refuse
before execution; failed UI tasks are not saved.

`params` is an object of current defaults, e.g. `{"amount":"11","result":"38"}`.
Use `{{amount}}` in `params.value` and `{{result}}` in a result check. Session
binds those defaults for this run and saves placeholders for later jobs:

```sh
tacto run /absolute/new.json --helper --think 0 -p amount=17 -p result=85
```

To parameterize an existing concrete Flow without any model/UI input:

```sh
tacto parameterize /absolute/concrete.json -p 11=amount -p 38=result -o /absolute/template.json
```

For a verified `tacto do` walk candidate, use `tacto candidates` then `tacto promote
<name> -p 25=n --no-decisions`. Digit sequences/derived formulas: read
[parameterization details](parameterization.md) only if the task requires them.


For several checks at the same checkpoint, use schema3 and `postcondition.all_of` from the
reuse guide. All predicates must hold on one observation. Keep a primary
field/equals plus `answers:true` to report the answer, with completion predicates
under all_of. Attach field/value checks directly to set_value; use captures on
verified actions for values needed later. Keep separate reads when the checkpoint
or data is different. This does not automatically rewrite existing Flows.

For several known parameter jobs, use [batch](batch.md) to keep one signed
connection. Read this only for repetition; a single replay still uses run.
