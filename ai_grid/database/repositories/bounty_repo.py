# ai_grid/database/repositories/bounty_repo.py
import logging
import math
import re
from datetime import datetime, timezone
from typing import Optional, List, Tuple
from sqlalchemy import func, update
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError

from ai_grid.database.base_repo import BaseRepository
from ai_grid.models import Bounty, Character, Player, NetworkAlias

logger = logging.getLogger("bounty_repo")


def _parse_mcp_tag(reason_str: str) -> Tuple[Optional[float], Optional[str]]:
    """Extracts existing MCP reward amount and reason from a bounty reason string if present."""
    if not reason_str or not isinstance(reason_str, str):
        return None, None
    m = re.search(r"\[MCP:\s*([0-9.]+)c(?::\s*([^\]]*))?\]", reason_str)
    if m:
        try:
            amt = float(m.group(1))
            desc = m.group(2) or ""
            return amt, desc
        except (ValueError, TypeError):
            pass
    return None, None


class BountyRepository(BaseRepository):
    """
    Repository managing the Bounty Board System:
    - Active bounty tracking per network
    - Player-funded bounty posting and credit deduction
    - MCP automated bounties based on Heat and Hostile standing
    - Atomic bounty claiming and payout upon combat resolution
    """

    async def _get_character_ci(self, nick: str, network: str, session) -> Optional[Character]:
        """Resolves nickname and network case-insensitively to a Character model within session."""
        if not nick or not isinstance(nick, str) or not network or not isinstance(network, str):
            return None
        char = await self.get_character_by_nick(nick, network, session)
        if not char:
            nick_lower = nick.lower()
            net_lower = network.lower()
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

    async def _resolve_target_names(self, target_nick: str, network: str, session) -> Tuple[Optional[Character], List[str]]:
        """Resolves target nickname to canonical Character and all associated aliases on the network."""
        if not target_nick or not isinstance(target_nick, str) or not network or not isinstance(network, str):
            return None, []
        names = {target_nick.lower()}
        char = await self._get_character_ci(target_nick, network, session)
        if char:
            if char.name:
                names.add(char.name.lower())
            if char.player_id:
                stmt_aliases = select(NetworkAlias.nickname).where(
                    NetworkAlias.player_id == char.player_id,
                    func.lower(NetworkAlias.network_name) == network.lower()
                )
                aliases = (await session.execute(stmt_aliases)).scalars().all()
                for a in aliases:
                    if a:
                        names.add(a.lower())
        return char, list(names)

    async def get_active_bounties(self, network: str) -> List[Bounty]:
        """Returns list of currently open bounties on the specified network."""
        if not network or not isinstance(network, str):
            return []
        async with self.async_session() as session:
            stmt = select(Bounty).where(
                func.lower(Bounty.network_name) == network.lower(),
                Bounty.status == "ACTIVE"
            ).order_by(Bounty.reward_credits.desc())
            return list((await session.execute(stmt)).scalars().all())

    async def get_bounty_for_target(self, target_nick: str, network: str) -> Optional[Bounty]:
        """Returns active bounty for a specific target on the network, if any."""
        if not target_nick or not isinstance(target_nick, str) or not network or not isinstance(network, str):
            return None
        async with self.async_session() as session:
            _, names = await self._resolve_target_names(target_nick, network, session)
            if not names:
                return None
            stmt = select(Bounty).where(
                func.lower(Bounty.target_nick).in_(names),
                func.lower(Bounty.network_name) == network.lower(),
                Bounty.status == "ACTIVE"
            )
            return (await session.execute(stmt)).scalars().first()

    async def post_bounty(
        self,
        issuer_nick: str,
        target_nick: str,
        network: str,
        reward_credits: float,
        reason: str = ""
    ) -> Tuple[bool, str, Optional[Bounty]]:
        """
        Validate issuer funds, deduct credits, and create a player-funded bounty.
        Prevent duplicate active bounties on the same target by stacking reward onto existing bounty.
        """
        if not issuer_nick or not isinstance(issuer_nick, str) or not target_nick or not isinstance(target_nick, str) or not network or not isinstance(network, str):
            return False, "Invalid issuer, target, or network.", None

        try:
            reward = float(reward_credits)
            if math.isnan(reward) or math.isinf(reward):
                return False, "Bounty reward must be a valid finite number.", None
        except (ValueError, TypeError):
            return False, "Bounty reward must be a valid number.", None

        reward = round(reward, 2)
        if reward < 100.0:
            return False, "Minimum bounty reward is 100c.", None

        if issuer_nick.lower() == target_nick.lower():
            return False, "Self-claim rejected: Cannot post a bounty on yourself.", None

        clean_reason = reason.strip() if reason and isinstance(reason, str) and reason.strip() else f"Contract posted by {issuer_nick}"
        clean_reason = clean_reason[:255]

        async with self.async_session() as session:
            issuer_char = await self._get_character_ci(issuer_nick, network, session)
            if not issuer_char:
                return False, f"Issuer character '{issuer_nick}' not found on {network}.", None

            target_char, target_names = await self._resolve_target_names(target_nick, network, session)
            if not target_char:
                return False, f"Target player '{target_nick}' not found on {network}.", None

            if issuer_char.id == target_char.id or (issuer_char.player_id and target_char.player_id and issuer_char.player_id == target_char.player_id):
                return False, "Self-claim rejected: Cannot post a bounty on yourself.", None

            if issuer_char.credits < reward:
                return False, f"Insufficient credits. You have {issuer_char.credits:.1f}c, but bounty requires {reward:.1f}c.", None

            # Deduct funds from issuer
            issuer_char.credits -= reward

            # Check existing active bounty to stack
            stmt = select(Bounty).where(
                func.lower(Bounty.target_nick).in_(target_names),
                func.lower(Bounty.network_name) == network.lower(),
                Bounty.status == "ACTIVE"
            )
            existing = (await session.execute(stmt)).scalars().first()
            if existing and existing.status == "ACTIVE":
                new_reward = round(existing.reward_credits + reward, 2)
                if "[MCP:" not in existing.reason and existing.issuer_nick == "MCP":
                    prev_amt = existing.reward_credits
                    new_reason = f"[MCP: {prev_amt:.1f}c: {existing.reason}]; {clean_reason}"
                elif clean_reason not in existing.reason:
                    new_reason = f"{existing.reason}; {clean_reason}"
                else:
                    new_reason = existing.reason

                stmt_stack = (
                    update(Bounty)
                    .where(Bounty.id == existing.id, Bounty.status == "ACTIVE")
                    .values(
                        reward_credits=new_reward,
                        reason=new_reason
                    )
                )
                res_stack = await session.execute(stmt_stack)
                if res_stack.rowcount == 1:
                    await session.commit()
                    await session.refresh(existing)
                    return True, f"Bounty on {target_char.name} increased by {reward:.1f}c! Total reward: {existing.reward_credits:.1f}c.", existing

            # No existing active bounty found (or it was claimed right now); insert new active row
            bounty = Bounty(
                target_nick=target_char.name,
                issuer_nick=issuer_char.name or issuer_nick,
                network_name=network,
                reward_credits=reward,
                reason=clean_reason,
                created_at=datetime.now(timezone.utc),
                status="ACTIVE"
            )
            session.add(bounty)
            try:
                await session.commit()
                await session.refresh(bounty)
                return True, f"Bounty of {reward:.1f}c posted on {target_char.name}!", bounty
            except IntegrityError:
                # Race condition caught by unique partial index on active bounty
                await session.rollback()
                async with self.async_session() as retry_session:
                    retry_issuer = await self._get_character_ci(issuer_nick, network, retry_session)
                    if not retry_issuer or retry_issuer.credits < reward:
                        return False, "Insufficient credits for retry transaction.", None
                    retry_issuer.credits -= reward

                    existing_retry = (await retry_session.execute(stmt)).scalars().first()
                    if existing_retry and existing_retry.status == "ACTIVE":
                        new_reward = round(existing_retry.reward_credits + reward, 2)
                        if "[MCP:" not in existing_retry.reason and existing_retry.issuer_nick == "MCP":
                            prev_amt = existing_retry.reward_credits
                            new_reason = f"[MCP: {prev_amt:.1f}c: {existing_retry.reason}]; {clean_reason}"
                        elif clean_reason not in existing_retry.reason:
                            new_reason = f"{existing_retry.reason}; {clean_reason}"
                        else:
                            new_reason = existing_retry.reason

                        stmt_stack_retry = (
                            update(Bounty)
                            .where(Bounty.id == existing_retry.id, Bounty.status == "ACTIVE")
                            .values(
                                reward_credits=new_reward,
                                reason=new_reason
                            )
                        )
                        res_stack_retry = await retry_session.execute(stmt_stack_retry)
                        if res_stack_retry.rowcount == 1:
                            await retry_session.commit()
                            await retry_session.refresh(existing_retry)
                            return True, f"Bounty on {target_char.name} increased by {reward:.1f}c! Total reward: {existing_retry.reward_credits:.1f}c.", existing_retry
                    return False, "Failed to register bounty due to concurrent conflict.", None

    async def ensure_mcp_bounty(self, target_nick: str, network: str) -> Optional[Bounty]:
        """
        Automatically creates or updates an MCP bounty for any target flagged as eligible by
        reputation_repo.is_bounty_eligible (Heat > 6.0 or 2+ Hostile regions), with base reward
        scaled by heat and number of hostile regions (e.g. 500c base + 250c per heat above 6.0).
        """
        if not target_nick or not isinstance(target_nick, str) or not network or not isinstance(network, str):
            return None

        from ai_grid.database.repositories.reputation_repo import ReputationRepository
        rep_repo = ReputationRepository(self.async_session)
        eligibility = await rep_repo.is_bounty_eligible(target_nick, network)
        if not eligibility or not eligibility.get("eligible"):
            return None

        heat = float(eligibility.get("heat", 0.0))
        hostile_regions = eligibility.get("hostile_regions", [])
        heat_bonus = max(0.0, heat - 6.0) * 250.0
        hostile_bonus = len(hostile_regions) * 250.0 if len(hostile_regions) >= 2 else 0.0
        reward = round(500.0 + heat_bonus + hostile_bonus, 2)
        reasons = eligibility.get("reasons", [])
        reason_str = "; ".join(reasons) if reasons else f"MCP Security Bounty (Heat: {heat:.1f})"

        async with self.async_session() as session:
            target_char, target_names = await self._resolve_target_names(target_nick, network, session)
            target_name = target_char.name if target_char else target_nick

            stmt = select(Bounty).where(
                func.lower(Bounty.target_nick).in_(target_names),
                func.lower(Bounty.network_name) == network.lower(),
                Bounty.status == "ACTIVE"
            )
            existing = (await session.execute(stmt)).scalars().first()
            if existing and existing.status == "ACTIVE":
                prev_mcp, _ = _parse_mcp_tag(existing.reason)
                if prev_mcp is not None:
                    if reward > prev_mcp:
                        delta = reward - prev_mcp
                        new_reward = round(existing.reward_credits + delta, 2)
                        new_tag = f"[MCP: {reward:.1f}c: {reason_str}]"
                        new_reason = re.sub(r"\[MCP:\s*[0-9.]+c(?::\s*[^\]]*)?\]", lambda _: new_tag, existing.reason)
                    else:
                        new_reward = existing.reward_credits
                        new_reason = existing.reason
                else:
                    if existing.issuer_nick == "MCP":
                        new_reward = max(existing.reward_credits, reward)
                        new_reason = reason_str
                    else:
                        new_reward = round(existing.reward_credits + reward, 2)
                        new_reason = f"{existing.reason}; [MCP: {reward:.1f}c: {reason_str}]"

                stmt_update = (
                    update(Bounty)
                    .where(Bounty.id == existing.id, Bounty.status == "ACTIVE")
                    .values(
                        reward_credits=new_reward,
                        reason=new_reason
                    )
                )
                res_update = await session.execute(stmt_update)
                if res_update.rowcount == 1:
                    await session.commit()
                    await session.refresh(existing)
                    return existing

            # No existing active bounty (or was claimed concurrently) -> insert new
            bounty = Bounty(
                target_nick=target_name,
                issuer_nick="MCP",
                network_name=network,
                reward_credits=reward,
                reason=reason_str,
                created_at=datetime.now(timezone.utc),
                status="ACTIVE"
            )
            session.add(bounty)
            try:
                await session.commit()
                await session.refresh(bounty)
                return bounty
            except IntegrityError:
                await session.rollback()
                async with self.async_session() as retry_session:
                    existing_retry = (await retry_session.execute(stmt)).scalars().first()
                    if existing_retry and existing_retry.status == "ACTIVE":
                        prev_mcp_r, _ = _parse_mcp_tag(existing_retry.reason)
                        if prev_mcp_r is not None:
                            if reward > prev_mcp_r:
                                delta = reward - prev_mcp_r
                                new_reward = round(existing_retry.reward_credits + delta, 2)
                                new_tag = f"[MCP: {reward:.1f}c: {reason_str}]"
                                new_reason = re.sub(r"\[MCP:\s*[0-9.]+c(?::\s*[^\]]*)?\]", lambda _: new_tag, existing_retry.reason)
                            else:
                                new_reward = existing_retry.reward_credits
                                new_reason = existing_retry.reason
                        else:
                            if existing_retry.issuer_nick == "MCP":
                                new_reward = max(existing_retry.reward_credits, reward)
                                new_reason = reason_str
                            else:
                                new_reward = round(existing_retry.reward_credits + reward, 2)
                                new_reason = f"{existing_retry.reason}; [MCP: {reward:.1f}c: {reason_str}]"

                        stmt_retry = (
                            update(Bounty)
                            .where(Bounty.id == existing_retry.id, Bounty.status == "ACTIVE")
                            .values(
                                reward_credits=new_reward,
                                reason=new_reason
                            )
                        )
                        res_retry = await retry_session.execute(stmt_retry)
                        if res_retry.rowcount == 1:
                            await retry_session.commit()
                            await retry_session.refresh(existing_retry)
                            return existing_retry
                return None

    async def claim_bounty(
        self,
        target_nick: str,
        killer_nick: str,
        network: str
    ) -> Tuple[bool, str, Optional[Bounty]]:
        """
        Atomically marks active bounty as CLAIMED, sets claimed_by and claimed_at,
        and transfers reward_credits to killer's character credits.
        Rejects suicide / self-claims.
        """
        if not target_nick or not isinstance(target_nick, str) or not killer_nick or not isinstance(killer_nick, str) or not network or not isinstance(network, str):
            return False, "Self-claim rejected: Invalid target, killer, or network.", None

        if target_nick.lower() == killer_nick.lower():
            return False, "Self-claim rejected: Cannot claim a bounty on yourself.", None

        async with self.async_session() as session:
            target_char, target_names = await self._resolve_target_names(target_nick, network, session)
            target_name = target_char.name if target_char else target_nick

            killer_char = await self._get_character_ci(killer_nick, network, session)
            if not killer_char:
                return False, f"Killer character '{killer_nick}' not found on {network}.", None

            if target_char and (killer_char.id == target_char.id or (killer_char.player_id and target_char.player_id and killer_char.player_id == target_char.player_id)):
                return False, "Self-claim rejected: Cannot claim a bounty on yourself.", None

            stmt = select(Bounty).where(
                func.lower(Bounty.target_nick).in_(target_names),
                func.lower(Bounty.network_name) == network.lower(),
                Bounty.status == "ACTIVE"
            )
            bounty = (await session.execute(stmt)).scalars().first()
            if not bounty:
                return False, f"No active bounty on {target_nick}.", None

            # Atomically mark claimed using conditional predicate
            now = datetime.now(timezone.utc)
            stmt_update = (
                update(Bounty)
                .where(Bounty.id == bounty.id, Bounty.status == "ACTIVE")
                .values(
                    status="CLAIMED",
                    claimed_by=killer_char.name or killer_nick,
                    claimed_at=now
                )
            )
            try:
                res = await session.execute(stmt_update)
                if res.rowcount != 1:
                    await session.rollback()
                    return False, f"No active bounty on {target_nick}.", None

                killer_char.credits += bounty.reward_credits
                await session.commit()
                await session.refresh(bounty)
                return True, f"Bounty of {bounty.reward_credits:.1f}c on {bounty.target_nick} claimed by {killer_nick}!", bounty
            except Exception as e:
                await session.rollback()
                logger.warning(f"Error during claim_bounty transaction: {e}")
                return False, f"Failed to claim bounty on {target_nick}.", None
