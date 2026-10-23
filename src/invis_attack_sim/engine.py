"""Discrete-event simulation of the simplified model, without a time-step grid.

Event phases: world clocks (0), combat (1), arrivals (2), spatial events (3),
collection (4), decisions (5), encounter timer creation (6). Random heap tie keys avoid
team-ID precedence. Action and motion generations invalidate interrupted work.
"""

from dataclasses import dataclass, replace
import heapq
import itertools
import random
import math

from .config import Config
from .results import Attack, Trial
from .spatial import EPS, distance, threshold_time

MODEL_VERSION = "m2.1"


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
    episode: int | None = None
    counted: int | None = None
    raid_kind: str | None = None
    approach_deadline: float | None = None
    fight: int | None = None


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
        self.episodes = itertools.count(1)
        self.fights: dict[int, tuple[int, int]] = {}
        self.spatial_pending = False
        self.spatial_generation = 0
        self.bed_loss_time: float | None = None

    def record(self, kind: str, **data) -> None:
        if self.trace is not None:
            self.trace.append(dict(time=self.time, kind=kind, **data))

    def schedule(self, time: float, phase: int, kind: str, player: int = -1,
                 version: int = -1, data=None) -> None:
        if time < self.time:
            raise RuntimeError("cannot schedule an event in the past")
        heapq.heappush(self.queue, (time, phase, self.rng.random(), next(self.sequence),
                                    kind, player, version, data))

    def run(self) -> Trial:
        self.schedule(self.next_spawn, 0, "spawn")
        for tier, time in enumerate(self.c.upgrade_times, 1):
            self.schedule(time, -1, "upgrade", data=tier)
        for p in self.players:
            self.schedule(0, 5, "decide", p.team, p.version)
        while self.queue and self.result is None:
            time, _, _, _, kind, team, version, data = heapq.heappop(self.queue)
            if self.c.max_time is not None and time > self.c.max_time:
                self.time = self.c.max_time
                self.finish("time_guard")
                break
            self.time = time
            if team >= 0 and self.players[team].version != version:
                continue
            self.events_processed += 1
            self.dispatch(kind, team, data)
            if self.validate:
                self.audit()
            self.check_stop()
            if self.result is None and self.events_processed >= self.c.max_events:
                self.finish("event_guard")
        if self.result is None:
            self.finish("no_events")
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
        elif kind == "spatial":
            self.spatial_pending = False
            self.sync_spatial()
        elif kind == "spatial_tick":
            if data == self.spatial_generation:
                self.request_spatial()
        elif kind == "break":
            self.resolve_break(self.players[team])
        elif kind == "fight":
            self.resolve_fight(data)
        elif kind == "respawn":
            p = self.players[team]
            p.alive = True
            p.route, p.x = p.team, 1.0
            p.action = "idle"
            self.record("respawn", player=team)
            self.schedule(self.time, 5, "decide", team, p.version)
            self.request_spatial()
        else:
            raise RuntimeError(f"unknown event {kind}")

    def finish(self, reason: str) -> None:
        if self.result is None:
            self.result = Trial(self.trial_id, tuple(self.attacks), self.time, reason,
                                self.bed_loss_time)

    def check_stop(self) -> None:
        own = self.players[self.c.our_team]
        if own.eliminated:
            self.finish("eliminated")
        elif sum(not p.eliminated for p in self.players) <= 1:
            self.finish("game_end")

    def at_middle(self, p: Player) -> bool:
        return p.alive and p.fight is None and p.motion is None and p.route is None

    def position(self, p: Player) -> tuple[int | None, float]:
        motion = p.motion
        if motion is None:
            return p.route, p.x
        fraction = min(1.0, max(0.0, (self.time - motion.start) / (motion.end - motion.start)))
        return motion.route, motion.start_x + fraction * (motion.end_x - motion.start_x)

    def middle_changed(self) -> None:
        if not self.refresh_pending:
            self.refresh_pending = True
            self.schedule(self.time, 4, "refresh")
        if not self.pairs_pending:
            self.pairs_pending = True
            self.schedule(self.time, 6, "sync_pairs")

    def wake_idle(self) -> None:
        for p in self.players:
            if p.alive and p.action == "idle":
                self.schedule(self.time, 5, "decide", p.team, p.version)

    def action_weights(self, p: Player) -> list[float]:
        collect, normal, invis = self.c.weights(self.time)
        if not p.bed:
            scaled_invis = self.c.no_bed_invis_odds_multiplier * invis
            denominator = normal + scaled_invis
            if denominator:
                attack = normal + invis
                normal, invis = attack * normal / denominator, attack * scaled_invis / denominator
        return [collect, normal, invis]

    def failure_death_chance(self, p: Player) -> float:
        return self.c.death_chance * (1.0 if p.bed else self.c.no_bed_failure_death_multiplier)

    def decide(self, p: Player) -> None:
        if not p.alive or p.action != "idle":
            return
        normal_targets = [q.team for q in self.players if q.team != p.team and not q.eliminated
                          and (q.bed or (q.alive and self.reachable(p, q)))]
        invis_targets = normal_targets
        weights = self.action_weights(p)
        if not normal_targets:
            weights[1] = 0
        if not invis_targets or p.emeralds < self.c.potion_cost:
            weights[2] = 0
        if sum(weights) == 0:
            return  # Retry at the next world event, without spinning at this time.
        action = self.rng.choices(("collect", "normal", "invis"), weights=weights)[0]
        p.version += 1
        p.action = action
        p.raid_kind = action if action != "collect" else None
        p.episode = next(self.episodes) if p.raid_kind else None
        p.counted = None
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
        if p.target is not None and not self.players[p.target].bed:
            self.start_pursuit(p)
        else:
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
            p.route, p.x = (None, 0.0) if end_x == 0 else (route, end_x)
            self.schedule(self.time, 5, "continue", p.team, p.version, continuation)
            self.request_spatial()
            return
        building = not self.bridges[route] and abs(end_x - start_x) == 1
        duration = abs(end_x - start_x) * (self.c.build_time if building else self.c.walk_time)
        token = next(self.sequence)
        p.route, p.x = route, start_x
        p.motion = Motion(route, start_x, end_x, self.time, self.time + duration, token, building, continuation)
        self.record("move", player=p.team, route=route, start_x=start_x, end_x=end_x,
                    duration=duration, building=building)
        self.schedule(self.time + duration, 2, "arrival", p.team, p.version, token)
        if was_middle:
            self.middle_changed()
        self.request_spatial()

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
        if motion.continuation not in ("collect", "track"):
            self.schedule(self.time, 5, "continue", p.team, p.version, motion.continuation)
        self.request_spatial()

    def target_valid(self, p: Player) -> bool:
        if p.target is None or self.players[p.target].eliminated:
            return False
        target = self.players[p.target]
        return target.bed or (target.alive and self.reachable(p, target))

    def continue_action(self, p: Player, continuation: str) -> None:
        if not p.alive:
            return
        if continuation == "home":
            p.action, p.target = "idle", None
            self.schedule(self.time, 5, "decide", p.team, p.version)
        elif continuation == "return_middle":
            self.move(p, p.team, 1.0, "home")
        elif continuation == "raid_middle":
            if not self.target_valid(p):
                self.return_home(p)
            else:
                if not self.players[p.target].bed:
                    self.start_pursuit(p)
                else:
                    if p.action == "invis":
                        self.drink(p)
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
        if p.fight is not None:
            raise RuntimeError("cannot retreat during unresolved combat")
        route, x = self.position(p)
        p.version += 1
        p.motion = None
        p.route, p.x = route, x
        p.action, p.target, p.potion = "return", None, False
        p.episode = p.counted = p.raid_kind = p.approach_deadline = None
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
                self.schedule(p.wait_cap, 4, "refresh", p.team, p.version)
            if crowded and p.contested_deadline is None:
                p.contested_deadline = min(p.wait_cap, self.time + self.rng.uniform(*self.c.contested_wait))
                self.schedule(p.contested_deadline, 4, "refresh", p.team, p.version)

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
        if self.rng.random() < self.failure_death_chance(self.players[loser]):
            self.kill(self.players[loser], winner)
        else:
            self.return_home(self.players[loser])
        self.middle_changed()

    def resolve_break(self, p: Player) -> None:
        if not p.alive or p.fight is not None or not self.target_valid(p):
            if p.alive and p.fight is None:
                self.return_home(p)
            return
        target = self.players[p.target]
        if not target.bed:
            self.start_pursuit(p)
            return
        chance = self.c.break_chance(self.time, invisible=p.action == "invis")
        success = self.rng.random() < chance
        self.record("break", player=p.team, target=target.team, success=success)
        if not success:
            self.fail_raid(p, target.team)
            return
        target.bed = False
        if target.team == self.c.our_team:
            self.bed_loss_time = self.time
        self.record("bed_destroyed", player=target.team)
        # Convert other en-route raids, preserving their episodes and potion clocks.
        self.invalidate_targets(target.team, exclude=p.team)
        self.wake_idle()
        eligible = (target.alive and self.reachable(p, target)
                    and (p.action != "invis" or p.potion or p.invis_until > self.time))
        if eligible and self.rng.random() < self.c.pursue_chance:
            self.start_pursuit(p)
        else:
            self.return_home(p)
        self.request_spatial()

    def drink(self, p: Player) -> None:
        if p.potion:
            p.potion = False
            p.invis_until = self.time + self.c.invis_duration
            self.record("drink", player=p.team, until=p.invis_until)
            self.request_spatial()

    def reachable(self, p: Player, target: Player) -> bool:
        # An incomplete bridge is unavailable to a new mobile pursuit. Retreat
        # may retrace a travelled partial leg, as in M1.
        positions = (self.position(p), self.position(target))
        return distance(*positions) <= EPS or all(
            route is None or x <= EPS or self.bridges[route] for route, x in positions)

    def start_pursuit(self, p: Player) -> None:
        if (not p.alive or p.target is None or not self.players[p.target].alive
                or not self.reachable(p, self.players[p.target])):
            self.return_home(p)
            return
        if p.action == "invis":
            self.drink(p)
            if p.invis_until <= self.time:
                self.return_home(p)
                return
        route, x = self.position(p)
        p.version += 1
        p.motion = None
        p.route, p.x = (None, 0.0) if x <= EPS else (route, x)
        if p.approach_deadline is None:
            p.approach_deadline = self.time + self.c.approach_timeout
        self.record("tracking", player=p.team, target=p.target,
                    episode=p.episode, deadline=p.approach_deadline)
        self.middle_changed()
        self.request_spatial()

    def request_spatial(self) -> None:
        if not self.spatial_pending:
            self.spatial_pending = True
            self.schedule(self.time, 3, "spatial")

    def velocity(self, p: Player) -> float:
        m = p.motion
        return (m.end_x - m.start_x) / (m.end - m.start) if m else 0.0

    def tracking(self, p: Player) -> bool:
        return p.alive and p.approach_deadline is not None and p.fight is None

    def orient_pursuer(self, p: Player) -> None:
        route, x = self.position(p)
        tr, tx = self.position(self.players[p.target])
        if distance((route, x), (tr, tx)) <= EPS:
            return
        if x <= EPS:
            route, x = tr, 0.0
            end_x = 1.0
        elif route != tr:
            end_x = 0.0
        else:
            end_x = 1.0 if tx > x else 0.0
        if p.motion and p.motion.route == route and p.motion.end_x == end_x:
            return
        # Only the direction uses the current position. Future motion is
        # predicted on this straight leg, then revised on the next state change.
        p.motion = None
        p.route, p.x = route, x
        self.move(p, route, end_x, "track")

    def count_attack(self, p: Player, *, followup: bool = False) -> None:
        if p.counted is not None:
            if followup:
                self.attacks[p.counted] = replace(self.attacks[p.counted], continued_pursuit=True)
            return
        p.counted = len(self.attacks)
        own = self.players[self.c.our_team]
        self.attacks.append(Attack(self.time, p.team, p.episode,
                                   "base" if own.bed else "pursuit", p.bed, followup))
        self.record("attack", player=p.team, episode=p.episode, own_bed=own.bed,
                    attacker_bed=p.bed)

    def sync_spatial(self) -> None:
        self.spatial_generation += 1
        generation = self.spatial_generation
        self.check_stop()
        if self.result is not None:
            return
        # Validate approaches before counting or starting a fight. Do not wait
        # for a dead target's already-earned respawn.
        for p in self.players:
            if self.tracking(p):
                target = self.players[p.target]
                if (not target.alive or target.eliminated or not self.reachable(p, target)
                        or (p.action == "invis" and p.invis_until <= self.time)):
                    self.return_home(p)
        for p in self.players:
            if self.tracking(p):
                self.orient_pursuer(p)
        # All valid events at this instant are counted before contact pairing,
        # including an attacker that must subsequently withdraw as a third party.
        own = self.players[self.c.our_team]
        for p in self.players:
            if (p.alive and p.team != own.team and p.target == own.team
                    and p.episode is not None and p.invis_until > self.time):
                if own.bed:
                    route, x = self.position(p)
                    if (p.motion and route == own.team and self.velocity(p) > 0
                            and abs(x - self.c.boundary) <= EPS):
                        self.count_attack(p)
                elif own.alive and self.reachable(p, own):
                    if distance(self.position(p), self.position(own)) <= self.c.engagement_range / self.c.walk_time + EPS:
                        self.count_attack(p, followup=p.counted is not None and
                                          self.attacks[p.counted].stage == "base")
        candidates = [p for p in self.players if self.tracking(p)
                      and distance(self.position(p), self.position(self.players[p.target])) <= EPS]
        self.rng.shuffle(candidates)
        for p in candidates:
            if not self.tracking(p):
                continue
            target = self.players[p.target]
            if target.fight is not None:
                self.return_home(p)
            else:
                self.start_fight(p, target)
        for p in self.players:
            if self.tracking(p) and self.time >= p.approach_deadline:
                self.return_home(p)
        # Exact roots replace a time grid. All arrival/turn/action events request
        # a fresh prediction and invalidate this generation's future tick.
        times = []
        for p in self.players:
            if self.tracking(p):
                target = self.players[p.target]
                times.append(p.approach_deadline)
                if p.action == "invis":
                    times.append(p.invis_until)
                duration = min((q.motion.end - self.time for q in (p, target) if q.motion), default=math.inf)
                for radius in (0.0, self.c.engagement_range / self.c.walk_time):
                    dt = threshold_time(self.position(p), self.velocity(p),
                                        self.position(target), self.velocity(target), radius, duration)
                    if dt is not None:
                        times.append(self.time + dt)
            if (own.bed and p.alive and p.target == own.team and p.episode is not None
                    and p.counted is None and p.invis_until > self.time and p.motion
                    and p.motion.route == own.team and self.velocity(p) > 0):
                _, x = self.position(p)
                dt = (self.c.boundary - x) / self.velocity(p)
                if dt > EPS and self.time + dt <= p.motion.end + EPS:
                    times.append(self.time + dt)
        future = [t for t in times if t > self.time]
        if future:
            self.schedule(min(future), 3, "spatial_tick", data=generation)

    def start_fight(self, p: Player, target: Player) -> None:
        assert p.fight is None and target.fight is None
        assert distance(self.position(p), self.position(target)) <= EPS
        token = next(self.sequence)
        self.fights[token] = (p.team, target.team)
        for q in (p, target):
            route, x = self.position(q)
            q.version += 1
            q.motion = None
            q.route, q.x = (None, 0.0) if x <= EPS else (route, x)
            q.approach_deadline = None
            q.wait_cap = q.contested_deadline = None
            q.fight, q.action = token, "combat"
        # A defender's interrupted attack has ended. The initiator retains the
        # counted episode until combat ends, including cross-bed followup tags.
        target.episode = target.counted = target.target = target.raid_kind = None
        target.potion = False
        self.record("combat_start", player=p.team, target=target.team, token=token,
                    route=p.route, x=p.x)
        self.schedule(self.time + self.rng.uniform(*self.c.pursuit_time), 1, "fight", data=token)
        self.middle_changed()
        self.request_spatial()

    def resolve_fight(self, token: int) -> None:
        pair = self.fights.pop(token, None)
        if pair is None:
            return
        p, target = (self.players[i] for i in pair)
        for q in (p, target):
            q.fight = None
        if not p.alive or not target.alive:
            for q in (p, target):
                if q.alive:
                    self.return_home(q)
            return
        success = self.rng.random() < self.c.kill_chance
        self.record("combat_end", player=p.team, target=target.team, success=success)
        if success:
            self.kill(target, p.team)
            self.return_home(p)
        else:
            self.fail_raid(p, target.team)
            self.return_home(target)
        self.request_spatial()

    def fail_raid(self, p: Player, target: int) -> None:
        if self.rng.random() < self.failure_death_chance(p):
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
        p.episode = p.counted = p.raid_kind = p.approach_deadline = None
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

    def invalidate_targets(self, team: int, *, exclude: int | None = None) -> None:
        target = self.players[team]
        for p in self.players:
            if not p.alive or p.target != team or p.team == exclude or p.action == "combat":
                continue
            if target.eliminated or (not target.bed and not target.alive):
                self.return_home(p)
            elif not target.bed and p.approach_deadline is None:
                self.start_pursuit(p)
        self.request_spatial()

    def audit(self) -> None:
        assert len(self.stock) == 2 and all(0 <= s <= self.c.generator_cap for s in self.stock)
        for p in self.players:
            assert p.emeralds >= 0
            assert not (p.alive and p.eliminated)
            assert not p.eliminated or not p.bed
            assert not p.potion or p.action == "invis"
            assert p.alive or p.motion is None
            assert p.target is None or p.target != p.team
            if p.fight is not None:
                assert p.alive and p.action == "combat" and p.motion is None
                assert p.team in self.fights[p.fight]
            if self.tracking(p):
                assert p.action in ("normal", "invis") and p.target is not None
                assert not p.motion or not p.motion.building
            if p.motion:
                assert p.motion.start <= self.time <= p.motion.end
                assert 0 <= p.motion.start_x <= 1 and 0 <= p.motion.end_x <= 1
        for token, pair in self.fights.items():
            a, b = (self.players[i] for i in pair)
            assert a.fight == b.fight == token
            assert distance(self.position(a), self.position(b)) <= EPS
        assert len({a.episode for a in self.attacks}) == len(self.attacks)


def simulate(config: Config, *, validate: bool = False) -> list[Trial]:
    rng = random.Random(config.seed)
    return [Game(config, rng, i, validate=validate).run() for i in range(config.trials)]
