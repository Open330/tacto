# Author from the current observation

A selector must match one element. Prefer stable identifiers; use role/title
when identifiers are unavailable. Do not reuse observation indices.

A deterministic Flow needs a target, steps, action meaning, and an observable
result. This schema example illustrates one temporary edit, not app-specific
identifiers to copy:

```json
{"schema":2,"name":"edit-entry","target":"<observed bundle-id>","safety_defaults":{"effects":["write_ephemeral"],"reversibility":"reversible","resource":{"object_type":"entry","scope":"entry"}},"steps":[{"action":"set_value","selector":{"role":"TextField","identifier":"<observed id>"},"params":{"value":"<requested text>"},"postcondition":{"field":{"role":"TextField","identifier":"<observed id>"},"attribute":"Value","equals":"<requested text>"},"answers":true}]}
```

`click` needs a selector; `set_value` also needs `params.value`. Use explicit result
checks where missed input would invalidate later actions. `skip_if` uses the
same check shape for an already-satisfied step.

For repeated fields, `selector_defaults` supplies common selector fields;
`check_defaults` supplies `field`/`attribute` to explicit `postcondition` and
`skip_if` only. Local check fields replace the entire shared field.
`safety_defaults` supplies common action meaning; local effects replace and
resource/destination objects merge. Keep approval evidence and `commit_point`
on the specific step. A missing contract is refused. Defaults apply in branches.

Read only if the observed task needs it:

- Ambiguous matches or ancestor targeting: [selectors](selectors.md).
- Unclear tree/window markers: [observation](observation.md).
- Persistent edits, deletion or sending: [safety](safety.md).
- TextEdit editing/saving: [documents](documents.md).
- Finder or menus: [finder/menus](finder-menus.md).
- Pointer/background preparation: [native input](native-input.md).
- Complex result checks/captures: [outcomes](outcomes.md).
- Branches or model judgment: [judgment](judgment.md).
- Parameterize/store an already proven flow: [reuse](reuse.md).
- Literal `{{...}}` screen labels or text: [literal data](parameterization.md#literal-data).
- Web upload registration: [upload API](../docs/api.md#cli-browser-upload-registration).

For uncovered schema fields, look up only the relevant section of
[API](../docs/api.md). A successful action is not proof of the user's outcome.

The model compiler `tacto compile` corrects an invalid recipe at most once by
default, without running it. Use `--repair-rounds 0` to prevent that extra call.
`--verify` separately enables UI verification. A caller-supplied model deadline
covers correction calls too; this is not a guarantee of remote cancellation.
The shared deadline uses wall time, so UI verification also reduces the time
remaining for a later correction; it does not interrupt that UI verification.
