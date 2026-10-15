# Browser examples recorded on 2026-10-15

Three real website tasks were executed in dedicated Chromium contexts through
Tacto's Python `BrowserDriver` and `Flow` replay. No personal browser profile,
existing Chrome/Safari tab, account credentials or desktop was used. The
recording source is [record_browser_demos.py](../sites/tacto/record_browser_demos.py).

| Example | Steps | Independently checked result | Scope |
|---|---:|---|---|
| Wikipedia search and section navigation | 4 | Final URL is `https://en.wikipedia.org/wiki/Seoul#History`; History heading is visible and the page scrolled | Real public information site |
| Travel category and book detail | 3 | Exact selected book title, £49.43 and `In stock (15 available)` match the rendered product | Books to Scrape public sandbox; fictional prices, no purchase |
| Multi-field draft | 5 | Text, textarea, date and select value match; original form URL retained; Submit never clicked | Selenium public test form, no registration submitted |

[Wikipedia video](../sites/tacto/dist/assets/demos/browser-wikipedia.mp4) ·
[Shopping video](../sites/tacto/dist/assets/demos/browser-shopping.mp4) ·
[Form video](../sites/tacto/dist/assets/demos/browser-form.mp4).

The four draft values are synthetic: `Tacto Demo`, a workshop inquiry,
`10/20/2026`, and option `Two` (value `2`). Password and file fields were untouched.
The shopping site explicitly describes its prices as random test data.

## Recording and reproduction

The host opens a fresh headless Chromium page and records its 1280 × 800
viewport. This is a live page recording, not a reconstructed website or browser
chrome screenshot. Each task action resolves a fresh observed accessibility
node through Tacto. A separate read of URL/DOM values checks the final outcome;
it is not used to perform the task inputs.

An aria-hidden, pointer-events-none overlay identifies the observed element's
bounds. Blue rings follow actual browser pointerdown events. The displayed
arrow is a guide annotation, not a system cursor recording. The original final
PNG is captured with the overlay hidden; posters and videos include the guide.
Pauses of 450 ms before and 650 ms after actions improve legibility. These
single demonstrations do not establish runtime speed, agent token cost or
browser-wide compatibility. Natural-language authoring is not filmed. No model
calls occur inside the authored replay.

The source websites may make their own network/telemetry requests. For example,
non-GET event requests were observed while visiting Wikipedia. Tacto's shown
task did not enter credentials or click a purchase/registration submission.
The landing page itself serves local videos and screenshots only; visiting it
does not load or embed the source sites.

Use a new private output path in a project environment with Playwright,
Chromium and ffmpeg installed:

```sh
python3 sites/tacto/record_browser_demos.py --output /tmp/tacto-browser-recording-new
```

Each scenario stores its Flow, observations, action times, bounds, replay log
and independently checked result in the private output directory. Only the
curated [public summary](../sites/tacto/dist/assets/demos/browser-examples.json),
local MP4, captions and final screenshots are published. Full page trees and
raw browser profiles are not published.

Sources remain owned by their respective publishers:
[Wikipedia article and content attribution](https://en.wikipedia.org/wiki/Seoul),
[Books to Scrape sandbox](https://books.toscrape.com/),
[Selenium test form](https://www.selenium.dev/selenium/web/web-form.html).
No affiliation or endorsement is claimed.

## Findings while preparing the examples

- Wikipedia changes its search control's accessibility role after text entry.
  The input action uses its observed role; the value check uses the unique
  observed accessible name so the legitimate role change does not hide it.
- A Return postcondition cannot merely search for the typed text: that text
  already exists in the input before Return. Completion must describe a new
  state. The current BrowserDriver also refuses Return as a navigation key.
  The TodoMVC Enter-only scenario was therefore excluded; the key policy was
  not relaxed and no direct Playwright key press was substituted.
- An exact HTML-label lookup did not identify the Selenium select reliably.
  Independent verification uses its observed combobox role and accessible name.
