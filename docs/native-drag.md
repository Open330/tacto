# Window-bound drag

The previous drag implementation was an experimental VRoid-only route. It did
not expose drag for ordinary macOS apps. The signed helper now supports `drag`
for a continuous left-button stroke inside one observed window. It sends
PID/window-addressed events and preserves the user's global cursor/foreground.

```sh
tacto drag com.example.app --helper --window 'Observed title' --from 20 80 --to 220 80
```

Coordinates are window-relative points, including the titlebar. Optional
`--via X Y` preserves each waypoint, `--duration` sets 0.1–2 seconds, and
`--prepare` prepares a background AppKit window that ignores first-mouse input.
The target must be the app's focused window, with the app in the background.
Use `tacto capture <bundle-id> --helper --window TITLE --output window.png`
for a fresh private PNG and pixel-to-point scale factors. Multiply image pixel
coordinates by those factors before dragging; Retina pixels differ from points.
The default output stays compact; `--json` includes diagnostics and a fresh tree.
See [the agent drag guide](../skill/references/drag.md) for reusable Flow steps
and [the action API](api.md#window-bound-drag-signed-macos-helper).

The dispatcher checks process/window identity and geometry, emits one timed
stroke, and attempts mouse-up at the last sent point when cancelled or failed.
It does not retry an uncertain gesture. Expensive AX readiness is checked at
the stroke boundaries; the timed path uses lightweight context/input/window
checks. Event timestamps advance with the stroke and every waypoint is retained.

## Receiving tests

Verified on macOS using the signed development launcher/helper:

| Case | Receiving evidence |
| --- | --- |
| Opaque AppKit canvas | Down, intermediate drag events and up received in the same window with one event number and correct coordinates/pressure |
| Standard NSSlider | Value changed from 20 to 82.3684; a reusable Flow returned it to 20 and checked the observed value |
| Actual TextEdit | After background preparation, AXSelectedText was `Hello, World!`, AXSelectedTextRange was location 0 / length 13, and document text was preserved |
| Cancellation, post failure, window drift | Injected native tests checked mouse-up cleanup at the last attempted point and no gesture retry |
| Short multi-segment paths | Native event tests retain exact waypoints and increasing timestamps |

The earlier straight/canvas samples took about 0.48–0.81 seconds inside the
helper, with 0.4–0.6 seconds of that intentionally spent on the stroke. These
are individual functional measurements, not a cross-tool performance benchmark.

An `acted` response proves dispatch, not application receipt. A Flow must have
a receiving postcondition and reobserve before continuing. A one-shot caller
must inspect the resulting selection/value/canvas. Some apps ignore background
mouse events even when the sequence is well formed.

Finder file transfers, HTML DragEvent semantics, moving/resizing windows and
cross-app drops are not established by these tests and are not advertised by
this route. They need separate receiving protocols and integration tests.
