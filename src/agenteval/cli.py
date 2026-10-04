"""命令行入口：run / list / show。"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from agenteval.fakes import build_demo_registry
from agenteval.models import Run
from agenteval.runner import ContractRunner, load_cases
from agenteval.store import RUNS_DIRNAME, RunStore
from agenteval.tools import ToolRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agenteval",
        description="Agent evaluation platform - deterministic tool contract layer",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=None,
        help="override the run home directory (default: .agenteval)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="execute contract cases and store the run")
    run_parser.add_argument("--cases", type=Path, required=True, help="path to a JSON or YAML cases file")
    source = run_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="use the built-in demo registry")
    source.add_argument(
        "--registry",
        help="registry factory as 'package.module:factory' or 'path/to/module.py:factory'",
    )

    list_parser = subparsers.add_parser("list", help="list stored runs")
    list_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")

    show_parser = subparsers.add_parser("show", help="show a stored run")
    show_parser.add_argument("run_id")
    show_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = _store_from_args(args)
    if args.command == "run":
        return _cmd_run(args, store)
    if args.command == "list":
        return _cmd_list(args, store)
    if args.command == "show":
        return _cmd_show(args, store)
    return 2


def _store_from_args(args: argparse.Namespace) -> RunStore:
    if args.home is not None:
        return RunStore(Path(args.home) / RUNS_DIRNAME)
    return RunStore.default()


def _cmd_run(args: argparse.Namespace, store: RunStore) -> int:
    cases = load_cases(args.cases)
    registry = build_demo_registry() if args.demo else load_registry(args.registry)
    runner = ContractRunner(registry=registry, store=store)
    run = runner.run(cases, metadata={"cases_file": str(args.cases), "registry": "demo" if args.demo else args.registry})
    _print_run(run)
    return 0 if run.summary.failed == 0 and run.summary.errored == 0 else 1


def _cmd_list(args: argparse.Namespace, store: RunStore) -> int:
    run_ids = store.list_runs()
    if args.json:
        print(json.dumps(run_ids, ensure_ascii=False))
        return 0
    if not run_ids:
        print("no runs recorded")
        return 0
    for run_id in run_ids:
        print(run_id)
    return 0


def _cmd_show(args: argparse.Namespace, store: RunStore) -> int:
    try:
        run = store.load(args.run_id)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(run.model_dump_json(indent=2))
    else:
        _print_run(run)
    return 0


def _print_run(run: Run) -> None:
    summary = run.summary
    print(f"run_id: {run.run_id}")
    print(
        f"cases: {summary.total}  pass: {summary.passed}  "
        f"fail: {summary.failed}  error: {summary.errored}"
    )
    for verdict in run.verdicts:
        print(f"  [{verdict.status.value:<5}] {verdict.case_id}  ({verdict.duration_ms:.1f} ms)")
        for check in verdict.failed_checks:
            detail = check.message or "check failed"
            print(f"         - {check.name}: {detail}")
        if verdict.error:
            print(f"         - error: {verdict.error}")


def load_registry(ref: str | None) -> ToolRegistry:
    """按 ``module:factory`` 或 ``path/to/module.py:factory`` 载入工具注册表。"""

    if not ref or ":" not in ref:
        raise SystemExit("--registry must look like 'package.module:factory' or 'path/to/module.py:factory'")
    target, _, attribute = ref.rpartition(":")
    module = _load_module(Path(target)) if _looks_like_path(target) else importlib.import_module(target)
    factory = getattr(module, attribute, None)
    if factory is None:
        raise SystemExit(f"registry factory not found: {attribute}")
    registry = factory() if callable(factory) else factory
    if not isinstance(registry, ToolRegistry):
        raise SystemExit("registry factory must return a ToolRegistry instance")
    return registry


def _looks_like_path(target: str) -> bool:
    return target.endswith(".py") or "/" in target or "\\" in target


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
