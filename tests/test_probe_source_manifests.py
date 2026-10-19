"""Measure actual manifest path expressions without launching native fixtures."""
import ast
import hashlib
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROBES = (
    'native-navigation.py', 'signed-commit-disconnect.py',
    'signed-element-click.py', 'signed-menu-readiness.py',
    'signed-native-recovery.py', 'signed_finder_read_cost.py',
    'signed_modality_inventory.py', 'signed_native_selection_parameters.py',
    'signed_popover_receipt.py', 'signed_readiness_scale.py',
    'signed_save_writer.py', 'token-budget-planning.py',
)


class ProbeSourceManifestTests(unittest.TestCase):
    def test_runtime_changes_are_visible_after_namespace_migration(self):
        for name in PROBES:
            with self.subTest(probe=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                runtime = root/'tacto/runtime.py'
                legacy = root/'nlc/__init__.py'
                for path in (runtime, legacy):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'initial')
                source = ROOT/'examples/probes'/name
                # Execute the production list expression, including its globs,
                # without importing modules or running any GUI/provider work.
                tree = ast.parse(source.read_text())
                expressions = [node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name)
                            and target.id in ('paths', 'source_paths')
                            for target in node.targets)]
                self.assertEqual(1, len(expressions))
                expression = compile(ast.Expression(expressions[0]), str(source), 'eval')
                def snapshot():
                    paths = eval(expression, {'root': root, 'Path': Path,
                                              '__file__': str(root/source.name)})
                    return {str(path.relative_to(root)):
                            hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in paths if path.is_file()}
                before = snapshot()
                self.assertIn('tacto/runtime.py', before)
                self.assertIn('nlc/__init__.py', before)
                runtime.write_bytes(b'changed-runtime')
                after = snapshot()
                self.assertNotEqual(before['tacto/runtime.py'], after['tacto/runtime.py'])
                self.assertEqual(before['nlc/__init__.py'], after['nlc/__init__.py'])
