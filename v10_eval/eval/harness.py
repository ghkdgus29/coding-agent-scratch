"""v10: 에이전트 평가 하네스 (SWE-bench 방식의 축소판).

과제 하나 = tasks/<name>/
    task.json        {"instruction": 에이전트에게 줄 작업, "fail_to_pass": 명령, "pass_to_pass": 명령(선택)}
    repo/            시작 상태의 코드
    hidden_tests/    에이전트가 끝난 뒤에야 복사되는 채점용 테스트 (에이전트는 볼 수 없다)
    solution/        정답 파일들 (--oracle 로 과제 자체가 올바른지 검증할 때만 사용)

채점
    FAIL_TO_PASS: 숨겨진 테스트가 "작업 전에는 실패, 작업 후에는 통과"해야 한다 → 요구사항을 해결했는가
    PASS_TO_PASS: 기존 테스트가 "작업 후에도 통과"해야 한다              → 다른 걸 망가뜨리지 않았는가

사용
    uv run python eval/harness.py --oracle                                   # 과제 검증 (API 불필요)
    uv run python eval/harness.py --agent v10_eval --model anthropic:claude-opus-5-5
    uv run python eval/harness.py --agent v01_minimal_loop,v04_editing,v10_eval --tasks fix_off_by_one,parse_duration
    uv run python eval/harness.py --agent v10_eval --repeat 3                # 같은 과제 3번 (LLM은 비결정적)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_DIR.parent.parent  # 버전 폴더들이 있는 곳
TASKS_DIR = EVAL_DIR / "tasks"
RESULTS_DIR = EVAL_DIR / "results"
HIDDEN = "_hidden_tests"


@dataclass
class Task:
    name: str
    path: Path
    instruction: str
    fail_to_pass: str
    pass_to_pass: str = ""

    @staticmethod
    def load(path: Path) -> Task:
        spec = json.loads((path / "task.json").read_text())
        return Task(path.name, path, spec["instruction"], spec["fail_to_pass"], spec.get("pass_to_pass", ""))


@dataclass
class Result:
    task: str
    agent: str
    resolved: bool
    fail_to_pass: bool
    pass_to_pass: bool
    seconds: float
    agent_exit: int | None = None
    stats: dict = field(default_factory=dict)
    error: str = ""


def sh(command: str, cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(command, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)


def install_hidden_tests(task: Task, workdir: Path) -> None:
    dst = workdir / HIDDEN
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(task.path / "hidden_tests", dst)
    (dst / "__init__.py").touch()


def grade(task: Task, workdir: Path) -> tuple:
    install_hidden_tests(task, workdir)
    f2p = sh(task.fail_to_pass, workdir).returncode == 0
    p2p = sh(task.pass_to_pass, workdir).returncode == 0 if task.pass_to_pass else True
    shutil.rmtree(workdir / HIDDEN, ignore_errors=True)
    return f2p, p2p


def prepare(task: Task) -> Path:
    workdir = Path(tempfile.mkdtemp(prefix=f"eval-{task.name}-"))
    shutil.copytree(task.path / "repo", workdir, dirs_exist_ok=True)
    return workdir


def agent_flags(agent_dir: Path) -> set:
    """버전마다 지원하는 CLI 옵션이 다르다 → --help를 보고 판단."""
    out = subprocess.run([sys.executable, "main.py", "--help"], cwd=agent_dir, capture_output=True, text=True).stdout
    return {f for f in ("--stats-json", "--step-limit", "--time-limit", "--effort") if f in out}


def run_agent(agent: str, task: Task, workdir: Path, args) -> Result:
    agent_dir = (PROJECT_ROOT / agent).resolve()
    flags = agent_flags(agent_dir)
    stats_file = workdir.parent / f"{workdir.name}-stats.json"
    cmd = [sys.executable, "main.py", task.instruction, "--cwd", str(workdir), "--model", args.model, "--yes"]
    if "--step-limit" in flags:
        cmd += ["--step-limit", str(args.step_limit)]
    if "--stats-json" in flags:
        cmd += ["--stats-json", str(stats_file)]
    if "--effort" in flags and args.effort:
        cmd += ["--effort", args.effort]
    start = time.time()
    error, exit_code = "", None
    log_path = RESULTS_DIR / "logs" / f"{agent}-{task.name}-{int(start)}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log_path, "w") as log:
            p = subprocess.run(cmd, cwd=agent_dir, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               timeout=args.time_limit, env={**os.environ, "NO_COLOR": "1"})
        exit_code = p.returncode
    except subprocess.TimeoutExpired:
        error = f"agent timed out after {args.time_limit}s"
    seconds = time.time() - start
    stats = json.loads(stats_file.read_text()) if stats_file.exists() else {}
    f2p, p2p = grade(task, workdir)
    return Result(task.name, agent, f2p and p2p, f2p, p2p, round(seconds, 1), exit_code,
                  {k: stats.get(k) for k in ("exit_status", "steps", "input_tokens", "output_tokens", "cost_usd")},
                  error or ("" if exit_code == 0 else f"see {log_path}"))


def run_oracle(task: Task) -> Result:
    """정답을 적용해서 과제가 올바른지 확인: 시작 상태에선 실패, 정답 적용 후엔 통과해야 한다."""
    workdir = prepare(task)
    f2p_before, _ = grade(task, workdir)
    shutil.copytree(task.path / "solution", workdir, dirs_exist_ok=True)
    f2p, p2p = grade(task, workdir)
    ok = (not f2p_before) and f2p and p2p
    err = "" if ok else ("hidden tests already pass before the fix" if f2p_before else "solution does not pass")
    return Result(task.name, "oracle", ok, f2p, p2p, 0.0, error=err)


def print_table(results: list[Result]) -> None:
    print(f"\n{'agent':<20} {'task':<22} {'resolved':<9} {'f2p':<5} {'p2p':<5} {'sec':>6} {'steps':>6} {'cost':>9}")
    for r in results:
        cost = r.stats.get("cost_usd")
        print(f"{r.agent:<20} {r.task:<22} {('✅' if r.resolved else '❌'):<9} {str(r.fail_to_pass)[0]:<5} "
              f"{str(r.pass_to_pass)[0]:<5} {r.seconds:>6} {str(r.stats.get('steps') or '-'):>6} "
              f"{(f'${cost:.4f}') if cost is not None else '-':>9}" + (f"  {r.error}" if r.error and not r.resolved else ""))
    print("\nsummary")
    for agent in dict.fromkeys(r.agent for r in results):
        rs = [r for r in results if r.agent == agent]
        costs = [r.stats.get("cost_usd") for r in rs if r.stats.get("cost_usd") is not None]
        cost = f", total ${sum(costs):.4f}" if costs else ""
        print(f"  {agent:<20} {sum(r.resolved for r in rs)}/{len(rs)} resolved "
              f"({100 * sum(r.resolved for r in rs) / len(rs):.0f}%), avg {sum(r.seconds for r in rs) / len(rs):.0f}s{cost}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="v10_eval", help="버전 폴더 이름 (쉼표로 여러 개 비교)")
    ap.add_argument("--model", default="anthropic:claude-opus-5-5")
    ap.add_argument("--effort", default=None)
    ap.add_argument("--tasks", default="", help="쉼표로 구분한 과제 이름 (기본: 전부)")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--step-limit", type=int, default=40)
    ap.add_argument("--time-limit", type=int, default=900, help="과제당 에이전트 실행 시간 제한(초)")
    ap.add_argument("--oracle", action="store_true", help="정답으로 과제 자체를 검증 (API 불필요)")
    args = ap.parse_args()

    names = [n for n in args.tasks.split(",") if n] or sorted(p.name for p in TASKS_DIR.iterdir() if p.is_dir())
    tasks = [Task.load(TASKS_DIR / n) for n in names]
    results: list[Result] = []
    if args.oracle:
        results = [run_oracle(t) for t in tasks]
    else:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out = RESULTS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        for agent in args.agent.split(","):
            for t in tasks:
                for _ in range(args.repeat):
                    print(f"[{agent}] {t.name} ...", flush=True)
                    workdir = prepare(t)
                    r = run_agent(agent, t, workdir, args)
                    results.append(r)
                    with open(out, "a") as f:
                        f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
                    shutil.rmtree(workdir, ignore_errors=True)
        print(f"\nresults → {out}")
    print_table(results)
    if args.oracle and not all(r.resolved for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
