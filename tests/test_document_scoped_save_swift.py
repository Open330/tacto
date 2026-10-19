"""Run the native Save admission predicate on untrusted action shapes."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('swift'),'Swift required')
class ScopedSaveSwiftTests(unittest.TestCase):
    def test_only_canonical_bound_save_is_admitted(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        start=source.index('func documentScopedSaveAction(')
        function=source[start:source.index('\n}\n',start)+3]
        good={'name':'menu','path':['File',['Save','저장']],'focused_document':'/owned/노트.txt'}
        cases=[(good,'/owned/노트.txt',True),(good,'/owned/노트.txt',True),
            (good,None,False),(good,'/other/노트.txt',False),
            ({**good,'focused_document':'/owned/노\u200b트.txt'},'/owned/노트.txt',False),
            ({**good,'element_index':0},'/owned/노트.txt',False),
            ({**good,'path':['File',['Save As…','Save']]},'/owned/노트.txt',False),
            ({**good,'path':['File',['Save','저장'],'Close']},'/owned/노트.txt',False),
            ({**good,'name':'click'},'/owned/노트.txt',False),
            ({**good,'focused_document':True},'/owned/노트.txt',False),
            ({'name':'menu','path':good['path']},'/owned/노트.txt',False)]
        payload=json.dumps([{'action':a,'path':p} for a,p,_ in cases],ensure_ascii=False)
        code='import Foundation\n'+function+'\nlet data='+json.dumps(payload,ensure_ascii=False)+'''.data(using:.utf8)!
let cases=try! JSONSerialization.jsonObject(with:data) as! [[String:Any]]
let result=cases.map { documentScopedSaveAction($0["action"] as! [String:Any],selectedPath:$0["path"] as? String) }
print(String(data:try! JSONSerialization.data(withJSONObject:result),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='nlc-scoped-save-predicate-') as work:
            path=Path(work)/'main.swift';path.write_text(code)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual([expected for _,_,expected in cases],json.loads(result.stdout))


if __name__=='__main__':unittest.main()
