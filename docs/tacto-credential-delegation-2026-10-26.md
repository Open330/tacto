# Vault-backed delegated login: source verification

Date: 2026-10-26. This feature is a development source change. The published
v1.0.0-rc.9 app/DMG and installations have not been replaced by this work.

## Result

The credential policy previously sent both credential effects and credential
data to a mandatory human handoff. Ordinary task approval did not authorize a
Vault source or destination. The new `auth` route registers reusable app/site,
Vault-item and operation scopes, then obtains values inside the runtime and
completes delegated field entry or login without exposing plaintext to the agent.

- `fill`: field entry without submit. `login`: entry, one submit and declared
  success-condition verification. Login grants also allow fill-only use.
- macOS Keychain stores local values under Tacto's dedicated service. Bitwarden
  requires its existing unlocked session and exact configured server/item/field.
- Grants contain references and selectors, not credential values. They may
  expire or last until revoked; changing their provider invalidates them.
- Scope is checked before reading the Vault and on live frame ancestry at input
  and submit. Revocation/replacement during a Vault fetch prevents dispatch.
- Signed helper capability negotiation prevents sending values through old
  helpers. Credential operations are excluded from full-screen tracing and
  automatic access receipts contain only operation outcomes.
- The stage-specific skill directs agents to this route instead of asserting
  that delegated login is impossible. Normal runs do not require renewed
  approvals or invoke a model.

## Receiving verification

1. Browser SDK: an owned loopback HTTP form received the expected synthetic
   username/password, returned a session cookie and rendered the declared success
   element. Reuse observed the authenticated state without another credential
   read or submit. A wrong origin and navigation during Vault retrieval sent no
   credential into the replacement document.
2. macOS Keychain: synthetic item create/read/update/delete passed. No real user
   credential was inspected. All owned Keychain test items and test grants were
   removed after use.
3. Real Safari through the built, signed source launcher/helper: the owned HTTP
   receiver verified both synthetic fields; `login_verified` was returned. A
   second run returned `already_authenticated`, zero fields dispatched and no
   second POST. This explicit native receiving test passed in 6.893 seconds total
   (server/setup, navigation and both runs); this is not a comparative benchmark.
4. That Safari test initially exposed a real WebKit issue: setting AXValue on an
   unfocused field could acknowledge success, then lose the username when the
   password field received input. Credential writes now focus the exact AX
   element and recheck its origin before setting the value. The receiving test
   passed with that correction.
5. Full Swift launcher and helper builds, Developer ID signatures and helper
   bundle verification passed with the existing Tacto signing identifiers/team.
   No published binary was relabeled or modified.

## Boundaries

Bitwarden protocol tests use controlled responses for session/server/item
matching, caching, child-environment session delivery and npm/node lookup under
an isolated launcher PATH. A real user's Bitwarden item was not read or used to
log into an external account. The external Vault was neither unlocked nor locked.

A configured success condition proves the observed UI condition in its scoped
destination, not independent account identity. MFA, passkeys, challenges and
multi-screen SSO authoring are not implemented by this provider. Native Safari
verification covers a two-field owned HTML login form; it is not a claim that
every website login has been tested. Generic plaintext Flow parameters remain
separate from this Vault-backed execution route.

## Checks

- Credential unit, transport and local-browser suite: 33 tests, zero failures,
  one opt-in native test skipped in ordinary discovery.
- Explicit native Safari/Keychain/signed transport receiving test: one pass.
- Contract suites: 71 tests, zero failures, one optional test skipped.
- CLI suites: 60 tests, zero failures.
- Safety suites: 30 tests, zero failures.
- Access-record suite: nine tests, zero failures.
- Native activity-view fixture: one pass.
- Source compilation, dependency lock refresh and whitespace checks passed.
- Full discovery ran 3,011 tests in 497.463 seconds with 16 skips. One existing
  Swift source-extraction test failed because the newly inserted AX functions
  fell between its pure-function extraction markers. Moving the credential
  functions outside that region preserved the existing extraction contract;
  all five native selection-parameter tests then passed. The credential suite
  was rerun after that correction and the default non-expiring grant change.
  The entire eight-minute discovery was not repeated for that source relocation.
