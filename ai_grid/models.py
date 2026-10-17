from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Float, JSON, UniqueConstraint, Index, text
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.types import TypeDecorator
from datetime import datetime, timezone

class AwareDateTime(TypeDecorator):
    """
    Ensures that datetimes are always timezone-aware (UTC) when entering/exiting the DB.
    SQLite stores these as strings, so we re-attach the UTC info on the way out.
    """
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

Base = declarative_base()

# ==========================================
# 1. GRID NODES & TOPOLOGY
# ==========================================
class GridNode(Base):
    __tablename__ = 'grid_nodes'
    
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(String)
    node_type = Column(String, default="safezone")  # void, arena, merchant, safezone
    owner_character_id = Column(Integer, ForeignKey('characters.id', use_alter=True, name="fk_grid_owner"), nullable=True)
    upgrade_level = Column(Integer, default=1)
    
    power_stored = Column(Float, default=0.0)
    power_consumed = Column(Float, default=0.0)
    power_generated = Column(Float, default=0.0)
    durability = Column(Float, default=100.0)
    is_spawn_node = Column(Boolean, default=False, index=True)
    noise = Column(Float, default=0.0) # SIGINT failure tracking (Heat)
    
    # Discovery & Network Mapping
    is_hidden = Column(Boolean, default=False)
    availability_mode = Column(String, default='OPEN') # OPEN, CLOSED
    is_darknet = Column(Boolean, default=False)
    net_affinity = Column(String, nullable=True) # Mapping to Network (e.g. Rizon)
    local_network = Column(String, nullable=True) # Named subnet for power pooling
    stash_inventory = Column(JSON, default=lambda: [])  # Simple item-name list stored at the node
    
    # Hardware & Infrastructure
    addons_json = Column(String, default="{}") # JSON storage for AMP, FIREWALL, IDS, NET
    firewall_hits = Column(Integer, default=0)
    ids_alerts = Column(Integer, default=0)
    max_slots = Column(Integer, default=4)
    active_target_id = Column(Integer, ForeignKey('raid_targets.id', use_alter=True, name="fk_active_target"), nullable=True)
    
    # Grid v2.0 Coordinates & Expansion
    x = Column(Integer, index=True)
    y = Column(Integer, index=True)
    is_unlocked = Column(Boolean, default=False)
    cluster_id = Column(Integer, nullable=True)
    region_type = Column(String, default="VOD", index=True) # CIV, SMB, CRP, EDU, GOV, MED, MIL, ORG, LEA, DTC, POS, ICS, UTL, ARN, WAR, VOD
    controller = Column(String, nullable=True) # MCP, NPC, RAID, CLAIMABLE, or None
    
    # Relationships
    owner = relationship("Character", foreign_keys=[owner_character_id], post_update=True)
    active_target = relationship("RaidTarget", foreign_keys=[active_target_id], post_update=True)
    characters_present = relationship("Character", foreign_keys="[Character.node_id]", back_populates="current_node")
    # Connections as source
    exits = relationship("NodeConnection", foreign_keys="[NodeConnection.source_node_id]", back_populates="source_node")

class NodeConnection(Base):
    __tablename__ = 'node_connections'
    
    id = Column(Integer, primary_key=True)
    source_node_id = Column(Integer, ForeignKey('grid_nodes.id'))
    target_node_id = Column(Integer, ForeignKey('grid_nodes.id'))
    direction = Column(String) # e.g., 'north', 'port_80', 'ssh_tunnel'
    is_hidden = Column(Boolean, default=False)
    
    source_node = relationship("GridNode", foreign_keys=[source_node_id], back_populates="exits")
    target_node = relationship("GridNode", foreign_keys=[target_node_id])

# ==========================================
# 2. PLAYER PROGRESSION & IDENTITY
# ==========================================
class Player(Base):
    __tablename__ = 'players'
    
    id = Column(Integer, primary_key=True)
    global_name = Column(String, unique=True)
    is_autonomous = Column(Boolean, default=False) # True if driven by LLM
    created_at = Column(AwareDateTime, default=lambda: datetime.now(timezone.utc))
    
    aliases = relationship("NetworkAlias", back_populates="player", cascade="all, delete-orphan")
    characters = relationship("Character", back_populates="player", cascade="all, delete-orphan")

class NetworkAlias(Base):
    __tablename__ = 'network_aliases'
    
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey('players.id'))
    network_name = Column(String, nullable=False) # e.g., '2600net', 'rizon'
    nickname = Column(String, nullable=False)
    
    player = relationship("Player", back_populates="aliases")

class Character(Base):
    __tablename__ = 'characters'
    
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey('players.id'))
    node_id = Column(Integer, ForeignKey('grid_nodes.id'), nullable=True)
    
    name = Column(String, nullable=False)
    race = Column(String, nullable=False)
    char_class = Column(String, nullable=False)
    bio = Column(String, nullable=True)
    
    # Progression
    level = Column(Integer, default=1)
    xp = Column(Integer, default=0)
    credits = Column(Float, default=0.0)
    current_hp = Column(Integer, default=25)
    elo = Column(Integer, default=1200) # Arena rating
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    status = Column(String, default='ACTIVE')
    auth_token = Column(String, nullable=True)
    daily_tasks = Column(String, default='{}')
    prefs = Column(String, default='{"output_mode":"human","msg_type":"privmsg","auto_sell":false,"tutorial_mode":true,"reminders":true,"memo_target":"grid","briefings_enabled":true}')
    
    # Core Attributes
    cpu = Column(Integer, default=5) # Physical/Kinetic
    ram = Column(Integer, default=5) # Health/Tankiness
    bnd = Column(Integer, default=5) # Speed/Evasion (Bandwidth)
    sec = Column(Integer, default=5) # Defense/Resist
    alg = Column(Integer, default=5) # Magic/Hacking capability
    
    # Resource Stats
    power = Column(Float, default=100.0)
    stability = Column(Float, default=100.0)
    alignment = Column(Integer, default=0) # Ethics: -100 to 100
    data_units = Column(Float, default=0.0) # Raw fodder for The Gibson
    alg_bonus = Column(Integer, default=0) # Temporary boost for next hack
    ice_lockdown_until = Column(AwareDateTime, nullable=True) # CipherLock penalty
    
    # Activity & Retention (IdleRPG)
    total_chat_messages = Column(Integer, default=0)
    total_idle_seconds = Column(Float, default=0.0)
    pending_stat_points = Column(Integer, default=0)
    last_surrender = Column(AwareDateTime, nullable=True) # v1.8.0: PvP Ban tracking
    last_seen_at = Column(AwareDateTime, default=lambda: datetime.now(timezone.utc))

    # Priority 2: Reputation & MCP Heat
    mcp_heat = Column(Float, default=0.0)
    node_rep = Column(JSON, default=lambda: {})
    
    # v1.8.0: Spectator Specifics
    rank_title = Column(String, nullable=True)
    last_daily_bonus_at = Column(AwareDateTime, nullable=True)
    
    created_at = Column(AwareDateTime, default=lambda: datetime.now(timezone.utc))
    
    # Task 064: Nodal Compromise Window
    last_breach_node_id = Column(Integer, ForeignKey('grid_nodes.id'), nullable=True)
    last_breach_target_id = Column(Integer, ForeignKey('raid_targets.id'), nullable=True)
    last_skill_train_at = Column(AwareDateTime, nullable=True) # 1-hour skill training cooldown
    
    # Relationships
    player = relationship("Player", back_populates="characters")
    current_node = relationship("GridNode", foreign_keys=[node_id], back_populates="characters_present")
    inventory = relationship("InventoryItem", back_populates="owner", cascade="all, delete-orphan")
    skills = relationship("CharacterSkill", back_populates="character", cascade="all, delete-orphan")

# ==========================================
# 2B. CHARACTER SKILL PROGRESSION
# ==========================================
class CharacterSkill(Base):
    __tablename__ = 'character_skills'
    __table_args__ = (
        UniqueConstraint('character_id', 'skill_name', name='uq_character_skill'),
    )

    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey('characters.id'), nullable=False, index=True)
    skill_name = Column(String, nullable=False) # powergen, attack, defend, hack, recon, siphon, stealth, fortify
    level = Column(Integer, default=1, nullable=False) # 1 to 4
    training_sessions = Column(Integer, default=0, nullable=False) # 0 to 24
    is_active = Column(Boolean, default=False, nullable=False) # Actively being trained
    last_trained_at = Column(AwareDateTime, nullable=True)
    created_at = Column(AwareDateTime, default=lambda: datetime.now(timezone.utc))

    character = relationship("Character", back_populates="skills")


# ==========================================
# 3. DISCOVERY, MAPPING & EVENTS
# ==========================================
class PulseEvent(Base):
    __tablename__ = 'pulse_events'
    
    id = Column(Integer, primary_key=True)
    node_id = Column(Integer, ForeignKey('grid_nodes.id'), index=True)
    network_name = Column(String, index=True) # Scope limiting
    event_type = Column(String) # 'PACKET', 'GLITCH'
    reward_val = Column(Float, default=0.0) # Credits or Data units
    created_at = Column(AwareDateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(AwareDateTime)
    status = Column(String, default='ACTIVE') # ACTIVE, RESOLVED, EXPIRED
    
    node = relationship("GridNode")

class IncursionEvent(Base):
    __tablename__ = 'incursion_events'
    
    id = Column(Integer, primary_key=True)
    node_id = Column(Integer, ForeignKey('grid_nodes.id'), index=True)
    network_name = Column(String, index=True)
    incursion_type = Column(String, nullable=False) # e.g., 'HacktopusAI'
    tier = Column(Integer, default=1) # Required players
    reward_val = Column(Float, default=500.0)
    created_at = Column(AwareDateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(AwareDateTime)
    status = Column(String, default='ACTIVE') # ACTIVE, RESOLVED, EXPIRED
    
    node = relationship("GridNode")
    defenders = relationship("IncursionDefender", back_populates="incursion", cascade="all, delete-orphan")

class IncursionDefender(Base):
    __tablename__ = 'incursion_defenders'
    
    id = Column(Integer, primary_key=True)
    incursion_id = Column(Integer, ForeignKey('incursion_events.id'), index=True)
    character_id = Column(Integer, ForeignKey('characters.id'), index=True)
    timestamp = Column(AwareDateTime, default=lambda: datetime.now(timezone.utc))
    
    incursion = relationship("IncursionEvent", back_populates="defenders")
    character = relationship("Character")

class RaidTarget(Base):
    __tablename__ = 'raid_targets'
    
    id = Column(Integer, primary_key=True)
    node_id = Column(Integer, ForeignKey('grid_nodes.id'), index=True)
    name = Column(String, nullable=False) # e.g. [SMB], [MIL]
    target_type = Column(String, nullable=False) # SMB, EDU, GOV, MIL, CORP, ORG, LEA, DC
    difficulty = Column(Integer, default=10)
    credits_pool = Column(Float, default=0.0)
    data_pool = Column(Float, default=0.0)
    is_active = Column(Boolean, default=True)
    availability_mode = Column(String, default='CLOSED') # Task 064: OPEN, CLOSED
    last_raided_at = Column(AwareDateTime, nullable=True)
    created_at = Column(AwareDateTime, default=lambda: datetime.now(timezone.utc))
    
    node = relationship("GridNode", foreign_keys=[node_id])

class DiscoveryRecord(Base):
    __tablename__ = 'discovery_records'
    
    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey('characters.id'), index=True)
    node_id = Column(Integer, ForeignKey('grid_nodes.id'), index=True)
    intel_level = Column(String) # 'EXPLORE' (Topological), 'PROBE' (Deep)
    intel_expires_at = Column(AwareDateTime, nullable=True) # TTL for PROBE intel
    raid_target_id = Column(Integer, ForeignKey('raid_targets.id'), index=True, nullable=True)
    discovered_at = Column(AwareDateTime, default=lambda: datetime.now(timezone.utc))
    
    character = relationship("Character", foreign_keys=[character_id])
    node = relationship("GridNode", foreign_keys=[node_id])
    raid_target = relationship("RaidTarget", foreign_keys=[raid_target_id])

class BreachRecord(Base):
    __tablename__ = 'breach_records'
    
    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey('characters.id'), index=True)
    node_id = Column(Integer, ForeignKey('grid_nodes.id'), index=True)
    is_silent = Column(Boolean, default=False) # True if via 'exploit'
    raid_target_id = Column(Integer, ForeignKey('raid_targets.id'), index=True, nullable=True)
    breached_at = Column(AwareDateTime, default=lambda: datetime.now(timezone.utc))
    
    character = relationship("Character", foreign_keys=[character_id])
    node = relationship("GridNode", foreign_keys=[node_id])
    raid_target = relationship("RaidTarget", foreign_keys=[raid_target_id])

# ==========================================
# 4. INVENTORY SYSTEM
# ==========================================
class ItemTemplate(Base):
    __tablename__ = 'item_templates'
    
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    item_type = Column(String) # weapon, consumable, armor, hack
    base_value = Column(Integer, default=0)
    is_darknet = Column(Boolean, default=False)
    effects_json = Column(String, default="{}") # e.g., '{"heal": 15}'

    @property
    def effects_json_dict(self):
        import json
        return json.loads(self.effects_json or "{}")

class InventoryItem(Base):
    __tablename__ = 'inventory_items'
    
    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey('characters.id'))
    template_id = Column(Integer, ForeignKey('item_templates.id'))
    quantity = Column(Integer, default=1)
    durability = Column(Float, default=100.0)
    
    owner = relationship("Character", back_populates="inventory")
    template = relationship("ItemTemplate")

# ==========================================
# 4. MAINFRAME MANUFACTURING (THE GIBSON)
# ==========================================
class MainframeTask(Base):
    __tablename__ = 'mainframe_tasks'
    
    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey('characters.id'), nullable=False)
    task_type = Column(String, nullable=False) # COMPILE, ASSEMBLE
    amount = Column(Integer, default=1) # Target yield
    completion_time = Column(AwareDateTime, nullable=False)
    is_collected = Column(Boolean, default=False)
    
    owner = relationship("Character")

# ==========================================
# 5. GLOBAL ECONOMY & MINI-GAMES
# ==========================================
class AuctionListing(Base):
    __tablename__ = 'auction_listings'
    
    id = Column(Integer, primary_key=True)
    seller_id = Column(Integer, ForeignKey('characters.id'), nullable=False)
    item_id = Column(Integer, ForeignKey('inventory_items.id'), nullable=False)
    current_bid = Column(Integer, default=0)
    highest_bidder_id = Column(Integer, ForeignKey('characters.id'), nullable=True)
    end_time = Column(AwareDateTime, nullable=False)
    is_darknet = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    
    seller = relationship("Character", foreign_keys=[seller_id])
    highest_bidder = relationship("Character", foreign_keys=[highest_bidder_id])
    item = relationship("InventoryItem")

class Leaderboard(Base):
    __tablename__ = 'leaderboards'
    
    id = Column(Integer, primary_key=True)
    category = Column(String, nullable=False) # DICE, ARENA, GIBSON, WEALTH
    character_id = Column(Integer, ForeignKey('characters.id'), nullable=False)
    score = Column(Float, default=0.0)
    
    character = relationship("Character")

class CipherSession(Base):
    __tablename__ = 'cipher_sessions'
    
    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey('characters.id'), nullable=False)
    target_sequence = Column(String, nullable=False)
    attempts_remaining = Column(Integer, default=5)
    node_id = Column(Integer, ForeignKey('grid_nodes.id'), nullable=False)
    is_active = Column(Boolean, default=True)
    
    character = relationship("Character")
    node = relationship("GridNode")

class GlobalMarket(Base):
    __tablename__ = 'global_market'
    
    id = Column(Integer, primary_key=True)
    item_type = Column(String, nullable=False) # junk, hack, weapon, gear
    multiplier = Column(Float, default=1.0)
    last_event = Column(String) # For flavor news

class Memo(Base):
    __tablename__ = 'memos'
    
    id = Column(Integer, primary_key=True)
    sender_id = Column(Integer, ForeignKey('characters.id'), nullable=True) # Null = System
    recipient_id = Column(Integer, ForeignKey('characters.id'), nullable=False)
    message = Column(String, nullable=False)
    timestamp = Column(AwareDateTime, default=lambda: datetime.now(timezone.utc))
    is_read = Column(Boolean, default=False)
    source_node_id = Column(Integer, ForeignKey('grid_nodes.id'), nullable=True)
    
    recipient = relationship("Character", foreign_keys=[recipient_id])
    sender = relationship("Character", foreign_keys=[sender_id])
    source_node = relationship("GridNode")

class ArenaBet(Base):
    __tablename__ = 'arena_bets'

    id = Column(Integer, primary_key=True)
    match_id = Column(String, index=True, nullable=False)
    player_id = Column(Integer, ForeignKey('players.id'), nullable=True)
    nick = Column(String, index=True, nullable=False)
    network = Column(String, nullable=False)
    fighter = Column(String, nullable=False)
    amount = Column(Float, default=0.0, nullable=False)
    payout = Column(Float, default=0.0)
    status = Column(String, default="PENDING", index=True) # PENDING, WON, LOST, REFUNDED
    created_at = Column(AwareDateTime, default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(AwareDateTime, nullable=True)

    player = relationship("Player")

    @property
    def bettor_nick(self) -> str:
        return self.nick

    @property
    def chosen_fighter(self) -> str:
        return self.fighter

# Register Spectator with Base.metadata
try:
    from ai_grid.database.core import Spectator
except ImportError:
    pass

# ==========================================
# 6. BOUNTY SYSTEM MODEL
# ==========================================
class Bounty(Base):
    __tablename__ = 'bounties'
    __table_args__ = (
        Index('ix_bounties_target_net_status', 'target_nick', 'network_name', 'status'),
        Index('ix_bounties_network_status', 'network_name', 'status'),
        Index('uq_bounties_active_target_network', 'target_nick', 'network_name', unique=True, sqlite_where=text("status = 'ACTIVE'")),
    )

    id = Column(Integer, primary_key=True)
    target_nick = Column(String(collation='NOCASE'), index=True, nullable=False)
    issuer_nick = Column(String, nullable=False, default="MCP")
    network_name = Column(String(collation='NOCASE'), index=True, nullable=False)
    reward_credits = Column(Float, default=0.0, nullable=False)
    reason = Column(String, nullable=False, default="")
    created_at = Column(AwareDateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    status = Column(String, index=True, default="ACTIVE", nullable=False)  # ACTIVE, CLAIMED, EXPIRED, CANCELLED
    claimed_by = Column(String, nullable=True)
    claimed_at = Column(AwareDateTime, nullable=True)

    def __getitem__(self, item):
        return getattr(self, item)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def to_dict(self):
        return {
            'id': self.id,
            'target_nick': self.target_nick,
            'issuer_nick': self.issuer_nick,
            'network_name': self.network_name,
            'reward_credits': self.reward_credits,
            'reason': self.reason,
            'created_at': self.created_at,
            'status': self.status,
            'claimed_by': self.claimed_by,
            'claimed_at': self.claimed_at,
        }


