# ai_grid/core/handlers/messenger.py
"""
MCP Grid Messenger Command Handler.
Routes !a messenger and !a dispatch commands, enforces escalating courier costs,
60-second rate limits, admin FQDN overrides, and cross-network reply relaying.
"""

import asyncio
import logging
import time
from typing import Any, List, Optional

from ai_grid.core.messenger import MCPMessengerClient, resolve_target_network
from ai_grid.core.validation import sanitize_irc_outbound
from ai_grid.models import Memo

logger = logging.getLogger("messenger_handler")


async def handle_messenger(node: Any, source_nick: str, args: List[str], reply_target: str):
    """
    Handles:
      !a messenger <network|fqdn> <channel|user> <message> [--wait <seconds>]
      !a dispatch <network|fqdn> <channel|user> <message> [--wait <seconds>]
    """
    prefix = getattr(node, "prefix", "!a")

    if not args or len(args) < 3:
        await node.send(
            f"PRIVMSG {reply_target} :[ERR] Syntax: {prefix} messenger <network|fqdn> <channel|user> <message> [--wait <seconds>]"
        )
        return

    # Extract optional --wait <seconds> flag
    wait_seconds = 0
    clean_args: List[str] = []
    i = 0
    while i < len(args):
        if args[i].lower() == "--wait":
            if i + 1 >= len(args):
                await node.send(
                    f"PRIVMSG {reply_target} :[ERR] Syntax: --wait requires a duration in seconds (5-300)."
                )
                return
            try:
                raw_wait = int(args[i + 1])
                if raw_wait < 5 or raw_wait > 300:
                    await node.send(
                        f"PRIVMSG {reply_target} :[ERR] Invalid --wait duration '{args[i + 1]}'. Must be an integer between 5 and 300."
                    )
                    return
                wait_seconds = raw_wait
            except ValueError:
                await node.send(
                    f"PRIVMSG {reply_target} :[ERR] Invalid --wait duration '{args[i + 1]}'. Must be an integer between 5 and 300."
                )
                return
            i += 2
        else:
            clean_args.append(args[i])
            i += 1

    if len(clean_args) < 3:
        await node.send(
            f"PRIVMSG {reply_target} :[ERR] Syntax: {prefix} messenger <network|fqdn> <channel|user> <message> [--wait <seconds>]"
        )
        return

    target_network_input = clean_args[0].strip()
    target_dest = clean_args[1].strip()
    message = " ".join(clean_args[2:]).strip()
    message = sanitize_irc_outbound(message)

    if not message:
        await node.send(f"PRIVMSG {reply_target} :[ERR] Message payload cannot be empty.")
        return

    # Check Administrative Status
    is_admin = False
    if hasattr(node, "is_admin") and callable(node.is_admin):
        is_admin = node.is_admin(source_nick)
    elif hasattr(node, "check_admin_privilege") and callable(node.check_admin_privilege):
        auth, _ = node.check_admin_privilege(source_nick)
        is_admin = bool(auth)
    elif hasattr(node, "admins") and node.admins:
        is_admin = source_nick.lower() in [a.lower() for a in node.admins]
        if hasattr(node, "nickserv_verified") and node.nickserv_verified:
            is_admin = is_admin and (source_nick.lower() in [v.lower() for v in node.nickserv_verified])

    # Enforce Player Rate Limit (60-second cooldown between courier dispatches)
    now_ts = time.time()
    cooldowns = getattr(node, "messenger_cooldowns", None)
    if cooldowns is None:
        cooldowns = {}
        node.messenger_cooldowns = cooldowns

    last_dispatch = cooldowns.get(source_nick.lower(), 0.0)
    if not is_admin and (now_ts - last_dispatch) < 60.0:
        remaining = max(1, int(round(60.0 - (now_ts - last_dispatch))))
        await node.send(
            f"PRIVMSG {reply_target} :[ERR] Messenger cooldown active. Please wait {remaining}s before dispatching another courier."
        )
        return

    # Scoping & Network Resolution
    ok, err_msg, net_info = resolve_target_network(node, target_network_input, is_admin)
    if not ok:
        await node.send(f"PRIVMSG {reply_target} :[ERR] {err_msg}")
        return

    # Character Resolution & Escalating Cost Calculation
    fee_credits = 2500.0
    fee_power = 50.0
    recent_dispatches = 0

    if hasattr(node, "db") and hasattr(node.db, "messenger"):
        fee_credits, fee_power, recent_dispatches = await node.db.messenger.calculate_fee(
            source_nick, hours=24
        )

    # Check Admin Bypass on FQDN dispatches
    is_fqdn = net_info.get("is_fqdn", False)
    char = None

    if is_admin and is_fqdn:
        # Admins executing FQDN test dispatches are not blocked by personal balance
        if hasattr(node, "db") and hasattr(node.db, "messenger"):
            async with node.db.async_session() as s:
                char = await node.db.messenger._get_character_ci(source_nick, node.net_name, s)
    else:
        # Standard player or non-FQDN dispatch: enforce registration and balance deduction
        if hasattr(node, "db") and hasattr(node.db, "messenger"):
            success, deduct_msg, char = await node.db.messenger.deduct_fee(
                source_nick, node.net_name, fee_credits, fee_power
            )
            if not success:
                await node.send(f"PRIVMSG {reply_target} :[ERR] {deduct_msg}")
                return
        else:
            # Fallback if no database attached
            pass

    # Update cooldown timestamp
    cooldowns[source_nick.lower()] = now_ts

    # Record dispatch in database
    dispatch_rec = None
    if hasattr(node, "db") and hasattr(node.db, "messenger"):
        try:
            dispatch_rec = await node.db.messenger.record_dispatch(
                sender_nick=source_nick,
                character_id=char.id if char else None,
                source_network=getattr(node, "net_name", "2600net"),
                target_network=net_info["network_name"],
                target_dest=target_dest,
                message=message,
                fee_credits=fee_credits if not (is_admin and is_fqdn) else 0.0,
                fee_power=fee_power if not (is_admin and is_fqdn) else 0.0,
                wait_seconds=wait_seconds,
                status="DISPATCHED",
            )
        except Exception as e:
            logger.debug(f"Failed to record dispatch in database: {e}")

    # Acknowledge launch on origin network
    target_net_display = net_info["network_name"]
    fee_display = (
        "Admin FQDN Test Bypass | 0.0c, 0.0uP"
        if (is_admin and is_fqdn)
        else f"Fee: {fee_credits:,.1f}c, {fee_power:,.1f}uP | 24h count: {recent_dispatches + 1}"
    )
    await node.send(
        f"PRIVMSG {reply_target} :[MCP COURIER DISPATCH] Courier initiated for {target_dest} on {target_net_display} "
        f"({fee_display}). Launching..."
    )

    # Callback when courier logs a reply
    async def on_reply(reply_nick: str, reply_text: str):
        clean_reply_text = sanitize_irc_outbound(reply_text)
        reply_notice = (
            f"[MCP COURIER DISPATCH] Reply from {reply_nick} on {target_net_display}: \"{clean_reply_text}\""
        )
        await node.send(f"PRIVMSG {reply_target} :{reply_notice}")

        # Transmit memo to sender character if registered
        char_id = getattr(char, "id", None)
        if not char_id and hasattr(node, "db") and hasattr(node.db, "messenger"):
            try:
                async with node.db.async_session() as s:
                    c = await node.db.messenger._get_character_ci(source_nick, node.net_name, s)
                    if c:
                        char_id = c.id
            except Exception:
                pass

        if char_id and hasattr(node, "db") and hasattr(node.db, "async_session"):
            try:
                async with node.db.async_session() as s:
                    memo = Memo(
                        sender_id=None,
                        recipient_id=char_id,
                        message=f"[MCP COURIER REPLY] From {reply_nick} on {target_net_display}: {clean_reply_text}",
                    )
                    s.add(memo)
                    await s.commit()
            except Exception as ex:
                logger.debug(f"Error persisting reply memo: {ex}")

        # Update database record
        if dispatch_rec and hasattr(node, "db") and hasattr(node.db, "messenger"):
            try:
                await node.db.messenger.update_dispatch(
                    dispatch_rec.id, reply_received=clean_reply_text, status="COMPLETED"
                )
            except Exception:
                pass

    # Background task wrapper
    async def courier_task_wrapper(courier_client):
        try:
            await courier_client.run()
            if courier_client.reply_received is not None:
                # Reply was already logged and forwarded via on_reply
                pass
            elif courier_client.wait_seconds > 0 and courier_client.status == "EXPIRED":
                await node.send(
                    f"PRIVMSG {reply_target} :[MCP COURIER DISPATCH] Courier mission complete: Reply window expired with no response from {target_dest} on {target_net_display}."
                )
                if dispatch_rec and hasattr(node, "db") and hasattr(node.db, "messenger"):
                    await node.db.messenger.update_dispatch(dispatch_rec.id, status="EXPIRED")
            else:
                await node.send(
                    f"PRIVMSG {reply_target} :[MCP COURIER DISPATCH] Courier mission complete: Delivery to {target_dest} on {target_net_display} concluded."
                )
                if dispatch_rec and hasattr(node, "db") and hasattr(node.db, "messenger"):
                    await node.db.messenger.update_dispatch(dispatch_rec.id, status="COMPLETED")
        except Exception as e:
            logger.error(f"[handle_messenger] Courier dispatch error: {e}")
            await node.send(
                f"PRIVMSG {reply_target} :[MCP COURIER DISPATCH] Courier delivery error: {e}"
            )
            if dispatch_rec and hasattr(node, "db") and hasattr(node.db, "messenger"):
                await node.db.messenger.update_dispatch(dispatch_rec.id, status="FAILED")

    # Instantiate Client (supports test factory injection)
    client_factory = getattr(node, "messenger_client_factory", None) or MCPMessengerClient
    client = client_factory(
        target_server=net_info["server"],
        target_port=net_info["port"],
        target_dest=target_dest,
        message=message,
        sender_nick=source_nick,
        source_network=getattr(node, "net_name", "2600net"),
        ssl_enabled=net_info["ssl"],
        wait_seconds=wait_seconds,
        on_reply=on_reply,
        node=node,
    )

    task = asyncio.create_task(courier_task_wrapper(client))
    bg_tasks = getattr(node, "_background_tasks", None)
    if bg_tasks is not None and isinstance(bg_tasks, set):
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)

