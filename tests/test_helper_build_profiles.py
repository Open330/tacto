"""Default signed helper must not include experimental foreground input."""
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]

class HelperBuildProfiles(unittest.TestCase):
    def test_experimental_sources_are_only_added_in_explicit_build_branch(self):
        source=(ROOT/'helper/build.sh').read_text()
        self.assertIn('${TACTO_ENABLE_EXPERIMENTAL_FOREGROUND:-0}',source)
        branch=source[source.index('  1)'):source.index('\n    ;;',source.index('  1)'))]
        self.assertIn('-D TACTO_EXPERIMENTAL_FOREGROUND',branch)
        self.assertIn('SWIFT_ARGS+=',branch)
        initial=next(line for line in source.splitlines() if line.startswith('SWIFT_ARGS='))
        self.assertNotIn('VroidDrag',initial)
        self.assertNotIn('experimental/',initial)

    def test_default_helper_source_has_no_unguarded_foreground_dispatch(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        enabled=True;default=[]
        for line in source.splitlines():
            if line=='#if TACTO_EXPERIMENTAL_FOREGROUND':enabled=False
            elif line=='#else' and not enabled:enabled=True
            elif line=='#endif':enabled=True
            elif enabled:default.append(line)
        code='\n'.join(default)
        self.assertNotIn('case "vroid_drag":',code)
        self.assertNotIn('case "vroid_focus":',code)
        self.assertNotIn('"action.vroid_drag"',code)
        self.assertNotIn('dispatchVroidDrag(',code)

    def test_default_built_helper_contains_no_foreground_drag_implementation(self):
        path=ROOT/'helper/nlc-helper'
        if not path.exists():self.skipTest('helper not built')
        result=subprocess.run(['nm',str(path)],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertNotIn('dispatchVroidDrag',result.stdout)
        self.assertNotIn('dispatchVroidFocus',result.stdout)
