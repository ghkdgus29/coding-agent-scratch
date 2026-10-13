"""v01: 최소 코딩 에이전트 (mini-swe-agent 구조를 그대로 따라함).

에이전트 = 다음을 반복하는 while 루프
    1. LLM에 지금까지의 대화를 보낸다            (query)
    2. 응답에서 bash 명령 하나를 뽑는다           (parse_action)
    3. 명령을 실행한다                           (execute)
    4. 실행 결과를 "user" 메시지로 대화에 붙인다   (observation)
LLM이 특수 명령(echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT)을 실행하면 끝난다.

LLM은 텍스트만 출력할 수 있으므로, "행동"은 약속된 텍스트 포맷(```bash 블록)으로 표현한다.
"""

import os
import platform
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

SYSTEM_TEMPLATE = """You are a helpful assistant that can interact with a computer.

Your response must contain exactly ONE bash code block with ONE command (or commands connected with && or ||).
Include a THOUGHT section before your command where you explain your reasoning process.
Format your response as shown in <format_example>.

<format_example>
THOUGHT: Your reasoning and analysis here.

```bash
your_command_here
```
</format_example>

Failure to follow these rules will cause your response to be rejected."""

INSTANCE_TEMPLATE = """Please solve this task: {task}

You can execute bash commands and edit files to implement the necessary changes.

## Recommended Workflow
1. Analyze the codebase by finding and reading relevant files
2. Create a script to reproduce the issue (if it is a bug)
3. Edit the source code to resolve the issue
4. Verify your fix works by running your script again
5. When you are done, issue the following command: `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`
   Do not combine it with any other command. After this command, you cannot continue working.

## Important Rules
1. Every response must contain exactly one action, enclosed in ```bash ... ```
2. Directory or environment variable changes are not persistent. Every action is executed in a new subshell.
   However, you can prefix any action with `cd /path && ...`
3. To create or edit files, use shell tools, e.g. `cat <<'EOF' > file.py` or `sed -i{sed_flag} 's/old/new/' file.py`
4. View files with line numbers: `nl -ba file.py | sed -n '10,40p'`

<system_information>
{system} {release} {machine}  (cwd: {cwd})
</system_information>"""

OBSERVATION_TEMPLATE = """<returncode>{returncode}</returncode>
<output>
{output}
</output>"""

FORMAT_ERROR_TEMPLATE = """Format error: expected exactly 1 ```bash block, found {n}.
If you want to end the task, run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` alone.
Otherwise respond with a THOUGHT and exactly one ```bash block."""

ACTION_REGEX = r"```bash\s*\n(.*?)\n```"
SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


# --- 흐름 제어용 예외들 (mini-swe-agent의 exceptions.py와 같은 아이디어) -----------------
class NonTerminating(Exception):
    """루프는 계속되지만 이번 step은 중단: 메시지를 대화에 붙이고 다음 step으로."""


class FormatError(NonTerminating):
    pass


class Terminating(Exception):
    """루프 종료."""


class Submitted(Terminating):
    pass


class LimitsExceeded(Terminating):
    pass


@dataclass
class AgentConfig:
    step_limit: int = 30
    timeout: int = 60  # 명령 하나당 초
    cwd: str = "."
    confirm: bool = True  # 실행 전에 사람에게 물어볼지 (v05에서 정식 승인 정책으로 발전)


class Agent:
    def __init__(self, llm: Callable[[str, list[dict]], str], config: AgentConfig, log: Callable[[str], None] = print,
                 confirm_fn: Callable[[str], bool] | None = None):
        self.llm = llm
        self.config = config
        self.log = log
        self.confirm_fn = confirm_fn or _ask_yes_no
        self.system = ""
        self.messages: list[dict] = []
        self.n_steps = 0

    # ---------------------------------------------------------------- run / step
    def run(self, task: str) -> dict:
        u = platform.uname()
        self.system = SYSTEM_TEMPLATE
        first = INSTANCE_TEMPLATE.format(
            task=task, system=u.system, release=u.release, machine=u.machine,
            cwd=os.path.abspath(self.config.cwd), sed_flag=" ''" if u.system == "Darwin" else "",
        )
        self.messages = [{"role": "user", "content": first}]
        while True:
            try:
                self.step()
            except NonTerminating as e:
                self.add("user", str(e))
            except Terminating as e:
                return {"exit_status": type(e).__name__, "result": str(e), "steps": self.n_steps}

    def step(self) -> None:
        response = self.query()
        action = self.parse_action(response)
        output = self.execute(action)
        self.add("user", OBSERVATION_TEMPLATE.format(**output))

    # ---------------------------------------------------------------- 구성 요소
    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})

    def query(self) -> str:
        if 0 < self.config.step_limit <= self.n_steps:
            raise LimitsExceeded(f"step limit {self.config.step_limit} reached")
        self.n_steps += 1
        text = self.llm(self.system, self.messages)
        self.add("assistant", text)
        self.log(f"\n[step {self.n_steps}] assistant:\n{text}")
        return text

    def parse_action(self, text: str) -> str:
        actions = re.findall(ACTION_REGEX, text, re.DOTALL)
        if len(actions) != 1:
            raise FormatError(FORMAT_ERROR_TEMPLATE.format(n=len(actions)))
        return actions[0].strip()

    def execute(self, command: str) -> dict:
        if self.config.confirm and not self.confirm_fn(command):
            raise NonTerminating("The user rejected this command. Try a different approach.")
        try:
            r = subprocess.run(
                command, shell=True, cwd=self.config.cwd, timeout=self.config.timeout,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
            )
            output = {"returncode": r.returncode, "output": r.stdout}
        except subprocess.TimeoutExpired as e:
            partial = e.output.decode(errors="replace") if isinstance(e.output, bytes) else (e.output or "")
            output = {"returncode": -1, "output": partial + f"\n[command timed out after {self.config.timeout}s]"}
        self.log(f"[returncode {output['returncode']}]\n{output['output'][-2000:]}")
        self._check_finished(output)
        return output

    def _check_finished(self, output: dict) -> None:
        lines = output["output"].lstrip().splitlines()
        if lines and lines[0].strip() == SUBMIT_MARKER and output["returncode"] == 0:
            raise Submitted("\n".join(lines[1:]))


def _ask_yes_no(command: str) -> bool:
    ans = input(f"\n실행할까요? $ {command}\n[y/N] ").strip().lower()
    return ans in ("y", "yes")
