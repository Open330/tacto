"""`nlc` stays importable as an alias of `tacto`, without a second module copy.

Every check runs in a fresh interpreter so import order is real, not whatever
an earlier test happened to load.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(code, *args):
    result = subprocess.run([sys.executable, "-c", textwrap.dedent(code), *args],
                            cwd=ROOT, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


class LegacyNamespaceTests(unittest.TestCase):
    def test_both_import_orders_return_the_canonical_module(self):
        for first, second in (("tacto", "nlc"), ("nlc", "tacto")):
            with self.subTest(first=first):
                got = run(f"""
                    import importlib, json, sys
                    a = importlib.import_module("{first}.flow")
                    b = importlib.import_module("{second}.flow")
                    copies = {{id(m) for m in list(sys.modules.values())
                              if getattr(m, "__file__", None) == a.__file__}}
                    print(json.dumps({{"same": a is b, "name": b.__name__,
                                       "spec": b.__spec__.name, "copies": len(copies),
                                       "registered": sys.modules["nlc.flow"] is sys.modules["tacto.flow"]}}))
                """)
                self.assertEqual({"same": True, "name": "tacto.flow", "spec": "tacto.flow",
                                  "copies": 1, "registered": True}, got)

    def test_from_imports_and_classes_are_identical(self):
        got = run("""
            import json
            from nlc.flow import Selector as Old
            from tacto.flow import Selector as New
            from nlc import flow as old_module
            import nlc, tacto.flow
            print(json.dumps({"cls": Old is New, "module": old_module is tacto.flow,
                              "attr": nlc.flow is tacto.flow,
                              "class_module": Old.__module__}))
        """)
        self.assertEqual({"cls": True, "module": True, "attr": True,
                          "class_module": "tacto.flow"}, got)

    def test_patch_through_the_legacy_name_changes_the_canonical_module(self):
        got = run("""
            import json
            from unittest.mock import patch
            import tacto.helper
            before = tacto.helper.HELPER
            with patch("nlc.helper.HELPER", "/patched"):
                inside = tacto.helper.HELPER
            print(json.dumps({"inside": inside, "restored": tacto.helper.HELPER == before}))
        """)
        self.assertEqual({"inside": "/patched", "restored": True}, got)

    def test_legacy_import_loads_nothing_the_canonical_import_does_not(self):
        # The alias may add only its own names; it must not drag in GUI
        # frameworks or other submodules eagerly.
        probe = """
            import importlib, json, sys
            base = set(sys.modules)
            importlib.import_module(sys.argv[1])
            print(json.dumps(sorted(set(sys.modules) - base)))
        """
        canonical = set(run(probe, "tacto.flow"))
        legacy = set(run(probe, "nlc.flow"))
        self.assertEqual({"nlc", "nlc.flow"}, legacy - canonical)
        self.assertEqual(set(), canonical - legacy)
        bare = set(run(probe, "nlc"))
        self.assertEqual({"nlc", "tacto"}, {m for m in bare if m.split(".")[0] in ("nlc", "tacto")})
        self.assertFalse({"AppKit", "Quartz", "Foundation"} & bare)

    def test_missing_legacy_module_is_a_normal_import_error(self):
        got = run("""
            import json
            try:
                import nlc.no_such_module
            except ModuleNotFoundError as error:
                print(json.dumps({"name": error.name}))
        """)
        self.assertEqual({"name": "nlc.no_such_module"}, got)

    def test_module_entry_point_matches_the_canonical_one(self):
        legacy = subprocess.run([sys.executable, "-m", "nlc.cli", "--help"], cwd=ROOT,
                                capture_output=True, text=True, timeout=120)
        canonical = subprocess.run([sys.executable, "-m", "tacto.cli", "--help"], cwd=ROOT,
                                   capture_output=True, text=True, timeout=120)
        self.assertEqual(canonical.returncode, legacy.returncode, legacy.stderr)
        self.assertEqual(canonical.stdout, legacy.stdout)

    def test_installing_the_alias_twice_adds_one_finder(self):
        got = run("""
            import importlib, json, sys
            import nlc
            importlib.reload(nlc)
            print(json.dumps(sum(type(f).__name__ == "_AliasFinder" for f in sys.meta_path)))
        """)
        self.assertEqual(1, got)

    def test_foreign_tacto_package_is_refused_before_any_alias(self):
        # An unrelated distribution named `tacto` (PyPI has one) shadows ours.
        with tempfile.TemporaryDirectory() as folder:
            foreign = Path(folder) / "tacto"
            foreign.mkdir()
            (foreign / "__init__.py").write_text("")
            sentinel = Path(folder) / "foreign-flow-ran"
            (foreign / "flow.py").write_text(f"open({str(sentinel)!r}, 'w').close()\n")
            result = subprocess.run([sys.executable, "-c", textwrap.dedent(f"""
                import json, sys
                sys.path[:0] = [{folder!r}, {str(ROOT)!r}]
                errors = []
                for name in ("nlc", "nlc.flow"):
                    try:
                        __import__(name)
                    except ImportError as error:
                        errors.append(str(error))
                print(json.dumps({{
                    "errors": errors,
                    "finders": sum(bool(getattr(f, "_tacto_legacy_alias", False)) for f in sys.meta_path),
                    "nlc_loaded": "nlc" in sys.modules,
                    "foreign_flow_loaded": "tacto.flow" in sys.modules or "nlc.flow" in sys.modules}}))
            """)], cwd=folder, capture_output=True, text=True, timeout=120,
                env={**os.environ, "PYTHONPATH": ""})
            self.assertEqual(0, result.returncode, result.stderr)
            got = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(2, len(got["errors"]))
            for message in got["errors"]:
                self.assertIn("tacto-runtime", message)
                self.assertIn("isolated environment", message)
            self.assertEqual(0, got["finders"])
            self.assertFalse(got["nlc_loaded"])
            self.assertFalse(got["foreign_flow_loaded"])
            self.assertFalse(sentinel.exists())


if __name__ == "__main__":
    unittest.main()
