"""Simulate the three-minute awake window without changing real power state."""
import json
import pathlib
import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import anchor_core as core

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "anchor.sh"
CODE = SCRIPT.read_text().split("<<'PYPREPARE'\n", 1)[1].split('\nPYPREPARE', 1)[0]
PREP = {"__name__": "preparation_test"}
exec(compile(CODE, str(SCRIPT), 'exec'), PREP)


class Preparation(unittest.TestCase):
    def wait(self, start, target, finish=None, timeout=240, jump_back=False):
        elapsed = [0]
        checked_at = []
        def clock():
            return start + (-elapsed[0] if jump_back else elapsed[0])
        def sleep(seconds):
            elapsed[0] += seconds
        def stat():
            checked_at.append(clock())
            if finish is None or clock() < finish:
                raise FileNotFoundError
            return SimpleNamespace(st_mtime=finish)
        result = PREP['wait_for_request'](target, SimpleNamespace(stat=stat), timeout,
                                         clock, lambda: elapsed[0], sleep)
        return result, elapsed[0], checked_at

    def test_holds_through_lead_time_and_releases_after_request_finishes(self):
        result, elapsed, checked = self.wait(1000, 1180, finish=1193)
        self.assertEqual(result, 'request finished')
        self.assertEqual(elapsed, 193)
        self.assertGreaterEqual(min(checked), 1180)

    def test_old_completion_does_not_skip_todays_request(self):
        self.assertEqual(self.wait(1000, 1180, finish=999)[0], 'preparation timeout')

    def test_missing_or_hung_request_has_bounded_awake_time(self):
        result, elapsed, _ = self.wait(1000, 1180)
        self.assertEqual(result, 'preparation timeout')
        self.assertEqual(elapsed, 510)

    def test_backwards_clock_jump_cannot_hold_forever(self):
        result, elapsed, _ = self.wait(1000, 1180, jump_back=True)
        self.assertEqual(result, 'preparation timeout')
        self.assertEqual(elapsed, 510)

    def test_late_wake_does_not_wait_until_tomorrow(self):
        result, elapsed, _ = self.wait(2800, 1180)
        self.assertEqual(result, 'outside preparation window')
        self.assertEqual(elapsed, 0)

    def test_midnight_and_configured_timezone(self):
        now = datetime(2026, 10, 28, 23, 57, tzinfo=ZoneInfo('Asia/Shanghai'))
        target = PREP['request_target'](now, {'timezone': 'Asia/Shanghai', 'hour': 0, 'minute': 0})
        self.assertEqual(target - now.timestamp(), 180)
        utc = now.astimezone(ZoneInfo('UTC'))
        self.assertEqual(PREP['request_target'](utc, {'timezone': 'Asia/Shanghai', 'hour': 0, 'minute': 0}), target)

    def test_preparation_plist_keeps_main_trigger_and_rolls_back_day(self):
        main = {'Label': core.LABEL, 'StartCalendarInterval': {'Hour': 0, 'Minute': 1},
                'RunAtLoad': False, 'EnvironmentVariables': {'PATH': '/usr/bin:/bin'}}
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            with mock.patch.multiple(core, HOME_DIR=root, INSTALLED=root / 'main.plist'), \
                    mock.patch.object(core, 'run', return_value=(0, '')) as run:
                rc, _ = core.install_preparation_job(main)
                data = core.plistlib.loads(core.preparation_path().read_bytes())
        self.assertEqual(rc, 0)
        self.assertEqual(main['StartCalendarInterval'], {'Hour': 0, 'Minute': 1})
        self.assertEqual(data['StartCalendarInterval'], {'Hour': 23, 'Minute': 58})
        self.assertEqual(data['ProgramArguments'], ['/bin/bash', str(root / 'anchor.sh'), '--prepare'])
        self.assertFalse(data['RunAtLoad'])
        self.assertEqual(data['ProcessType'], 'Standard')
        self.assertEqual(run.call_count, 3)

    def test_legacy_timezone_null_uses_current_local_offset(self):
        now = datetime(2026, 10, 28, 23, 57, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertEqual(PREP['request_target'](now, {'hour': 0, 'minute': 0}) - now.timestamp(), 180)

    def test_prepare_entry_holds_before_waiting_and_never_invokes_model(self):
        for human_active in (False, True):
            with self.subTest(human_active=human_active), tempfile.TemporaryDirectory() as folder:
                root = pathlib.Path(folder)
                (root / 'anchor.sh').write_bytes(SCRIPT.read_bytes())
                now = datetime.now(ZoneInfo('UTC'))
                (root / 'config.json').write_text(json.dumps({
                    'timezone': 'UTC', 'hour': now.hour, 'minute': now.minute}))
                (root / '.last-run-finished').touch()
                caffeinate = root / 'caffeinate'
                caffeinate.write_text('#!/usr/bin/python3\nimport sys,subprocess,json\n'
                    f'with open({str(root / "assertions")!r}, "a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
                    'if sys.argv[1:3] == ["-i", "-s"]: sys.exit(subprocess.run(sys.argv[3:]).returncode)\n')
                pmset = root / 'pmset'
                owner = 'WindowServer' if human_active else 'powerd'
                pmset.write_text('#!/bin/bash\nif [ "$1" = "-g" ]; then\n'
                    f'echo "UserIsActive 1"; echo "pid 1({owner}): UserIsActive wakeschedule"\n'
                    f'else echo "$@" >> "{root / "display"}"; fi\n')
                cli = root / 'codex'
                cli.write_text(f'#!/bin/bash\ntouch "{root / "MODEL_WAS_CALLED"}"\nexit 1\n')
                for tool in (caffeinate, pmset, cli):
                    tool.chmod(0o755)
                result = subprocess.run(['/bin/bash', str(root / 'anchor.sh'), '--prepare'],
                    env={**os.environ, 'CAFFEINATE': str(caffeinate), 'PMSET': str(pmset),
                         'CODEX_BIN': str(cli)}, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('request finished', result.stdout)
                self.assertFalse((root / 'MODEL_WAS_CALLED').exists())
                calls = [json.loads(line) for line in (root / 'assertions').read_text().splitlines()]
                self.assertEqual(calls[0][:2], ['-i', '-s'])
                self.assertEqual(['-u', '-t', '1'] in calls, not human_active)
                self.assertEqual((root / 'display').exists(), not human_active)

    def test_toggle_controls_preparation_together_with_main_job(self):
        import anchor_mcp as server
        for enable in (False, True):
            with self.subTest(enable=enable), \
                    mock.patch.object(core, 'run', return_value=(0, '')) as run, \
                    mock.patch.object(core, 'status', return_value={}):
                result = json.loads(server.call_tool('anchor_toggle', {'enable': enable})['content'][0]['text'])
                self.assertTrue(result['ok'])
                run.assert_any_call(['launchctl', 'enable' if enable else 'disable', core.SERVICE + '.prepare'])



if __name__ == '__main__':
    unittest.main()
