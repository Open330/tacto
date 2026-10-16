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
