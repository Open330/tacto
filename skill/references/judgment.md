## Where judgment is needed: `decide` steps

A recipe replays with no model. When one step genuinely depends on what the
screen says — which key, which part of a text, what to write — make that step
a **decision** instead of hard-coding what you saw. It touches nothing on
screen; it asks once and later steps use the answer as `{{into}}`.

```jsonc
// choose — the answer is one of `choices`
{ "action": "decide", "params": { "question": "화면의 수가 짝수입니까?",
                                  "choices": ["2", "3"], "into": "k" } },
{ "action": "click", "selector": { "role": "Button", "desc": "{{k}}",
                                   "ancestor": "CalculatorKeypadView" }, ... },

// extract — the answer must be literally inside the named captures
{ "action": "decide", "params": { "question": "주문번호만",
                                  "extract_from": ["source"], "into": "order" } },

// compose — free text; may only be typed, never used to aim at an element
{ "action": "decide", "params": { "question": "두 문장짜리 답장", "compose": true,
                                  "into": "reply" } },

// branch — the answer changes everything after it; a branch may decide again
{ "action": "decide", "params": { "question": "짝수입니까, 홀수입니까?",
    "choices": ["짝수", "홀수"], "into": "case", "goal": "짝수면 ×2, 홀수면 +1",
    "branches": { "홀수": [ /* steps for this case, to the end */ ] } } }
```

Rules the engine enforces:

- `into` is an ASCII name, unique across the whole recipe including branches.
- Read what the decision rests on first (a `read` step with a `capture`); the
  captures so far are what the model sees. `"sensitive": true` ones it never sees.
- A decided value may reach a step that needs consent only if the run can ask
  at that moment (`confirm`); the person then sees the actual value — the whole
  text for `compose`. Otherwise the recipe is refused before it starts.
- A branch answer with no steps is walked by the agent from that point when a
  walker is available, and kept if the walk checks out. Without one:
  `branch_unwalked`.

Details and measurements: [`docs/recipes.md`](../docs/recipes.md).
