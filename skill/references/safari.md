For Safari address navigation, use File > Open Location… to start editing,
then `set_value` on `WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD` and a bound submit
`Return`. Setting the value and confirming without starting address editing can
leave an existing page unchanged even when `AXConfirm` reports success.

Scope the address selector and binding to the intended window's observed
Identifier. Before the app-wide menu, verify that this is Safari's main window.
If the menu needs the foreground, use `tacto activate com.apple.Safari --window
"<unique observed title>" --helper` when the person requested it or consented.
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
