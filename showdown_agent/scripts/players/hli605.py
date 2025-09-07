# hli605.py

import math
from poke_env.player import Player
from poke_env.battle import AbstractBattle

# The team is well-balanced for a bulky offense strategy. No changes are needed.
team = """
Necrozma-Dusk-Mane @ Leftovers
Ability: Prism Armor
Tera Type: Steel
EVs: 252 HP / 252 Def / 4 SpD
Impish Nature
- Stealth Rock
- Sunsteel Strike
- Morning Sun
- Earthquake

Kyogre @ Choice Scarf
Ability: Drizzle
Tera Type: Water
EVs: 252 SpA / 4 SpD / 252 Spe
Timid Nature
IVs: 0 Atk
- Water Spout
- Origin Pulse
- Ice Beam
- Thunder

Arceus-Fairy @ Pixie Plate
Ability: Multitype
Tera Type: Fairy
EVs: 252 HP / 200 Def / 56 Spe
Bold Nature
IVs: 0 Atk
- Judgment
- Recover
- Will-O-Wisp
- Calm Mind

Eternatus @ Black Sludge
Ability: Pressure
Tera Type: Poison
EVs: 252 HP / 4 Def / 252 SpD
Calm Nature
IVs: 0 Atk
- Dynamax Cannon
- Flamethrower
- Recover
- Toxic Spikes

Great Tusk @ Leftovers
Ability: Protosynthesis
Tera Type: Ground
EVs: 252 HP / 252 Def / 4 Spe
Impish Nature
- Rapid Spin
- Headlong Rush
- Knock Off
- Body Press

Kingambit @ Black Glasses
Ability: Supreme Overlord
Tera Type: Dark
EVs: 252 Atk / 4 SpD / 252 Spe
Adamant Nature
- Kowtow Cleave
- Sucker Punch
- Iron Head
- Swords Dance
"""


class CustomAgent(Player):
    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)

    def _get_opponent_threat_level(self, pokemon, opponent):
        """Calculates threat level, factoring in opponent's boosts."""
        if not pokemon or not opponent or not opponent.types:
            return 1.0
        
        atk_boost = opponent.boosts.get('atk', 0)
        spa_boost = opponent.boosts.get('spa', 0)
        boost_multiplier = 1 + (0.5 * max(atk_boost, spa_boost)) if max(atk_boost, spa_boost) > 0 else 1

        return max((pokemon.damage_multiplier(t) for t in opponent.types if t), default=1.0) * boost_multiplier

    def _evaluate_move(self, move, me, opponent, battle):
        """Scores a move with added strategic nuance."""
        score = 0
        if move.category.name == 'STATUS':
            if move.id in ['recover', 'morningsun']:
                score = 70 * (1 - me.current_hp_fraction)
            elif move.id == 'stealthrock' and 'stealthrock' not in battle.opponent_side_conditions:
                score = 85 - battle.turn * 2
            elif move.id == 'willowisp' and opponent.status is None and 'Fire' not in opponent.types:
                score = 95 if opponent.base_stats['atk'] > opponent.base_stats['spa'] else 70
            elif move.id in ['swordsdance', 'calmmind']:
                score = 90
            elif move.id == 'rapidspin' and any(battle.side_conditions):
                score = 90
            return score

        power = move.base_power or 0
        if move.id == 'waterspout':
            power = 150 * me.current_hp_fraction
        
        type_multiplier = opponent.damage_multiplier(move)
        
        score = power * type_multiplier
        if type_multiplier >= 2:
            score *= 1.5  # Heavily prioritize super-effective hits

        if move.type in me.types:
            score *= 1.5
            
        # MODIFIED LINE: Added a check to ensure both speed stats are known before comparing.
        if (move.priority > 0 and 
            opponent.current_hp_fraction < 0.4 and 
            me.stats.get('spe') is not None and 
            opponent.stats.get('spe') is not None and 
            opponent.stats['spe'] > me.stats['spe']):
            score *= 1.5 # Bonus for revenge-killing with priority
            
        if move.id == 'knockoff' and opponent.item:
            score *= 1.2

        return score

    def _get_best_switch(self, battle: AbstractBattle):
        """Finds the best switch-in, heavily prioritizing defensive synergy."""
        opponent = battle.opponent_active_pokemon
        if not opponent or not battle.available_switches:
            return None

        best_switch = None
        max_score = -math.inf

        for pokemon in battle.available_switches:
            threat_level = self._get_opponent_threat_level(pokemon, opponent)
            
            if threat_level == 0: defensive_score = 5.0  # Immunity is a massive bonus
            else: defensive_score = 1 / threat_level

            offensive_score = max((opponent.damage_multiplier(m) for m in pokemon.moves.values() if m.base_power > 0), default=0)

            score = (3 * defensive_score) + offensive_score + pokemon.current_hp_fraction
            if score > max_score:
                max_score = score
                best_switch = pokemon
        return best_switch

    def _handle_opponent_setup(self, battle: AbstractBattle):
        """Identifies and counters a dangerously boosted opponent."""
        opponent = battle.opponent_active_pokemon
        atk_boost = opponent.boosts.get('atk', 0)
        spa_boost = opponent.boosts.get('spa', 0)

        if atk_boost >= 2 or spa_boost >= 2:
            if atk_boost >= 2:
                wow_move = next((m for m in battle.available_moves if m.id == 'willowisp'), None)
                if wow_move: return self.create_order(wow_move)

            best_counter = self._get_best_switch(battle)
            if best_counter: return self.create_order(best_counter)
        
        return None

    def choose_move(self, battle: AbstractBattle):
        me = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        if battle.force_switch:
            return self.create_order(self._get_best_switch(battle) or self.choose_random_move(battle))

        if not me or not opponent:
            return self.choose_random_move(battle)

        # 1. CRITICAL THREAT: Counter an opponent's setup sweep.
        setup_counter_move = self._handle_opponent_setup(battle)
        if setup_counter_move:
            return setup_counter_move

        # 2. SELF-PRESERVATION: Heal if at moderate health and not in immediate danger.
        if me.current_hp_fraction < 0.65 and self._get_opponent_threat_level(me, opponent) < 2:
            recovery_move = next((m for m in battle.available_moves if m.id in ['recover', 'morningsun']), None)
            if recovery_move:
                return self.create_order(recovery_move)

        # 3. HAZARD CONTROL: Clear hazards if they are present and we can do so safely.
        if battle.side_conditions and 'Ghost' not in opponent.types:
            spin_move = next((m for m in battle.available_moves if m.id == 'rapidspin'), None)
            if spin_move: return self.create_order(spin_move)

        # 4. STRATEGIC PLAY: Terastallize to turn the tables.
        can_tera = hasattr(self, '_last_request') and self._last_request and "active" in self._last_request and self._last_request["active"] and "canTerastallize" in self._last_request["active"][0]
        if can_tera and self._get_opponent_threat_level(me, opponent) >= 2:
            try:
                tera_type_obj = self.dex.types[me.tera_type.lower()]
                if opponent.damage_multiplier(tera_type_obj) < 1:
                    best_move, _ = max(((m, self._evaluate_move(m, me, opponent, battle)) for m in battle.available_moves), key=lambda x: x[1], default=(None, 0))
                    if best_move: return self.create_order(best_move, terastallize=True)
            except (AttributeError, KeyError): pass

        # 5. CORE LOGIC: Evaluate best move vs. best switch.
        if not battle.available_moves:
             return self.create_order(self._get_best_switch(battle) or self.choose_random_move(battle))

        best_move, best_score = max(((m, self._evaluate_move(m, me, opponent, battle)) for m in battle.available_moves), key=lambda x: x[1])

        if not battle.trapped:
            threat_level = self._get_opponent_threat_level(me, opponent)
            if threat_level >= 2 and best_score < 180: # Be more willing to switch if our attack isn't a KO
                best_switch = self._get_best_switch(battle)
                if best_switch: return self.create_order(best_switch)
            elif best_score < 50: # Switch if we're completely walled
                best_switch = self._get_best_switch(battle)
                if best_switch: return self.create_order(best_switch)

        # 6. EXECUTE: Use the best move found.
        return self.create_order(best_move)
