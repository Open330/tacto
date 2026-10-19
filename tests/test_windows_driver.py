"""WindowsDriver logic against a fake UI Automation tree (runs on any OS)."""
import unittest

from tacto.general import candidates
from tacto.flow import Selector, TargetMissing
from tacto.windows_driver import WindowsDriver


class Element:
    def __init__(self, control_type, name=None, *, automation_id=None, value=None, readonly=None,
                 enabled=True, offscreen=False, selected=None, patterns=(), children=(), pid=10):
        self.props = {"control_type": control_type, "name": name, "automation_id": automation_id,
                      "help": None, "enabled": enabled, "offscreen": offscreen, "pid": pid,
                      "value": value, "readonly": readonly, "selected": selected,
                      "runtime_id": (id(self),), "patterns": set(patterns),
                      "hwnd": id(self) if control_type == "Window" else None}
        self.kids = list(children)
        self.parent = None
        for child in self.kids:
            child.parent = self


class Backend:
    def __init__(self, windows, *, apps=None):
        self.windows = windows
        self.apps = apps or [{"pid": 10, "exe": "Notepad.exe", "name": "Notepad"}]
        self.calls = []
        self.modal = False
        self.front = None

    def front_window(self, hwnds):
        if isinstance(self.front, Exception):
            raise self.front
        return self.front if self.front in hwnds else None

    def modal_present(self, pid):
        return self.modal

    def processes(self):
        return self.apps

    def top_windows(self, pid):
        return [w for w in self.windows if w.props["pid"] == pid]

    def children(self, element):
        return element.kids

    def descendants(self, element):
        out = []
        for child in element.kids:
            out.append(child)
            out.extend(self.descendants(child))
        return out

    def props(self, element):
        return dict(element.props)

    def _record(self, name, element, *args):
        self.calls.append((name, element.props["name"], *args))

    def invoke(self, element):
        self._record("invoke", element)

    def toggle(self, element):
        self._record("toggle", element)

    def select(self, element):
        self._record("select", element)

    def expand(self, element):
        self._record("expand", element)

    def collapse(self, element):
        self._record("collapse", element)

    def set_value(self, element, text):
        self._record("set_value", element, text)
        element.props["value"] = text

    def scroll_container(self, element):
        while element is not None and "scroll" not in element.props["patterns"]:
            element = element.parent
        return element

    def scroll(self, element, direction, pages):
        self._record("scroll", element, direction, pages)


def notepad():
    save = Element("Button", "Save", automation_id="save", patterns={"invoke"})
    custom = Element("Custom", "Canvas")
    wrap = Element("CheckBox", "Word wrap", patterns={"toggle"}, selected=None)
    edit = Element("Edit", "Text editor", automation_id="body", value="hello", readonly=False,
                   patterns={"value"})
    status = Element("Text", "Ln 1, Col 1")
    hidden = Element("Button", "Hidden", offscreen=True, patterns={"invoke"})
    disabled = Element("Button", "Print", enabled=False, patterns={"invoke"})
    options = [Element("ListItem", label, patterns={"selection_item"}) for label in ("UTF-8", "ANSI")]
    encoding = Element("ComboBox", "Encoding", patterns={"expand_collapse"}, children=options)
    pane = Element("Pane", None, patterns={"scroll"},
                   children=[save, custom, wrap, edit, status, hidden, disabled, encoding])
    window = Element("Window", "Untitled - Notepad", children=[pane])
    return window, {e.props["name"]: e for e in (save, custom, wrap, edit, status, hidden,
                                                    disabled, encoding, pane, *options) if e.props["name"]}


class WindowsDriverTests(unittest.TestCase):
    def setUp(self):
        self.window, self.named = notepad()
        self.backend = Backend([self.window])
        self.driver = WindowsDriver(self.backend)

    def observe(self):
        obs = self.driver.observe("notepad")
        self.assertEqual("observation", obs["type"])
        return obs

    def index(self, obs, **selector):
        return Selector(**selector).resolve(obs["tree"])[0]

    def act(self, obs, name, **action):
        return self.driver.act(obs["obs_id"], "notepad", {"name": name, **action})

    def test_front_window_is_marked_for_in_focused_window_selectors(self):
        # Two identical documents: only the app's front window may be meant.
        second, named = notepad()
        self.backend.windows.append(second)
        field = dict(role="TextField", identifier="body", in_focused_window=True)
        self.backend.front = second.props["hwnd"]
        obs = self.observe()
        self.assertEqual(1, obs["tree"].count("[focused]"))
        index = self.index(obs, **field)
        self.act(obs, "set_value", element_index=index, value="x")
        self.assertEqual("x", named["Text editor"].props["value"])
        self.assertEqual("hello", self.named["Text editor"].props["value"])
        # Unknown or unreadable front window: nothing is marked, so the
        # selector finds nothing instead of guessing.
        for front in (None, OSError("enum failed")):
            self.backend.front = front
            with self.subTest(front=front):
                obs = self.observe()
                self.assertNotIn("[focused]", obs["tree"])
                with self.assertRaises(TargetMissing):
                    Selector(**field).resolve(obs["tree"])

    def test_packaged_app_content_acts_but_its_frame_host_title_bar_does_not(self):
        # UWP: the frame window and its title bar belong to the frame host
        # (pid 20); only the content inside belongs to the app (pid 30).
        equals = Element("Button", "Equals", automation_id="equalButton", patterns={"invoke"}, pid=30)
        content = Element("Window", "Calculator", children=[equals], pid=30)
        close = Element("Button", "Close Calculator", automation_id="Close", patterns={"invoke"}, pid=20)
        frame = Element("Window", "Calculator", children=[Element("TitleBar", None, children=[close], pid=20),
                                                          content], pid=20)
        backend = Backend([frame], apps=[{"pid": 30, "exe": "CalculatorApp.exe", "name": "CalculatorApp"}])
        backend.top_windows = lambda pid: [frame] if pid == 30 else []
        driver = WindowsDriver(backend)
        obs = driver.observe("calculatorapp")
        self.assertEqual("observation", obs["type"])
        act = lambda identifier: driver.act(obs["obs_id"], "calculatorapp", {
            "name": "click", "element_index": Selector(identifier=identifier).resolve(obs["tree"])[0]})
        self.assertEqual("not_started", act("Close")["effect"])
        self.assertEqual([], backend.calls)
        self.assertNotEqual("not_started", act("equalButton").get("effect"))
        self.assertEqual([("invoke", "Equals")], backend.calls)

    def test_a_failing_uia_query_is_reported_not_raised(self):
        self.backend.top_windows = lambda pid: (_ for _ in ()).throw(OSError("provider busy"))
        result = self.driver.observe("notepad")
        self.assertEqual(("error", "observation_unavailable"), (result["type"], result["error"]))
        self.assertNotIn("provider busy", str(result))

    def test_menu_popup_listed_twice_is_shown_once_inside_its_window(self):
        # Windows 11 XAML menus: the popup host is its own top-level window
        # and also a child of the app window in UIA.
        save_as = Element("MenuItem", "Save as", patterns={"invoke"})
        popup = Element("Pane", "PopupHost", children=[Element("Window", "Popup", children=[save_as])])
        self.window.kids.append(popup)
        self.backend.windows.insert(0, popup)  # in front, as EnumWindows lists it
        self.backend.front = self.window.props["hwnd"]
        obs = self.observe()
        self.assertEqual(1, obs["tree"].count("Save as"))
        self.assertTrue(obs["tree"].startswith('0 Window  Title: "Untitled - Notepad" [focused]'), obs["tree"])
        index = self.index(obs, title="Save as", in_focused_window=True)
        self.assertEqual("acted", self.act(obs, "click", element_index=index)["type"])

    def test_tree_uses_engine_roles_and_attributes(self):
        tree = self.observe()["tree"]
        self.assertIn('Window  Title: "Untitled - Notepad"', tree)
        self.assertIn("Button  Title: Save, Identifier: save", tree)
        self.assertIn("TextField  Title: Text editor, Value: hello, Identifier: body", tree)
        self.assertIn("StaticText  Title: Ln 1, Col 1", tree)
        self.assertIn("Button  Title: Print [disabled]", tree)
        self.assertNotIn("Hidden", tree)
        roles = {c["role"] for c in candidates(tree)}
        self.assertTrue({"Button", "TextField", "CheckBox", "StaticText"} <= roles)

    def test_target_resolution_is_exact_and_unambiguous(self):
        self.assertEqual(10, self.driver._resolve("notepad"))
        self.assertEqual(10, self.driver._resolve("NOTEPAD.EXE"))
        self.assertEqual(10, self.driver._resolve("pid:10"))
        self.assertIsNone(self.driver._resolve("note"))
        self.backend.apps.append({"pid": 11, "exe": "notepad.exe", "name": "Notepad"})
        self.backend.windows.append(Element("Window", "Second", pid=11))
        self.assertIsNone(self.driver._resolve("notepad"))
        self.assertEqual("target_not_found", self.driver.observe("notepad")["error"])
        self.assertEqual("win:notepad.exe", self.driver.identity("Notepad"))

    def test_click_uses_patterns_in_order_and_never_synthesizes_input(self):
        obs = self.observe()
        result = self.act(obs, "click", element_index=self.index(obs, title="Save"))
        self.assertEqual(("acted", "uia_invoke"), (result["type"], result["input_route"]))
        # A synchronous pattern call is not an unverified dispatch (engine contract).
        self.assertNotIn("unverified", result)
        obs = self.observe()
        self.assertEqual("uia_toggle", self.act(obs, "click", element_index=self.index(obs, title="Word wrap"))["input_route"])
        self.assertEqual([("invoke", "Save"), ("toggle", "Word wrap")], self.backend.calls)

    def test_elements_without_patterns_are_refused_before_any_call(self):
        self.window.kids[0].kids[1].props["control_type"] = "Button"
        obs = self.observe()
        result = self.act(obs, "click", element_index=self.index(obs, title="Canvas"))
        self.assertEqual(("error", "not_started", True), (result["type"], result["effect"], result["needs_pointer"]))
        disabled = self.act(obs, "click", element_index=self.index(obs, title="Print"))
        self.assertEqual("not_started", disabled["effect"])
        for name in ("press_key", "type_text"):
            refused = self.act(obs, name, element_index=0, key="Tab", text="x")
            self.assertEqual(("not_started", True), (refused["effect"], refused["needs_foreground"]))
        self.assertEqual([], self.backend.calls)

    def test_set_value_respects_read_only_and_value_type(self):
        obs = self.observe()
        index = self.index(obs, identifier="body")
        self.assertEqual("acted", self.act(obs, "set_value", element_index=index, value="draft")["type"])
        self.assertEqual("draft", self.named["Text editor"].props["value"])
        self.named["Text editor"].props["readonly"] = True
        obs = self.observe()
        self.assertIn("TextField [readonly]", obs["tree"])
        self.assertEqual("not_started", self.act(obs, "set_value", element_index=index, value="x")["effect"])
        self.assertEqual("not_started", self.act(obs, "set_value", element_index=index, value=3)["effect"])

    def test_stale_or_replaced_elements_are_never_addressed(self):
        obs = self.observe()
        index = self.index(obs, title="Save")
        newer = self.observe()
        self.assertEqual("stale_observation", self.act(obs, "click", element_index=index)["error"])
        self.named["Save"].props["runtime_id"] = ("replaced",)
        changed = self.act(newer, "click", element_index=index)
        self.assertEqual(("not_started", "target changed since observation"), (changed["effect"], changed["detail"]))
        out_of_range = self.act(newer, "click", element_index=999)
        self.assertEqual("not_started", out_of_range["effect"])
        self.assertEqual([], self.backend.calls)

    def test_select_option_is_exact_and_unknown_after_opening(self):
        obs = self.observe()
        index = self.index(obs, title="Encoding")
        result = self.act(obs, "select_option", element_index=index, option_label="ANSI")
        self.assertEqual("acted", result["type"])
        self.assertEqual([("expand", "Encoding"), ("select", "ANSI"), ("collapse", "Encoding")], self.backend.calls)
        self.backend.calls.clear()
        missing = self.act(self.observe(), "select_option", element_index=index, option_label="UTF-16")
        self.assertEqual("unknown", missing["effect"])
        self.assertEqual([("expand", "Encoding"), ("collapse", "Encoding")], self.backend.calls)
        mismatch = self.act(self.observe(), "select_option", element_index=index, option_label="ANSI", value="x")
        self.assertEqual("not_started", mismatch["effect"])

    def test_scroll_finds_container_and_bounds_pages(self):
        obs = self.observe()
        index = self.index(obs, title="Save")
        self.assertEqual("acted", self.act(obs, "scroll", element_index=index, direction="down", pages=2)["type"])
        self.assertEqual(("scroll", None, "down", 2), self.backend.calls[-1])
        for bad in ({"direction": "sideways"}, {"direction": "down", "pages": 11}, {"direction": "up", "pages": True}):
            self.assertEqual("not_started", self.act(self.observe(), "scroll", element_index=index, **bad)["effect"])

    def test_consent_gates_match_the_macos_driver(self):
        obs = self.observe()
        index = self.index(obs, title="Save")
        ask = self.driver.act(obs["obs_id"], "notepad", {"name": "click", "element_index": index},
                              risk_class="confirm", intent_text="save")
        self.assertEqual("confirm_request", ask["type"])
        self.assertEqual("handoff_request", self.driver.act(obs["obs_id"], "notepad", {"name": "click"},
                                                            risk_class="handoff")["type"])
        self.assertEqual([], self.backend.calls)

    def test_expectations_are_checked_before_dispatch_like_macos(self):
        obs = self.observe()
        save = self.index(obs, title="Save")
        def act(expect, index=save):
            return self.driver.act(obs["obs_id"], "notepad", {"name": "click", "element_index": index},
                                   expect=expect)
        refused = {
            "role": {"role": "TextField"},
            "label": {"label": "Open"},
            "attribute": {"label": "Save", "attribute": "Identifier"},
            "selection": {"selection": "digest"},
        }
        for name, expect in refused.items():
            with self.subTest(name):
                result = act(expect)
                self.assertEqual(("target_changed", "not_started"), (result["error"], result["effect"]))
        self.backend.modal = True
        self.assertEqual("target_changed", act({"no_modal": True})["error"])
        self.backend.modal = None
        self.assertEqual("target_changed", act({"no_modal": True})["error"])
        self.backend.modal = False
        no_index = self.driver.act(obs["obs_id"], "notepad", {"name": "read"}, expect={"role": "Button"})
        self.assertEqual("target_changed", no_index["error"])
        self.assertEqual([], self.backend.calls)
        ok = act({"role": "Button", "label": "save", "attribute": "Identifier", "enabled": True, "no_modal": True})
        self.assertEqual("acted", ok["type"])
        self.assertEqual([("invoke", "Save")], self.backend.calls)

    def test_identity_includes_kind_and_name_and_refuses_unknown_ids(self):
        obs = self.observe()
        save = self.index(obs, title="Save")
        # A positional proxy id can stay the same while a different item moves in.
        self.named["Save"].props["name"] = "Delete"
        self.assertEqual("target changed since observation",
                         self.act(obs, "click", element_index=save)["detail"])
        self.named["Save"].props["name"] = "Save"
        self.named["Save"].props["runtime_id"] = None
        obs = self.observe()
        refused = self.act(obs, "click", element_index=self.index(obs, title="Save"))
        self.assertEqual(("not_started", "element identity unavailable"), (refused["effect"], refused["detail"]))
        self.assertEqual([], self.backend.calls)

    def test_pattern_vanishing_before_or_after_opening_is_reported_honestly(self):
        obs = self.observe()
        def gone(element):
            raise LookupError("invoke")
        self.backend.invoke = gone
        before = self.act(obs, "click", element_index=self.index(obs, title="Save"))
        self.assertEqual("not_started", before["effect"])
        def select_gone(element):
            raise LookupError("selection_item")
        self.backend.select = select_gone
        after = self.act(self.observe(), "select_option", element_index=self.index(obs, title="Encoding"),
                         option_label="ANSI")
        self.assertEqual("unknown", after["effect"])
        self.assertEqual("not_started", self.driver.act(self.observe()["obs_id"], "notepad", "click")["effect"])

    def test_unreadable_enabled_state_fails_an_enabled_expectation(self):
        self.named["Save"].props["enabled"] = None
        obs = self.observe()
        result = self.driver.act(obs["obs_id"], "notepad",
                                 {"name": "click", "element_index": self.index(obs, title="Save")},
                                 expect={"enabled": True})
        self.assertEqual("target_changed", result["error"])

    def test_vanishing_elements_truncate_instead_of_crashing(self):
        original = self.backend.props
        def flaky(element):
            if element.props["name"] == "Word wrap":
                raise OSError("element not available")
            return original(element)
        self.backend.props = flaky
        obs = self.observe()
        self.assertTrue(obs["truncated"])
        self.assertIn("트리 일부만 표시", obs["tree"])
        self.assertNotIn("Word wrap", obs["tree"])

    def test_window_titles_cannot_spoof_keys(self):
        self.window.props["name"] = "a, Identifier: save"
        tree = self.observe()["tree"]
        self.assertIn('Title: "a, Identifier: save"', tree)
        self.assertEqual(1, Selector(identifier="save").resolve(tree)[0])

    def test_engine_replay_accepts_windows_results(self):
        from tacto.contract import Resource, SafetyContract
        from tacto.flow import Flow, Step, replay
        contract = SafetyContract(effects=["read"], reversibility="reversible", resource=Resource(scope="entry"))
        result = replay(self.driver, Flow(name="read", target="notepad", steps=[
            Step(action="read", selector=Selector(identifier="body"), safety=contract)]))
        self.assertTrue(result.ok, result.log)

    def test_probe_reads_one_element_and_rejects_stale_ids(self):
        obs = self.observe()
        index = self.index(obs, identifier="body")
        self.assertEqual({"type": "probe", "line": "Title: Text editor, Value: hello, Identifier: body"},
                         self.driver.probe(obs["obs_id"], "notepad", index))
        self.assertEqual("stale_observation", self.driver.probe("old", "notepad", index)["error"])
        self.assertEqual("target_missing", self.driver.probe(obs["obs_id"], "notepad", 999)["error"])

    def test_esc_stops_before_dispatch_and_ends_waits(self):
        from tacto import windows_control
        obs = self.observe()
        index = self.index(obs, title="Save")
        windows_control._stop.set()
        try:
            stopped = self.act(obs, "click", element_index=index)
            self.assertEqual(("aborted_by_user", "not_started"), (stopped["error"], stopped["effect"]))
            self.assertEqual("aborted_by_user", self.driver.wait_until("notepad", lambda t: False)["error"])
            self.assertTrue(windows_control.wait(5, sleep=lambda s: None))
        finally:
            windows_control._stop.clear()
        self.assertEqual([], self.backend.calls)
        self.assertEqual("acted", self.act(self.observe(), "click", element_index=index)["type"])

    def test_control_is_a_quiet_no_op_off_windows_and_resets_the_flag(self):
        from tacto import windows_control
        windows_control._stop.set()
        with windows_control.control():
            self.assertFalse(windows_control.stopped())

    def test_wait_until_polls_until_predicate_or_timeout(self):
        clock = [0.0]
        driver = WindowsDriver(self.backend, clock=lambda: clock[0],
                               sleep=lambda s: clock.__setitem__(0, clock[0] + s))
        self.assertEqual("observation", driver.wait_until("notepad", lambda t: "Save" in t)["type"])
        timeout = driver.wait_until("notepad", lambda t: "Never" in t, timeout_ms=500, poll_ms=100)
        self.assertEqual("timeout", timeout["type"])

    def test_list_targets_and_windowless_observation(self):
        self.assertEqual([{"id": "Notepad.exe", "displayName": "Notepad", "pid": 10, "isRunning": True}],
                         self.driver.list_targets()["apps"])
        self.backend.windows = []
        self.backend.apps.append({"pid": 12, "exe": "tray.exe", "name": "tray"})
        self.assertEqual([], self.driver.list_targets()["apps"])


if __name__ == "__main__":
    unittest.main()
