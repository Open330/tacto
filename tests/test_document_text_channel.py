"""The signed channel must refuse the same things the in-process reader does.

No app, no helper process, no screen: these drive the Python end of the L2
surface with a recording `_send`, and read the launcher's Swift source for the
rules it enforces before a message is ever forwarded. What they check is that a
malformed request is refused at every end rather than at one of them — a caller
that gets a different answer from the same request depending on which transport
it used has no contract at all.
"""
from __future__ import annotations

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class _Recorder:
    """A helper driver whose channel records instead of speaking."""

    def __init__(self, reply=None):
        self.sent = []
        self.reply = reply or {'type': 'document_text', 'document_token': 'abc',
                               'text': '', 'text_complete': True,
                               'document_path': None}

    def _send(self, message):
        self.sent.append(message)
        return self.reply


def _driver(reply=None):
    from nlc.helper import InheritedHelperDriver
    driver = InheritedHelperDriver.__new__(InheritedHelperDriver)
    driver.capabilities = {'evidence.document_text.path_selector', 'observe.document_token'}
    recorder = _Recorder(reply)
    driver._send = recorder._send
    return driver, recorder


class DocumentTextChannelTests(unittest.TestCase):
    def test_old_helper_cannot_silently_ignore_scope(self):
        driver, recorder = _driver()
        driver.capabilities = set()
        self.assertEqual(driver.document_text('com.apple.TextEdit', document_path=os.path.realpath('/tmp/owned.rtf'))['effect'], 'not_started')
        self.assertEqual(driver.observe('com.apple.TextEdit', document_token='abc')['effect'], 'not_started')
        self.assertEqual(recorder.sent, [])
    def test_path_selector_is_sent_once_and_invalid_combinations_never_sent(self):
        path = os.path.realpath('/tmp/nlc-owned.rtf')
        driver, recorder = _driver()
        driver.document_text('com.apple.TextEdit', document_path=path)
        self.assertEqual(recorder.sent[0]['document_path'], path)
        for kwargs in ({'document_path':'relative'}, {'document_path':path, 'document_token':'abc'}):
            driver, recorder = _driver()
            self.assertEqual(driver.document_text('com.apple.TextEdit', **kwargs)['effect'], 'not_started')
            self.assertEqual(recorder.sent, [])

    def test_scoped_observe_sends_token_and_refuses_foreign_target(self):
        driver, recorder = _driver({'type':'observation'})
        driver._last_action_at = 0
        driver.observe('com.apple.TextEdit', document_token='abc')
        self.assertEqual(recorder.sent, [{'type':'observe', 'target':'com.apple.TextEdit', 'require_window':True, 'document_token':'abc'}])
        recorder.sent.clear()
        for token in ('', 1, 'x'*129):
            self.assertEqual(driver.observe('com.apple.TextEdit', document_token=token)['effect'], 'not_started')
        self.assertEqual(driver.observe('foreign', document_token='abc')['effect'], 'not_started')
        self.assertEqual(recorder.sent, [])
    def test_every_transport_exposes_it(self):
        from nlc.helper import HelperDriver, InheritedHelperDriver
        for cls in (HelperDriver, InheritedHelperDriver):
            with self.subTest(transport=cls.__name__):
                self.assertTrue(hasattr(cls, 'document_text'),
                                'a transport without document_text answers a '
                                'request the others answer')

    def test_a_well_formed_request_is_sent_once_and_whole(self):
        driver, recorder = _driver()
        driver.document_text('com.apple.TextEdit')
        self.assertEqual(1, len(recorder.sent))
        self.assertEqual({'type': 'document_text', 'target': 'com.apple.TextEdit',
                          'max_utf16_units': 1000000}, recorder.sent[0])

    def test_a_token_travels_only_when_there_is_one(self):
        driver, recorder = _driver()
        driver.document_text('com.apple.TextEdit', 'deadbeef')
        self.assertEqual('deadbeef', recorder.sent[0]['document_token'])
        driver, recorder = _driver()
        driver.document_text('com.apple.TextEdit', None)
        self.assertNotIn('document_token', recorder.sent[0])

    def test_malformed_budgets_never_reach_the_channel(self):
        # True is an int in Python, 1.0 is not one, and a gigabyte is a promise
        # this process will not keep.
        for budget in (True, False, -1, 1.0, '1000', 1000001, None):
            with self.subTest(budget=budget):
                driver, recorder = _driver()
                answer = driver.document_text('com.apple.TextEdit',
                                              max_utf16_units=budget)
                self.assertEqual('error', answer['type'])
                self.assertEqual('not_started', answer['effect'])
                self.assertEqual([], recorder.sent,
                                 'a refused request must not be sent anyway')

    def test_malformed_tokens_never_reach_the_channel(self):
        for token in ('', b'abc', 123, True, []):
            with self.subTest(token=token):
                driver, recorder = _driver()
                answer = driver.document_text('com.apple.TextEdit', token)
                self.assertEqual('error', answer['type'])
                self.assertEqual('not_started', answer['effect'])
                self.assertEqual([], recorder.sent)

    def test_the_reply_is_passed_through_unchanged(self):
        reply = {'type': 'document_text', 'document_token': 'tok', 'text': 'hi',
                 'text_complete': True, 'document_path': '/tmp/x.rtf'}
        driver, _ = _driver(reply)
        self.assertEqual(reply, driver.document_text('com.apple.TextEdit'))


class LauncherVettingSourceTests(unittest.TestCase):
    """The launcher is the gate; these read what it actually enforces.

    Source-level because the launcher is Swift and this suite must run without
    building it. A rule that exists only in the helper is a rule the launcher
    would have forwarded.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'launcher/tacto.swift')) as stream:
            cls.source = stream.read()

    def test_document_text_is_an_allowed_type(self):
        allowed = re.search(r'let allowedTypes: Set<String> = \[(.*?)\]',
                            self.source, re.S).group(1)
        self.assertIn('"document_text"', allowed)

    def test_its_fields_are_top_level_allowed(self):
        keys = re.search(r'let topLevelKeys: Set<String> = \[(.*?)\]',
                         self.source, re.S).group(1)
        self.assertIn('"document_token"', keys)
        self.assertIn('"max_utf16_units"', keys)

    def test_the_launcher_pins_the_target_and_the_ceiling(self):
        self.assertIn('document_text 는 TextEdit 만 지원합니다', self.source)
        self.assertIn('let maxDocumentUTF16Units = 1_000_000', self.source)
        self.assertIn('n.intValue <= maxDocumentUTF16Units', self.source)
        self.assertIn('n.intValue >= 0', self.source)

    def test_the_launcher_refuses_unknown_fields_on_this_message(self):
        self.assertIn('document_text 에 허용되지 않은 필드', self.source)


class HelperSourceTests(unittest.TestCase):
    """What the helper promises about identity, tokens and what it will not claim."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'helper/TactoHelper.swift')) as stream:
            cls.source = stream.read()

    def test_tokens_are_bounded_and_reset_per_connection(self):
        self.assertIn('let documentTokenLimit = 32', self.source)
        self.assertIn('documentTokens.removeAll()', self.source)
        self.assertIn('if documentTokens.count > documentTokenLimit '
                      '{ documentTokens.removeFirst() }', self.source)

    def test_identity_is_pid_and_launch_date_together(self):
        self.assertIn('app.launchDate', self.source)
        self.assertIn('pinned!.pid != p || pinned!.stamp != stamp', self.source)

    def test_only_no_value_means_unsaved(self):
        self.assertIn('if err == AXError(rawValue: -25212) { return nil }', self.source)

    def test_it_declares_document_text_and_not_document_snapshot(self):
        # Scoped to the capability LIST. The prose above it says the word while
        # explaining what is deliberately not claimed, and a check that reads
        # comments is checking the wrong thing.
        declared = re.search(r'let helperCapabilities: \[String\] = \[(.*?)\n\]',
                             self.source, re.S).group(1)
        self.assertIn('"evidence.document_text"', declared)
        self.assertNotIn('"evidence.document_snapshot"', declared)

    def test_traversal_fails_rather_than_truncating(self):
        self.assertIn('document traversal budget exceeded', self.source)

    def test_the_authority_is_the_whole_netloc_not_just_the_host(self):
        # file://user@localhost/path and file://localhost:80/path both have an
        # acceptable host and an unacceptable authority.
        self.assertIn('parts.user == nil, parts.password == nil, parts.port == nil',
                      self.source)

    def test_percent_decoding_is_strict_rather_than_substituting(self):
        self.assertIn('func documentDecodePercent', self.source)
        self.assertIn("String(bytes: bytes, encoding: .utf8)", self.source)

    def test_escapes_are_checked_on_the_raw_url_before_foundation_repairs_them(self):
        # Foundation rewrites a bare `%` to `%25` on the way into
        # URLComponents, so a check that reads percentEncodedPath is reading a
        # string that has already been repaired. Measured: file:///…/%zz and
        # file:///…/% were both accepted until this moved earlier.
        self.assertIn('func documentEscapesAreWellFormed', self.source)
        body = self.source.split('func documentURLPath(', 1)[1].split('\nfunc ', 1)[0]
        raw_check = body.index('documentEscapesAreWellFormed(text)')
        components = body.index('URLComponents(string: text)')
        self.assertLess(raw_check, components,
                        'the escape check must run before URLComponents sees '
                        'the string, not after')

    def test_a_well_formed_escape_for_a_literal_percent_stays_valid(self):
        # %25zz is an escape for '%' followed by two ordinary letters, and
        # rejecting it would be rejecting a legal path.
        checker = self.source.split('func documentEscapesAreWellFormed(', 1)[1]
        checker = checker.split('\nfunc ', 1)[0]
        self.assertIn('index += 3', checker,
                      'a valid escape must consume all three characters, or '
                      '%25zz reads as % followed by zz')

    def test_a_number_is_asked_of_corefoundation(self):
        self.assertIn('CFGetTypeID(number) == CFBooleanGetTypeID()', self.source)
        self.assertIn('CFNumberIsFloatType(number as CFNumber)', self.source)
        self.assertIn('documentIsWholeNumber(asked)', self.source)

    def test_a_missing_file_is_still_a_canonical_path(self):
        # realpath(3) needs the file to exist and Python's realpath does not;
        # a document open on a deleted file must not answer differently here.
        self.assertIn('func documentIsCanonical', self.source)
        self.assertIn('if errno != ENOENT { return false }', self.source)

    def test_a_stop_lands_between_reads_not_only_at_the_door(self):
        self.assertIn('func documentCheckAbort', self.source)
        # Before every kind of AX read this path makes, and once more before
        # anything is returned.
        self.assertGreaterEqual(self.source.count('try documentCheckAbort()'), 5)
        for site in ('func documentRequired', 'func documentPath',
                     'func documentTextRange'):
            body = self.source.split(site, 1)[1][:300]
            self.assertIn('try documentCheckAbort()', body,
                          f'{site} reads AX without checking the stop first')

    def test_an_abort_is_reported_as_an_abort(self):
        self.assertIn('"error": "aborted_by_user"', self.source)

    def test_the_text_is_cross_checked_and_reread(self):
        self.assertIn('AXValue and AXStringForRange disagree', self.source)
        self.assertIn('AX text changed while taking the snapshot', self.source)


if __name__ == '__main__':
    unittest.main()
