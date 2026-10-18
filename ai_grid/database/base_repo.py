# ai_grid/database/base_repo.py
from sqlalchemy import func
from sqlalchemy.future import select
from ai_grid.models import Character, Player, NetworkAlias, GridNode
from sqlalchemy.orm import selectinload
from ai_grid.database.core import CONFIG, logger

class BaseRepository:
    def __init__(self, async_session):
        self.async_session = async_session

    async def get_available_node_power(self, node, session) -> float:
        """Returns the specific node's power or the pooled power of its local network."""
        if not node.owner_character_id or not node.local_network:
            return node.power_stored
            
        # Sum power from all nodes in the same local network owned by the same player
        stmt = select(func.sum(GridNode.power_stored)).where(
            GridNode.owner_character_id == node.owner_character_id,
            GridNode.local_network == node.local_network
        )
        res = await session.execute(stmt)
        return res.scalar() or 0.0

    async def consume_node_power(self, node, amount: float, session) -> bool:
        """Deducts power from the node or the pool proportionately."""
        if not node.owner_character_id or not node.local_network:
            if node.power_stored < amount: return False
            node.power_stored -= amount
            return True
            
        # Get pool members
        stmt = select(GridNode).where(
            GridNode.owner_character_id == node.owner_character_id,
            GridNode.local_network == node.local_network
        )
        pool_nodes = (await session.execute(stmt)).scalars().all()
        total_pool = sum(n.power_stored for n in pool_nodes)
        if total_pool < amount: return False
        
        # Deduct proportionately or just sequentially
        remaining = amount
        for n in pool_nodes:
            can_take = min(remaining, n.power_stored)
            n.power_stored -= can_take
            remaining -= can_take
            if remaining <= 0: break
        return True
    async def get_character_by_nick(self, nick: str, network: str, session) -> Character:
        """Shared utility for resolving a nickname to a Character model within a session."""
        nick_lower = nick.lower()
        stmt = select(Character).join(Player).join(NetworkAlias).where(
            func.lower(NetworkAlias.nickname) == nick_lower,
            NetworkAlias.network_name == network
        ).options(selectinload(Character.current_node))
        char = (await session.execute(stmt)).scalars().first()
        if not char:
            stmt2 = select(Character).where(
                func.lower(Character.name) == nick_lower
            ).options(selectinload(Character.current_node))
            char = (await session.execute(stmt2)).scalars().first()
        return char

    async def verify_presence(self, char: Character, target_node, command_label: str) -> tuple[bool, str]:
        """ Standardizes physical presence checks for grid interactions.
            Allows remote interaction for specific commands (collect, defend, patch, repair).
        """
        remote_allowed = ['collect', 'defend', 'patch', 'repair']
        
        # If command is in the remote allowed list, bypass the presence check
        if command_label.lower() in remote_allowed:
            return True, ""
            
        # Verify physical presence
        if not char.node_id or char.node_id != target_node.id:
            return False, f"[GRID][SITREP] Physical presence required at {target_node.name} for {command_label.upper()} protocols."
            
        return True, ""

    def calculate_mcp_rewards(self, level: int, action_type: str) -> dict:
        """
        Calculates a balanced reward package (XP, Credits, Data) for MCP actions.
        Scaling: 4 Big tasks to level at L1, 100 Big tasks to level at L50.
        """
        try:
            level = max(1, int(float(level))) if level is not None else 1
        except (TypeError, ValueError, OverflowError):
            level = 1
        rewards = CONFIG.get('mechanics', {}).get('mcp_rewards')
        if not rewards:
            rewards = {
                'xp_divisor_low': 4.0,
                'xp_divisor_high': 100.0,
                'max_level_ref': 50,
                'base_credits': {'small': 50.0, 'big': 200.0, 'biggest': 500.0},
                'base_data': {'small': 10.0, 'big': 40.0, 'biggest': 100.0},
                'multipliers': {'repair': 1.0, 'defend': 1.0}
            }
            
        div_low = rewards.get('xp_divisor_low', 4.0)
        div_high = rewards.get('xp_divisor_high', 100.0)
        max_lvl = rewards.get('max_level_ref', 50)
        
        # 1. Calculate XP Divisor using linear interpolation
        # Divisor moves from 4.0 (L1) to 100.0 (L50)
        progress = min(1.0, max(0.0, (level - 1) / (max_lvl - 1)))
        divisor = div_low + (progress * (div_high - div_low))
        
        # 2. Get threshold for the NEXT level
        threshold = int(100 * (1.25 ** (level - 1)))
        
        # 3. Base 'Big' XP package
        base_xp = threshold / divisor
        
        # 4. Apply multipliers and base values
        mul = rewards.get('multipliers', {}).get(action_type, 1.0)
        
        # Reward Tier Determination
        tier = "small"
        if action_type == 'repair': tier = 'big'
        if action_type == 'defend': tier = 'biggest'
        
        credits = rewards.get('base_credits', {}).get(tier, 50.0) * level
        data = rewards.get('base_data', {}).get(tier, 10.0) * (1 + (level / 10.0))
        
        return {
            "xp": int(base_xp * mul),
            "credits": round(credits, 1),
            "data": round(data, 1)
        }

    async def add_xp_to_char(self, char: Character, amount: int, session):
        """
        Awards XP and handles level-ups using the standard exponential formula.
        Logic mirrored from ProgressionRepository for cross-repo utility.
        """
        char.xp += amount
        while True:
            # v1.8.0 Math: 100 * (1.25 ** (char.level - 1))
            xp_threshold = int(100 * (1.25 ** (char.level - 1)))
            if char.xp >= xp_threshold:
                char.xp -= xp_threshold
                char.level += 1
                if char.race != "Spectator":
                    char.pending_stat_points += 1
            else:
                break
