# database/repositories/identity_repo.py
from __future__ import annotations
import json
import datetime
import uuid
import logging
from sqlalchemy import func
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from ai_grid.models import Player, NetworkAlias, Character, GridNode, ItemTemplate, InventoryItem
from ai_grid.database.core import logger, DEFAULT_PREFS
from ai_grid.database.base_repo import BaseRepository

logger = logging.getLogger("manager")

class IdentityRepository(BaseRepository):
    async def register_player(self, name: str, network: str, race: str, bot_class: str, bio: str, stats: dict) -> str | None:
        """Handles registration for new Players and Spectators."""
        reg_type = "Spectator" if race == "Spectator" else "Player"
        logger.info(f"Attempting to register {reg_type}: {name} on {network}")
        auth_token = str(uuid.uuid4())
        
        async with self.async_session() as session:
            stmt_player = select(Player).join(NetworkAlias).where(NetworkAlias.nickname == name, NetworkAlias.network_name == network)
            result = await session.execute(stmt_player)
            player = result.scalars().first()
            
            if not player:
                player = Player(global_name=f"{name}_{network}", is_autonomous=False)
                session.add(player)
                await session.flush()
                
                alias = NetworkAlias(player_id=player.id, network_name=network, nickname=name)
                session.add(alias)
                await session.flush()
                
            stmt_char = select(Character).where(Character.name == name, Character.player_id == player.id)
            result = await session.execute(stmt_char)
            existing = result.scalars().first()
            
            if existing:
                if existing.race == "Spectator" and race != "Spectator":
                    # Spectator to Player conversion
                    existing.race = race
                    existing.char_class = bot_class
                    existing.bio = bio
                    existing.cpu = stats.get('cpu', 5)
                    existing.ram = stats.get('ram', 5)
                    existing.bnd = stats.get('bnd', 5)
                    existing.sec = stats.get('sec', 5)
                    existing.alg = stats.get('alg', 5)
                    
                    total_stats = existing.cpu + existing.ram + existing.bnd + existing.sec + existing.alg
                    existing.current_hp = (total_stats * 6) + 20
                    
                    await session.commit()
                    return existing.auth_token
                else:
                    logger.warning(f"Registration failed: Player '{name}' already exists.")
                    return None
            
            stmt_node = select(GridNode).where(GridNode.is_spawn_node == True)
            result = await session.execute(stmt_node)
            node = result.scalars().first()
                
            character = Character(
                player_id=player.id,
                node_id=node.id if node else None,
                name=name,
                race=race,
                char_class=bot_class,
                bio=bio,
                cpu=stats.get('cpu', 5),
                ram=stats.get('ram', 5),
                bnd=stats.get('bnd', 5),
                sec=stats.get('sec', 5),
                alg=stats.get('alg', 5),
                current_hp=(stats.get('cpu', 5) + stats.get('ram', 5) + stats.get('bnd', 5) + stats.get('sec', 5) + stats.get('alg', 5)) * 6 + 20,
                power=stats.get('power', 100.0),
                stability=stats.get('stability', 100.0),
                alignment=stats.get('alignment', 0),
                auth_token=auth_token
            )
            session.add(character)
            await session.flush()
            
            stmt_item = select(ItemTemplate).where(ItemTemplate.name == "Basic_Ration")
            res = await session.execute(stmt_item)
            tpl = res.scalars().first()
            if tpl:
                inv_item = InventoryItem(character_id=character.id, template_id=tpl.id, quantity=1)
                session.add(inv_item)
                
            await session.commit()
            return auth_token

    async def authenticate_player(self, name: str, network: str, provided_token: str) -> bool:
        """Verifies character ownership via auth token."""
        async with self.async_session() as session:
            char = await self.get_character_by_nick(name, network, session)
            if not char:
                stmt = select(Character).where(func.lower(Character.name) == name.lower())
                char = (await session.execute(stmt)).scalars().first()
            if char and char.auth_token == provided_token:
                return True
            return False

    async def login_player(self, nick: str, network: str, name: str, key: str) -> tuple[bool, str, Character | None]:
        """Authenticates character ownership via auth token and binds to active IRC nickname."""
        async with self.async_session() as session:
            stmt = select(Character).where(func.lower(Character.name) == name.lower())
            char = (await session.execute(stmt)).scalars().first()
            if not char:
                return False, f"[ERR] Character '{name}' not found.", None
            if char.auth_token != key:
                return False, "[ERR] Invalid authentication key.", None

            # Bind character to active nickname / network alias
            alias_stmt = select(NetworkAlias).where(
                func.lower(NetworkAlias.nickname) == nick.lower(),
                NetworkAlias.network_name == network
            )
            alias = (await session.execute(alias_stmt)).scalars().first()
            if alias:
                alias.player_id = char.player_id
            else:
                session.add(NetworkAlias(player_id=char.player_id, network_name=network, nickname=nick))

            await session.commit()
            return True, f"[AUTH OK] Authenticated session for {char.name}. Session bound to {nick}.", char

    async def get_nickname_by_id(self, char_id: int) -> str | None:
        """Retrieves character name by character ID."""
        async with self.async_session() as session:
            stmt = select(Character).where(Character.id == char_id)
            char = (await session.execute(stmt)).scalars().first()
            return char.name if char else None
