# hli605.py
from __future__ import annotations

from typing import Dict, Optional, Tuple, List

from poke_env.battle import AbstractBattle
from poke_env.player import Player


# Gen 9 Ubers hazard-denial bulky offense
# - Denies Defog with Gholdengo (Good as Gold)
# - Compounds hazards with Nec-DM (SR) + Ting-Lu (Spikes + Whirlwind)
# - Eternatus lays TSpikes; Giratina-O provides Defog fail-safe + phazing + WoW + Hex
# - Arceus-Fairy closes; defensive tera reserved for pivotal exchanges
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
- Defog
- Dragon Tail

Ting-Lu @ Leftovers
Ability: Vessel of Ruin
Tera Type: Water
EVs: 252 HP / 4 Atk / 252 SpD
Careful Nature
- Spikes
- Whirlwind
- Ruination
- Earthquake

Gholdengo @ Leftovers
Ability: Good as Gold
Tera Type: Flying
EVs: 252 HP / 4 SpA / 252 SpD
Calm Nature
IVs: 0 Atk
- Make It Rain
- Shadow Ball
- Nasty Plot
- Recover
"""


class CustomAgent(Player):
    """
    Overhauled agent with:
    - Showdown-like Opponent Model: assume max-damage priority with simple hazard/clear patterns.
    - Normalized, relative evaluation: avoid brittle absolute thresholds.
    - Short intent memory: reduce dithering for hazards/CM.
    - Hazards-denial team: Gholdengo + Ting-Lu.
    - Tera as an explicit alternate action (rare, high-value).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)
        self._state: Dict[str, Dict] = {}

    # -----------------------
    # Core decision function
    # -----------------------
    def choose_move(self, battle: AbstractBattle):
        try:
            st = self._get_battle_state(battle)

            # If no moves, must switch
            if not battle.available_moves:
                sw = self._pick_best_switch(battle)
                if sw is not None:
                    return self.create_order(sw)
                return self.choose_random_switch(battle)

            # Update opponent repetition tracker (choice-lock-ish)
            self._update_opponent_repeat_tracker(battle)

            # Commit logic: if we have intent and it's still safe, honor it
            if self._has_commit_intent(st) and self._intent_still_safe(battle):
                intent_move = self._move_for_intent(battle)
                if intent_move is not None:
                    st["intent_turns"] -= 1
                    return self.create_order(intent_move)

            # Enumerate actions: moves (+ tera variants for top-2) + top switches
            candidate_actions = self._enumerate_actions(battle)

            # Evaluate actions via one-ply with opponent model
            best = None
            best_score = float("-inf")
            for act in candidate_actions:
                score = self._evaluate_action(battle, act)
                if score > best_score:
                    best_score = score
                    best = act

            if best is None:
                # Fallback
                if battle.available_moves:
                    return self.create_order(battle.available_moves[0])
                return self.choose_random_move(battle)

            # If best action is a move with tera_flag, commit tera
            if best["type"] == "move":
                # Optional: set intent for hazards or CM if we select them now
                self._maybe_set_intent_from_move(battle, best["move"])
                if best.get("tera", False):
                    self._get_battle_state(battle)["tera_used"] = True
                    return self.create_order(best["move"], terastallize=True)
                return self.create_order(best["move"])

            # If best is switch
            if best["type"] == "switch":
                return self.create_order(best["target"])

            # Fallback
            return self.choose_random_move(battle)
        except Exception:
            if battle.available_moves:
                return self.choose_random_move(battle)
            return self.choose_random_switch(battle)

    # -----------------------
    # Action enumeration
    # -----------------------
    def _enumerate_actions(self, battle: AbstractBattle) -> List[Dict]:
        actions: List[Dict] = []

        # Moves
        our_moves = list(battle.available_moves)
        # Pre-score base damage to select tera candidates
        prescores = []
        for m in our_moves:
            prescores.append((m, self._move_damage_score(m, battle.active_pokemon, battle.opponent_active_pokemon)))
        prescores.sort(key=lambda x: x[1], reverse=True)
        top2 = [m for m, _ in prescores[:2]]

        for m in our_moves:
            actions.append({"type": "move", "move": m, "tera": False})

        # Tera-action variants for top-2 moves if available and not used
        st = self._get_battle_state(battle)
        can_tera = bool(getattr(battle, "can_tera", True)) and not st.get("tera_used", False)
        if can_tera and len(top2) > 0:
            # Gate early tera: avoid turns 1-2 unless defensive KO-prevent or clear offensive gain
            if battle.turn >= 3 or self._would_be_ko_likely(battle):
                for m in top2:
                    actions.append({"type": "move", "move": m, "tera": True})

        # Switches: consider top-3 safe switches
        switch_candidates = battle.available_switches or []
        scored_switches = []
        for mon in switch_candidates:
            s = self._switch_safety_score(mon, battle.opponent_active_pokemon)
            o = self._switch_offense_score(mon, battle.opponent_active_pokemon)
            scored_switches.append((mon, s, o))
        # Lower safety is better; sort by combined desirability
        scored_switches.sort(key=lambda x: (x[1], -x[2]))
        for mon, _, _ in scored_switches[:3]:
            actions.append({"type": "switch", "target": mon})

        return actions

    # -----------------------
    # Showdown-like opponent model and evaluation
    # -----------------------
    def _evaluate_action(self, battle: AbstractBattle, action: Dict) -> float:
        """
        Normalized net = (our_damage_norm + util_norm + progress_norm) - (opp_reply_damage_norm * weight)
        - Normalize scores to reduce volatility
        - Opponent model: max damage reply, with hazard/clear as alternates; rare switching considered
        """
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        if active is None or opponent is None:
            return 0.0

        # Evaluate our immediate effect
        if action["type"] == "move":
            move = action["move"]
            tera = action.get("tera", False)
            util, dmg = self._util_and_damage_score(battle, move, tera=tera)
            our_damage_norm = self._normalize_damage_score(dmg)
            util_norm = self._normalize_utility_score(util)

            # Accuracy gating: avoid low-accuracy when in danger unless payoff is very large
            danger = self._danger_multiplier(active, opponent)
            if move.accuracy is not None and move.accuracy < 0.85 and danger >= 1.75:
                our_damage_norm *= 0.9
                util_norm *= 0.9

            # Progress bias: favor hazards in early-game, damage in endgame
            progress_norm = self._progress_value(battle, move)

            # Predict opponent reply under our post-action typing (tera affects defense)
            our_types_after = self._types_after_tera(active, move, battle) if tera else (active.types or [])
            opp_damage_reply = self._opponent_reply_damage_score(battle, defender_types=our_types_after)

            # Normalize opponent reply
            opp_damage_norm = self._normalize_damage_score(opp_damage_reply)

            # Weight depends on speed
            faster = self._is_likely_faster(active, opponent)
            reply_weight = 0.7 if faster else 0.85

            net = (our_damage_norm + util_norm + progress_norm) - (reply_weight * opp_damage_norm)

            # Rare, high-confidence tera trigger: only if tera variant nets at least 20% better than non-tera baseline
            if tera:
                baseline_move_dmg = self._move_damage_score(move, active, opponent)
                base_norm = self._normalize_damage_score(baseline_move_dmg)
                if base_norm > 0:
                    rel = (our_damage_norm - base_norm) / max(0.001, base_norm)
                    if rel < 0.2:
                        net *= 0.9  # downrank weak tera benefits

            return net

        elif action["type"] == "switch":
            target = action["target"]
            # Our immediate damage is 0; utility is in safety and future offense
            safety = self._switch_safety_score(target, opponent)  # lower is safer
            offense = self._switch_offense_score(target, opponent)

            # Convert safety to normalized "not getting chunked next turn"
            opp_damage_vs_target = self._opponent_reply_damage_score_vs(battle, defender=target)
            opp_damage_norm = self._normalize_damage_score(opp_damage_vs_target)

            # Prefer switches that (a) cut reply damage, (b) bring hazard denial or removal if needed
            util_norm = 0.0
            if self._hazard_pressure(battle.side_conditions) >= 1.5:
                if self._mon_has_move(target, "defog"):
                    util_norm += 0.3
            # Bring Gholdengo when we predict their Defog
            if self._predict_opp_will_defog(battle) and self._is_gholdengo(target):
                util_norm += 0.4

            # Offense normalized
            offense_norm = self._normalize_damage_score(offense) * 0.5

            # Penalize risky switches (high expected reply)
            net = util_norm + offense_norm - (0.8 * opp_damage_norm)

            return net

        return 0.0

    def _opponent_reply_damage_score(self, battle: AbstractBattle, defender_types: List = None) -> float:
        """
        Showdown-like reply: prioritize max-damage move. Consider hazard clear if their side is pressured.
        Rare switching considered if clearly walled.
        """
        opp = battle.opponent_active_pokemon
        us = battle.active_pokemon
        if opp is None or us is None:
            return 0.0

        # 1) Max-damage option (primary)
        best_dmg = 0.0
        if opp.moves:
            for mv in opp.moves.values():
                if (mv.base_power or 0) <= 0:
                    continue
                dmg = self._rough_damage_score_types(mv, attacker_types=opp.types or [], defender_types=defender_types or (us.types or []), attacker=opp, defender=us)
                best_dmg = max(best_dmg, dmg)
        else:
            # Assume generic STABs 90 BP for each known type
            for t in opp.types or []:
                if t is None:
                    continue
                base_power = 90.0
                stab = 1.5
                type_mult = self._damage_multiplier_of_type(t, defender_types or (us.types or []))
                best_dmg = max(best_dmg, base_power * stab * type_mult)

        # 2) Hazard clear option: if their side is pressured, they may Defog or Spin (not modeled as damage)
        clear_desire = self._hazard_pressure(battle.opponent_side_conditions)
        clear_bonus = 0.0
        if clear_desire >= 1.5:
            clear_bonus = 0.15  # small chance/value of choosing clear instead of max-damage

        # 3) Rare switch: if we resist both STABs hard, assume a moderate fallback (we'll encode as 60% of best dmg)
        switch_penalty = 0.0
        if self._hard_walls(our_types=defender_types or (us.types or []), opp_types=opp.types or []):
            switch_penalty = -0.4 * best_dmg  # they switch; less immediate damage

        # Combine (expected-like)
        expected = max(best_dmg * (1.0 - clear_bonus), best_dmg + switch_penalty)
        return expected

    def _opponent_reply_damage_score_vs(self, battle: AbstractBattle, defender) -> float:
        opp = battle.opponent_active_pokemon
        if opp is None or defender is None:
            return 0.0
        dtypes = defender.types or []
        if not dtypes:
            return 0.0
        # Use same logic as above but with defender specified
        best_dmg = 0.0
        if opp.moves:
            for mv in opp.moves.values():
                if (mv.base_power or 0) <= 0:
                    continue
                dmg = self._rough_damage_score_types(mv, attacker_types=opp.types or [], defender_types=dtypes, attacker=opp, defender=defender)
                best_dmg = max(best_dmg, dmg)
        else:
            for t in opp.types or []:
                if t is None:
                    continue
                base_power = 90.0
                stab = 1.5
                type_mult = self._damage_multiplier_of_type(t, dtypes)
                best_dmg = max(best_dmg, base_power * stab * type_mult)
        return best_dmg

    # -----------------------
    # Utility and scoring helpers
    # -----------------------
    def _util_and_damage_score(self, battle: AbstractBattle, move, tera: bool = False) -> Tuple[float, float]:
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        mid = (move.id or "").lower()

        # Damage with or without tera
        if tera:
            dmg = self._move_damage_score_with_tera(move, active, opponent, getattr(active, "tera_type", None))
        else:
            dmg = self._move_damage_score(move, active, opponent)

        util = 0.0
        danger = self._danger_multiplier(active, opponent)
        endgame = self._is_endgame(battle)

        # Hazards
        if mid == "stealthrock":
            if not self._has_side_condition(battle.opponent_side_conditions, "stealthrock"):
                util += 120.0 - 6.0 * battle.turn
        elif mid == "spikes":
            util += 95.0 - 5.0 * battle.turn
        elif mid == "toxicspikes":
            # Gate TSpikes if many poison/steel on opp side
            if not self._has_side_condition(battle.opponent_side_conditions, "toxicspikes"):
                steel_or_poison_active = self._opponent_has_type(opponent, "STEEL") or self._opponent_has_type(opponent, "POISON")
                base = 85.0 - 5.0 * battle.turn
                util += base * (0.6 if steel_or_poison_active else 1.0)
        elif mid == "defog":
            util += self._defog_value(battle)
        elif mid == "rapidspin":
            util += self._rapid_spin_value(battle)  # not on this team, but keep generic
        elif mid == "willowisp":
            if opponent and opponent.status is None and not self._opponent_has_type(opponent, "FIRE"):
                util += 95.0 * self._physical_lean(opponent)
                if danger >= 1.75:
                    util *= 0.75  # avoid greedy WoW in danger
        elif mid == "dragontail":
            if not self._opponent_has_type(opponent, "FAIRY"):
                has_haz = self._hazard_count(battle.opponent_side_conditions) > 0
                util += 80.0 + (40.0 if has_haz else 0.0) + (40.0 if self._total_boost(opponent) >= 2 else 0.0)
                if danger >= 1.75:
                    util *= 0.8
        elif mid == "calmmind":
            if danger <= 1.0 and battle.turn <= 12 and not endgame:
                util += 80.0

        # Knock/Ruination etc. would go here if present; keep generic
        if mid == "ruination":
            opp_hp_frac = opponent.current_hp_fraction or 1.0
            acc = move.accuracy if move.accuracy is not None else 1.0
            util += 120.0 * opp_hp_frac * acc

        # Accuracy penalties for risky nukes
        if mid in {"overheat", "dracometeor", "leafstorm"}:
            dmg *= 0.9
        if mid in {"closecombat", "headlongrush"}:
            dmg *= 0.95

        # Hex synergy
        if mid == "hex" and opponent and opponent.status is not None:
            dmg *= 1.9

        return util, dmg

    def _normalize_damage_score(self, dmg: float) -> float:
        # Scale to ~0..1 range for typical Ubers moves; soft cap
        return min(dmg / 220.0, 1.5)

    def _normalize_utility_score(self, util: float) -> float:
        return min(util / 150.0, 1.2)

    def _progress_value(self, battle: AbstractBattle, move) -> float:
        endgame = self._is_endgame(battle)
        mid = (move.id or "").lower()
        if endgame:
            # Encourage direct damage and CM finishers in endgame
            if (move.base_power or 0) > 0:
                return 0.1
            if mid == "calmmind":
                return 0.05
            return -0.05
        else:
            # Encourage hazards early
            if mid in {"stealthrock", "spikes", "toxicspikes"}:
                return 0.15
            return 0.0

    def _hard_walls(self, our_types, opp_types) -> bool:
        # If our (defender) types resist both opp STAB types strongly, say x0.5 or better each
        try:
            strong_resists = 0
            for t in opp_types or []:
                if t is None:
                    continue
                mult = self._damage_multiplier_of_type(t, our_types)
                if mult <= 0.5:
                    strong_resists += 1
            return strong_resists >= 2
        except Exception:
            return False

    def _predict_opp_will_defog(self, battle: AbstractBattle) -> bool:
        # Predict Defog if their side hazard pressure is high; crude heuristic
        return self._hazard_pressure(battle.opponent_side_conditions) >= 1.5

    def _types_after_tera(self, active, move, battle: AbstractBattle):
        try:
            if not getattr(battle, "can_tera", True):
                return active.types or []
            if getattr(active, "tera_type", None) is None:
                return active.types or []
            # Defensive tera uses single tera type for damage calculation
            return [active.tera_type]
        except Exception:
            return active.types or []

    # -----------------------
    # Intent memory
    # -----------------------
    def _has_commit_intent(self, st: Dict) -> bool:
        return st.get("intent") in {"hazard_stack", "cm_push"} and st.get("intent_turns", 0) > 0

    def _intent_still_safe(self, battle: AbstractBattle) -> bool:
        act = battle.active_pokemon
        opp = battle.opponent_active_pokemon
        if not act or not opp:
            return False
        # Abort intent if danger spikes
        return self._danger_multiplier(act, opp) <= 1.3

    def _move_for_intent(self, battle: AbstractBattle):
        st = self._get_battle_state(battle)
        desired = None
        if st.get("intent") == "hazard_stack":
            for m in battle.available_moves:
                mid = (m.id or "").lower()
                if mid in {"stealthrock", "spikes", "toxicspikes"}:
                    desired = m
                    break
        elif st.get("intent") == "cm_push":
            for m in battle.available_moves:
                if (m.id or "").lower() == "calmmind":
                    desired = m
                    break
        return desired

    def _maybe_set_intent_from_move(self, battle: AbstractBattle, move):
        st = self._get_battle_state(battle)
        mid = (move.id or "").lower()
        if mid in {"stealthrock", "spikes", "toxicspikes"}:
            st["intent"] = "hazard_stack"
            st["intent_turns"] = 2
        elif mid == "calmmind":
            st["intent"] = "cm_push"
            st["intent_turns"] = 2
        else:
            # decay intent naturally
            if st.get("intent_turns", 0) > 0:
                st["intent_turns"] -= 1
            else:
                st["intent"] = None

    # -----------------------
    # Switching heuristics
    # -----------------------
    def _pick_best_switch(self, battle: AbstractBattle):
        opponent = battle.opponent_active_pokemon
        candidates = battle.available_switches
        if not candidates:
            return None
        best = None
        best_val = float("-inf")
        for mon in candidates:
            safety = self._switch_safety_score(mon, opponent)
            offense = self._switch_offense_score(mon, opponent)
            util = 0.0
            # Prefer Gholdengo if we predict Defog
            if self._predict_opp_will_defog(battle) and self._is_gholdengo(mon):
                util += 0.5
            # Prefer Giratina-O if we need emergency Defog
            if self._hazard_pressure(battle.side_conditions) >= 2.0 and self._mon_has_move(mon, "defog"):
                util += 0.4
            score = (-1.0 * safety) + 0.4 * self._normalize_damage_score(offense) + util
            if score > best_val:
                best_val = score
                best = mon
        return best

    def _switch_safety_score(self, mon, opponent) -> float:
        # lower is safer; use worst-case STAB mult
        if mon is None or opponent is None:
            return 1.0
        try:
            worst = 1.0
            for t in opponent.types or []:
                if t is None:
                    continue
                mult = t.damage_multiplier(*[x for x in (mon.types or []) if x is not None])
                if mult > worst:
                    worst = mult
            return worst
        except Exception:
            return 1.0

    def _switch_offense_score(self, mon, opponent) -> float:
        if mon is None or opponent is None:
            return 0.0
        best = 0.0
        try:
            if mon.moves:
                for m in mon.moves.values():
                    if (m.base_power or 0) <= 0 and (m.id or "") not in {"hex", "ruination"}:
                        continue
                    score = self._move_damage_score(m, mon, opponent)
                    if (m.id or "") == "hex" and opponent.status is not None:
                        score *= 1.9
                    best = max(best, score)
                return best
        except Exception:
            pass
        # default to 90BP STAB
        try:
            for t in mon.types or []:
                if t is None:
                    continue
                mult = t.damage_multiplier(*[x for x in (opponent.types or []) if x is not None])
                best = max(best, 90.0 * mult)
        except Exception:
            return 0.0
        return best

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
                    count += v or 1
                elif "toxicspikes" in name:
                    count += v or 1
                elif "stickyweb" in name:
                    count += 1
        except Exception:
            pass
        return count

    def _hazard_pressure(self, side_conditions: Dict) -> float:
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
        # Clear if we are meaningfully pressured; downrank if we out-hazard the opponent
        value = 0.0
        if our_pressure >= 1.0:
            value += 110.0 + 30.0 * (our_pressure - max(0.0, opp_pressure - 0.5))
        if self._has_side_condition(battle.side_conditions, "stickyweb"):
            value += 80.0
        return max(0.0, value)

    def _rapid_spin_value(self, battle: AbstractBattle) -> float:
        # Included for completeness if moveset differs
        our_pressure = self._hazard_pressure(battle.side_conditions)
        val = 0.0
        if our_pressure > 0.0:
            val = 95.0 + 35.0 * our_pressure
        return max(0.0, val)

    # -----------------------
    # Damage helpers
    # -----------------------
    def _move_damage_score(self, move, attacker, defender) -> float:
        base_power = move.base_power or 0
        accuracy = move.accuracy if move.accuracy is not None else 1.0
        if base_power <= 0:
            return 0.5

        type_multiplier = self._type_effectiveness(move, defender)

        # STAB
        stab = 1.0
        try:
            if move.type is not None and attacker is not None and attacker.types is not None:
                if move.type in attacker.types:
                    stab = 1.5
        except Exception:
            stab = 1.0

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

    def _move_damage_score_with_tera(self, move, attacker, defender, tera_type) -> float:
        base_power = move.base_power or 0
        accuracy = move.accuracy if move.accuracy is not None else 1.0
        if base_power <= 0:
            return 0.5

        type_multiplier = self._type_effectiveness(move, defender)

        # STAB with tera
        stab = 1.0
        try:
            orig_types = attacker.types or []
            if move.type is not None:
                is_orig = move.type in orig_types
                is_tera = (tera_type is not None) and (move.type == tera_type)
                if is_orig and is_tera:
                    stab = 2.0
                elif is_orig or is_tera:
                    stab = 1.5
        except Exception:
            stab = 1.0

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

    def _rough_damage_score_types(self, move, attacker_types, defender_types, attacker=None, defender=None) -> float:
        base_power = move.base_power or 0
        if base_power <= 0:
            return 0.0
        acc = move.accuracy if move.accuracy is not None else 1.0

        stab = 1.0
        try:
            if move.type is not None:
                for t in attacker_types or []:
                    if t is not None and t == move.type:
                        stab = 1.5
                        break
        except Exception:
            pass

        type_mult = self._damage_multiplier_of_type(getattr(move, "type", None), defender_types)

        atk_boost = 0
        def_boost = 0
        try:
            if attacker and hasattr(attacker, "boosts") and attacker.boosts is not None:
                if move.category.name.lower() == "special":
                    atk_boost = attacker.boosts.get("spa", 0) or 0
                else:
                    atk_boost = attacker.boosts.get("atk", 0) or 0
            if defender and hasattr(defender, "boosts") and defender.boosts is not None:
                if move.category.name.lower() == "special":
                    def_boost = defender.boosts.get("spd", 0) or 0
                else:
                    def_boost = defender.boosts.get("def", 0) or 0
        except Exception:
            pass

        atk_mult = self._stage_multiplier(atk_boost)
        def_mult = self._stage_multiplier(def_boost)
        boost_factor = atk_mult / max(0.5, def_mult)

        return float(base_power) * float(acc) * stab * type_mult * boost_factor

    # -----------------------
    # State and inference
    # -----------------------
    def _get_battle_state(self, battle: AbstractBattle) -> Dict:
        key = battle.battle_tag if hasattr(battle, "battle_tag") else str(id(battle))
        if key not in self._state:
            self._state[key] = {
                "tera_used": False,
                "opp_last_move_id": None,
                "opp_repeat_count": 0,
                "intent": None,
                "intent_turns": 0,
            }
        return self._state[key]

    def _update_opponent_repeat_tracker(self, battle: AbstractBattle):
        st = self._get_battle_state(battle)
        opp = battle.opponent_active_pokemon
        if not opp:
            return
        last_move = None
        try:
            last_move = getattr(opp, "last_move_used", None) or getattr(opp, "last_move", None)
        except Exception:
            last_move = None
        move_id = None
        try:
            if last_move is not None:
                move_id = getattr(last_move, "id", None)
        except Exception:
            move_id = None

        if move_id is None:
            st["opp_repeat_count"] = max(0, st.get("opp_repeat_count", 0) - 1)
            return

        if st.get("opp_last_move_id") == move_id:
            st["opp_repeat_count"] = st.get("opp_repeat_count", 0) + 1
        else:
            st["opp_last_move_id"] = move_id
            st["opp_repeat_count"] = 1

    def _is_endgame(self, battle: AbstractBattle) -> bool:
        try:
            alive = 0
            for mon in (battle.opponent_team or {}).values():
                if mon is not None and not mon.fainted:
                    alive += 1
            return alive <= 2
        except Exception:
            return False

    def _danger_multiplier(self, our_pokemon, opp_pokemon) -> float:
        if our_pokemon is None or opp_pokemon is None:
            return 1.0
        try:
            worst = 1.0
            for t in opp_pokemon.types or []:
                if t is None:
                    continue
                mult = t.damage_multiplier(*[x for x in (our_pokemon.types or []) if x is not None])
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
            our_spe = (our_mon.stats or {}).get("spe") or (our_mon.base_stats or {}).get("spe", 0)
            opp_spe = (opp_mon.stats or {}).get("spe") or (opp_mon.base_stats or {}).get("spe", 0)
            boost = 0
            if our_mon.boosts and "spe" in our_mon.boosts:
                boost = our_mon.boosts["spe"] or 0
            if boost != 0:
                our_spe *= self._stage_multiplier(boost)
            return our_spe >= opp_spe
        except Exception:
            return False

    def _physical_lean(self, mon) -> float:
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

    def _is_gholdengo(self, mon) -> bool:
        try:
            return (mon.species or "").lower() == "gholdengo"
        except Exception:
            return False

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

    def _damage_multiplier_of_type(self, move_type, defender_types: List) -> float:
        if move_type is None or not defender_types:
            return 1.0
        try:
            return move_type.damage_multiplier(*defender_types)
        except Exception:
            return 1.0

    def _would_be_ko_likely(self, battle: AbstractBattle) -> bool:
        active = battle.active_pokemon
        if not active:
            return False
        opp_best = self._opponent_reply_damage_score(battle, defender_types=active.types or [])
        hp_frac = active.current_hp_fraction or 1.0
        # Rough mapping from score to fraction; conservative
        return self._normalize_damage_score(opp_best) > (hp_frac + 0.15)
