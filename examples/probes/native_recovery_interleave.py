"""Trusted evaluation hook: switch apps only after a verified parent step.

No resume authority or serializable checkpoint is created here. Child failures
permanently stop this hook; a spent boundary must never repeat its Save.
"""
class OwnedTextEditInterleave:
    def __init__(self, *, target, driver, receiving, run_child):
        self.target, self.driver = target, driver
        self.receiving, self.run_child = receiving, run_child
        self.rows = []
        self._spent = set()
        self._failed = False

    def after_wait(self, target, predicate, response):
        if self._failed:
            raise RuntimeError('owned_interleave_failed')
        if target != self.target or response.get('type') != 'observation':
            return response
        before = dict(self.receiving())
        stage = before.get('stage')
        if stage not in ('8', '14', '20') or stage in self._spent:
            return response
        if before.get('sheet') != 'false' or not predicate(response['tree']):
            raise RuntimeError('owned_interleave_boundary_unverified')
        self._spent.add(stage)  # reserve before any child work, even on failure
        row = {'parent_stage': int(stage), 'before': before, 'phase': 'attempted'}
        self.rows.append(row)
        try:
            child = self.run_child()
            row['child'] = child
            if (type(child) is not dict or child.get('passed') is not True
                    or child.get('cleanup_status') != 'closed'
                    or child.get('exact_saved_text') is not True
                    or 'evidence_error' not in child
                    or child.get('evidence_error') is not None
                    or child.get('save_dispatch_type') != 'acted'):
                raise RuntimeError('owned_interleave_child_unverified')
            after = dict(self.receiving()); row['after'] = after
            if after != before:
                raise RuntimeError('owned_interleave_parent_changed')
            # Child calls replaced the helper's global observation. Re-read the
            # parent on that channel; never return its pre-child obs_id/index.
            fresh = self.driver.observe(self.target, 'tree_full')
            if (type(fresh) is not dict or fresh.get('type') != 'observation'
                    or not isinstance(fresh.get('tree'), str)
                    or not predicate(fresh['tree'])):
                raise RuntimeError('owned_interleave_reobserve_unverified')
            confirmed = dict(self.receiving()); row['after_reobserve'] = confirmed
            if confirmed != before:
                raise RuntimeError('owned_interleave_parent_changed')
            row['phase'] = 'completed'
            return fresh
        except BaseException as error:
            self._failed = True
            row['phase'] = 'failed'; row['error_class'] = type(error).__name__
            raise
