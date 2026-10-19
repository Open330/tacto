"""Production geometry hint respects the existing normalized scrollbar units."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('swift'), 'Swift required')
class ComboScrollHintSwiftTests(unittest.TestCase):
    def test_distance_hints_are_bounded_and_invalid_geometry_is_not_a_hint(self):
        source = (ROOT/'helper/TactoHelper.swift').read_text()
        function = source[source.index('func comboBoxScrollPagesHint('):]
        function = function[:function.index('\n}\n')+3]
        script = 'import Foundation\nimport CoreGraphics\n'+function+r'''
let viewport=CGRect(x:0,y:0,width:200,height:154)
func row(_ y:CGFloat)->CGRect { CGRect(x:0,y:y,width:200,height:18) }
precondition(comboBoxScrollPagesHint(row(2340),viewport,2358)==10)
precondition(comboBoxScrollPagesHint(row(336),viewport,1154)==2)
precondition(comboBoxScrollPagesHint(row(-200),viewport,1154)==2)
precondition(comboBoxScrollPagesHint(row(137),viewport,1154)==1)
precondition(comboBoxScrollPagesHint(row(100000),viewport,1154)==10)
precondition(comboBoxScrollPagesHint(row(20),viewport,1154)==nil)
precondition(comboBoxScrollPagesHint(row(0),viewport,154)==nil)
precondition(comboBoxScrollPagesHint(row(2340),viewport,.infinity)==nil)
precondition(comboBoxScrollPagesHint(row(2340),viewport,.nan)==nil)
precondition(comboBoxScrollPagesHint(CGRect(x:-1,y:2340,width:200,height:18),viewport,2358)==nil)
precondition(comboBoxScrollPagesHint(CGRect(x:0,y:2340,width:201,height:18),viewport,2358)==nil)
precondition(comboBoxScrollPagesHint(CGRect(x:0,y:2340,width:200,height:155),viewport,2358)==nil)
precondition(comboBoxScrollPagesHint(CGRect(x:0,y:2340,width:0,height:18),viewport,2358)==nil)
let huge=CGRect(x:1e308,y:2340,width:1e308,height:18)
precondition(comboBoxScrollPagesHint(huge,CGRect(x:1e308,y:0,width:1e308,height:154),2358)==nil)
print("OK")
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'main.swift'
            path.write_text(script)
            result = subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=45)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual('OK',result.stdout.strip())
