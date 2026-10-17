# Tacto visual identity

**Mark.** A lowercase *t* whose crossbar starts at the tip of a coral pointer.
Read left to right: the agent clicks in an app (the pointer), and the authored
path runs from that point (the *t*). The pointer tip sits on the crossbar's
left cap, so the two shapes read as one sign rather than a cursor placed next
to a letter. This replaced the open-return *t* on 2026-10-17. That mark carried
the reuse idea but read as "h" or "b" at small sizes. The earlier mark and the
touch-ripple alternative are in the exploration sheet.

The mark and wordmark are original drawings made for this project. They do not
imply trademark clearance or any affiliation with other products named Tacto.

## Assets

| Use | Path |
|---|---|
| Standalone mark (transparent) | `docs/assets/tacto-logo.svg`, `sites/tacto/dist/assets/tacto-logo.svg` |
| Mark + wordmark | `docs/assets/tacto-wordmark.svg`, `sites/tacto/dist/assets/tacto-wordmark.svg` |
| README / social cover | `docs/assets/tacto-banner.svg`, `sites/tacto/dist/assets/tacto-banner.svg` |
| Favicon (ink tile, like the app icon) | `sites/tacto/dist/assets/favicon.svg` |
| macOS app icon | `docs/assets/tacto-app.icns`, sources `docs/assets/tacto-app-icon.svg`, `docs/assets/tacto-app-icon-small.svg` |
| Explorations (open return, touch ripple, pointer) | `docs/assets/tacto-logo-options.svg` |

`setup/build_brand_assets.py` writes the mark, wordmark, favicon, explorations
and app icon from one set of constants; `--check` fails if a tracked copy has
drifted. All files are self-contained SVG: no fonts, scripts, links, raster
data or external references. The wordmark letters are drawn as paths, so it renders the
same everywhere. Only the banner uses live text, set in the system sans stack.

## Palette

| Role | Hex | Notes |
|---|---|---|
| Ink | `#1b211c` | Mark stroke and text. On dark backgrounds use ivory `#f6f4ed` for the stroke. |
| Coral | `#fa6b3b` | The pointer, accents. Not for small text. |
| Rust | `#c4532c` | Large display text on ivory (e.g. "Use it again."). |
| Ivory | `#f6f4ed` | Background; the *t* on ink tiles (app icon, favicon). |
| Muted | `#5f6b58` | Secondary text. |

## Construction and spacing

- 32-unit grid, monoline stroke 3.4 units with round caps and joins. Wordmark
  letters share the stroke: baseline y=26, x-height 13, ascender 19.
- The pointer is the macOS arrow shape at 0.76 scale, tip on the crossbar's
  start (6.5, 12). It overlaps the crossbar cap and stays clear of the stem.
- Clear space: at least the pointer's width (≈6 units at 32 px) on every side.
- Never detach the pointer from the crossbar, recolor it to ink, rotate it, or
  add effects. Do not use the mark as a cursor or as the "acting" indicator.

## Minimum sizes

- Mark: 16 px. At 16–32 px use the heavier small optical size (stroke 4.2) from
  `tacto-app-icon-small.svg`; prefer the ink tile on busy surfaces.
- Wordmark: 20 px tall. Below that, use the mark alone.

## Product and interface icons

Claude and Codex are identified with unmodified official raster assets served
locally, not font glyphs or approximations. They identify compatible products;
Tacto is independent and does not claim sponsorship or endorsement. These
third-party marks remain the property of their respective owners and are not
covered by the Tacto artwork description above.

Retrieved 2026-10-15:

| Local asset | Official source | Context |
|---|---|---|
| `sites/tacto/dist/assets/claude-icon.png` | [Claude homepage](https://claude.com/) · [original asset](https://assets.claude.com/95a868946ac8a31e5ff832e2899f294aa368b836.png) | Homepage favicon; 32 × 32 |
| `sites/tacto/dist/assets/codex-icon.png` | [Codex extension published by OpenAI](https://marketplace.visualstudio.com/items?itemName=openai.chatgpt) · [original asset](https://openai.gallerycdn.vsassets.io/extensions/openai/chatgpt/26.51007.21434/1791508178385/Microsoft.VisualStudio.Services.Icons.Default) | Official extension icon (OpenAI Blossom); 400 × 400 |

The Codex entry deliberately uses the icon shipped by OpenAI with its Codex
extension. It is the OpenAI Blossom, not an invented Codex-specific symbol.
See [OpenAI's brand guidelines](https://openai.com/brand/).

`sites/tacto/dist/assets/ui-icons.svg` contains project-authored vector symbols
for arrows, check, repeat, zero, swap, disclosure, stop, workflow and code file.
The same symbols serve static HTML and dynamic demo rows. Decorative icons are
hidden from assistive technology; adjacent labels provide the accessible name.
Textual workflow arrows and the calculator multiplication sign remain text.

Asset checksums (SHA-256):

- `claude-icon.png`: `4004381309500f25e49a1ae639912ce307ea81da74cc383954a1ac8b9d343fe6`
- `codex-icon.png`: `2ce1c57dd3b312417106a487815ab1235bddce17e1fa9b32a2f048bd71ee25d3`
