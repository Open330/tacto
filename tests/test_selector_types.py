"""Recipe selectors are type-checked when the recipe is read, not mid-run."""
import json
import unittest

from tacto.flow import Flow
from tacto.library import builders

SAFETY = {"effects": ["write_ephemeral"], "resource": {"object_type": "entry", "scope": "entry"},
          "reversibility": "reversible"}


def doc(**step):
    return {"schema": 2, "name": "p", "target": "pad",
            "steps": [{"action": "click", "selector": {"identifier": "one"}, "safety": SAFETY, **step}]}


class SelectorTypeTests(unittest.TestCase):
    def test_wrong_types_anywhere_are_refused_on_read(self):
        bad_fields = ({"identifier": 42}, {"role": ["Button"]}, {"title": ""}, {"identifier": []},
                      {"identifier": "one", "allow_nth": "0"}, {"identifier": "one", "allow_nth": -1},
                      {"identifier": "one", "in_focused_window": "yes"}, {"nearby": "x"}, {"allow_nth": 0},
                      "one")
        for bad in bad_fields:
            for where, step in (("selector", {"selector": bad}),
                                ("postcondition", {"postcondition": {"field": bad, "equals": "1"}}),
                                ("skip_if", {"skip_if": {"field": bad, "equals": "1"}}),
                                ("capture", {"capture": {"name": "v", "field": bad}})):
                with self.subTest(where=where, bad=bad), self.assertRaises(ValueError):
                    Flow.from_json(json.dumps(doc(**step)))

    def test_every_valid_shape_still_reads(self):
        for good in ({"identifier": ["AllClear", "Clear"]}, {"role": "TextField", "in_focused_window": True},
                     {"identifier": "display", "allow_nth": 0}, {"ancestor": "Sidebar", "title": "Docs"},
                     {"role": "Button", "subrole": "AXCloseButton"}):
            with self.subTest(good=good):
                Flow.from_json(json.dumps(doc(selector=good)))

    def test_every_built_in_recipe_round_trips(self):
        for name, build in builders().items():
            with self.subTest(name=name):
                flow = build()
                self.assertEqual(flow.to_json(), Flow.from_json(flow.to_json()).to_json())


if __name__ == "__main__":
    unittest.main()
