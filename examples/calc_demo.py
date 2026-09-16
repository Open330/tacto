"""P0 smoke test: reproduce the codex calculator demo through the NLC driver.

Clicks 1 2 × 3 4 = in Calculator using only L2 messages, re-observing after
every act (D3/D7), and verifies 408 appears in the final tree.
"""

import re
import sys

sys.path.insert(0, __file__.rsplit("/", 2)[0])
from nlc.driver import Driver

TARGET = "com.apple.calculator"
# Button labels as they appear in Calculator's AX tree (Korean or English UI).
SEQUENCE = [
    ("all clear|모두 지우기", "AC"),
    ("Description: 1,|ID: One", "1"),
    ("Description: 2,|ID: Two", "2"),
    ("multiply|곱하기", "×"),
    ("Description: 3,|ID: Three", "3"),
    ("Description: 4,|ID: Four", "4"),
    ("equals|=|ID: Equals", "="),
]


def find_index(tree: str, pattern: str) -> int:
    for line in tree.splitlines():
        if not re.search(r"^\s*\d+ Button", line):
            continue
        if any(p.lower() in line.lower() for p in pattern.split("|")):
            return int(line.strip().split()[0])
    raise SystemExit(f"button {pattern!r} not found in tree:\n{tree}")


def main():
    d = Driver()
    obs = d.observe(TARGET, "tree_full")
    if obs["type"] != "observation":
        raise SystemExit(f"observe failed: {obs}")
    print(f"[observe] {obs['obs_id']} elements={obs['element_count']}")

    for pattern, label in SEQUENCE:
        idx = find_index(obs["tree"], pattern)
        r = d.act(obs["obs_id"], TARGET,
                  {"name": "click", "element_index": idx},
                  intent_text=f"{label} 버튼을 누릅니다")
        print(f"[act] click {label!r} idx={idx} -> {r['type']}"
              + (f" ({r.get('detail')})" if r["type"] == "error" else ""))
        if r["type"] == "error":
            raise SystemExit(1)
        obs = d.observe(TARGET, "tree_full")   # D7: fresh state, fresh indices
        print(f"[observe] {obs['obs_id']} waited={obs['waited_ms']}ms")

    if "408" in obs["tree"]:
        print("\nVERIFIED: result 408 present in final tree — E2E OK")
    else:
        display = [l for l in obs["tree"].splitlines() if "Input" in l or "Result" in l]
        print("\nFAILED: 408 not found. Display lines:")
        print("\n".join(display) or obs["tree"][:800])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
