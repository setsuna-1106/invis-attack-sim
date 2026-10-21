import unittest

from invis_attack_sim.config import Config
from invis_attack_sim.results import Attack, Trial, count_curve_distance, count_stability, histogram


class ConfigResultsTests(unittest.TestCase):
    def test_policy_endpoints(self):
        for time, expected in ((0, (.8, .16, .04)), (1800, (.3, .21, .49))):
            for actual, value in zip(Config().weights(time), expected):
                self.assertAlmostEqual(actual, value)
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
        trials = [Trial(0, (Attack(0, 1), Attack(5, 1)), 10, 'horizon'),
                  Trial(1, (Attack(10, 2),), 10, 'horizon'),
                  Trial(2, (), 2, 'bed_destroyed')]
        rows = histogram(trials, config)
        self.assertEqual([r['count'] for r in rows], [1, 2])
        self.assertAlmostEqual(sum(r['density_per_s'] * 5 for r in rows), 1)
        self.assertEqual(trials[0].event_time, 0)

    def test_repeats_within_one_bin_count_separately_but_hit_only_once(self):
        config = Config(horizon=5)
        trials = [Trial(0, (Attack(1, 1), Attack(2, 2), Attack(3, 1)), 5, 'horizon'),
                  Trial(1, (), 5, 'horizon')]
        row = histogram(trials, config)[0]
        self.assertEqual(row['count'], 3)
        self.assertEqual(row['hit_trials'], 1)
        self.assertEqual(row['mean_count'], 1.5)
        self.assertEqual(row['hit_probability'], .5)
        self.assertEqual(row['density_per_s'], .3)
        self.assertEqual(row['density_per_s'] * 5, 1.5)

    def test_stability_detects_later_attacks_even_with_identical_first_times(self):
        config = Config(horizon=10)
        a = [Trial(0, (Attack(1, 1),), 10, 'horizon')]
        b = [Trial(0, (Attack(1, 1), Attack(6, 1)), 10, 'horizon')]
        self.assertEqual(count_curve_distance(a, b, config), 1)
        report = count_stability(a, b, config, seed=42, repetitions=20)
        self.assertFalse(report['passed'])
        self.assertEqual(report['distance'], 1)
        self.assertEqual(report['threshold_95'], 0)

    def test_bootstrap_resamples_whole_histories_and_is_reproducible(self):
        config = Config(horizon=10)
        a = [Trial(0, (Attack(1, 1), Attack(6, 1)), 10, 'horizon'),
             Trial(1, (), 10, 'horizon')]
        report = count_stability(a, a, config, seed=42, repetitions=20)
        self.assertEqual(report, count_stability(a, a, config, seed=42, repetitions=20))
        self.assertTrue(report['passed'])
        self.assertEqual(report['distance'], 0)
        self.assertEqual(report['threshold_95'], 2)

    def test_reject_attack_after_stop_or_unordered(self):
        config = Config(horizon=10)
        for events in ((Attack(6, 1),), (Attack(3, 1), Attack(2, 1))):
            with self.assertRaises(ValueError):
                histogram([Trial(0, events, 5, 'bed_destroyed')], config)


if __name__ == '__main__':
    unittest.main()
