# Host-gated continuation of causal commit journals

`reconcile_journal` is an explicit trusted Python integration. A fresh
`DurableDispatchDriver` keeps its original directory and reservations. The host supplies a concrete list/tuple of original commits: the original event, original expected outcome, and original native
`expect` for **every** reserved slot, then reads fresh causal evidence. On
success, this wrapper starts at the next slot for explicitly authored new input.
It does not replay a recipe prefix, reconnect a helper, select a continuation,
or grant approval. Default CLI/restart behavior still refuses existing slots.

```python
from tacto.dispatch_journal import DurableDispatchDriver
from tacto.journal_reconciliation import CommitReconciliation, reconcile_journal

# These values come from the original trusted task, not the journal or a model.
commit = CommitReconciliation(original_event, original_expected,
                              expect=original_dispatch_expect)
with DurableDispatchDriver(fresh_driver, same_private_directory) as guarded:
    reconcile_journal(guarded, [commit], read_fresh_causal_evidence)
    # Independently authorize and observe the NEW operation before calling act.
    guarded.act(fresh_obs_id, next_target, next_action, **next_dispatch_parameters)
```

`read_fresh_causal_evidence(slot, event_copy)` must only read and return a typed
`CommitEvidence` from the actual producer/receiver. A bool, dictionary, transport
acknowledgement, saved-state file, or sender transcript cannot open the gate.
Producer evidence requires identity/nonce, destination/name/content expectations.
Delivery evidence requires recipient/account and body or body digest expectations;
attachment expectations remain exact when supplied. Existing `evidence_error`
checks still apply. One producer completion sequence or receiver message ID
cannot cover multiple journal slots. This is an in-process trusted host boundary,
not cryptographic authentication of a dishonest host or kernel writer attribution.

The API presently reconciles only causal producer/delivery commits. A journal
containing ephemeral inputs, payments, deletion, or only saved-state proof cannot
be advanced by this API. Those workflows still require another verified
reconciliation design. The original plan must be retained by the host; the
journal stores hashes, not task inputs or outcome authority. This API does not
recover that plan from worker output, receipts, or model guesses.

The complete bounded catalog must be contiguous and each record a private
owner-only regular file with one link. Malformed/torn/duplicate-key records,
holes, symlinks, substituted directories or changed records refuse continuation.
The directory is pinned by descriptor. Old records are never deleted, edited or
renumbered. They are checked again before new input. A completed operation's
same target/action/expect replay fingerprint is refused at a new slot; this
comparison excludes volatile `element_index`. It conservatively also refuses
new operations whose remaining fingerprints are indistinguishable. This is
not a generic semantic identity check: changes to other action/expect fields
may change the fingerprint. Hosts must not restart the old recipe from its
beginning or use invented bindings to bypass this gate. Author only the verified
new continuation. The API neither selects nor authenticates that continuation.
Old artifacts/receipts must still be independently verifiable; overwritten or
expired evidence cannot open the gate.

Reconciliation uses one fresh wrapper and one attempt. Failure keeps it blocked.
A later explicit host attempt may use another fresh wrapper with the same
unchanged journal and fresh evidence, never another directory to retry uncertain
input. Callbacks receive a separate event copy; caller plan changes during reads
are not adopted. Attempts to call wrapped input during reconciliation fail closed.
A 30-second default acceptance deadline (after acquiring the wrapper lock) and optional cancellation callback are
checked around reads; they cannot forcibly interrupt a blocking callback or OS
read. One ordered writer per directory remains required. Calling the underlying
driver or another browser/native input path bypasses this barrier.

The tests use actual loopback HTTP and worker termination: the first worker
POSTs once and exits before reply logging; fresh receiver evidence permits a
new second request. The first record stays byte-identical and the receiver has
exactly `first/one`, then `second/two`. This proves a local transport continuation,
not signed native GUI, external account delivery, general application recovery,
power-loss durability, automatic resume or exactly-once delivery under all failures.


## Signed native integration

`nlc eval signed_producer_worker_reconcile --report NEW.json` explicitly checks
an owned producer Save, worker exit17 before reply logging, and a new signed
worker. The original event/expectation is retained before input. Fresh callback
and file reads reconcile slot0; slot1 dispatches one new owned native button
input. An independent AX counter must advance0→1 while producer completion
sequence stays1 and the original record remains unchanged. See the dated
verification record for the actual run and manifests. This is one causal commit
and one new native operation, not automatic recipe resume or ephemeral-prefix
reconciliation. It grants no new signing/TCC authority or general writer proof.

## Explicit Flow continuation

Use `reconcile_and_replay` when the trusted host has retained an explicitly
authored **new** Flow and its independent approvals and expected outcomes:

```python
from tacto.journal_reconciliation import reconcile_and_replay

with DurableDispatchDriver(fresh_driver, same_private_directory) as guarded:
    result = reconcile_and_replay(
        guarded, original_commits, read_fresh_causal_evidence, new_flow,
        approvals=new_approvals, prepare_commit=new_receipt.prepare_commit,
        after_commit=new_receipt.after_commit,
        commit_expected=new_receipt.commit_expected,
    )
```

The Flow and static approval/expectation values are copied before evidence
callbacks run. Hooks retain their original trusted state; the visual registry
mapping is copied without cloning its registered adapters. The whole gate and
replay use the same wrapper lock. Journal close also waits for that lock, so it
cannot invalidate the pinned descriptor during another thread's reconciliation.
This wait cannot forcibly interrupt a blocking host callback or driver.

The new Flow still obtains fresh observations and validates normal contracts,
approvals, preparation and post-commit evidence. Old proofs do not approve new
transmissions. A denied approval can leave a reserved slot with category unknown;
no receiving POST occurred in the owned negative test, but the reservation stays
blocked and is not deleted. This is conservative input accounting, not completion.
The reconciliation deadline/cancellation applies to the proof gate, not the
lifetime of the new Flow. Replay escalation/model budgets remain explicitly
controlled through its ordinary options.

`tests/test_browser_journal_continuation.py` terminates a real headless Chromium
worker with17 after the first receiving record exists and before reply logging.
A parent host opens a fresh browser/guard, reads the original recipient inbox,
and runs a separately approved new Flow with a distinct real button label.
Exactly first/second bodies reach the owned receiver, each has a new exact receipt,
and original slot0 remains byte-identical. A reader mutating the caller's Flow
and approval dictionary cannot replace their snapshots. Missing original
receipt blocks the gate; absent new approval prevents a second POST.
This is an owned two-operation browser continuation with a parent host, not a
second restarted worker, signed native GUI, external accounts, inferred resume
cursor, ephemeral-prefix recovery, general long multi-app work or a fair model
comparison. Same-control operations whose replay fingerprints remain identical
are still refused; hosts must not invent labels/expectations to bypass that rule.

Related regression (local evidence: `../reviews/browser-causal-continuation-b.log`):65 tests10.867s/
OK/no skips, source manifest (local evidence: `../reviews/browser-causal-continuation-b.json`)
process11.080s/source subset stable. Initial fixture context-manager errors and
the first suite's incorrect reservation assertion are preserved in the dated
verification status.
