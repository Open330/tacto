To just open an address the person asked for, use `tacto open-url <URL>
--helper` (LaunchServices, no typing; `loaded_verified` from the page's web area,
`page_not_shown` for a redirect, never reopened). `already_open: true` with
`loaded_verified: false` means the page was already in front: done, not a failure;
do not open it again. `open_unconfirmed` may still finish later; observe, never
resend. Use the steps below only when a Flow must navigate an existing window.

For Safari address navigation, use File > Open Location… to start editing,
then `set_value` on `WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD` and a bound submit
`Return`. Setting the value and confirming without starting address editing can
leave an existing page unchanged even when `AXConfirm` reports success.

Scope the address selector and binding to the intended window's observed
Identifier. Before the app-wide menu, verify that this is Safari's main window.
If the menu needs the foreground, use `tacto activate com.apple.Safari --window
"<unique observed title>" --helper`.
Verify the selected window before continuing.

The Return step needs its input selector, binding, declared transmit effect,
pinned destination and an explicit attribute postcondition. The helper confirms
the exact URL with `AXConfirm`, without keyboard events. Replay checks the loaded
WebArea URL in that same window and records `browser_navigation` evidence.
Address text and a successful native action response are insufficient.

`commit_unverified` with `destination_not_shown` means the pinned destination
was not witnessed: inspect the current page without resending the confirmation.
A redirect to another URL does not count as the requested destination. For a
newly authorized navigation, include address-editing preparation in the flow.
See [URL confirmation](../docs/native-window-input.md#safari-url-confirmation)
for the receiving evidence and window identity rules.

Observed GitHub support-ticket, issue and pull-request detail links may carry a
quoted `URL`. Tacto pins that URL again at dispatch, so verbs in the record
subject do not turn reading the record into a delete/send action. Missing or
changed URLs are not treated as verified navigation.
