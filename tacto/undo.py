"""Undoing something — only where we actually hold the means to.

`SafetyContract.reversibility` is a word a model wrote. It says what someone
believed about an action, which is worth having and is not the same as being
able to undo it. A recipe that declares `recoverable` and a system with no way
to put the file back are indistinguishable at the point it matters.

So this module is about capability rather than belief. A reversal exists for an
effect only when all of it is true at once:

  * something in this repo knows the inverse operation, by name, and it was
    written here rather than described by a model at run time;
  * whatever that inverse needs was captured BEFORE the effect, because a
    snapshot taken afterwards is a snapshot of the damage;
  * the window it is valid in has not closed;
  * and there is a way to tell whether the undo itself worked.

Two rules follow that are easy to get backwards.

**An effect that may not have happened is never undone.** Reversing something
is itself an effect, and "probably sent, so unsend it" deletes a message that
may be the only copy. Unknown is not a weak yes.

**Recovery is not success.** Putting things back means the harm was avoided,
not that the task was done, and a receipt says `recovered` rather than `done`
(advisor finding).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Capability:
    """A named way to undo one kind of effect, and what it costs to have it.

    `needs` is the point of the whole dataclass: a reversal that requires the
    previous contents has to say so, so that the loop captures them while they
    still exist.
    """
    effect: str            # the effect this reverses
    needs: tuple           # facts to capture BEFORE the effect
    inverse: str           # a key of REVERSALS — code here, not model prose
    confirms: str          # what makes the undo itself verifiable
    window_s: float = 0.0  # 0 = no time limit of its own


# What this repo can actually undo. Deliberately short: an entry here is a
# promise that the inverse is written, tested and verifiable, and a missing one
# is the honest answer rather than a gap to paper over with a model's opinion.
REVERSALS: dict = {
    "write_persistent": Capability(
        # "저장 전 있었는가" is in `needs` because the two cases are different
        # operations wearing one word. Undoing a save that CREATED a file is
        # removing it; undoing one that OVERWROTE a file is restoring what was
        # there, which needs the old contents — and if nobody took them before
        # the write, they are gone. Refusing the second is the honest answer;
        # removing the file instead would destroy what the save landed on.
        effect="write_persistent", needs=("저장 위치", "저장 전 있었는가"),
        inverse="remove_written_file", confirms="그 자리에 파일이 없음"),
}


def _remove_written_file(captured: dict) -> "Reversal":
    """Take back a save that created a file, by removing what it created.

    Through the filesystem rather than the app's Undo, which is not a shortcut:
    the app's menu item is a control whose effect on THIS file, at this moment,
    nothing has established (advisor finding). The path came from our own
    capture before the write, and nothing else is touched.
    """
    import os
    path = str(captured.get("저장 위치") or "")
    existed = captured.get("저장 전 있었는가")
    if existed:
        return Reversal(attempted=False, inverse="remove_written_file",
                        why="저장하기 전에도 그 자리에 파일이 있었습니다 — "
                            "지우면 원래 있던 것이 사라집니다")
    if not path or not os.path.isabs(path):
        return Reversal(attempted=False, inverse="remove_written_file",
                        why=f"되돌릴 파일 경로가 온전하지 않습니다: {path!r}")
    if not os.path.exists(path):
        # Already not there. Nothing to undo, and saying so beats reporting a
        # success nobody caused.
        return Reversal(attempted=False, ok=True,
                        inverse="remove_written_file",
                        why="그 자리에 파일이 없습니다")
    try:
        os.remove(path)
    except OSError as e:
        return Reversal(attempted=True, inverse="remove_written_file",
                        why=f"지우지 못했습니다: {e}")
    return Reversal(attempted=True, ok=not os.path.exists(path),
                    inverse="remove_written_file",
                    why="" if not os.path.exists(path) else "아직 남아 있습니다")


# The inverse operations themselves. Named, written here, and verifiable —
# the same discipline as `RECOVERIES`, and for the same reason: a model may
# choose WHICH undo applies, never describe what undoing means.
INVERSES = {
    "remove_written_file": _remove_written_file,
}


def reverse(cap: "Capability | None", captured: dict) -> "Reversal":
    """Perform the reversal. Callers check `may_reverse` first."""
    if cap is None:
        return Reversal(why="되돌릴 방법이 없습니다")
    fn = INVERSES.get(cap.inverse)
    if fn is None:
        return Reversal(inverse=cap.inverse,
                        why=f"'{cap.inverse}' 는 아직 구현돼 있지 않습니다")
    return fn(captured)


def capability_for(effects) -> "Capability | None":
    """The reversal for these effects, if we hold one for every one of them.

    Every effect, not any: undoing the save and leaving the send is not undoing
    the task, and reporting it as recovered would be worse than reporting the
    failure.
    """
    real = {e for e in effects if e != "read"}
    if not real or len(real) > 1:
        return None                     # nothing to undo, or more than one kind
    return REVERSALS.get(next(iter(real)))


def held(cap: "Capability | None", captured: dict) -> tuple[bool, str]:
    """Whether the reversal can actually be performed right now, and why not.

    Answered from what was captured, not from what anyone declared. A `needs`
    that is missing means the snapshot was never taken, and taking it now would
    photograph the damage.
    """
    if cap is None:
        return False, "이 동작을 되돌릴 방법을 갖고 있지 않습니다"
    # Presence, not truthiness. "저장 전 있었는가: False" is one of the two
    # answers that question has, and treating it as unanswered refuses to undo
    # exactly the case that CAN be undone.
    missing = [n for n in cap.needs if n not in captured or captured[n] is None]
    if missing:
        return False, ("되돌리는 데 필요한 것을 미리 확보하지 못했습니다: "
                       + ", ".join(missing))
    return True, ""


def may_reverse(effect_certainty: str, cap, captured: dict) -> tuple[bool, str]:
    """The whole decision, including the one that is easy to get backwards.

    `effect_certainty` is "confirmed" or "unknown". Undoing an unknown effect
    is not a cautious choice — it is a second action taken on a guess, and the
    guess it rests on is the one nobody could settle.
    """
    if effect_certainty != "confirmed":
        return False, ("실행됐는지 확실하지 않아 되돌리지 않았습니다 — "
                       "되돌리는 것도 하나의 동작입니다")
    return held(cap, captured)


@dataclass
class Reversal:
    """What happened when we tried to put things back."""
    attempted: bool = False
    ok: bool = False
    why: str = ""
    inverse: str = ""
