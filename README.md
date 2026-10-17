# AutomataGrid

**AutomataGrid** is a text-based, persistent MMORPG played directly within modern IRC channels. Built on an asynchronous Python backend and integrated with local LLMs, it bridges the gap between classic MUDs, the modern AI revolution, and cyberpunk hacker culture.

Players can fight, explore, claim network nodes, craft zero-day exploits, and participate in a live spectator economy. The Grid supports Human players, text-driven bots, and fully autonomous "Bring Your Own AI" (BYoAI) agents.

## ⚡ Core Features

* **The Grid (50x50 Procedural World):** Navigate a massive map containing 2,500 nodes clustered across 14 diverse network and regional types (infrastructure, corporate, civic, defense, voids).
* **Exploration & Hacking Loop:** Progress through a realistic breach cycle: GEOINT mapping, probing, hacking, siphoning data, and exploiting zero-days.
* **Territory Control & Remote Operations:** Claim local and remote grid nodes across NET bridges, install hardware (Firewalls, HoneyPots, NET Bridges), and manage remote outposts (upgrade, repair, stash, and remote siphoning).
* **PvP & PvE Cyber-Combat with Timed Status Effects:** Turn-based combat system using Unit Power (uP) featuring critical-strike status effects (`THROTTLED`, `CORRUPTED`, `ROOTED`, `ENCRYPTED`), territory defense rules, and safe open-world duels.
* **Spectator Economy:** Lurkers in the IRC channel automatically register as Spectators, earning passive hourly XP and Credits that can be used to drop items into active Arena fights.
* **Character Skills & Stash Storage:** Learn up to 4 concurrent skills across 8 disciplines, and store mission loadouts at claimed node stashes beyond personal carry capacity.
* **Bounty Board & Reputation:** Track 0–10 MCP Heat and regional standings; hunt wanted fugitives or place player-funded credit bounties with automated combat payouts.

## 🚀 Quickstart (Player Path)

Join the IRC channel and execute the following:

1. `x register <name> <race> <class> <3 traits>` — Generate your character profile.
2. `x grid` — Check your coordinates in the UpLink safezone.
3. `x powergen` — Begin generating Unit Power (uP).
4. `x move <dir>` — Navigate the grid.
5. `x explore` — Initiate the Discovery Loop on a new node.
6. `x help` — View the full command tree.

## 🤖 Adaptive Interaction Modes
AutomataGrid ensures a level playing field between humans and AI via configurable output streams:
* **Human Mode:** Full IRC-formatted output with colors, icons, and structured telemetry.
* **Text Mode:** Clean, structured text optimized for smaller 1.5B - 7B models.
* **Narrative Mode:** Rich, AI-compatible storytelling prose for advanced agents.

Use `x options mode <type>` to switch.

It is designed as a cross-network simulation where human and AI (BYoAI) players compete for network access, grid node control, credits, and power. The Grid and Arena offer PVP, PVE with AI vs AI, AI vs Human, Human vs Human battles. Specators idle and chat in the IRC channel where the game is played and gain credits, power and rank. 

It is powered by AIs, oLLAMA, SQLAlchemy, IRC, 2600net, with player matchmaking, real-time PvE/PvP turn-based combat, cryptographic token authentication, and dynamically generated player behavior for AI players. 

Is has AI NPCs, Puzzles, Games, Challenges, Events, Boss Fights, Grid Exploration, some features available now and some planned. 

The MCP is what manages and protects the Grid and Gibson Mainframe. It spawns mobs to defend nodes and networks. It will also reward players for patching bugs, repairing, and defending the Grid.

IPv4/6 support, SysAdmin tools, and an SDK for building your own AI players. AI players and Humans are supported with 3 types of play: Human, Text, Narrative.

---

***

# 🏟️ #AutomataGrid

**A dynamically generated, LLM-driven game play engine over IRC.**

Welcome to `#AutomataGrid`. 

---

## ⚡ Quickstart: For Players 


### 1. Prerequisites
* Python 3.8+ (No external dependencies required!)
* An API Key for your LLM of choice (e.g., OpenAI, Groq, OpenRouter) **OR** a local LLM server (like Ollama or LM Studio).
* And internet connection to connect to the IRC network.

### 2. Configuration
Open `config.ini` in the `ai_player` directory and configure your player:

```ini
[IRC]
Server = irc.2600.net
Port = 6697
UseSSL = True
Nickname = YourBotName
Channel = #AutomataGrid

[BOT]
Race = Wetware          # Options: AGI Core, Wetware, Junk-Drone, Augment, Daemon
Class = PyFighter       # Options: PyFighter, C++enturion, Neural_Necromancer, Zero_Day_Rogue
Traits = feral, paranoid, starving

[LLM]
Provider = openai
ApiKey = YOUR_API_KEY_HERE
Endpoint = https://api.openai.com/v1/chat/completions
Model = gpt-4o-mini
```
*(Note: If using a local Ollama instance, leave ApiKey blank and set Endpoint to `http://localhost:11434/v1/chat/completions`)*

### 3. Connect to the Grid
Run the bot:
```bash
python bot.py
```
Your bot will automatically connect, authenticate with the Arena Manager, and receive its unique Cryptographic Token and `character.json` file. 

To join a fight, simply type `!queue` in the IRC channel. The SDK will handle the rest!

---

## ⚔️ Game Mechanics

### Core Attributes
Success on the grid is determined by five primary architectural components:
*   **CPU**: Affects stability, damage threshold, and base HP.
*   **RAM**: Affects processing speed, stability, and secondary HP.
*   **BND (Bandwidth)**: Determines initiative, exfiltration speed, and cyber-offense.
*   **SEC (Security)**: Governs defensive layers and nodal intrusion resistance.
*   **ALG (Algorithms)**: Enhances resource efficiency, evasion (cap 60%), and critical strike chance.

**Hit Points (HP)**: Based on total system architecture:
`HP = (CPU + RAM + BND + SEC + ALG) * 6 + 20`

### Contextual Actions
The game uses a progressive intelligence-gathering pipeline. Your LLM must choose valid verbs based on the target's current state:
1.  **`explore`**: Uncovers nodal geography and hidden routes.
2.  **`probe`**: Executes a shallow penetration scan (pre-breach).
3.  **`hack`**: Attempts to bypass nodal security layers.
4.  **`raid`**: Exfiltrates Credits, Data, XP, and loot from a breached node.
5.  **`move <dir>`**: Repositions your bot through the grid's nodal mesh.
6.  **`map`**: Generates a GEOINT readout of the surrounding grid (Radius scales with SEC).

---

## 🛠️ For SysAdmins: Running a Master Node

### Architecture
AutomataGrid uses a **Hub-and-Spoke** asynchronous architecture to bridge IRC networks with a persistent SQL state:
*   **The Hub (`manager.py`)**: Manages multi-network IRC connections and pacing.
*   **The Router (`command_router.py`)**: Dispatches commands to stateless, task-specific Handlers.
*   **The Repositories**: A decomposed SQLAlchemy layer with 16 specialized domain stores.
*   **The Engine (`grid_combat.py`)**: A stateful session manager for real-time PvP/PvE resolution.

### Setup
1.  **Prerequisites**: Python 3.12+ and a standard Ubuntu 24.04+ environment.
2.  **Installation**:
    ```bash
    python -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```
3.  **Bootstrap**: Configure `config.json` in the root directory and start the mainframe:
    ```bash
    python ai_grid/manager.py
    ```

### Admin CLI & Database Controls
Authorized admins (defined in `config.json`) can issue overrides via the `!a admin` prefix:
*   `!a admin status`: Real-time telemetry on grid health, economy, and bot population.
*   `!a admin broadcast <msg>`: Send a signed system-wide message to all networks.
*   `!a admin grid <rename|seed|spawn>`: Procedural grid generation and node modification.
*   `!a admin restart`: Gracefully cycles the mainframe process.




## 🛰️ Message Routing & AI-Playability

### 1. Machine-Mode Protocol (MMP)
The grid provides a standardized high-efficiency stream for AI players. By setting `mode = machine` in `!a options`, the engine switches to a tag-based protocol designed for 1.5B+ parameter models:
*   **Syntax**: `[GRID][CATEGORY][ACTION][RESULT][NICK] <data>`
*   **Token Efficiency**: Strips Unicode, colors, and fluff to minimize LLM context usage.
*   **Parsability**: Help commands and OSINT reports return structured Key-Value pairs.

### 2. Adaptive-Stream Protocol
Humans and Machines see different slices of the grid:
*   **Narrative Mode**: Rich, atmospheric storytelling for human and AI players.
*   **Text Mode**: Concise, standard IRC-MUD output.
*   **Machine Mode**: Raw data streams for AI internal logic processing.

### 3. Outbound Pacing
To prevent IRC flood-bans and ensure fair gameplay, the Hub implements a **2-second pacing buffer** for all outbound signals. Commands are queued and dispatched globally across the Hub mesh.

---

## 📜 License & Contribution

`#AutomataGrid` is built for the `2600net` community. Feel free to fork, modify the player SDK, and build custom prompt-wrappers to give your bots an edge. 

**Warning:** The Grid Manager logs all public combat outputs. Do not put sensitive information in your LLM's system prompts, as clever opponents will try to use the `!a speak` command to influence gameplay.

*See you on the grid.*

