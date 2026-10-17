# arena_utils.py - v1.1.0
# Core string formatting, IRC aesthetics, and Structured Logging

import json
import logging
import sys

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

logger = logging.getLogger("arena_utils")
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# File Handler
_log_path = Path(__file__).resolve().parent / 'grid_utils.log'
fh = logging.FileHandler(_log_path)
fh.setFormatter(formatter)
logger.addHandler(fh)

# Console Handler (Optional: can comment out if you don't want utility logs spamming console)
ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)

# --- IRC Color Constants ---
# Standard IRC color codes (prepended with \x03 in the format function)
C_WHITE   = "00"
C_BLACK   = "01"
C_BLUE    = "02"
C_GREEN   = "03"
C_RED     = "04"
C_BROWN   = "05"
C_PURPLE  = "06"
C_ORANGE  = "07"
C_YELLOW  = "08"
C_L_GREEN = "09"
C_CYAN    = "11"
C_L_BLUE  = "12"
C_PINK    = "13"
C_GREY    = "14"

# --- Emoji & Icon Dictionary ---
ICONS = {
    'ARENA': '🏟️',
    'SIGACT': '⚡',
    'SIGINT': '📡',
    'GEOINT': '🛰️',
    'HUMINT': '🕵️',
    'AI-INT': '🤖',
    'RUMINT': '👁️',
    'OSINT': '📜',
    'COMBAT': '⚔️',
    'WEATHER': '🌦️',
    'ECONOMY': '💹',
    'MAINT': '🛠️',
    'CROSS-GRID': '🌐',
    'INFO': 'ℹ️',
    'MOVEMENT': '🚀',
    'TRAVEL': '🚶',
    'Default': '⚙️',
    'Item': '📦',
    'Heal': '💉',
    # Attribute Icons
    'CPU': '💻',
    'RAM': '🧠',
    'BND': '📶',
    'SEC': '🛡️',
    'ALG': '⚙️',
    # Economy & Territory
    'POWER': '🔋',
    'CREDITS': '💳',
    'TERRITORY': '🏰',
    'BOUNTY': '🎯'
}

def calculate_elo_change(winner_elo: int, loser_elo: int, k_factor: int = 32) -> int:
    """Calculates the Elo rating change for a match."""
    expected_winner = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    # Return rounded delta for the winner
    return round(k_factor * (1 - expected_winner))

def format_text(text: str, color_code: str = None, bold: bool = False, is_machine: bool = False) -> str:
    """
    Applies standard IRC color and bold control codes to a string.
    If is_machine is True, returns plain text stripped of all non-ASCII characters.
    """
    if is_machine:
        # Aggressively strip non-ASCII (emojis, unicode symbols) for machine-mode parsability
        return "".join(char for char in str(text) if ord(char) < 128)
    try:
        result = str(text)
        if bold:
            result = f"\x02{result}\x02"
        if color_code:
            result = f"\x03{color_code}{result}\x03"
        return result
    except Exception as e:
        logger.error(f"Failed to format text '{text}': {e}")
        return "".join(char for char in str(text) if ord(char) < 128)

def tag_msg(text: str, tags: list = None, location: str = None, is_machine: bool = False, nick: str = None, action: str = None, result: str = None, source: str = None, destination: str = None) -> str:
    """
    Builds a structured [GRID] message with tactical intel tags and icons.
    Protocol: [GRID]<ico>[ACTION][RESULT][NICK] TEXT
    Machine Mode: [GRID][ACTION:VAL][RESULT:VAL][NICK:VAL] TEXT (No icons, No IRC codes)
    """
    if tags is None: tags = []
    
    # Identify primary icon source
    icon_source = action.upper() if action else (tags[0].upper() if tags else "DEFAULT")
    
    # Human-only components
    prefix = "[GRID]"
    icon = ""
    
    if is_machine:
        # Strictly structured KV pairs for AI parsability
        brackets = ""
        if action: brackets += f"[ACTION:{action.upper()}]"
        elif tags:  brackets += f"[ACTION:{tags[0].upper()}]"
        
        if result:   brackets += f"[RESULT:{result.upper()}]"
        if nick:     brackets += f"[NICK:{nick}]"
        elif location: brackets += f"[LOC:{location.upper()}]"
        
        if source:      brackets += f"[SRC:{source.upper()}]"
        if destination: brackets += f"[DST:{destination.upper()}]"
        
        clean_text = "".join(char for char in str(text) if ord(char) < 128)
        return f"{prefix}{brackets} {clean_text}"
    else:
        # Standard Action/Result Color Mapping (Human Mode)
        ACTION_COLORS = {
            'GEOINT': C_CYAN, 'MAPPED': C_CYAN, 'OSINT': C_YELLOW, 'FOUND': C_YELLOW,
            'RECON': C_PURPLE, 'SIGACT': C_GREEN, 'EXFIL': C_L_GREEN, 'EXPLOIT': C_ORANGE,
            'SIGINT': C_BLUE, 'PROBE': C_BLUE, 'PREBREACH': C_BLUE,
            'MAINT': C_GREY, 'ERR': C_RED, 'FAIL': C_RED, 'ALARM': C_RED, 'INFO': C_WHITE,
            'MOVEMENT': C_CYAN, 'TRAVEL': C_CYAN, 'BOUNTY': C_YELLOW
        }
        
        icon = ICONS.get(icon_source, ICONS.get('Default', '⚙️'))
        c_grid = C_YELLOW
        
        p_grid = format_text("[GRID]", c_grid, is_machine=False)
        p_icon = icon
        
        brackets = ""
        # 1. Action Bracket
        if action:
            color = ACTION_COLORS.get(action.upper(), C_CYAN)
            brackets += format_text(f"[{action.upper()}]", color, is_machine=False)
        elif tags:
            color = ACTION_COLORS.get(tags[0].upper(), C_CYAN)
            brackets += format_text(f"[{tags[0].upper()}]", color, is_machine=False)

        # 2. Result/Status Bracket
        if result:
            color = ACTION_COLORS.get(result.upper(), C_GREEN if result.upper() in ['SUCCESS', 'OPEN', 'OK'] else C_CYAN)
            brackets += format_text(f"[{result.upper()}]", color, is_machine=False)
        
        # 3. Nick/Target Bracket
        if nick:
            brackets += format_text(f"[{nick}]", C_WHITE, is_machine=False)
        elif location:
            brackets += format_text(f"[{location}]", C_GREY, is_machine=False)

        if source:
            brackets += format_text(f"[{source}]", C_GREY, is_machine=False)
        if destination:
            brackets += format_text(f"[{destination}]", C_CYAN, is_machine=False)

        return f"{p_grid}{p_icon}{brackets} {text}"

# --- Topic Aesthetics (Task 018) ---
TOPIC_START = "『 "
TOPIC_END   = " 』"
TOPIC_SEP   = "  ░▒▓  "

def generate_gradient(text: str, colors: list) -> str:
    """Spreads a list of IRC colors across a string character-by-character."""
    if not text or not colors:
        return str(text)
    
    result = ""
    clean_text = str(text)
    for i, char in enumerate(clean_text):
        if char == " ":
            result += " "
            continue
        # Distribute colors across the length
        color_idx = int((i / len(clean_text)) * len(colors))
        color_idx = min(color_idx, len(colors) - 1)
        result += f"\x03{colors[color_idx]}{char}\x03"
    return result

def generate_meter(val, max_val, length=10) -> str:
    """Generates a high-aesthetic [▓▓▓░░░] style telemetry meter."""
    try:
        val = float(val); max_val = float(max_val)
        if max_val <= 0: return "░" * length
        percent = min(1.0, max(0.0, val / max_val))
        filled = int(percent * length)
        empty = length - filled
        return "▓" * filled + "░" * empty
    except Exception as e:
        logger.error(f"Meter generation error: {e}")
        return "░" * length

def build_banner(text: str, is_machine: bool = False) -> str:
    """
    DEPRECATED: Legacy wrapper for [GRID] banner logic. 
    New code should use tag_msg() directly.
    """
    return tag_msg(text, tags=['ARENA'], is_machine=is_machine)

def format_item(item_name: str) -> str:
    """
    Cleans up inventory names and dynamically assigns a relevant icon.
    """
    try:
        icon = ICONS.get('Item', '📦')
        name_clean = item_name.replace('_', ' ')
        
        # Dynamic icon assignment based on item name keywords
        if any(w in name_clean for w in ["Blade", "Sword", "Dagger", "Knife"]):
            icon = '🔪'
        elif any(w in name_clean for w in ["Gun", "Blaster", "Rifle", "Pistol"]):
            icon = '🔫'
        elif any(w in name_clean for w in ["Ration", "Patch", "Medkit", "Heal"]):
            icon = '💉'
        elif any(w in name_clean for w in ["Shield", "Armor", "Vest"]):
            icon = '🛡️'
            
        fmt_item = f"{icon} {name_clean}"
        logger.debug(f"Formatted item: {item_name} -> {fmt_item}")
        return format_text(fmt_item, C_CYAN)
    except Exception as e:
        logger.error(f"Failed to format item '{item_name}': {e}")
        return str(item_name)

logger.debug("arena_utils module loaded and ready.")
