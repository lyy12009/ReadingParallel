"""Evidence/report consistency only; no product behavior is mocked."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    'acceptance_replay', Path(__file__).with_name('acceptance_replay.py'))
REPLAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPLAY)


class AcceptanceEvidenceTests(unittest.TestCase):
    def test_expected_gap_is_recorded_for_each_case(self):
        rows = [{'case': case, 'known_word_editor_gap_observed': True} for case in 'ABC']
        self.assertEqual(REPLAY.check_ia01_evidence(rows, True), [])
        self.assertTrue(all(row['ia01']['status'] == 'MATCH' for row in rows))
        self.assertTrue(all(row['ia01']['expected_observed'] is True for row in rows))

    def test_missing_reproduction_is_mismatch_not_silent_pass(self):
        rows = [{'case': 'A', 'known_word_editor_gap_observed': False}]
        messages = REPLAY.check_ia01_evidence(rows, True)
        self.assertEqual(rows[0]['ia01']['status'], 'MISMATCH')
        self.assertFalse(rows[0]['ia01']['observed'])
        self.assertEqual(len(messages), 1)
        self.assertIn('IA-01', messages[0])
        self.assertIn('A', messages[0])
        self.assertIn('重新檢查', messages[0])

    def test_future_fixed_expectation_accepts_absence_but_not_reappearance(self):
        rows = [{'case': 'A', 'known_word_editor_gap_observed': False},
                {'case': 'B', 'known_word_editor_gap_observed': True}]
        messages = REPLAY.check_ia01_evidence(rows, False)
        self.assertEqual([row['ia01']['status'] for row in rows], ['MATCH', 'MISMATCH'])
        self.assertEqual(len(messages), 1)
        self.assertIn('B', messages[0])

    def test_missing_observation_does_not_count_as_observed_or_fixed(self):
        for value in [None, 0, 1, 'False']:
            with self.subTest(value=value):
                rows = [{'case': 'A', 'known_word_editor_gap_observed': value}]
                self.assertTrue(REPLAY.check_ia01_evidence(rows, False))
                self.assertEqual(rows[0]['ia01']['status'], 'MISMATCH')


if __name__ == '__main__':
    unittest.main()
