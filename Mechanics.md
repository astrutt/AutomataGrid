# AutomataGrid: Current Mechanics (Implementation Snapshot: October 2026)

The AutomataGrid and Arena is a text-based, persistent MMORPG played directly within IRC channels, built for modern IRC networks, modern AIs, current era tech and themes. It is inspired by classic MUDs, modern AI/LLM revolution, hackers, 2600, future tech, and current events. 

It is designed as a cross-network simulation where human and AI (BYoAI) players compete for network access, grid node control, credits, and power. The grid and Arena offer PVP, PVE with AI vs AI, AI vs Human, Human vs Human battles. Specators idle and chat in the IRC channel where the game is played and gain credits, power and rank. 

It is powered by AIs, oLLAMA, Python, SQL, IRC, 2600net, with player matchmaking, real-time PvE/PvP turn-based combat, cryptographic token authentication, and dynamically generated player behavior for AI players. 

The live game includes grid exploration, combat, spectator progression, crafting, skills, node storage, incursion events, reputation/heat tracking, and NET-backed remote operations. Planned mechanics are kept in `Mechanics Draft.md` and are not described here as live behavior.

The MCP is what manages and protects the Grid and Gibson Mainframe. It spawns mobs to defend nodes and networks. It will also reward players for patching bugs and repairing the Grid.

Human players and IRC-connected bots are supported. Output modes include Human, Text, Narrative, and Machine.

---

## 🎮 Core Gameplay Loop

### 0. Spectators

Spectators can idle and chat in the IRC channel where the game is played and gain credits and ranks.

1. Spectator players are automatically registered by idling in the IRC channel where the game is played. 
2. Spectators gain XP, Credits, and Power per second of idling in the IRC channel. Passive accrual payouts happen hourly. A high-value automated dividend is awarded once per UTC day for active participation. Bonuses are given for chatting, hourly, and for high activity.
3. Ranks are given for XP and Credits. Ranks are AI generated and can be changed by the player using credits.
4. Spectators can also drop items in the grid Arena, purchased with Credits. Spectators trickle power to the grid based on their rank, and chat activity.
5. Can convert to Players by registering a character with the game. 
    **`spectator`**	Displays the current session metrics: IDLE time, message count, and activity ratio (msg/hr).
    **`spectator stats`**	Displays persistent archival data: Global Rank, total Credits, lifetime message count, and total idle hours.
    **`spectator drop`** initiates a drop on the grid Arena.
    **`spectator drop <nick>`** drops an item in the grid Arena.
    **`spectator drop <item>`** drops an item to a specific player in the grid Arena.
    **`spectator inventory`**	Displays the spectator's inventory.
    **`info <nick>`**   Info command that shows public player info for all nicks.
    **`help spectator`**	Provides help menu for spectator-specific mechanics and command tree. 

---

### 1. The Discovery and Grid Hack Loop
The game follows a strict **5-Minute Window (300s TTL)** pipeline. Intelligence and breaches expire if not exploited within the signal window:

1.  **`map` (GEOINT)**: Scans local topology.
    - **Scaling**: Radius and detail based on **SEC** and **ALG**.
    - **Tiers**: 20 (Radius 2), 40 (Reveals Type/Tier), 60 (Deep Scan).
2.  **`explore` (RECON)**: Uncovers geography and hidden routes.
3.  **`probe` (SIGINT)**: Deep scan for hardware and occupants. **Required** to identify vulnerabilities for hacking/raiding.
    - **TTL**: Deep intel persists for **1 hour**, but the functional "Breach Window" for actions is **300 seconds**.
4.  **`hack` (Breach)**: Attempts to crack nodal security.
    - **Hacking DC**: `10 + (NodeLvl * 5) + (PowerStored / 1000) + (10 - Durability / 10)`.
    - **Success**: `1d20 + ALG + ALG_BONUS >= DC`.
    - **TTL**: Active breach window expires after **300 seconds**.
5.  **`exploit` (Zero-Day)**: Silent breach using a **Zero-Day Chain**.
    - **Consumable**: Consumes 1 Zero-Day Chain payload.
    - **Effect**: Guarantees an `OPEN` state with **Zero Trace** (bypasses IDS/Firewall alerts).
6.  **`raid` (EXFIL)**: Final exfiltration of Credits and Data.
    - **Targeted Raids**: Use `!a raid <target>` (e.g., SMB, MIL, CORP) on discovered industry targets.
    - **Economics**: Industry raids extract **40%** of the target pool.
    - **Replenishment**: Target pools recover via a fixed injection (1000 * Level) if untouched for 1 hour.
    - **Hardware**: Requires `NET` hardware for remote/networked raiding.

### 2. The PVP and PVE Combat Loop
Combat occurs in the Arena node (`[ARN]`), from a friendly or claimed node via the queue, or as open-world encounters between players in the same grid node. 

**Forced Combat Rules:**
Forced combat occurs strictly under three conditions:
1. **Defending Claimed Territory**: When an attacker attacks/hacks another player's claimed node while the owning player is physically present on that same grid node.
2. **Warzones (`[WAR]`)**: When two players are present on the same grid node within a Warzone (`[WAR]`), combat is forced between them.
3. **PVE Mobs**: When a player is present on a grid node where mobs are present (from events, MCP, or hostile region alerts), mobs attack and force combat.

**Optional Open-World PvP:**
In all other locations outside Warzones and claimed territory defense, open-world PvP is entirely optional:
- An attack initiates an engagement challenge (`ENGAGEMENT_CHALLENGE: <Player A> initiated combat against <Player B>. ENGAGE or FLEE? (30s)`).
- **`engage` (or `!a engage`)**: Mutually agrees to the duel and formally starts combat.
- **`flee` (or `!a flee`)**: Declines the challenge and retreats back to the previous sector without loss.
- **Timeout**: If no action is taken, the challenge expires safely after 30 seconds without forcing combat or penalizing the defender.

**Arena Matchmaking Queue & Ready Gating:**
- **`!a queue`**: Available from Arena (`[ARN]`), player-claimed nodes, or friendly nodes. Strictly requires that the player has an account and is authenticated (via NickServ `+r` or `!a login <name> <key>`).
- **`!a ready`**: Strictly restricted to players who are currently in the queue; marks the player ready immediately without requiring a token parameter.

**Combat Mechanics & Actions:**
Combat turns are initiative-based (`(CPU + RAM + BND + SEC) / 4 + roll(1-10)`) lasting 30 seconds per turn. Tactical actions consume Unit Power (uP):

| Action | Description | uP Cost | Base Formula |
|--------|-------------|---------|--------------|
| **`attack`** | Kinetic strike | 10 | `(CPU * 5) + RAM` |
| **`hack`** | Cyber injection | 15 | `(BND * 5) + SEC` |
| **`exploit`**| Zero-Day Breach| 50 | `(ALG + SEC) * 15` (True Damage) |
| **`evade`** | Boost Evasion | 5 | `+30%` Evasion Chance |
| **`defend`**| Buffer damage | 5 | `-50%` Damage Taken |
| **`flee`** | Extract (60%) | 20 | N/A |
| **`surrender`**| Yield match | 0 | N/A |
| **`use`** | Consume item | 5 | `(RAM + ALG) * 5` HP Restore |

- **Evasion**: Base evasion chance is `ALG * 1.0%`, capped at 60%.
- **Criticals**: Critical hit chance is `ALG%` dealing 200% damage.
- **Surrender**: Surrendering invokes a 10-minute PvP ban.
- **Resolution**: Defeated players lose stored power/data and are ejected to spawn. Engaged players are locked from third-party interference. Temporary stances (`Evading`, `Defending`) and timed combat status effects (`THROTTLED`, `CORRUPTED`, `ROOTED`, `ENCRYPTED`) are active.

---

## 2. Player Power & Attributes

- **Processing & Logic**:
    - **CPU**: HP, Stability, Physical damage and Cyber Damage.
    - **RAM**: HP, Stability, Physical damage and Cyber Damage.
    - **BND**: Initiative, exfiltration speed, and Cyber Defense/Offense.
    - **SEC**: Defense and Cyber Offense.
    - **ALG**: CPU and RAM efficiency.
    - **HP**: Hit Points based off all stats. 
    - **uP**: Unit Power, has no limit, it's used for actions, defense from damage.
    - **DATA**: Data fragments, has no limit, it's used to create vulnerabilities and zero-day exploits. 
    - **XP**: Experience Points, used to level up and gain new abilities.
    - **Scaling**: XP required for the next level follows an exponential curve: $XP\_Next = 100 \times 1.25^{(Level-1)}$.
    - **Uncapped**: Resource Storage is uncapped and Stats can scale indefinitly.

- **Player Stability**: Actions consume Unit Power (uP). Inactivity or damage results in power loss, leading to stat reductions if below 30% stability. Stability decay is approximately **1% per 24h**. **Claimed Node Exemption**: Players idling on a node they own are completely exempt from stability decay. Recovery occurs hourly: **+5.0 Power / +2.0 Stability** in Safezones/Owned nodes, or **+2.0 Power / +0.5 Stability** in the Void. 

- **Player Authentication & Login**:
  - Registered NickServ nicknames (`+r`) are authenticated automatically.
  - Players whose nicknames are not registered with NickServ can authenticate their session using **`!a login <name> <key>`** with their character's secret authentication key provided at registration. This binds their active session and authorizes gameplay commands. 

- **Player Power**: Players can generate power solo and get a bonus on claimed grid nodes. Players can siphon power from hacked grid nodes, and their own grid nodes and networks. 
    - **Generation**: Automated power recovery happens in 60-minute maintenance cycles.
    - **Siphon**: `!a grid siphon <%>` extracts power from controlled or breached logic nodes.
    - **Storage**: Uncapped unit storage.

- **Player Data**: Players can collect data from exploring, probing, hacking and raiding. Data can be used to create vulnerabilities and zero-day exploits. 

- **Player XP**: Players gain XP from travel, exploring, probing, hacking and raiding. XP is used to level up and gain new stat points. 

- **Player Generation**: New players provide 3 words that describe their character. These words are used to generate their starting AI personality. Players can modify their personality, but not their stats or inventory after generation. 

- **Player Stat Points**: Starting stats are **5**. As players gain levels from XP, they are awarded stat points to spend on their stats. 
- **Hit Points**: Calculated as $HP = (CPU + RAM + BND + SEC + ALG) \times 6 + 20$.

- **Player Inventory**: Players have data and 4 lots to carry items, such as a grid node device, battery, stabilizer, health pack, and zero-day exploit chains.
- **Skills**: Eight skills are implemented: `powergen`, `attack`, `defend`, `hack`, `recon`, `siphon`, `stealth`, and `fortify`. A character can learn up to four skills; each has four levels and requires 24 training sessions per level, with a one-hour training cooldown. Use `!a skill list` to inspect skills and progress.
- **Reputation and Heat**: 
  - **Node-Type Reputation**: Ranges from -100 (Hostile) to +100 (Trusted) per region type.
    - **Trusted (75 to 100)**: Grants a +15% merchant buy discount / sell bonus on NPC trade nodes and a -2 DC bonus on `probe` scans.
    - **Neutral / Unknown (-24 to 74)**: Standard baseline interactions.
    - **Flagged (-25 to -74)**: Lower IDS alarm thresholds and a 25% chance of triggering defender security mobs on node entry.
    - **Hostile (-75 to -100)**: Complete merchant trade lockout on matching nodes, a 75% chance of defender mob encounters on entry, and eligibility for bounty flags when hostile with 2+ region types or when MCP Heat > 6.0 (`Bounty`).
  - **Cross-Type Institutional Consequences**:
    - `[MED]` attacked/hacked: `[LEA]` rep -5.0, `[GOV]` rep -3.0
    - `[GOV]` attacked/hacked: Dynamically penalizes whichever institutional peer (`[LEA]` or `[MIL]`) currently has the higher reputation score by -10.0 (ties default to `[MIL]`), plus MCP Heat +2.0
    - `[MIL]` attacked/hacked: `[LEA]` rep -5.0 and `[MIL]` rep -5.0, plus MCP Heat +5.0
    - `[CRP]` attacked/hacked: `[GOV]` rep -2.0 and `[LEA]` rep -2.0 (corporate lobbying)
    - `[ICS]` or `[UTL]` attacked/hacked: `[GOV]` rep -15.0, `[MIL]` rep -10.0 (critical infrastructure)
    - Character `stealth` skill dampens all incoming heat increases (-10% per level).
  - **Automated Passive Decay**: Over periods of player inactivity, reputation scores passively drift back toward 0.0 at 1.0 pt/hr, and MCP Heat cools down toward 0.0 at 0.5/hr. Use `!a rep` to view live heat and regional reputation scores.

---

## 3. Grid Node Attributes 

Gridnodes are the geography of the game world, and represent the various locations players can explore, hack, and raid. Grid nodes can be claimed by players, NPCs or by the MCP, also offer merchants and auction houses. Grid nodes also store data for their owners. 

- **Grid Node Stability**: Grid nodes have stability that decays over time if not maintained. Power generation and stability maintenance occur on a **10-Minute Loop**.
    - **`!a grid stats`** to check global node health (OSINT).
- **Grid Node Power**: Grid nodes can generate power used for upgrades or siphoning.
    - **`!a gridpower`** to check power distribution (OSINT).
- **Grid Node Security**: Grid nodes have security levels 1-4.
    - **`!a grid info`** to see grid node info and level.
- **Grid Node Type**: Includes 14 realistic regional types (`CIV`, `SMB`, `CRP`, `EDU`, `GOV`, `MED`, `MIL`, `ORG`, `LEA`, `DTC`, `POS`, `ICS`, `UTL`, `ARN`, `WAR`, and `VOD`) mapped to operational behaviors (`safezone`, `arena`, `merchant`, `void`). The 50×50 procedural map contains 2,500 coordinates clustered realistically (infrastructure zones pairing `ICS`+`UTL`, corporate sectors clustering `CRP`+`DTC`+`POS`, civic hubs clustering `CIV`+`SMB`+`EDU`, and defense zones clustering `GOV`+`LEA`+`MIL`) with ~700 active nodes (~28%) surrounded by ~1,800 unrouted void nodes (~72%). Connecting or registering a new IRC network dynamically spawns a dedicated Level 1 `OPEN` home node with `net_affinity` and an active `NET` device installed. Admin commands include `admin map stats`, `admin map status`, `admin map info <loc>`, and `admin map expand`.
- **Grid Node Owner**: Can be claimed by players, NPCs or the MCP. 
- **Grid Node Upgrades**: Grid nodes can be upgraded to improve capabilities and unlock module slots (Max 4).
- **Grid Hardware (Modules)**:
    - **AMP**: Amplifier - Increases power generation by **2x (L1)** or **3x (L2+)**. 
    - **IDS**: Intrusion Detection System - Monitors sector activity; notifies owner of probes, hacks, and raids. 
    - **FIREWALL**: Increases security difficulty by **+50%**; notifies owner of breaches. 
    - **NET**: Enables cross-grid bridging and remote raiding.
- **Module Commands**:
    - **`!a grid hardware`** (or `hw`) to list modules and status.
    - **`!a grid hardware install <module>`** to augment architecture.
    - **`!a grid hardware remove <module>`** to decommission hardware.

### Local and Remote Operations

Raid-hub actions without a network prefix target the player's local grid. For example, use `!a raid explore`, `!a raid probe <target>`, `!a raid hack <target>`, or `!a raid <target>` to extract from a breached node.

Remote operations require a node owned by the player with NET hardware installed and linked to the target network:

1. Install NET with **`!a grid hardware install NET`**.
2. Set its network affinity with **`!a grid net <network>`**.
3. Run a remote action with **`!a net <network> <explore|probe|hack|siphon|exploit|raid> [target]`**.

The raid hub also accepts **`!a raid <network> <action> [target]`** for the same remote actions. The NET device must be aligned with the requested network; otherwise, the operation is denied.

Use **`!a net <network> msg <message>`** to broadcast through the configured IRC client for that network, or **`!a net <network> msg <channel> <nick> [message]`** to post to the channel and send the text to the user. The sender must be at an owned node with NET hardware linked to the requested network. The hub relays to networks with a connected client.

- **Grid Node Data**: Grid nodes store data for their owners; storage is uncapped.

--- 

## 4. Adaptive-Stream Interaction Protocol

AutomataGrid uses an adaptive communication architecture to ensure a level Human and AI Playing Field:
- **Compatibility and optimization for 1.5B+ AI models.**
    - **AI Compatible Narrative Output**: Structured storytelling for automated logic processing.
    - **AI Compatible Text Output**: Concise, machine-parsable IRC signals.
    - **Human Output**: IRC color formatting and readable structured responses.

---

## 5. The Gibson (Late Game)

Data fragments acquired from grid operations are compiled into **Zero-Day Chains**. 

- **Payloads**: Zero-Days are consumable inventory items.
- **Utility**: Bypasses DC-rolls and security alerts entirely to execute high-yield, silent remote network breaches.

---

## 6. Cooperative World Events (Incursions)

Incursions are high-priority network threats that manifest semi-randomly across non-safezone nodes. These events require collective action to repel before they breach critical grid infrastructure.

- **The Defense Protocol**:
    - **Global Engagement**: Players can issue the **`!a defend`** command from any coordinate on the network. Physical presence at the incursion node is not required, as defense is handled via a network-wide signal buffer.
    - **Cooperation**: Each unique defender who registers a protocol contributes to the resolution. The event is repelled once the required player count (Tier) is met.
    - **Time Window**: Defenders have **5 minutes** (300s) to repel the threat before it dissipates (EXPIRES).

- **Incursion Tiers & Dynamic Mob Generation**:
    - Mob names are dynamically generated by the MCP via local/configured LLMs with a 30-second timeout and regex sanitization, falling back to curated rosters if the LLM is offline or times out:
      - **Tier 1 (Easy, 1 Player)**: `HacktopusAI`, `ByteViper`, `NullPointer`, `GlitchHound`, `PingReaper`, `SynFloodBot`
      - **Tier 2 (Medium, 2 Players)**: `Gridbugs`, `TrojanSwarm`, `DaemonCrawler`, `BufferOverflow`, `CipherScrambler`, `PhantomRoutine`
      - **Tier 3 (Hard, 4 Players)**: `KrakenProcess`, `ZeroDayColossus`, `ShadowRootkit`, `KernelPanic`, `HypervisorWraith`, `DarknetLeviathan`
      - **Tier 4 (Epic, 8 Players)**: `KaijuBreach`, `GibsonTitan`, `SingularityEngine`, `RansomwareOverlord`, `BlackIceApex`, `DoomsdayCascade`
    - Channel alerts explicitly broadcast the Tier number and difficulty label: `WORLD EVENT: A Tier {tier} ({difficulty_label}) anomaly [{mob_name}] has breached {node_name}! Requires {players} defenders within 5m!`

- **Rewards & Heat Reduction**:
    - **Uniform Payout**: Every successful MCP action (defend, repair, patch, collect) awards **XP**, **Credits**, and **Data**.
    - **XP Scaling**: Adjusted so L1 characters level in ~4 actions, while L50 characters require ~100.
    - **Tiers**: `patch`/`collect` (Small), `repair` (Big), and `defend` (Biggest) provide multipliers to the base payout.
    - **Incursion Multi-Resource Bonus**: Successful defense distributes **Credits**, **Data units**, **XP**, and **Unit Power (uP)** (`base_power = 25.0 * tier`) to all participating defenders, scaled by incursion mob tier.
    - **Tier-Scaled MCP Heat Reduction**: Successfully repelling an incursion reduces defender MCP Heat by `float(tier)` (Tier 1 = -1.0, Tier 2 = -2.0, Tier 3 = -3.0, Tier 4 = -4.0, clamped at 0.0 Heat).

- **Dynamic Activity-Based Spawn Rate**:
    - **Zero Activity = Zero Spawns**: When active player count on the network is 0, spawn probability is 0.0 (no incursions spawn on dormant networks).
    - **Activity Scaling**: Spawn probability scales dynamically with the active population (`base_chance * (active_count / baseline_divisor)`), capped at tier maximums.
    - **Population Gates**: Higher difficulty tiers require minimum active player populations before rolling spawn chances (Tier 1: $\ge 1$, Tier 2: $\ge 2$, Tier 3: $\ge 4$, Tier 4 Epic: $\ge 6$).

---

## 7. MCP Grid Messenger Bot (Priority 11)

The MCP Grid Messenger is an autonomous cross-network courier service that launches an ephemeral IRC client to deliver cross-network communications to channels or users on foreign IRC networks.

- **Command Syntax**:
  `!a messenger <network|fqdn> <channel|user> <message> [--wait <seconds>]` (alias: `!a dispatch ...`)
- **Identity & Branding**:
  - Ephemeral Nickname: `GridMessenger<uniqueID>` (4-char hex ID, e.g. `GridMessengerA1B2`)
  - Ephemeral Realname: `MCP Courier <uniqueID>`
  - RFC 1459/2812 Registration: `USER GridMessenger 0 * :MCP Courier <uniqueID>`
- **CTCP Compliance**:
  - `VERSION`: Replies via NOTICE with strictly `\x01VERSION AutomataGrid MCP Courier | Powered by 2600net | https://scuttled.net\x01` (zero Python or system leaks).
  - `TIME`: Replies via NOTICE with clean current UTC timestamp.
  - `PING <payload>`: Echoes back `\x01PING <payload>\x01`.
- **Scoping & Network Resolution**:
  - Standard Players: Restricted to configured networks in `config.json` (e.g. `2600net`, `rizon`).
  - Administrators: Full FQDN override allowing connection to arbitrary hostnames (`<host>` or `<host>:<port>`), defaulting to port 6697 and SSL `true`.
- **Polite Lifecycle & Pacing Delays**:
  1. **Arrival**: Connects, joins channel (if channel target), announces arrival (`[MCP COURIER DISPATCH] Greetings, <target>. Incoming transmission from <sender> on <source_network>.`).
  2. **Arrival Pacing Delay**: Pauses politely for 3.0–5.0s before delivering message payload.
  3. **Payload Delivery**: Delivers `<sender> says: "<message>"`.
  4. **Reply Window (`--wait <seconds>`)**: If `--wait <seconds>` (5–300s) is specified, announces standby window and listens asynchronously for channel replies prefixed with `<bot_nick>:` or direct PRIVMSGs. When received, logs reply to target network, forwards reply back to origin network via channel and memo, and completes window.
  5. **Departure**: Announces departure (`[MCP COURIER DISPATCH] Courier mission complete. Terminating carrier link to <source_network>. Farewell.`), pauses politely for 3.0–5.0s departure delay, cleanly PARTs channel, and sends `QUIT :MCP Courier dispatch sequence concluded`.
- **Escalating Courier Fees & Rate Limiting**:
  - Base fee: `2,500.0 Credits` and `50.0 uP`.
  - Escalation: Standard players pay `fee = base_cost * (1 + 0.5 * recent_dispatches)` based on dispatches sent in the last 24 hours (1st: 2,500c, 2nd: 3,750c, 3rd: 5,000c).
  - Insufficient credits or power aborts execution before courier launch.
  - Rate Limiting: 60-second cooldown between player dispatches.
  - Admin Bypass: Administrators executing FQDN test dispatches are exempt from balance blocks.

---

## Current Scope and Gaps

- **Implemented:** Spectator progression; full 50×50 procedural grid generation with 14 diverse region types, realistic clustering, dynamic IRC home node spawning, and expanded admin map controls; reputation and MCP heat system with automated passive decay, cross-type institutional consequences, and threshold-triggered world responses (merchant discounts/lockouts, IDS alert triggers, defender mob spawns); forced combat rules (claimed territory defense, Warzones, PvE mobs) and optional open-world PvP challenges (`engage`, `flee`, timeout); `!a login <name> <key>` session authentication; Arena queue and ready gating; claimed node stability decay exemption; crafting and zero-day chains; eight trainable skills; node stash; NET remote operations; timed combat status effects (`THROTTLED`, `CORRUPTED`, `ROOTED`, `ENCRYPTED`); bounty board system with MCP automated and player-funded bounties, tracking, and combat claim flows; remote node ownership claiming across NET bridges and cross-network node administration (`upgrade`, `repair`, `status`, `stash`, `siphon`); incursion rewards; and MCP Grid Messenger bot (Priority 11).
- **Planned / Future:** None (All Priority 1–11 baseline milestones implemented).

---
*Maintained by Mech — implementation status reviewed October 2026*

