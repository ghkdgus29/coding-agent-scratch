import tempfile
import unittest
from pathlib import Path

from agent import SUBMIT_MARKER, Agent, AgentConfig
from fake_llm import FakeLLM, bash


def make_agent(responses, **cfg):
    tmp = tempfile.mkdtemp()
    fake = FakeLLM(responses)
    agent = Agent(fake, AgentConfig(cwd=tmp, confirm=False, **cfg), log=lambda *_: None)
    return agent, fake, Path(tmp)


class TestLoop(unittest.TestCase):
    def test_creates_file_and_submits(self):
        agent, fake, tmp = make_agent([
            bash("echo 'print(1+1)' > a.py"),
            bash("python3 a.py"),
            bash(f"echo {SUBMIT_MARKER}"),
        ])
        result = agent.run("make a.py")
        self.assertEqual(result["exit_status"], "Submitted")
        self.assertEqual(result["steps"], 3)
        self.assertTrue((tmp / "a.py").exists())
        # 3번째 호출 시점의 대화에는 python 실행 결과(2)가 observation으로 들어가 있어야 한다
        last_obs = fake.calls[2][-1]["content"]
        self.assertIn("<returncode>0</returncode>", last_obs)
        self.assertIn("2", last_obs)

    def test_format_error_is_fed_back(self):
        agent, fake, _ = make_agent([
            "I will just talk without a code block",
            bash(f"echo {SUBMIT_MARKER}"),
        ])
        result = agent.run("x")
        self.assertEqual(result["exit_status"], "Submitted")
        self.assertIn("Format error", fake.calls[1][-1]["content"])

    def test_two_blocks_is_format_error(self):
        agent, fake, _ = make_agent([
            bash("ls") + "\n" + bash("pwd"),
            bash(f"echo {SUBMIT_MARKER}"),
        ])
        agent.run("x")
        self.assertIn("found 2", fake.calls[1][-1]["content"])

    def test_step_limit(self):
        agent, _, _ = make_agent([bash("true")] * 10, step_limit=3)
        result = agent.run("x")
        self.assertEqual(result["exit_status"], "LimitsExceeded")
        self.assertEqual(result["steps"], 3)

    def test_nonzero_returncode_is_observation_not_crash(self):
        agent, fake, _ = make_agent([bash("exit 3"), bash(f"echo {SUBMIT_MARKER}")])
        agent.run("x")
        self.assertIn("<returncode>3</returncode>", fake.calls[1][-1]["content"])

    def test_timeout(self):
        agent, fake, _ = make_agent([bash("sleep 5"), bash(f"echo {SUBMIT_MARKER}")], timeout=1)
        agent.run("x")
        self.assertIn("timed out", fake.calls[1][-1]["content"])

    def test_rejected_by_user(self):
        fake = FakeLLM([bash("rm -rf something"), bash(f"echo {SUBMIT_MARKER}")])
        agent = Agent(fake, AgentConfig(cwd=tempfile.mkdtemp(), confirm=True), log=lambda *_: None,
                      confirm_fn=lambda cmd: "rm" not in cmd)
        agent.run("x")
        self.assertIn("rejected", fake.calls[1][-1]["content"])


if __name__ == "__main__":
    unittest.main()
