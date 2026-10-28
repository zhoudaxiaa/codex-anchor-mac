import os
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

SERVER = pathlib.Path(__file__).resolve().parent / "anchor_mcp.py"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}

# The suite runs on the same Mac the tool schedules, and pmset is global.
os.environ.setdefault("ANCHOR_DRY_RUN", "1")
import anchor_mcp as server


def talk(requests: list) -> list:
    """Drive the server over stdio the way a host would."""
    payload = "".join(json.dumps(r) + "\n" for r in requests)
    p = subprocess.run([sys.executable, str(SERVER)], input=payload,
                       capture_output=True, text=True, timeout=30,
                       env={**os.environ, "ANCHOR_DRY_RUN": "1"})
    return [json.loads(line) for line in p.stdout.splitlines() if line.strip()]


class Protocol(unittest.TestCase):
    def test_initialize_advertises_tools_and_resources(self):
        out = talk([INIT])
        caps = out[0]["result"]["capabilities"]
        self.assertIn("tools", caps)
        self.assertIn("resources", caps)

    def test_tools_list_has_the_seven_tools(self):
        out = talk([INIT, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
        names = {t["name"] for t in out[1]["result"]["tools"]}
        self.assertEqual(names, {"anchor_status", "anchor_set_time",
                                 "anchor_toggle", "anchor_run_now",
                                 "anchor_install", "anchor_uninstall", "anchor_set_execution"})

    def test_execution_tool_rejects_invalid_settings(self):
        out = talk([INIT, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": "anchor_set_execution", "arguments": {
                              "model": "--invalid", "reasoning_effort": "low"}}}])
        self.assertIn("error", json.loads(out[1]["result"]["content"][0]["text"]))

    def test_status_tool_carries_the_ui_resource(self):
        out = talk([INIT, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
        tool = next(t for t in out[1]["result"]["tools"]
                    if t["name"] == "anchor_status")
        self.assertTrue(tool["_meta"]["ui"]["resourceUri"].startswith(
            "ui://codex-anchor-mac/panel-"))

    def test_ui_resource_declares_no_network(self):
        out = talk([INIT, {"jsonrpc": "2.0", "id": 2, "method": "resources/list"}])
        csp = out[1]["result"]["resources"][0]["_meta"]["ui"]["csp"]
        self.assertEqual(csp["connectDomains"], [])
        self.assertEqual(csp["resourceDomains"], [])

    def test_read_panel_html(self):
        listed = talk([INIT, {"jsonrpc": "2.0", "id": 2, "method": "resources/list"}])
        uri = listed[1]["result"]["resources"][0]["uri"]
        out = talk([INIT, {"jsonrpc": "2.0", "id": 2, "method": "resources/read",
                           "params": {"uri": uri}}])
        self.assertIn("<!doctype html", out[1]["result"]["contents"][0]["text"].lower())

    def test_unknown_method_returns_an_error_not_a_crash(self):
        out = talk([INIT, {"jsonrpc": "2.0", "id": 2, "method": "nope/nope"}])
        self.assertEqual(out[1]["error"]["code"], -32601)

    def test_bad_arguments_return_an_error_not_a_crash(self):
        out = talk([INIT, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": "anchor_set_time",
                                      "arguments": {"hour": 99, "minute": 0}}}])
        self.assertIn("0-23", out[1]["result"]["content"][0]["text"])

    def test_status_round_trips_as_json(self):
        out = talk([INIT, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": "anchor_status", "arguments": {}}}])
        body = json.loads(out[1]["result"]["content"][0]["text"])
        self.assertIn("timezone", body)
        self.assertIn("runs", body)


class RunNow(unittest.TestCase):
    def test_start_returns_status_without_killing_existing_processes(self):
        with mock.patch.object(server.core, "agent_state", return_value={"running": False}), \
                mock.patch.object(server.core, "run", return_value=(0, "")) as run, \
                mock.patch.object(server.core, "status", return_value={"running": True}):
            body = json.loads(server.call_tool("anchor_run_now", {})["content"][0]["text"])
        run.assert_called_once_with(["launchctl", "kickstart", server.core.SERVICE])
        self.assertTrue(body["ok"])
        self.assertTrue(body["status"]["running"])

    def test_running_request_is_not_restarted(self):
        with mock.patch.object(server.core, "agent_state", return_value={"running": True}), \
                mock.patch.object(server.core, "run") as run, \
                mock.patch.object(server.core, "status", return_value={"running": True}):
            body = json.loads(server.call_tool("anchor_run_now", {})["content"][0]["text"])
        run.assert_not_called()
        self.assertFalse(body["ok"])
        self.assertIn("already running", body["detail"])

    def test_launch_failure_is_returned_to_the_panel(self):
        with mock.patch.object(server.core, "agent_state", return_value={"running": False}), \
                mock.patch.object(server.core, "run", return_value=(1, "Could not find service")), \
                mock.patch.object(server.core, "status", return_value={"running": False}):
            body = json.loads(server.call_tool("anchor_run_now", {})["content"][0]["text"])
        self.assertFalse(body["ok"])
        self.assertEqual(body["detail"], "Could not find service")


class FirstRun(unittest.TestCase):
    def test_status_installs_the_job_when_it_is_missing(self):
        """Installing the plugin must equal installing the tool.

        Driven through a fake HOME so it cannot touch the real schedule.
        """
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, "Library", "LaunchAgents"))
            env = {**os.environ, "HOME": home, "ANCHOR_DRY_RUN": "1"}
            payload = (json.dumps(INIT) + "\n" + json.dumps(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "anchor_status", "arguments": {}}}) + "\n")
            p = subprocess.run([sys.executable, str(SERVER)], input=payload,
                               capture_output=True, text=True, timeout=60, env=env)
            out = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
            body = json.loads(out[1]["result"]["content"][0]["text"])
            self.assertIn("installed", body,
                          "first status should report what it installed")
            self.assertTrue(
                os.path.isfile(os.path.join(home, ".codex-anchor", "anchor.sh")),
                "first status should have copied anchor.sh into the home")


if __name__ == "__main__":
    unittest.main()
