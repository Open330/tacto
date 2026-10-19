# Calculator: self-contained authoring

Use this instead of authoring.md. Confirm identifiers in the session observation.
Flow shape (one digit example; adapt to the requested calculation):

```json
{"schema":2,"name":"calculate","target":"com.apple.calculator","selector_defaults":{"role":"Button"},"check_defaults":{"field":{"role":"ScrollArea","identifier":"StandardInputView"},"attribute":"Value"},"safety_defaults":{"effects":["write_ephemeral"],"reversibility":"reversible","resource":{"object_type":"calculator entry","scope":"entry"}},"steps":[{"action":"click","selector":{"identifier":"One"},"postcondition":{"equals":"1"},"answers":true}]}
```

Start from zero. For a nonzero/partial entry, prepend two reset steps matching
`identifier:["AllClear","Clear"]`, each with `safety:{"effects":["delete"]}`
and `skip_if:{"equals":"0"}`; the second also checks `postcondition:{"equals":"0"}`.
Clear removes the current operand; the second reset clears the remaining expression.

Add digit/operator/Equals click steps using observed identifiers. Check each digit's
actual display, including the left operand/operator for the right operand
(e.g. `2 × 3`). The final Equals step checks the computed result and has
`answers:true`; earlier steps do not. Defaults fill only explicit selector/check
objects. Use stable identifier prefixes when the tree appends live state after `;`.
An uncertain outcome must be inspected before another input/retry.
