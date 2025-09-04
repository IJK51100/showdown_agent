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

    def _get_threat_multiplier(self, pokemon, opponent):
        """Calculates the highest damage multiplier against a pokemon from an opponent's STABs."""
        if not opponent or not opponent.types or not pokemon:
            return 1.0
        return max(
            (pokemon.damage_multiplier(opp_type) for opp_type in opponent.types if opp_type),
            default=1.0,
        )

    def _should_switch(self, battle: AbstractBattle) -> bool:
        """Determines if a switch is strategically necessary."""
        me = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        if battle.trapped or not battle.available_switches:
            return False

        threat_level = self._get_threat_multiplier(me, opponent)

        # 1. Extreme danger: 4x weakness or more. Switch is almost always correct.
        if threat_level >= 4:
            return True

        # 2. High danger (2x weakness) AND we can't threaten back effectively.
        best_move, move_score = self._get_best_move(battle, consider_utility=False)
        if threat_level >= 2 and move_score < 120:  # 120 is a heuristic for a strong hit
            return True

        # 3. Low HP and no recovery option, and can't secure a KO.
        has_recovery = any(move.id in ["recover", "morningsun"] for move in battle.available_moves)
        if me.current_hp_fraction < 0.25 and not has_recovery and move_score < opponent.current_hp * 1.5:
             return True
        
        # 4. Choice-locked into a terrible move (e.g., immune or resisted).
        if me.item == "choicescarf" and len(battle.available_moves) == 1:
            locked_move = battle.available_moves[0]
            if opponent.damage_multiplier(locked_move) < 0.5:
                return True

        return False

    def _get_best_switch(self, battle: AbstractBattle) -> (object, float):
        """Finds the best pokemon to switch into based on a scoring system."""
        opponent = battle.opponent_active_pokemon
        if not opponent or not battle.available_switches:
            return None, -math.inf

        best_switch = None
        max_score = -math.inf

        for pokemon in battle.available_switches:
            # 1. Defensive Score: How well can this pokemon take a hit from the opponent's STABs?
            threat_multiplier = self._get_threat_multiplier(pokemon, opponent)
            if threat_multiplier > 0:
                defensive_score = 1 / threat_multiplier
            else:
                defensive_score = 4.0  # Immunity is highly valued

            # 2. Offensive Score: How well can this pokemon threaten the opponent back?
            offensive_score = 0
            for move_type in pokemon.types:
                if move_type:
                    effectiveness = opponent.damage_multiplier(move_type)
                    offensive_score = max(offensive_score, effectiveness)

            # Final score: Combine defense and offense, weighting defense more heavily.
            score = (defensive_score * 2.0) + offensive_score
            score *= pokemon.current_hp_fraction # Prioritize healthy switches

            if score > max_score:
                max_score = score
                best_switch = pokemon

        return best_switch, max_score

    def _get_best_move(self, battle: AbstractBattle, consider_utility=True) -> (object, float):
        """
        Calculates the best move to use, considering offensive, and optionally, utility moves.
        """
        me = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        if not battle.available_moves:
            return None, -1

        best_move = None
        max_score = -math.inf
        threat_level = self._get_threat_multiplier(me, opponent)

        for move in battle.available_moves:
            score = 0
            
            # --- Part 1: Score Damaging Moves ---
            if move.category.name != 'STATUS':
                type_multiplier = opponent.damage_multiplier(move)
                stab_bonus = 1.5 if move.type in me.types else 1.0
                score = move.base_power * type_multiplier * stab_bonus

                # Special move heuristics
                if move.id == 'waterspout':
                    score *= me.current_hp_fraction
                elif move.id == 'suckerpunch':
                    # High-risk, high-reward. Good for finishing off faster foes.
                    is_faster = (opponent.base_stats['spe'] > me.base_stats['spe'])
                    if is_faster and opponent.current_hp_fraction < 0.4:
                        score *= 1.5 # Boost priority for revenge killing
                    else:
                        score *= 0.1 # Risky otherwise, might fail
                elif move.id == 'knockoff':
                    # Item removal is valuable early on
                    if opponent.item and battle.turn < 10:
                        score += 40 # Add a flat bonus equivalent to 40 base power
            
            # --- Part 2: Score Utility Moves (if considered) ---
            elif consider_utility:
                if move.id in ['recover', 'morningsun']:
                    # Heal if below 2/3 health and not severely threatened
                    if me.current_hp_fraction < 0.7 and threat_level < 2:
                        score = 200 # High priority to heal
                elif move.id in ['calmmind', 'swordsdance']:
                    # Set up if it's safe
                    if threat_level < 1.0 and me.current_hp_fraction > 0.8:
                        score = 95
                elif move.id == 'stealthrock':
                    if 'stealthrock' not in battle.opponent_side_conditions:
                        score = 110
                elif move.id == 'toxicspikes':
                    if 'toxicspikes' not in battle.opponent_side_conditions:
                        score = 100
                elif move.id == 'willowisp':
                    # Good against physical attackers
                    is_physical = opponent.base_stats['atk'] > opponent.base_stats['spa']
                    if is_physical and not opponent.status and 'Fire' not in opponent.types:
                        score = 90
                elif move.id == 'rapidspin':
                    # Use if we have hazards and opponent is not a ghost
                    if battle.side_conditions and 'Ghost' not in opponent.types:
                        score = 80
            
            # --- Part 3: Adjust score based on risk ---
            # Don't use risky setup/status moves if threatened
            if threat_level >= 2 and move.category.name == 'STATUS':
                score *= 0.1

            if score > max_score:
                max_score = score
                best_move = move
        
        return best_move, max_score

    def choose_move(self, battle: AbstractBattle):
        # 1. Handle forced switches
        if battle.force_switch:
            best_switch, _ = self._get_best_switch(battle)
            if best_switch:
                return self.create_order(best_switch)
            return self.choose_random_move(battle)

        me = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        if not me or not opponent:
            return self.choose_random_move(battle)

        # 2. Evaluate if a switch is the best strategic option
        if self._should_switch(battle):
            best_switch, _ = self._get_best_switch(battle)
            if best_switch:
                return self.create_order(best_switch)

        # 3. If not switching, find the best move to make
        best_move, best_score = self._get_best_move(battle)

        # 4. Consider an offensive Terastallization to secure a KO
        if battle.can_terastallize and best_move and best_move.category.name != 'STATUS':
            # Heuristic: If a super-effective move can KO with a Tera boost
            # This is a simplified damage estimation
            type_multiplier = opponent.damage_multiplier(best_move)
            stab_bonus = 1.5 if best_move.type in me.types else 1.0
            tera_stab_bonus = 1.5 if best_move.type == me.tera_type else stab_bonus
            
            current_damage_est = best_move.base_power * type_multiplier * stab_bonus
            tera_damage_est = best_move.base_power * type_multiplier * tera_stab_bonus

            # If current damage is not enough, but Tera damage is, and opponent is a threat
            if (current_damage_est < opponent.current_hp and 
                tera_damage_est > opponent.current_hp and
                type_multiplier >= 1.5):
                return self.create_order(best_move, terastallize=True)

        # 5. Execute the best calculated move
        if best_move:
            return self.create_order(best_move)

        # 6. Fallback to a random move if no other logic applies
        return self.choose_random_move(battle)
