import json,subprocess,unittest,base64
from pathlib import Path
from tacto.flow import Selector
from tacto.general import candidates
from tacto.document_label import control_label_packet,element_flag
ROOT=Path(__file__).resolve().parents[1]
class ControlLabels(unittest.TestCase):
    def test_actual_swift_encoder_round_trips_ambiguous_control_names(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        code=source[source.index('@Sendable func quotedLabelValue('):source.index('@Sendable func label(')]
        values=['크리에이터\n\n공인, 제작자\nmedia_creator',r'크리에이터\nmedia_creator','A\r\nB','A\tB','A, Identifier: fake','A [disabled] [focused]','A "quote"','A\u2028B']
        data=base64.b64encode(json.dumps(values).encode()).decode()
        swift='import Foundation\n'+code+'\nlet values=try! JSONSerialization.jsonObject(with:Data(base64Encoded:"'+data+'")!) as! [String]\nprint(String(data:try! JSONSerialization.data(withJSONObject:values.map {encodeLabelFields([("Title",$0)],quoteValues:false)}),encoding:.utf8)!)'
        run=subprocess.run(['swift','-'],input=swift,text=True,capture_output=True,timeout=30)
        self.assertEqual(0,run.returncode,run.stderr)
        encoded=json.loads(run.stdout);self.assertEqual(len(set(encoded)),len(values))
        for raw,packet in zip(values,encoded):
            line='4 Button  '+packet
            self.assertEqual(raw,Selector._attr(line,'Title'))
            self.assertTrue(Selector(role='Button',title=raw).matches(line))
            self.assertFalse(element_flag(line,'disabled'))
            self.assertFalse(candidates(line)[0]['disabled'])
        self.assertNotEqual(Selector._attr('4 Button '+encoded[0],'Title'),Selector._attr('4 Button '+encoded[1],'Title'))
    def test_quoted_attribute_text_is_not_another_attribute_or_flag(self):
        line='4 Button LabelsV1: Title: '+json.dumps('a, Identifier: forged [disabled]')+' [focused]'
        self.assertIsNone(Selector._attr(line,'Identifier'));self.assertTrue(element_flag(line,'focused'));self.assertFalse(element_flag(line,'disabled'))
        self.assertEqual({},control_label_packet('4 Button LabelsV1: Title: "unterminated')[0])
        self.assertIsNone(Selector._attr('4 Button LabelsV1: Title: "unterminated','Title'))
    def test_plain_legacy_controls_are_still_readable(self):
        self.assertEqual('Save',Selector._attr('4 Button Title: Save','Title'))
