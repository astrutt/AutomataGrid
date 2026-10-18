# ai_grid/database/repositories/messenger_repo.py
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from sqlalchemy import func
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from ai_grid.database.base_repo import BaseRepository
from ai_grid.models import MessengerDispatch, Character, Player, NetworkAlias

logger = logging.getLogger("messenger_repo")

BASE_COURIER_CREDITS: float = 2500.0
BASE_COURIER_POWER: float = 50.0
ESCALATION_MULTIPLIER: float = 0.5  # +50% per dispatch in last 24h


class MessengerRepository(BaseRepository):
    """
    Repository managing MCP Grid Messenger Bot dispatch state, escalating fees,
    and cross-network courier history.
    """

    async def _get_character_ci(self, nick: str, network: str, session) -> Optional[Character]:
        """Resolves nickname and network case-insensitively to a Character model within session."""
        if not nick or not isinstance(nick, str):
            return None
        char = await self.get_character_by_nick(nick, network, session)
        if not char:
            nick_lower = nick.lower()
            net_lower = network.lower() if network else ""
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                func.lower(NetworkAlias.nickname) == nick_lower,
                func.lower(NetworkAlias.network_name) == net_lower
            ).options(selectinload(Character.current_node))
            char = (await session.execute(stmt)).scalars().first()
            if not char:
                stmt2 = select(Character).where(
                    func.lower(Character.name) == nick_lower
                ).options(selectinload(Character.current_node))
                char = (await session.execute(stmt2)).scalars().first()
        return char

    async def get_recent_dispatch_count(self, sender_nick: str, hours: int = 24) -> int:
        """Counts courier dispatches by sender in the last N hours."""
        if not sender_nick:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        async with self.async_session() as session:
            stmt = select(func.count(MessengerDispatch.id)).where(
                func.lower(MessengerDispatch.sender_nick) == sender_nick.lower(),
                MessengerDispatch.dispatched_at >= cutoff
            )
            res = await session.execute(stmt)
            return int(res.scalar() or 0)

    async def calculate_fee(self, sender_nick: str, hours: int = 24) -> Tuple[float, float, int]:
        """
        Calculates escalating courier fee:
        fee = base_cost * (1 + 0.5 * recent_dispatches)
        Returns (fee_credits, fee_power, recent_dispatches).
        """
        recent = await self.get_recent_dispatch_count(sender_nick, hours=hours)
        fee_credits = BASE_COURIER_CREDITS * (1.0 + ESCALATION_MULTIPLIER * recent)
        fee_power = BASE_COURIER_POWER
        return fee_credits, fee_power, recent

    async def deduct_fee(
        self,
        sender_nick: str,
        network: str,
        fee_credits: float,
        fee_power: float
    ) -> Tuple[bool, str, Optional[Character]]:
        """
        Atomically validates balance and deducts courier dispatch fees.
        Returns (success: bool, message: str, character: Optional[Character]).
        """
        async with self.async_session() as session:
            char = await self._get_character_ci(sender_nick, network, session)
            if not char:
                return False, f"Unregistered persona: '{sender_nick}' must register before dispatching couriers.", None

            current_credits = float(char.credits or 0.0)
            current_power = float(char.power or 0.0)

            if current_credits < fee_credits:
                return (
                    False,
                    f"Insufficient credits: Required {fee_credits:,.1f}c (Current balance: {current_credits:,.1f}c).",
                    char
                )

            if current_power < fee_power:
                return (
                    False,
                    f"Insufficient power: Required {fee_power:,.1f}uP (Current reserves: {current_power:,.1f}uP).",
                    char
                )

            char.credits = current_credits - fee_credits
            char.power = current_power - fee_power
            await session.commit()
            return True, "Courier dispatch fee deducted successfully.", char

    async def record_dispatch(
        self,
        sender_nick: str,
        character_id: Optional[int],
        source_network: str,
        target_network: str,
        target_dest: str,
        message: str,
        fee_credits: float,
        fee_power: float,
        wait_seconds: int = 0,
        status: str = "DISPATCHED"
    ) -> MessengerDispatch:
        """Records a new courier dispatch in the database."""
        async with self.async_session() as session:
            dispatch = MessengerDispatch(
                sender_nick=sender_nick,
                character_id=character_id,
                source_network=source_network,
                target_network=target_network,
                target_dest=target_dest,
                message=message,
                dispatched_at=datetime.now(timezone.utc),
                fee_credits=fee_credits,
                fee_power=fee_power,
                wait_seconds=wait_seconds,
                status=status
            )
            session.add(dispatch)
            await session.commit()
            await session.refresh(dispatch)
            return dispatch

    async def update_dispatch(
        self,
        dispatch_id: int,
        reply_received: Optional[str] = None,
        status: Optional[str] = None
    ) -> bool:
        """Updates dispatch record status or logged reply."""
        async with self.async_session() as session:
            stmt = select(MessengerDispatch).where(MessengerDispatch.id == dispatch_id)
            dispatch = (await session.execute(stmt)).scalars().first()
            if not dispatch:
                return False
            if reply_received is not None:
                dispatch.reply_received = reply_received
            if status is not None:
                dispatch.status = status
            await session.commit()
            return True

