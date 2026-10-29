# Credential entry and Vault-backed login

Flow `authenticate`, multi-screen grants, account checks and app Vault settings
require rc.11 or later. rc.10 supports the original single-screen CLI route.

For an already authorized task with a user-provided value, use `set_value` on
its observed password field and `run --yes`. The task approval covers the input;
no separate Vault registration is required. New and confirmation-password fields
work the same way. Tacto masks the entered value in runtime results and traces.
Prefer parameters and private input channels to literal secrets in saved Flows,
command arguments or shell history. Filling a field does not submit the form;
keep submission as its own requested step. Use app scope for credential edits,
which may need to focus the exact field (rather than a `--window-id` session).

For secrets that should stay outside agent context, use the optional Vault route
below. Read this only for credential entry or login. Vault credentials stay outside model
context, Flow JSON, command arguments, traces and Tacto access logs. The runtime
reads the selected Vault field and sends it directly over the signed helper
connection. Tacto does not unlock, lock or change the external Vault session.

## Use an existing delegation

`tacto auth list` lists grant names, app/site and allowed level (no secrets or
Vault item references). Select the matching scope. Run `tacto auth login NAME --helper` for
login, or `tacto auth fill NAME --helper` to fill without submitting. A login
grant also permits fill-only use; a fill grant cannot submit. No repeated
approval or model call is needed. Open the requested login page before running.
`login_verified` means the configured success condition and destination were
observed. `already_authenticated` means that condition was already present;
it does not independently establish which account owns the existing session.

For a saved Flow, add `{"action":"authenticate","params":{"grant":"NAME"}}`
at a protected-work boundary. The runtime checks the registered scope, logs in
only when its success condition is absent, then continues the next Flow step on
a fresh observation. No selector/safety contract or secret belongs on this
host step. A missing/expired grant is caught before earlier Flow inputs run.
Vault values are also masked in later Flow results if the app echoes them.

## App setup

In the updated app, open **설정 → Vault·로그인 위임 관리**. Register a Vault,
choose an existing connection to edit it, or store a local item using the
secure Keychain field. An agent authors the selector/reference plan below;
the user can drop that JSON file on **로그인 위임**, review its app/site and
level, save it, or revoke an existing grant. The user does not write JSON.
The preview is bound to the file's digest; changing it requires another review.
Choosing fill-only narrows the imported plan and cannot grant submission.

## Register once

Register one provider:

```sh
tacto auth vault work --bitwarden https://vault.example.com --executable "$(command -v bw)" --session-file /absolute/private/session-file
# Or local macOS Keychain:
tacto auth vault local --keychain
tacto auth put account-password
```

Bitwarden must already be unlocked; use an existing `BW_SESSION` or a private
owner-only session file. Use the actual configured Vault server and exact item
ID, not an ambiguous item name. Keychain `put` reads hidden terminal input or
stdin, never a password argument. Do not retrieve credentials into agent output
or shell command text. Local items use the `password` field; create a separate
item for a username if using the local provider.

Observe the login screen once with Tacto and copy fixed selectors from that
observation into a plan containing references only. Example for Safari (replace
selectors, site and item ID with the actual authorized destination):

```json
{
  "target": "com.apple.Safari",
  "origin": "https://login.example.com",
  "level": "login",
  "minutes": 0,
  "fields": [
    {"name":"username","selector":{"role":"TextField","identifier":"username"},"secret":{"vault":"work","item":"exact-item-id","field":"login.username"}},
    {"name":"password","selector":{"role":"TextField","identifier":"password"},"secret":{"vault":"work","item":"exact-item-id","field":"login.password"}}
  ],
  "submit": {"role":"Button","title":"Sign in"},
  "success": {"field":{"role":"StaticText","title":"Signed in"},"attribute":"Title","equals":"Signed in"}
}
```

`tacto auth grant work-login /absolute/login-plan.json --yes` registers the
user's delegation. Omit `minutes` (or use `0`) to keep it until revoked; set a
positive value only when the user wants a time-limited delegation.
Choose `level: "fill"` and omit `submit`/`success` for entry without submission.
Native application grants can omit `origin`; browser fields require their
actual web origin. HTTPS is supported; HTTP is limited to local loopback tests.
An explicit `success_origin` supports an authorized post-login redirect.

`tacto auth revoke work-login` revokes the delegation. Replacing a provider
configuration invalidates existing grants using that provider.

For username → password screens or checking the logged-in account, read
[stages and account checks](credential-stages.md) only when needed.

## Resolve a failed run

- `credential_driver_upgrade_required`: update both launcher and helper; do not
  send a credential through an older unguarded transport.
- `vault_locked` / `vault_server_mismatch`: use the user's authenticated session
  for the registered server. Do not repeatedly unlock or lock their Vault.
- `credential_scope_mismatch` / `credential_target_missing`: observe the actual
  login screen, then repair its destination/selectors. No secret was returned
  to the agent. Frame origins are checked again immediately before input.
- The `repair` summary names only the failed field alias and stage, completed
  stages and a short hint. Repair that selector; keep the remaining scope and
  Vault references. Previously verified stages are skipped when their success
  screen is still present. An uncertain submit is never automatically retried.
- `credential_account_mismatch`: use the authorized account-switch path; the
  runtime did not silently accept the currently signed-in different account.
- `credential_login_unverified`: inspect the resulting session before another
  submission. Tacto does not retry an uncertain submit automatically.

This credential route uses its own live verification rather than a general
Flow's typed commit receipt. `run --yes` alone does not choose a Vault item.
MFA, passkeys and challenges are not implemented by this credential provider.
Use the next supported step for that actual screen; do not infer a permission
problem from an unrelated failure.
