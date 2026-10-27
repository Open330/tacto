# Multi-screen login and account checks

Development after rc.10. Read the base credential guide first; do not reload
the general authoring guides. These are deterministic, declared screens, not
automatic support for arbitrary SSO, MFA, passkeys or challenges.

For username → password, put two objects in the grant's `stages` array. Each
has `fields`, `submit`, and `success` in the same shape as a single-screen grant.
The first success check must identify the password screen; the final check must
identify the signed-in screen. Keep `target` at the root. Root `origin` is the
default; explicitly declare a stage's `origin` / `success_origin` when a
configured redirect requires another destination. The runtime checks live frame
origins before each credential write and submit.

```json
{
  "target":"com.apple.Safari",
  "origin":"https://login.example.com",
  "stages":[
    {"fields":[{"name":"username","selector":{"role":"TextField","title":"Username"},"secret":{"vault":"work","item":"exact-item-id","field":"login.username"}}],"submit":{"role":"Button","title":"Next"},"success":{"field":{"role":"TextField","title":"Password"},"attribute":"Title","equals":"Password"}},
    {"fields":[{"name":"password","selector":{"role":"TextField","title":"Password"},"secret":{"vault":"work","item":"exact-item-id","field":"login.password"}}],"submit":{"role":"Button","title":"Sign in"},"success":{"field":{"role":"Heading","title":"Dashboard"},"attribute":"Title","equals":"Dashboard"}}
  ],
  "account":{"field":{"role":"StaticText","desc":"Account"},"attribute":"Value","secret":{"vault":"work","item":"exact-item-id","field":"login.username"}}
}
```

Copy selectors from the actual screen; do not copy these illustrative labels.
`account` is optional. When configured, the observed account attribute must
exactly match the selected Vault field. Comparison is internal; the expected
value never appears in the result. Use a Vault display-name field if the UI
shows a display name instead of the login username. `account_verified: false`
means no account check was configured, not that a particular account was proved.

The result's `repair` contains a field alias, `stage_index`, completed-stage
count and short hint. Resume from the present screen: a stage whose declared
success condition already holds is read-checked and skipped. Never restart a
submitted stage just because its response was uncertain. A durable journal
still requires reconciliation across a process restart; it does not silently
replay a reserved submission.

For a slow result screen, set root `verify_seconds` explicitly (default 10,
up to 120). It changes read-only result waiting; it does not retry submission.
