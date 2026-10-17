"""Browser backend (OPTIONAL) — the same L2 protocol, over Playwright + CDP.

Not required for computer use of the user's real browser: to the macOS AX driver
a browser (Chrome/Safari/Arc) is just another app, driven through the same
accessibility layer as everything else. This module is a separate, optional path
for headless / programmatic web automation — install it with the `web` extra
(`uv sync --extra web`). The on-philosophy way to reach the *user's own* browser
is to attach to its running instance (Codex's iab/extension/cdp providers do
this), not to spawn a headless Chromium; that attach path is future work.

Design parity with the macOS AX driver: observe returns a flat, indexed
accessibility tree (role + name), actions target an element by the index derived
from the latest observation, and the same freshness/verify discipline applies.
This keeps one flow vocabulary across native apps and the web, and keeps the
accessibility-tree-first (not pixel) stance the whole protocol is built on.

Selectors reuse the same shape: `role` + `title` (the a11y *name*) + `ancestor`.
There is no AXIdentifier on the web, so the strong key is role+name; ancestor
(a landmark/region name) scopes repeats.

Input values are serialized as Value on the control itself, so a field-scoped
postcondition with attribute=Value can verify a typed value exactly.

Identity-kind separation (codex guidance): native AX, browser AX, DOM node_id
and Playwright locators are DIFFERENT id spaces — never share obs_id or indices
across them. This driver keeps its own observation registry and its own index
space; a Flow authored against the browser backend is not portable to the native
one and vice versa. The L2 observe/act *semantics* are shared; the handles are not.
"""

from __future__ import annotations

import uuid

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - import guarded so the pkg stays optional
    sync_playwright = None


class BrowserDriver:
    """L2 endpoint for a web page. Mirrors driver.Driver's message surface."""

    capabilities = frozenset({"action.scroll", "action.press_key", "action.select_option", "action.open_select", "action.select_custom_option", "expect.modal_scope"})

    def __init__(self, headless: bool = True):
        if sync_playwright is None:
            raise RuntimeError("playwright not installed: pip install playwright "
                               "&& python -m playwright install chromium")
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self._page = self._browser.new_page()
        self._cdp = self._page.context.new_cdp_session(self._page)
        self._cdp.send("Accessibility.enable")
        self._latest = {}          # target(url key) -> {obs_id, nodes}
        self._target = "page"
        from .web_select import CustomSelect
        self._custom_select = CustomSelect(self)

    def close(self):
        self._custom_select.close()
        try:
            self._browser.close(); self._pw.stop()
        except Exception:
            pass

    # ------------------------------------------------------------ navigation

    def identity(self, target: str = "page") -> str:
        """Contract-binding identity for the web is the page ORIGIN, not the
        opaque 'page' target — a recorded judgment about example.com must not
        validate against evil.com (advisor: destination/origin drift is hard)."""
        from urllib.parse import urlparse
        u = urlparse(self._page.url)
        return f"{u.scheme}://{u.netloc}" if u.netloc else f"{u.scheme}:"

    def goto(self, url: str):
        self._page.goto(url, wait_until="domcontentloaded")
        # Navigation changes the document/handle space: invalidate observations
        # so no stale obs_id/index survives across it (codex guidance).
        self._latest.clear()
        self._custom_select.close()
        return {"type": "navigated", "url": self._page.url}

    # ------------------------------------------------------------ observe

    _SKIP_ROLES = {"", "none", "generic", "InlineTextBox", "LineBreak",
                   "StaticText", "paragraph", "LayoutTable", "LayoutTableRow",
                   "LayoutTableCell"}

    def observe(self, target: str = "page", mode: str = "tree_full",
                require_window: bool = True):
        if target != self._target:
            return {"type": "error", "error": "target_not_found"}
        self._page.wait_for_timeout(300)   # settle
        full = self._cdp.send("Accessibility.getFullAXTree")
        nodes, lines = [], []
        by_id = {n["nodeId"]: n for n in full.get("nodes", [])}
        # CDP's array order is not a preorder traversal. Indenting that array
        # made an input appear under a preceding sibling button/region, which
        # breaks scoped selectors. Walk childIds and compress omitted wrappers.
        children = {cid for n in by_id.values() for cid in n.get("childIds", [])}
        roots = [nid for nid in by_id if nid not in children]
        stack = [(nid, 0) for nid in reversed(roots)]
        visited = set()
        while stack:
            nid, depth = stack.pop()
            if nid in visited or nid not in by_id:
                continue
            visited.add(nid)
            n = by_id[nid]
            role = (n.get("role") or {}).get("value", "")
            name = (n.get("name") or {}).get("value", "")
            omitted = n.get("ignored") or (role in self._SKIP_ROLES and not name)
            child_depth = depth if omitted else depth + 1
            stack.extend((cid, child_depth) for cid in reversed(n.get("childIds", [])))
            if omitted:
                continue
            idx = len(nodes)
            nodes.append(self._node(n))
            # Same attribute format as the AX driver so flow.Selector (Title:/…)
            # matches identically across native and web backends. Web a11y `name`
            # maps to Title.
            lines.append(self._line(idx, nodes[-1], depth))
        obs_id = f"b{uuid.uuid4().hex[:8]}"
        self._latest[self._target] = {"obs_id": obs_id, "nodes": nodes,
                                    "document": self._document()}
        custom_selects = {}
        for i, node in enumerate(nodes):
            if node['role'] == 'combobox' and node.get('options') is None:
                state = self._custom_select.discover(obs_id, target, i)
                custom_selects[str(i)] = (state if state.get('type') == 'select_control'
                                          else {'state': 'unsupported', 'options': []})
        self._latest[self._target]['custom_selects'] = custom_selects
        return {"type": "observation", "obs_id": obs_id, "target": self._target,
                "custom_selects": {key: {k: state[k] for k in ('state', 'options')}
                                   for key, state in custom_selects.items()},
                "tree": "\n".join(lines), "element_count": len(nodes),
                "select_options": {str(i): n["options"] for i, n in enumerate(nodes)
                                   if isinstance(n.get("options"), list)},
                "url": self._page.url}

    def wait_until(self, target, predicate, timeout_ms=5000, poll_ms=300):
        import time
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            obs = self.observe(target)
            if obs["type"] != "observation":
                return obs
            if predicate(obs["tree"]):
                return obs
            if time.monotonic() >= deadline:
                return {"type": "timeout", "last_obs": obs}
            self._page.wait_for_timeout(poll_ms)

    # ------------------------------------------------------------ act

    def _node(self, node):
        props = {p["name"]: p.get("value", {}).get("value")
                 for p in node.get("properties", [])}
        result = {"role": node.get("role", {}).get("value", ""),
                "name": node.get("name", {}).get("value", ""),
                "value": node.get("value", {}).get("value"),
                "backendId": node.get("backendDOMNodeId"),
                "disabled": props.get("disabled", False),
                "readonly": props.get("readonly") is True}
        if result["role"] in ("combobox", "listbox"):
            handle = None
            try:
                handle = self._locator(result)
                result["options"] = handle.evaluate("""el =>
                    el.tagName === 'SELECT' && !el.multiple ?
                    Array.from(el.options, o => ({value:o.value, label:o.label,
                      disabled:o.disabled || !!o.closest('optgroup[disabled]')})) : null""")
            except Exception:
                result["options"] = None
            finally:
                self._dispose(handle)
        # Chromium omits AX value for empty inputs. Missing is not evidence of
        # emptiness: confirm it on this exact DOM node, without exposing a
        # nonempty value that accessibility intentionally withheld.
        if result["value"] is None and result["role"] in ("textbox", "searchbox", "combobox", "spinbutton"):
            handle = None
            try:
                handle = self._locator(result)
                empty = handle.evaluate("""el =>
                    (typeof el.value === 'string' && el.value === '') ||
                    (el.isContentEditable && el.textContent === '')""")
                if empty:
                    result["value"] = ""
            except Exception:
                pass  # Inaccessible remains unknown; never synthesize a blank.
            finally:
                self._dispose(handle)
        return result

    def _document(self):
        frame = self._cdp.send("Page.getFrameTree")["frameTree"]["frame"]
        return (frame["id"], frame.get("loaderId"))

    @staticmethod
    def _line(idx, node, depth=0):
        # Values belong to the control itself, including an empty input.
        name = str(node.get("name", "")).replace("\n", " ")
        value = node.get("value")
        return (f"{'  '*depth}{idx} {node['role']}" +
                (" [readonly]" if node.get("readonly") else "") +
                (f"  Title: {name}" if name else "") +
                (f", Value: {str(value).replace(chr(10), ' ')}" if value is not None else "") +
                (" [disabled]" if node.get("disabled") else ""))

    def _locator(self, node):
        """Pin a Playwright handle to the observed CDP node, never first-match.

        A temporary JavaScript reference bridges the two public APIs without
        mutating the DOM or selecting by label. Detached nodes and frames
        outside this page fail instead of re-targeting.
        """
        backend = node.get("backendId")
        if backend is None:
            raise ValueError("observed AX node has no DOM identity")
        obj = self._cdp.send("DOM.resolveNode", {"backendNodeId": backend})["object"]["objectId"]
        reference = "__nlc_node_" + uuid.uuid4().hex
        handle = None
        try:
            result = self._cdp.send("Runtime.callFunctionOn", {
                "objectId": obj,
                "functionDeclaration": "function(k) { if (!this.isConnected || this.nodeType !== 1) throw Error('detached/non-element'); Object.defineProperty(globalThis, k, {value: this, configurable: true}); }",
                "arguments": [{"value": reference}]})
            if result.get("exceptionDetails"):
                raise ValueError("observed DOM node is no longer available")
            handle = self._page.evaluate_handle("key => globalThis[key]", reference)
            element = handle.as_element()
            if element is None:
                handle.dispose()
                raise ValueError("observed DOM node is outside the current page")
            return element
        finally:
            try:
                self._cdp.send("Runtime.callFunctionOn", {
                    "objectId": obj, "functionDeclaration": "function(k) { delete globalThis[k]; }",
                    "arguments": [{"value": reference}]})
            except Exception:
                pass  # Navigation may already have destroyed this realm.
            finally:
                try:
                    self._cdp.send("Runtime.releaseObject", {"objectId": obj})
                except Exception:
                    pass

    @staticmethod
    def _dispose(handle):
        if handle is not None:
            try:
                handle.dispose()
            except Exception:
                pass  # Cleanup must not replace the recorded action outcome.

    def probe(self, obs_id, target, element_index):
        if target != self._target:
            return {"type": "error", "error": "target_mismatch", "effect": "not_started"}
        cur = self._latest.get(self._target)
        if not cur or cur["obs_id"] != obs_id or cur["document"] != self._document():
            return {"type": "error", "error": "stale_observation", "effect": "not_started"}
        if type(element_index) is not int or not 0 <= element_index < len(cur["nodes"]):
            return {"type": "error", "error": "target_missing", "effect": "not_started"}
        node = cur["nodes"][element_index]
        try:
            nodes = self._cdp.send("Accessibility.getPartialAXTree", {
                "backendNodeId": node["backendId"], "fetchRelatives": False})["nodes"]
            current = next(n for n in nodes if n.get("backendDOMNodeId") == node["backendId"] and not n.get("ignored"))
            live = self._node(current)
            return {"type": "probe", "line": self._line(element_index, live), "node": live}
        except Exception as e:
            return {"type": "error", "error": "target_missing", "effect": "not_started", "detail": str(e)}

    def selection(self, target):
        import hashlib
        if target != self._target:
            return {"type": "error", "error": "target_mismatch", "effect": "not_started"}
        value = self._page.evaluate("""() => {
            const path = [];
            let el = document.activeElement;
            while (el && path.length < 128) {
                const parent = el.parentElement;
                path.push([el.tagName, el.id,
                    parent ? Array.prototype.indexOf.call(parent.children, el) : 0]);
                el = parent;
            }
            return [location.href, String(getSelection()), path];
        }""")
        return {"type": "selection", "digest": hashlib.sha256(repr(value).encode()).hexdigest()[:32]}

    @staticmethod
    def _to_pw_key(key: str) -> str:
        """xdotool-style (super+c, Return) -> Playwright (Meta+c, Enter)."""
        parts = key.split("+")
        m = {"super": "Meta", "cmd": "Meta", "ctrl": "Control", "alt": "Alt",
             "shift": "Shift", "Return": "Enter", "KP_0": "Numpad0"}
        return "+".join(m.get(p, m.get(p.lower(), p)) for p in parts)

    def upload(self, obs_id, target, open_selector, paths, approved=False):
        """File upload via the chooser pattern (codex guidance): subscribe to the
        filechooser BEFORE clicking the control that opens it, then setFiles.
        Selecting files != server upload success — the caller verifies upload
        with a separate postcondition. Uploads are confirm/handoff-worthy, so a
        caller must pass approved=True."""
        if not approved:
            return {"type": "confirm_request", "what": "파일 업로드",
                    "detail": "resend with approved=true"}
        if target != self._target:
            return {"type": "error", "error": "target_mismatch", "effect": "not_started"}
        cur = self._latest.get(self._target)
        if cur is None or cur["obs_id"] != obs_id:
            return {"type": "error", "error": "stale_observation", "effect": "not_started"}
        if cur["document"] != self._document():
            return {"type": "error", "error": "stale_observation", "effect": "not_started"}
        if type(open_selector) is not int or not 0 <= open_selector < len(cur["nodes"]):
            return {"type": "error", "error": "target_missing", "effect": "not_started"}
        handle = None
        dispatched = False
        try:
            handle = self._locator(cur["nodes"][open_selector])
            with self._page.expect_file_chooser(timeout=10000) as fc:
                dispatched = True
                handle.click(timeout=4000)
            fc.value.set_files(paths)
        except Exception as e:
            return {"type": "error", "error": "upload_failed", "detail": str(e)[:200],
                    "effect": "unknown" if dispatched else "not_started"}
        finally:
            self._dispose(handle)
        return {"type": "acted", "action": "upload", "note": "files selected; "
                "verify server-side completion with a separate postcondition"}

    def act(self, obs_id, target, action, expect=None, risk_class="none", intent_text="",
            approved=False):
        # D6 gate parity with the native driver: preapproved gates too (the
        # driver has no view of the original request, so it fails closed).
        if risk_class == "blocked" or risk_class not in ("none", "preapproved", "confirm", "handoff"):
            return {"type": "error", "error": "unverifiable_action", "effect": "not_started"}
        if risk_class == "handoff":
            return {"type": "handoff_request", "reason": intent_text}
        if risk_class in ("confirm", "preapproved") and not approved:
            return {"type": "confirm_request", "what": intent_text, "risk_class": risk_class}
        if target != self._target:
            return {"type": "error", "error": "target_mismatch", "effect": "not_started"}
        cur = self._latest.get(self._target)
        if cur is None or cur["obs_id"] != obs_id:
            return {"type": "error", "error": "stale_observation", "effect": "not_started"}
        if cur["document"] != self._document():
            return {"type": "error", "error": "stale_observation", "effect": "not_started"}
        if "element_index" in action:
            index = action["element_index"]
            if type(index) is not int or not 0 <= index < len(cur["nodes"]):
                return {"type": "error", "error": "target_missing", "effect": "not_started"}
        if action.get("name") == "press_key" and "element_index" in action:
            return {"type": "error", "error": "invalid_key_target", "effect": "not_started"}
        if expect:
            if "element_index" not in action and any(k in expect for k in ("role", "label", "attribute", "enabled")):
                return {"type": "error", "error": "unverifiable_expectation", "effect": "not_started"}
            if expect.get("selection") and self.selection(target)["digest"] != expect["selection"]:
                return {"type": "error", "error": "target_changed", "effect": "not_started"}
            if expect.get("no_modal") and self._page.locator('dialog[open], [role="dialog"][aria-modal="true"], [role="alertdialog"][aria-modal="true"]').count():
                return {"type": "error", "error": "target_changed", "effect": "not_started"}
            if expect.get('modal_scope'):
                handle = None
                try:
                    index = action.get('element_index')
                    handle = self._locator(cur['nodes'][index]) if type(index) is int else None
                    inside = self._page.evaluate("""element => {
                      const visible = el => !!(el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
                      const modals = Array.from(document.querySelectorAll('dialog[open], [role="dialog"], [role="alertdialog"]')).filter(visible);
                      const target = element || document.activeElement;
                      return modals.length === 1 && target && modals[0].contains(target);
                    }""", handle)
                    if not inside:
                        return {"type": "error", "error": "target_changed", "effect": "not_started", "detail": "modal scope changed or ambiguous"}
                except Exception:
                    return {"type": "error", "error": "target_changed", "effect": "not_started"}
                finally:
                    self._dispose(handle)
            if "element_index" in action:
                probe = self.probe(obs_id, target, action["element_index"])
                node = probe.get("node", {})
                field = {"Title": "name", "Value": "value"}.get(expect.get("attribute", "Title"))
                mismatch = (probe["type"] != "probe" or
                            (expect.get("role") and node.get("role", "").casefold() != expect["role"].casefold()) or
                            ("label" in expect and (field is None or str(node.get(field, "")) != str(expect["label"]))))
                if expect.get("enabled") and node.get("disabled"):
                    mismatch = True
                if mismatch:
                    return {"type": "error", "error": "target_changed", "effect": "not_started"}
        name = action.get("name")
        if name in ('open_select', 'select_custom_option'):
            index = action.get('element_index')
            if type(index) is not int:
                return {"type": "error", "error": "target_missing", "effect": "not_started"}
            allowed = {'name', 'element_index'} | ({'option_label', 'value'} if name == 'select_custom_option' else set())
            if set(action) - allowed:
                return {"type": "error", "error": "invalid_action", "effect": "not_started"}
            state = cur.get('custom_selects', {}).get(str(index))
            if state is None or 'snapshot_id' not in state:
                return {"type": "error", "error": "unsupported_custom_select", "effect": "not_started"}
            if name == 'open_select':
                return self._custom_select.open(state['snapshot_id'])
            if not isinstance(action.get('option_label'), str) or ('value' in action and not isinstance(action['value'], str)):
                return {"type": "error", "error": "invalid_option", "effect": "not_started"}
            return self._custom_select.select(state['snapshot_id'], label=action['option_label'], value=action.get('value'))
        if name == "press_key":
            from .ask import NAVIGATION_KEYS
            if not isinstance(action.get("key"), str) or action["key"] not in NAVIGATION_KEYS:
                return {"type": "error", "error": "unsupported_key", "effect": "not_started"}
        if name == "secondary_action" and action.get("action") != "AXShowMenu":
            return {"type": "error", "error": "unsupported_secondary_action", "effect": "not_started"}
        if name == "read":
            return {"type": "acted", "action": name, "obs_id": obs_id}
        index = action.get("element_index")
        if name in ("click", "set_value", "type_text", "scroll", "secondary_action", "select_option") and (type(index) is not int or not 0 <= index < len(cur["nodes"])):
            return {"type": "error", "error": "target_missing", "effect": "not_started"}
        if name == "scroll" and (action.get("direction", "down") not in ("up", "down", "left", "right")
                                 or type(action.get("pages", 1)) is not int
                                 or not 1 <= action.get("pages", 1) <= 100):
            return {"type": "error", "error": "invalid_scroll", "effect": "not_started"}
        dispatched = False
        loc = None
        try:
            if name == "select_option":
                value = action.get("value")
                node = cur["nodes"][index]
                options = node.get("options")
                if not isinstance(value, str) or not isinstance(options, list):
                    return {"type": "error", "error": "unsupported_select", "effect": "not_started"}
                matches = [o for o in options if o["value"] == value]
                if len(matches) != 1 or matches[0]["disabled"]:
                    return {"type": "error", "error": "invalid_option", "effect": "not_started"}
                if "option_label" in action and action["option_label"] != matches[0]["label"]:
                    return {"type": "error", "error": "option_changed", "effect": "not_started"}
                loc = self._locator(node)
                current = loc.evaluate("""el => ({disabled:el.matches(':disabled'),
                    options:el.tagName === 'SELECT' && !el.multiple ?
                    Array.from(el.options, o => ({value:o.value, label:o.label,
                      disabled:o.disabled || !!o.closest('optgroup[disabled]')})) : null})""")
                if current["disabled"] or current["options"] != options:
                    return {"type": "error", "error": "target_changed", "effect": "not_started"}
                dispatched = True
                selected = loc.select_option(value=value, timeout=4000)
                if selected != [value] or loc.input_value() != value:
                    return {"type": "error", "error": "selection_unverified", "effect": "unknown"}
            elif name in ("click", "set_value", "type_text", "scroll", "secondary_action"):
                node = cur["nodes"][action["element_index"]]
                loc = self._locator(node)
                if name in ("set_value", "type_text") and (
                        node.get("readonly") or not loc.is_editable()):
                    return {"type": "error", "error": "target_not_editable",
                            "effect": "not_started"}
                dispatched = True
                if name == "click":
                    loc.click(timeout=4000)
                elif name == "set_value":
                    loc.fill(action.get("value", ""), timeout=4000)
                elif name == "type_text":
                    loc.focus()
                    if not loc.evaluate("el => el.getRootNode().activeElement === el"):
                        raise ValueError("text target did not retain focus")
                    self._page.keyboard.insert_text(action.get("text", ""))
                elif name == "scroll":
                    loc.evaluate("""(el, options) => {
                        if (!el.isConnected) throw Error('detached scroll target');
                        const vertical = options.direction === 'up' || options.direction === 'down';
                        let box = el;
                        while (box && !(vertical ? box.scrollHeight > box.clientHeight : box.scrollWidth > box.clientWidth))
                            box = box.parentElement;
                        box = box || el.ownerDocument.scrollingElement;
                        if (!box) throw Error('no scroll container');
                        const sign = options.direction === 'up' || options.direction === 'left' ? -1 : 1;
                        const distance = (vertical ? box.clientHeight : box.clientWidth) * options.pages * sign;
                        box.scrollBy({top: vertical ? distance : 0, left: vertical ? 0 : distance, behavior: 'instant'});
                    }""", {"direction": action.get("direction", "down"), "pages": action.get("pages", 1)})
                elif name == "secondary_action":
                    loc.click(button="right", timeout=4000)
            elif name == "press_key":
                dispatched = True
                self._page.keyboard.press(self._to_pw_key(action["key"]))
            elif name == "goto":
                dispatched = True
                self.goto(action["url"])
            else:
                return {"type": "error", "error": f"unknown_action:{name}", "effect": "not_started"}
        except Exception as e:
            return {"type": "error", "error": "action_failed", "effect": "unknown" if dispatched else "not_started", "detail": str(e)[:200]}
        finally:
            self._dispose(loc)
        return {"type": "acted", "action": name, "obs_id": obs_id,
                "note": "not verified — observe again (D7)"}


if __name__ == "__main__":
    # Offline deterministic smoke: observe -> fill a field by index -> verify.
    PAGE = ("data:text/html,<h1>가입</h1>"
            "<label>이름<input aria-label='이름'></label>"
            "<button>보내기</button>")
    d = BrowserDriver(headless=True)
    try:
        d.goto(PAGE)
        obs = d.observe()
        print(obs["tree"])
        idx = next((int(l.split()[0]) for l in obs["tree"].splitlines()
                    if l.strip().split()[1:2] == ["textbox"]), None)
        r = d.act(obs["obs_id"], "page",
                  {"name": "type_text", "element_index": idx, "text": "홍길동"},
                  intent_text="이름 입력")
        print("\ntype:", r["type"])
        # Verify by re-observing the textbox Value attribute.
        after = d.wait_until("page", lambda t: "홍길동" in t, timeout_ms=3000)
        ok = after["type"] == "observation"
        print("RESULT:", "PASS — value verified in a11y tree" if ok else "FAIL")
    finally:
        d.close()
