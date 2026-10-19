"""Build a signed checkout-backed setup app without launching it."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import shutil


def build(output: Path):
    source = Path(__file__).resolve().parent
    root = source.parent
    for name in (".venv/bin/python", "launcher/tacto", "helper/Tacto Helper.app", "skill/install.sh"):
        if not (root / name).exists():
            raise SystemExit(f"Build the native checkout first: missing {name}")
    if output.exists() or output.is_symlink():
        raise SystemExit(f"Refusing to replace existing output: {output}")
    if not output.parent.is_dir() or output.suffix != ".app":
        raise SystemExit("Output must be a new .app in an existing directory")
    identity = os.environ.get("TACTO_SIGN_IDENTITY") or os.environ.get("NLC_SIGN_IDENTITY")
    if not identity:
        listing = subprocess.check_output(["security", "find-identity", "-p", "codesigning", "-v"], text=True)
        identity = next((m.group(1) for line in listing.splitlines()
                         if "CSSMERR" not in line and (m := re.search(r'"([^"]+)"', line))), None)
    if not identity:
        raise SystemExit("No signing identity; set TACTO_SIGN_IDENTITY")
    with tempfile.TemporaryDirectory(prefix="tacto-setup-build-", dir=output.parent) as temporary:
        app = Path(temporary) / output.name
        contents = app / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Resources").mkdir()
        for name in ("tacto-app.icns", "tacto-logo.svg", "tacto-menu-template.svg"):
            shutil.copy2(root / "docs/assets" / name, contents / "Resources" / name)
        executable = contents / "MacOS/TactoSetup"
        subprocess.run(["swiftc", str(source / "main.swift"), str(source / "PermissionState.swift"),
                        "-o", str(executable)], check=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleDisplayName": "Tacto", "CFBundleName": "Tacto",
            "CFBundleIconFile": "tacto-app.icns",
            "CFBundleExecutable": "TactoSetup", "CFBundleIdentifier": "ai.callabo.nlc.setup.preview",
            "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "0.1.0",
            "LSMinimumSystemVersion": "13.0",
        }))
        (contents / "Resources/runtime.json").write_text(json.dumps({"root": str(root)}))
        for target in (executable, app):
            subprocess.run(["codesign", "--force", "--sign", identity, "--identifier",
                            "ai.callabo.nlc.setup.preview", "--options", "runtime",
                            "--timestamp=none", str(target)], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        shutil.move(str(app), str(output))
    print(f"Built and verified: {output}\nNot launched; requires checkout at {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output.absolute())
