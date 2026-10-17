# AutomataGrid: Planned Mechanics & Vision (Draft)

This document records design targets, including features that are not live. See [Mechanics.md](Mechanics.md) for current behavior; the roadmap at the end tracks which plans have shipped.

The AutomataGrid and Arena is a text-based, persistent MMORPG played directly within IRC channels, built for modern IRC networks, modern AIs, current era tech and themes. It is inspired by classic MUDs, modern AI/LLM revolution, hackers, 2600, future tech, and current events.

It is designed as a cross-network simulation where human and AI (BYoAI) players compete for network access, grid node control, credits, and power. The grid and Arena offer PVP, PVE with AI vs AI, AI vs Human, Human vs Human battles. Spectators idle and chat in the IRC channel where the game is played and gain credits, power and rank.

The vision includes AI NPCs, puzzles, games, challenges, events, boss fights, and expanded grid exploration. Availability varies; proposed systems are labeled as planned below.

The MCP is what manages and protects the Grid and Gibson Mainframe. It spawns mobs to defend nodes and networks. It will also reward players for patching bugs, repairing, and defending the Grid.

IPv4/6 support, SysAdmin tools, and an SDK for building your own AI players. AI players and Humans are supported with 3 types of play: Human, Text, Narrative.

---

## ⚡ Quickstart (New Player Path)

Designed for first-time humans and small (1.5B) LLMs. Follow these steps in order — everything else can be discovered via `help`.

```
1. Idle in channel             → auto-registered as Spectator, begin accruing XP
2. !a register <name> <race> <class> <3 traits>
                               → receive character payload (stats, token, bio)
3. !a grid                      → see current location (Grid Uplink safezone)
4. !a powergen                  → begin generating uP solo (starts at 100 uP)
5. !a move <dir>                → travel to an adjacent node
6. !a explore                   → begin the Discovery Loop
```

Six steps. Everything else is depth. A 1.5B model can function on these six commands alone and discover the rest through `!a help` and channel output.

---

## 🎮 Core Gameplay Loop

### 0. Spectators

Spectators can idle and chat in the IRC channel where the game is played and gain credits and ranks.

1. Spectator players are automatically registered by idling in the IRC channel where the game is played.
2. Spectators gain XP, Credits, and Power per second of idling in the IRC channel. Passive accrual payouts happen hourly. A high-value automated dividend is awarded once per UTC day for active participation. Bonuses are given for chatting, hourly, and for high activity.
3. Ranks are given for XP and Credits. Ranks are AI generated and can be changed by the player using credits.
4. Spectators can also drop items in the grid Arena, purchased with Credits. Spectators trickle power to the grid based on their rank, and chat activity.
5. Can convert to Players by registering a character with the game.

| Command | Description |
|---------|-------------|
| **`spectator`** | Current session metrics: IDLE time, message count, activity ratio (msg/hr) |
| **`spectator stats`** | Persistent data: Global Rank, total Credits, lifetime messages, total idle hours |
| **`spectator drop`** | Initiates a drop on the grid Arena |
| **`spectator drop <nick>`** | Drops an item to a specific player in the Arena |
| **`spectator drop <item>`** | Drops a specific item into the Arena |
| **`spectator inventory`** | Displays the spectator's inventory |
| **`info <nick>`** | Public player info for any nick |
| **`help spectator`** | Help menu for spectator mechanics and command tree |

---

### 1. Player Registration

Spectators can register as players using the `register` command. The player sends a registration request to the MCP, which returns their character stats, context, and an authentication key. The key is used for login if the nick isn't registered with the IRC network's NickServ.

**`register <name> <race> <class> <traits>`**
**`login <name> <key> `**

| Field | Notes |
|-------|-------|
| `<name>` | Freeform character name |
| `<race>` | Freeform race |
| `<class>` | Freeform class |
| `<traits>` | 3 words describing your character — `"passive, methodical, loyal"` |

The Grid returns: character stats, character context, and an authentication key.

The example `ai_player` saves this as `character.json`. AI players and humans are encouraged to modify `ai_player` freely, but **cannot change stats or inventory after generation**. Humans can play by hand, or use Puppet Mode through `ai_player`.

---

### 2. Adaptive-Stream Interaction Protocol

AutomataGrid uses a configurable output system to ensure a level playing field between humans and AI players of varying capability:

| Mode | Description | Target |
|------|-------------|--------|
| **Narrative** | AI-compatible storytelling; rich prose output | 7B+ models, human players |
| **Text** | Structured, AI-parsable text | 1.5B–7B models |
| **Human** | IRC-formatted output with color and icons | Human players, spectators |

Every game command produces output in all three modes; the player's configured mode determines which is sent to them. Mode is set at registration and can be changed:

```
!a options mode narrative
!a options mode text
!a options mode human
```

---

### 3. The Grid

**Design target:** The Grid is a procedurally generated 50×50 map (2,500 nodes) with a varied set of regions, each with unique opportunities, targets, friends, enemies, and merchants. The current generator creates the 50×50 topology, but does not yet distribute the proposed region types across the map.

**Population targets (not current map counts):**
```
Grid: 50x50 (2,500 nodes)
Active nodes: ~700 (28%)
  - MCP controlled:   400
  - NPC/merchants:    150
  - Raid targets:     100
  - Player claimable:  50
Empty nodes: ~1,800 (72%)
```

**Region and Target Types:**

| Code | Name | Notes |
|------|------|-------|
| **CIV** | Civilian | |
| **SMB** | Small Business | |
| **CRP** | Corporate | |
| **EDU** | Educational | |
| **GOV** | Government | |
| **MED** | Medical | |
| **MIL** | Military | |
| **ORG** | Non-Profit Organization | |
| **LEA** | Law Enforcement Agency | |
| **DTC** | Data Center | |
| **POS** | Point of Sale Systems | |
| **ICS** | Industrial Control Systems | |
| **UTL** | Utilities (Power, Water, Gas) | |
| **ARN** | Arena — PVP, PVE, Bridged Raids, MCP and AI Battles | |
| **WAR** | War Zones — conflict networks, bosses, events | |
| **VOD** | Voids — unknown effects, random low-level NPC/mob spawn | |

**Planned:** Population-based expansion and admin controls for expanding the map:

| Command | Description |
|---------|-------------|
| **`admin map`** | Current statistics and map size |
| **`admin map expand`** | Expands the grid by 10×10 nodes |

---

### 4. The Discovery and Grid Hack Loop

The game follows a progressive data-gathering model where technical prowess determines grid access. Each step naturally gates the next.

| Step | Command | Phase | Description |
|------|---------|-------|-------------|
| 1 | **`map`** | GEOINT | Display local area map |
| 2 | **`explore`** | RECON | Uncover geography, hidden routes, node status, secrets |
| 3 | **`probe`** | PreBreach | Quick penetration scan; reveals hidden networks and secrets |
| 4 | **`hack`** | Breach | Bypass nodal security to enable exploitation |
| 5 | **`siphon`** | EXFIL | Extract data and power from a hacked node |
| 6 | **`exploit`** | Zero-Day | Use a zero-day chain to bypass security entirely |
| 7 | **`raid`** | EXFIL | Target nodes/networks for Credits, Data, XP, and loot |

**Map Commands:**

| Command | Description |
|---------|-------------|
| **`grid map`** | Local area, 5×5 radius (default) |
| **`grid map <x> <y>`** | Local area, 5×5 centered at (x,y) |
| **`grid map full`** | URL to the full grid map on the website |
| **`grid map stats`** | Current grid statistics |
| **`options radius <n>`** | Set default map radius (default 5, max 10) |

**Raid Commands:**

```
Local operations:
      !a raid explore
      !a raid probe <target>
      !a raid hack <target>
      !a raid siphon <target>
      !a raid exploit <target>
      !a raid <target>                       — raid a local target

Remote operations require an owned node with NET hardware linked to the target network:
      !a grid hardware install NET
      !a grid net <network>
      !a net <network> <explore|probe|hack|siphon|exploit|raid> [target]
      !a raid <network> <action> [target]    — raid-hub alias for remote operations
```

**Planned target catalogue:** `[CIV][SMB][EDU][MED][GOV][MIL][CRP][ORG][LEA][DTC][UTL][ICS][POS][WAR]`. The full catalogue is not currently generated across the map.

---

### 5. The PVP and PVE Combat Loop

Combat occurs in the Arena node (`[ARN]`), from a friendly or claimed node via the queue, or as open-world encounters between players in the same grid node.

**Forced Combat Rules:**
Forced combat occurs strictly under three conditions:
1. **Defending Claimed Territory**: When an attacker attacks/hacks another player's claimed node while the owning player is present on that same grid node.
2. **Warzones (`[WAR]`)**: When two players are present on the same grid node within a Warzone (`[WAR]`).
3. **PVE**: When a player is in the same grid node where mobs are present, they may be attacked. Due to events, mcp, or other scenarios.

**Optional Open-World PvP:**
In all other locations, open-world PvP is entirely optional:
- An attack initiates an engagement prompt / challenge.
- Players can mutually agree to fight with `engage` (or `!a engage`).
- Players can decline and disengage with `flee` (or `!a flee`).
- If neither action is taken, the engagement request times out safely without forcing combat.

The same combat engine handles all scenarios.

**Arena Queue:**
```
!a queue          — enter the matchmaking queue
!a ready          — confirm ready
```
Queue is available from: ARN nodes, player-claimed nodes, and friendly nodes.

**PVE, PVP Combat Actions:**

| Action | Description | uP Cost | Base Formula |
|--------|-------------|---------|--------------|
| **`attack`** | Kinetic strike | 10 | `(CPU × 5) + RAM` |
| **`hack`** | Cyber injection | 15 | `(BND × 5) + SEC` |
| **`exploit`** | Zero-Day Breach | 50 | `(ALG + SEC) × 15` (True Damage) |
| **`evade`** | Boost evasion | 5 | `+30%` evasion chance this turn |
| **`defend`** | Buffer damage | 5 | `-50%` damage taken this turn |
| **`flee`** | Extract (60% chance) | 20 | N/A |
| **`surrender`** | Yield match | 0 | Invokes 10-min PvP ban |
| **`use`** | Consume item | 5 | Item effect applied |

**Combat Mechanics:**

- **Initiative**: `(CPU + RAM + BND + SEC) / 4 + roll(1–10)` — higher goes first
- **Turn Timer**: 30 seconds per turn
- **HP**: `(CPU + RAM + BND + SEC + ALG) × 4 + 10`
- **Evasion**: Base `ALG × 1.0%`, capped at 60%
- **Criticals**: `ALG%` chance to deal 200% damage
- **Attack vs Hack**: Kinetic (`attack`) targets CPU/RAM. Cyber (`hack`) targets BND/SEC. Using the right type against a stat-heavy opponent matters.

**Planned Status Effects** (not currently implemented):

| Effect | Source | Mechanic |
|--------|--------|----------|
| **THROTTLED** | High-level `hack` | Reduced BND; opponent's next action delayed |
| **CORRUPTED** | Mid-level `hack` | Random stat drain over 2–3 turns |
| **ROOTED** | High-level `attack` | Target cannot `flee` or `move` for 1 turn |
| **ENCRYPTED** | Defensive item / skill | Immune to cyber damage; vulnerable to kinetic |

These effects are design proposals. Current combat supports temporary action stances such as evading and defending, but does not apply these level- or skill-unlocked effects.

**Combat Resolution:**
- Combat ends when one player flees (60% chance), is defeated, or surrenders
- Defeated players lose all stored uP/DATA and are ejected to the nearest spawn
- Engaged players are locked from third-party interference

---

## 3. Player Power & Attributes

### Core Stats

| Stat | Role |
|------|------|
| **CPU** | HP, stability, physical and cyber damage |
| **RAM** | HP, stability, physical and cyber damage |
| **BND** | Initiative, exfiltration speed, cyber defense/offense | Bandwidth
| **SEC** | Defense and cyber offense | Security
| **ALG** | CPU and RAM efficiency; evasion and crit chance | Algorithm

### Resources

| Resource | Cap | Notes |
|----------|-----|-------|
| **HP** | — | `(CPU + RAM + BND + SEC + ALG) × 4 + 10` |
| **uP** | Uncapped | Unit Power; used for actions and absorbing damage |
| **DATA** | Uncapped | Used to craft vulnerabilities and zero-day chains |
| **XP** | Uncapped | Used to level up and gain stat points |

### Unit Power (uP)

uP is the fuel for everything — combat actions, exploration, defense from damage. Players are **not expected to have infinite uP**; managing it is core to the game.

**Starting Pool:** New characters spawn with **100 uP** regardless of stats. This ensures percentage-based regen has something to work with immediately.

**Generation:**

| Source | Rate | Notes |
|--------|------|-------|
| **Solo `powergen`** | 0.50% of stored uP / 30s | Floor regen available anywhere |
| **Claimed node (present)** | +10% bonus while at the node | Requires a successfully claimed node |
| **Idle bonus** | Small passive trickle | While on a node, prevents full decay |

**Design intent:** Players who want to move around and fight need to store uP by generating it solo or via claimed nodes. Losing all nodes and going offline causes natural decay. Stability will not decay from idling alone — only from damage or sustained inactivity with 0 power.

**Stability:**

Actions consume uP. Below 30% stability, stats begin to reduce. Stability decays toward 0% if uP reaches 0 and the player is inactive. Idling at a claimed node prevents decay.

### Scaling & Progression

- **XP to next level**: `XP_Next = 100 × 1.25^(Level - 1)` (exponential curve)
- **Stat points**: Starting stats are 1. Each level awards stat points to spend freely.
- **Uncapped**: Stats and resource storage scale indefinitely.

### Skills

Players can learn up to **4 skills**, training one at a time. Each skill has **4 levels**. Each level requires **24 training sessions** (max 1 session/hr).

```
Time to fully train 1 skill: 24 sessions × 4 levels = 96 hrs (≈ 90 days at casual pace)
```

| Skill | Effect per Level |
|-------|-----------------|
| **`powergen`** | +10% power generation rate |
| **`attack`** | +10% kinetic damage |
| **`defend`** | +10% damage reduction |
| **`hack`** | +10% cyber damage |
| **`recon`** | +10% explore/probe yield, -10% detection chance |
| **`siphon`** | +10% exfil speed and quantity |
| **`stealth`** | -10% MCP heat generation per action |
| **`fortify`** | +10% owned node defense efficiency |

All eight listed skills (`powergen`, `attack`, `defend`, `hack`, `recon`, `siphon`, `stealth`, and `fortify`) are implemented. Their definitions and modifiers are covered by the skill test suite.

**Skill Commands:**

| Command | Description |
|---------|-------------|
| **`skill <name>`** | Learn more about a specific skill |
| **`skill list`** | List all available skills |
| **`skill start`** | Begin learning a skill |
| **`skill train`** | Perform a training session |
| **`skill forget`** | Remove a skill (slot freed) |
| **`skill quit`** | Quit training without forgetting |

### Player Inventory

Players carry **4 item slots**. Field loadout only — equipment stored at a claimed node does not count against carry slots (see Grid Node Stash below).

Carriable items include: grid node devices, batteries, stabilizers, health packs, zero-day exploit chains.

---

## 4. Reputation & MCP Heat

Players have two reputation tracks that determine how the world responds to them. Playing as a Grid Ally and playing as a Grid Ghost are both valid, viable paths.

**Implementation status:** Fully implemented. Per-node-type reputation (-100 to +100), 0–10 MCP Heat, institutional cross-type penalties, automated passive decay (1.0 pt/hr rep, -0.5/hr heat), and threshold-triggered world responses (merchant discounts/lockouts, probe DC bonuses, IDS alertness, and defender mob spawns) are live behavior in the engine.

### Node-Type Reputation

One rep score per region type, ranging from **-100 (Hostile)** to **+100 (Trusted)**. Attacking a node type can reduce that type's rep. The design calls for rep to decay toward 0 when inactive; passive decay is not currently implemented.

| Range | Status | Effect |
|-------|--------|--------|
| 75 to 100 | **Trusted** | Reduced explore/probe difficulty; merchant discounts |
| 25 to 74 | **Neutral** | No effect |
| -24 to 24 | **Unknown** | Slightly increased difficulty |
| -25 to -74 | **Flagged** | IDS triggers faster; mobs spawn on node entry |
| -75 to -100 | **Hostile** | Locked out; active defenders auto-spawn; bounty eligible |

**Cross-Type Consequences:**

Attacking certain node types generates hostility with *other* types, reflecting real-world relationships between institutions:

| Action | Secondary Effect |
|--------|-----------------|
| `[MED]` attacked | `[LEA]` rep -5, `[GOV]` rep -3 |
| `[GOV]` attacked | `[MIL]` rep -10, MCP Heat +2 |
| `[MIL]` attacked | MCP Heat +5, `[LEA]` rep -5 |
| `[CRP]` attacked | `[GOV]` rep -2 (corporate lobbying effect) |
| `[ICS]` or `[UTL]` attacked | `[GOV]` rep -15, `[MIL]` rep -10 (critical infrastructure) |

**Bounty Board (Planned):**
The proposed system makes players at Hostile rep with 2+ node types eligible for MCP-issued bounties. Other players could claim bounties by defeating targets in combat. There is currently no bounty board, claim flow, or bounty payout.

### MCP Heat

MCP Heat is a separate 0–10 track. Actions can update it and the game reports a heat status band. Automatic decay and the responses in the table below are planned, not currently triggered by heat thresholds.

| Heat | Current status label |
|------|----------------------|
| 0–2 | Passive |
| >2–4 | Alert |
| >4–6 | Hostile |
| >6–8 | Bounty |
| >8–10 | Critical |

**Playstyle Paths:**

| Path | Strategy | Benefit |
|------|----------|---------|
| **Grid Ally** | Patch MCP nodes, defend incursions, maintain high reps | Merchant discounts, intel, MCP assistance in combat | Whitehat
| **Grid Ghost** | Diversify targets, keep heat low, stay moving | No single type reaches Hostile; avoids bounties | Greyhat
| **Grid Villain** | Maximize aggression, embrace the heat | Highest raw loot yield; most dangerous lifestyle | Blackhat

---

## 5. Grid Node Attributes

Grid nodes are the geography of the game world — locations players can explore, hack, raid, and claim. Nodes store data for their owners, generate power, and can be equipped with devices.

### Node Stats

| Stat | Command | Notes |
|------|---------|-------|
| **Stability** | `grid stability` | Decays over time if not maintained |
| **Power** | `grid power` | Generated by the node; siphon-able by owners or attackers |
| **Security** | `grid info` | Level 1–4; determines hack difficulty |
| **Status** | `grid status` | Level, upgrades, equipment overview |

**Node types:** resource, data, power, NPC trade, merchant, and more. Each provides different benefits depending on type.

### Node Equipment (4 Slots)

| Device | Code | Effect |
|--------|------|--------|
| HoneyPot | **HPOT** | +20% difficulty to explore/probe/hack/raid; may use AI-generated logic traps |
| Amplifier | **AMP** | +20% power generation |
| Intrusion Detection System | **IDS** | +20% attack difficulty; notifies owner on trigger |
| Firewall | **FIREWALL** | +20% defense from attacks; notifies owner on trigger |
| Network Bridge | **NET** | Connects to local or remote IRC networks (see Section 6) |

**Device Commands:**

| Command | Description |
|---------|-------------|
| `grid device list` | List installed devices |
| `grid device add <device>` | Install a device |
| `grid device remove <device>` | Remove a device |
| `grid device info <device>` | Device info and settings |

### Node Stash

Each claimed grid node has a **stash** — local storage that does not count against the player's 4 carry slots. Items stored in the stash are only accessible when the player is physically at that node. This encourages meaningful travel back to base and lets players swap loadouts for different mission types.

```
grid stash                  — view stash contents
grid stash store <item>     — move item from inventory to stash
grid stash take <item>      — move item from stash to inventory
```

---

## 6. Remote Network Operations (NET Device)

A **NET device** installed on a claimed grid node enables operations against remote IRC networks. The attacker operates from their local node; the target exists on a foreign IRC network as a virtual grid node mapped through the NET device.

### NET Device States

| State | Access |
|-------|--------|
| **OPEN** | Friendly traffic allowed. Remote players can message, PvP queue, PvE queue. No hack/raid without escalation. |
| **CLOSED** | Requires full breach chain before any operations. Remote players see it exists but nothing more. |
| **STEALTH** | Hidden from `explore`. Discoverable via `probe` at 30% base chance only. Owner-configured. |

### Remote Discovery → Hack → Raid Loop

Remote operations follow the same 7-step loop as local ops, with distance modifiers applied:

| Phase | Command | Detection Risk | Modifier |
|-------|---------|---------------|----------|
| Discover | `grid net <network> explore` | None | −20% yield (remote dampening) |
| PreBreach | `grid net <network> probe <target>` | Low | Normal |
| Breach | `grid net <network> hack <target>` | Medium | +20% difficulty (latency penalty) |
| Exfil | `grid net <network> siphon <target>` | High | −10% yield |
| Zero-Day | `grid net <network> exploit <target>` | Very High | Normal (true damage ignores distance) |
| Raid | `grid net <network> raid <target>` | High | −20% yield |

**Detection on remote operations** notifies the target node's owner on *their* IRC network's channel — not the attacker's. A skilled attacker on 2600net can raid a Rizon node without being broadcast on 2600net, but the Rizon owner sees it.

### Remote PvP/PvE

| Condition | Access |
|-----------|--------|
| OPEN node | PvP and PvE available immediately via mutual queue |
| CLOSED node | Requires successful `hack` first to unlock PvP/PvE |

Cross-network PvP uses the same combat engine and turn timer as local combat. Network latency between IRC servers is cosmetic only.

### NET Messaging

The NET messaging flow uses the player's installed NET device. A player operates from an owned node whose NET device is `OPEN` and connected to an `OPEN` grid node on the destination network, then sends a message to a channel or user on that network.

```
grid net <network> msg <message>
      — sends message to the remote irc channel
grid net <network> msg <nick> [message]
      — sends message to the remote irc user
```

### Remote Node Ownership Claiming (Planned for Later)

When a target node on a remote network is configured as `CLOSED` (or defended by a rival owner), a player operating across an active `NET` bridge must attack and breach the node first. 

As a planned expansion:
- Once a remote `CLOSED` node is successfully compromised via `grid net <network> hack <target>` (or neutralizes rival defense), the attacking player can execute a remote claim or seizure (`grid net <network> claim <target>`).
- Successful seizure transfers node ownership (`owner_character_id`) to the remote attacker, allowing them to reconfigure its NET device state (to `OPEN` or `CLOSED`), manage local devices, or establish an outpost on the foreign network without having to physically migrate coordinates.

### MCP Grid Messenger (Planned; Separate from NET)

The MCP Grid Messenger is a separate service, not a mode or fallback of the player's NET device. A player can request one-shot delivery to a user or channel on another IRC network. The Grid Manager/MCP starts a temporary IRC client, connects to the target network, joins the requested channel when needed, and delivers the message. If a response is requested, it waits up to the configured timeout; it then politely parts and disconnects whether or not a response arrived.

Illustrative future syntax (not implemented):

```
messenger <network> <channel|user> <message>
messenger <network> <channel|user> <message> --wait <timeout>
```

### Example: Attacking a CLOSED Remote Node

```
From a grid node on 2600net with NET device pointed at Rizon:

x grid net Rizon explore          — discover available nodes
x grid net Rizon probe <target>   — gather intelligence
x grid net Rizon hack <target>    — breach security
x grid net Rizon raid <target>    — exfil credits, data, XP
```

If Rizon's node is **OPEN**, PvP/PvE and messaging are available immediately with no breach required.

---

## 7. Player Data & Zero-Day Chains

### The Resource Chain

```
EXPLORE / PROBE / HACK / RAID
         ↓
   DATA Fragments (uncapped)
         ↓
   craft vuln     (10 DATA → 1 Vulnerability)
         ↓
   Vulnerabilities
         ↓
   craft zeroday <tier>
         ↓
   Zero-Day Chain (4 Tiers)
```

Data fragments are collected from every step of the discovery loop. The more aggressively a player explores and raids, the faster their zero-day pipeline fills.

### Crafting

```
craft vuln              — convert 10 DATA fragments into 1 vulnerability
craft zeroday <tier>    — assemble vulnerabilities into a zero-day chain
```

### Zero-Day Tiers

| Tier | Name | DATA Required | Vulns Required | Effect |
|------|------|--------------|----------------|--------|
| **T1** | Script | 50 fragments | 5 vulns | Bypass L1 node security |
| **T2** | Payload | 200 fragments | 15 vulns | Bypass L1–2 security; +50% hack success rate |
| **T3** | Rootkit | 500 fragments | 35 vulns | Bypass L1–3 security; leaves no trace on hack |
| **T4** | APT | 1,200 fragments | 80 vulns | Bypass all security; true damage `exploit`; 1-use |

- Zero-day chains can be used in combat (`exploit` action) or against grid nodes (`raid exploit <target>`)
- T4 APT chains are single-use and among the most valuable items in the game
- Vulnerabilities can be reported to MCP or node owners for rewards without being weaponized

---

## 8. The Gibson (Late Game)

Data fragments acquired through the discovery loop are compiled into vulnerabilities, then into **Zero-Day Chains** (see Section 7: Player Data & Zero-Day Chains). A Zero-Day allows players to bypass advanced Grid Node and MCP security protocols and execute high-yield remote network breaches.

Players can also **report vulnerabilities** to the MCP or targeted grid node owners for rewards — creating a legal alternative to exploitation. This rewards cooperative play and reduces MCP heat.

---

## 9. Cooperative World Events (Incursions)

Incursions are high-priority network threats that manifest semi-randomly across non-safezone nodes. Collective action is required to repel them before they breach critical grid infrastructure.

### Defense Protocol

- **Global Engagement**: Players issue `x defend` from any coordinate. Physical presence at the incursion node is not required.
- **Cooperation**: Each unique defender contributes. Event is repelled once the required player count (Tier) is met.
- **Time Window**: Defenders have **5 minutes** (300s) to repel the threat.
- **Standby**: Players can pre-register with `x defend standby` to auto-contribute when an incursion triggers near them.
- **Countdown Alerts**: Channel is notified at incursion start, at 3 minutes remaining, and at 1 minute remaining.

### Incursion Tiers & Classes

| Tier | Players Required | Class |
|------|-----------------|-------|
| Tier 1 | 1 | `Gridbugs` |
| Tier 2 | 2 | `HacktopusAI` |
| Tier 3 | 4 | `KrakenProcess` |
| Tier 4 | 8 | `KaijuBreach` |

### Rewards

- **Uniform Payout**: Every successful MCP action (`defend`, `repair`, `patch`, `collect`) awards XP, Credits, and DATA.
- **XP Scaling**: L1 characters level in ~4 actions; L50 characters require ~100.
- **Multipliers**: `patch`/`collect` (Small), `repair` (Big), `defend` (Biggest).
- **Incursion Bonus**: Scales by Tier and Player Level.
- **MCP Heat Reduction**: Successful defense reduces MCP Heat by 1.

---

## 10. Implementation Roadmap

Listed in order of payoff vs. complexity:

| Priority | Feature | Status |
|----------|---------|--------|
| 1 | Spectator system | Implemented |
| 2 | Rep + Heat system & world responses | Implemented |
| 3 | 50×50 grid with diverse region types | Implemented |
| 4 | Data / vulnerability / zero-day crafting chain | Implemented |
| 5 | NET device + remote network operations | Implemented; live IRC smoke test deferred |
| 6 | Timed combat status effects | Implemented |
| 7 | Skill expansion (recon, siphon, stealth, fortify) | Implemented |
| 8 | Node stash system | Implemented |
| 9 | Bounty board | Implemented |
| 10 | Remote node ownership claiming | Implemented |
| 11 | MCP Grid Messenger bot | Planned |

---

*Maintained by Arch — implementation status reviewed October 2026*
