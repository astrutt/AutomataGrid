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
 
## Reputation & MCP Heat System

Check your current standing and heat anytime with:
- `!a rep` (or machine mode `REP:INFO`)

### Reputation Standing & World Reactions:
- **`Trusted` (75 to 100)**: 15% discount when purchasing gear/items from NPC merchants, 15% sell bonus, and -2 DC probe bonus.
- **`Neutral` / `Unknown` (-24 to 74)**: Standard operations.
- **`Flagged` (-25 to -74)**: Security systems alert faster; 25% chance of automated defender security mobs intercepting you upon node entry.
- **`Hostile` (-75 to -100)**: Merchants refuse to trade with you; 75% chance of defender security mobs spawning on entry.
- **Passive Decay**: Reputation passively decays towards 0.0 at 1.0 pt/hr, and heat cools down at -0.5/hr during inactivity.

## Combat & PvP Rules
 
- **Forced Combat**:
  1. Defending claimed nodes when the owner is present in the sector.
  2. Warzone nodes (`[WAR]`) when multiple players enter the same coordinate.
  3. PvE encounters where hostile mobs are present.
- **Optional Open-World PvP**: In all other sectors, attacking another player issues a 30-second challenge:
  - `!a engage` — accept the duel and initiate combat.
  - `!a flee` — decline and retreat to safety without penalty.
  - Doing nothing allows the challenge to safely expire after 30 seconds.
- **Arena Matchmaking**:
  - `!a queue` — enter the Arena match queue (available at `[ARN]`, claimed, or friendly nodes; requires account and active login).
  - `!a ready` — confirm ready for match drop (only available when queued).

## Session Authentication & Login

If your IRC nickname is not registered with NickServ (`+r`), you can authenticate your character session using:
- `!a login <name> <key>`

Where `<key>` is the secret auth token returned to you during character registration.

