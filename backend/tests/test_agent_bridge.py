from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
from threading import Thread
import urllib.request


BRIDGE_PATH = Path(__file__).resolve().parents[2] / "bridges" / "openpulse_agent_bridge.py"


def _load_bridge_module():
    spec = importlib.util.spec_from_file_location("openpulse_agent_bridge", BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_bridge_arg_mode_passes_prompt_as_command_argument(tmp_path):
    bridge = _load_bridge_module()
    output_path = tmp_path / "argv.json"
    script_path = tmp_path / "agent.py"
    script_path.write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(output_path)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )

    class TestHandler(bridge.BridgeHandler):
        pass

    TestHandler.agent_command = [sys.executable, str(script_path)]
    TestHandler.token = None
    TestHandler.timeout_seconds = 5
    TestHandler.prompt_mode = "arg"
    server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        request = urllib.request.Request(
            url,
            data=json.dumps(
                {
                    "type": "openpulse.monitor.condition_matched",
                    "data": {
                        "monitor": {"name": "Price watch"},
                        "event": {"summary": "Price changed", "currentValue": "12"},
                    },
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 202
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    argv = json.loads(output_path.read_text())
    assert len(argv) == 1
    assert "You were woken by an OpenPulse monitor event." in argv[0]
    assert "Price watch" in argv[0]
