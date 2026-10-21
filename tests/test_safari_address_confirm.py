"""Native address confirmation accepts only Safari chrome, not web content."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(sys.platform=='darwin','native AX types')
class SafariAddressConfirmTests(unittest.TestCase):
    def test_native_field_eligibility_and_changed_or_unreadable_context(self):
        text=(ROOT/'helper/WindowInput.swift').read_text()
        function=text.split('func nativeSafariAddressConfirmReceipt(',1)[1].split('// MARK: - Return into the Go to Folder',1)[0]
        function='func nativeSafariAddressConfirmReceipt('+function
        harness=r'''
import Foundation
import ApplicationServices
enum NativeWindowInputError:Error { case refused(String) }
typealias AXRead = (AXUIElement,String) throws -> (AXError,CFTypeRef?)
func axActionFailureEffect(_ status:AXError) -> String { "unknown" }
FUNCTION
for status:AXError in [.success,.cannotComplete] {
    let receipt=nativeSafariAddressConfirmReceipt(status,obsID:"test")
    assert(receipt["type"] as? String == "acted")
    assert(receipt["effect"] as? String == "unknown")
    assert(receipt["reobserve_required"] as? Bool == true)
    assert(receipt["posted_events"] as? Int == 0)
}
assert(nativeSafariAddressConfirmReceipt(.actionUnsupported,obsID:"test")["type"] as? String == "error")
let field=AXUIElementCreateApplication(100), group=AXUIElementCreateApplication(101), window=AXUIElementCreateApplication(102)
func check(_ changes:[String:Any]=[:], actions:[String]=["AXConfirm"], fault:String?=nil) -> Bool {
    let values:[String:Any] = ["AXRole":"AXTextField","AXIdentifier":"WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD",
                              "AXEnabled":true,"AXValue":"https://example.com/","AXParent":group]
    let input=values.merging(changes){_,new in new}
    let read:AXRead={node,key in
        if key == fault { return (.cannotComplete,nil) }
        let value:Any?
        if CFEqual(node,field) { value=input[key] }
        else if CFEqual(node,group) { value=["AXRole":"AXGroup","AXParent":window][key] }
        else { value=["AXRole":changes["parentRole"] ?? "AXWindow","AXSubrole":changes["parentSubrole"] ?? "AXStandardWindow"][key] }
        return value.map{(.success,$0 as CFTypeRef)} ?? (.attributeUnsupported,nil)
    }
    return (try? nativeSafariAddressConfirmEligible(field,read:read,actions:{_ in actions})) ?? false
}
assert(check())
for bad:[String:Any] in [["AXRole":"AXButton"],["AXIdentifier":"fake"],["AXEnabled":false],
    ["AXEnabled":1],["AXValue":"javascript:alert(1)"],["AXValue":"file:///tmp/x"],
    ["AXValue":"search query"],["parentRole":"AXWebArea"],["parentRole":"AXSheet"],
    ["parentRole":"AXDialog"],["parentSubrole":"AXDialog"]] { assert(!check(bad)) }
assert(!check(actions:["AXPress"]))
assert(!check(fault:"AXParent"))
assert(!check(fault:"AXValue"))
print("native-address-confirm-pass")
'''.replace('FUNCTION',function)
        with tempfile.TemporaryDirectory(prefix='tacto-safari-confirm-') as folder:
            source=Path(folder)/'main.swift';source.write_text(harness)
            result=subprocess.run(['swift',str(source)],capture_output=True,text=True,timeout=60)
            self.assertEqual(0,result.returncode,result.stderr)
            self.assertIn('native-address-confirm-pass',result.stdout)

class SafariValuePinTests(unittest.TestCase):
    def test_safari_return_pins_the_observed_url_when_capability_is_present(self):
        from dataclasses import replace
        from unittest.mock import patch
        from tests.test_bound_return import App,CAPS,step
        from tacto.flow import Flow,Selector,Postcondition,replay
        from tacto.contract import ContextBinding
        from tacto.flow import pin_params
        url="https://example.com/expected"
        class Browser(App):
            def observe(self,*args,**kwargs):
                return {"type":"observation","obs_id":"o","tree":
                        "0 Window Title: Browser\n  1 TextField Identifier: WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD, Value: "+url+"\n  2 StaticText Identifier: navigation-status, Value: pending"}
        for extra in (set(),{"press_key.safari_address_confirm"}):
            app=Browser();app.capabilities=CAPS|extra|{"press_key.return_in_go_to_folder"}
            st=step(effects={"transmit"},selector=Selector(identifier="WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD"),
                    binding=ContextBinding(bundle_id="com.apple.Safari",identifier="WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD",
                                           operation="press_key",params_digest=pin_params({"key":"Return"}),
                                           destination_identity=url,destination_identity_from={"identifier":"WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD","attribute":"Value"}),
                    postcondition=Postcondition(field=Selector(identifier="navigation-status"),attribute="Value",equals="done"))
            with patch("tacto.flow._approved",return_value=True):
                replay(app,Flow("navigate","com.apple.Safari",[st],schema=2))
            self.assertEqual(1,len(app.calls))
            claim=app.calls[0][1]["expect"]
            self.assertEqual("WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD",claim["label"])
            if extra:self.assertEqual(url,claim["value"])
            else:self.assertNotIn("value",claim)

    def test_a_drifted_observed_url_is_refused_before_any_input(self):
        """Safari may show another URL by the time Return is pressed. The bound
        destination is compared on the host first: nothing is sent at all."""
        from unittest.mock import patch
        from tests.test_bound_return import App,CAPS,step
        from tacto.flow import Flow,Selector,Postcondition,replay,pin_params
        from tacto.contract import ContextBinding
        def run(bound,shown):
            class Browser(App):
                def observe(self,*args,**kwargs):
                    return {"type":"observation","obs_id":"o","tree":
                            "0 Window Title: Browser\n  1 TextField Identifier: WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD, Value: "+shown+"\n  2 StaticText Identifier: navigation-status, Value: pending"}
            app=Browser();app.capabilities=CAPS|{"press_key.safari_address_confirm"}
            st=step(effects={"transmit"},selector=Selector(identifier="WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD"),
                    binding=ContextBinding(bundle_id="com.apple.Safari",identifier="WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD",
                                           operation="press_key",params_digest=pin_params({"key":"Return"}),
                                           destination_identity=bound,destination_identity_from={"identifier":"WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD","attribute":"Value"}),
                    postcondition=Postcondition(field=Selector(identifier="navigation-status"),attribute="Value",equals="done"))
            with patch("tacto.flow._approved",return_value=True):
                replay(app,Flow("navigate","com.apple.Safari",[st],schema=2))
            return [c[1].get("expect",{}) for c in app.calls]
        url="https://example.com/expected"
        self.assertEqual([],run(url,url+"-login"))
        self.assertEqual([url],[c.get("value") for c in run(url,url)])
        # Not a full HTTP(S) destination: no pin, so the semantic route is not chosen.
        self.assertTrue(all("value" not in c for c in run("example.com","example.com")))
