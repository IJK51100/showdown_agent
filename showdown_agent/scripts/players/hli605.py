# hli605.py
from __future__ import annotations

from typing import Dict, Optional, Tuple, List

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
    Heuristic agent with terastallization and one-ply lookahead:
    - Utility logic: hazards, removal, status, phazing, setup, recover
    - Damage scoring with situational modifiers (Hex synergy, KO/accuracy nudges)
    - One-step lookahead: net advantage = our move score - opponent best reply score * weight
    - Terastallization: defensive (reduce incoming weaknesses) and offensive (meaningful damage swing)
    - Endgame mode: bias toward direct damage when opponent is down to 1-2 mons
    """

    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)
        # Track per-battle state
        self._state: Dict[str, Dict] = {}

    # -----------------------
    # Core decision function
    # -----------------------
    def choose_move(self, battle: AbstractBattle):
        try:
            st = self._get_battle_state(battle)

            # If we must switch (e.g., no moves available), pick best switch
            if not battle.available_moves:
                switch_target = self._pick_best_switch(battle)
                if switch_target is not None:
                    return self.create_order(switch_target)
                return self.choose_random_switch(battle)

            active = battle.active_pokemon
            opponent = battle.opponent_active_pokemon

            # Update simple opponent repetition tracker (choice-lock suspicion)
            self._update_opponent_repeat_tracker(battle)

            # Endgame mode heuristic
            endgame = self._is_endgame(battle)

            # 0) Hazard removal if our side is pressured (avoid if endgame and we out-hazard)
            clear_move = self._pick_hazard_clear_move(battle)
            if clear_move is not None and not endgame:
                if self._should_clear_now(battle, clear_move):
                    return self.create_order(clear_move)

            # 1) Hazard setup early if safe (SR > TSpikes)
            hazard_move = self._pick_hazard_setup_move(battle, endgame=endgame)
            if hazard_move is not None:
                if self._danger_multiplier(active, opponent) <= 1.25:
                    return self.create_order(hazard_move)

            # 2) Status: Will-O-Wisp vs physical threats when safe
            wow_move = self._pick_wow_if_good(battle)
            if wow_move is not None:
                return self.create_order(wow_move)

            # 3) Recovery when low and safe
            recovery_move = self._pick_recovery_move(battle)
            if recovery_move is not None:
                hp_frac = active.current_hp_fraction or 1.0
                # Be more conservative in endgame (tend to attack more)
                safe = self._danger_multiplier(active, opponent) <= (1.0 if not endgame else 0.8)
                if hp_frac < 0.5 and safe:
                    return self.create_order(recovery_move)

            # 4) Setup when safe (Calm Mind on Arceus-Fairy), more in midgame
            boosting_move = self._pick_boost_move(battle)
            if boosting_move is not None:
                very_safe = self._danger_multiplier(active, opponent) <= 1.0
                if very_safe and battle.turn <= 12:
                    return self.create_order(boosting_move)

            # 5) Phaze when opponent is boosted and hazards are up
            phaze_move = self._pick_phaze_move(battle)
            if phaze_move is not None:
                return self.create_order(phaze_move)

            # 6) Advanced offensive selection: one-ply lookahead with utility integration
            best_move, net_score = self._pick_best_move_with_lookahead(battle, endgame=endgame)

            # 7) Consider defensive switching if in big danger and our net offense is poor
            if self._should_switch_from_position(battle, net_score):
                switch_target = self._pick_best_switch(battle)
                if switch_target is not None:
                    return self.create_order(switch_target)

            # 8) Terastallization decision (offensive or defensive)
            if best_move is not None:
                tera_flag = self._should_tera_now(battle, best_move, base_net=net_score, endgame=endgame)
                if tera_flag:
                    st["tera_used"] = True
                    return self.create_order(best_move, terastallize=True)

                # Otherwise, use the selected best move
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
    # Lookahead and scoring
    # -----------------------
    def _pick_best_move_with_lookahead(self, battle: AbstractBattle, endgame: bool = False):
        """
        Score moves with utility + damage + one-step opponent reply penalty.
        Returns (best_move, net_score).
        """
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        our_moves = list(battle.available_moves)
        if not our_moves:
            return None, float("-inf")

        # Pre-sort by naive score to reduce compute; keep top-K
        prescored = []
        for m in our_moves:
            prescored.append((m, self._baseline_move_score(m, active, opponent)))
        prescored.sort(key=lambda x: x[1], reverse=True)
        top_k = [m for m, _ in prescored[:max(3, min(5, len(prescored)))]]

        best_move = None
        best_net = float("-inf")

        # Weigh opponent reply more if we are slower
        reply_weight_base = 0.75 if not endgame else 0.6
        faster = self._is_likely_faster(active, opponent)
        reply_weight = reply_weight_base if faster else min(1.0, reply_weight_base + 0.2)

        for move in top_k:
            util, dmg = self._util_and_damage_score(battle, move)
            our_score = max(util, dmg)

            # Account for KO nudge and risky drops/recoil
            our_score = self._refine_score_with_situations(battle, move, our_score)

            # Opponent best reply (no tera)
            opp_reply = self._opponent_best_reply_score(battle, our_after_tera_type=None)

            net = our_score - reply_weight * opp_reply

            # Slight bias towards accurate moves in danger
            if self._danger_multiplier(active, opponent) >= 2.0:
                if move.accuracy is not None and move.accuracy < 0.85:
                    net *= 0.95

            # Endgame: favor damage more directly
            if endgame and (move.base_power or 0) > 0:
                net *= 1.05

            if net > best_net:
                best_net = net
                best_move = move

        return best_move, best_net

    def _baseline_move_score(self, move, attacker, defender) -> float:
        """
        Quick baseline for presorting: max of utility value and raw damage value.
        """
        # Utility rough
        util = self._utility_value_rough(move, attacker, defender)
        dmg = self._move_damage_score(move, attacker, defender)
        return max(util, dmg)

    def _utility_value_rough(self, move, attacker, defender) -> float:
        mid = (move.id or "").lower()
        if mid in {"stealthrock"}:
            return 90.0
        if mid in {"toxicspikes"}:
            return 70.0
        if mid in {"defog", "rapidspin"}:
            return 60.0
        if mid in {"willowisp"}:
            return 65.0
