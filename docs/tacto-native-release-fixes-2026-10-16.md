# Tacto native release fixes — 2026-10-16

The previous peer review identified a development foreground-input conflict and
a native signing policy pinned to one project team. Both findings were corrected.
This report covers these changes and the permission/packaging preview, rather
than the entire unpublished development runtime.

## Native trust and build behavior

- Launcher and helper derive the required peer team from their own verified
  Apple-issued signature. The peer identifier remains exact. Ad-hoc signatures,
  a different team or identifier, and runtime environment overrides cannot pass.
- Signing scripts discover the selected certificate's actual team and verify
  its designated requirement. Changing teams warns that macOS grants may need
  to be given again. A missing or invalid certificate fails the build.
- Launcher, helper and app bundle are built and verified in staging before
  replacement. Intentional signing failures preserved the prior artifact hashes.
- The development VRoid implementation moved into `helper/experimental/`.
  The default build neither compiles nor advertises its foreground operations.
  An explicit `TACTO_ENABLE_EXPERIMENTAL_FOREGROUND=1` build is required, in
  addition to per-call consent. The publication runtime has no VRoid route.

## Permission and packaging preview

The setup app explains each grant and opens the appropriate System Settings
pane after a click. A draggable helper panel accompanies the settings pane.
Read-only status polling is bounded; agent connection remains disabled until
both required grants are confirmed. Opening the setup app does not grant rights
or install connections.

The checkout preview and standalone preview are separate builders. The latter
includes Python and the signed runtime, restricts its bundled root, checks
symlinks for escape and validates imports after relocation. All 174 native
runtime files were signed. A 59,109,495-byte DMG containing Tacto.app and the
Applications link passed signature and image-checksum verification. The build
and image checks did not launch, mount, install or change permissions.

## Verification

- 36 focused signing, default-profile, native-input and packaging tests passed,
  including actual signed probes rejecting wrong teams, wrong identifiers,
  unsigned code and environment spoofing.
- Six documentation checks passed in the publication checkout (42 total with
  the focused suite). Landing-page checks passed 83/83 with zero axe violations.
- Nine additional development VRoid guard tests passed without dispatching input.
- A private experimental-profile build included the expected VRoid symbols;
  the default helper excluded them. An invalid profile was refused.
- The permission state/placement check passed. Offscreen snapshots were rendered.
- The rebuilt launcher's help and read-only doctor check succeeded. A helper
  observation of a deliberately absent target returned `target_not_found`,
  confirming authenticated communication without input.
- Claude peer re-review approved the core signing and default-profile fixes,
  then separately approved the standalone root, same-team and symlink checks.

## Remaining distribution validation

The app and DMG are **developer previews**, not a completed public release.
Notarization and a fresh Mac's Gatekeeper, permission-grant, panel placement and
actual drag-install path have not been exercised. Existing rights on the build
machine do not establish fresh-install success. No signing credentials or local
preview binaries are committed to the repository.

## Follow-up: novice onboarding

Added numbered permission guidance, an explicit explanation of the system-list
name, and a collapsed alternative path with Finder reveal and copy-location
buttons. Password/restart guidance and next steps after connection are visible.
Failed receipts now invalidate stale grants and preserve actionable errors.
The helper identity is unchanged to preserve existing TCC grants.

The updated Swift state check covers a lost/invalid receipt after a successful
one. Five offscreen UI states cover no grants, one grant, ready, failed refresh
and expanded alternatives, with assertions for the connection gate and actual
file-URL drag payload. System Settings accepting a drop and fresh-user completion
remain outside these offscreen checks.

The permission-state check passed 12 receipt cases plus watch and placement
checks; the 14 packaging/documentation tests passed. Peer review found that a
failed initial check could prevent a refresh when returning from Settings;
the activation condition was corrected, with a guard for an uninitialized helper.
The expanded panel measures about 300 × 573 logical points in the rendered state.

Launching from a read-only installer now stops before permission checks or agent
connection and explains how to copy the app into Applications. A preview DMG was
mounted read-only without opening the app: Foundation reported the mounted app
as read-only and the copied app as writable. The image was then detached. No
system grants were changed. This checks the volume condition, rather than
claiming completion of the interactive install or first-permission flow.

## Live self-test using Tacto

Tacto's signed sessions operated the standalone GUI itself. The test clicked
Settings, expanded help, copied the helper location, revealed the helper in
Finder, and collapsed help. Independent Tacto observations confirmed the copy
feedback, the selected bundled helper, and the disabled agent-connection button
while screen capture remained unavailable. The bounded wait later ended and
retained the retry instruction. Permissions were not changed and connections
were not installed. One role-only close-button selector was refused before
input; the owned test process was subsequently stopped as test teardown.

Two findings were addressed:

1. The standalone package's Python module lived in site-packages, so native
   helper discovery looked below site-packages and doctor fell back to the
   interpreter's permissions. Discovery now recognizes the fixed bundled .venv
   layout, rejects a helper escaping that runtime and preserves source-checkout
   discovery. The relocation build check now requires the actual bundled helper
   path, in addition to Python/package imports.
2. On the tested macOS 27 machine the screen-capture deep link opened Device
   Control and Data Access. The GUI location text now names that observed
   integrated pane on macOS 27, while retaining the older label on earlier OSes.

The preview helper's own LaunchServices receipt reported Accessibility true
and screen capture false. This is an incomplete grant, not a successful
first-install test; GUI readiness was not relaxed. Dragging into the protected
system list, granting access and connecting the agent remain untested. Existing
checkout rights do not establish the copied helper's rights.

Sixteen focused helper-discovery, packaging and documentation tests passed,
plus the Swift permission-state/watch/placement checks. Private raw UI trees
remain local because they include account names and filesystem paths.

The rebuilt standalone app was retested: its doctor now recognizes the bundled
helper and reports screen capture as incomplete, matching the GUI's blocked
connection. The erroneous fallback to the interpreter's ready state is gone.
Tacto observed the updated macOS 27 location label in the actual rebuilt GUI.
The corrected standalone app and DMG passed signing, relocation/helper discovery
and image-checksum verification. Peer review approved the bounded path fix.
