from dataclasses import replace
import heapq
import random
import unittest

from invis_attack_sim.config import Config
from invis_attack_sim.engine import Game, simulate
from invis_attack_sim.results import histogram


def advance(game, until):
    """Advance a controlled fixture without starting a fresh match."""
    while game.queue and game.queue[0][0] <= until and game.result is None:
        time, _, _, _, kind, team, version, data = heapq.heappop(game.queue)
        game.time = time
        if team >= 0 and game.players[team].version != version:
            continue
        game.dispatch(kind, team, data)
        game.audit()
    if game.result is None:
        game.time = until


class EngineTests(unittest.TestCase):
    def game(self, **kwargs):
        game = Game(Config(**kwargs), random.Random(42), trace=True, validate=True)
        # Focused fixtures stop at the end of an action rather than drawing another.
        game.decide = lambda player: None
        return game

    def at_middle(self, game, team, action='collect', emeralds=0):
        p = game.players[team]
        p.route, p.x, p.action, p.emeralds = None, 0.0, action, emeralds
        return p

    def test_first_build_includes_crossing_and_is_shared(self):
        game = self.game()
        p = game.players[1]
        p.action = 'normal'
        game.move(p, 1, 0, 'collect')
        self.assertEqual(p.motion.end, 60)
        advance(game, 59)
        self.assertFalse(game.bridges[1])
        advance(game, 60)
        self.assertTrue(game.bridges[1])
        game.return_home(p)
        self.assertEqual(p.motion.end, 70)
        other = self.at_middle(game, 2, 'normal')
        other.target = 1
        game.move(other, 1, 1, 'raid_base')
        self.assertEqual(other.motion.end, 70)

    def test_builder_death_invalidates_completion(self):
        game = self.game()
        p = game.players[1]
        p.action = 'collect'
        game.move(p, 1, 0, 'collect')
        game.time = 30
        p.emeralds = 3
        game.kill(p, 2)
        advance(game, 60)
        self.assertFalse(game.bridges[1])
        self.assertEqual(game.players[2].emeralds, 3)
        self.assertTrue(p.alive)
        self.assertEqual(p.emeralds, 0)
        self.assertEqual(p.route, 1)

    def test_return_from_partial_route_uses_position(self):
        game = self.game()
        game.bridges = [True] * 4
        p = self.at_middle(game, 1, 'normal')
        game.move(p, 2, 1, 'raid_base')
        advance(game, 4)
        game.return_home(p)
        self.assertAlmostEqual(p.motion.start_x, .4)
        self.assertEqual(p.motion.end, 8)
        advance(game, 8)
        self.assertEqual(p.motion.end, 18)
        advance(game, 18)
        self.assertEqual((p.route, p.x, p.action), (1, 1, 'idle'))

    def test_drink_then_cross_existing_and_unbuilt_bridge(self):
        for built, expected in ((True, 2.5), (False, 15)):
            with self.subTest(built=built):
                game = self.game()
                game.bridges[0] = built
                p = self.at_middle(game, 1, 'invis')
                p.target, p.potion = 0, True
                game.continue_action(p, 'raid_middle')
                self.assertFalse(p.potion)
                self.assertEqual(p.invis_until, 30)
                advance(game, expected)
                self.assertEqual(game.result.event_time, expected)
                self.assertEqual(game.result.attacker, 1)

    def test_bed_loss_and_expiry_precede_equal_time_crossing(self):
        game = self.game(invis_duration=2.5)
        game.bridges[0] = True
        p = self.at_middle(game, 1, 'invis')
        p.target = 0
        game.continue_action(p, 'raid_middle')
        advance(game, 2.5)
        self.assertIsNone(game.result)
        game = self.game(normal_break_chance=1)
        game.bridges[0] = True
        p = self.at_middle(game, 1, 'invis')
        p.target = 0
        game.continue_action(p, 'raid_middle')
        breaker = game.players[2]
        breaker.action, breaker.target = 'normal', 0
        game.schedule(2.5, 1, 'break', 2, breaker.version)
        advance(game, 2.5)
        self.assertEqual(game.result.stop_reason, 'bed_destroyed')
        self.assertIsNone(game.result.event_time)

    def test_horizon_crossing_counts(self):
        game = self.game(horizon=5)
        game.bridges[0] = True
        game.time = 2.5
        p = self.at_middle(game, 1, 'invis')
        p.target = 0
        game.continue_action(p, 'raid_middle')
        advance(game, 5)
        self.assertEqual(game.result.event_time, 5)

    def test_solo_collects_all_even_if_already_enough(self):
        game = self.game()
        p = self.at_middle(game, 1, emeralds=2)
        game.stock = [4, 4]
        game.refresh_collectors()
        self.assertEqual(p.emeralds, 10)
        self.assertEqual(game.stock, [0, 0])
        self.assertEqual(p.action, 'return')

    def test_solo_waits_at_most_one_spawn(self):
        game = self.game(potion_cost=10)
        p = self.at_middle(game, 1)
        game.refresh_collectors()
        self.assertEqual(p.wait_cap, 56)
        game.schedule(56, 0, 'spawn')
        advance(game, 56)
        self.assertEqual(p.emeralds, 2)
        self.assertEqual(p.action, 'return')

    def test_contested_enough_does_not_leave_early_or_redraw(self):
        game = self.game(contested_wait=(10, 10))
        p = self.at_middle(game, 1, emeralds=10)
        other = self.at_middle(game, 2, 'normal')
        game.refresh_collectors()
        self.assertEqual(p.action, 'collect')
        self.assertEqual(p.contested_deadline, 10)
        game.time = 2
        other.route = 2
        game.refresh_collectors()
        self.assertEqual(p.action, 'return')
        game = self.game(contested_wait=(10, 10), potion_cost=10)
        p = self.at_middle(game, 1)
        other = self.at_middle(game, 2, 'normal')
        game.refresh_collectors()
        other.route = 2
        game.time = 3
        game.refresh_collectors()
        other.route = None
        game.time = 6
        game.refresh_collectors()
        self.assertEqual(p.contested_deadline, 10)
        self.assertEqual(p.wait_cap, 56)

    def test_spawn_pickup_precedes_collection_cutoff(self):
        game = self.game(contested_wait=(56, 56))
        p = self.at_middle(game, 1)
        self.at_middle(game, 2, 'normal')
        game.refresh_collectors()
        game.schedule(56, 0, 'spawn')
        # Do not install encounters in this isolated collection fixture.
        game.sync_pairs = lambda: None
        advance(game, 56)
        self.assertEqual(p.emeralds, 2)
        self.assertEqual(p.action, 'return')

    def test_pickup_is_not_fixed_by_team_and_transit_cannot_pickup(self):
        winners = set()
        for seed in range(30):
            game = self.game()
            game.rng.seed(seed)
            self.at_middle(game, 0, 'normal')
            for team in (1, 2, 3):
                self.at_middle(game, team)
            game.stock = [4, 4]
            game.refresh_collectors()
            self.assertEqual(game.players[0].emeralds, 0)
            winners.add(next(p.team for p in game.players if p.emeralds))
        self.assertEqual(winners, {1, 2, 3})

    def test_spawn_clock_upgrades_cap_and_pickup_dont_reset(self):
        game = self.game(horizon=1800)
        game.schedule(56, 0, 'spawn')
        game.schedule(360, -1, 'upgrade', data=1)
        game.schedule(1080, -1, 'upgrade', data=2)
        advance(game, 359)
        self.assertEqual(game.stock, [4, 4])
        self.assertEqual(game.next_spawn, 392)
        advance(game, 360)
        self.assertEqual(game.next_spawn, 392)
        game.stock = [0, 0]
        advance(game, 392)
        self.assertEqual(game.stock, [1, 1])
        self.assertEqual(game.next_spawn, 432)
        advance(game, 1080)
        self.assertEqual(game.next_spawn, 1112)
        advance(game, 1112)
        self.assertEqual(game.next_spawn, 1140)
        self.assertEqual(game.stock, [4, 4])

    def test_encounter_leave_cancels_only_related_pairs(self):
        game = self.game()
        for team in (1, 2, 3):
            self.at_middle(game, team, 'normal')
        game.sync_pairs()
        unaffected = game.pairs[(2, 3)]
        stale_token = game.pairs[(1, 2)]
        game.return_home(game.players[1])
        game.sync_pairs()
        self.assertEqual(game.pairs, {(2, 3): unaffected})
        game.encounter((1, 2), stale_token)
        self.assertFalse(any(e['kind'] == 'encounter' for e in game.trace))

    def test_encounter_death_transfers_or_retreat_keeps_inventory(self):
        for chance in (0, 1):
            game = self.game(death_chance=chance)
            for team in (1, 2):
                self.at_middle(game, team, 'normal', emeralds=3)
            game.sync_pairs()
            game.encounter((1, 2), game.pairs[(1, 2)])
            if chance == 1:
                self.assertEqual(sorted(p.emeralds for p in game.players[1:3]), [0, 6])
                self.assertEqual(sum(p.alive for p in game.players), 3)
            else:
                self.assertEqual([p.emeralds for p in game.players[1:3]], [3, 3])
                self.assertTrue(all(p.alive for p in game.players))

    def test_death_locks_respawn_eligibility(self):
        game = self.game()
        p = game.players[1]
        game.kill(p)
        p.bed = False
        advance(game, 5)
        self.assertTrue(p.alive)
        game.kill(p)
        self.assertTrue(p.eliminated)
        self.assertFalse(p.alive)
        self.assertFalse(any(e[4] == 'respawn' for e in game.queue))

    def test_break_after_expiry_uses_invis_success_and_can_pursue(self):
        game = self.game(invis_break_chance=1, normal_break_chance=0, pursue_chance=1)
        p = game.players[1]
        p.action, p.target, p.invis_until = 'invis', 2, 5
        game.time = 40
        game.resolve_break(p)
        self.assertFalse(game.players[2].bed)
        self.assertEqual(p.target, 2)
        self.assertEqual(p.action, 'pursuit')
        self.assertTrue(any(e[4] == 'pursuit' for e in game.queue))

    def test_failed_break_ends_raid_and_resource_recipient_must_be_alive(self):
        game = self.game(normal_break_chance=0, death_chance=1)
        p = game.players[1]
        p.action, p.target, p.emeralds = 'normal', 2, 6
        game.players[2].alive = False
        game.resolve_break(p)
        self.assertFalse(p.alive)
        self.assertEqual(game.players[2].emeralds, 0)
        self.assertFalse(any(e[4] == 'break' for e in game.queue))

    def test_target_bed_loss_cancels_invis_but_not_normal_travel(self):
        game = self.game()
        game.bridges = [True] * 4
        for team, action in ((1, 'invis'), (3, 'normal')):
            p = game.players[team]
            p.action, p.target = action, 2
            game.move(p, team, 0, 'raid_middle')
        game.time = 4
        game.players[2].bed = False
        game.invalidate_targets(2)
        self.assertEqual(game.players[1].action, 'return')
        self.assertAlmostEqual(game.players[1].motion.end, 8)
        self.assertEqual(game.players[3].action, 'normal')

    def test_pursuit_does_not_freeze_target_and_kill_cancels_old_motion(self):
        game = self.game(kill_chance=1, pursuit_time=(5, 5))
        game.bridges = [True] * 4
        p = game.players[1]
        p.action, p.target = 'normal', 2
        target = game.players[2]
        target.bed, target.emeralds, target.action = False, 7, 'normal'
        game.move(target, 2, 0, 'raid_middle')
        game.start_pursuit(p)
        advance(game, 5)
        self.assertTrue(target.eliminated)
        self.assertIsNone(target.motion)
        self.assertEqual(p.emeralds, 7)
        advance(game, 10)
        self.assertFalse(target.alive)

    def test_actions_mask_invalid_targets_and_charge_one_potion(self):
        game = Game(Config(collection_start=0, collection_end=0,
                           invis_share_start=1, invis_share_end=1), random.Random(2), trace=True)
        p = game.players[1]
        game.decide(p)
        self.assertEqual(p.action, 'idle')
        p.emeralds = 6
        for team in (0, 2):
            game.players[team].bed = False
        game.decide(p)
        self.assertEqual(p.target, 3)
        self.assertEqual(p.emeralds, 4)
        self.assertTrue(p.potion)

    def test_full_runs_invariants_reproducibility_and_normalization(self):
        config = Config(trials=200)
        first = simulate(config, validate=True)
        self.assertEqual(first, simulate(config, validate=True))
        self.assertNotEqual(first, simulate(replace(config, seed=43)))
        none = sum(t.event_time is None for t in first) / len(first)
        self.assertAlmostEqual(sum(r['probability'] for r in histogram(first, config)) + none, 1)
        self.assertTrue(all(0 <= t.stop_time <= config.horizon for t in first))
        self.assertTrue(all(t.attacker != config.our_team for t in first if t.event_time is not None))

    def test_game_end_counts_respawning_team_as_not_eliminated(self):
        game = self.game()
        for team in (1, 2):
            p = game.players[team]
            p.bed = False
            game.kill(p)
        game.kill(game.players[3])
        self.assertIsNone(game.result)
        game.players[3].bed = False
        advance(game, 5)
        game.kill(game.players[3])
        self.assertEqual(game.result.stop_reason, 'game_end')


if __name__ == '__main__':
    unittest.main()
