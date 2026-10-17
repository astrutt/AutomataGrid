# ai_grid/core/handlers/remote_net.py
import asyncio
import inspect
import json
import logging
from typing import Optional, List, Dict, Any

from ai_grid.grid_utils import format_text, tag_msg, C_GREEN, C_CYAN, C_RED, C_YELLOW, C_WHITE
from .base import get_action_routing, check_rate_limit

logger = logging.getLogger("manager")


def get_hub_target_bot(hub, network_name: str):
    """Resolves target bot instance from hub across casing variations."""
    if not hub:
        return None
    nodes = getattr(hub, "nodes", {})
    if network_name in nodes:
        return nodes[network_name]
    if network_name.lower() in nodes:
        return nodes[network_name.lower()]
    for k, v in nodes.items():
        if k.lower() == network_name.lower():
            return v
    return None


async def handle_remote_alert_routing(node, alert_data: Dict[str, Any]):
    """
    R3: Routes triggered IDS or breach alerts to the target node owner's IRC
    network channel, NEVER to the attacker's home channel.
    """
    if not alert_data:
        return

    try:
        target_network = alert_data.get("target_network")
        alert_msg = alert_data.get("alert_msg")
        if not target_network or not alert_msg:
            return

        hub = getattr(node, "hub", None)
        if hub:
            target_bot = get_hub_target_bot(hub, target_network)
            if target_bot:
                target_chan = target_bot.config.get("channel", f"#{target_network.lower()}")
                await target_bot.send(f"PRIVMSG {target_chan} :{tag_msg(alert_msg, action='ALARM', result='BREACH')}")
                return
            if hasattr(hub, "relay_message"):
                target_chan = alert_data.get("target_channel", f"#{target_network.lower()}")
                tagged_alert = tag_msg(alert_msg, action='ALARM', result='BREACH')
                await hub.relay_message(target_network, target_chan, tagged_alert)
                return

        if hasattr(node, "relay_remote_alert"):
            await node.relay_remote_alert(target_network, alert_msg)
    except Exception as e:
        logger.warning(f"Remote alert routing failed: {e}")


async def handle_remote_net_command(
    node, nick: str, reply_target: str, target_network_or_action: str, args: Optional[List[str]] = None
):
    """
    Dispatches Remote Network Operations (NET Device) - Section 6:
    Commands:
      net <network> explore
      net <network> probe <target>
      net <network> hack <target>
      net <network> siphon <target> [power|data|all] [%]
      net <network> exploit <target>
      net <network> raid <target>
      net <network> claim <target>
      net <network> upgrade <target>
      net <network> repair <target>
      net <network> status <target>
      net <network> stash <target>
      net <network> msg <message>
      net <network> msg <channel> <nick> [message]
      net state <OPEN|CLOSED|STEALTH>
      net <network> state <OPEN|CLOSED|STEALTH>
    """
    args = args or []
    private_target, broadcast_chan, machine_mode, reply_method = await get_action_routing(node, nick, reply_target)

    # 1. State Configuration: net state <state> [node_name] or net mode <state> [node_name]
    if target_network_or_action.lower() in ["state", "mode"]:
        if not args:
            await node.send(f"{reply_method} {private_target} :{tag_msg('Syntax: net state <OPEN|CLOSED|STEALTH> [node_name]', action='NET', result='ERR', nick=nick, is_machine=machine_mode)}")
            return
        state = args[0].upper()
        target_node_name = args[1] if len(args) > 1 else None
        ok, msg = await node.db.remote_net.set_net_device_state(nick, node.net_name, state, node_name=target_node_name)
        await node.send(f"{reply_method} {private_target} :{tag_msg(msg, action='NET', result='SUCCESS' if ok else 'FAIL', nick=nick, is_machine=machine_mode)}")
        return

    # Direct state shortcut: net <OPEN|CLOSED|STEALTH> [node_name]
    if target_network_or_action.upper() in ["OPEN", "CLOSED", "STEALTH"]:
        state = target_network_or_action.upper()
        target_node_name = args[0] if args else None
        ok, msg = await node.db.remote_net.set_net_device_state(nick, node.net_name, state, node_name=target_node_name)
        await node.send(f"{reply_method} {private_target} :{tag_msg(msg, action='NET', result='SUCCESS' if ok else 'FAIL', nick=nick, is_machine=machine_mode)}")
        return

    target_network = target_network_or_action

    # 2. State Configuration with network: net <network> state <state> [node_name]
    if args and args[0].lower() in ["state", "mode"]:
        if len(args) < 2:
            await node.send(f"{reply_method} {private_target} :{tag_msg('Syntax: net <network> state <OPEN|CLOSED|STEALTH> [node_name]', action='NET', result='ERR', nick=nick, is_machine=machine_mode)}")
            return
        state = args[1].upper()
        target_node_name = args[2] if len(args) > 2 else None
        ok, msg = await node.db.remote_net.set_net_device_state(nick, node.net_name, state, node_name=target_node_name)
        await node.send(f"{reply_method} {private_target} :{tag_msg(msg, action='NET', result='SUCCESS' if ok else 'FAIL', nick=nick, is_machine=machine_mode)}")
        return

    # 3. Network Link Affinity (Single arg fallback): grid net <network>
    if not args:
        res = await node.db.link_network(nick, node.net_name, target_network)
        ok, msg = res.get('success', False), res.get('msg', 'Link failed.')
        await node.send(f"{reply_method} {private_target} :{tag_msg(msg, action='NET', result='SUCCESS' if ok else 'FAIL', nick=nick, is_machine=machine_mode)}")
        return

    sub_action = args[0].lower()
    sub_args = args[1:]

    # Rate limiting for intensive remote ops
    if sub_action in ["explore", "probe", "hack", "siphon", "exploit", "raid", "claim", "upgrade", "repair", "status", "stash"]:
        cooldown = 15 if sub_action in ["explore", "status", "stash"] else (30 if sub_action in ["probe", "hack", "claim", "upgrade", "repair"] else 60)
        if not await check_rate_limit(node, nick, reply_target, cooldown=cooldown, consume=False, verb=f"net_{sub_action}"):
            return

    # 4. NET Messaging: net <network> msg ...
    if sub_action == "msg":
        if not sub_args:
            await node.send(f"{reply_method} {private_target} :{tag_msg('Syntax: net <network> msg <message> OR net <network> msg <channel> <nick>', action='NET', result='ERR', nick=nick, is_machine=machine_mode)}")
            return

        # Verification: Attacker must be at a claimed node with active NET pointed at target network
        async with node.db.async_session() as session:
            char = await node.db.get_character_by_nick(nick, node.net_name, session)
            ok, vmsg, _ = node.db.remote_net.verify_remote_prerequisites(char, target_network)
            if not ok:
                await node.send(f"{reply_method} {private_target} :{tag_msg(vmsg, action='NET', result='FAIL', nick=nick, is_machine=machine_mode)}")
                return

        hub = getattr(node, "hub", None)
        target_bot = get_hub_target_bot(hub, target_network)

        # Syntax A: net <network> msg <channel> <nick> [message]
        is_channel_syntax = (
            sub_args[0].startswith(("#", "&"))
            or (len(sub_args) >= 2 and sub_args[0].lower() == target_network.lower())
        )
        if is_channel_syntax:
            t_chan = sub_args[0] if sub_args[0].startswith(("#", "&")) else f"#{sub_args[0].lower()}"
            t_nick = sub_args[1] if len(sub_args) > 1 else None
            t_content = " ".join(sub_args[2:]) if len(sub_args) > 2 else f"Incoming connection from <{nick}@{node.net_name}>."

            dispatched = False
            if target_bot:
                chan_msg = f"{t_nick}: <{nick}@{node.net_name}> {t_content}" if t_nick else f"<{nick}@{node.net_name}> {t_content}"
                await target_bot.send(f"PRIVMSG {t_chan} :{chan_msg}")
                if t_nick:
                    await target_bot.send(f"PRIVMSG {t_nick} :<{nick}@{node.net_name}> {t_content}")
                dispatched = True
            elif hub and hasattr(hub, "relay_message"):
                chan_msg = f"{t_nick}: <{nick}@{node.net_name}> {t_content}" if t_nick else f"<{nick}@{node.net_name}> {t_content}"
                dispatched = await hub.relay_message(target_network, t_chan, chan_msg)
                if t_nick:
                    await hub.relay_message(target_network, t_nick, f"<{nick}@{node.net_name}> {t_content}")

            if dispatched:
                await node.send(f"{reply_method} {private_target} :{tag_msg(f'Message dispatched to {t_chan} on {target_network}.', action='NET', result='SUCCESS', nick=nick, is_machine=machine_mode)}")
            else:
                await node.send(f"{reply_method} {private_target} :{tag_msg(f'Relay to {target_network} failed: network bridge unreachable.', action='NET', result='FAIL', nick=nick, is_machine=machine_mode)}")
            return

        # Syntax B: net <network> msg <message> (Broadcast to global channel)
        broadcast_msg = " ".join(sub_args)
        t_chan = target_bot.config.get("channel", f"#{target_network.lower()}") if target_bot else f"#{target_network.lower()}"
        payload = f"[NET BROADCAST from {nick}@{node.net_name}] {broadcast_msg}"

        dispatched = False
        if target_bot:
            await target_bot.send(f"PRIVMSG {t_chan} :{payload}")
            dispatched = True
        elif hub and hasattr(hub, "relay_message"):
            dispatched = await hub.relay_message(target_network, t_chan, payload)

        if dispatched:
            await node.send(f"{reply_method} {private_target} :{tag_msg(f'Broadcast relayed to {target_network} global channel.', action='NET', result='SUCCESS', nick=nick, is_machine=machine_mode)}")
        else:
            await node.send(f"{reply_method} {private_target} :{tag_msg(f'Broadcast to {target_network} failed: network bridge unreachable.', action='NET', result='FAIL', nick=nick, is_machine=machine_mode)}")
        return

    # 5. Remote Operations
    if sub_action == "explore":
        result = await node.db.remote_net.remote_explore(nick, node.net_name, target_network)
        ok = result.get("success", False)
        msg = result.get("msg", "Exploration complete.")
        await node.send(f"{reply_method} {private_target} :{tag_msg(msg, action='NET_EXPLORE', result='SUCCESS' if ok else 'FAIL', nick=nick, is_machine=machine_mode)}")
        if ok and hasattr(node, "add_xp"):
            res = node.add_xp(nick, 5, reply_target)
            if asyncio.iscoroutine(res):
                await res
        return

    target_name = sub_args[0] if sub_args else None
    if not target_name:
        await node.send(f"{reply_method} {private_target} :{tag_msg(f'Syntax: net {target_network} {sub_action} <target>', action='NET', result='ERR', nick=nick, is_machine=machine_mode)}")
        return

    if sub_action == "probe":
        result = await node.db.remote_net.remote_probe(nick, node.net_name, target_network, target_name)
        ok = result.get("success", False)
        msg = result.get("msg", "Probe complete.")
        alert_data = result.get("alert_data")
        if alert_data:
            asyncio.create_task(handle_remote_alert_routing(node, alert_data))
        await node.send(f"{reply_method} {private_target} :{tag_msg(msg, action='NET_PROBE', result='SUCCESS' if ok else 'FAIL', nick=nick, is_machine=machine_mode)}")
        if ok and hasattr(node, "add_xp"):
            res = node.add_xp(nick, 15, reply_target)
            if asyncio.iscoroutine(res):
                await res
        return

    if sub_action == "hack":
        ok, msg, alert_data = await node.db.remote_net.remote_hack(nick, node.net_name, target_network, target_name)
        if alert_data:
            asyncio.create_task(handle_remote_alert_routing(node, alert_data))
        await node.send(f"{reply_method} {private_target} :{tag_msg(msg, action='NET_HACK', result='SUCCESS' if ok else 'FAIL', nick=nick, is_machine=machine_mode)}")
        if ok and hasattr(node, "add_xp"):
            res = node.add_xp(nick, 25, reply_target)
            if asyncio.iscoroutine(res):
                await res
        return

    if sub_action == "claim":
        result = await node.db.remote_claim_node(nick, node.net_name, target_network, target_name)
        ok = result.get("success", False)
        msg = result.get("msg", "Claim sequence resolved.")
        alert_data = result.get("alert_data")
        if alert_data:
            asyncio.create_task(handle_remote_alert_routing(node, alert_data))
        fmt_msg = format_text(msg, C_GREEN if ok else C_RED, is_machine=machine_mode)
        await node.send(f"{reply_method} {private_target} :{tag_msg(fmt_msg, action='NET_CLAIM', result='SUCCESS' if ok else 'FAIL', nick=nick, is_machine=machine_mode)}")
        if ok and hasattr(node, "add_xp"):
            res = node.add_xp(nick, 50, reply_target)
            if asyncio.iscoroutine(res):
                await res
        return

    if sub_action in ["upgrade", "repair", "status", "stash"]:
        result = await node.db.remote_manage_node(nick, node.net_name, target_network, target_name, sub_action, sub_args[1:])
        ok = result.get("success", False)
        msg = result.get("msg", "Remote administration sequence resolved.")
        alert_data = result.get("alert_data")
        if alert_data:
            asyncio.create_task(handle_remote_alert_routing(node, alert_data))
        color = C_CYAN if (ok and sub_action in ["status", "stash"]) else (C_GREEN if ok else C_RED)
        fmt_msg = format_text(msg, color, is_machine=machine_mode)
        action_tag = f"NET_{sub_action.upper()}"
        await node.send(f"{reply_method} {private_target} :{tag_msg(fmt_msg, action=action_tag, result='SUCCESS' if ok else 'FAIL', nick=nick, is_machine=machine_mode)}")
        if ok and hasattr(node, "add_xp"):
            xp_reward = 25 if sub_action == "upgrade" else (20 if sub_action == "repair" else 5)
            res = node.add_xp(nick, xp_reward, reply_target)
            if asyncio.iscoroutine(res):
                await res
        return

    if sub_action == "siphon":
        # Check if caller owns target node on target_network across bridge
        is_owned = False
        try:
            owner_fn = getattr(node.db, "is_node_owner", None)
            if owner_fn is None and hasattr(node.db, "remote_net"):
                owner_fn = getattr(node.db.remote_net, "is_node_owner", None)
            if owner_fn is None and hasattr(node.db, "grid"):
                owner_fn = getattr(node.db.grid, "is_node_owner", None)

            if owner_fn and callable(owner_fn):
                res = owner_fn(nick, node.net_name, target_network, target_name)
                if inspect.isawaitable(res):
                    is_owned = await res
                elif isinstance(res, bool):
                    is_owned = res
        except Exception as e:
            logger.debug(f"Remote siphon ownership verification failed: {e}")
            is_owned = False

        if is_owned:
            result = await node.db.remote_manage_node(nick, node.net_name, target_network, target_name, "siphon", sub_args[1:])
            ok = result.get("success", False)
            msg = result.get("msg", "Remote siphon resolved.")
            alert_data = result.get("alert_data")
            if alert_data:
                asyncio.create_task(handle_remote_alert_routing(node, alert_data))
            fmt_msg = format_text(msg, C_GREEN if ok else C_RED, is_machine=machine_mode)
            await node.send(f"{reply_method} {private_target} :{tag_msg(fmt_msg, action='NET_SIPHON', result='SUCCESS' if ok else 'FAIL', nick=nick, is_machine=machine_mode)}")
            if ok and hasattr(node, "add_xp"):
                res = node.add_xp(nick, 20, reply_target)
                if asyncio.iscoroutine(res):
                    await res
            return
        else:
            percent = 100.0
            if len(sub_args) > 1:
                try:
                    percent = float(sub_args[1].rstrip("%"))
                except ValueError:
                    pass
            ok, msg, alert_data = await node.db.remote_net.remote_siphon(nick, node.net_name, target_network, target_name, percent=percent)
            if alert_data:
                asyncio.create_task(handle_remote_alert_routing(node, alert_data))
            fmt_msg = format_text(msg, C_GREEN if ok else C_RED, is_machine=machine_mode)
            await node.send(f"{reply_method} {private_target} :{tag_msg(fmt_msg, action='NET_SIPHON', result='SUCCESS' if ok else 'FAIL', nick=nick, is_machine=machine_mode)}")
            if ok and hasattr(node, "add_xp"):
                res = node.add_xp(nick, 20, reply_target)
                if asyncio.iscoroutine(res):
                    await res
            return

    if sub_action == "exploit":
        item_name = sub_args[1] if len(sub_args) > 1 else None
        ok, msg, alert_data = await node.db.remote_net.remote_exploit(nick, node.net_name, target_network, target_name, item_name=item_name)
        if alert_data:
            asyncio.create_task(handle_remote_alert_routing(node, alert_data))
        await node.send(f"{reply_method} {private_target} :{tag_msg(msg, action='NET_EXPLOIT', result='SUCCESS' if ok else 'FAIL', nick=nick, is_machine=machine_mode)}")
        if ok and hasattr(node, "add_xp"):
            res = node.add_xp(nick, 35, reply_target)
            if asyncio.iscoroutine(res):
                await res
        return

    if sub_action == "raid":
        result = await node.db.remote_net.remote_raid(nick, node.net_name, target_network, target_name)
        ok = result.get("success", False)
        msg = result.get("msg", "Raid sequence resolved.")
        alert_data = result.get("alert_data")
        if alert_data:
            asyncio.create_task(handle_remote_alert_routing(node, alert_data))
        await node.send(f"{reply_method} {private_target} :{tag_msg(msg, action='NET_RAID', result='SUCCESS' if ok else 'FAIL', nick=nick, is_machine=machine_mode)}")
        if ok and hasattr(node, "add_xp"):
            res = node.add_xp(nick, 30, reply_target)
            if asyncio.iscoroutine(res):
                await res
        return

    # Unrecognized operation
    await node.send(f"{reply_method} {private_target} :{tag_msg(f'Unrecognized remote operation: {sub_action}. Supported: explore, probe, hack, siphon, exploit, raid, claim, upgrade, repair, status, stash, msg, state', action='NET', result='ERR', nick=nick, is_machine=machine_mode)}")
