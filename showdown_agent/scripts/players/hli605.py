# hli605.py

import math
from poke_env.player import Player
from poke_env.battle import AbstractBattle

# Rationale for team selection:
# This is a balanced Gen9 Ubers team that is fully compliant with the format rules,
# excluding any AG-banned Pokemon like Miraidon or Koraidon.
# - Necrozma-Dusk-Mane: A premier physical tank and Stealth Rock setter. Its Prism Armor
#   ability and steel typing provide excellent defensive utility against many threats.
# - Kyogre: A top-tier special wallbreaker. With a Choice Scarf, it acts as a potent
#   revenge killer, outspeeding and threatening a huge portion of the metagame with
#   its powerful rain-boosted Water-type attacks.
# - Arceus-Fairy: An elite defensive pivot and check to the format's many Dragon-types.
#   It can spread status with Will-O-Wisp, heal with Recover, and serve as a win condition.
# - Eternatus: A fantastic special wall and utility Pokemon. It can set Toxic Spikes,
#   has reliable recovery, and its high Speed and Pressure ability wear down opponents.
# - Great Tusk: Provides crucial hazard control with Rapid Spin. It also serves as a
#   strong physical wall and offensive threat with its excellent typing and stats.
# - Kingambit: An exceptional late-game cleaner. Supreme Overlord makes it devastating
#   once its teammates are down, and Sucker Punch provides powerful priority to
#   pick off faster, weakened foes.
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

    def _get_danger_level(self, pokemon, opponent):
        """Calculates the highest damage multiplier against a pokemon."""
        if not pokemon or not opponent or not opponent.types:
            return 1.0
        return max(
            (pokemon.damage_multiplier(opp_type) for opp_type in opponent.types if opp_type),
            default=1.0
        )

    def _evaluate_move(self, move, me, opponent, battle):
        """Calculates a score for a given move."""
        if move.category.name == 'STATUS':
            # Give base scores to key status moves
            if move.id in ['stealthrock', 'toxicspikes']:
                # Only score if hazards aren't already up
                if move.id not in battle.opponent_side_conditions:
                    return 95
            if move.id == 'willowisp' and opponent.status is None and 'Fire' not in opponent.types:
                return 90
            if move.id in ['swordsdance', 'calmmind']:
                return 85 # Scored higher in dedicated functions
            if move.id in ['recover', 'morningsun']:
                return 100 # Scored higher in dedicated functions
            return 0

        # Factor in base power, type effectiveness, and STAB for damaging moves
        power = move.base_power
        
        # Special handling for variable power moves
        if move.id == 'waterspout':
            power = 150 * (me.current_hp_fraction)
        
        type_multiplier = opponent.damage_multiplier(move)
        score = power * type_multiplier
        
        if move.type in me.types:
            score *= 1.5
            
        # Bonus for priority moves to finish off low-health opponents
        if move.priority > 0 and opponent.current_hp_fraction < 0.3:
            score *= 1.3
            
        # Bonus for Knock Off if opponent likely has an item
        if move.id == 'knockoff' and opponent.item:
            score *= 1.2

        return score

    def _get_best_switch(self, battle: AbstractBattle) -> (object, float):
        """Finds the best pokemon to switch into."""
        opponent = battle.opponent_active_pokemon
        if not opponent or not battle.available_switches:
            return None, -math.inf

        best_switch = None
        max_score = -math.inf

        for pokemon in battle.available_switches:
            defensive_score = 1 / max(self._get_danger_level(pokemon, opponent), 0.25) # Avoid division by zero
            
            offensive_score = 0
            for move_type in pokemon.types:
                if move_type:
                    offensive_score = max(offensive_score, opponent.damage_multiplier(move_type))

            score = (defensive_score * 1.5) + offensive_score
            score *= pokemon.current_hp_fraction # Prioritize healthy switches

            if score > max_score:
                max_score = score
                best_switch = pokemon

        return best_switch, max_score

    def choose_move(self, battle: AbstractBattle):
        me = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        # 1. Handle forced switches
        if battle.force_switch:
            best_switch, _ = self._get_best_switch(battle)
            return self.create_order(best_switch) if best_switch else self.choose_random_move(battle)

        if not me or not opponent:
            return self.choose_random_move(battle)

        danger_level = self._get_danger_level(me, opponent)

        # 2. High-Priority Recovery: Heal if below 65% health and not facing an immediate KO threat
        if me.current_hp_fraction < 0.65 and danger_level < 2:
            recovery_moves = [m for m in battle.available_moves if m.id in ['recover', 'morningsun']]
            if recovery_moves:
                return self.create_order(recovery_moves[0])

        # 3. Terastallization Logic
        # For poke-env v0.10.0, the raw request is stored in `self._last_request`.
        can_tera = False
        if hasattr(self, '_last_request') and self._last_request:
            if "active" in self._last_request and self._last_request["active"]:
                if "canTerastallize" in self._last_request["active"][0]:
                    can_tera = True
        
        if can_tera:
            # Defensive Tera: If in extreme danger (>=4x weak) and Tera can save you
            if danger_level >= 4:
                try:
                    # Get the Type object for our Tera type to calculate future damage
                    tera_type_obj = self.dex.types[me.tera_type.lower()]
                    tera_danger = opponent.damage_multiplier(tera_type_obj)

                    if tera_danger < 1:
                        # Find best move to use post-Tera
                        best_move, _ = max(
                            ((m, self._evaluate_move(m, me, opponent, battle)) for m in battle.available_moves),
                            key=lambda x: x[1], default=(None, 0)
                        )
                        if best_move:
                            return self.create_order(best_move, terastallize=True)
                except (AttributeError, KeyError):
                    # Failsafe in case self.dex isn't ready or type is invalid
                    pass

            # Offensive Tera: If it guarantees a KO on a key threat
            for move in battle.available_moves:
                if move.type.name.upper() == me.tera_type.upper():
                    tera_score = self._evaluate_move(move, me, opponent, battle)
                    if move.type not in me.types:
                        tera_score *= 2.0
                    else:
                        tera_score *= (2.0 / 1.5)

                    if tera_score > 300 and opponent.current_hp_fraction < 0.6:
                         return self.create_order(move, terastallize=True)

        # 4. Setup Opportunity: Use Calm Mind/Swords Dance if safe and opponent is passive
        if danger_level < 1:
            setup_moves = [m for m in battle.available_moves if m.id in ['swordsdance', 'calmmind']]
            if setup_moves:
                stat = 'atk' if setup_moves[0].id == 'swordsdance' else 'spa'
                if me.boosts[stat] < 6:
                    return self.create_order(setup_moves[0])

        # 5. Evaluate all available moves and find the best one
        best_move, best_score = max(
            ((m, self._evaluate_move(m, me, opponent, battle)) for m in battle.available_moves),
            key=lambda x: x[1], default=(None, 0)
        )

        # 6. Decide between attacking and switching
        should_switch = False
        if not battle.trapped:
            best_switch, switch_score = self._get_best_switch(battle)
            if danger_level >= 2 and best_score < 150:
                if best_switch and (1 / max(self._get_danger_level(best_switch, opponent), 0.25)) > 2:
                    should_switch = True
            elif best_score < 50 and switch_score > 2.5:
                should_switch = True
        
        if should_switch:
            return self.create_order(best_switch)

        # 7. Default to the best evaluated move
        if best_move:
            return self.create_order(best_move)

        # 8. Fallback to random if no other choice is made
        return self.choose_random_move(battle)
