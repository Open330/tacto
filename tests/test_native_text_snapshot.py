"""Pure AX result injection: no native imports, permissions, or GUI calls."""
from dataclasses import FrozenInstanceError
import unittest

from tacto.native_text_snapshot import read_complete_text, TextSnapshotError


class AXReads:
    def __init__(self, text):
        count = len(text.encode('utf-16-le')) // 2
        self.attributes = {'AXNumberOfCharacters': [(0, count), (0, count)],
                           'AXValue': [(0, text), (0, text)]}
        self.range_result = (0, text)
        self.ranges = []

    def attribute(self, name):
        return self.attributes[name].pop(0)

    def range(self, start, length):
        self.ranges.append((start, length))
        return self.range_result

    def read(self, budget=100):
        return read_complete_text(self.attribute, self.range, max_utf16_units=budget)


class NativeTextSnapshotTests(unittest.TestCase):
    def test_korean_emoji_and_empty_are_complete_immutable_witnesses(self):
        for text, units in [('한글😀', 4), ('', 0), ('a\n한', 3)]:
            with self.subTest(text=text):
                ax = AXReads(text)
                result = ax.read(budget=units)
                self.assertEqual((result.text, result.utf16_units), (text, units))
                self.assertEqual(ax.ranges, [(0, units)])
                self.assertEqual(ax.attributes, {'AXNumberOfCharacters': [], 'AXValue': []})
                with self.assertRaises(FrozenInstanceError):
                    result.text = 'changed'

    def test_pyobjc_integer_subclass_is_normalized_but_bool_is_not(self):
        class BridgedInteger(int):
            pass
        ax = AXReads('한글😀')
        ax.attributes['AXNumberOfCharacters'] = [(0, BridgedInteger(4)), (0, BridgedInteger(4))]
        result = ax.read()
        self.assertEqual(4, result.utf16_units)
        self.assertIs(type(result.utf16_units), int)

    def test_partial_or_different_full_range_is_rejected(self):
        for ranged in ('한', '다른😀'):
            ax = AXReads('한글😀')
            ax.range_result = (0, ranged)
            with self.subTest(ranged=ranged), self.assertRaises(TextSnapshotError):
                ax.read()

    def test_bad_count_or_budget_stops_before_range(self):
        for count in (True, False, -1, 1.0, '1', None, 101):
            ax = AXReads('x')
            ax.attributes['AXNumberOfCharacters'][0] = (0, count)
            with self.subTest(count=count), self.assertRaises(TextSnapshotError):
                ax.read()
            self.assertEqual(ax.ranges, [])
        for budget in (True, -1, 1.0, None):
            ax = AXReads('')
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                ax.read(budget)
            self.assertEqual(ax.ranges, [])

    def test_initial_value_count_mismatch_and_invalid_unicode_stop_before_range(self):
        for value in ('', 'too long', '😀', '\ud800', None):
            ax = AXReads('x')
            ax.attributes['AXValue'][0] = (0, value)
            with self.subTest(value=repr(value)), self.assertRaises(TextSnapshotError):
                ax.read()
            self.assertEqual(ax.ranges, [])

    def test_every_read_failure_is_rejected_including_empty_range(self):
        for source in ('count-before', 'value-before', 'range', 'count-after', 'value-after'):
            ax = AXReads('')
            if source == 'range':
                ax.range_result = (-25212, '')
            else:
                key = 'AXValue' if source.startswith('value') else 'AXNumberOfCharacters'
                index = 0 if source.endswith('before') else 1
                ax.attributes[key][index] = (-25212, '' if key == 'AXValue' else 0)
            with self.subTest(source=source), self.assertRaises(TextSnapshotError):
                ax.read()

    def test_same_length_body_change_and_count_change_are_rejected(self):
        for changed in ('y', 'xx'):
            ax = AXReads('x')
            ax.attributes['AXValue'][1] = (0, changed)
            ax.attributes['AXNumberOfCharacters'][1] = (0, len(changed))
            with self.subTest(changed=changed), self.assertRaises(TextSnapshotError):
                ax.read()

    def test_callback_exception_or_malformed_result_never_becomes_empty_text(self):
        def raises(*args):
            raise RuntimeError('AX unavailable')
        with self.assertRaises(TextSnapshotError):
            read_complete_text(raises, raises, max_utf16_units=10)
        for result in (None, (0,), (False, 0), (0.0, 0), (0, None)):
            with self.subTest(result=result), self.assertRaises(TextSnapshotError):
                read_complete_text(lambda name: result, raises, max_utf16_units=10)


if __name__ == '__main__':
    unittest.main()
