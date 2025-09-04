# hli605.py

import math
from poke_env.player import Player
from poke_env.battle import AbstractBattle
# The following imports are removed as they can cause ModuleNotFoundError
# depending on the poke-env version. The necessary data can be accessed
# via object attributes instead of direct class comparisons.
# from poke_env.environment.pokemon import Pokemon
# from poke_env.environment.move import Move
# from poke_env.environment.move_category import MoveCategory

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

    def _get_best_offensive_move(self, battle: AbstractBattle) -> (object, float):
        """
        Calculates the best offensive move to use against the opponent's active pokemon.
        Returns the move and its calculated score.
        """
        opponent = battle.opponent_active_pokemon
        if not opponent:
            return None, -1

        best_move = None
        max_score = -1.0

        for move in battle.available_moves:
            # Check move category by its string name to avoid import issues.
            if move.category.name == 'STATUS':
                continue

            # Calculate type effectiveness using the library's built-in method
            type_multiplier = opponent.damage_multiplier(move)
            
            # Score is base power * type effectiveness
            score = move.base_power * type_multiplier

            # Apply a bonus for STAB (Same-Type Attack Bonus)
            if move.type in battle.active_pokemon.types:
                score *= 1.5

            if score > max_score:
                max_score = score
                best_move = move
        
        return best_move, max_score


    def _get_best_switch(self, battle: AbstractBattle) -> (object, float):
        """
        Finds the best pokemon to switch into based on a scoring system.
        Returns the pokemon and its calculated score.
        """

        opponent = battle.opponent_active_pokemon
        if not opponent or not battle.available_switches:
            return None, -math.inf

        best_switch = None
        max_score = -math.inf

        for pokemon in battle.available_switches:
            # 1. Offensive Score: How well can this pokemon threaten the opponent?
            # We check the effectiveness of our STAB moves against the opponent.
            offensive_score = 0
            for move_type in pokemon.types:
                if move_type:
                    effectiveness = opponent.damage_multiplier(move_type)
                    if effectiveness > offensive_score:
                        offensive_score = effectiveness
            
            # 2. Defensive Score: How well can this pokemon take a hit?
            # We check the effectiveness of the opponent's STAB moves against us.
            highest_threat_multiplier = 0
            for opp_type in opponent.types:
                if opp_type:
                    multiplier = pokemon.damage_multiplier(opp_type)
                    if multiplier > highest_threat_multiplier:
                        highest_threat_multiplier = multiplier
            
            # Invert the multiplier for a defensive score. High resistance = high score.
            # Immunity (multiplier=0) is highly valued.
            if highest_threat_multiplier > 0:
                defensive_score = 1 / highest_threat_multiplier
            else:
                defensive_score = 4.0  # Immunity is twice as good as 4x resistance

            # Final score: Combine offense and defense, weighting defense more heavily for a switch-in.
            # Also, factor in the pokemon's remaining health.
            score = (defensive_score * 1.5) + offensive_score
            score *= pokemon.current_hp_fraction

            if score > max_score:
                max_score = score
                best_switch = pokemon

        return best_switch, max_score

    def choose_move(self, battle: AbstractBattle):
        # If we are forced to switch, we must find the best option.
        if battle.force_switch:
            best_switch, _ = self._get_best_switch(battle)
            if best_switch:
                return self.create_order(best_switch)
            return self.choose_random_move(battle)

        me = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        if not me or not opponent:
            return self.choose_random_move(battle)

        # Priority 1: Use Stealth Rock if it's safe and not already up.
        if 'stealthrock' not in battle.opponent_side_conditions:
            for move in battle.available_moves:
                if move.id == 'stealthrock':
                    # Check if it's "safe" to set up rocks (not weak to opponent's STABs)
                    is_threatened = any(me.damage_multiplier(opp_type) >= 2 for opp_type in opponent.types if opp_type)
                    if not is_threatened:
                        return self.create_order(move)

        # Priority 2: Decide whether to attack or switch.
        best_move, move_score = self._get_best_offensive_move(battle)
        best_switch, switch_score = self._get_best_switch(battle)

        # Determine the threat level to our active pokemon.
        highest_threat_multiplier = max(
            (me.damage_multiplier(opp_type) for opp_type in opponent.types if opp_type), 
            default=1
        )

        # Define conditions for switching.
        should_switch = False
        # Correctly check if the player is trapped using battle.trapped
        if best_switch and not battle.trapped:
            # Condition A: Switch if we are at high risk (2x weak) and have a good counter.
            if highest_threat_multiplier >= 2 and switch_score > 1.5:
                should_switch = True
            # Condition B: Switch if we are at extreme risk (4x weak), even for a mediocre counter.
            elif highest_threat_multiplier >= 4 and switch_score > 0.5:
                should_switch = True
            # Condition C: Switch if we are low on health, can't do much damage, and have a decent switch.
            elif me.current_hp_fraction < 0.3 and move_score < 100 and switch_score > 1.0:
                should_switch = True

        # Priority 3: Execute the decision.
        if should_switch:
            return self.create_order(best_switch)
        elif best_move:
            return self.create_order(best_move)
        
        # Fallback: If no good offensive move, choose any available move.
        return self.choose_random_move(battle)