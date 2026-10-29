"""Exercise the installed shell entry point against inert tools and a stub CLI."""
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "anchor.sh"


class ExecutionArguments(unittest.TestCase):
    def run_script(self, config, final_reply="ok", diagnostics="stub reply", server_names=(), discovery_error=False, servers=None, linked_skill=False):
        if servers is None:
            servers = [{"name": name, "transport": {"type": "stdio", "command": "/test/server"}}
                       for name in server_names]
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            (root / "anchor.sh").write_bytes(SCRIPT.read_bytes())
            if config is not None:
                (root / "config.json").write_text(json.dumps(config))
            if linked_skill:
                skill = root / "skill-source"
                skill.mkdir()
                (skill / "SKILL.md").write_text("test fixture")
                skills = root / ".agents" / "skills"
                skills.mkdir(parents=True)
                (skills / "linked").symlink_to(skill, target_is_directory=True)
                (skill / "cycle").symlink_to(skills, target_is_directory=True)
            stub = root / "codex-stub"
            stub.write_text('#!/usr/bin/python3\nimport json,sys,pathlib\n'
                            'if sys.argv[1:] == ["mcp", "list", "--json"]:\n'
                            f'    print(json.dumps({servers!r}))\n'
                            f'    sys.exit({1 if discovery_error else 0})\n'
                            'instruction = next(a.split("=", 1)[1] for a in sys.argv if a.startswith("model_instructions_file="))\n'
                            'assert pathlib.Path(json.loads(instruction)).read_text() == "Reply exactly ok.\\n"\n'
                            'json.dump(sys.argv[1:],open("argv.json","w"))\n'
                            f'reply = {final_reply!r}\n'
                            'if reply is not None:\n'
                            '    pathlib.Path(sys.argv[sys.argv.index("--output-last-message")+1]).write_text(reply)\n'
                            f'print({diagnostics!r})\n')
            pmset = root / "pmset-stub"
            pmset.write_text('#!/bin/bash\necho "UserIsActive 1"\n')
            caffeinate = root / "caffeinate-stub"
            caffeinate.write_text('#!/bin/bash\nwhile [[ "$1" == -* ]]; do shift; done\nexec "$@"\n')
            for path in (stub, pmset, caffeinate):
                path.chmod(0o755)
            result = subprocess.run(["/bin/bash", str(root / "anchor.sh")], cwd=root,
                                    env={**os.environ, "CODEX_BIN": str(stub), "PMSET": str(pmset),
                                         "CAFFEINATE": str(caffeinate)},
                                    capture_output=True, text=True, timeout=10)
            args = json.loads((root / "argv.json").read_text()) if (root / "argv.json").exists() else None
            self.assertTrue((root / ".last-run-finished").exists())
            log = (root / "anchor.log").read_text()
            return result.returncode, args, log

    def test_legacy_default_model_and_low_effort(self):
        rc, args, _ = self.run_script(None)
        self.assertEqual(rc, 0)
        self.assertNotIn("--model", args)
        self.assertIn('model_reasoning_effort="low"', args)

    def test_explicit_model_and_effort_reach_cli(self):
        rc, args, _ = self.run_script({"model": "test/model", "reasoning_effort": "high"})
        self.assertEqual(rc, 0)
        self.assertEqual(args[args.index("--model") + 1], "test/model")
        self.assertIn('model_reasoning_effort="high"', args)
        self.assertEqual(args[args.index("-s") + 1], "read-only")
        self.assertIn("--ephemeral", args)
        for override in ["project_doc_max_bytes=0", "features.apps=false", "features.plugins=false",
                         "features.memories=false", "features.shell_tool=false"]:
            self.assertIn(override, args)
        self.assertEqual(args[-1], "回复ok")
        self.assertNotIn("README", args[-1])

    def test_success_requires_final_acknowledgement_without_readme(self):
        rc, _, log = self.run_script(None)
        self.assertEqual(rc, 0)
        self.assertIn("OK    exit=0", log)
        self.assertIn("reply: ok", log)

    def test_exit_zero_with_diagnostics_but_no_reply_is_not_success(self):
        rc, _, log = self.run_script(None, final_reply=None,
                                    diagnostics="head: README.md: No such file or directory")
        self.assertEqual(rc, 1)
        self.assertIn("missing or unexpected final acknowledgement", log)
        self.assertNotIn("OK    exit=0", log)

    def test_acknowledgement_in_diagnostics_does_not_validate_wrong_final_reply(self):
        rc, _, log = self.run_script(None, final_reply="Could not read the file", diagnostics="ok")
        self.assertEqual(rc, 1)
        self.assertNotIn("OK    exit=0", log)

    def test_empty_values_inherit_cli_configuration(self):
        rc, args, _ = self.run_script({"model": "", "reasoning_effort": ""})
        self.assertEqual(rc, 0)
        self.assertNotIn("--model", args)
        self.assertFalse(any(a.startswith("model_reasoning_effort=") for a in args))

    def test_model_with_spaces_unicode_and_shell_syntax_stays_one_argument(self):
        model = 'test/模型 name $(touch SHOULD_NOT_EXIST); "quoted"'
        rc, args, _ = self.run_script({"model": model, "reasoning_effort": "low"})
        self.assertEqual(rc, 0)
        self.assertEqual(args[args.index("--model") + 1], model)

    def test_invalid_settings_never_invoke_cli(self):
        for config in [{"model": "--bad"}, {"model": "bad\nmodel"}, {"reasoning_effort": "oops"}]:
            with self.subTest(config=config):
                rc, args, log = self.run_script(config)
                self.assertEqual(rc, 1)
                self.assertIsNone(args)
                self.assertIn("invalid execution settings", log)

    def test_disables_all_cli_resolved_server_names_in_one_table(self):
        names = ["plain", "quoted name", "literal.dot", 'quote"key', "服务🔧"]
        rc, args, _ = self.run_script(None, server_names=names)
        self.assertEqual(rc, 0)
        override = next(a for a in args if a.startswith("mcp_servers="))
        for name in names:
            self.assertIn(json.dumps(name, ensure_ascii=False) + '={enabled=false,command="/test/server"}', override)
        self.assertFalse(any(a.startswith("mcp_servers.") for a in args))

    def test_preserves_http_transport_when_disabling(self):
        servers = [{"name": "http.server", "transport": {
            "type": "streamable_http", "url": "https://example.test/mcp"}}]
        rc, args, _ = self.run_script(None, servers=servers)
        self.assertEqual(rc, 0)
        override = next(a for a in args if a.startswith("mcp_servers="))
        self.assertIn('"http.server"={enabled=false,url="https://example.test/mcp"}', override)
        self.assertNotIn("command=", override)

    def test_unknown_transport_fails_before_model_request(self):
        rc, args, _ = self.run_script(None, servers=[{"name": "bad", "transport": {"type": "unknown"}}])
        self.assertEqual(rc, 1)
        self.assertIsNone(args)

    def test_minimal_context_overrides_are_per_invocation(self):
        rc, args, _ = self.run_script(None, linked_skill=True)
        self.assertEqual(rc, 0)
        for value in ['developer_instructions=""', 'features.multi_agent=false',
                      'features.skill_search=false', 'features.code_mode_host=false',
                      'features.sleep_tool=false', 'features.view_image=false',
                      'features.image_generation=false', 'web_search="disabled"']:
            self.assertIn(value, args)
        disabled = next(a for a in args if a.startswith("skills.config="))
        self.assertIn('/skill-source/SKILL.md",enabled=false}', disabled)
        self.assertEqual(disabled.count('/skill-source/SKILL.md"'), 1)

    def test_failed_mcp_discovery_never_starts_model_request(self):
        rc, args, log = self.run_script(None, discovery_error=True)
        self.assertEqual(rc, 1)
        self.assertIsNone(args)
        self.assertNotIn("OK    exit=0", log)


if __name__ == "__main__":
    unittest.main()
