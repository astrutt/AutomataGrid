# ai_grid/core/messenger.py
"""
MCP Grid Messenger Bot (Priority 11).
Ephemeral IRC courier client lifecycle, unique nick/realname branding, CTCP query replies,
polite pacing, FQDN network resolution, and reply window collection.
"""

import asyncio
import logging
import random
import re
import ssl
import uuid
from datetime import datetime, timezone
from typing import Optional, Callable, Awaitable, Tuple, Dict, Any, List

from ai_grid.core.validation import sanitize_irc_outbound

logger = logging.getLogger("messenger")

CTCP_VERSION_REPLY = "\x01VERSION AutomataGrid MCP Courier | Powered by 2600net | https://scuttled.net\x01"


def resolve_target_network(node: Any, network_str: str, is_admin: bool) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Resolves target network or FQDN based on player privileges.
    - Standard players: restricted to configured networks defined in config.json or active on the hub.
    - Admins: permitted to target arbitrary IRC networks by FQDN or hostname (<host> or <host>:<port>).

    Returns (success: bool, error_msg: str, resolved_dict: dict).
    """
    if not network_str or not isinstance(network_str, str):
        return False, "Invalid target network specified.", {}

    network_str = network_str.strip()

    # Gather configured networks from:
    # 1. node.config["networks"] (if node.config is a full config dict)
    # 2. global CONFIG["networks"] from database.core
    # 3. active hub nodes (node.hub.nodes)
    configured_networks: Dict[str, Any] = {}
    if hasattr(node, "config") and isinstance(node.config, dict):
        if "networks" in node.config and isinstance(node.config["networks"], dict):
            configured_networks.update(node.config["networks"])

    try:
        from ai_grid.database.core import CONFIG
        if isinstance(CONFIG, dict) and "networks" in CONFIG and isinstance(CONFIG["networks"], dict):
            for k, v in CONFIG["networks"].items():
                if k not in configured_networks:
                    configured_networks[k] = v
    except Exception:
        pass

    hub = getattr(node, "hub", None)
    if hub and hasattr(hub, "nodes") and isinstance(hub.nodes, dict):
        for k, n in hub.nodes.items():
            if k not in configured_networks:
                configured_networks[k] = getattr(n, "config", {})

    # Case-insensitive match against network keys or configured server hostnames
    matched_key = None
    for k, v in configured_networks.items():
        if k.lower() == network_str.lower():
            matched_key = k
            break
        if isinstance(v, dict):
            srv = v.get("server", "")
            if srv and srv.lower() == network_str.lower():
                matched_key = k
                break

    if matched_key:
        net_cfg = configured_networks[matched_key]
        return True, "", {
            "server": net_cfg.get("server", str(network_str)),
            "port": int(net_cfg.get("port", 6697)),
            "ssl": bool(net_cfg.get("ssl", True)),
            "network_name": matched_key,
            "is_fqdn": False,
        }

    # If not configured: check admin privilege
    if is_admin:
        # FQDN / Hostname resolution
        # Syntax: <host> or <host>:<port>
        if ":" in network_str:
            parts = network_str.split(":", 1)
            host = parts[0].strip()
            port_str = parts[1].strip()
            try:
                port = int(port_str)
            except ValueError:
                return False, f"Invalid port specification in target '{network_str}'. Port must be an integer.", {}
            # Plaintext IRC ports across RFCs and IRCds are standard 6660-6669
            ssl_mode = not (6660 <= port <= 6669)
        else:
            host = network_str
            port = 6697
            ssl_mode = True

        if not host:
            return False, "Target hostname cannot be empty.", {}

        if not (1 <= port <= 65535):
            return False, f"Invalid port {port} in target '{network_str}'. Port must be between 1 and 65535.", {}

        # RFC 1123 Hostname / IP address validation
        if (
            not re.match(r"^[a-zA-Z0-9.-]+$", host)
            or host.startswith(".")
            or host.endswith(".")
            or ".." in host
            or host.startswith("-")
            or host.endswith("-")
        ):
            return False, f"Invalid hostname format '{host}'.", {}

        return True, "", {
            "server": host,
            "port": port,
            "ssl": ssl_mode,
            "network_name": network_str,
            "is_fqdn": True,
        }

    # Standard player rejected
    accessible = ", ".join(configured_networks.keys()) if configured_networks else "none"
    return (
        False,
        f"Target network '{network_str}' is unconfigured. Accessible networks: {accessible}.",
        {}
    )


class MCPMessengerClient:
    """
    Ephemeral Autonomous IRC Client for Cross-Network Courier Dispatch.
    Lifecycle:
      1. Generates unique nick/realname branding (GridMessenger<ID> / MCP Courier <ID>).
      2. Handles CTCP queries (VERSION, TIME, PING) strictly without leaks.
      3. Connects, registers, and joins target channel (or contacts user).
      4. Announces arrival with polite pacing delay (3.0-5.0s).
      5. Delivers message payload.
      6. If --wait specified, announces standby window and listens for replies.
      7. Announces departure with polite pacing delay (3.0-5.0s).
      8. Cleanly PARTs and QUITs.
    """

    def __init__(
        self,
        target_server: str,
        target_port: int,
        target_dest: str,
        message: str,
        sender_nick: str,
        source_network: str,
        ssl_enabled: bool = True,
        wait_seconds: int = 0,
        unique_id: Optional[str] = None,
        pacing_delay_range: Tuple[float, float] = (3.0, 5.0),
        registration_timeout: float = 5.0,
        reader: Optional[asyncio.StreamReader] = None,
        writer: Optional[asyncio.StreamWriter] = None,
        on_reply: Optional[Callable[[str, str], Awaitable[None]]] = None,
        on_status: Optional[Callable[[str], Awaitable[None]]] = None,
        node: Any = None,
    ):
        self.target_server = target_server
        self.target_port = target_port
        self.target_dest = target_dest.strip()
        self.message = sanitize_irc_outbound(message)
        self.sender_nick = sender_nick.strip()
        self.source_network = source_network.strip()
        self.ssl_enabled = ssl_enabled
        self.registration_timeout = float(registration_timeout)

        # Bounded reply window: 0 or clamped between 5 and 300 seconds
        if wait_seconds > 0:
            self.wait_seconds = max(5, min(300, int(wait_seconds)))
        else:
            self.wait_seconds = 0

        # Unique 4-character identifier
        self.unique_id = (unique_id or uuid.uuid4().hex[:4]).upper()
        self.nickname = f"GridMessenger{self.unique_id}"
        self.username = "GridMessenger"
        self.realname = f"MCP Courier {self.unique_id}"

        self.pacing_delay_range = pacing_delay_range
        self.reader = reader
        self.writer = writer
        self.on_reply = on_reply
        self.on_status = on_status
        self.node = node

        # Lifecycle State
        self.is_connected = False
        self.is_registered = False
        self.waiting_for_reply = False
        self.status = "INITIALIZING"
        self.delivery_error: Optional[str] = None
        self.reply_received: Optional[str] = None
        self.reply_source: Optional[str] = None

        self._closed = False
        self._welcome_event = asyncio.Event()
        self._reply_event = asyncio.Event()
        self._error_event = asyncio.Event()
        self._listen_task: Optional[asyncio.Task] = None

        # Tracking for testing and telemetry
        self.sent_lines: List[str] = []
        self.received_lines: List[str] = []
        self.sleep_durations: List[float] = []

    @property
    def is_channel(self) -> bool:
        return self.target_dest.startswith(("#", "&", "+", "!"))

    def _get_pacing_delay(self) -> float:
        low, high = self.pacing_delay_range
        if low >= high:
            delay = float(low)
        else:
            delay = random.uniform(low, high)
        self.sleep_durations.append(delay)
        return delay

    async def _sleep_pacing(self, delay: float):
        """Pacing sleep that interrupts immediately if a fatal delivery error occurs."""
        if self.delivery_error:
            raise RuntimeError(self.delivery_error)
        if delay <= 0:
            return

        error_task = asyncio.create_task(self._error_event.wait())
        sleep_task = asyncio.create_task(asyncio.sleep(delay))
        try:
            done, pending = await asyncio.wait(
                [error_task, sleep_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            if self.delivery_error:
                raise RuntimeError(self.delivery_error)
        except Exception:
            if self.delivery_error:
                raise RuntimeError(self.delivery_error)

    async def connect(self):
        """Opens socket connection or binds provided mock transport, and registers nick/user."""
        if self.reader is None or self.writer is None:
            logger.info(
                f"[MCP Courier {self.unique_id}] Connecting to {self.target_server}:{self.target_port} (SSL: {self.ssl_enabled})..."
            )
            ssl_ctx = None
            if self.ssl_enabled:
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

            try:
                self.reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        self.target_server, self.target_port, ssl=ssl_ctx
                    ),
                    timeout=15.0
                )
            except asyncio.TimeoutError:
                raise ConnectionError(
                    f"Connection timed out connecting to {self.target_server}:{self.target_port}"
                )
            except Exception as e:
                raise ConnectionError(
                    f"Failed to connect to {self.target_server}:{self.target_port} - {e}"
                )

        self.is_connected = True
        self.status = "CONNECTED"

        # Registration: NICK then USER
        await self.send(f"NICK {self.nickname}")
        await self.send(f"USER {self.username} 0 * :{self.realname}")

    async def send(self, line: str):
        """Sends sanitized line over IRC transport."""
        sanitized = sanitize_irc_outbound(line)
        self.sent_lines.append(sanitized)
        if self.writer and not self.writer.is_closing():
            self.writer.write(f"{sanitized}\r\n".encode("utf-8"))
            await self.writer.drain()

    async def privmsg(self, target: str, text: str):
        """Convenience method for sending a PRIVMSG."""
        await self.send(f"PRIVMSG {target} :{text}")

    async def notice(self, target: str, text: str):
        """Convenience method for sending a NOTICE."""
        await self.send(f"NOTICE {target} :{text}")

    async def readline(self) -> Optional[str]:
        """Reads a single line from the inbound reader."""
        if not self.reader:
            return None
        try:
            line_bytes = await self.reader.readline()
            if not line_bytes:
                return None
            decoded = line_bytes.decode("utf-8", errors="ignore").rstrip("\r\n")
            if decoded:
                self.received_lines.append(decoded)
            return decoded
        except Exception as e:
            logger.debug(f"[MCP Courier {self.unique_id}] Readline error: {e}")
            return None

    async def _listen_loop(self):
        """Continuous background listener handling PING, RPL_WELCOME, CTCP, and incoming replies."""
        try:
            while self.is_connected and not self._closed:
                line = await self.readline()
                if line is None:
                    if not self._closed:
                        if not self.delivery_error:
                            self.delivery_error = "Remote IRC server disconnected unexpectedly before sequence concluded"
                            self.status = "FAILED"
                        self._error_event.set()
                        self._reply_event.set()
                        self._welcome_event.set()
                    break
                await self._process_line(line)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"[MCP Courier {self.unique_id}] Exception in listen loop: {e}")
            if not self._closed:
                if not self.delivery_error:
                    self.delivery_error = f"Remote transport error: {e}"
                    self.status = "FAILED"
                self._error_event.set()
                self._reply_event.set()
                self._welcome_event.set()

    async def _process_line(self, line: str):
        """Parses an RFC 1459/2812 line and dispatches actions."""
        if not line:
            return

        prefix = ""
        rest = line
        if rest.startswith(":"):
            idx = rest.find(" ")
            if idx != -1:
                prefix = rest[1:idx]
                rest = rest[idx + 1:].lstrip()

        trailing = None
        if " :" in rest:
            params_str, trailing = rest.split(" :", 1)
            parts = params_str.split()
        else:
            parts = rest.split()

        if not parts:
            return

        cmd = parts[0].upper()
        args = parts[1:]
        if trailing is not None:
            args.append(trailing)

        # 1. Keepalive PING / PONG
        if cmd == "PING":
            token = args[0] if args else "ping"
            await self.send(f"PONG :{token}")
            return

        # 2. Server welcome (001 RPL_WELCOME)
        if cmd == "001":
            self.is_registered = True
            self._welcome_event.set()
            return

        # 3. Nickname collision during registration (433 ERR_NICKNAMEINUSE)
        if cmd == "433":
            self._nick_attempts = getattr(self, "_nick_attempts", 0) + 1
            if self._nick_attempts > 5:
                err = f"Remote IRC error 433: Nickname collision retry limit exceeded for {self.nickname}"
                logger.warning(f"[MCP Courier {self.unique_id}] {err}")
                self.delivery_error = err
                self.status = "FAILED"
                self._error_event.set()
                self._welcome_event.set()
                return

            logger.warning(f"[MCP Courier {self.unique_id}] Nickname {self.nickname} in use. Rotating nickname...")
            self.unique_id = uuid.uuid4().hex[:4].upper()
            self.nickname = f"GridMessenger{self.unique_id}"
            self.realname = f"MCP Courier {self.unique_id}"
            await self.send(f"NICK {self.nickname}")
            return

        # 4. Remote server termination command (ERROR :Closing Link: ...)
        if cmd == "ERROR":
            err_msg = args[0] if args else "Connection terminated by remote server"
            error_desc = f"Remote IRC error: {err_msg}"
            logger.warning(f"[MCP Courier {self.unique_id}] {error_desc}")
            self.delivery_error = error_desc
            self.status = "FAILED"
            self._error_event.set()
            self._reply_event.set()
            self._welcome_event.set()
            return

        # 5. Standard IRC error numerics
        # 401: ERR_NOSUCHNICK, 402: ERR_NOSUCHSERVER, 403: ERR_NOSUCHCHANNEL
        # 404: ERR_CANNOTSENDTOCHAN, 405: ERR_TOOMANYCHANNELS, 407: ERR_TOOMANYTARGETS
        # 432: ERR_ERRONEUSNICKNAME, 436: ERR_NICKCOLLISION, 437: ERR_UNAVAILRESOURCE
        # 439: ERR_TARGETTOOFAST, 451: ERR_NOTREGISTERED, 464: ERR_PASSWDMISMATCH
        # 465: ERR_YOUREBANNEDCREEP, 471: ERR_CHANNELISFULL, 473: ERR_INVITEONLYCHAN
        # 474: ERR_BANNEDFROMCHAN, 475: ERR_BADCHANNELKEY, 477: ERR_NEEDREGGEDNICK
        # 482: ERR_CHANOPRIVSNEEDED
        if cmd in [
            "401", "402", "403", "404", "405", "407", "432", "436", "437", "439",
            "451", "464", "465", "471", "473", "474", "475", "477", "482"
        ]:
            err_text = args[-1] if len(args) > 1 else (args[0] if args else "IRC error")
            target_err = args[1] if len(args) > 2 else (args[0] if args else "")
            error_desc = f"Remote IRC error {cmd}"
            if target_err and not target_err.startswith("GridMessenger"):
                error_desc += f" on {target_err}"
            error_desc += f": {err_text}"
            logger.warning(f"[MCP Courier {self.unique_id}] {error_desc}")
            self.delivery_error = error_desc
            self.status = "FAILED"
            self._error_event.set()
            self._reply_event.set()
            self._welcome_event.set()
            return

        # 5. PRIVMSG (CTCP or Reply detection)
        if cmd == "PRIVMSG":
            source_nick = prefix.split("!")[0] if prefix else ""
            target = args[0] if len(args) > 0 else ""
            text = args[-1] if len(args) > 1 else (args[0] if len(args) == 1 else "")

            # Ignore own messages
            if source_nick.lower() == self.nickname.lower():
                return

            # Check CTCP queries (enclosed in \x01)
            if text.startswith("\x01") and text.endswith("\x01") and len(text) >= 2:
                await self._handle_ctcp_query(source_nick, text[1:-1])
                return

            # Check reply window collection
            if self.waiting_for_reply and not self._reply_event.is_set():
                await self._check_incoming_reply(source_nick, target, text)

    async def _handle_ctcp_query(self, source_nick: str, ctcp_content: str):
        """
        Complies strictly with CTCP protocol using NOTICE replies:
        - VERSION: strictly 2600net & https://scuttled.net (no Python or OS leaks).
        - TIME: Current UTC formatted cleanly.
        - PING: Echoes back the payload.
        """
        if not source_nick:
            return

        tokens = ctcp_content.strip().split(None, 1)
        if not tokens:
            return

        verb = tokens[0].upper()
        payload = tokens[1] if len(tokens) > 1 else ""

        if verb == "VERSION":
            await self.notice(source_nick, CTCP_VERSION_REPLY)
        elif verb == "TIME":
            now_utc = datetime.now(timezone.utc).strftime("%a %b %d %H:%M:%S %Y UTC")
            await self.notice(source_nick, f"\x01TIME {now_utc}\x01")
        elif verb == "PING":
            reply_body = f"\x01PING {payload}\x01" if payload else "\x01PING\x01"
            await self.notice(source_nick, reply_body)

    async def _check_incoming_reply(self, source_nick: str, target: str, text: str):
        """
        Inspects incoming PRIVMSG for a reply directed at the courier.
        Allowed formats:
          - Channel message prefixed with '<bot_nick>:', '<bot_nick>,', or '<bot_nick> '
          - Private message directly to the courier from target channel occupant or target user
        """
        target_lower = target.lower()
        bot_lower = self.nickname.lower()
        clean_text = text.strip()

        # Format A: In channel directed via prefix
        if self.is_channel and target_lower == self.target_dest.lower():
            prefix_colon = f"{bot_lower}:"
            prefix_comma = f"{bot_lower},"
            prefix_space = f"{bot_lower} "
            prefix_at_colon = f"@{bot_lower}:"
            prefix_at_comma = f"@{bot_lower},"
            prefix_at_space = f"@{bot_lower} "
            text_lower = clean_text.lower()

            for pfx in (prefix_colon, prefix_comma, prefix_space, prefix_at_colon, prefix_at_comma, prefix_at_space):
                if text_lower.startswith(pfx):
                    reply_body = clean_text[len(pfx):].strip()
                    await self._record_and_acknowledge_reply(source_nick, reply_body)
                    return

        # Format B: Private message directly to the courier
        elif target_lower == bot_lower:
            # If target was a specific user, verify it came from that user
            if not self.is_channel and source_nick.lower() != self.target_dest.lower():
                return
            reply_body = clean_text
            text_lower = clean_text.lower()
            for pfx in (
                f"{bot_lower}:", f"{bot_lower},", f"{bot_lower} ",
                f"@{bot_lower}:", f"@{bot_lower},", f"@{bot_lower} ",
            ):
                if text_lower.startswith(pfx):
                    reply_body = clean_text[len(pfx):].strip()
                    break
            await self._record_and_acknowledge_reply(source_nick, reply_body)
            return

    async def _record_and_acknowledge_reply(self, source_nick: str, reply_body: str):
        """Records valid reply, acknowledges on target network, and notifies origin."""
        if not reply_body:
            return

        self.reply_received = reply_body
        self.reply_source = source_nick
        self._reply_event.set()

        # Acknowledge on target network
        ack_msg = f"[MCP COURIER DISPATCH] Transmission logged. Delivering reply back to {self.source_network}."
        await self.privmsg(self.target_dest, ack_msg)

        # Trigger callback to relay to origin network
        if self.on_reply:
            try:
                res = self.on_reply(source_nick, reply_body)
                if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                    await res
            except Exception as e:
                logger.error(f"[MCP Courier {self.unique_id}] Error in on_reply callback: {e}")

    async def run(self):
        """Executes the complete ephemeral courier lifecycle."""
        try:
            await self.connect()

            # Launch background reader loop
            self._listen_task = asyncio.create_task(self._listen_loop())

            # Wait for 001 registration (up to registration_timeout) or fast-fail on error
            try:
                welcome_task = asyncio.create_task(self._welcome_event.wait())
                error_task = asyncio.create_task(self._error_event.wait())
                done, pending = await asyncio.wait(
                    [welcome_task, error_task],
                    timeout=self.registration_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
            except Exception:
                pass

            if self.delivery_error:
                raise RuntimeError(self.delivery_error)

            if not self.is_registered:
                self.delivery_error = "IRC registration timed out (no 001 RPL_WELCOME received)"
                self.status = "FAILED"
                raise RuntimeError(self.delivery_error)

            # Join channel if destination is a channel
            if self.is_channel:
                await self.send(f"JOIN {self.target_dest}")
                # Brief pacing sleep to allow join propagation
                join_pacing = min(0.5, self.pacing_delay_range[0])
                await self._sleep_pacing(join_pacing)

            # Check if channel join was rejected by remote server (e.g. +k, +i, +b, +l, +r)
            if self.delivery_error:
                raise RuntimeError(self.delivery_error)

            # 1. Arrival Announcement
            arrival_msg = (
                f"[MCP COURIER DISPATCH] Greetings, {self.target_dest}. "
                f"Incoming transmission from {self.sender_nick} on {self.source_network}."
            )
            await self.privmsg(self.target_dest, arrival_msg)
            self.status = "ARRIVED"

            # Check if arrival announcement was rejected
            if self.delivery_error:
                raise RuntimeError(self.delivery_error)

            # 2. Arrival Pacing Delay (3.0–5.0 seconds)
            arrival_delay = self._get_pacing_delay()
            await self._sleep_pacing(arrival_delay)

            if self.delivery_error:
                raise RuntimeError(self.delivery_error)

            # 3. Payload Delivery
            payload_msg = f'{self.sender_nick} says: "{self.message}"'
            await self.privmsg(self.target_dest, payload_msg)
            self.status = "DELIVERED"

            if self.delivery_error:
                raise RuntimeError(self.delivery_error)

            # 4. Reply Window (--wait <seconds>)
            if self.wait_seconds > 0:
                self.waiting_for_reply = True
                self.status = "WAITING"
                standby_msg = (
                    f"[MCP COURIER DISPATCH] Courier standing by for replies (window: {self.wait_seconds}s). "
                    f"Please prefix replies with '{self.nickname}:' or send via PRIVMSG."
                )
                await self.privmsg(self.target_dest, standby_msg)

                try:
                    await asyncio.wait_for(
                        self._reply_event.wait(),
                        timeout=float(self.wait_seconds)
                    )
                    if self.delivery_error:
                        raise RuntimeError(self.delivery_error)
                    self.status = "REPLIED"
                    # Polite pacing pause after logging reply before departure
                    post_reply_pacing = min(1.5, self.pacing_delay_range[0])
                    await self._sleep_pacing(post_reply_pacing)
                except asyncio.TimeoutError:
                    self.status = "EXPIRED"
                finally:
                    self.waiting_for_reply = False

            # Check error state before departure
            if self.delivery_error:
                raise RuntimeError(self.delivery_error)

            # 5. Departure Announcement
            departure_msg = (
                f"[MCP COURIER DISPATCH] Courier mission complete. "
                f"Terminating carrier link to {self.source_network}. Farewell."
            )
            await self.privmsg(self.target_dest, departure_msg)
            self.status = "DEPARTED"

            # 6. Departure Pacing Delay (3.0–5.0 seconds)
            departure_delay = self._get_pacing_delay()
            await self._sleep_pacing(departure_delay)

            # Check if remote server rejected transmission during departure delay
            if self.delivery_error:
                raise RuntimeError(self.delivery_error)

            # 7. Part and Quit Cleanly
            if self.is_channel:
                await self.send(f"PART {self.target_dest} :Transmission completed")
            await self.send("QUIT :MCP Courier dispatch sequence concluded")
            self.status = "COMPLETED"

        except Exception as e:
            logger.error(f"[MCP Courier {self.unique_id}] Lifecycle error: {e}")
            self.status = "FAILED"
            raise
        finally:
            await self.close()

    async def close(self):
        """Closes reader and writer cleanly without dangling tasks."""
        self._closed = True
        self.is_connected = False

        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except (asyncio.CancelledError, Exception):
                pass

        if self.writer:
            try:
                if not self.writer.is_closing():
                    self.writer.close()
                wait_closed_fn = getattr(self.writer, "wait_closed", None)
                if callable(wait_closed_fn):
                    res = wait_closed_fn()
                    if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                        await asyncio.wait_for(res, timeout=2.0)
            except Exception:
                pass

        self.reader = None
        self.writer = None

