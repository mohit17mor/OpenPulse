# OpenPulse

OpenPulse is a local-first visual website monitor. Open a controlled browser, press `M` on a public page, select a highlighted fact, save a condition, and inspect local check logs.

This MVP intentionally avoids npm, browser extensions, LLMs, APIs, login handling, CAPTCHA handling, and external services. The runtime is Python/FastAPI with browser-native JavaScript served as static files.

## Setup

```bash
cd /Users/mmor/scratch/openpulse
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt -e backend
```

If Playwright cannot find a local Chrome installation, install Chromium with:

```bash
.venv/bin/playwright install chromium
```

## Run

```bash
cd /Users/mmor/scratch/openpulse
.venv/bin/uvicorn openpulse.app:app --app-dir backend/src --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

## Try The Fixture

1. Click `Launch`.
2. Keep the default URL: `http://127.0.0.1:8000/fixtures/product.html`.
3. Click `Navigate`.
4. In the controlled browser window, press `M`.
5. Click the highlighted price or drag a rectangle around the product details.
6. Save a monitor with `less than 100`.
7. Change the saved monitor URL to `http://127.0.0.1:8000/fixtures/product_changed.html` in the database or create a monitor from that fixture to test a matched check.

## Browser Sessions And Protected Pages

OpenPulse checks use the already-launched app browser session when one exists. This helps with pages that behave differently in a fresh headless browser. If no app browser is open, checks fall back to a separate headless browser.

If a site serves a bot/security verification page, logs show `blocked` with `security_verification` instead of a generic missing target.

## Agent Destinations

OpenPulse can push matched events to agents without keeping an LLM in the polling loop. Create destinations from the `Agents` sidebar view, then select one or more destinations when saving a monitor.

Supported destination types:

- Webhook: sends the OpenPulse event JSON to a local or remote HTTP endpoint.
- Local command: runs a command without shell mode and sends the OpenPulse event JSON to stdin.

If a monitor has no destinations selected, matched events stay local in the event log only. With multiple agents, routing is explicit: each monitor stores the destination IDs it should wake.

For CLIs that do not expose native webhooks, use the bundled bridge:

```bash
python3 bridges/openpulse_agent_bridge.py --port 8765 --prompt-mode arg -- codex exec
```

Then add a webhook destination pointed at `http://127.0.0.1:8765`.

The `Agents` view includes Codex and Claude bridge presets, shows the bridge command to run, and checks whether each destination is online without waking the agent. The bridge prints received events, the agent command it starts, and completion/failure status. Agent stdout/stderr is streamed to the bridge terminal by default; add `--capture-output` if you want the bridge to hide command output.

## Script Monitors

OpenPulse can run local scripts on the same scheduler. The setup flow mirrors website monitoring:

1. Choose `Script`.
2. Enter command, args, working directory, and timeout.
3. Click `Run Preview`.
4. Select a scalar output field or a JSON array of items.
5. Save the monitor.

Plain text stdout is treated as one value. JSON stdout is rendered as selectable scalar paths and item-list arrays. For item-list monitors, choose a stable ID field such as `guid`, `id`, or Jira `key`; OpenPulse stores the preview items as the baseline and logs only new IDs seen in later runs.

Example scalar JSON:

```bash
python3 fixtures/scripts/price_json.py
```

Select `btc.price` and choose a numeric condition.

Example plain text:

```bash
python3 fixtures/scripts/plain_count.py
```

Select `$stdout`.

Example item-list snapshot:

```bash
python3 fixtures/scripts/feed_items.py
```

Select `items[]`, set ID field to `guid`, display field to `title`, and URL field to `link`.

## Sample Monitors

OpenPulse includes working sample monitor templates in `sample-monitors/`. Open `Sample monitors` in the sidebar, choose a template, and OpenPulse fills the script command, args, selection, condition, and interval into the Script monitor form.

Current samples use only the Python standard library:

- Disk usage
- CPU usage
- Folder size
- RSS feed item detection
- Process count
- System load

Samples are templates, not auto-created monitors. Review or edit the settings, run preview, then save the monitor.

## Test

```bash
cd /Users/mmor/scratch/openpulse
.venv/bin/pytest backend -q
```
