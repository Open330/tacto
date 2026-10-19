import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from tacto.vroid_drag import main

TITLE="VRoid Studio 2.14.0 - Trial.vroid*"
class VroidDragTests(unittest.TestCase):
    def test_ordered_waypoints_map_from_capture_and_dispatch_once(self):
        d=self.driver();cap=Mock();cap.viewport={"width":3000,"height":1800};cap.window_frame=(100,200,1500,900)
        with tempfile.TemporaryDirectory() as root,patch("tacto.vroid_drag.capture_window",return_value=cap):
            main(self.args(Path(root)/"run")+["--via-pixels","1610","980","--via-pixels","1620","960"],d)
        self.assertEqual(1,d.act.call_count)
        self.assertEqual([{"x":805.0,"y":490.0},{"x":810.0,"y":480.0}],d.act.call_args.args[2]["via"])

    def test_invalid_waypoints_never_dispatch(self):
        for extra in (["--via-pixels","nan","900"], ["--via-pixels","1600","900"]*13):
            d=self.driver()
            with tempfile.TemporaryDirectory() as root,self.assertRaises(SystemExit):
                main(self.args(Path(root)/"run")+extra,d)
            d.observe.assert_not_called();d.act.assert_not_called()

    def driver(self):
        d=Mock();d.capabilities={"action.vroid_drag"}
        d.observe.return_value={"type":"observation","obs_id":"owned","tree":'0 Window  Title: "'+TITLE+'" [focused]'}
        d.act.return_value={"type":"error","effect":"unknown","error":"partial"}
        return d
    def args(self, output):
        return ["--helper","--yes","--title",TITLE,"--from-pixels","1600","1000","--to-pixels","1640","940","--output",str(output)]
    def test_actual_capture_pixels_map_to_points_and_partial_dispatch_is_not_retried(self):
        d=self.driver();cap=Mock();cap.viewport={"width":3000,"height":1800};cap.window_frame=(100,200,1500,900)
        with tempfile.TemporaryDirectory() as root,patch("tacto.vroid_drag.capture_window",return_value=cap):
            self.assertEqual(1,main(self.args(Path(root)/"run"),d))
        self.assertEqual(1,d.act.call_count)
        payload=d.act.call_args.args[2]
        self.assertEqual({"x":800.0,"y":500.0},payload["from"])
        self.assertEqual({"x":820.0,"y":470.0},payload["to"])
        self.assertEqual(TITLE,payload["title"])
    def test_missing_capability_never_falls_back_to_click_or_direct_backend(self):
        d=self.driver();d.capabilities={"action.click_point"}
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(1,main(self.args(Path(root)/"run"),d))
        d.observe.assert_not_called();d.act.assert_not_called()
    def test_wrong_document_never_dispatches(self):
        d=self.driver();d.observe.return_value["tree"]='0 Window  Title: "Other.vroid" [focused]'
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(1,main(self.args(Path(root)/"run"),d))
        d.act.assert_not_called()
    def test_invalid_duration_or_absent_explicit_opt_in_never_observes(self):
        for extra in (["--duration","nan"],["--duration","3"]):
            d=self.driver()
            with tempfile.TemporaryDirectory() as root,self.assertRaises(SystemExit):
                main(self.args(Path(root)/"run")+extra,d)
            d.observe.assert_not_called();d.act.assert_not_called()
        d=self.driver()
        with tempfile.TemporaryDirectory() as root,self.assertRaises(SystemExit):
            main([a for a in self.args(Path(root)/"run") if a!="--yes"],d)
        d.observe.assert_not_called()

    def test_graph_rectangle_maps_without_expanding_canvas_default(self):
        d=self.driver();cap=Mock();cap.viewport={"width":3000,"height":1800};cap.window_frame=(0,0,1500,900)
        with tempfile.TemporaryDirectory() as root,patch("tacto.vroid_drag.capture_window",return_value=cap):
            main(self.args(Path(root)/"run")+["--graph-bounds-pixels","2576","850","400","172"],d)
        self.assertEqual({"x":1288.0,"y":425.0,"width":200.0,"height":86.0},d.act.call_args.args[2]["graph_bounds"])
