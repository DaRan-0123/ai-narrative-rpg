# AI Narrative RPG

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-lightgrey.svg)](#requirements)

A **text-only AI narrative RPG** — desktop-native, offline-capable, and works with any OpenAI-compatible LLM API.

---

## What makes this different

There are plenty of AI text games. Most of them hand your input straight to a model and let it tell a story. This project adds a layer of constraint **before** narration and **after** state updates, to solve the two problems that always show up in a long campaign.

### 1. Adjudication before narration

Your input does not go directly to the narrator. A separate adjudication layer first rules on whether the action is viable in the current situation, and what it costs:

- **Risky actions get bounced back.** You must choose *Take the risk* (accepting a stated cost) or *Try something else*.
- **Failure always carries a concrete, perceivable cost** — not "you failed," but "you drop off the ledge, a sharp pain shoots through your left ankle, and the footsteps behind you get closer."
- The outcome is locked in **before** the narrator describes it. The model never gets the chance to narrate a failed lockpick as a success.

### 2. Zero state drift

The status panel (time, location, money, injuries, inventory…) is emitted by the model every turn. But there is a hard rule at the prompt level:

> **State fields that objectively have not changed must be copied verbatim, character for character.**

Without that rule the model "helpfully" rephrases state every turn, and after a few dozen turns the status panel no longer matches what actually happened — and you will not notice, because the drift is gradual. The rule turns state from "whatever the model feels like writing" into "the model may only change what genuinely changed."

### 3. Facts are never deleted

Long campaigns inevitably outgrow the context window. The usual answer is to let the model compress old facts, at the cost of **silently losing information** — and then one day the plot collapses.

The rule here: **facts may be summarized, but never dropped.**

- When the active fact set exceeds its cap, the oldest low-confidence facts are **moved into an archive file** (`saves/<save>/archives/facts_archive.json`) rather than deleted.
- A summary is generated *alongside* the originals, not instead of them.
- The archive stays readable on disk forever.

### 4. Desktop-native

Python + tkinter. Double-click a `.bat` and play — no browser, no server, no Docker. A bundled offline dependency set means the **first install needs no network at all**.

### 5. Multi-provider

Any OpenAI-compatible `/chat/completions` endpoint works. Pick your provider in the settings screen:

| Provider | Notes |
|---|---|
| DeepSeek | Default; good price/performance |
| OpenAI | GPT series |
| Moonshot | Kimi |
| Zhipu GLM | |
| Qwen | DashScope compatible mode |
| SiliconFlow | |
| Google Gemini | OpenAI-compatible endpoint |
| Ollama / LM Studio | Local inference — no key, no cost |
| Custom / gateway | Any compatible base URL (Claude and others can be reached through a gateway) |

---

## Screenshots

<!-- TODO: the existing screenshots in docs/screenshots/ show the older Chinese UI.
     Retake them against the current English build before publishing. -->

_Coming soon._

---

## Quick start

### Requirements

- **Windows 10 / 11**
- **Python 3.10 or newer** ([download](https://www.python.org/downloads/) — be sure to check **Add Python to PATH** during installation)

> ⚠️ **Do not use the Python from the Microsoft Store.** Windows 10/11 ships a Store `python.exe` stub that exists even when Python is not installed, and installing it frequently leaves this program unable to run. Download the installer from the link above instead.

> Windows only for now: the program uses Windows APIs for DPI awareness and screen-work-area queries, and has not been ported elsewhere.

### Run it

Just double-click **`start_game.bat`**. It checks for Python, installs dependencies, and launches the game — and if anything goes wrong it prints the reason and the fix in the window instead of flashing past.

Or do it by hand:

```bash
python -m pip install -r requirements.txt
python main.py
```

There are only two dependencies (`ttkbootstrap`, `requests`), so this takes seconds.

> **About the offline bundle:** if this directory contains `vendor_packages/` (it ships with the full distribution), the first run needs **no network at all** — the `.bat` installs from it directly. A repository cloned from GitHub does not include that directory (it is `.gitignore`d), so the first run does need a connection.

### Configure your API key

1. Click **Settings** in the top-right of the main menu.
2. Choose an **API provider** and paste your **API key** (the *Get an API key* button links to the signup page).
3. Click **Test connection** to confirm it works.
4. Click **Save settings**.

**Your key is stored only on your own machine, in `~/.ai_rpg_config.json`. It is never uploaded anywhere.** If you launch the game without a key configured, it will prompt you.

> Running the game incurs API costs, billed by your provider. Local inference (Ollama / LM Studio) is free.

---

## How to play

- Type what you want to do in free text each turn; the AI generates objective narration.
- The world evolves on its own: NPCs act independently, seasons and weather advance with time.
- When creating a world you hold a multi-turn conversation with an AI wizard, drilling into the details you care about.
- Top bar: **Roll Back** (undo the previous turn) · **Manual Save** · **World Archive** · **Log** · **Debug mode**.

---

## Headless / HTTP mode

Besides the desktop GUI there is a headless service build: `serve.py` exposes the same engine over a stdlib-only HTTP API (no extra dependencies), for scripting or embedding.

```bash
python serve.py
```

See [docs/API.md](docs/API.md) for the endpoints.

---

## Project layout

```
start_game.bat        Double-click this (checks Python, installs deps, launches)
main.py               Program entry point
serve.py              Headless HTTP service entry point
src/
  engine.py           Shared engine base (GUI and headless both subclass it)
  prompts.py          The P1–P14 prompt system (the core of this project)
  api_client.py       Multi-provider API layer (including streaming SSE)
  game_state.py       Game state, calendar, rollback snapshots
  save_manager.py     Saves, archives, atomic writes
  config.py           Configuration
  world_builder.py    World creation pipeline (P9 / P10 / P6 / P8)
  vocab.py            Canonical tokens and bilingual parsers
  ui/                 Interface (main menu / game window / world wizard / debug window)
  server/             Headless HTTP service
docs/                 Design documents (architecture, data flow, subsystems)
saves/                Save directory (empty on first run; 10 slots)
vendor_packages/      Offline dependency bundle (full distribution only; not in git)
requirements.txt      Dependency list
```

### The prompt system

`src/prompts.py` holds a dozen-plus independent prompt roles, P1 through P14. When they are called, the pieces are **assembled in prefix-cache-friendly order**: the immutable world block first, then low-frequency blocks, then the per-turn-growing history in the middle, and the ever-changing player input last. That keeps the shared prefix as long as possible across calls, which cuts token costs substantially.

---

## Known limitations

Honestly:

- **Windows only.** Other platforms are not supported.
- **You must supply your own API key,** and it costs money to run.
- **Anthropic's native protocol is not implemented.** Claude must be reached through an OpenAI-compatible gateway (choose *Custom* and enter the gateway URL).
- **No cloud sync.** Saves live on your machine.
- **Single-player only.** No multiplayer, no networking.

---

## Design documents

`docs/` contains the full design documentation — architecture, data flow, P12 spatial positioning, seasons and weather, NPC memory, and more. Read [docs/HANDOVER.md](docs/HANDOVER.md) before changing core logic.

---

## License

[MIT](LICENSE)
