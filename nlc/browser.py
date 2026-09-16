"""Browser backend — the same L2 protocol, over Playwright + the CDP a11y tree.

Design parity with the macOS AX driver: observe returns a flat, indexed
accessibility tree (role + name), actions target an element by the index derived
from the latest observation, and the same freshness/verify discipline applies.
This keeps one flow vocabulary across native apps and the web, and keeps the
accessibility-tree-first (not pixel) stance the whole protocol is built on.

Selectors reuse the same shape: `role` + `title` (the a11y *name*) + `ancestor`.
There is no AXIdentifier on the web, so the strong key is role+name; ancestor
(a landmark/region name) scopes repeats.

Postcondition note: native AX nests a control's value under it (a child
StaticText), so a field-scoped `equals` works. On the web the a11y tree does not
nest an input's value under the input node, so verify a typed value with a
whole-tree `contains` instead of a field-scoped `equals`.

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

    def close(self):
        try:
            self._browser.close(); self._pw.stop()
        except Exception:
            pass

    # ------------------------------------------------------------ navigation

    def goto(self, url: str):
        self._page.goto(url, wait_until="domcontentloaded")
        # Navigation changes the document/handle space: invalidate observations
        # so no stale obs_id/index survives across it (codex guidance).
        self._latest.clear()
        return {"type": "navigated", "url": self._page.url}

    # ------------------------------------------------------------ observe

    _SKIP_ROLES = {"", "none", "generic", "InlineTextBox", "LineBreak",
                   "StaticText", "paragraph", "LayoutTable", "LayoutTableRow",
                   "LayoutTableCell"}

    def observe(self, target: str = "page", mode: str = "tree_full"):
        self._page.wait_for_timeout(300)   # settle
        full = self._cdp.send("Accessibility.getFullAXTree")
        nodes, lines = [], []
        by_id = {n["nodeId"]: n for n in full.get("nodes", [])}
        # depth via parent chain (CDP gives childIds); compute a light indent
        depth_of = {}
        for n in full.get("nodes", []):
            for cid in n.get("childIds", []):
                depth_of[cid] = depth_of.get(n["nodeId"], 0) + 1
        for n in full.get("nodes", []):
            if n.get("ignored"):
                continue
            role = (n.get("role") or {}).get("value", "")
            name = (n.get("name") or {}).get("value", "")
            if role in self._SKIP_ROLES and not name:
                continue
            idx = len(nodes)
            nodes.append({"role": role, "name": name, "backendId": n.get("backendDOMNodeId")})
            d = depth_of.get(n["nodeId"], 0)
            # Same attribute format as the AX driver so flow.Selector (Title:/…)
            # matches identically across native and web backends. Web a11y `name`
            # maps to Title.
            lines.append(f"{'  '*min(d, 12)}{idx} {role}" + (f"  Title: {name}" if name else ""))
        obs_id = f"b{uuid.uuid4().hex[:8]}"
        self._latest[self._target] = {"obs_id": obs_id, "nodes": nodes}
        return {"type": "observation", "obs_id": obs_id, "target": self._target,
                "tree": "\n".join(lines), "element_count": len(nodes),
                "url": self._page.url}

    def wait_until(self, target, predicate, timeout_ms=5000, poll_ms=300):
        # `target` accepted (and ignored) so this is a drop-in for flow.replay,
        # which calls driver.wait_until(flow.target, predicate).
        import time
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            obs = self.observe()
            if predicate(obs["tree"]):
                return obs
            if time.monotonic() >= deadline:
                return {"type": "timeout", "last_obs": obs}
            self._page.wait_for_timeout(poll_ms)

    # ------------------------------------------------------------ act

    def _locator(self, node):
        """Best-effort Playwright locator from an a11y node (role + name)."""
        role = node.get("role", "")
        name = node.get("name", "")
        if name:
            return self._page.get_by_role(role, name=name, exact=True).first
        return self._page.get_by_role(role).first

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
        cur = self._latest.get(self._target)
        if cur is None or cur["obs_id"] != obs_id:
            return {"type": "error", "error": "stale_observation"}
        try:
            with self._page.expect_file_chooser(timeout=10000) as fc:
                self._locator(cur["nodes"][open_selector]).click(timeout=4000)
            fc.value.set_files(paths)
        except Exception as e:
            return {"type": "error", "error": "upload_failed", "detail": str(e)[:200]}
        return {"type": "acted", "action": "upload", "note": "files selected; "
                "verify server-side completion with a separate postcondition"}

    def act(self, obs_id, target, action, risk_class="none", intent_text="",
            approved=False):
        # D6 gate parity
        if risk_class == "handoff":
            return {"type": "handoff_request", "reason": intent_text}
        if risk_class == "confirm" and not approved:
            return {"type": "confirm_request", "what": intent_text}
        cur = self._latest.get(self._target)
        if cur is None or cur["obs_id"] != obs_id:
            return {"type": "error", "error": "stale_observation"}
        name = action.get("name")
        try:
            if name in ("click", "set_value", "type_text", "scroll", "secondary_action"):
                node = cur["nodes"][action["element_index"]]
                loc = self._locator(node)
                if name == "click":
                    loc.click(timeout=4000)
                elif name in ("set_value", "type_text"):
                    loc.fill(action.get("value", action.get("text", "")), timeout=4000)
                elif name == "scroll":
                    loc.scroll_into_view_if_needed(timeout=4000)
                elif name == "secondary_action":
                    loc.click(button="right", timeout=4000)
            elif name == "press_key":
                self._page.keyboard.press(self._to_pw_key(action["key"]))
            elif name == "goto":
                self.goto(action["url"])
            else:
                return {"type": "error", "error": f"unknown_action:{name}"}
        except Exception as e:
            return {"type": "error", "error": "action_failed", "detail": str(e)[:200]}
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
        # verify by re-observing: the textbox now carries the value as its name
        after = d.wait_until("page", lambda t: "홍길동" in t, timeout_ms=3000)
        ok = after["type"] == "observation"
        print("RESULT:", "PASS — value verified in a11y tree" if ok else "FAIL")
    finally:
        d.close()
