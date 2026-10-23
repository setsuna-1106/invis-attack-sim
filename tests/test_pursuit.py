"""Spatial/episode regressions for the user-confirmed M2 pursuit baseline."""
from dataclasses import replace
import random
import unittest
from unittest.mock import patch

from invis_attack_sim.config import Config
from invis_attack_sim.engine import Game, simulate
from test_engine import advance


class PursuitTests(unittest.TestCase):
    def game(self, **kwargs):
        g = Game(Config(pursuit_time=(5, 5), **kwargs), random.Random(42), trace=True, validate=True)
        g.bridges = [True] * 4
        g.decide = lambda p: None
        g.sync_pairs = lambda: None
        return g

    def attack(self, g, team=1, target=0, route=0, x=0, invisible=True):
        p = g.players[team]
        p.route, p.x = (None, 0) if x == 0 else (route, x)
        p.action = p.raid_kind = 'invis' if invisible else 'normal'
        p.target, p.episode = target, next(g.episodes)
        p.potion = invisible
        p.counted = None
        return p

    def mobile(self, g, **kwargs):
        g.players[0].bed = False
        p = self.attack(g, **kwargs)
        g.start_pursuit(p)
        return p, g.players[0]

    def test_static_target_counts_range_before_contact_then_dies(self):
        g = self.game(kill_chance=1)
        p, q = self.mobile(g)
        q.emeralds = 7
        advance(g, 7.5)
        self.assertEqual([(a.time, a.stage) for a in g.attacks], [(7.5, 'pursuit')])
        self.assertIsNone(q.fight)
        advance(g, 10)
        self.assertIsNotNone(q.fight)
        self.assertTrue(q.alive)
        advance(g, 15)
        self.assertTrue(q.eliminated)
        self.assertEqual(p.emeralds, 7)
        self.assertTrue(g.result.complete)

    def test_opposing_motion_contacts_at_midpoint(self):
        g = self.game()
        p, q = self.mobile(g)
        q.action = 'normal'
        g.move(q, 0, 0, 'collect')
        advance(g, 3.75)
        self.assertAlmostEqual(g.attacks[0].time, 3.75)
        advance(g, 5)
        self.assertAlmostEqual(q.x, .5)
        self.assertAlmostEqual(p.x, .5)
        self.assertIsNotNone(q.fight)

    def test_same_speed_same_direction_cannot_shrink_gap(self):
        g = self.game(walk_time=100, build_time=100, invis_duration=60)
        q = g.players[0]
        q.route, q.x, q.bed, q.action = 0, .2, False, 'normal'
        g.move(q, 0, 1, 'collect')
        p = self.attack(g)
        g.start_pursuit(p)
        advance(g, 29)
        self.assertAlmostEqual(g.position(q)[1] - g.position(p)[1], .2)
        self.assertIsNone(q.fight)
        advance(g, 30)
        self.assertEqual(p.action, 'return')
        self.assertEqual(g.attacks, [])

    def test_different_routes_pass_through_middle(self):
        g = self.game()
        p, q = self.mobile(g, route=1, x=1)
        advance(g, 10)
        self.assertEqual(g.position(p)[1], 0)
        advance(g, 17.5)
        self.assertEqual(g.attacks[0].time, 17.5)
        advance(g, 20)
        self.assertIsNotNone(q.fight)
        self.assertEqual(p.route, 0)

    def test_target_turn_replans_from_current_position(self):
        g = self.game()
        q = g.players[0]
        q.bed, q.route, q.x, q.action = False, 0, .6, 'normal'
        g.move(q, 0, 1, 'collect')
        p = self.attack(g)
        g.start_pursuit(p)
        advance(g, 4)
        g.move(q, 0, 0, 'collect')
        advance(g, 6.9)
        self.assertIsNone(q.fight)
        advance(g, 7)
        self.assertIsNotNone(q.fight)
        self.assertAlmostEqual(p.x, .7)
        self.assertEqual(p.approach_deadline, None)

    def test_expiry_retains_count_but_ends_approach(self):
        g = self.game(invis_duration=8)
        p, q = self.mobile(g)
        advance(g, 8)
        self.assertEqual(len(g.attacks), 1)
        self.assertEqual(p.action, 'return')
        self.assertIsNone(q.fight)
        self.assertTrue(p.alive)

    def test_expiry_at_contact_prevents_new_fight(self):
        g = self.game(invis_duration=10)
        p, q = self.mobile(g)
        advance(g, 10)
        self.assertEqual(p.action, 'return')
        self.assertIsNone(q.fight)
        self.assertEqual(len(g.attacks), 1)

    def test_expiry_during_combat_does_not_cancel_outcome(self):
        g = self.game(invis_duration=12, kill_chance=1)
        p, q = self.mobile(g)
        advance(g, 15)
        self.assertTrue(q.eliminated)
        self.assertEqual(len(g.attacks), 1)

    def test_exact_approach_deadline_contact_allowed_if_effect_valid(self):
        for invisible in (False, True):
            g = self.game(walk_time=30, invis_duration=35)
            p, q = self.mobile(g, invisible=invisible)
            advance(g, 30)
            self.assertIsNotNone(q.fight)
            self.assertEqual(p.action, 'combat')

    def test_cross_bed_loss_keeps_episode_and_followup_tag(self):
        g = self.game()
        p = self.attack(g)
        g.continue_action(p, 'raid_middle')
        advance(g, 3)
        episode, until = p.episode, p.invis_until
        self.assertEqual(len(g.attacks), 1)
        g.players[0].bed = False
        g.invalidate_targets(0)
        self.assertEqual(p.episode, episode)
        self.assertEqual(p.invis_until, until)
        self.assertEqual(p.approach_deadline, 33)
        advance(g, 7.5)
        self.assertEqual(len(g.attacks), 1)
        self.assertEqual(g.attacks[0].stage, 'base')
        self.assertTrue(g.attacks[0].continued_pursuit)

    def test_inside_range_drink_reentry_and_new_episode(self):
        g = self.game()
        q = g.players[0]
        q.bed, q.x = False, .8
        p = self.attack(g, x=.6)
        g.start_pursuit(p)
        advance(g, 0)
        self.assertEqual(g.attacks[0].time, 0)
        q.x = 1
        g.request_spatial()
        advance(g, .5)
        q.x = .8
        g.request_spatial()
        advance(g, .5)
        self.assertEqual(len(g.attacks), 1)
        g.return_home(p)
        p = self.attack(g, x=.6)
        g.start_pursuit(p)
        advance(g, .5)
        self.assertEqual(len(g.attacks), 2)
        self.assertNotEqual(g.attacks[0].episode, g.attacks[1].episode)

    def test_simultaneous_attackers_count_before_third_party_retreat(self):
        g = self.game()
        g.players[0].bed = False
        for team in (1, 2):
            p = self.attack(g, team=team, x=1)
            g.start_pursuit(p)
        advance(g, 0)
        self.assertEqual(len(g.attacks), 2)
        self.assertEqual(len(g.fights), 1)
        self.assertEqual(sorted(p.action for p in g.players[1:3]), ['combat', 'return'])

    def test_mutual_contact_initiator_is_not_fixed_by_team(self):
        initiators = set()
        for seed in range(30):
            g = self.game(our_team=3)
            g.rng.seed(seed)
            for team, target in ((0, 1), (1, 0)):
                g.players[target].bed = False
                p = self.attack(g, team=team, target=target, x=.5, invisible=False)
                g.start_pursuit(p)
            advance(g, 0)
            initiators.add(next(e['player'] for e in g.trace if e['kind'] == 'combat_start'))
            g.audit()
        self.assertEqual(initiators, {0, 1})

    def test_combat_cancels_collection_and_old_middle_encounter(self):
        g = self.game()
        q = g.players[0]
        q.bed, q.route, q.x, q.action = False, None, 0, 'collect'
        g.stock = [4, 4]
        p = self.attack(g)
        g.schedule(0, 4, 'refresh')
        g.start_pursuit(p)
        advance(g, 0)
        self.assertEqual(q.action, 'combat')
        self.assertEqual(q.emeralds, 0)
        self.assertEqual(g.stock, [4, 4])
        self.assertFalse(g.at_middle(q))

    def test_unreachable_target_cannot_be_chased_or_chosen(self):
        g = self.game()
        g.bridges[0] = False
        g.players[0].bed = False
        p = self.attack(g)
        g.start_pursuit(p)
        self.assertEqual(p.action, 'return')
        self.assertFalse(g.bridges[0])
        self.assertEqual(g.attacks, [])

    def test_target_becomes_unreachable_ends_episode(self):
        g = self.game()
        p, q = self.mobile(g)
        advance(g, 1)
        q.route, q.x = 2, .5
        g.bridges[2] = False
        g.request_spatial()
        advance(g, 1)
        self.assertEqual(p.action, 'return')
        self.assertIsNone(p.episode)

    def test_no_bed_targets_share_uniform_pool_and_drink_immediately(self):
        g = Game(Config(collection_start=0, invis_share_start=1), random.Random(42), trace=True)
        g.bridges = [True] * 4
        p = g.players[1]
        p.emeralds = 2
        g.players[0].bed = g.players[2].bed = False
        g.players[2].alive = False
        with patch.object(g.rng, 'choice', return_value=0) as choose:
            g.decide(p)
        self.assertEqual(choose.call_args.args[0], [0, 3])
        self.assertEqual(p.emeralds, 0)
        self.assertFalse(p.potion)
        self.assertEqual(p.invis_until, 30)
        self.assertEqual(p.approach_deadline, 30)

    def test_target_death_cancels_tracking_but_bed_raid_can_continue(self):
        g = self.game()
        p = self.attack(g, target=2)
        g.kill(g.players[2])  # Still has a bed; base remains a valid target.
        self.assertEqual(p.target, 2)
        g.players[2].bed = False
        g.invalidate_targets(2)
        self.assertEqual(p.action, 'return')
        advance(g, 5)
        self.assertTrue(g.players[2].alive)  # Respawn right was earned at death.

    def test_statistics_bin_width_does_not_change_trajectories(self):
        c = Config(trials=30)
        self.assertEqual(simulate(c, validate=True), simulate(replace(c, bin_width=60), validate=True))

    def test_no_bed_threshold_at_expiry_does_not_count(self):
        g = self.game(invis_duration=7.5)
        p, q = self.mobile(g)
        advance(g, 7.5)
        self.assertEqual(g.attacks, [])
        self.assertEqual(p.action, 'return')

    def test_bed_loss_does_not_stop_observation_and_guards_are_incomplete(self):
        g = self.game()
        g.players[0].bed = False
        g.check_stop()
        self.assertIsNone(g.result)
        c = Config(trials=2, max_events=10)
        self.assertTrue(all(not t.complete and t.stop_reason == 'event_guard' for t in simulate(c)))


if __name__ == '__main__':
    unittest.main()
