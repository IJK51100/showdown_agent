# hli605.py
from __future__ import annotations

from typing import Dict, Optional, Tuple

from poke_env.battle import AbstractBattle
from poke_env.player import Player


# Gen 9 Ubers bulky offense team
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
    A simple heuristic-based agent:
    - Attacks with the highest naive expected damage (BP * accuracy * STAB * type effectiveness * boost factor)
    - Sets hazards early (SR / TSpikes) if safe and not already up
    - Uses recovery/boosting when safe and valuable
    - Switches to safer counters when facing severe type disadvantage and poor damage options
    """

    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)

    # -----------------------
    # Core decision function
    # -----------------------
    def choose_move(self, battle: AbstractBattle):
        try:
            # If we must switch (e.g., no moves available or trapped status), pick best switch
            if not battle.available_moves:
                switch_target = self._pick_best_switch(battle)
                if switch_target is not None:
                    return self.create_order(switch_target)
                return self.choose_random_switch(battle)

            active = battle.active_pokemon
            opponent = battle.opponent_active_pokemon

            # 1) Early hazards if safe and useful
            hazard_move = self._pick_hazard_move(battle)
            if hazard_move is not None:
                # Only place hazards when not in immediate danger
                if self._danger_multiplier(active, opponent) < 2.0:
                    return self.create_order(hazard_move)

            # 2) Recover when low and safe
            recovery_move = self._pick_recovery_move(battle)
            if recovery_move is not None:
                # Prefer recovery below 50% HP and when not in immediate danger
                if (active.current_hp_fraction is not None and active.current_hp_fraction < 0.5) and (
                    self._danger_multiplier(active, opponent) <= 1.0
                ):
                    return self.create_order(recovery_move)

            # 3) Boost when quite safe
            boosting_move = self._pick_boost_move(battle)
            if boosting_move is not None:
                # Avoid boosting if we are in a dangerous matchup
                if self._danger_multiplier(active, opponent) <= 1.0 and (battle.turn <= 10):
                    return self.create_order(boosting_move)

            # 4) Offense: pick the best available move by naive expected damage
            move, best_score = self._pick_best_damage_move(battle)
            # 5) If we are in severe danger and have a good switch, consider switching
            if self._should_switch_from_position(battle, best_score):
                switch_target = self._pick_best_switch(battle)
                if switch_target is not None:
                    return self.create_order(switch_target)

            # 6) Otherwise, attack with best move
            if move is not None:
                return self.create_order(move)

            # 7) Fallbacks
            if battle.available_moves:
                return self.create_order(battle.available_moves[0])
            elif battle.available_switches:
                return self.create_order(battle.available_switches[0])
            else:
                # Absolute fallback
                return self.choose_random_move(battle)
        except Exception:
            # Robustness fallback in case something goes wrong
            if battle.available_moves:
                return self.choose_random_move(battle)
            return self.choose_random_switch(battle)

    # -----------------------
    # Heuristic helpers
    # -----------------------
    def _pick_best_damage_move(self, battle: AbstractBattle):
        """
        Returns (best_move, score) using naive expected damage.
        """
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        best_move = None
        best_score = float("-inf")

        for move in battle.available_moves:
            score = self._move_damage_score(move, active, opponent)

            # Encourage pivoting when in danger
            move_id = move.id or ""
            if self._danger_multiplier(active, opponent) >= 2.0 and move_id in {"uturn", "voltswitch", "flipturn", "partingshot"}:
                score *= 1.15

            if score > best_score:
                best_score = score
                best_move = move

        return best_move, best_score

    def _move_damage_score(self, move, attacker, defender) -> float:
        """
        Naive expected damage estimator:
        BP * accuracy * STAB * typeEffectiveness * (boost factor)
        """
        # Handle non-damaging moves with a small baseline
        base_power = move.base_power or 0
        accuracy = move.accuracy if move.accuracy is not None else 1.0  # poke-env gives 0-1 for acc
        if base_power <= 0:
            # Small baseline for status moves (so hazards/boosting can still be considered via other paths)
            return 1.0

        # Type effectiveness
        type_multiplier = self._type_effectiveness(move, defender)

        # STAB
        stab = 1.0
        if move.type is not None and attacker is not None and attacker.types is not None:
            stab = 1.5 if move.type in attacker.types else 1.0

        # Boost factor: consider basic attack/defense stages to slightly bias choice
        atk_boost = 0.0
        def_boost = 0.0
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

        atk_mult = self._stage_multiplier(atk_boost)
        def_mult = self._stage_multiplier(def_boost)
        boost_factor = atk_mult / max(0.5, def_mult)

        # Avoid spamming very inaccurate moves when similar options exist
        # (e.g., Draco Meteor vs Dragon Pulse – this is soft bias since accuracy is already in score)
        score = float(base_power) * float(accuracy) * stab * type_multiplier * boost_factor
        return score

    def _type_effectiveness(self, move, target) -> float:
        if move is None or move.type is None or target is None:
            return 1.0
        try:
            types = target.types or []
            if not types:
                return 1.0
            # poke-env Type has method: damage_multiplier(*types)
            return move.type.damage_multiplier(*[t for t in types if t is not None])
        except Exception:
            return 1.0

    def _stage_multiplier(self, stage: int) -> float:
        # Standard stage mechanic
        # stage >= 0: (2 + stage)/2; stage < 0: 2/(2 - stage)
        if stage >= 0:
            return (2.0 + stage) / 2.0
        return 2.0 / (2.0 - stage)

    def _danger_multiplier(self, our_pokemon, opp_pokemon) -> float:
        """
        Estimate how dangerous opponent’s STAB types are against our active mon.
        Return the maximum STAB type effectiveness found.
        """
        if our_pokemon is None or opp_pokemon is None or our_pokemon.types is None or opp_pokemon.types is None:
            return 1.0
        try:
            # Worst-case: any of opponent's types hitting our types
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

    def _should_switch_from_position(self, battle: AbstractBattle, best_attack_score: float) -> bool:
        """
        Decide whether to switch:
        - If facing super effective threat (>= 2x) AND
        - Our best move is not promising (score below a threshold)
        """
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        if not battle.available_switches:
            return False
        danger = self._danger_multiplier(active, opponent)

        # Calibrate a simple threshold based on naive scores:
        # Typical strong neutral score ~ 80-150; super-effective high-power ~ 200-400
        low_offense = best_attack_score < 120.0

        # Encourage switching if in big danger and not dealing enough damage
        if danger >= 2.0 and low_offense:
            # Only switch if we can find a safer alternative
            best_switch = self._pick_best_switch(battle)
            if best_switch is not None:
                # Ensure the switch is actually safer than staying in
                switch_safety = self._safety_against_opponent(best_switch, opponent)
                current_safety = self._safety_against_opponent(active, opponent)
                return switch_safety < current_safety
        return False

    def _pick_best_switch(self, battle: AbstractBattle):
        """
        Choose a switch that is safer defensively and has better offensive typing potential.
        """
        opponent = battle.opponent_active_pokemon
        candidates = battle.available_switches
        if not candidates:
            return None

        best_choice = None
        best_score = float("-inf")
        for mon in candidates:
            safety = self._safety_against_opponent(mon, opponent)  # lower is better
            offense = self._offense_potential(mon, opponent)  # higher is better
            hp_frac = mon.current_hp_fraction if mon.current_hp_fraction is not None else 1.0

            # Composite score: prioritize safety, then offensive potential, then HP
            # Negative weight for safety so that lower safety (i.e., more danger) reduces score.
            score = (-1.25 * safety) + (0.75 * offense) + (0.25 * hp_frac)

            if score > best_score:
                best_score = score
                best_choice = mon
        return best_choice

    def _safety_against_opponent(self, mon, opponent) -> float:
        """
        Safety proxy: maximum opponent STAB type effectiveness vs mon.
        Lower is safer.
        """
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

    def _offense_potential(self, mon, opponent) -> float:
        """
        Offensive proxy based on STAB typing: best of mon's types vs opponent's types.
        """
        if mon is None or opponent is None or mon.types is None or opponent.types is None:
            return 1.0
        try:
            best = 1.0
            for t in mon.types:
                if t is None:
                    continue
                mult = t.damage_multiplier(*[x for x in opponent.types if x is not None])
                if mult > best:
                    best = mult
            return best
        except Exception:
            return 1.0

    def _has_side_condition(self, side_conditions: Dict, name_substring: str) -> bool:
        """
        Checks if a side condition dictionary contains a condition matching the name substring
        (case-insensitive).
        """
        try:
            look = name_substring.lower()
            for k in side_conditions.keys():
                if look in str(k).lower():
                    return True
        except Exception:
            pass
        return False

    def _pick_hazard_move(self, battle: AbstractBattle):
        """
        Prefer hazards early game if not already set and move is available.
        """
        # Only consider hazards early game
        if battle.turn > 5:
            return None

        opp_has_sr = self._has_side_condition(battle.opponent_side_conditions, "stealthrock")
        opp_has_spikes = self._has_side_condition(battle.opponent_side_conditions, "spikes")
        opp_has_tspikes = self._has_side_condition(battle.opponent_side_conditions, "toxicspikes")

        # Available hazards from our team: SR, Spikes, TSpikes, Sticky Web
        preferred_order = []
        if not opp_has_sr:
            preferred_order.append("stealthrock")
        if not opp_has_tspikes:
            preferred_order.append("toxicspikes")
        if not opp_has_spikes:
            preferred_order.append("spikes")
        preferred_order.append("stickyweb")  # generally less good in Ubers, but consider if present

        if not preferred_order:
            return None

        # Pick the first available hazard move from preferred order
        for move in battle.available_moves:
            mid = (move.id or "").lower()
            if mid in preferred_order:
                return move
        return None

    def _pick_recovery_move(self, battle: AbstractBattle):
        """
        If we can heal and it's worthwhile, pick a recovery move.
        """
        recover_ids = {"recover", "morningsun", "roost", "slackoff", "softboiled", "rest", "milkdrink", "strengthsap"}
        for move in battle.available_moves:
            if (move.id or "").lower() in recover_ids:
                return move
        return None

    def _pick_boost_move(self, battle: AbstractBattle):
        """
        Pick a boosting move if available and potentially valuable in Ubers.
        """
        boost_ids = {
            "calmmind",
            "swordsdance",
            "dragondance",
            "bulkup",
            "nastyplot",
            "agility",
            "irondefense",
            "amnesia",
            "coil",
        }
        for move in battle.available_moves:
            if (move.id or "").lower() in boost_ids:
                return move
        return None
