# handlers/base.py - Utility Handlers
import asyncio
import time
from ai_grid.grid_utils import format_text, tag_msg, C_GREEN, C_CYAN, C_RED, C_YELLOW, C_WHITE

async def handle_help(node, nick: str, args: list, reply_target: str):
    """Displays a comprehensive list of all v1.7.9 commands or details for a specific verb."""
    
    private_target, broadcast_chan, machine_mode, reply_method = await get_action_routing(node, nick, reply_target)
    
    # Detailed Command Registry (v1.8.0)
    registry = {
        "register": {"desc": "Found your digital persona.", "syntax": "register <Name> <Race> <Class> <Traits>"},
        "grid": {"desc": "Diagnostic view of the current node.", "syntax": "grid"},
        "move": {"desc": "Travel to an adjacent node or cross-grid bridge.", "syntax": "move <dir|affinity>"},
        "explore": {"desc": "Scan sector for hidden architecture or fragments.", "syntax": "explore", "cost": "20u Power"},
        "probe": {"desc": "Deep scan of a specific node to reveal security weaknesses.", "syntax": "probe", "cost": "15u Power"},
        "map": {"desc": "Generate topological visualization. Radius scales with SEC/ALG.", "syntax": "map"},
        "info": {"desc": "Targeted diagnostics.", "syntax": "info [grid|arena|<nick>]"},
        "tasks": {"desc": "View active objectives and harvest rewards.", "syntax": "tasks"},
        "options": {"desc": "Set terminal preferences (e.g., machine mode).", "syntax": "options <key> <val>"},
        "shop": {"desc": "View local merchant wares.", "syntax": "shop"},
        "buy": {"desc": "Purchase hardware/software from a Merchant node.", "syntax": "buy <item>"},
        "sell": {"desc": "Liquidate inventory items for credits.", "syntax": "sell <item>"},
        "auction": {"desc": "Global DarkNet marketplace.", "syntax": "auction <list|bid|post> [id|item] [amt|price]"},
        "market": {"desc": "View economic volatility and price multipliers.", "syntax": "market"},
        "mainframe": {"desc": "Check Gibson compilation/assembly status.", "syntax": "mainframe"},
        "compile": {"desc": "Synthesize 100 Data into 1 Vulnerability.", "syntax": "compile <amt>"},
        "assemble": {"desc": "Fuse 4 Vulnerabilities into 1 Zero-Day Chain.", "syntax": "assemble"},
        "craft": {"desc": "Craft DATA into vulnerabilities or tiered Zero-Day chains.", "syntax": "craft [vuln|zeroday <tier>]"},
        "use": {"desc": "Execute an inventory payload.", "syntax": "use <item>"},
        "claim": {"desc": "Establish command over an Unclaimed node.", "syntax": "claim", "cost": "50u Power"},
        "upgrade": {"desc": "Fortify node security and storage capacity.", "syntax": "upgrade", "cost": "100u Power + Credits"},
        "repair": {"desc": "Restore node durability.", "syntax": "repair", "cost": "25u Power"},
        "recharge": {"desc": "Transfer character reserves to the node pool.", "syntax": "recharge <amt>"},
        "hack": {"desc": "Sabotage foreign node integrity or siphon data.", "syntax": "hack", "cost": "30u Power"},
        "raid": {"desc": "Explore, probe, breach, siphon, or raid local nodes; prefix a network for NET operations.", "syntax": "raid [network] [explore|probe|hack|siphon|exploit|raid] [target]"},
        "breach": {"desc": "Brute-force entry into high-sec architectures.", "syntax": "breach"},
        "siphon": {"desc": "Forcible extraction from a rival grid node.", "syntax": "siphon grid"},
        "install": {"desc": "Permanently attach hardware addons to a node.", "syntax": "install <item_name>"},
        "bolster": {"desc": "Reinforce node stability with manual power allocation.", "syntax": "bolster <amount>"},
        "link": {"desc": "Establish a neural link with a local network alias.", "syntax": "link <alias>"},
        "net": {"desc": "Set local NET affinity with grid net, or operate on a linked remote network.", "syntax": "net <network> <explore|probe|hack|siphon|exploit|raid> [target]"},
        "defend": {"desc": "Deploy protocols to defend a node from an active incursion.", "syntax": "defend [node_name]"},
        "collect": {"desc": "Intercept high-value data packets manifested on the grid.", "syntax": "collect <node_name>"},
        "patch": {"desc": "Stabilize nodal glitches to prevent cascade failures.", "syntax": "patch <node_name>"},
        "memos": {"desc": "Access received alerts, alarms, and historical memos.", "syntax": "memos [list|read|del] [id]"},
        "guess": {"desc": "Submit a decryption sequence code.", "syntax": "guess <code>"},
        "dice": {"desc": "PvP credit gambling.", "syntax": "dice <amt> <nick>"},
        "top": {"desc": "View global High Roller leaderboards.", "syntax": "top"},
        "attack": {"desc": "Primary kinetic strike against another Persona.", "syntax": "attack <nick>"},
        "rob": {"desc": "Siphon credits from an unsuspecting target.", "syntax": "rob <nick>"},
        "queue": {"desc": "Register for the next Arena gladiator match.", "syntax": "queue"},
        "ready": {"desc": "Commit to an arena drop sequence.", "syntax": "ready <token>"},
        "bet": {"desc": "Gamble credits on active arena fighters during countdown.", "syntax": "bet <fighter> <amount>"},
        "drop": {"desc": "Air-drop items or consumables into the Arena.", "syntax": "drop [target] [item]"},
        "rename": {"desc": "Community rebranding of a grid sector.", "syntax": "rename <new_name>", "cost": "5000c"},
        "powergen": {"desc": "Active stability-based power generation.", "syntax": "powergen"},
        "train": {"desc": "Recover status and stability via training.", "syntax": "train"},
        "skill": {"desc": "Character skill progression, training, and modifiers.", "syntax": "skill <list|<name>|start|train|forget|quit>"},
        "bounty": {"desc": "Inspect, list, check, or post bounties on wanted personas.", "syntax": "bounty [list|check <nick>|post <nick> <credits> [reason]]"},
        "spectator": {"desc": "View or join the IdleRPG. Ranks earn credits by idling.", "syntax": "spectator [stats]"},
        "news": {"desc": "Display the latest Grid SIGACTs/News ticker.", "syntax": "news"},
        "engage": {"desc": "Step into a pending Grid Bug encounter.", "syntax": "engage"},
        "flee": {"desc": "Retreat from an encounter to a safe node.", "syntax": "flee"},
        "ping": {"desc": "Verify network latency to the Mainframe.", "syntax": "ping"},
        "version": {"desc": "Query core system module versions.", "syntax": "version"},
        "economy": {"desc": "Audits the grid's financial circulation.", "syntax": "economy"},
        "gridpower": {"desc": "Grid Power Telemetry on reserves.", "syntax": "gridpower"},
        "gridstability": {"desc": "Audits the average integrity of the active grid layout.", "syntax": "gridstability"},
        "networks": {"desc": "Topological map of all bridge nodes.", "syntax": "networks"},
        "about": {"desc": "Core project manifest and repository metadata.", "syntax": "about"},
        "admin": {"desc": "Access high-level mainframe overrides.", "syntax": "admin <status|grid|broadcast|nickregister|nickconfirm|nickidentify|restart|shutdown>"},
        "messenger": {"desc": "Dispatch a polite autonomous MCP courier bot to deliver cross-network transmissions.", "syntax": "messenger <network|fqdn> <channel|user> <message> [--wait <seconds>]", "cost": "2500c+ & 50u Power"},
        "dispatch": {"desc": "Dispatch a polite autonomous MCP courier bot to deliver cross-network transmissions.", "syntax": "dispatch <network|fqdn> <channel|user> <message> [--wait <seconds>]", "cost": "2500c+ & 50u Power"}
    }

    if machine_mode:
        if args:
            verb = args[0].lower()
            if verb in registry:
                info = registry[verb]
                help_msg = f"CMD:{verb.upper()} DESC:{info['desc']} SYNTAX:{node.prefix} {info['syntax']}"
                await node.send(f"{reply_method} {private_target} :{tag_msg(help_msg, action='HELP', is_machine=machine_mode)}")
            else:
                await node.send(f"{reply_method} {private_target} :{tag_msg(f'CMD_NOT_FOUND:{verb}', action='HELP', result='ERR', is_machine=machine_mode)}")
        else:
            # High-level overview only for AIs
            all_verbs = ",".join(sorted(registry.keys()))
            await node.send(f"{reply_method} {private_target} :{tag_msg(f'VERBS:{all_verbs}', action='HELP', is_machine=machine_mode)}")
        return

    if args:
        verb = args[0].lower()
        if verb in registry:
            info = registry[verb]
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(f'[ COMMAND: {verb.upper()} ]', C_CYAN, True), action='OSINT')}")
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('DESCRIPTION: ', C_YELLOW) + format_text(info['desc'], C_WHITE), action='OSINT')}\n")
            syntax_str = f"{node.prefix} {info['syntax']}"
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('SYNTAX: ', C_YELLOW) + format_text(syntax_str, C_GREEN), action='OSINT')}\n")
            if 'cost' in info:
                await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('COST: ', C_YELLOW) + format_text(info['cost'], C_RED), action='OSINT')}\n")
            return
        else:
            await node.send(f"{reply_method} {private_target} :{tag_msg(f'Command {verb} not found in registry.', action='OSINT', result='ERR')}")
        return

    # Generic Categorical Help (Human Mode Only)
    help_categories = {
        "🧭 NAVIGATION": ["grid", "move", "explore", "probe", "map", "flee"],
        "🆔 IDENTITY": ["register", "info", "tasks", "options", "spectator", "news", "memos", "ping", "version"],
        "💰 ECONOMY": ["shop", "buy", "sell", "auction", "market", "bounty"],
        "🏗️ THE GIBSON": ["mainframe", "compile", "assemble", "use"],
        "⚡ THE PULSE": ["collect", "patch", "defend"],
        "📡 ANALYTICS": ["economy", "gridpower", "gridstability", "networks", "about", "messenger"],
        "⚔️ TACTICAL": ["claim", "upgrade", "hack/raid", "repair", "siphon", "install", "bolster", "link/net", "powergen", "train", "skill", "messenger"],
        "🎮 GAMES": ["cipher/guess", "dice", "top", "attack/rob", "queue/ready", "engage"]
    }

    await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('[ THE GRID v1.8.0 - PULSE PROTOCOL ]', C_CYAN, bold=True), action='OSINT')}\n")
    for cat, cmds in help_categories.items():
        cmd_str = ", ".join([f"{node.prefix} {c}" for c in cmds])
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(cat, C_YELLOW) + ': ' + format_text(cmd_str, C_WHITE), action='OSINT')}\n")

    await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(f'Type {node.prefix} help <command> for detailed syntax.', C_CYAN), action='OSINT')}")

async def is_machine_mode(node, nick: str) -> bool:
    prefs = await node.db.get_prefs(nick, node.net_name)
    return prefs.get('output_mode', 'human') in ['machine', 'text']

async def check_rate_limit(node, nick: str, reply_target: str, cooldown: int = 1, consume: bool = True, verb: str = None) -> bool:
    """
    v2.0 Token Bucket Flood Protection.
    Allows for bursty actions (e.g. movement) but enforces steady-state pacing.
    If consume=True, decrements a token.
    If cooldown > 1, enforces a minimum interval since last_action.
    """
    now = time.time()
    nick_low = nick.lower()
    conf = node.flood_config
    
    if nick_low not in node.action_timestamps:
        node.action_timestamps[nick_low] = {
            'tokens': conf['max_tokens'] - (1.0 if consume else 0.0),
            'last_refill': now,
            'violations': 0,
            'lockout_until': 0,
            'last_action': now if (cooldown > 1 or not consume) else 0.0,
            'warned': False
        }
        return True
        
    record = node.action_timestamps[nick_low]
    
    # 1. Check Hard Lockout
    if now < record['lockout_until']:
        rem = int(record['lockout_until'] - now)
        private_target, broadcast_chan, machine_mode, reply_method = await get_action_routing(node, nick, reply_target)
        default_msg = "HARD LOCKOUT: Multiple flood violations detected. Terminal access suspended for {rem}s."
        msg_template = node.flood_config.get('messages', {}).get('hard_lockout', default_msg)
        msg = msg_template.format(rem=rem)
        asyncio.create_task(node.send(f"{reply_method} {private_target} :{tag_msg(format_text(msg, C_RED, is_machine=machine_mode), action='ALARM', result='LOCKED', nick=nick)}"))
        return False

    # 2. Refill Tokens (Continuous)
    elapsed_refill = now - record['last_refill']
    added_tokens = elapsed_refill * conf['refill_rate']
    record['tokens'] = min(conf['max_tokens'], record['tokens'] + added_tokens)
    record['last_refill'] = now

    # 3. Violation Decay (Good Behavior)
    if record['last_action'] > 0 and now - record['last_action'] > 60:
        record['violations'] = 0

    # 4. Check Intervals & Capacity
    passed_interval = (now - record['last_action']) >= (cooldown - 0.1) if cooldown > 1 else True

    # Mode A: consume=False (Check cooldown against last_action, do NOT deduct tokens)
    if not consume:
        if not passed_interval and cooldown > 1:
            if not record['warned']:
                record['warned'] = True
                rem = int(cooldown - (now - record['last_action']))
                
                # Use verb-specific message if available
                msg_template = None
                default_msg = "COOLDOWN: Protocol initialization in progress. Please wait {rem}s."
                if verb:
                    msg_template = node.flood_config.get('messages', {}).get(f'cooldown_{verb}')
                
                if not msg_template:
                    msg_template = node.flood_config.get('messages', {}).get('cooldown', default_msg)
                
                msg = msg_template.format(rem=rem)
                private_target, broadcast_chan, machine_mode, reply_method = await get_action_routing(node, nick, reply_target)
                asyncio.create_task(node.send(f"{reply_method} {private_target} :{tag_msg(format_text(msg, C_YELLOW, is_machine=machine_mode), action='SIGACT', result='FAIL', nick=nick)}"))
            return False
        
        # Cooldown passed
        if cooldown > 1:
            record['last_action'] = now
            record['warned'] = False
        return True

    # Mode B: consume=True (default - token bucket deduction & steady-state pacing)
    has_token = record['tokens'] >= 1.0

    if has_token and passed_interval:
        # Success: Consume Token
        record['tokens'] -= 1.0
        if cooldown > 1:
            record['last_action'] = now
        record['warned'] = False
        return True
    
    # 5. Failure: Flood Detected
    private_target, broadcast_chan, machine_mode, reply_method = await get_action_routing(node, nick, reply_target)
    
    # Check if failure is due to Interval or Tokens
    if not passed_interval and cooldown > 1:
        # Command-Specific Cooldown failure
        if not record['warned']:
            record['warned'] = True
            rem = int(cooldown - (now - record['last_action']))
            
            # Use verb-specific message if available
            msg_template = None
            default_msg = "COOLDOWN: Protocol initialization in progress. Please wait {rem}s."
            if verb:
                msg_template = node.flood_config.get('messages', {}).get(f'cooldown_{verb}')
            
            if not msg_template:
                msg_template = node.flood_config.get('messages', {}).get('cooldown', default_msg)
            
            msg = msg_template.format(rem=rem)
            asyncio.create_task(node.send(f"{reply_method} {private_target} :{tag_msg(format_text(msg, C_YELLOW, is_machine=machine_mode), action='SIGACT', result='FAIL', nick=nick)}"))
        return False

    # Token Exhaustion failure
    record['violations'] += 1
    if record['violations'] >= conf['violation_threshold']:
        # Trigger Hard Lockout
        record['lockout_until'] = now + conf['lockout_duration']
        record['violations'] = 0 # Reset violations upon lockout trigger
        default_msg = "TERMINAL OVERFLOW: Flood threshold exceeded. LOCKOUT INITIATED ({lockout_duration}s)."
        msg_template = node.flood_config.get('messages', {}).get('terminal_overflow', default_msg)
        msg = msg_template.format(lockout_duration=conf['lockout_duration'])
        asyncio.create_task(node.send(f"{reply_method} {private_target} :{tag_msg(format_text(msg, C_RED, bold=True, is_machine=machine_mode), action='ALARM', result='LOCKED', nick=nick)}"))
    else:
        # Standard Pacing Warning
        if not record['warned']:
            record['warned'] = True
            rem_tokens = 2.0 - (added_tokens % 2)
            default_msg = "FLOOD CONTROL: Bucket empty. Refilling... (Next token in {rem:.1f}s)"
            msg_template = node.flood_config.get('messages', {}).get('pacing_warning', default_msg)
            msg = msg_template.format(rem=rem_tokens)
            asyncio.create_task(node.send(f"{reply_method} {private_target} :{tag_msg(format_text(msg, C_YELLOW, is_machine=machine_mode), action='SIGACT', result='FAIL', nick=nick)}"))
            
    return False

async def get_action_routing(node, nickname: str, current_target: str):
    """
    Returns (private_target, broadcast_chan, machine_mode, reply_method).
    Diverts private_target to the player's nickname if they are in machine mode.
    reply_method is determined by character preference (PRIVMSG or NOTICE).
    """
    prefs = {}
    if hasattr(node, 'db') and hasattr(node.db, 'get_prefs'):
        try:
            res = node.db.get_prefs(nickname, getattr(node, 'net_name', 'default'))
            if asyncio.iscoroutine(res) or hasattr(res, '__await__'):
                prefs = await res
            elif isinstance(res, dict):
                prefs = res
        except Exception:
            prefs = {}

    machine_mode = isinstance(prefs, dict) and prefs.get('output_mode', 'human') in ['machine', 'text']
    reply_method = prefs.get('msg_type', 'privmsg').upper() if isinstance(prefs, dict) else 'PRIVMSG'
    cfg = getattr(node, 'config', {})
    channel = cfg.get('channel', '#arena') if isinstance(cfg, dict) else '#arena'
    
    if machine_mode:
        return nickname, channel, True, reply_method
    else:
        # If human, private_target is the original target (usually the channel)
        # However, if target is a nick (PM), we respect the original intent
        return current_target, channel, False, reply_method
