# arena_combat.py - v1.1.1
# Combat Engine with Inventory Consumption & 'Use' Verb Fix

import random
import asyncio
import json
import logging
import sys
import os

# --- Path Injection (Allows running from within the package directory) ---
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_grid.grid_utils import format_text, tag_msg, format_item, C_RED, C_GREEN, C_YELLOW, C_CYAN

# --- Config & Logging Setup ---
from pathlib import Path

def _load_config():
    base_dir = Path(__file__).resolve().parent
    root_dir = base_dir.parent
    for candidate in [
        base_dir / 'config.json',
        root_dir / 'config.json',
        base_dir / 'config.json.example',
        root_dir / 'config.json.example',
    ]:
        if candidate.is_file():
            try:
                with open(candidate, 'r') as f:
                    return json.load(f)
            except Exception:
                continue
    return {}

CONFIG = _load_config()

log_level_str = CONFIG.get('logging', {}).get('level', 'INFO').upper()
log_level = getattr(logging, log_level_str, logging.INFO)

logger = logging.getLogger("arena_combat")
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# File Handler
_log_path = Path(__file__).resolve().parent / 'grid_combat.log'
fh = logging.FileHandler(_log_path)
fh.setFormatter(formatter)
logger.addHandler(fh)

# Console Handler
ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)


class Entity:
    def __init__(self, name, db_record=None, is_npc=False, **kwargs):
        self.name = name
        self.is_npc = is_npc
        if db_record is None:
            db_record = {}
        self.char_name = kwargs.get('char_name') or (db_record.get('name') if isinstance(db_record, dict) else getattr(db_record, 'name', None)) or name
        self.nickname = kwargs.get('nickname') or name
        # v1.8.0: Starting stats are 1
        self.cpu = kwargs.get('cpu', db_record.get('cpu', 1))
        self.ram = kwargs.get('ram', db_record.get('ram', 1))
        self.bnd = kwargs.get('bnd', db_record.get('bnd', 1))
        self.sec = kwargs.get('sec', db_record.get('sec', 1))
        self.alg = kwargs.get('alg', db_record.get('alg', 1))
        self.bio = kwargs.get('bio', db_record.get('bio', 'A rogue process.' if is_npc else 'A mindless drone.'))
        
        try:
            self.inventory = json.loads(db_record.get('inventory', '[]')) if isinstance(db_record.get('inventory'), str) else (kwargs.get('inventory') or [])
        except:
            self.inventory = []
            
        # v1.8.1: HP = (SumStats * 6) + 20 (Calibrated for 6-10 STK)
        total_stats = self.cpu + self.ram + self.bnd + self.sec + self.alg
        self.max_hp = kwargs.get('max_hp', db_record.get('max_hp', (total_stats * 6) + 20))
        self.hp = kwargs.get('hp', db_record.get('hp', self.max_hp))
        
        # v1.8.0: Unit Power (uP) and Stability
        self.up = kwargs.get('up', db_record.get('power', 100)) # Current Unit Power
        self.max_up = kwargs.get('max_up', 1000) # Default cap for Arena encounters, though uncapped in persistence
        self.stability = kwargs.get('stability', 100.0) # Percentage
        
        self.alignment = kwargs.get('alignment', db_record.get('alignment', 0))
        self.zone = kwargs.get('zone', "The_Arena")
        self.status = kwargs.get('status', "Normal")
        raw_skills = kwargs.get('skills', db_record.get('skills', {}))
        self.skills = raw_skills if isinstance(raw_skills, dict) else {}
        self.command_queued = None
        self.last_attacker_name = None
        logger.debug(f"Entity '{self.name}' initialized. HP: {self.hp}/{self.max_hp}, UP: {self.up}, NPC: {self.is_npc}")

    @property
    def is_alive(self):
        return self.hp > 0

    @property
    def alive(self):
        return self.is_alive

class CombatEngine:
    def __init__(self, match_id, network_prefix, send_callback, llm=None):
        self.match_id = match_id
        self.prefix = network_prefix 
        self.send_callback = send_callback 
        self.llm = llm
        self.turn_events = []
        self.entities = {}
        self.turn = 1
        self.active = False
        
        # v1.8.0: Expanded verb map
        self.verb_map = {
            "kinetic": ["attack", "strike", "hit", "punch", "smash", "bash"],
            "cyber": ["hack", "corrupt", "inject", "scramble", "scan"],
            "exploit": ["exploit", "zeroday", "0day"],
            "flee": ["flee", "retreat", "escape", "run"],
            "surrender": ["surrender", "yield", "quit"],
            "evade": ["evade", "dodge", "duck"],
            "defend": ["defend", "block", "brace", "prepare"],
            "support": ["use", "consume", "repair", "heal", "patch"],
            "social": ["speak", "yell", "taunt", "broadcast"]
        }
        logger.info(f"CombatEngine initialized for match: {self.match_id}")

    def is_combat_verb(self, verb: str) -> bool:
        v = verb.lower()
        return any(v in aliases for aliases in self.verb_map.values())

    def get_entity(self, name: str):
        if not name:
            return None
        if name in self.entities:
            return self.entities[name]
        name_low = name.lower()
        for k, ent in self.entities.items():
            if k.lower() == name_low:
                return ent
        for ent in self.entities.values():
            if getattr(ent, 'char_name', '').lower() == name_low or getattr(ent, 'nickname', '').lower() == name_low:
                return ent
        return None

    def add_entity(self, entity: Entity):
        self.entities[entity.name] = entity
        logger.info(f"Added {entity.name} to match {self.match_id}")

    async def broadcast_state(self) -> str:
        logger.debug(f"Match {self.match_id} broadcasting state for Turn {self.turn}")
        raw_state = f"TURN {self.turn} | LOC: {list(self.entities.values())[0].zone} | "
        for e in self.entities.values():
            if e.is_alive:
                hp_color = C_GREEN if e.hp > (e.max_hp/2) else C_RED
                hp_str = format_text(f"{e.hp}/{e.max_hp}", hp_color)
                raw_state += f"{e.name} [HP:{hp_str}] "
        return raw_state

    def queue_command(self, entity_name: str, raw_command: str):
        actor = self.get_entity(entity_name)
        if not actor or not actor.is_alive:
            logger.debug(f"Command ignored: {entity_name} is dead or not in match.")
            return
        
        cmd = raw_command.strip()
        if not cmd:
            return

        # Strip prefix if present (e.g., "!a strike Enemy" -> "strike Enemy")
        if self.prefix and cmd.lower().startswith(self.prefix.lower()):
            cmd = cmd[len(self.prefix):].strip()

        parts = cmd.split(maxsplit=1)
        if not parts:
            return

        verb = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        action_intent = "invalid"
        for intent, aliases in self.verb_map.items():
            if verb in aliases:
                action_intent = intent
                break
                
        actor.command_queued = {"intent": action_intent, "raw_verb": verb, "args": args}
        logger.info(f"Command Queued | {actor.name} -> [{action_intent}] '{verb}' args: '{args}'")

    async def resolve_turn(self):
        logger.info(f"--- Resolving Turn {self.turn} for Match {self.match_id} ---")
        turn_order = []
        for name, ent in self.entities.items():
            if ent.is_alive:
                # v1.8.0: Initiative based on CPU, RAM, BND, SEC
                init_base = (ent.cpu + ent.ram + ent.bnd + ent.sec) / 4
                roll = random.randint(1, 10) + init_base
                turn_order.append((roll, ent))
                logger.debug(f"Initiative Roll: {name} rolled {roll:.1f} (Base: {init_base:.1f})")
        
        turn_order.sort(key=lambda x: x[0], reverse=True) 

        narrative_log = []
        for roll, actor in turn_order:
            if not actor.is_alive or actor.status == "Stunned": continue

            cmd = actor.command_queued
            if not cmd:
                logger.warning(f"Timeout: {actor.name} submitted no command.")
                narrative_log.append(f"{actor.name}'s AI core timed out. (Skipped turn)")
                self.turn_events.append({"type": "timeout", "actor": actor.name})
                continue

            if actor.status == "Evading": actor.status = "Normal"

            intent = cmd["intent"]
            target_name = cmd["args"].split()[0] if cmd["args"] else None

            # v1.8.0: uP Cost Logic
            up_costs = {"kinetic": 10, "cyber": 15, "exploit": 50, "flee": 20, "evade": 5, "defend": 5, "support": 5}
            cost = up_costs.get(intent, 0)
            
            if actor.up < cost:
                narrative_log.append(f"{actor.name} has insufficient power for {cmd['raw_verb']}! (Action Failed)")
                self.turn_events.append({"type": "insufficient_power", "actor": actor.name, "verb": cmd['raw_verb']})
                actor.command_queued = None
                continue
                
            actor.up -= cost
            logger.debug(f"Executing intent: {intent} for {actor.name} (uP remains: {actor.up})")

            if intent == "kinetic":
                narrative_log.append(self._execute_attack(actor, target_name, mode="kinetic"))
            elif intent == "cyber":
                narrative_log.append(self._execute_attack(actor, target_name, mode="cyber"))
            elif intent == "exploit":
                # Check for Zero-Day chain in inventory
                chain_item = next((i for i in actor.inventory if "Zero-Day" in i or "exploit" in i.lower()), None)
                if chain_item:
                    actor.inventory.remove(chain_item)
                    narrative_log.append(self._execute_attack(actor, target_name, mode="exploit"))
                else:
                    narrative_log.append(f"{actor.name} attempts an exploit but has no Zero-Day chain! (Action Failed)")
            
            elif intent == "evade":
                actor.status = "Evading"
                narrative_log.append(f"{format_text(actor.name, C_CYAN)} enters evasion mode. (uP耗: 5)")
                self.turn_events.append({"type": "defend_stance", "actor": actor.name, "mode": "evade"})
            elif intent == "defend":
                actor.status = "Defending"
                narrative_log.append(f"{format_text(actor.name, C_CYAN)} buffers incoming damage. (uP耗: 5)")
                self.turn_events.append({"type": "defend", "actor": actor.name})
            elif intent == "flee":
                # v1.8.0: Flee attempt
                if random.random() > 0.4: # 60% success
                    actor.hp = 0 # Mark as out of match
                    narrative_log.append(f"{format_text(actor.name, C_YELLOW)} successfully extracted from the combat zone!")
                    self.turn_events.append({"type": "flee", "actor": actor.name, "success": True})
                else:
                    narrative_log.append(f"{actor.name} tried to flee but the escape route is locked!")
                    self.turn_events.append({"type": "flee", "actor": actor.name, "success": False})
            elif intent == "surrender":
                actor.hp = 0
                actor.status = "Surrendered"
                narrative_log.append(f"{format_text(actor.name, C_RED)} has YIELDED. Combat terminated for unit.")
                self.turn_events.append({"type": "surrender", "actor": actor.name})
                
            elif intent == "support":
                if not target_name:
                    narrative_log.append(f"{actor.name} tries to {cmd['raw_verb']}, but didn't specify what to use!")
                else:
                    inventory_lower = [i.lower() for i in actor.inventory]
                    item_key = target_name.lower().replace("_", " ")
                    if item_key in inventory_lower:
                        exact_item = next(i for i in actor.inventory if i.lower().replace("_", " ") == item_key)
                        actor.inventory.remove(exact_item)
                        heal = (actor.ram + actor.alg) * 5
                        actor.hp = min(actor.max_hp, actor.hp + heal)
                        narrative_log.append(f"{format_text(actor.name, C_CYAN)} used {format_item(exact_item)}, restoring {format_text(str(heal), C_GREEN)} HP!")
                        self.turn_events.append({"type": "support", "actor": actor.name, "item": exact_item, "heal": heal, "hp": actor.hp})
                    else:
                        narrative_log.append(f"{actor.name} searches for '{target_name}' but fails to locate it!")
                        
            elif intent == "social":
                speech = cmd["args"][:150]
                narrative_log.append(f"{format_text(actor.name, C_CYAN)} broadcasts: \"{format_text(speech, C_YELLOW)}\"")
                self.turn_events.append({"type": "social", "actor": actor.name, "text": speech})
            else:
                narrative_log.append(f"{actor.name} attempted invalid opcode '{cmd['raw_verb']}'.")

            actor.command_queued = None

        report_lines = []
        if self.llm and hasattr(self.llm, 'generate_turn_battle_report'):
            try:
                combatants = list(self.entities.keys())
                report_lines = await self.llm.generate_turn_battle_report(
                    self.turn, self.turn_events, combatants, fallback_lines=narrative_log
                )
            except Exception as e:
                logger.warning(f"Error calling LLM battle report: {e}")
                report_lines = narrative_log

        lines_to_send = report_lines if report_lines else narrative_log

        await self.send_callback(tag_msg(f"TURN {self.turn} RESULTS:", tags=['ARENA', 'COMBAT']))
        for line in lines_to_send:
            await self.send_callback(f"⚔️ {line}")
            await asyncio.sleep(0.5) 

        self.turn_events = []
        self.turn += 1
        is_active = self._check_match_status()
        if not is_active:
            logger.info(f"Match {self.match_id} triggered completion condition.")
        return is_active

    def _execute_attack(self, attacker, target_name: str, mode: str = "kinetic"):
        if isinstance(attacker, str):
            attacker = self.entities.get(attacker)
        if not attacker:
            return ""

        mode_clean = mode.lower() if isinstance(mode, str) else "kinetic"
        if mode_clean in ["strike", "kinetic"]:
            mode = "kinetic"
        elif mode_clean in ["scan", "cyber"]:
            mode = "cyber"
        elif mode_clean in ["exploit"]:
            mode = "exploit"

        # --- SMART TARGETING LOGIC ---
        if not target_name:
            if attacker.last_attacker_name and attacker.last_attacker_name in self.entities:
                target_name = attacker.last_attacker_name
            else:
                potential_targets = [e.name for e in self.entities.values() if e.name != attacker.name and e.is_alive]
                if potential_targets: target_name = potential_targets[0]

        if not target_name or target_name not in self.entities: 
            return f"{attacker.name}'s connection timed out during targeting."
        
        target = self.entities[target_name]
        if not target.is_alive: 
            return f"{attacker.name} strikes {target.name}'s offline chassis. Disrespectful."

        target.last_attacker_name = attacker.name

        # --- v1.8.0 EVASION / DEFENSE LOGIC ---
        # Mech Balance: Evasion decoupling - uses ALG instead of BND
        # Remediation: Reduced multiplier to 1.0 and cap to 60% to normalize STK
        evade_chance = target.alg * 1.0
        if target.status == "Evading": evade_chance += 30
        evade_chance = min(60.0, evade_chance) # Cap at 60%
        
        if random.randint(1, 100) <= evade_chance: 
            self.turn_events.append({"type": "evade", "actor": attacker.name, "target": target.name, "mode": mode})
            return f"{attacker.name}'s {mode} maneuver was {format_text('EVADED', C_YELLOW)} by {target.name}!"

        # --- v1.8.0 DAMAGE FORMULAS ---
        attacker_skills = getattr(attacker, 'skills', {}) or {}
        target_skills = getattr(target, 'skills', {}) or {}

        if mode == "kinetic":
            raw_dmg = (attacker.cpu * 5) + attacker.ram
            attack_lvl = attacker_skills.get("attack", 0)
            if attack_lvl > 0:
                raw_dmg = int(raw_dmg * (1.0 + 0.10 * attack_lvl))
            protection = target.sec
            verb = "strikes"
        elif mode == "cyber":
            raw_dmg = (attacker.bnd * 5) + attacker.sec
            hack_lvl = attacker_skills.get("hack", 0)
            if hack_lvl > 0:
                raw_dmg = int(raw_dmg * (1.0 + 0.10 * hack_lvl))
            protection = target.bnd
            verb = "injects code into"
        elif mode == "exploit":
            raw_dmg = (attacker.alg + attacker.sec) * 15
            protection = 0
            verb = "executes a ZERO-DAY on"
        else:
            raw_dmg, protection, verb = 1, 0, "pokes"

        final_dmg = max(1, raw_dmg - protection)
        if target.status == "Defending": final_dmg = int(final_dmg * 0.5)

        # Defend skill modifier (+10% damage reduction per level)
        defend_lvl = target_skills.get("defend", 0)
        if defend_lvl > 0:
            final_dmg = max(1, int(final_dmg * (1.0 - 0.10 * defend_lvl)))

        # Crit check via ALG
        is_crit = random.randint(1, 100) <= attacker.alg
        if is_crit:
            final_dmg *= 2
            dmg_str = format_text(f"{final_dmg} CRITICAL DMG", C_RED, bold=True)
        else:
            dmg_str = format_text(f"{final_dmg} DMG", C_RED)

        target.hp -= final_dmg
        is_fatal = target.hp <= 0
        self.turn_events.append({
            "type": "attack" if mode != "exploit" else "exploit",
            "actor": attacker.name,
            "target": target.name,
            "mode": mode,
            "damage": final_dmg,
            "critical": is_crit,
            "fatal": is_fatal
        })
        fatal_str = f" {format_text(target.name + ' HAS BEEN DISCONNECTED!', C_RED, bold=True)}" if is_fatal else ""
        
        return f"{format_text(attacker.name, C_CYAN)} {verb} {target.name} for {dmg_str}!{fatal_str}"

    def _check_match_status(self):
        alive = sum(1 for e in self.entities.values() if e.is_alive)
        return alive > 1
