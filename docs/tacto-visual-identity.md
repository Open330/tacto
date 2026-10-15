# Tacto visual identity

**Mark.** A lowercase *t* whose crossbar runs on into a return arc that stays
open at the bottom. A coral dot starts the stroke. Read left to right: an agent
touches the app (coral), the authored path is drawn once (the *t*), and it comes
back around for the next run (the open return). The gap is deliberate. A closed
loop reads as "b" below 32 px, and a full circle reads as a generic
record/refresh icon. Both alternatives are in the contact sheet.

The mark and wordmark are original drawings made for this project. They do not
imply trademark clearance or any affiliation with other products named Tacto.

## Assets

| Use | Path |
|---|---|
| Standalone mark (transparent) | `docs/assets/tacto-logo.svg`, `sites/tacto/dist/assets/tacto-logo.svg` |
| Mark + wordmark | `docs/assets/tacto-wordmark.svg`, `sites/tacto/dist/assets/tacto-wordmark.svg` |
| README / social cover | `docs/assets/tacto-banner.svg`, `sites/tacto/dist/assets/tacto-banner.svg` |
| Favicon (coral tile) | `sites/tacto/dist/assets/favicon.svg` |
| Explorations (A/B/C) | `docs/assets/tacto-logo-options.svg` |

All files are self-contained SVG: no fonts, scripts, links, raster data or
external references. The wordmark letters are drawn as paths, so it renders the
same everywhere. Only the banner uses live text, set in the system sans stack.

## Palette

| Role | Hex | Notes |
|---|---|---|
| Ink | `#1b211c` | Mark stroke and text. On dark backgrounds use ivory `#f6f4ed` for the stroke. |
| Coral | `#fa6b3b` | The touch dot, favicon tile, accents. Not for small text. |
| Rust | `#c4532c` | Large display text on ivory (e.g. "Use it again."). |
| Ivory | `#f6f4ed` | Background; touch dot on the coral favicon tile. |
| Muted | `#5f6b58` | Secondary text. |

## Construction and spacing

- 32-unit grid, monoline stroke 3.4 units with round caps and joins. Wordmark
  letters share the stroke: baseline y=26, x-height 13, ascender 19.
- Clear space: at least the touch-dot diameter (≈6 units at 32 px) on every side.
- Never close the return arc, recolor the dot to ink, or add effects.

## Minimum sizes

- Mark: 16 px. At 16–24 px prefer the favicon tile on busy or dark surfaces.
- Wordmark: 20 px tall. Below that, use the mark alone.
