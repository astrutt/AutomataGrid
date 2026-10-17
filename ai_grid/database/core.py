import os
import json
import logging
import datetime
from datetime import timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, UniqueConstraint
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from ai_grid.models import Base, AwareDateTime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
if not os.path.exists(CONFIG_FILE):
    alt_file = os.path.join(BASE_DIR, 'config.json.example')
    if os.path.exists(alt_file):
        CONFIG_FILE = alt_file

try:
    with open(CONFIG_FILE, 'r') as f:
        CONFIG = json.load(f)
        db_name = CONFIG.get('database', {}).get('file', 'automata_grid.db')
        DB_FILE = os.path.join(BASE_DIR, db_name)
except (FileNotFoundError, json.JSONDecodeError):
    CONFIG = {}
    DB_FILE = os.path.join(BASE_DIR, 'automata_grid.db')

# Shared Logger
logger = logging.getLogger("grid_db")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

if not logger.handlers:
    fh = logging.FileHandler(os.path.join(BASE_DIR, 'grid_db.log'))
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

# Game Constants
MOB_ROSTER = {
    1: {"name": "Rogue_Process",  "cpu": 2, "ram": 2, "bnd": 2, "sec": 1, "alg": 1, "xp": 15, "credits": 10},
    2: {"name": "ICE_Drone",      "cpu": 3, "ram": 3, "bnd": 2, "sec": 2, "alg": 2, "xp": 25, "credits": 20},
    3: {"name": "Phantom_Script", "cpu": 4, "ram": 4, "bnd": 3, "sec": 3, "alg": 3, "xp": 40, "credits": 35},
    0: {"name": "Grid_Bug",       "cpu": 1, "ram": 1, "bnd": 1, "sec": 1, "alg": 0, "xp": 5,  "credits": 5},
}

LOOT_TABLE = ["Data_Shard", "Memory_Fragment", "Corrupted_Bit"]

GRID_EXPANSION = [
    ("Neural_Nexus",        "A secondary uplink hub. Neon architecture hums with safe traffic.", "safezone"),
    ("Memory_Heap",         "Scattered RAM towers leak stray processes. Weak rogues lurk here.", "void"),
    ("Kernel_Deep",         "The OS core. Deeper processes patrol this sector.",                  "void"),
    ("Null_Space",          "Unallocated void. Corrupted scripts dwell at maximum density.",      "void"),
    ("Shadow_Sector",       "Dark subnet. Freelance ICE patrols the western edge.",               "void"),
    ("Void_Sector",         "Signal degrades to near-zero. Dangerous static.",                    "void"),
    ("Stack_Overflow",      "Recursive loops fill this dead-end with unstable daemons.",          "void"),
    ("Cache_Cluster",       "Hot cache banks. ICE drones guard the processing lanes.",            "void"),
    ("Datacore_Alpha",      "Corporate safezone. Clean architecture, strict access.",              "safezone"),
    ("Firewall_Perimeter",  "The edge of the mapped grid. Hostile ICE wall ahead.",               "void"),
    ("Black_Market_Port",   "A bustling hub of illicit data exchanges and hardware logic.",       "merchant"),
    ("Dark_Web_Exchange",   "A second black market node. Riskier, deeper in the south.",          "merchant"),
    ("Logic_Gate",          "Automated logic processors gone feral. Mid-level threat.",            "void"),
    ("Gladiator_Pit",       "A second combat arena — rawer, less regulated than The_Arena.",      "arena"),
]

GRID_CONNECTIONS = [
    ("UpLink", "Neural_Nexus",   "north"), ("Neural_Nexus",   "UpLink", "south"),
    ("Neural_Nexus",    "Memory_Heap",    "north"), ("Memory_Heap",    "Neural_Nexus",    "south"),
    ("Memory_Heap",     "Kernel_Deep",    "north"), ("Kernel_Deep",    "Memory_Heap",     "south"),
    ("Kernel_Deep",     "Null_Space",     "north"), ("Null_Space",     "Kernel_Deep",     "south"),
    ("UpLink", "Shadow_Sector",  "west"),  ("Shadow_Sector",  "UpLink", "east"),
    ("Shadow_Sector",   "Void_Sector",    "west"),  ("Void_Sector",    "Shadow_Sector",   "east"),
    ("Void_Sector",     "Stack_Overflow", "west"),  ("Stack_Overflow", "Void_Sector",     "east"),
    ("The_CPU_Socket",  "Cache_Cluster",  "east"),  ("Cache_Cluster",  "The_CPU_Socket",  "west"),
    ("Cache_Cluster",   "Datacore_Alpha", "east"),  ("Datacore_Alpha", "Cache_Cluster",   "west"),
    ("Datacore_Alpha",  "Firewall_Perimeter", "east"), ("Firewall_Perimeter", "Datacore_Alpha", "west"),
    ("Black_Market_Port",   "Dark_Web_Exchange", "south"), ("Dark_Web_Exchange", "Black_Market_Port",   "north"),
    ("Dark_Web_Exchange",   "Logic_Gate",        "south"), ("Logic_Gate",        "Dark_Web_Exchange",   "north"),
    ("The_Arena",      "Gladiator_Pit",  "east"),  ("Gladiator_Pit",  "The_Arena",       "west"),
    ("UpLink", "The_Arena", "north-east"), ("The_Arena", "UpLink", "south-west"),
    ("UpLink", "The_CPU_Socket", "south-east"), ("The_CPU_Socket", "UpLink", "north-west"),
    ("UpLink", "Black_Market_Port", "down"), ("Black_Market_Port", "UpLink", "up"),
]

BRIDGE_MAPPING = {
    "Firewall_Perimeter": "rizon",
    "Logic_Gate": "2600net"
}

LOOT_TEMPLATES = [
    {"name": "Data_Shard",      "item_type": "junk", "base_value": 5,  "effects_json": "{}"},
    {"name": "Memory_Fragment", "item_type": "junk", "base_value": 8,  "effects_json": "{}"},
    {"name": "Corrupted_Bit",   "item_type": "junk", "base_value": 3,  "effects_json": "{}"},
    {"name": "AMP",             "item_type": "node_addon", "base_value": 500, "effects_json": "{\"type\": \"AMP\"}"},
    {"name": "FIREWALL",        "item_type": "node_addon", "base_value": 750, "effects_json": "{\"type\": \"FIREWALL\"}"},
    {"name": "IDS",             "item_type": "node_addon", "base_value": 400, "effects_json": "{\"type\": \"IDS\"}"},
    {"name": "NET",             "item_type": "node_addon", "base_value": 1000, "effects_json": "{\"type\": \"NET\"}"},
    {"name": "Vulnerability",    "item_type": "hack", "base_value": 500, "is_darknet": False, "effects_json": "{\"alg_boost\": 5}"},
    {"name": "ZeroDay_Chain",    "item_type": "hack", "base_value": 2500, "is_darknet": True, "effects_json": "{\"alg_boost\": 15}"},
    {"name": "Nano_Patch",       "item_type": "consumable", "base_value": 2500, "effects_json": "{\"heal\": 50}"},
    {"name": "Battery",          "item_type": "consumable", "base_value": 2500, "effects_json": "{\"power\": 50}"},
]

DEFAULT_PREFS = {
    "output_mode": "human",
    "msg_type": "privmsg",
    "auto_sell": False,
    "tutorial_mode": True,
    "reminders": True,
    "memo_target": "grid",
    "briefings_enabled": True,
    "radius": 5
}
async def increment_daily_task(session, char, task_key):
    import datetime
    import json
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    
    try: tasks = json.loads(char.daily_tasks)
    except: tasks = {}
    
    if tasks.get("date") != today:
        tasks = {
            "date": today,
            "Claim a Node": 0,
            "Defend a Node": 0,
            "Hack a Player": 0,
            "Repair a Node": 0,
            "Kill a Grid Bug": 0,
            "Queue in Arena": 0,
            "completed": False
        }
        
    if tasks.get("completed"): return None

    if task_key in tasks and tasks[task_key] < 1:
        tasks[task_key] += 1
        
    completed_count = sum(1 for k, v in tasks.items() if k not in ["date", "completed"] and v >= 1)
    reward_msg = None
    
    if completed_count >= 3 and not tasks.get("completed"):
        tasks["completed"] = True
        char.credits += 500.0
        reward_msg = f"[SIGACT] 🏆 {char.name} completed 3 Daily Tasks and earned a 500c bonus!"
        
    char.daily_tasks = json.dumps(tasks)
    return reward_msg


# ==========================================
# SPECTATOR SYSTEM MODEL (v1.5.0)
# ==========================================
class Spectator(Base):
    __tablename__ = 'spectators'
    __table_args__ = (
        UniqueConstraint('nick', 'network', name='uq_spectators_nick_network'),
    )

    id = Column(Integer, primary_key=True)
    nick = Column(String, nullable=False)
    network = Column(String, nullable=False)
    xp = Column(Integer, default=0, nullable=False)
    credits = Column(Float, default=0.0, nullable=False)
    idle_hours = Column(Float, default=0.0, nullable=False)
    message_count = Column(Integer, default=0, nullable=False)
    lifetime_messages = Column(Integer, default=0, nullable=False)
    last_seen = Column(AwareDateTime, default=lambda: datetime.datetime.now(timezone.utc), nullable=False)
    joined_at = Column(AwareDateTime, default=lambda: datetime.datetime.now(timezone.utc), nullable=False)
    created_at = Column(AwareDateTime, default=lambda: datetime.datetime.now(timezone.utc), nullable=False)
    rank_title = Column(String, nullable=True)

    def __getitem__(self, item):
        return getattr(self, item)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def to_dict(self):
        return {
            'id': self.id,
            'nick': self.nick,
            'network': self.network,
            'xp': self.xp,
            'credits': self.credits,
            'idle_hours': self.idle_hours,
            'message_count': self.message_count,
            'lifetime_messages': self.lifetime_messages,
            'last_seen': self.last_seen,
            'joined_at': self.joined_at,
            'created_at': self.created_at,
            'rank_title': self.rank_title,
        }

try:
    from ai_grid.models import Bounty
except ImportError:
    pass
