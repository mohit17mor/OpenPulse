# OpenPulse

OpenPulse is a local-first monitor for websites, scripts, feeds, and anything else you can turn into browser state or command output.

It is built for the annoying gap between simple cron jobs and expensive always-on AI agents: you set up a monitor once, OpenPulse checks it deterministically, and agents are only woken when something actually matters.

## What It Does

- Opens a managed Chromium browser so you can select page content visually.
- Captures DOM context and recent network responses during setup.
- Uses an optional one-time Google AI Studio setup step to choose a stable monitoring recipe for complex pages.
- Runs saved monitors on a local scheduler.
- Runs local script monitors from plain text, JSON scalar values, or JSON item lists.
- Sends matched events to webhooks, local commands, or agent bridges such as Codex and Claude.
- Supports one-shot triggers so threshold monitors do not wake agents on every matching check.
- Keeps logs and delivery history in a local SQLite database.

OpenPulse does not keep an LLM in the polling loop. The intended shape is: use intelligence while setting up the monitor if it helps, then monitor cheaply with deterministic checks.

## Why This Exists

People often ask AI agents to keep checking things:

- "Tell me when this price crosses a threshold."
- "Watch this website and tell me if this text changes."
- "Check this feed every few minutes and summarize new items."
- "Run this script and alert me if the number is too high."
- "Wake Codex when this local condition becomes true."

That burns tokens and keeps the agent doing boring polling work. OpenPulse does the boring part locally and wakes the agent only when a saved rule matches.

## Current Status

This is an early local-first MVP. It is useful, but intentionally small:

- No hosted service.
- No browser extension.
- No user accounts.
- No CAPTCHA bypassing.
- No promise that every complex website can be monitored reliably.

For protected pages, OpenPulse works best when the managed browser session is already logged in or already past any interactive checks.

## Quick Start

Requirements:

- Python 3.11+
- Chrome or Chromium

Install on macOS, Linux, or WSL:

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt -e backend
```

If Playwright cannot find a browser:

```bash
.venv/bin/playwright install chromium
```

Run:

```bash
.venv/bin/uvicorn openpulse.app:app --app-dir backend/src --reload --host 127.0.0.1 --port 8000
```

Install on Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements-dev.txt -e backend
```

If Playwright cannot find a browser:

```powershell
.\.venv\Scripts\python -m playwright install chromium
```

Run:

```powershell
.\.venv\Scripts\python -m uvicorn openpulse.app:app --app-dir backend/src --reload --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## Platform Support

OpenPulse is built on Python, FastAPI, SQLite, and Playwright, so the core app is intended to work on macOS, Linux, Windows, and WSL.

Current testing status:

- macOS: actively used during development.
- Linux/WSL: expected to work with the documented commands.
- Windows: expected to work with PowerShell commands, but less exercised than macOS.

Some bundled script examples use Unix-style commands such as `python3`, `bash`, `ps`, or `/proc/stat`. On Windows, use the PowerShell commands above and change script monitor commands from `python3` to `python` or `py` if needed.

## First Demo: Website Price Monitor

1. Open OpenPulse.
2. Click `Launch browser`.
3. Use the default fixture URL:

   ```text
   http://127.0.0.1:8000/fixtures/product.html
   ```

4. Click `Go`.
5. In the managed browser, press `M` or use `Select on page`.
6. Click the highlighted price.
7. Set a condition such as `less than 100`.
8. Choose a trigger policy:
   - `Every matching check`
   - `Only first match`
9. Save the monitor.
10. Run a check from `Saved monitors`.

To test a changed fixture, open:

```text
http://127.0.0.1:8000/fixtures/product_changed.html
```

## Website Monitors

Website monitors start from a visual selection. OpenPulse stores the selected target and enough surrounding context to re-check it later.

For straightforward pages, the DOM path and selector are usually enough. For dynamic pages where list order or rendered DOM changes often, OpenPulse can use recent network responses captured during setup to build a more stable recipe.

Checks prefer the already-launched browser session when one is available. This helps with pages that behave differently in a fresh headless browser. If no managed browser is open, checks fall back to a separate headless browser.

If a site shows a bot/security verification page, OpenPulse logs the check as blocked instead of pretending the target simply disappeared.

## Optional Smart Setup

For complex sites, OpenPulse can make one cheap LLM call during monitor setup. The call receives a compact, redacted candidate packet and decides whether the monitor should use:

- DOM extraction
- Network extraction
- A fallback recipe

Set one of these before starting the server:

```bash
export GOOGLE_API_KEY="..."
# or
export GEMINI_API_KEY="..."
```

Default model:

```bash
export OPENPULSE_GEMINI_MODEL="gemini-2.5-flash-lite"
```

On Windows PowerShell:

```powershell
$env:GOOGLE_API_KEY="..."
$env:OPENPULSE_GEMINI_MODEL="gemini-2.5-flash-lite"
```

The LLM is not used for every check. Saved network monitors use deterministic recipes: find the same entity by stable identity fields, then read the configured value path. If the entity disappears, OpenPulse reports it as missing instead of reading the wrong list item.

## Script Monitors

Script monitors let OpenPulse watch anything you can print to stdout.

Flow:

1. Open `Script monitor`.
2. Enter a command, arguments, working directory, and timeout.
3. Click `Run preview`.
4. Select a value or item list from the output.
5. Save the monitor with a condition and trigger policy.

Supported output:

- Plain text stdout as a single value.
- JSON scalar paths such as `btc.price`.
- JSON arrays as item-list monitors.

For item-list monitors, choose a stable ID field such as `guid`, `id`, or Jira `key`. OpenPulse stores the preview items as the baseline and only logs new IDs seen in later runs.

Item-list monitors default to batch delivery. If 10 new feed items appear, OpenPulse sends one event containing all 10 items. You can switch a monitor to per-item delivery when each new item should wake its own agent run, such as one investigation per Jira ticket.

Example scripts:

```bash
python3 fixtures/scripts/price_json.py
python3 fixtures/scripts/plain_count.py
python3 fixtures/scripts/feed_items.py
```

## Script Library

Open `Scripts` in the sidebar to load starter monitors from `scripts/examples/`.

Included examples:

- Disk usage
- Folder size
- CPU usage
- RSS/feed item detection
- Process count
- System load

Put your own scripts in:

```text
scripts/custom/
```

Starter scripts are templates. OpenPulse fills the form, then you preview, inspect, adjust, and save.

## Trigger Policies

Each monitor can decide when matched events should wake destinations.

`Every matching check`

The default. If the condition matches every check, OpenPulse sends a delivery every time.

`Only first match`

OpenPulse sends the first matched delivery and then suppresses future deliveries while the same rule stays armed. Checks and logs continue, but agents are not woken repeatedly.

Changing the condition or trigger policy resets the one-shot state. Changing the schedule does not.

For item-list monitors, keep `Every matching check` and use `One event with all new items` when you want a single agent call to summarize a feed batch.

This is useful for threshold-style monitors such as:

- BTC above a target price
- Flight price below a target price
- A product becoming available
- A page containing a specific phrase

## Agent Destinations

OpenPulse can send matched events to destinations. A monitor can route to zero, one, or many destinations.

Supported destination types:

- Webhook
- Local command
- Codex bridge preset
- Claude bridge preset

If no destination is selected, matched events stay local in OpenPulse logs.

## Agent Bridge

For CLIs that do not expose native webhooks, run the bundled bridge:

```bash
python3 bridges/openpulse_agent_bridge.py --port 8765 --prompt-mode arg -- codex exec
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python bridges\openpulse_agent_bridge.py --port 8765 --prompt-mode arg -- codex exec
```

Then create a webhook destination pointed at:

```text
http://127.0.0.1:8765
```

The bridge:

- Responds to `/health` so OpenPulse can show whether it is online.
- Receives OpenPulse event JSON.
- Formats a compact, task-focused event prompt.
- Starts the configured command.
- Streams agent stdout/stderr by default so you can see what the agent is doing.

To hide agent output in the bridge terminal:

```bash
python3 bridges/openpulse_agent_bridge.py --port 8765 --prompt-mode arg --capture-output -- codex exec
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python bridges\openpulse_agent_bridge.py --port 8765 --prompt-mode arg --capture-output -- codex exec
```

The bridge prompt includes the monitor name, event summary, previous/current values, relevant source details, new feed items when present, and your monitor's agent instructions. It does not include the full raw event JSON by default, which keeps agent runs cheaper and less confusing.

For debugging, append the full event JSON:

```bash
python3 bridges/openpulse_agent_bridge.py --port 8765 --prompt-mode arg --include-raw-json -- codex exec
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python bridges\openpulse_agent_bridge.py --port 8765 --prompt-mode arg --include-raw-json -- codex exec
```

Example agent instruction on a monitor:

```text
Summarize the matched event and tell me whether it needs action.
```

## Interesting Things To Monitor

OpenPulse is not limited to websites. Good demos are things people currently waste agent tokens polling:

- A crypto or stock page crossing a threshold.
- A flight or bus price becoming cheap enough.
- A product changing from sold out to available.
- A WhatsApp Web or internal dashboard text changing in an already-open browser session.
- New items in an RSS feed or Hacker News search.
- Disk usage, CPU usage, or process count crossing a threshold.
- A local build artifact, folder size, or generated report changing.
- A script that checks an API and prints JSON.

The sweet spot: a cheap deterministic check plus a one-time agent wakeup only when the check matters.

## Data And Privacy

OpenPulse stores monitor state locally in SQLite:

```text
backend/data/openpulse.db
```

Browser profile data lives under:

```text
data/browser-profile/
```

If smart setup is enabled, a compact setup packet may be sent to the configured Google AI Studio model. Regular checks do not call the LLM.

## Development

Run tests:

```bash
.venv/bin/pytest backend -q
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python -m pytest backend -q
```

Run the app:

```bash
.venv/bin/uvicorn openpulse.app:app --app-dir backend/src --reload --host 127.0.0.1 --port 8000
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python -m uvicorn openpulse.app:app --app-dir backend/src --reload --host 127.0.0.1 --port 8000
```

Useful paths:

```text
backend/src/openpulse/       FastAPI app and monitor runtime
backend/src/openpulse/static Browser UI
backend/tests/               Test suite
bridges/                     Agent bridge
fixtures/                    Local demo pages and scripts
scripts/                     Script monitor workspace
```

## Notes

OpenPulse is local-first software. Be respectful of websites, rate limits, and terms of service. It is meant for personal monitoring, internal workflows, and demos where you control the environment or have permission to monitor.
