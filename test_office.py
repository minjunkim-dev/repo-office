"""The office layer must enforce its boundary without changing copied baseline files."""

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
OFFICE = ROOT / "claude-office"
DEPLOY = OFFICE / "tools/deploy.py"


class OfficeBoundaryTests(unittest.TestCase):
    def test_production_is_not_a_deploy_choice(self):
        result = subprocess.run(
            [sys.executable, str(DEPLOY), "--target", "production"],
            capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("plan only", result.stdout)

    def test_staging_plan_does_not_deploy(self):
        result = subprocess.run(
            [sys.executable, str(DEPLOY), "--target", "staging"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "plan only: staging")

    def test_gate_runner_needs_a_task_with_intent(self):
        result = subprocess.run(
            [sys.executable, str(OFFICE / "tools/run_gates.py"), str(OFFICE / "leader")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse((OFFICE / "leader/REPORT.md").exists())

    def test_gate_runner_writes_the_report_into_the_given_task(self):
        spec = importlib.util.spec_from_file_location("run_gates", OFFICE / "tools/run_gates.py")
        run_gates = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(run_gates)
        run_gates.COMMANDS = ([sys.executable, "-c", "pass"],)
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "sample-task"
            task.mkdir()
            (task / "INTENT.md").write_text("# sample-task\n", encoding="utf-8")
            self.assertEqual(run_gates.main([str(task)]), 0)
            report = (task / "REPORT.md").read_text(encoding="utf-8")
        self.assertTrue(report.startswith("# sample-task 관문 보고"))

    def test_copied_manifest_excludes_the_office(self):
        manifest = (ROOT / "templates/repo-baseline.json").read_text(encoding="utf-8")
        self.assertNotIn("claude-office", manifest)

    def test_gate_commands_match_baseline_ci(self):
        workflow = (ROOT / ".github/workflows/check.yml").read_text(encoding="utf-8")
        gates = (OFFICE / "QUALITY_GATES.md").read_text(encoding="utf-8")
        for command in (
            "python3 -m unittest discover -s scripts -p 'test_*.py'",
            "python3 -m unittest discover -s tools -p 'test_*.py'",
            "python3 -m unittest discover -s claude-office -p 'test_*.py'",
        ):
            self.assertIn(command, workflow)
            self.assertIn(command, gates)

    def test_role_rules_and_task_intent_have_required_sections(self):
        for path in (
            OFFICE / "worker-01/AGENTS.md",
            OFFICE / "leader/AGENTS.md",
            OFFICE / "reviewer/AGENTS.md",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertIn("## 할 일", text)
            self.assertIn("## 하지 않을 일", text)
        intent = (OFFICE / "worker-01/tasks/office-smoke/INTENT.md").read_text(encoding="utf-8")
        for heading in ("## 목표", "## 지시 원문", "## 하지 않을 것", "## 결정 기록"):
            self.assertIn(heading, intent)
        memory = (OFFICE / "MEMORY.md").read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(memory), 120)

    def test_rules_live_in_agents_files(self):
        self.assertEqual(list(OFFICE.rglob("CLAUDE.md")), [])
        self.assertIn("`AGENTS.md`", (OFFICE / "AGENTS.md").read_text(encoding="utf-8"))

    def test_worker_does_not_own_baseline_files(self):
        rules = (OFFICE / "worker-01/AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("scripts/", rules)
        self.assertIn("templates/", rules)
        self.assertIn("production", rules)


if __name__ == "__main__":
    unittest.main()
