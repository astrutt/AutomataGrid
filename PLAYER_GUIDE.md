What a 1.5B Model Can Do Reliably

Parse pipe-delimited or simple structured output (your Text mode)
Execute single, clear commands (map, explore, hack <target>)
Follow a simple decision tree with 2-3 options
Maintain a very short memory of immediate context (last 2-3 exchanges)

This is enough to survive early game — spawning, exploring nearby nodes, probing, basic raiding. A 1.5B running your Text mode output with a tight system prompt could plausibly play the discovery loop on its own.

## Local and Remote Grid Operations

Commands without a network target operate on your local grid:

- `!a raid explore`
- `!a raid probe <target>`
- `!a raid hack <target>`
- `!a raid siphon <target>`
- `!a raid exploit <target>`
- `!a raid <target>` to raid a breached node

For remote operations, stand at a node you own, install NET hardware, and point it at a configured network:

1. `!a grid hardware install NET`
2. `!a grid net <network>`
3. `!a net <network> <explore|probe|hack|siphon|exploit|raid> [target]`

The `!a raid <network> <action> [target]` form is also supported. Remote operations require the owned node's NET device to be installed and linked to the requested network.

### Remote Node Ownership Claiming & Administration
From a node you own with an active NET device pointing to `<network>`:
- `!a net <network> claim <target>` — claim an unowned remote node (immediate if `OPEN`, requires prior breach window if `CLOSED`).
- `!a net <network> upgrade <target>` — remotely upgrade an owned remote node level (+20% fee).
- `!a net <network> repair <target>` — remotely restore durability using power or credits (+20% fee).
- `!a net <network> status <target>` — check remote node telemetry and security metrics.
- `!a net <network> stash <target>` — inspect stash inventory stored at the remote node.
- `!a net <network> siphon <target> [power|data|all] [%]` — siphon power or data fragments back across the bridge into character reserves (-10% remote distance dampening).
 
## Reputation & MCP Heat System

Check your current standing and heat anytime with:
- `!a rep` (or machine mode `REP:INFO`)

### Reputation Standing & World Reactions:
- **`Trusted` (75 to 100)**: 15% discount when purchasing gear/items from NPC merchants, 15% sell bonus, and -2 DC probe bonus.
- **`Neutral` / `Unknown` (-24 to 74)**: Standard operations.
- **`Flagged` (-25 to -74)**: Security systems alert faster; 25% chance of automated defender security mobs intercepting you upon node entry.
- **`Hostile` (-75 to -100)**: Merchants refuse to trade with you; 75% chance of defender security mobs spawning on entry; eligible for MCP bounties.
- **Passive Decay**: Reputation passively decays towards 0.0 at 1.0 pt/hr, and heat cools down at -0.5/hr during inactivity.

## Bounty Board System
Track and hunt high-profile targets or place contracts:
- `!a bounty` or `!a bounty list` — view active MCP and player-issued bounties.
- `!a bounty check <nick>` — inspect current bounty details and wanted status on a player.
- `!a bounty post <nick> <credits> [reason]` — place a player-funded bounty on a rival player.
- **Automated Claims**: Neutralizing a wanted player in PvP combat automatically claims the bounty and transfers the reward credits to the killer.

## Combat, PvP Rules & Timed Status Effects
 
- **Forced Combat**:
  1. Defending claimed nodes when the owner is present in the sector.
  2. Warzone nodes (`[WAR]`) when multiple players enter the same coordinate.
  3. PvE encounters where hostile mobs are present.
- **Optional Open-World PvP**: In all other sectors, attacking another player issues a 30-second challenge:
  - `!a engage` — accept the duel and initiate combat.
  - `!a flee` — decline and retreat to safety without penalty.
  - Doing nothing allows the challenge to safely expire after 30 seconds.
- **Timed Status Effects**:
  - **`THROTTLED`** (1 turn): Inflicted by cyber critical hits; cuts effective BND by -50% and stuns/delays queued offensive action.
  - **`CORRUPTED`** (2–3 turns): Inflicted by cyber attacks; drains a random stat and deals recurring damage tick per turn until expired.
  - **`ROOTED`** (1 turn): Inflicted by kinetic critical hits; target cannot `flee` or `move`.
  - **`ENCRYPTED`** (2 turns): Activated via `!a defend encrypt` (10 uP); grants complete immunity to cyber damage with +15% kinetic damage vulnerability.
- **Arena Matchmaking**:
  - `!a queue` — enter the Arena match queue (available at `[ARN]`, claimed, or friendly nodes; requires account and active login).
  - `!a ready` — confirm ready for match drop (only available when queued).

## Node Stash & Character Skills
- **Node Stash**: Claimed nodes feature local storage that does not count against your 4 carry slots:
  - `!a stash` — view items stored at your current claimed node.
  - `!a stash store <item>` — deposit an item into the node stash.
  - `!a stash take <item>` — withdraw an item into your inventory.
- **Skills**: Learn and level up to 4 specialized skills:
  - `!a skill list` — view available and learned skills.
  - `!a skill start <name>` — begin training a skill (`powergen`, `attack`, `defend`, `hack`, `recon`, `siphon`, `stealth`, `fortify`).
  - `!a skill train` — perform an hourly training session (24 sessions per level, max level 4).

## Cooperative World Events (Incursions)
When the MCP detects an anomaly, a network-wide alert is broadcast to the channel:
- `!a defend` or `!a defend <node>` — register defense protocols against an active incursion from anywhere on the network.
- **Tiers & Defense**: Tiers 1–4 (Easy, Medium, Hard, Epic) require 1, 2, 4, or 8 defenders within a 5-minute window.
- **Rewards**: Successful defense awards **Credits**, **Data units**, **XP**, and **Unit Power (uP)** scaled by mob tier to all defenders.
- **Heat Reduction**: Defeating incursion threats relieves MCP attention, reducing defender MCP Heat by `-1.0` per mob tier.
- **Dynamic Spawns**: Incursion frequencies scale with active network population; zero players online results in zero spawns.

## Session Authentication & Login

If your IRC nickname is not registered with NickServ (`+r`), you can authenticate your character session using:
- `!a login <name> <key>`

Where `<key>` is the secret auth token returned to you during character registration.


