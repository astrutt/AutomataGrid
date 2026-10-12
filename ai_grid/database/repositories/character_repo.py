# ai_grid/database/repositories/character_repo.py
import json
import datetime
import logging
from sqlalchemy import func
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from ai_grid.models import Character, Player, NetworkAlias, GridNode, InventoryItem
from ai_grid.database.core import logger, DEFAULT_PREFS
from ai_grid.database.base_repo import BaseRepository

logger = logging.getLogger("character_repo")

class CharacterRepository(BaseRepository):
    async def get_prefs(self, name: str, network: str) -> dict:
        """Retrieves character preferences."""
        async with self.async_session() as session:
            char = await self.get_character_by_nick(name, network, session)
            if not char: return dict(DEFAULT_PREFS)
            try:
                return {**DEFAULT_PREFS, **json.loads(char.prefs or '{}')}
            except Exception:
                return dict(DEFAULT_PREFS)

    async def get_prefs_by_id(self, char_id: int) -> dict:
        """Retrieves character preferences by character ID."""
        async with self.async_session() as session:
            stmt = select(Character).where(Character.id == char_id)
            char = (await session.execute(stmt)).scalars().first()
            if not char: return dict(DEFAULT_PREFS)
            try:
                return {**DEFAULT_PREFS, **json.loads(char.prefs or '{}')}
            except Exception:
                return dict(DEFAULT_PREFS)

    async def set_pref(self, name: str, network: str, key: str, value) -> bool:
        """Updates a specific character preference."""
        async with self.async_session() as session:
            char = await self.get_character_by_nick(name, network, session)
            if not char: return False
            prefs = json.loads(char.prefs or '{}')
            prefs[key] = value
            char.prefs = json.dumps(prefs)
            await session.commit()
            return True

    async def get_player(self, name: str, network: str):
        """Retrieves a full player profile summary."""
        async with self.async_session() as session:
            name_lower = name.lower()
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                func.lower(NetworkAlias.nickname) == name_lower,
                NetworkAlias.network_name == network
            ).options(
                selectinload(Character.inventory).selectinload(InventoryItem.template),
                selectinload(Character.skills)
            )
            result = await session.execute(stmt)
            char = result.scalars().first()
            if not char:
                stmt2 = select(Character).where(
                    func.lower(Character.name) == name_lower
                ).options(
                    selectinload(Character.inventory).selectinload(InventoryItem.template),
                    selectinload(Character.skills)
                )
                char = (await session.execute(stmt2)).scalars().first()
            
            territory_count, mesh_power = 0, 0
            if char:
                # Territory aggregation
                t_stmt = select(func.count(GridNode.id), func.sum(GridNode.power_stored)).where(GridNode.owner_character_id == char.id)
                t_res = await session.execute(t_stmt)
                territory_count, mesh_power = t_res.first()
                territory_count = territory_count or 0
                mesh_power = mesh_power or 0.0

            if char:
                inv = [item.template.name for item in char.inventory] if char.inventory else []
                skills_dict = {s.skill_name: s.level for s in char.skills} if char.skills else {}
                return {
                    'name': char.name,
                    'race': char.race,
                    'char_class': char.char_class,
                    'level': char.level,
                    'xp': char.xp,
                    'is_npc': False,
                    'cpu': char.cpu,
                    'ram': char.ram,
                    'bnd': char.bnd,
                    'sec': char.sec,
                    'alg': char.alg,
                    'bio': char.bio,
                    'inventory': json.dumps(inv), 
                    'alignment': char.alignment,
                    'stability': char.stability,
                    'power': char.power,
                    'status': char.status,
                    'elo': char.elo,
                    'wins': char.wins,
                    'losses': char.losses,
                    'credits': char.credits,
                    'current_hp': char.current_hp,
                    'max_hp': (char.cpu + char.ram + char.bnd + char.sec + char.alg) * 6 + 20,
                    'data_units': char.data_units,
                    'pending_stat_points': char.pending_stat_points,
                    'territory_count': territory_count,
                    'mesh_power': mesh_power,
                    'skills': skills_dict
                }
            return None

    async def list_players(self, network=None):
        """Lists all registered characters, optionally filtered by network."""
        async with self.async_session() as session:
            stmt = select(Character, NetworkAlias).select_from(Character).join(Player).join(NetworkAlias)
            if network:
                stmt = stmt.where(NetworkAlias.network_name == network)
            stmt = stmt.order_by(Character.elo.desc())
            
            result = await session.execute(stmt)
            players = []
            for char, alias in result:
                players.append({
                    'name': char.name,
                    'network': alias.network_name,
                    'elo': char.elo,
                    'wins': char.wins,
                    'losses': char.losses,
                    'credits': char.credits
                })
            return players

    async def update_last_seen(self, nick: str, network: str):
        """Updates the activity timestamp for a character."""
        try:
            async with self.async_session() as session:
                char = await self.get_character_by_nick(nick, network, session)
                if char:
                    char.last_seen_at = datetime.datetime.now(datetime.timezone.utc)
                    await session.commit()
        except Exception:
            pass

    async def update_activity_stats(self, nick: str, network: str, chat_inc: int, idle_sec: float):
        """Updates persistent activity stats (idle time and chat count)."""
        try:
            async with self.async_session() as session:
                char = await self.get_character_by_nick(nick, network, session)
                if char:
                    char.total_chat_messages += chat_inc
                    char.total_idle_seconds += idle_sec
                    char.last_seen_at = datetime.datetime.now(datetime.timezone.utc)
                    await session.commit()
        except Exception:
            pass
