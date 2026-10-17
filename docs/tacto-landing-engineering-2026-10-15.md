# Landing page verification — 2026-10-15

On this date the logo and wordmark used an open return stroke and a coral touch
dot; the pointer *t* replaced them on 2026-10-17. The
[visual identity guide](tacto-visual-identity.md) records the alternatives,
construction, minimum sizes and reusable SVG assets.

## Delivery measurements

Measured against the running LAN service using HTTP GET requests. Values below
are response-body bytes for HTML, CSS and JavaScript only; headers, logos and
favicon are excluded. The final page also includes keyboard and no-JavaScript
fallback improvements, so the before/after comparison includes those changes.

| Resource | Before (uncompressed) | Final (identity) | Final (gzip) |
|---|---:|---:|---:|
| HTML | 16,185 | 25,602 | 6,995 |
| CSS | 19,531 | 23,388 | 5,429 |
| JavaScript | 3,232 | 5,583 | 2,133 |
| Total | 38,948 | 54,573 | 14,557 |

The transferred core body is 62.6% smaller than before. Compression reduces the
same final resources by 73.3%. Conditional requests with matching ETags return
304 with no body; files are revalidated so replacements at the same URL appear
without an indefinite cache delay. The wordmark adds 516 compressed bytes.

These measurements concern website delivery. They do not establish lower agent
token usage or computer-use execution time. Local paint timings vary and are
not presented as a causal speed improvement.

## Verification

- 42/42 Playwright browser checks passed against the deployed LAN page.
- axe-core 4.14.0 found no violations in default desktop, expanded content and
  mobile states. Automated results do not establish complete WCAG conformance.
- Keyboard tabs, skip link, clipboard fallback, 320–768 px layouts, reduced
  motion and JavaScript-disabled content were checked in fresh browser contexts.
- No external resource requests, browser storage writes or script errors were
  observed during those checks.
- 11/11 static-server integration tests passed with macOS Python 3.9. Tests cover
  compression negotiation, variant ETags, conditional requests, same-size file
  updates, HEAD, hidden/parent paths, symlinks, directories and bounded files.
- 6/6 documentation consistency checks passed in the project Python environment.

The shipped page has no build step, third-party script, tracking or external
font dependency. Playwright and axe are QA-only tools. Reproduce the browser
checks using the [onboarding page guide](../sites/tacto/README.md).

## Official icon follow-up

Replaced the Unicode stand-ins throughout the landing page with official
product images and a shared SVG UI symbol sheet. This includes all six product
marks, link arrows, completion and stop states, repeat, workflow, code file,
feature symbols and disclosure controls. Source URLs and hashes are recorded
in the [visual identity guide](tacto-visual-identity.md#product-and-interface-icons).
Textual step arrows and calculator operators remain meaningful text.

The updated page passes 45/45 browser checks on the LAN service, including
product-image loading, actual SVG symbol bounds and absence of placeholder
glyphs. All three axe states still report no violations. The measurements above
predate this icon update and are retained as that revision's results.

The LAN check exposed connection resets during concurrent asset loading with
the server's five-connection default backlog. Increasing the listening queue
to 32 resolved the observed failure; the final LAN browser check and three
additional fresh browser loads completed without failed resource requests.
