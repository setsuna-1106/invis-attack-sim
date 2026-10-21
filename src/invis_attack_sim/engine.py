"""Discrete-event simulation of the simplified model, without a time-step grid.

Event phases: world clocks (0), combat (1), motion/crossings (2), collection
(3), decisions (4), encounter timer creation (5). Random heap tie keys avoid
team-ID precedence. Action and motion generations invalidate interrupted work.
"""

from dataclasses import dataclass
import heapq
import itertools
import random

from .config import Config
from .results import Attack, Trial

MODEL_VERSION = "m1.2"


@dataclass
class Motion:
    route: int
    start_x: float
    end_x: float
    start: float
    end: float
    token: int
    building: bool
    continuation: str
    attack_recorded: bool = False


@dataclass
class Player:
    team: int
    bed: bool = True
    alive: bool = True
    eliminated: bool = False
    emeralds: int = 0
    action: str = "idle"
    target: int | None = None
    version: int = 0
    route: int | None = None
    x: float = 1.0
    motion: Motion | None = None
    invis_until: float = 0.0
    potion: bool = False
    wait_cap: float | None = None
    contested_deadline: float | None = None


class Game:
    def __init__(self, config: Config, rng: random.Random, trial_id: int = 0,
                 *, trace: bool = False, validate: bool = False):
        self.c = config
        self.rng = rng
        self.trial_id = trial_id
        self.time = 0.0
        self.players = [Player(i, route=i) for i in range(4)]
        self.bridges = [False] * 4
        self.stock = [0, 0]
        self.tier = 0
        self.next_spawn = config.spawn_periods[0]
        self.queue: list[tuple] = []
        self.sequence = itertools.count()
        self.pairs: dict[tuple[int, int], int] = {}
        self.refresh_pending = False
        self.pairs_pending = False
        self.result: Trial | None = None
        self.attacks: list[Attack] = []
        self.trace = [] if trace else None
        self.validate = validate
        self.events_processed = 0

    def record(self, kind: str, **data) -> None:
        if self.trace is not None:
            self.trace.append(dict(time=self.time, kind=kind, **data))

    def schedule(self, time: float, phase: int, kind: str, player: int = -1,
                 version: int = -1, data=None) -> None:
        if time < self.time:
            raise RuntimeError("cannot schedule an event in the past")
        if time <= self.c.horizon:
            heapq.heappush(self.queue, (time, phase, self.rng.random(), next(self.sequence),
                                        kind, player, version, data))

    def run(self) -> Trial:
        self.schedule(self.next_spawn, 0, "spawn")
        for tier, time in enumerate(self.c.upgrade_times, 1):
            self.schedule(time, -1, "upgrade", data=tier)
        for p in self.players:
            self.schedule(0, 4, "decide", p.team, p.version)
        while self.queue and self.result is None:
            time, _, _, _, kind, team, version, data = heapq.heappop(self.queue)
            self.time = time
            if team >= 0 and self.players[team].version != version:
                continue
            self.events_processed += 1
            self.dispatch(kind, team, data)
            if self.validate:
                self.audit()
            self.check_stop()
        if self.result is None:
            self.time = self.c.horizon
            self.finish("horizon")
        return self.result

    def dispatch(self, kind: str, team: int, data) -> None:
        if kind == "spawn":
            self.stock = [min(self.c.generator_cap, value + 1) for value in self.stock]
            self.record("spawn", stock=list(self.stock), tier=self.tier)
            self.next_spawn = self.time + self.c.spawn_periods[self.tier]
            self.schedule(self.next_spawn, 0, "spawn")
            self.middle_changed()
            self.wake_idle()
        elif kind == "upgrade":
            self.tier = data
            self.record("upgrade", tier=data)
        elif kind == "refresh":
            self.refresh_pending = False
            self.refresh_collectors()
        elif kind == "sync_pairs":
            self.pairs_pending = False
            self.sync_pairs()
        elif kind == "encounter":
            self.encounter(*data)
        elif kind == "decide":
            self.decide(self.players[team])
        elif kind == "arrival":
            self.arrive(self.players[team], data)
        elif kind == "continue":
            self.continue_action(self.players[team], data)
        elif kind == "cross":
            self.cross(self.players[team], data)
        elif kind == "break":
            self.resolve_break(self.players[team])
        elif kind == "pursuit":
            self.resolve_pursuit(self.players[team])
        elif kind == "respawn":
            p = self.players[team]
            p.alive = True
            p.route, p.x = p.team, 1.0
            p.action = "idle"
            self.record("respawn", player=team)
            self.schedule(self.time, 4, "decide", team, p.version)
        else:
            raise RuntimeError(f"unknown event {kind}")

    def finish(self, reason: str) -> None:
        if self.result is None:
            self.result = Trial(self.trial_id, tuple(self.attacks), self.time, reason)

    def check_stop(self) -> None:
        own = self.players[self.c.our_team]
        if not own.bed:
            self.finish("bed_destroyed")
        elif own.eliminated:
            self.finish("eliminated")
        elif sum(not p.eliminated for p in self.players) <= 1:
            self.finish("game_end")

    def at_middle(self, p: Player) -> bool:
        return p.alive and p.motion is None and p.route is None

    def position(self, p: Player) -> tuple[int | None, float]:
        motion = p.motion
        if motion is None:
            return p.route, p.x
        fraction = min(1.0, (self.time - motion.start) / (motion.end - motion.start))
        return motion.route, motion.start_x + fraction * (motion.end_x - motion.start_x)

    def middle_changed(self) -> None:
        if not self.refresh_pending:
            self.refresh_pending = True
            self.schedule(self.time, 3, "refresh")
        if not self.pairs_pending:
            self.pairs_pending = True
            self.schedule(self.time, 5, "sync_pairs")

    def wake_idle(self) -> None:
        for p in self.players:
            if p.alive and p.action == "idle":
                self.schedule(self.time, 4, "decide", p.team, p.version)

    def decide(self, p: Player) -> None:
        if not p.alive or p.action != "idle":
            return
        normal_targets = [q.team for q in self.players if q.team != p.team and not q.eliminated]
        invis_targets = [team for team in normal_targets if self.players[team].bed]
        weights = list(self.c.weights(self.time))
        if not normal_targets:
            weights[1] = 0
        if not invis_targets or p.emeralds < self.c.potion_cost:
            weights[2] = 0
        if sum(weights) == 0:
            return  # Retry at the next world event, without spinning at this time.
        action = self.rng.choices(("collect", "normal", "invis"), weights=weights)[0]
        p.version += 1
        p.action = action
        p.wait_cap = p.contested_deadline = None
        if action == "collect":
            p.target = None
        else:
            p.target = self.rng.choice(invis_targets if action == "invis" else normal_targets)
            if action == "invis":
                p.emeralds -= self.c.potion_cost
                p.potion = True
                self.record("purchase", player=p.team, emeralds=p.emeralds)
        self.record("action", player=p.team, action=action, target=p.target)
        self.move(p, p.team, 0.0, "collect" if action == "collect" else "raid_middle")

    def move(self, p: Player, route: int, end_x: float, continuation: str) -> None:
        was_middle = self.at_middle(p)
        current_route, start_x = self.position(p)
        if current_route is None:
            start_x = 0.0
        elif current_route != route:
            raise RuntimeError("movement must pass through the middle")
        if abs(end_x - start_x) < 1e-12:
            p.motion = None
            self.schedule(self.time, 4, "continue", p.team, p.version, continuation)
            return
        building = not self.bridges[route] and abs(end_x - start_x) == 1
        duration = abs(end_x - start_x) * (self.c.build_time if building else self.c.walk_time)
        token = next(self.sequence)
        p.route, p.x = route, start_x
        p.motion = Motion(route, start_x, end_x, self.time, self.time + duration, token, building, continuation)
        self.record("move", player=p.team, route=route, start_x=start_x, end_x=end_x,
                    duration=duration, building=building)
        self.schedule(self.time + duration, 2, "arrival", p.team, p.version, token)
        boundary = self.c.boundary
        if route == self.c.our_team and p.team != route and start_x <= boundary <= end_x:
            crossing = self.time + duration * (boundary - start_x) / (end_x - start_x)
            self.schedule(crossing, 2, "cross", p.team, p.version, token)
        if was_middle:
            self.middle_changed()

    def arrive(self, p: Player, token: int) -> None:
        motion = p.motion
        if not p.alive or motion is None or motion.token != token:
            return
        p.motion = None
        p.route, p.x = (None, 0.0) if motion.end_x == 0 else (motion.route, motion.end_x)
        if motion.building:
            self.bridges[motion.route] = True
            self.record("bridge", route=motion.route)
        if self.at_middle(p):
            self.middle_changed()
        if motion.continuation != "collect":
            self.schedule(self.time, 4, "continue", p.team, p.version, motion.continuation)

    def cross(self, p: Player, token: int) -> None:
        if (p.alive and p.motion is not None and p.motion.token == token
                and not p.motion.attack_recorded and p.invis_until > self.time):
            self.check_stop()
            if self.result is None:
                self.record("attack", player=p.team)
                p.motion.attack_recorded = True
                self.attacks.append(Attack(self.time, p.team))

    def target_valid(self, p: Player) -> bool:
        if p.target is None or self.players[p.target].eliminated:
            return False
        return p.action != "invis" or self.players[p.target].bed

    def continue_action(self, p: Player, continuation: str) -> None:
        if not p.alive:
            return
        if continuation == "home":
            p.action, p.target = "idle", None
            self.schedule(self.time, 4, "decide", p.team, p.version)
        elif continuation == "return_middle":
            self.move(p, p.team, 1.0, "home")
        elif continuation == "raid_middle":
            if not self.target_valid(p):
                self.return_home(p)
            else:
                if p.action == "invis":
                    p.potion = False
                    p.invis_until = self.time + self.c.invis_duration
                    self.record("drink", player=p.team, until=p.invis_until)
                self.move(p, p.target, 1.0, "raid_base")
        elif continuation == "raid_base":
            if not self.target_valid(p):
                self.return_home(p)
            elif self.players[p.target].bed:
                self.schedule(self.time + self.rng.uniform(*self.c.break_time), 1,
                              "break", p.team, p.version)
            else:
                self.start_pursuit(p)
        else:
            raise RuntimeError(f"invalid continuation {continuation}")

    def return_home(self, p: Player) -> None:
        if not p.alive:
            return
        route, x = self.position(p)
        p.version += 1
        p.motion = None
        p.route, p.x = route, x
        p.action, p.target, p.potion = "return", None, False
        p.wait_cap = p.contested_deadline = None
        self.record("return", player=p.team, route=route, x=x)
        if route is None:
            self.move(p, p.team, 1.0, "home")
        elif route == p.team:
            self.move(p, route, 1.0, "home")
        else:
            self.move(p, route, 0.0, "return_middle")
        self.middle_changed()

    def refresh_collectors(self) -> None:
        present = [p for p in self.players if self.at_middle(p)]
        collectors = [p for p in present if p.action == "collect"]
        self.rng.shuffle(collectors)
        for p in collectors:
            amount = sum(self.stock)
            if amount:
                p.emeralds += amount
                self.stock = [0, 0]
                self.record("pickup", player=p.team, amount=amount, emeralds=p.emeralds)
        # Reevaluate after each departure: presence is the current island state.
        for p in collectors:
            crowded = any(q.team != p.team and self.at_middle(q) for q in self.players)
            if ((p.wait_cap is not None and self.time >= p.wait_cap)
                    or (p.contested_deadline is not None and crowded and self.time >= p.contested_deadline)
                    or (not crowded and p.emeralds >= self.c.potion_cost)):
                self.return_home(p)
                continue
            if p.wait_cap is None:
                p.wait_cap = self.next_spawn
                self.schedule(p.wait_cap, 3, "refresh", p.team, p.version)
            if crowded and p.contested_deadline is None:
                p.contested_deadline = min(p.wait_cap, self.time + self.rng.uniform(*self.c.contested_wait))
                self.schedule(p.contested_deadline, 3, "refresh", p.team, p.version)

    def sync_pairs(self) -> None:
        present = [p.team for p in self.players if self.at_middle(p)]
        wanted = set(itertools.combinations(present, 2))
        for pair in list(self.pairs):
            if pair not in wanted:
                del self.pairs[pair]
        for pair in sorted(wanted):
            if pair not in self.pairs:
                token = next(self.sequence)
                self.pairs[pair] = token
                self.schedule(self.time + self.rng.uniform(*self.c.encounter_wait), 1,
                              "encounter", data=(pair, token))

    def encounter(self, pair: tuple[int, int], token: int) -> None:
        if self.pairs.get(pair) != token or not all(self.at_middle(self.players[i]) for i in pair):
            return
        del self.pairs[pair]
        loser = self.rng.choice(pair)
        winner = pair[1] if loser == pair[0] else pair[0]
        self.record("encounter", loser=loser, winner=winner)
        if self.rng.random() < self.c.death_chance:
            self.kill(self.players[loser], winner)
        else:
            self.return_home(self.players[loser])
        self.middle_changed()

    def resolve_break(self, p: Player) -> None:
        if not p.alive or not self.target_valid(p):
            if p.alive:
                self.return_home(p)
            return
        target = self.players[p.target]
        if not target.bed:
            self.start_pursuit(p)
            return
        chance = self.c.invis_break_chance if p.action == "invis" else self.c.normal_break_chance
        success = self.rng.random() < chance
        self.record("break", player=p.team, target=target.team, success=success)
        if not success:
            self.fail_raid(p, target.team)
            return
        target.bed = False
        p.action = "pursuit"
        self.record("bed_destroyed", player=target.team)
        self.check_stop()
        self.invalidate_targets(target.team)
        self.wake_idle()
        if self.rng.random() < self.c.pursue_chance:
            # A successful invis raid remains valid as a pursuit after bed loss.
            p.action = "pursuit"
            self.start_pursuit(p)
        else:
            self.return_home(p)

    def start_pursuit(self, p: Player) -> None:
        if p.target is None or not self.players[p.target].alive:
            self.return_home(p)
            return
        self.schedule(self.time + self.rng.uniform(*self.c.pursuit_time), 1,
                      "pursuit", p.team, p.version)

    def resolve_pursuit(self, p: Player) -> None:
        if not p.alive:
            return
        if p.target is None or not self.players[p.target].alive or self.players[p.target].eliminated:
            self.return_home(p)
            return
        target = p.target
        success = self.rng.random() < self.c.kill_chance
        self.record("pursuit", player=p.team, target=target, success=success)
        if success:
            self.kill(self.players[target], p.team)
            self.return_home(p)
        else:
            self.fail_raid(p, target)

    def fail_raid(self, p: Player, target: int) -> None:
        if self.rng.random() < self.c.death_chance:
            self.kill(p, target)
        else:
            self.return_home(p)

    def kill(self, p: Player, recipient: int | None = None) -> None:
        if not p.alive:
            return
        amount = p.emeralds
        if recipient is not None and recipient != p.team:
            receiver = self.players[recipient]
            if receiver.alive and not receiver.eliminated:
                receiver.emeralds += amount
        p.version += 1
        p.alive = False
        p.motion = None
        p.emeralds = 0
        p.potion = False
        p.invis_until = 0
        p.wait_cap = p.contested_deadline = None
        p.target = None
        self.record("death", player=p.team, recipient=recipient, emeralds=amount, respawn=p.bed)
        if p.bed:
            p.action = "respawning"
            self.schedule(self.time + self.c.respawn_time, 1, "respawn", p.team, p.version)
        else:
            p.eliminated = True
            p.action = "eliminated"
            self.invalidate_targets(p.team)
        self.middle_changed()
        self.wake_idle()
        self.check_stop()

    def invalidate_targets(self, team: int) -> None:
        target = self.players[team]
        for p in self.players:
            if (p.alive and p.target == team
                    and (target.eliminated or (p.action == "invis" and not target.bed))):
                self.return_home(p)

    def audit(self) -> None:
        assert len(self.stock) == 2 and all(0 <= s <= self.c.generator_cap for s in self.stock)
        for p in self.players:
            assert p.emeralds >= 0
            assert not (p.alive and p.eliminated)
            assert not p.eliminated or not p.bed
            assert not p.potion or p.action == "invis"
            assert p.alive or p.motion is None
            assert p.target is None or p.target != p.team
            if p.motion:
                assert p.motion.start <= self.time <= p.motion.end
                assert 0 <= p.motion.start_x <= 1 and 0 <= p.motion.end_x <= 1


def simulate(config: Config, *, validate: bool = False) -> list[Trial]:
    rng = random.Random(config.seed)
    return [Game(config, rng, i, validate=validate).run() for i in range(config.trials)]
