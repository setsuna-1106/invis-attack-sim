import unittest

from invis_attack_sim.config import Config
from invis_attack_sim.results import Trial, cdf_distance, histogram


class ConfigResultsTests(unittest.TestCase):
    def test_policy_endpoints(self):
        self.assertEqual(Config().weights(0), (0.8, 0.15999999999999998, 0.039999999999999994))
        for actual, expected in zip(Config().weights(1800), (0.3, 0.21, 0.49)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(Config(horizon=60).weights(60), Config().weights(60))

    def test_reject_invalid_inputs(self):
        for kwargs in ({"trials": 0}, {"seed": True}, {"walk_time": float('nan')},
                       {"potion_cost": 0}, {"boundary": 1}, {"death_chance": 1.1},
                       {"horizon": 1801}, {"bin_width": 7}, {"spawn_periods": [1, 2]},
                       {"encounter_wait": [5, 2]}, {"upgrade_times": [5, 5]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Config(**kwargs)

    def test_histogram_keeps_none_and_exact_right_edge(self):
        config = Config(horizon=10)
        trials = [Trial(0, 0, 0, 'attack'), Trial(1, 5, 5, 'attack'),
                  Trial(2, 10, 10, 'attack'), Trial(3, None, 2, 'bed_destroyed')]
        rows = histogram(trials, config)
        self.assertEqual([r['count'] for r in rows], [1, 2])
        self.assertAlmostEqual(sum(r['density_per_s'] * 5 for r in rows) + 0.25, 1)

    def test_exact_cdf_detects_within_bin_difference(self):
        a = [Trial(0, 1, 1, 'attack'), Trial(1, None, 10, 'horizon')]
        b = [Trial(0, 2, 2, 'attack'), Trial(1, None, 10, 'horizon')]
        self.assertEqual(cdf_distance(a, b), 0.5)
        self.assertEqual(cdf_distance(a, a), 0)


if __name__ == '__main__':
    unittest.main()
