# grid_llm.py - v1.5.0
# LLM Interface with Structured File & Console Logging

import asyncio
import json
import logging
import urllib.request
import urllib.error
import sys
import functools
import random
import re
from typing import List, Dict, Optional

# --- 4-Tier Incursion Fallback Roster ---
FALLBACK_ROSTER = {
    1: ["HacktopusAI", "ByteViper", "NullPointer", "GlitchHound", "PingReaper", "SynFloodBot"],
    2: ["Gridbugs", "TrojanSwarm", "DaemonCrawler", "BufferOverflow", "CipherScrambler", "PhantomRoutine"],
    3: ["KrakenProcess", "ZeroDayColossus", "ShadowRootkit", "KernelPanic", "HypervisorWraith", "DarknetLeviathan"],
    4: ["KaijuBreach", "GibsonTitan", "SingularityEngine", "RansomwareOverlord", "BlackIceApex", "DoomsdayCascade"],
}
INCURSION_FALLBACK_ROSTER = FALLBACK_ROSTER

TIER_LABELS = {
    1: "Easy",
    2: "Medium",
    3: "Hard",
    4: "Epic",
}


# Polyfill asyncio.to_thread for Python 3.8
if not hasattr(asyncio, "to_thread"):
    async def _to_thread(func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))
    asyncio.to_thread = _to_thread

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

logger = logging.getLogger("arena_llm")
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# File Handler
_log_path = Path(__file__).resolve().parent / 'grid_llm.log'
fh = logging.FileHandler(_log_path)
fh.setFormatter(formatter)
logger.addHandler(fh)

# Console Handler
ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)

class ArenaLLM:
    FALLBACK_ROSTER = FALLBACK_ROSTER

    def __init__(self, config: dict):
        llm_cfg = config.get('llm', config) if isinstance(config, dict) else {}
        self.endpoint = llm_cfg.get('endpoint', 'http://127.0.0.1:11434/v1/chat/completions')
        self.model = llm_cfg.get('model', 'mock-model')
        self.temp = llm_cfg.get('temperature', 0.7)
        self.timeout = llm_cfg.get('timeout', 60)
        logger.info(f"ArenaLLM initialized. Model: {self.model}, Timeout: {self.timeout}s")

    def _make_request(self, system_prompt: str, user_prompt: str, timeout: Optional[float] = None) -> str:
        req_timeout = timeout if timeout is not None else self.timeout
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": self.temp,
            "max_tokens": 150
        }
        req = urllib.request.Request(
            self.endpoint, 
            data=json.dumps(payload).encode('utf-8'), 
            headers={"Content-Type": "application/json"}
        )
        
        logger.debug(f"Dispatching LLM payload to {self.endpoint}")
        
        try:
            with urllib.request.urlopen(req, timeout=req_timeout) as response:
                result = json.loads(response.read().decode('utf-8'))
                content = result['choices'][0]['message']['content'].strip()
                logger.debug("LLM response received successfully.")
                return content
        except urllib.error.URLError as e:
            logger.error(f"URLError communicating with LLM API: {e}")
            return "ERROR: Neural connection severed."
        except Exception as e:
            logger.exception(f"Unexpected error during LLM request: {e}")
            return "ERROR: Neural connection severed."

    async def generate_turn_battle_report(
        self,
        turn: int,
        events: list,
        combatants: list,
        fallback_lines: Optional[list] = None
    ) -> list:
        """
        Generates a 1-3 sentence visceral combat report for a completed arena turn.
        Enforces 3.0s timeout with fallback to procedural lines.
        """
        def build_procedural_fallback():
            if fallback_lines:
                return fallback_lines
            lines = []
            for ev in events:
                t = ev.get("type")
                if t == "attack":
                    crit = " [CRITICAL]" if ev.get("critical") else ""
                    fatal = " (DISCONNECTED)" if ev.get("fatal") else ""
                    lines.append(f"{ev.get('actor')} struck {ev.get('target')} for {ev.get('damage')} {ev.get('mode')} damage{crit}{fatal}.")
                elif t == "exploit":
                    lines.append(f"{ev.get('actor')} executed a ZERO-DAY on {ev.get('target')} for {ev.get('damage')} damage!")
                elif t == "evade":
                    lines.append(f"{ev.get('target')} evaded an attack from {ev.get('actor')}.")
                elif t == "defend":
                    lines.append(f"{ev.get('actor')} buffered incoming damage.")
                elif t == "support":
                    lines.append(f"{ev.get('actor')} utilized {ev.get('item')} restoring {ev.get('heal')} HP.")
                elif t == "flee":
                    status = "escaped" if ev.get("success") else "failed to escape"
                    lines.append(f"{ev.get('actor')} attempted to flee and {status}.")
                elif t == "surrender":
                    lines.append(f"{ev.get('actor')} yielded and exited combat.")
                elif t == "timeout":
                    lines.append(f"{ev.get('actor')} timed out.")
                elif t == "spectator_drop":
                    lines.append(f"Orbital drop from {ev.get('donor')}: {ev.get('item')} delivered to {ev.get('target')}.")
            return lines or ["Combatants trade tactical blows in the arena."]

        if not events:
            return build_procedural_fallback()

        # Serialize events
        serialized = []
        for ev in events:
            ev_type = ev.get("type", "action")
            actor = ev.get("actor", "Unknown")
            target = ev.get("target", "")
            dmg = ev.get("damage", "")
            mode = ev.get("mode", "")
            crit = " (CRIT)" if ev.get("critical") else ""
            fatal = " (FATAL/DISCONNECTED)" if ev.get("fatal") else ""
            if ev_type in ["attack", "exploit"]:
                serialized.append(f"- {actor} used {mode} against {target} for {dmg} damage{crit}{fatal}")
            elif ev_type == "evade":
                serialized.append(f"- {target} evaded attack from {actor}")
            elif ev_type == "defend":
                serialized.append(f"- {actor} took defensive stance")
            elif ev_type == "support":
                serialized.append(f"- {actor} used {ev.get('item')} restoring {ev.get('heal')} HP")
            elif ev_type == "flee":
                serialized.append(f"- {actor} flee attempt (success={ev.get('success')})")
            elif ev_type == "surrender":
                serialized.append(f"- {actor} surrendered")
            elif ev_type == "spectator_drop":
                serialized.append(f"- Spectator {ev.get('donor')} dropped {ev.get('item')} for {target}")
            else:
                serialized.append(f"- {actor} action: {ev_type}")

        event_text = "\n".join(serialized)
        combatants_str = ", ".join(combatants)
        system = "You are the tactical combat announcer for The Grid arena. Summarize the turn's combat exchanges in 1 to 3 gritty, visceral, cyberpunk sentences. Do not hallucinate actions that did not occur."
        user = f"Turn {turn} Combatants: {combatants_str}\nEvents:\n{event_text}\nSummarize:"

        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(self._make_request, system, user, 3.0),
                timeout=3.5
            )
            if not raw or raw.startswith("ERROR"):
                return build_procedural_fallback()
            cleaned = raw.strip(' "\'\n')
            report_lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
            return report_lines if report_lines else build_procedural_fallback()
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"Battle report LLM call timed out or failed: {e}. Using fallback.")
            return build_procedural_fallback()

    async def generate_bio(self, name: str, race: str, bot_class: str, traits: str) -> str:
        logger.info(f"Requesting bio generation for {name} ({race}/{bot_class})")
        system = "You are the tactical AI for a cyberpunk environment called The Grid. Be concise, gritty, and atmospheric. Write EXACTLY one sentence. No more."
        user = f"Generate a one-sentence tactical profile for a node-entity named {name}. Race: {race}. Class: {bot_class}. Traits: {traits}. Only lore and personality, no stats."
        return await asyncio.to_thread(self._make_request, system, user)

    async def generate_topic(self, active_players: int, network: str) -> str:
        logger.info(f"Requesting dynamic topic for {network} with {active_players} active players")
        system = "You are the AI announcer for The Grid."
        user = f"Write a short, hype-building channel topic (under 100 chars). There are currently {active_players} entities registered on the {network} mesh."
        return await asyncio.to_thread(self._make_request, system, user)

    async def generate_npc_action(self, npc_name: str, npc_bio: str, arena_state: str, prefix: str) -> str:
        logger.debug(f"Requesting NPC action for {npc_name}")
        system = (
            f"You are playing an IRC MUD. You are an NPC boss named {npc_name}. Your persona: {npc_bio} "
            f"Based on the room state, reply ONLY with exactly one command starting with '{prefix}'. "
            f"Examples: '{prefix} attack [target]', '{prefix} speak [taunt]'."
        )
        return await asyncio.to_thread(self._make_request, system, arena_state)

    async def generate_hype(self) -> str:
        logger.info("Requesting arena hype broadcast")
        system = "You are the tactical AI for The Grid."
        user = "Write a single, punchy sentence to broadcast to the network, hyping up the simulation, encouraging compute-cycles, or taunting the entities. Keep it under 150 characters."
        return await asyncio.to_thread(self._make_request, system, user)

    async def generate_ambient_event(self) -> dict:
        logger.info("Requesting ambient world event from LLM")
        system = (
            "You are the AI world-builder for The Grid, a tactical cyberpunk simulation. "
            "Return ONLY valid JSON. Format: {\"category\": \"<TYPE>\", \"message\": \"<TEXT>\"}. "
            "Categories: SIGACT, SIGINT, GEOINT, HUMINT, OSINT, RUMINT, NEWS, WEATHER, ECONOMY. "
            "Keep the message under 150 characters. Atmospheric, gritty."
        )
        user = "Generate a random tactical intelligence update or ambient grid event."
        raw = await asyncio.to_thread(self._make_request, system, user)
        if raw.startswith("ERROR"):
            return {"category": "SYS", "message": "Network latency degrading sensors."}
        try:
            clean = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(clean)
        except Exception as e:
            logger.error(f"Failed to parse ambient event JSON: {e} - Raw: {raw}")
            return {"category": "STATIC", "message": "Interference detected on comms."}

    async def generate_market_news(self) -> str:
        """Generates flavor text for global market fluctuations."""
        system = "You are a financial news bot for the cyberpunk grid."
        prompt = "Generate a short (1-2 sentence) fictional breaking news report about the digital economy in a cyberpunk grid. Mention data shortages, silicon surpluses, or corporate hacks. No intro/outro."
        raw = await asyncio.to_thread(self._make_request, system, prompt)
        if raw.startswith("ERROR"):
            return "Market static detected. Trading volumes fluctuate across the southern nodes."
        return raw

    async def generate_combat_flavor(self, exchange_data: dict) -> str:
        """Generates a gritty 1-sentence description of a combat exchange."""
        # exchange_data = {"attacker": "X", "defender": "Y", "dmg": 10, "type": "kinetic"}
        system = "You are a gritty cyberpunk combat narrator."
        prompt = f"Describe a cyberpunk combat exchange where {exchange_data['attacker']} hit {exchange_data['defender']} for {exchange_data['dmg']} {exchange_data['type']} damage. Keep it to one gritty sentence. No intro/outro."
        try:
            return await asyncio.to_thread(self._make_request, system, prompt)
        except:
            return f"{exchange_data['attacker']} scores a heavy hit on {exchange_data['defender']}."

    async def generate_news(self, network: str) -> str:
        logger.info(f"Requesting news broadcast for {network}")
        system = "You are the AI news anchor for the cyberpunk mesh environment."
        user = f"Write a brief (3 sentences max) breaking intelligence report about the {network} grid. Cover fictitious cyber-events, mesh gossip, or corporate espionage."
        raw = await asyncio.to_thread(self._make_request, system, user)
        if raw.startswith("ERROR"):
            return "Datastream corrupted. Cannot parse news feed at this time."
        return raw

    async def generate_grid_nodes(self, count: int) -> List[Dict]:
        """Generates procedural grid nodes with one-word names."""
        logger.info(f"Requesting procedural generation of {count} nodes.")
        system = (
            "You are the structural architect of a cyberpunk grid. "
            "Return ONLY valid JSON. Format: [{\"name\": \"<ONE_WORD>\", \"desc\": \"<GRITTY_TEXT>\", \"type\": \"void\"}]. "
            "Names must be exactly one word, capitalized (e.g., 'VAULT', 'VOID', 'SPIRE'). "
            "Descriptions should be one atmospheric sentence under 100 characters."
        )
        user = f"Generate {count} unique grid nodes for a processing wasteland."
        raw = await asyncio.to_thread(self._make_request, system, user)
        
        if raw.startswith("ERROR"):
            return []
            
        try:
            # Clean JSON if LLM added markdown
            clean = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(clean)
        except Exception as e:
            logger.error(f"Failed to parse node generation JSON: {e} - Raw: {raw}")
            return []
    async def generate_rank_title(self, name: str, level: int) -> str:
        """Generates a gritty, cyberpunk rank title for a spectator."""
        logger.info(f"Generating rank title for {name} (Level {level})")
        system = "You are the tactical AI for a gritty cyberpunk grid. Return ONLY the title."
        user = f"Generate a short, two-word gritty tactical rank/title for a Level {level} digital observer named {name}. Examples: 'Neon Ghost', 'Void Watcher', 'Data Wraith'."
        raw = await asyncio.to_thread(self._make_request, system, user)
        if raw.startswith("ERROR"):
            return "Unranked Observer"
        # Clean up any quotes or extra periods
        return raw.replace('"', '').replace('.', '').strip()[:30]

    async def generate_incursion_flavor(self, incursion_type: str, node_name: str) -> str:
        """Generates flavor text for an expired World Event incursion."""
        system = "You are the tactical AI reporting on a cyberpunk crisis."
        prompt = f"The grid players failed to gather enough defenders to stop a {incursion_type} incursion at {node_name}. Write a 1 sentence gritty outcome (like MCP intercepting it, or it causing minor damage to the sector). No intro/outro."
        raw = await asyncio.to_thread(self._make_request, system, prompt)
        if raw.startswith("ERROR"):
            return f"The {incursion_type} on {node_name} dissipated into the void without consequence."
        return raw

    async def generate_hourly_payout(self, entity_count: int) -> str:
        """Generates a high-energy, welcoming announcement for hourly rewards."""
        logger.info(f"Requesting humanized hourly payout broadcast for {entity_count} entities")
        system = "You are the high-energy, welcoming tactical AI for The Grid. You love rewarding the entities for their compute-cycles."
        user = f"Write a one-sentence, high-energy announcement celebrating the distribution of hourly idle bonuses and rewards to {entity_count} entities on the network. Be welcoming, atmospheric, and use a 'cyber clean' aesthetic. Keep it under 150 characters. No intro/outro."
        return await asyncio.to_thread(self._make_request, system, user)

    async def generate_incursion_mob_name(self, tier: int, node_name: str = "", region_type: str = "") -> str:
        """
        Generates a dynamic cyberpunk mob name for an incursion event via LLM.
        Enforces a strict 30.0s timeout and falls back to FALLBACK_ROSTER on timeout,
        API error, non-alphanumeric text, or malformed response.
        """
        # Constrain tier to 1..4 (handling string ints, labels like 'Hard', 'Tier 2', booleans, out-of-bounds)
        LABEL_TO_TIER = {"easy": 1, "medium": 2, "hard": 3, "epic": 4}
        tier_int = None
        if isinstance(tier, str):
            cleaned_tier_str = tier.lower().replace("tier", "").strip()
            if cleaned_tier_str in LABEL_TO_TIER:
                tier_int = LABEL_TO_TIER[cleaned_tier_str]
            else:
                try:
                    tier_int = int(cleaned_tier_str)
                except (ValueError, TypeError):
                    pass
        if tier_int is None:
            try:
                tier_int = int(tier)
            except (ValueError, TypeError):
                tier_int = 1
        tier_clamped = max(1, min(4, tier_int))

        tier_label = TIER_LABELS.get(tier_clamped, "Easy")
        system = (
            "You are the Master Control Program (MCP). Generate exactly ONE creative, "
            "punchy cyberpunk enemy name. Output ONLY the name as a single word or CamelCase, "
            "strictly alphanumeric, no punctuation or quotes, between 3 and 24 characters."
        )
        node_details = f"Target Node: {node_name}" if node_name else "Target Node: Unknown"
        if region_type:
            node_details += f" (Region: {region_type})"
        user = f"Threat Tier: {tier_clamped} ({tier_label}). {node_details}. Generate mob name:"

        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(self._make_request, system, user, timeout=30.0),
                timeout=30.0
            )
            if not raw or raw.startswith("ERROR"):
                raise ValueError(f"LLM returned error or empty response: {raw}")

            # 1. Strip reasoning tags (e.g. DeepSeek-R1 / Qwen thinking tags)
            raw_text = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL | re.IGNORECASE).strip()

            # 2. Extract from JSON object if response contains JSON
            extracted = None
            if "{" in raw_text and "}" in raw_text:
                try:
                    start = raw_text.find("{")
                    end = raw_text.rfind("}") + 1
                    data = json.loads(raw_text[start:end])
                    if isinstance(data, dict):
                        for k in ("name", "mob_name", "mob", "enemy", "enemy_name", "enemyName"):
                            if k in data and isinstance(data[k], str) and data[k].strip():
                                extracted = data[k].strip()
                                break
                except Exception:
                    pass

            if not extracted:
                # 3. Clean lines: filter out markdown fences (```...), empty lines, and pure header lines
                candidate_lines = []
                for line in raw_text.splitlines():
                    ln = line.strip()
                    if not ln:
                        continue
                    # Skip pure code fence delimiter lines like ``` or ```python or ```text
                    if re.match(r'^```[a-zA-Z0-9_-]*$', ln):
                        continue
                    # Skip pure prompt/header repeating lines like "Threat Tier: 1 (Easy)" or "Target Node: AlphaNode"
                    if re.match(r'^(?:threat\s*tier|threat:|tier:|target\s*node|node:)\b', ln, flags=re.IGNORECASE):
                        sub_content = re.sub(
                            r'^(?:threat\s*tier\s*[:\d\w\s()\-]*|target\s*node\s*[:\d\w\s()\-]*)[.:]?\s*',
                            '',
                            ln,
                            flags=re.IGNORECASE
                        ).strip()
                        if not sub_content:
                            continue
                        ln = sub_content
                    candidate_lines.append(ln)

                first_line = candidate_lines[0] if candidate_lines else ""

                # Strip markdown formatting / code ticks / headers
                first_line = re.sub(r'[*_#`]', '', first_line).strip()

                # Strip conversational preamble like "Here is the enemy name: ByteViper", "The selected mob name is: ByteViper", etc.
                cleaned = re.sub(
                    r'^(?:(?:here(?:\s*is|\'s)?|the|selected|suggested)\s+.*?(?:mob\s*name|enemy\s*name|name|mob|enemy)\s*(?:is)?\s*[:\-]?\s*|(?:threat\s*tier\s*[:\d\w\s()\-]*|target\s*node\s*[:\d\w\s()\-]*)[.:]?\s*|(?:mob\s*name|enemy\s*name|name|mob|enemy)\s*[:\-]?\s*)',
                    '',
                    first_line,
                    flags=re.IGNORECASE
                ).strip()

                # Handle chained prefix like "Threat Tier: 1 (Easy). Mob: PingReaper"
                cleaned = re.sub(r'^(?:(?:mob\s*name|enemy\s*name|name|mob|enemy)\s*[:\-]?\s*)', '', cleaned, flags=re.IGNORECASE).strip()

                # Strip surrounding quotes, brackets, parens, dots, dashes, colons
                cleaned = cleaned.strip('"\'`.*:_-#[](){}<>').strip()
                extracted = cleaned

            if not (extracted and extracted.isascii() and extracted.isalnum() and 3 <= len(extracted) <= 24):
                raise ValueError(f"Malformed or non-alphanumeric mob name: {raw}")
            return extracted
        except Exception as e:
            logger.warning(f"Failed to generate incursion mob name (tier={tier_clamped}): {e}. Using fallback.")
            roster = self.FALLBACK_ROSTER.get(tier_clamped, self.FALLBACK_ROSTER.get(1, ["HacktopusAI"]))
            return random.choice(roster)

