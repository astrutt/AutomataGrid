# ai_grid/core/handlers/bounty.py
import asyncio
import math
from datetime import datetime, timezone
from ai_grid.grid_utils import format_text, tag_msg, C_CYAN, C_GREEN, C_RED, C_YELLOW, C_WHITE
from .base import get_action_routing
from ai_grid.core.validation import validate_nickname


async def _send_bounty_msg(node, private_target: str, reply_method: str, mode: str, human: str, text: str, narrative: str):
    if mode == "text":
        out = text
    elif mode == "narrative":
        out = narrative
    else:
        out = human
    await node.send(f"{reply_method} {private_target} :{out}")


async def handle_bounty(node, nick: str, args: list, reply_target: str):
    """
    IRC Interface for the Bounty Board System:
    - !a bounty / !a bounty list: Displays formatted active bounties
    - !a bounty check <nick>: Inspects bounty details or eligibility status
    - !a bounty post <nick> <credits> [reason]: Posts a player-funded bounty (min 100c)
    - Formats output across human, text, and narrative modes
    """
    private_target, broadcast_chan, machine_mode, reply_method = await get_action_routing(node, nick, reply_target)

    # Determine mode: human, text, or narrative
    prefs = {}
    if hasattr(node, 'db') and hasattr(node.db, 'get_prefs'):
        try:
            res = node.db.get_prefs(nick, getattr(node, 'net_name', 'default'))
            if asyncio.iscoroutine(res) or hasattr(res, '__await__'):
                prefs = await res
            elif isinstance(res, dict):
                prefs = res
        except Exception:
            prefs = {}

    raw_mode = prefs.get('output_mode', 'human').lower() if isinstance(prefs, dict) else 'human'
    if raw_mode in ['machine', 'text']:
        mode = 'text'
    elif raw_mode == 'narrative':
        mode = 'narrative'
    else:
        mode = 'human'

    if not args or not args[0].strip():
        await _handle_bounty_list(node, nick, private_target, reply_method, mode)
        return

    sub = args[0].lower().strip()

    if sub in ["list", "board", "all"]:
        await _handle_bounty_list(node, nick, private_target, reply_method, mode)
    elif sub in ["check", "inspect", "info", "view"]:
        target_nick = args[1].strip() if len(args) > 1 else None
        await _handle_bounty_check(node, nick, target_nick, private_target, reply_method, mode)
    elif sub in ["post", "place", "set", "add"]:
        subargs = args[1:]
        await _handle_bounty_post(node, nick, subargs, private_target, broadcast_chan, reply_method, mode)
    elif sub in ["help", "?"]:
        await _handle_bounty_help(node, nick, private_target, reply_method, mode)
    else:
        # If single arg provided and valid nickname, interpret as check
        if len(args) == 1 and validate_nickname(sub):
            await _handle_bounty_check(node, nick, args[0], private_target, reply_method, mode)
        else:
            await _handle_bounty_help(node, nick, private_target, reply_method, mode)


async def _handle_bounty_list(node, nick: str, private_target: str, reply_method: str, mode: str):
    net_name = getattr(node, 'net_name', 'rizon')
    bounties = await node.db.get_active_bounties(net_name) if hasattr(node.db, 'get_active_bounties') else []

    if not bounties:
        await _send_bounty_msg(
            node, private_target, reply_method, mode,
            human=tag_msg(format_text("[BOUNTY] No active bounties currently listed on this network.", C_CYAN), action="BOUNTY", result="INFO", nick=nick),
            text=f"[GRID][ACTION:BOUNTY][RESULT:LIST][NICK:{nick}] COUNT:0 BOUNTIES:[]",
            narrative=f"[GRID][NARRATIVE] The bounty terminals flicker with static. No active contracts are currently open on {net_name}."
        )
        return

    if mode == "text":
        b_entries = [f"{b.target_nick}:{b.reward_credits:.1f}c:{b.issuer_nick}:{b.reason.replace(' ', '_')}" for b in bounties]
        msg = f"[GRID][ACTION:BOUNTY][RESULT:LIST][NICK:{nick}] COUNT:{len(bounties)} BOUNTIES:[{','.join(b_entries)}]"
        await node.send(f"{reply_method} {private_target} :{msg}")
    elif mode == "narrative":
        b_descs = [f"{b.target_nick} ({b.reward_credits:.1f}c, issued by {b.issuer_nick} for {b.reason})" for b in bounties]
        msg = f"[GRID][NARRATIVE] The bounty board displays {len(bounties)} active contracts: {'; '.join(b_descs)}."
        await node.send(f"{reply_method} {private_target} :{msg}")
    else:
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('[ BOUNTY BOARD ]', C_CYAN, bold=True), action='BOUNTY', result='INFO', nick=nick)}")
        for b in bounties:
            line = f"🎯 TARGET: {b.target_nick} | REWARD: {b.reward_credits:.1f}c | ISSUER: {b.issuer_nick} | REASON: {b.reason}"
            await node.send(f"{reply_method} {private_target} :{tag_msg(line, action='BOUNTY', is_machine=False)}")


async def _handle_bounty_check(node, nick: str, target_nick: str, private_target: str, reply_method: str, mode: str):
    if not target_nick:
        await _send_bounty_msg(
            node, private_target, reply_method, mode,
            human=tag_msg(format_text("Syntax error: !a bounty check <nick>", C_RED), action="BOUNTY", result="ERR", nick=nick),
            text=f"[GRID][ACTION:BOUNTY][RESULT:ERR][NICK:{nick}] MSG:SYNTAX_ERROR_SPECIFY_NICK",
            narrative="[GRID][NARRATIVE] Syntax error: specify which target to inspect with !a bounty check <nick>."
        )
        return

    net_name = getattr(node, 'net_name', 'rizon')
    bounty = await node.db.get_bounty_for_target(target_nick, net_name) if hasattr(node.db, 'get_bounty_for_target') else None

    if bounty:
        if mode == "text":
            msg = f"[GRID][ACTION:BOUNTY][RESULT:CHECK][NICK:{nick}] TARGET:{bounty.target_nick} STATUS:ACTIVE REWARD:{bounty.reward_credits:.1f} ISSUER:{bounty.issuer_nick} REASON:{bounty.reason.replace(' ', '_')}"
            await node.send(f"{reply_method} {private_target} :{msg}")
        elif mode == "narrative":
            msg = f"[GRID][NARRATIVE] Terminal query for {bounty.target_nick}: Active contract for {bounty.reward_credits:.1f}c issued by {bounty.issuer_nick}. Directive: {bounty.reason}."
            await node.send(f"{reply_method} {private_target} :{msg}")
        else:
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(f'[ BOUNTY: {bounty.target_nick} ]', C_CYAN, bold=True), action='BOUNTY', result='INFO', nick=nick)}")
            info_line = f"Status: ACTIVE | Reward: {bounty.reward_credits:.1f}c | Issuer: {bounty.issuer_nick} | Reason: {bounty.reason}"
            await node.send(f"{reply_method} {private_target} :{tag_msg(info_line, action='BOUNTY', is_machine=False)}")
        return

    # No active bounty, check reputation eligibility
    elig = await node.db.is_bounty_eligible(target_nick, net_name) if hasattr(node.db, 'is_bounty_eligible') else {"eligible": False}

    if elig.get("eligible"):
        heat_val = float(elig.get("heat", 0.0))
        reasons_str = "; ".join(elig.get("reasons", [])) or "Hostile Standing / High Heat"
        status_label = elig.get("status", "Bounty")

        if mode == "text":
            msg = f"[GRID][ACTION:BOUNTY][RESULT:CHECK][NICK:{nick}] TARGET:{target_nick} STATUS:ELIGIBLE REWARD:0.0 HEAT:{heat_val:.1f} ELIGIBLE:TRUE"
            await node.send(f"{reply_method} {private_target} :{msg}")
        elif mode == "narrative":
            msg = f"[GRID][NARRATIVE] While {target_nick} currently has no active bounty listed, their MCP heat ({heat_val:.1f}) and standing flag them as eligible for automated bounties: {reasons_str}."
            await node.send(f"{reply_method} {private_target} :{msg}")
        else:
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(f'[ BOUNTY: {target_nick} ]', C_YELLOW, bold=True), action='BOUNTY', result='INFO', nick=nick)}")
            elig_line = f"Target has no active bounty, but is ELIGIBLE for MCP bounty! Heat: {heat_val:.1f} ({status_label}). Reasons: {reasons_str}"
            await node.send(f"{reply_method} {private_target} :{tag_msg(elig_line, action='BOUNTY', is_machine=False)}")
    else:
        if mode == "text":
            msg = f"[GRID][ACTION:BOUNTY][RESULT:CHECK][NICK:{nick}] TARGET:{target_nick} STATUS:CLEAN REWARD:0.0 ELIGIBLE:FALSE"
            await node.send(f"{reply_method} {private_target} :{msg}")
        elif mode == "narrative":
            msg = f"[GRID][NARRATIVE] Scan complete: {target_nick} is operating with clean network standing and carries no active bounty."
            await node.send(f"{reply_method} {private_target} :{msg}")
        else:
            await node.send(f"{reply_method} {private_target} :{tag_msg(f'[BOUNTY: {target_nick}] No active bounty found and target is not currently bounty eligible.', action='BOUNTY', result='INFO', nick=nick)}")


async def _handle_bounty_post(node, nick: str, subargs: list, private_target: str, broadcast_chan: str, reply_method: str, mode: str):
    if len(subargs) < 2:
        await _send_bounty_msg(
            node, private_target, reply_method, mode,
            human=tag_msg(format_text("Syntax: !a bounty post <nick> <credits> [reason]", C_RED), action="BOUNTY", result="ERR", nick=nick),
            text=f"[GRID][ACTION:BOUNTY][RESULT:ERR][NICK:{nick}] MSG:SYNTAX_ERROR_USAGE_POST_NICK_CREDITS_REASON",
            narrative="[GRID][NARRATIVE] Syntax error: specify target and reward with !a bounty post <nick> <credits> [reason]."
        )
        return

    target_nick = subargs[0].strip()
    if not validate_nickname(target_nick):
        await _send_bounty_msg(
            node, private_target, reply_method, mode,
            human=tag_msg(format_text(f"Invalid target nickname '{target_nick}'.", C_RED), action="BOUNTY", result="ERR", nick=nick),
            text=f"[GRID][ACTION:BOUNTY][RESULT:ERR][NICK:{nick}] MSG:INVALID_TARGET_NICKNAME",
            narrative=f"[GRID][NARRATIVE] Bounty placement failed: '{target_nick}' is not a valid network nickname."
        )
        return

    try:
        reward = float(subargs[1])
        if math.isnan(reward) or math.isinf(reward):
            raise ValueError()
    except (ValueError, TypeError):
        await _send_bounty_msg(
            node, private_target, reply_method, mode,
            human=tag_msg(format_text("Bounty credits must be a valid number.", C_RED), action="BOUNTY", result="ERR", nick=nick),
            text=f"[GRID][ACTION:BOUNTY][RESULT:ERR][NICK:{nick}] MSG:INVALID_CREDITS_AMOUNT",
            narrative="[GRID][NARRATIVE] Bounty placement failed: credit amount must be a valid numeric quantity."
        )
        return

    reason = " ".join(subargs[2:]).strip() if len(subargs) > 2 else f"Contract posted by {nick}"
    net_name = getattr(node, 'net_name', 'rizon')

    success, msg, bounty = await node.db.post_bounty(nick, target_nick, net_name, reward, reason)

    if not success:
        await _send_bounty_msg(
            node, private_target, reply_method, mode,
            human=tag_msg(format_text(f"[ERR] {msg}", C_RED), action="BOUNTY", result="FAIL", nick=nick),
            text=f"[GRID][ACTION:BOUNTY][RESULT:FAIL][NICK:{nick}] MSG:{msg.upper().replace(' ', '_')}",
            narrative=f"[GRID][NARRATIVE] Bounty placement rejected: {msg}"
        )
        return

    # Success response
    await _send_bounty_msg(
        node, private_target, reply_method, mode,
        human=tag_msg(format_text(msg, C_GREEN), action="BOUNTY", result="SUCCESS", nick=nick),
        text=f"[GRID][ACTION:BOUNTY][RESULT:SUCCESS][NICK:{nick}] TARGET:{bounty.target_nick} REWARD:{bounty.reward_credits:.1f} MSG:BOUNTY_REGISTERED",
        narrative=f"[GRID][NARRATIVE] {nick} transmits bounty contract onto the network: {reward:.1f}c placed on {bounty.target_nick}. Total bounty now stands at {bounty.reward_credits:.1f}c."
    )

    # Public channel broadcast announcement
    chan = node.config.get('channel', '#arena') if getattr(node, 'config', None) else '#arena'
    announcement = f"[BOUNTY POSTED] {nick} placed a {reward:.1f}c bounty on {bounty.target_nick}! Total Reward: {bounty.reward_credits:.1f}c."
    if hasattr(node, 'send'):
        await node.send(f"PRIVMSG {chan} :{tag_msg(format_text(announcement, C_YELLOW), tags=['BOUNTY', 'SIGACT'])}")


async def _handle_bounty_help(node, nick: str, private_target: str, reply_method: str, mode: str):
    if mode == "text":
        msg = f"[GRID][ACTION:BOUNTY][RESULT:HELP][NICK:{nick}] SYNTAX:!a_bounty_<list|check_<nick>|post_<nick>_<credits>_[reason]>"
        await node.send(f"{reply_method} {private_target} :{msg}")
    elif mode == "narrative":
        msg = "[GRID][NARRATIVE] Bounty terminal commands: list contracts with '!a bounty', inspect a target with '!a bounty check <nick>', or fund a contract with '!a bounty post <nick> <credits> [reason]'."
        await node.send(f"{reply_method} {private_target} :{msg}")
    else:
        prefix = getattr(node, 'prefix', '!a')
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('[ BOUNTY COMMANDS ]', C_CYAN, bold=True), action='BOUNTY', result='INFO', nick=nick)}")
        await node.send(f"{reply_method} {private_target} :{tag_msg(f'{prefix} bounty [list] - Display all open bounties', action='BOUNTY', is_machine=False)}")
        await node.send(f"{reply_method} {private_target} :{tag_msg(f'{prefix} bounty check <nick> - Inspect bounty status on a target', action='BOUNTY', is_machine=False)}")
        await node.send(f"{reply_method} {private_target} :{tag_msg(f'{prefix} bounty post <nick> <credits> [reason] - Post a bounty (min 100c)', action='BOUNTY', is_machine=False)}")
