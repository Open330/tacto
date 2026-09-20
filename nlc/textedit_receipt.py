"""Host-side, fresh-name TextEdit save receipts; no native backend selection."""
from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

from .artifact_receipt import ArtifactReceiptError
from .rtf_receipt import FreshRTFReceipt


TARGET = 'com.apple.TextEdit'
SNAPSHOT_CAPABILITY = 'evidence.document_snapshot'


class TextEditReceiptUnavailable(ArtifactReceiptError):
    """The driver cannot independently witness the document being saved."""


def _snapshot(driver, token=None):
    result = driver.document_snapshot(TARGET, document_token=token)
    if (not isinstance(result, dict) or result.get('type') != 'document_snapshot'
            or not isinstance(result.get('document_token'), str)
            or not result['document_token']
            or not isinstance(result.get('text'), str)
            or result.get('text_complete') is not True
            or result.get('source_format') != 'rtf'
            or 'document_path' not in result):
        raise TextEditReceiptUnavailable('complete supported document snapshot unavailable')
    if token is not None and result['document_token'] != token:
        raise ArtifactReceiptError('document identity changed')
    path = result['document_path']
    if path is not None and (not isinstance(path, str) or not os.path.isabs(path)
                             or '\0' in path or os.path.realpath(path) != path):
        raise TextEditReceiptUnavailable('document path must be canonical absolute or None')
    # Do not retain a mutable backend response as the trusted expectation.
    snapshot = {key: result[key] for key in
            ('document_token', 'text', 'text_complete', 'document_path', 'source_format')}
    snapshot['save_panel'] = deepcopy(result.get('save_panel'))
    return snapshot


class TextEditReceipt:
    """One predetermined new RTF path containing the original exact plain text.

    Only canonical ~/Downloads is supported. Rich-text formatting/attachments
    are not certified. The backend owns and validates the opaque document token
    across observations, process restarts, window replacement, and connections.
    """
    def __init__(self, driver, *, target, directory, filename,
                 timeout_seconds=3.0, poll_interval=.02):
        if target != TARGET:
            raise ValueError('TextEdit receipt requires explicit com.apple.TextEdit target')
        if (SNAPSHOT_CAPABILITY not in (getattr(driver, 'capabilities', ()) or ())
                or not callable(getattr(driver, 'document_snapshot', None))):
            raise TextEditReceiptUnavailable('driver lacks evidence.document_snapshot')
        directory = os.fspath(directory)
        expected_directory = str((Path.home() / 'Downloads').resolve())
        if directory != expected_directory:
            raise ValueError('only the canonical Downloads directory is supported')
        if (not isinstance(filename, str) or not filename.endswith('.rtf')
                or filename in ('.rtf', '.', '..') or '/' in filename
                or '\\' in filename or '\0' in filename):
            raise ValueError('filename must be an explicit single-component .rtf name')
        self._driver = driver
        self._initial = _snapshot(driver)
        self._expected_panel = dict(document_token=self._initial['document_token'],
                                    directory=directory, filename=filename, format='rtf')
        self._receipt = FreshRTFReceipt(directory, filename, self._initial['text'],
                                        document_path=self._after_document_path,
                                        timeout_seconds=timeout_seconds,
                                        poll_interval=poll_interval)

    @property
    def commit_expected(self):
        return self._receipt.commit_expected

    def _current(self):
        current = _snapshot(self._driver, self._initial['document_token'])
        if current['text'] != self._initial['text']:
            raise ArtifactReceiptError('document text changed after save preparation')
        return current

    def prepare_commit(self, event):
        if event.get('target') != TARGET:
            raise ArtifactReceiptError('commit belongs to a different application')
        action = event.get('action')
        obs_id = event.get('obs_id')
        if (not isinstance(obs_id, str) or not obs_id.strip()
                or not isinstance(action, dict)
                or set(action) != {'name', 'element_index'}
                or action['name'] != 'click'
                or type(action['element_index']) is not int
                or action['element_index'] < 0):
            raise ArtifactReceiptError('save requires an observation-bound button click')
        current = self._current()
        if (current['document_path'] != self._initial['document_path']
                or current['source_format'] != self._initial['source_format']):
            raise ArtifactReceiptError('source document changed before commit')
        panel = current['save_panel']
        if (not isinstance(panel, dict) or any(
                panel.get(key) != value for key, value in self._expected_panel.items())):
            raise ArtifactReceiptError('save panel document, destination, name, or format mismatch')
        control = panel.get('commit_control')
        # The backend must derive this from its live native element map, not
        # from the requested action or a label such as "Save". This gate does
        # not replace native identity/settings revalidation at dispatch.
        if (not isinstance(control, dict) or control.get('obs_id') != obs_id
                or control.get('enabled') is not True
                or control.get('action') != action
                or type(control.get('action', {}).get('element_index')) is not int):
            raise ArtifactReceiptError('action is not the observed save-panel commit control')
        return self._receipt.prepare_commit(event)

    def _after_document_path(self):
        # FreshRTFReceipt checks the returned path against its predetermined path.
        return self._current()['document_path']

    def after_commit(self, event, driver_result, context):
        return self._receipt.after_commit(event, driver_result, context)


def make_textedit_receipt(driver, *, target, directory, filename,
                          timeout_seconds=3.0, poll_interval=.02):
    """Explicit host parameters only: never infer a destination from a flow name."""
    return TextEditReceipt(driver, target=target, directory=directory, filename=filename,
                           timeout_seconds=timeout_seconds, poll_interval=poll_interval)
