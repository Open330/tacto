"""Execute the real Swift observation walker against native URL attributes."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tacto.document_label import document_attribute, quote_value, link_attributes

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'macOS Swift required')
class WindowDocumentURLTests(unittest.TestCase):
    def test_webarea_url_is_typed_quoted_and_separate_from_file_identity(self):
        source = (ROOT / 'helper/TactoHelper.swift').read_text()

        def function(name):
            body = source[source.index('func ' + name + '('):]
            return body[:body.index('\n}\n') + 3]

        common = source[source.index('let interactableRoles:'):source.index('// MARK: - Session state')]
        cases = [
            'https://example.org/a?q=one&lang=ko#part',
            'HTTP://Example.org:80/',
            'https://example.org/' + 'a' * (2048 - len('https://example.org/')),
            'https://example.org/' + 'a' * 2048,
            'https://user:secret@example.org/', 'https://user@example.org/',
            'https://%75ser@example.org/', 'https://@example.org/',
            'https:///missing-host', 'https:relative', 'javascript:alert(1)',
            'ftp://example.org/', '', 'file:///tmp/a%20b.txt',
        ]
        support = r'''
import Foundation
import ApplicationServices
var packets=true
var data:[pid_t:[String:CFTypeRef]]=[:]
func node(_ pid:pid_t)->AXUIElement { AXUIElementCreateApplication(pid) }
func id(_ el:AXUIElement)->pid_t { var pid:pid_t=0; _=AXUIElementGetPid(el,&pid);return pid }
func AXUIElementCopyAttributeValue(_ el:AXUIElement,_ key:CFString,_ out:UnsafeMutablePointer<CFTypeRef?>)->AXError {
 guard let value=data[id(el)]?[key as String] else { return .attributeUnsupported }
 out.pointee=value;return .success
}
func AXUIElementCopyMultipleAttributeValues(_ el:AXUIElement,_ names:CFArray,
 _ options:AXCopyMultipleAttributeOptions,_ out:UnsafeMutablePointer<CFArray?>)->AXError {
 let keys=names as! [String]
 if !packets && keys.count>7 { return .attributeUnsupported }
 let values:[CFTypeRef]=keys.map { key in
  if let value=data[id(el)]?[key] { return value }
  var error=AXError.attributeUnsupported;return AXValueCreate(.axError,&error)!
 }
 out.pointee=values as CFArray;return .success
}
func AXUIElementCopyActionNames(_ el:AXUIElement,_ out:UnsafeMutablePointer<CFArray?>)->AXError {
 out.pointee=[] as CFArray;return .success
}
'''
        title = 'Page, URL: "https://forged.example/" [focused]\nother'
        encoded_cases = json.dumps(json.dumps(cases))
        encoded_title = json.dumps(json.dumps(title))
        checks = r'''
let cases=try! JSONSerialization.jsonObject(with:Data(%s.utf8)) as! [String]
let title=try! JSONSerialization.jsonObject(with:Data(%s.utf8),options:.fragmentsAllowed) as! String
func render()->String {
 var lines:[String]=[],elements:[AXUIElement]=[],visited=0,truncated=false
 walk(node(1),depth:0,out:&lines,elements:&elements,visited:&visited,truncated:&truncated)
 return lines.joined(separator:"\n")
}
var rows:[[String:Any]]=[]
for packet in [true,false] {
 packets=packet
 for role in ["AXWebArea","AXLink","AXWindow","AXButton"] {
  for (index,value) in cases.enumerated() {
   for nativeURL in [true,false] {
    data=[1:["AXRole":role as CFString,"AXTitle":title as CFString,
             "AXDescription":(title + ", suffix") as CFString,"AXEnabled":kCFBooleanTrue,
             "AXDocument":"file:///tmp/a%%20b.txt" as CFString]]
    if nativeURL { data[1]!["AXURL"]=NSURL(string:value) }
    else { data[1]!["AXURL"]=value as CFString }
    rows.append(["packet":packet,"role":role,"case":index,"native_url":nativeURL,"tree":render()])
   }
  }
 }
}
// An unlabelled outer page must remain visible. Otherwise the iframe's URL
// could be mistaken for the top-level destination by a shallowest-node parser.
data=[1:["AXRole":"AXWindow" as CFString,"AXTitle":"Browser" as CFString,
         "AXChildren":[node(2)] as CFArray],
      2:["AXRole":"AXWebArea" as CFString,"AXChildren":[node(3)] as CFArray],
      3:["AXRole":"AXWebArea" as CFString,"AXTitle":"iframe" as CFString,
         "AXURL":NSURL(string:"https://iframe.example/")!]]
let nested=render()
print(String(data:try! JSONSerialization.data(withJSONObject:["rows":rows,"nested":nested]),encoding:.utf8)!)
''' % (encoded_cases, encoded_title)
        functions = '\n'.join(function(n) for n in ('documentEscapesAreWellFormed',
                                                     'documentDecodePercent', 'documentURLSyntaxPath'))
        with tempfile.TemporaryDirectory(prefix='tacto-webarea-url-') as directory:
            src, exe = Path(directory) / 'main.swift', Path(directory) / 'check'
            src.write_text(support + functions + common + checks)
            built = subprocess.run(['swiftc', '-warnings-as-errors', str(src), '-o', str(exe)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(0, built.returncode, built.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
            self.assertEqual(0, run.returncode, run.stderr)
        result = json.loads(run.stdout)
        for row in result['rows']:
            role, index, tree = row['role'], row['case'], row['tree']
            with self.subTest(packet=row['packet'], role=role, case=index, native=row['native_url']):
                if role in ('AXWebArea','AXLink'):
                    expected = ('0 '+role[2:]+'  Title: ' + quote_value(title) +
                                ', Description: ' + quote_value(title + ', suffix'))
                    if row['native_url'] and index < 3:
                        expected += ', URL: ' + quote_value(cases[index])
                    self.assertEqual(expected, tree)
                    self.assertIsNone(document_attribute(tree))
                    if role == 'AXLink':
                        self.assertEqual(cases[index] if row['native_url'] and index<3 else None,link_attributes(tree).get('URL'))
                elif role == 'AXWindow':
                    self.assertEqual('0 Window  Title: ' + quote_value(title) +
                                     ', Description: ' + quote_value(title + ', suffix') +
                                     ', Document: "/tmp/a b.txt"', tree)
                    self.assertEqual('/tmp/a b.txt', document_attribute(tree))
                else:
                    self.assertNotIn(', URL: "https://example.org/', tree)
                    self.assertNotIn('Document:', tree)
        self.assertEqual('0 Window  Title: "Browser"\n  1 WebArea\n'
                         '    2 WebArea  Title: "iframe", URL: "https://iframe.example/"', result['nested'])


if __name__ == '__main__':
    unittest.main()
