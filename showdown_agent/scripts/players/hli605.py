# hli605.py
from __future__ import annotations

from typing import Dict, Optional, Tuple

from poke_env.battle import AbstractBattle
from poke_env.player import Player


# Gen 9 Ubers bulky offense team (unchanged)
team = """
Necrozma-Dusk-Mane @ Heavy-Duty Boots
Ability: Prism Armor
Tera Type: Steel
EVs: 252 HP / 4 Atk / 252 Def
Impish Nature
- Stealth Rock
- Sunsteel Strike
- Earthquake
- Morning Sun

Arceus-Fairy @ Pixie Plate
Ability: Multitype
Tera Type: Fairy
EVs: 252 HP / 4 Def / 252 Spe
Timid Nature
IVs: 0 Atk
- Judgment
- Calm Mind
- Recover
- Earth Power

Eternatus @ Black Sludge
Ability: Pressure
Tera Type: Poison
EVs: 252 HP / 4 SpA / 252 SpD
Calm Nature
IVs: 0 Atk
- Sludge Bomb
- Flamethrower
- Toxic Spikes
- Recover

Giratina-Origin @ Griseous Core
Ability: Levitate
Tera Type: Ghost
EVs: 248 HP / 252 Def / 8 SpD
Impish Nature
- Will-O-Wisp
- Hex
- Dragon Tail
- Rest

Great Tusk @ Leftovers
Ability: Protosynthesis
Tera Type: Water
EVs: 252 HP / 252 Def / 4 Spe
Impish Nature
- Rapid Spin
- Headlong Rush
- Close Combat
- Knock Off

Chi-Yu @ Choice Scarf
Ability: Beads of Ruin
Tera Type: Fire
EVs: 4 Def / 252 SpA / 252 Spe
Timid Nature
IVs: 0 Atk
- Flamethrower
- Dark Pulse
- Overheat
- Ruination
"""


class CustomAgent(Player):
    """
    Improved heuristic-based agent:
    - Chooses moves via enhanced expected damage with situational modifiers
    - Utility logic for hazards, removal, status, phazing, setup, and recovery
    - Switching based on defensive danger and offensive potential of candidates
    """

    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)

    def choose_move(self, battle: AbstractBattle):
        try:
            # If we must switch (e.g., no moves), pick best switch
            if not battle.available_moves:
                switch_target = self._pick_best_switch(battle)
                if switch_target is not None:
                    return self.create_order(switch_target)
                return self.choose_random_switch(battle)

            active = battle.active_pokemon
            opponent = battle.opponent_active_pokemon

            # 0) Hazard removal if our side is pressured
            clear_move = self._pick_hazard_clear_move(battle)
            if clear_move is not None:
                # Don't clear if opponent has significantly more hazards than us (unless Sticky Web on our side)
                if self._should_clear_now(battle, clear_move):
                    return self.create_order(clear_move)

            # 1) Hazard setup early if safe
            hazard_move = self._pick_hazard_setup_move(battle)
            if hazard_move is not None:
                if self._danger_multiplier(active, opponent) <= 1.25:
                    return self.create_order(hazard_move)

            # 2) Status spreading: Will-O-Wisp vs physical threats when safe
            wow_move = self._pick_wow_if_good(battle)
            if wow_move is not None:
                return self.create_order(wow_move)

            # 3) Recovery when low and safe
            recovery_move = self._pick_recovery_move(battle)
            if recovery_move is not None:
                hp_frac = active.current_hp_fraction or 1.0
                if hp_frac < 0.5 and self._danger_multiplier(active, opponent) <= 1.0:
                    return self.create_order(recovery_move)

            # 4) Setup when safe (Calm Mind on Arceus-Fairy)
            boosting_move = self._pick_boost_move(battle)
            if boosting_move is not None:
                # Only if we are quite safe and early/mid game
                if self._danger_multiplier(active, opponent) <= 1.0 and battle.turn <= 12:
                    return self.create_order(boosting_move)

            # 5) Phaze when opponent is boosted and we have hazards up
            phaze_move = self._pick_phaze_move(battle)
            if phaze_move is not None:
                return self.create_order(phaze_move)

            # 6) Offensive choice: best damage / utility (includes Ruination and Knock Off bias)
            best_move, best_score = self._pick_best_move_with_util(battle)

            # 7) Consider a defensive switch if in big danger and offense is poor
            if self._should_switch_from_position(battle, best_score):
                switch_target = self._pick_best_switch(battle)
                if switch_target is not None:
                    return self.create_order(switch_target)

            # 8) Use the selected best move
            if best_move is not None:
                return self.create_order(best_move)

            # Fallbacks
            if battle.available_moves:
                return self.create_order(battle.available_moves[0])
            if battle.available_switches:
                return self.create_order(battle.available_switches[0])
            return self.choose_random_move(battle)
        except Exception:
            # Robustness fallback
            if battle.available_moves:
                return self.choose_random_move(battle)
            return self.choose_random_switch(battle)

    # -----------------------
    # Move selection helpers
    # -----------------------
    def _pick_best_move_with_util(self, battle: AbstractBattle):
        """
        Score both damaging and key utility moves and pick the highest value.
        Returns (move, score).
        """
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        best_move = None
        best_score = float("-inf")

        # Precompute some context
        opp_hp_frac = opponent.current_hp_fraction if opponent and opponent.current_hp_fraction is not None else 1.0
        our_danger = self._danger_multiplier(active, opponent)

        for move in battle.available_moves:
            mid = (move.id or "").lower()

            # Utility scoring first
            util_score = 0.0

            # Recovery and Boost handled earlier; keep tiny score here
            if mid in {"recover", "morningsun", "roost", "slackoff", "softboiled", "rest", "milkdrink", "strengthsap"}:
                # Defer to earlier decision; small residual value
                util_score = 30.0

            elif mid == "defog":
                util_score = self._defog_value(battle)

            elif mid == "rapidspin":
                # Avoid into Ghost-type (spin block)
                if not self._opponent_has_type(opponent, "GHOST"):
                    util_score = self._rapid_spin_value(battle)
                else:
                    util_score = 0.0

            elif mid == "stealthrock":
                util_score = self._sr_value(battle)

            elif mid == "toxicspikes":
                util_score = self._tspikes_value(battle, opponent)

            elif mid == "willowisp":
                # If target already statused or Fire-type, 0
                if opponent and opponent.status is None and not self._opponent_has_type(opponent, "FIRE"):
                    phys_lean = self._physical_lean(opponent)
                    util_score = 80.0 + 40.0 * phys_lean  # 80-120 scaling
                    # Avoid if we are in big danger
                    if our_danger >= 2.0:
                        util_score *= 0.7
                else:
                    util_score = 0.0

            elif mid == "knockoff":
                # Early/midgame item removal is great
                util_score = 85.0
                # If target already revealed no item (very rare), reduce; otherwise keep
                # In danger, de-prioritize slightly
                if our_danger >= 2.0:
                    util_score *= 0.85

            elif mid == "dragontail":
                # Great with hazards or vs boosts; avoid into Fairy
                if not self._opponent_has_type(opponent, "FAIRY"):
                    has_hazards = self._hazard_count(battle.opponent_side_conditions) > 0
                    opp_boosted = self._total_boost(opponent) >= 2
                    util_score = 0.0
                    if has_hazards:
                        util_score += 90.0
                    if opp_boosted:
                        util_score += 80.0
                    # Avoid using DT if we're in huge danger
                    if our_danger >= 2.0:
                        util_score *= 0.7
                else:
                    util_score = 0.0

            elif mid == "ruination":
                # Half current HP; value tapers as HP drops
                acc = move.accuracy if move.accuracy is not None else 1.0
                util_score = 120.0 * opp_hp_frac * acc
                # Slightly prefer if coverage is otherwise poor
                if self._best_raw_damage_score(battle) < 130.0:
                    util_score *= 1.1

            elif mid == "calmmind":
                util_score = 0.0  # handled earlier

            # Damage scoring
            dmg_score = self._move_damage_score(move, active, opponent)

            # Synergy: Hex when opponent statused (power effectively 130)
            if mid == "hex" and opponent and opponent.status is not None:
                dmg_score *= 1.9  # 65 -> ~123-130 effective

            # KO nudge if move seems likely to KO (very naive)
            if opponent and opp_hp_frac <= 0.35 and self._type_effectiveness(move, opponent) >= 1.0:
                if (move.base_power or 0) >= 80:
                    dmg_score *= 1.2

            # Penalize heavy drops/recoil to preserve momentum
            if mid in {"overheat", "dracometeor", "leafstorm"}:
                dmg_score *= 0.88
            if mid in {"closecombat", "headlongrush"}:
                dmg_score *= 0.9

            # Pick max of utility and damage (keeps a unified scale)
            score = max(util_score, dmg_score)

            # Slight danger-driven tweak: prefer safer, accurate moves when in danger
            if our_danger >= 2.0 and move.accuracy is not None and move.accuracy < 0.85:
                score *= 0.92

            if score > best_score:
                best_score = score
                best_move = move

        return best_move, best_score

    def _pick_hazard_setup_move(self, battle: AbstractBattle):
        """
        Prefer hazard setup early if not established: Stealth Rock > Toxic Spikes.
        Only when we have the move and it's worthwhile by turn count.
        """
        if battle.turn > 8:
            return None

        opp_has_sr = self._has_side_condition(battle.opponent_side_conditions, "stealthrock")
        opp_has_tspikes = self._has_side_condition(battle.opponent_side_conditions, "toxicspikes")

        for move in battle.available_moves:
            mid = (move.id or "").lower()
            if mid == "stealthrock" and not opp_has_sr:
                return move
        # TSpikes second priority
        for move in battle.available_moves:
            mid = (move.id or "").lower()
            if mid == "toxicspikes" and not opp_has_tspikes:
                # Avoid setting if target is Steel/Poison (less immediate value), but still allow
                opponent = battle.opponent_active_pokemon
                if opponent and (self._opponent_has_type(opponent, "STEEL") or self._opponent_has_type(opponent, "POISON")):
                    continue
                return move
        return None

    def _pick_hazard_clear_move(self, battle: AbstractBattle):
        """
        Pick Defog or Rapid Spin when clearing hazards is valuable.
        """
        clear_move = None
        best_val = 0.0
        for move in battle.available_moves:
            mid = (move.id or "").lower()
            if mid == "defog":
                val = self._defog_value(battle)
                if val > best_val:
                    best_val = val
                    clear_move = move
            elif mid == "rapidspin":
                # Avoid Rapid Spin into Ghosts (no removal)
                if not self._opponent_has_type(battle.opponent_active_pokemon, "GHOST"):
                    val = self._rapid_spin_value(battle)
                    if val > best_val:
                        best_val = val
                        clear_move = move
        return clear_move

    def _should_clear_now(self, battle: AbstractBattle, clear_move) -> bool:
        """
        Decide whether to clear hazards now based on side pressure and Sticky Web priority.
        """
        our_haz = self._hazard_pressure(battle.side_conditions)
        opp_haz = self._hazard_pressure(battle.opponent_side_conditions)
        have_web = self._has_side_condition(battle.side_conditions, "stickyweb")
        # Clear if we are under pressure or webs on our side, unless opponent has way more hazards than us
        if have_web:
            return True
        return our_haz >= max(1.5, opp_haz - 0.5)

    def _pick_wow_if_good(self, battle: AbstractBattle):
        """
        Use Will-O-Wisp vs physically leaning, non-Fire, non-statused target when reasonable.
        """
        for move in battle.available_moves:
            if (move.id or "").lower() == "willowisp":
                opp = battle.opponent_active_pokemon
                if opp and opp.status is None and not self._opponent_has_type(opp, "FIRE"):
                    # Avoid if we are in big danger
                    if self._danger_multiplier(battle.active_pokemon, opp) <= 1.5:
                        # Value increased if opponent leans physical
                        return move
        return None

    def _pick_phaze_move(self, battle: AbstractBattle):
        """
        Use Dragon Tail if opponent is boosted and hazards exist on opponent's side.
        Avoid into Fairy types.
        """
        opp = battle.opponent_active_pokemon
        if not opp:
            return None
        if self._opponent_has_type(opp, "FAIRY"):
            return None
        opp_boosted = self._total_boost(opp) >= 2
        opp_has_haz = self._hazard_count(battle.opponent_side_conditions) > 0
        if not (opp_boosted or opp_has_haz):
            return None
        for move in battle.available_moves:
            if (move.id or "").lower() == "dragontail":
                return move
        return None

    def _pick_recovery_move(self, battle: AbstractBattle):
        recover_ids = {"recover", "morningsun", "roost", "slackoff", "softboiled", "rest", "milkdrink", "strengthsap"}
        for move in battle.available_moves:
            if (move.id or "").lower() in recover_ids:
                return move
        return None

    def _pick_boost_move(self, battle: AbstractBattle):
        boost_ids = {"calmmind", "swordsdance", "dragondance", "bulkup", "nastyplot", "agility", "irondefense", "amnesia", "coil"}
        for move in battle.available_moves:
            if (move.id or "").lower() in boost_ids:
                return move
        return None

    # -----------------------
    # Scoring primitives
    # -----------------------
    def _move_damage_score(self, move, attacker, defender) -> float:
        """
        Naive expected damage estimator:
        base_power * accuracy * STAB * type_effectiveness * (atk/def mult from boosts)
        """
        base_power = move.base_power or 0
        accuracy = move.accuracy if move.accuracy is not None else 1.0
        if base_power <= 0:
            return 0.5  # minimal for pure utility

        # Type effectiveness
        type_multiplier = self._type_effectiveness(move, defender)

        # STAB
        stab = 1.0
        try:
            if move.type is not None and attacker is not None and attacker.types is not None:
                if move.type in attacker.types:
                    stab = 1.5
        except Exception:
            stab = 1.0

        # Boost factor from stages
        atk_boost = 0
        def_boost = 0
        try:
            if hasattr(attacker, "boosts") and attacker.boosts is not None:
                if move.category.name.lower() == "special":
                    atk_boost = attacker.boosts.get("spa", 0) or 0
                elif move.category.name.lower() == "physical":
                    atk_boost = attacker.boosts.get("atk", 0) or 0
            if hasattr(defender, "boosts") and defender.boosts is not None:
                if move.category.name.lower() == "special":
                    def_boost = defender.boosts.get("spd", 0) or 0
                elif move.category.name.lower() == "physical":
                    def_boost = defender.boosts.get("def", 0) or 0
        except Exception:
            pass

        atk_mult = self._stage_multiplier(atk_boost)
        def_mult = self._stage_multiplier(def_boost)
        boost_factor = atk_mult / max(0.5, def_mult)

        score = float(base_power) * float(accuracy) * stab * type_multiplier * boost_factor

        return score

    def _best_raw_damage_score(self, battle: AbstractBattle) -> float:
        """
        Best raw damage score among available moves (no utility elevation).
        """
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        best = 0.0
        for move in battle.available_moves:
            best = max(best, self._move_damage_score(move, active, opponent))
        return best

    def _stage_multiplier(self, stage: int) -> float:
        if stage >= 0:
            return (2.0 + stage) / 2.0
        return 2.0 / (2.0 - stage)

    def _type_effectiveness(self, move, target) -> float:
        if move is None or move.type is None or target is None:
            return 1.0
        try:
            types = target.types or []
            if not types:
                return 1.0
            return move.type.damage_multiplier(*[t for t in types if t is not None])
        except Exception:
            return 1.0

    # -----------------------
    # Hazard utilities
    # -----------------------
    def _has_side_condition(self, side_conditions: Dict, name_substring: str) -> bool:
        try:
            look = name_substring.lower()
            for k in side_conditions.keys():
                if look in str(k).lower():
                    return True
        except Exception:
            pass
        return False

    def _hazard_count(self, side_conditions: Dict) -> int:
        count = 0
        try:
            for k, v in side_conditions.items():
                name = str(k).lower()
                if "stealthrock" in name:
                    count += 1
                elif "spikes" in name and "toxic" not in name:
                    # Spikes levels
                    count += v or 1
                elif "toxicspikes" in name:
                    count += v or 1
                elif "stickyweb" in name:
                    count += 1
        except Exception:
            pass
        return count

    def _hazard_pressure(self, side_conditions: Dict) -> float:
        """
        Weighted hazard pressure on a side.
        """
        pressure = 0.0
        try:
            for k, v in side_conditions.items():
                name = str(k).lower()
                layers = v or 1
                if "stealthrock" in name:
                    pressure += 1.2
                elif "spikes" in name and "toxic" not in name:
                    pressure += 0.9 * layers
                elif "toxicspikes" in name:
                    pressure += 0.8 * layers
                elif "stickyweb" in name:
                    pressure += 1.5
        except Exception:
            pass
        return pressure

    def _defog_value(self, battle: AbstractBattle) -> float:
        our_pressure = self._hazard_pressure(battle.side_conditions)
        opp_pressure = self._hazard_pressure(battle.opponent_side_conditions)
        # Clear mostly when our pressure is high or webs are on our side
        val = 0.0
        if our_pressure > 0.0:
            val = 100.0 + 40.0 * our_pressure - 20.0 * max(0.0, opp_pressure - our_pressure)
        if self._has_side_condition(battle.side_conditions, "stickyweb"):
            val += 80.0
        return max(0.0, val)

    def _rapid_spin_value(self, battle: AbstractBattle) -> float:
        our_pressure = self._hazard_pressure(battle.side_conditions)
        val = 0.0
        if our_pressure > 0.0:
            val = 95.0 + 35.0 * our_pressure
        # Speed boost bonus if we might be slower
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        if not self._is_likely_faster(active, opponent):
            val += 20.0
        return max(0.0, val)

    def _sr_value(self, battle: AbstractBattle) -> float:
        if self._has_side_condition(battle.opponent_side_conditions, "stealthrock"):
            return 0.0
        # Diminishing value as turns pass
        return max(0.0, 130.0 - 8.0 * battle.turn)

    def _tspikes_value(self, battle: AbstractBattle, opponent) -> float:
        if self._has_side_condition(battle.opponent_side_conditions, "toxicspikes"):
            return 0.0
        base = 100.0 - 7.0 * battle.turn
        # Less valuable into current Steel/Poison
        if opponent and (self._opponent_has_type(opponent, "STEEL") or self._opponent_has_type(opponent, "POISON")):
            base *= 0.6
        return max(0.0, base)

    # -----------------------
    # Switching heuristics
    # -----------------------
    def _should_switch_from_position(self, battle: AbstractBattle, best_attack_score: float) -> bool:
        """
        Switch if:
        - Danger from opponent STAB >= 2x, and
        - Our best move score is weak
        Avoid switching if we have a strong super effective hit.
        """
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        if not battle.available_switches:
            return False

        danger = self._danger_multiplier(active, opponent)
        # Strong attack threshold (very naive)
        strong_offense = best_attack_score >= 170.0

        # If we have a strong hit, prefer to stay
        if strong_offense:
            return False

        # Default switch trigger
        if danger >= 2.0 and best_attack_score < 130.0:
            # Only if we find an actually better switch
            best_switch = self._pick_best_switch(battle)
            if best_switch is None:
                return False
            switch_safety = self._safety_against_opponent(best_switch, opponent)
            current_safety = self._safety_against_opponent(active, opponent)
            return switch_safety + 1e-6 < current_safety
        return False

    def _pick_best_switch(self, battle: AbstractBattle):
        opponent = battle.opponent_active_pokemon
        candidates = battle.available_switches
        if not candidates:
            return None

        best_choice = None
        best_score = float("-inf")
        for mon in candidates:
            safety = self._safety_against_opponent(mon, opponent)  # lower is safer
            offense = self._offense_with_moves(mon, opponent)  # best move score from that mon
            hp_frac = mon.current_hp_fraction if mon.current_hp_fraction is not None else 1.0

            # Bonus if this mon can clear hazards and we are pressured
            clear_bonus = 0.0
            if self._hazard_pressure(battle.side_conditions) > 0.0:
                if self._mon_has_move(mon, "defog"):
                    clear_bonus += 0.4
                if self._mon_has_move(mon, "rapidspin"):
                    # avoid if opponent Ghost active; still small bonus otherwise
                    if not self._opponent_has_type(opponent, "GHOST"):
                        clear_bonus += 0.4

            # Composite score: prioritize safety, then offense, then HP, then utility
            score = (-1.3 * safety) + (0.8 * (offense / 150.0)) + (0.3 * hp_frac) + clear_bonus

            if score > best_score:
                best_score = score
                best_choice = mon
        return best_choice

    def _safety_against_opponent(self, mon, opponent) -> float:
        if mon is None or opponent is None or mon.types is None or opponent.types is None:
            return 1.0
        try:
            worst = 1.0
            for t in opponent.types:
                if t is None:
                    continue
                mult = t.damage_multiplier(*[x for x in mon.types if x is not None])
                if mult > worst:
                    worst = mult
            return worst
        except Exception:
            return 1.0

    def _offense_with_moves(self, mon, opponent) -> float:
        """
        Estimate best offensive potential of a specific mon based on its known moves.
        """
        if mon is None or opponent is None:
            return 0.0
        best = 0.0
        try:
            # mon.moves should exist for our own team; fallback to STAB typing if not
            if mon.moves:
                for m in mon.moves.values():
                    # Skip pure utility here
                    if (m.base_power or 0) <= 0 and (m.id or "") not in {"hex", "ruination"}:
                        continue
                    score = self._move_damage_score(m, mon, opponent)
                    if (m.id or "") == "hex" and opponent.status is not None:
                        score *= 1.9
                    if score > best:
                        best = score
                return best
        except Exception:
            pass
        # Fallback: STAB typing proxy
        best = 0.0
        try:
            for t in mon.types or []:
                if t is None:
                    continue
                mult = t.damage_multiplier(*[x for x in (opponent.types or []) if x is not None])
                best = max(best, 90.0 * mult)  # assume 90 BP STAB
        except Exception:
            return 0.0
        return best

    # -----------------------
    # Small utilities
    # -----------------------
    def _danger_multiplier(self, our_pokemon, opp_pokemon) -> float:
        if our_pokemon is None or opp_pokemon is None or our_pokemon.types is None or opp_pokemon.types is None:
            return 1.0
        try:
            worst = 1.0
            for t in opp_pokemon.types:
                if t is None:
                    continue
                mult = t.damage_multiplier(*[x for x in our_pokemon.types if x is not None])
                if mult > worst:
                    worst = mult
            return worst
        except Exception:
            return 1.0

    def _opponent_has_type(self, opp, type_name: str) -> bool:
        if not opp or not opp.types:
            return False
        tname = type_name.upper()
        try:
            for t in opp.types:
                if t and getattr(t, "name", "").upper() == tname:
                    return True
        except Exception:
            pass
        return False

    def _is_likely_faster(self, our_mon, opp_mon) -> bool:
        try:
            if not our_mon or not opp_mon:
                return False
            # Use stats if available, else base stats
            our_spe = (our_mon.stats or {}).get("spe") or (our_mon.base_stats or {}).get("spe", 0)
            opp_spe = (opp_mon.stats or {}).get("spe") or (opp_mon.base_stats or {}).get("spe", 0)
            # Account for our own speed boosts minimally
            boost = 0
            if our_mon.boosts and "spe" in our_mon.boosts:
                boost = our_mon.boosts["spe"] or 0
            if boost != 0:
                # Rough multiplier
                our_spe *= self._stage_multiplier(boost)
            return our_spe >= opp_spe
        except Exception:
            return False

    def _physical_lean(self, mon) -> float:
        """
        Returns a value in [0,1+]: higher means more physical leaning.
        """
        try:
            atk = (mon.base_stats or {}).get("atk", 100)
            spa = (mon.base_stats or {}).get("spa", 100)
            if atk <= 0 and spa <= 0:
                return 0.5
            return max(0.0, (atk - spa) / max(1.0, atk + spa)) * 2.0 + (1.0 if atk > spa else 0.5)
        except Exception:
            return 1.0

    def _total_boost(self, mon) -> int:
        try:
            if not mon or not mon.boosts:
                return 0
            return sum(v or 0 for v in mon.boosts.values())
        except Exception:
            return 0

    def _mon_has_move(self, mon, move_id: str) -> bool:
        try:
            if not mon or not mon.moves:
                return False
            for m in mon.moves.values():
                if (m.id or "").lower() == move_id.lower():
                    return True
        except Exception:
            pass
        return False
