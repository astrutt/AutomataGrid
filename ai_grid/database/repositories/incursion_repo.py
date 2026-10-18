# ai_grid/database/repositories/incursion_repo.py
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.future import select
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from ai_grid.models import IncursionEvent, IncursionDefender, GridNode, Character
from ai_grid.database.base_repo import BaseRepository

logger = logging.getLogger("grid_db")

class IncursionRepository(BaseRepository):
    _locks: dict = {}

    def _get_lock(self) -> asyncio.Lock:
        """Returns or creates a loop-safe asyncio.Lock for serializing incursion state transitions."""
        loop = asyncio.get_running_loop()
        if loop not in IncursionRepository._locks:
            IncursionRepository._locks[loop] = asyncio.Lock()
        return IncursionRepository._locks[loop]

    async def get_random_incursion_node(self, network: str) -> GridNode | None:
        """Selects a random non-safezone node on this network without active incursions."""
        async with self.async_session() as session:
            now = datetime.now(timezone.utc)
            active_node_ids = select(IncursionEvent.node_id).where(
                IncursionEvent.network_name == network,
                IncursionEvent.status == 'ACTIVE',
                IncursionEvent.expires_at > now
            )
            # Select non-safezone nodes that do not already have an active incursion
            stmt_free = select(GridNode).where(
                GridNode.net_affinity == network,
                GridNode.node_type != 'safezone',
                GridNode.id.not_in(active_node_ids)
            ).order_by(func.random()).limit(1)
            return (await session.execute(stmt_free)).scalars().first()

    async def spawn_incursion(
        self,
        network: str,
        inc_type: str,
        tier: int,
        reward: float = 500.0,
        duration_mins: int = 5,
        target_node_id: int | None = None
    ) -> dict | None:
        """Manifests a new Incursion Event on an unoccupied non-safezone node or specified target node."""
        async with self._get_lock():
            async with self.async_session() as session:
                now = datetime.now(timezone.utc)
                tier_val = max(1, min(4, int(tier)))
                if target_node_id is not None:
                    # Ensure target node does not already host an active incursion and is not a safezone
                    active_stmt = select(IncursionEvent.id).where(
                        IncursionEvent.node_id == target_node_id,
                        IncursionEvent.status == 'ACTIVE',
                        IncursionEvent.expires_at > now
                    )
                    if (await session.execute(active_stmt)).scalars().first():
                        return None
                    stmt = select(GridNode).where(
                        GridNode.id == target_node_id,
                        GridNode.net_affinity == network,
                        GridNode.node_type != 'safezone'
                    )
                else:
                    # Select random non-safezone node on this network without active incursions
                    active_node_ids = select(IncursionEvent.node_id).where(
                        IncursionEvent.network_name == network,
                        IncursionEvent.status == 'ACTIVE',
                        IncursionEvent.expires_at > now
                    )
                    stmt = select(GridNode).where(
                        GridNode.net_affinity == network,
                        GridNode.node_type != 'safezone',
                        GridNode.id.not_in(active_node_ids)
                    ).order_by(func.random()).limit(1)
                
                node = (await session.execute(stmt)).scalars().first()
                if not node:
                    return None
                
                expiry = now + timedelta(minutes=duration_mins)
                
                inc = IncursionEvent(
                    node_id=node.id,
                    network_name=network,
                    incursion_type=inc_type,
                    tier=tier_val,
                    reward_val=reward,
                    expires_at=expiry,
                    status='ACTIVE'
                )
                session.add(inc)
                await session.commit()
                
                return {
                    "node_name": node.name,
                    "type": inc_type,
                    "tier": tier_val,
                    "reward": reward,
                    "expiry": expiry
                }

    async def register_defense(self, char_name: str, network: str, node_name: str) -> tuple[bool, str, list[str]]:
        """Registers a player's defense against an active incursion and checks resolution."""
        async with self._get_lock():
            async with self.async_session() as session:
                # Get character
                stmt_char = select(Character).where(Character.name == char_name)
                char = (await session.execute(stmt_char)).scalars().first()
                if not char:
                    return False, "Character not found.", []
                
                # Get node
                stmt_node = select(GridNode).where(GridNode.name == node_name, GridNode.net_affinity == network)
                node = (await session.execute(stmt_node)).scalars().first()
                if not node:
                    return False, "Target node not found on this network.", []
                    
                # Find active incursion
                inc_stmt = select(IncursionEvent).where(
                    IncursionEvent.node_id == node.id,
                    IncursionEvent.status == 'ACTIVE',
                    IncursionEvent.expires_at > datetime.now(timezone.utc)
                ).options(selectinload(IncursionEvent.defenders))
                
                inc = (await session.execute(inc_stmt)).scalars().first()
                if not inc:
                    return False, "No active incursion detected at this coordinate.", []
                    
                # Location Validation
                success, err_msg = await self.verify_presence(char, node, "defend")
                if not success:
                    return False, err_msg, []
                    
                # Check if already defended
                if any(d.character_id == char.id for d in inc.defenders):
                    return False, f"You have already deployed defense protocols against the {inc.incursion_type}.", []
                    
                # Register Defense
                defense = IncursionDefender(incursion_id=inc.id, character_id=char.id)
                session.add(defense)
                
                # Need to commit to refresh the defenders relationship accurately or just append to the list in memory
                inc.defenders.append(defense)
                
                current_defenders = len(inc.defenders)
                tier_defenders_map = {1: 1, 2: 2, 3: 4, 4: 8}
                required = tier_defenders_map.get(inc.tier, inc.tier)
                
                if current_defenders >= required:
                    # Resolve Incursion
                    inc.status = 'RESOLVED'
                    victors = []
                    
                    # Pay out rewards using v1.8.3 calibrated scaling
                    for d in inc.defenders:
                        stmt_d_char = select(Character).where(Character.id == d.character_id)
                        d_char = (await session.execute(stmt_d_char)).scalars().first()
                        if d_char:
                            pkg = self.calculate_mcp_rewards(d_char.level, 'defend')
                            # Scale by Tier
                            pkg['credits'] *= inc.tier
                            pkg['data'] *= inc.tier
                            pkg['xp'] *= inc.tier
                            
                            d_char.credits += pkg['credits']
                            d_char.data_units += pkg['data']
                            await self.add_xp_to_char(d_char, pkg['xp'], session)
                            # Reduce MCP Heat by 1 on successful defense per Mechanics Draft Section 9
                            if hasattr(d_char, 'mcp_heat') and d_char.mcp_heat and d_char.mcp_heat > 0:
                                d_char.mcp_heat = max(0.0, d_char.mcp_heat - 1.0)
                            victors.append(d_char.name)
                            
                    msg = f"SUCCESS: The {inc.incursion_type} has been repelled! Rewards distributed to all defenders."
                    await session.commit()
                    return True, msg, victors
                else:
                    await session.commit()
                    msg = f"Defense protocols engaged. Waiting for {required - current_defenders} more player(s) to repel {inc.incursion_type}."
                    return True, msg, []

    async def expire_incursions(self, network: str, llm_client=None) -> list[str]:
        """Tails through expired incursions and handles flavor generation."""
        notifications = []
        expired_data = []
        async with self._get_lock():
            async with self.async_session() as session:
                now = datetime.now(timezone.utc)
                stmt = select(IncursionEvent).where(
                    IncursionEvent.network_name == network,
                    IncursionEvent.status == 'ACTIVE',
                    IncursionEvent.expires_at <= now
                ).options(selectinload(IncursionEvent.node))
                
                expired = (await session.execute(stmt)).scalars().all()
                for inc in expired:
                    inc.status = 'EXPIRED'
                    node_name = inc.node.name if inc.node else "Unknown Node"
                    expired_data.append((inc.incursion_type, node_name))
                
                await session.commit()

        # Generate flavor text outside DB transaction and lock to prevent latency bottlenecks
        for inc_type, node_name in expired_data:
            flavor_text = f"The {inc_type} on {node_name} dissipated into the net before full materialization."
            if llm_client and hasattr(llm_client, 'generate_incursion_flavor'):
                try:
                    flavor_text = await llm_client.generate_incursion_flavor(inc_type, node_name)
                except Exception as e:
                    logger.error(f"Error generating incursion flavor: {e}")
            notifications.append(f"[GRID ALARM] {flavor_text}")

        return notifications
