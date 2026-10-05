"""命令行入口：run / list / show。"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from agenteval.cassette import (
    CASSETTES_DIRNAME,
    DEFAULT_SENSITIVE_FIELDS,
    Cassette,
    CassetteMode,
    CassetteSession,
    CassetteStore,
    wrap_registry,
)
from agenteval.audit_import import (
    AuditImportError,
    ImportReport,
    build_trace,
    fidelity_report,
    load_audit_records,
)
from agenteval.console.server import DEFAULT_HOST, DEFAULT_PORT, create_server
from agenteval.fakes import build_demo_registry
from agenteval.gate import (
    BASELINES_DIRNAME,
    DEFAULT_BASELINE_NAME,
    Baseline,
    BaselineStore,
    GateThresholds,
    evaluate,
    format_report,
)
from agenteval.judge import (
    JudgeThresholds,
    calibrate as calibrate_judge,
    format_report as format_judge_report,
    load_gold_set,
)
from agenteval.load import load_scenarios, run_scenarios
from agenteval.reports import (
    KIND_GATE,
    KIND_JUDGE,
    KIND_TRIAGE,
    ConclusionStore,
    write_conclusion,
)
from agenteval.reports import REPORTS_DIRNAME
from agenteval.models import Run
from agenteval.models import AnyCase
from agenteval.runner import ContractRunner, load_cases
from agenteval.store import RUNS_DIRNAME, RunStore
from agenteval.tasks import TaskRunner, load_tasks
from agenteval.trace_store import TRACES_DIRNAME, TraceStore, extract_trace
from agenteval.tools import ToolRegistry
from agenteval.triage import (
    DEFAULT_TRACE_PREFIX,
    format_report as format_triage_report,
    triage_run,
    write_candidates,
)


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
    run_parser.add_argument("--cassette", help="cassette name for recording or replay")
    run_parser.add_argument(
        "--trace",
        help="evaluate process cases against a stored trace instead of driving their steps",
    )
    run_parser.add_argument(
        "--cassette-mode",
        choices=[mode.value for mode in CassetteMode],
        help="required with --cassette: record, replay, or auto",
    )
    run_parser.add_argument(
        "--strict-cassette",
        action="store_true",
        help="fail the run when recorded interactions are left unused",
    )
    run_parser.add_argument(
        "--redact",
        action="append",
        default=[],
        metavar="FIELD",
        help="extra top-level argument name to redact (repeatable)",
    )

    list_parser = subparsers.add_parser("list", help="list stored runs")
    list_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")

    show_parser = subparsers.add_parser("show", help="show a stored run")
    show_parser.add_argument("run_id")
    show_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")

    baseline_parser = subparsers.add_parser(
        "baseline", help="capture a stored run as the gate baseline"
    )
    baseline_parser.add_argument("--run", dest="run_id", help="run id (default: latest)")
    baseline_parser.add_argument("--name", help=f"baseline name (default: {DEFAULT_BASELINE_NAME})")
    baseline_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")

    gate_parser = subparsers.add_parser(
        "gate", help="evaluate a stored run against thresholds and the baseline"
    )
    gate_parser.add_argument("--run", dest="run_id", help="run id (default: latest)")
    gate_parser.add_argument("--name", help=f"baseline name (default: {DEFAULT_BASELINE_NAME})")
    gate_parser.add_argument(
        "--min-pass-rate", type=float, default=1.0, help="minimum pass rate (default: 1.0)"
    )
    gate_parser.add_argument(
        "--max-errors", type=int, default=0, help="maximum errored cases (default: 0)"
    )
    gate_parser.add_argument(
        "--allow-regressions",
        action="store_true",
        help="do not fail the gate on regressions or missing cases",
    )
    gate_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")

    trace_parser = subparsers.add_parser("trace", help="manage stored traces")
    trace_subparsers = trace_parser.add_subparsers(dest="trace_command", required=True)
    trace_save_parser = trace_subparsers.add_parser(
        "save", help="capture a trace from a stored run"
    )
    trace_save_parser.add_argument("--name", required=True, help="trace name to write")
    trace_save_parser.add_argument("--run", dest="run_id", help="run id (default: latest)")
    trace_save_parser.add_argument("--case", dest="case_id", help="case id whose trace to capture")
    trace_list_parser = trace_subparsers.add_parser("list", help="list stored traces")
    trace_list_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    trace_import_parser = trace_subparsers.add_parser(
        "import", help="import tool-call audit records as a named trace"
    )
    trace_import_parser.add_argument(
        "--from",
        dest="source",
        type=Path,
        required=True,
        help="audit export file (JSON or CSV)",
    )
    trace_import_parser.add_argument("--name", required=True, help="trace name to write")
    trace_import_parser.add_argument(
        "--case-id", help="case id for the imported trace (default: the trace name)"
    )
    trace_import_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")

    task_parser = subparsers.add_parser("task", help="run end-to-end tasks")
    task_subparsers = task_parser.add_subparsers(dest="task_command", required=True)
    task_run_parser = task_subparsers.add_parser("run", help="execute tasks against an agent")
    task_run_parser.add_argument("--tasks", type=Path, required=True, help="path to a JSON or YAML tasks file")
    task_run_parser.add_argument(
        "--registry",
        required=True,
        help="environment factory as 'package.module:factory' or 'path/to/module.py:factory'",
    )
    task_run_parser.add_argument(
        "--agent",
        required=True,
        help="agent factory as 'package.module:factory' or 'path/to/module.py:factory'",
    )
    task_run_parser.add_argument("--json", action="store_true", help="emit the task report as JSON")

    triage_parser = subparsers.add_parser(
        "triage", help="attribute failures and reflow them into regression cases"
    )
    triage_parser.add_argument("--run", dest="run_id", help="run id (default: latest)")
    triage_parser.add_argument(
        "--cases", type=Path, help="original cases file (default: the one recorded in the run)"
    )
    triage_parser.add_argument(
        "--emit", type=Path, help="write reflowable candidate cases to this file"
    )
    triage_parser.add_argument(
        "--trace-prefix",
        default=DEFAULT_TRACE_PREFIX,
        help=f"prefix for saved evidence traces (default: {DEFAULT_TRACE_PREFIX})",
    )
    triage_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")

    judge_parser = subparsers.add_parser("judge", help="calibrate an answer-quality judge")
    judge_subparsers = judge_parser.add_subparsers(dest="judge_command", required=True)
    judge_calibrate_parser = judge_subparsers.add_parser(
        "calibrate", help="measure judge agreement and bias against a gold set"
    )
    judge_calibrate_parser.add_argument(
        "--gold", type=Path, required=True, help="pairwise gold set (JSON or YAML)"
    )
    judge_calibrate_parser.add_argument(
        "--judge", required=True, help="judge factory as 'package.module:factory'"
    )
    judge_calibrate_parser.add_argument("--min-agreement", type=float, default=0.8)
    judge_calibrate_parser.add_argument("--max-position-flip", type=float, default=0.2)
    judge_calibrate_parser.add_argument("--max-length-bias", type=float, default=0.8)
    judge_calibrate_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")

    serve_parser = subparsers.add_parser("serve", help="open the local read-only console")
    serve_parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind address (default: {DEFAULT_HOST})")
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default: {DEFAULT_PORT})")

    load_parser = subparsers.add_parser("load", help="run HTTP load scenarios")
    load_subparsers = load_parser.add_subparsers(dest="load_command", required=True)
    load_run_parser = load_subparsers.add_parser("run", help="execute load scenarios")
    load_run_parser.add_argument(
        "--scenarios", type=Path, required=True, help="scenario file (JSON or YAML)"
    )
    load_run_parser.add_argument(
        "--base-url", help="override the base address of scenario targets"
    )
    load_run_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
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
    if args.command == "baseline":
        return _cmd_baseline(args, store)
    if args.command == "gate":
        return _cmd_gate(args, store)
    if args.command == "trace":
        return _cmd_trace(args, store)
    if args.command == "task":
        return _cmd_task(args, store)
    if args.command == "triage":
        return _cmd_triage(args, store)
    if args.command == "judge":
        return _cmd_judge(args)
    if args.command == "serve":
        return _cmd_serve(args, store)
    if args.command == "load":
        return _cmd_load(args, store)
    return 2


def _store_from_args(args: argparse.Namespace) -> RunStore:
    if args.home is not None:
        return RunStore(Path(args.home) / RUNS_DIRNAME)
    return RunStore.default()


def _cmd_run(args: argparse.Namespace, store: RunStore) -> int:
    if args.cassette_mode and not args.cassette:
        print("--cassette-mode requires --cassette", file=sys.stderr)
        return 2
    if args.cassette and not args.cassette_mode:
        print(
            "--cassette requires an explicit --cassette-mode (record, replay, or auto)",
            file=sys.stderr,
        )
        return 2

    trace_override = None
    if args.trace:
        try:
            trace_override = _trace_store_from_args(args).load(args.trace)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    try:
        cases = load_cases(args.cases)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    registry = build_demo_registry() if args.demo else load_registry(args.registry)
    session = _build_session(args)
    if session is not None:
        if session.mode is CassetteMode.REPLAY and not session.cassette.interactions:
            print(
                f"cassette '{session.cassette.name}' carries no recorded interactions",
                file=sys.stderr,
            )
            return 2
        registry = wrap_registry(registry, session)

    metadata = {
        "cases_file": str(args.cases),
        "registry": "demo" if args.demo else args.registry,
    }
    if session is not None:
        metadata["cassette_name"] = session.cassette.name
        metadata["cassette_mode"] = session.mode.value

    runner = ContractRunner(
        registry=registry,
        store=store,
        cassette_session=session,
        trace_override=trace_override,
        trace_name=args.trace,
        trace_resolver=_trace_store_from_args(args).load,
    )
    run = runner.run(cases, metadata=metadata)
    _print_run(run)
    return 0 if run.summary.failed == 0 and run.summary.errored == 0 else 1


def _build_session(args: argparse.Namespace) -> CassetteSession | None:
    if not args.cassette:
        return None
    mode = CassetteMode(args.cassette_mode)
    cassette_store = _cassette_store_from_args(args)
    sensitive = DEFAULT_SENSITIVE_FIELDS | set(args.redact)
    if mode is CassetteMode.RECORD:
        if cassette_store.exists(args.cassette):
            print(f"warning: overwriting existing cassette '{args.cassette}'", file=sys.stderr)
        cassette_store.reset(args.cassette)
        cassette = Cassette(name=args.cassette, sensitive=sensitive)
    else:
        cassette = cassette_store.load(args.cassette, sensitive=sensitive)
    return CassetteSession(
        cassette=cassette,
        mode=mode,
        store=cassette_store,
        strict=args.strict_cassette,
    )


def _cassette_store_from_args(args: argparse.Namespace) -> CassetteStore:
    if args.home is not None:
        return CassetteStore(Path(args.home) / CASSETTES_DIRNAME)
    return CassetteStore.default()


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


def _cmd_baseline(args: argparse.Namespace, store: RunStore) -> int:
    run_id = args.run_id or _latest_run_id(store)
    if run_id is None:
        print("no runs recorded", file=sys.stderr)
        return 2
    try:
        run = store.load(run_id)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    baseline = Baseline.from_run(run, name=args.name or DEFAULT_BASELINE_NAME)
    path = _baseline_store_from_args(args).save(baseline)
    if args.json:
        print(baseline.model_dump_json(indent=2))
    else:
        print(f"baseline '{baseline.name}' captured from run {baseline.run_id}")
        print(f"cases: {len(baseline.verdicts)}")
        print(f"stored: {path}")
    return 0


def _cmd_gate(args: argparse.Namespace, store: RunStore) -> int:
    run_id = args.run_id or _latest_run_id(store)
    if run_id is None:
        print("no runs recorded", file=sys.stderr)
        return 2
    try:
        run = store.load(run_id)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    baseline_name = args.name or DEFAULT_BASELINE_NAME
    baseline = _baseline_store_from_args(args).load(baseline_name)
    if args.name is not None and baseline is None:
        print(f"baseline not found: {baseline_name}", file=sys.stderr)
        return 2

    result = evaluate(
        run,
        baseline=baseline,
        thresholds=GateThresholds(
            min_pass_rate=args.min_pass_rate,
            max_errors=args.max_errors,
        ),
        allow_regressions=args.allow_regressions,
    )
    _record_conclusion(
        args,
        kind=KIND_GATE,
        title=f"gate {result.run_id}",
        passed=result.passed,
        run_id=result.run_id,
        summary={
            "run_id": result.run_id,
            "pass_rate": result.pass_rate,
            "failed": result.failed,
            "errored": result.errored,
            "regressions": result.regressions,
            "missing": result.missing,
        },
        payload=result.model_dump(mode="json"),
    )
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print(format_report(result))
    return 0 if result.passed else 1


def _latest_run_id(store: RunStore) -> str | None:
    return store.latest_run_id()


def _baseline_store_from_args(args: argparse.Namespace) -> BaselineStore:
    if args.home is not None:
        return BaselineStore(Path(args.home) / BASELINES_DIRNAME)
    return BaselineStore.default()


def _cmd_trace(args: argparse.Namespace, store: RunStore) -> int:
    if args.trace_command == "save":
        return _cmd_trace_save(args, store)
    if args.trace_command == "list":
        return _cmd_trace_list(args)
    if args.trace_command == "import":
        return _cmd_trace_import(args)
    return 2


def _cmd_trace_import(args: argparse.Namespace) -> int:
    try:
        records = load_audit_records(args.source)
    except FileNotFoundError:
        print(f"audit file not found: {args.source}", file=sys.stderr)
        return 2
    except AuditImportError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"audit file is not valid JSON: {exc}", file=sys.stderr)
        return 2

    case_id = args.case_id or args.name
    trace = build_trace(records, case_id)
    path = _trace_store_from_args(args).save(
        args.name, trace, source=f"import:{args.source}"
    )
    report = ImportReport(
        records=len(records),
        case_id=case_id,
        trace_name=args.name,
        limitations=fidelity_report(records),
    )
    if args.json:
        print(report.model_dump_json(indent=2))
        return 0
    print(f"trace '{args.name}' imported from {args.source}")
    print(f"records: {report.records}  case: {case_id}  events: {len(trace.events)}")
    for limitation in report.limitations:
        print(f"limitation: {limitation}")
    print(f"stored: {path}")
    return 0


def _cmd_trace_save(args: argparse.Namespace, store: RunStore) -> int:
    run_id = args.run_id or _latest_run_id(store)
    if run_id is None:
        print("no runs recorded", file=sys.stderr)
        return 2
    try:
        run = store.load(run_id)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        trace = extract_trace(run, args.case_id)
    except KeyError as exc:
        print(exc.args[0] if exc.args else exc, file=sys.stderr)
        return 2
    path = _trace_store_from_args(args).save(args.name, trace, source=f"run:{run.run_id}")
    print(f"trace '{args.name}' captured from run {run.run_id} (case {trace.case_id})")
    print(f"events: {len(trace.events)}")
    print(f"stored: {path}")
    return 0


def _cmd_trace_list(args: argparse.Namespace) -> int:
    names = _trace_store_from_args(args).list_traces()
    if args.json:
        print(json.dumps(names, ensure_ascii=False))
        return 0
    if not names:
        print("no traces recorded")
        return 0
    for name in names:
        print(name)
    return 0


def _trace_store_from_args(args: argparse.Namespace) -> TraceStore:
    if args.home is not None:
        return TraceStore(Path(args.home) / TRACES_DIRNAME)
    return TraceStore.default()


def _cmd_task(args: argparse.Namespace, store: RunStore) -> int:
    if args.task_command == "run":
        return _cmd_task_run(args, store)
    return 2


def _cmd_task_run(args: argparse.Namespace, store: RunStore) -> int:
    try:
        tasks = load_tasks(args.tasks)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    environment = load_factory(args.registry, "registry")
    agent_factory = load_factory(args.agent, "agent")

    def agent(registry: ToolRegistry, task: Any) -> None:
        # 每个任务重建一次 agent，使 agent 自身的状态也不会跨任务残留
        runner = agent_factory()
        if not callable(runner):
            raise TypeError("agent factory must return a callable agent")
        runner(registry, task)

    runner = TaskRunner(agent=agent, environment=environment, store=store)
    run = runner.run(
        tasks,
        metadata={
            "tasks_file": str(args.tasks),
            "registry": args.registry,
            "agent": args.agent,
        },
    )
    report: dict[str, Any] = run.metadata.get("task_report") or {}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if _all_tasks_resolved(report) else 1
    _print_task_run(run, report)
    return 0 if _all_tasks_resolved(report) else 1


def _all_tasks_resolved(report: dict[str, Any]) -> bool:
    """空任务集不算通过，避免在空集上拿到满分。"""

    total = report.get("total") or 0
    return bool(total) and report.get("resolved") == total


def _print_task_run(run: Run, report: dict[str, Any]) -> None:
    print(f"run_id: {run.run_id}")
    print(
        f"tasks: {report.get('total', 0)}  resolved: {report.get('resolved', 0)}  "
        f"resolved_rate: {report.get('resolved_rate', 0.0):.4f}"
    )
    attempts = run.metadata.get("attempt_report")
    if isinstance(attempts, dict) and attempts.get("attempts", 0) > attempts.get("tasks", 0):
        print(
            f"attempts: {attempts.get('attempts', 0)}  "
            f"pass@1: {attempts.get('pass_at_1', 0.0):.4f}  "
            f"pass@k: {attempts.get('pass_at_k', 0.0):.4f}"
        )
    _print_metrics(run.metadata)
    for verdict in run.verdicts:
        print(f"  [{verdict.status.value:<5}] {verdict.case_id}  ({verdict.duration_ms:.1f} ms)")
        for check in verdict.failed_checks:
            print(f"         - {check.name}: {check.message or 'check failed'}")
        if verdict.error:
            print(f"         - error: {verdict.error}")
    unresolved = report.get("unresolved_tasks") or []
    errored = report.get("errored_tasks") or []
    if unresolved:
        print(f"unresolved: {', '.join(unresolved)}")
    if errored:
        print(f"errored: {', '.join(errored)}")


def _cmd_triage(args: argparse.Namespace, store: RunStore) -> int:
    run_id = args.run_id or _latest_run_id(store)
    if run_id is None:
        print("no runs recorded", file=sys.stderr)
        return 2
    try:
        run = store.load(run_id)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    cases_path: Path | None = args.cases
    if cases_path is None:
        recorded = run.metadata.get("cases_file") or run.metadata.get("tasks_file")
        if not recorded:
            print("run does not record its cases file; pass --cases", file=sys.stderr)
            return 2
        cases_path = Path(str(recorded))
    try:
        cases = _load_original_cases(cases_path)
    except FileNotFoundError:
        print(f"original cases file not found: {cases_path}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    report, candidates = triage_run(
        run,
        cases,
        _trace_store_from_args(args),
        trace_prefix=args.trace_prefix,
    )
    if args.emit is not None:
        write_candidates(args.emit, candidates)
    _record_conclusion(
        args,
        kind=KIND_TRIAGE,
        title=f"triage {run_id}",
        passed=not any(
            detail.reflowable and not detail.verified for detail in report.details
        ),
        run_id=run_id,
        summary={
            "run_id": run_id,
            "total_failures": report.total_failures,
            "reflowable": report.reflowable,
            "verified": report.verified,
            "candidates": [candidate.id for candidate in candidates],
        },
        payload=report.model_dump(mode="json"),
    )
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(format_triage_report(report))
        if args.emit is not None:
            print(f"candidates: {len(candidates)} written to {args.emit}")

    unverified = [
        detail.case_id for detail in report.details if detail.reflowable and not detail.verified
    ]
    return 1 if unverified else 0


def _load_original_cases(path: Path) -> list[AnyCase]:
    """归因需要原始用例定义；这里同时接受用例文件与任务文件。"""

    try:
        return list(load_cases(path))
    except ValueError:
        return list(load_tasks(path))


def _cmd_judge(args: argparse.Namespace) -> int:
    if args.judge_command != "calibrate":
        return 2
    try:
        items = load_gold_set(args.gold)
    except FileNotFoundError:
        print(f"gold set not found: {args.gold}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    judge = load_factory(args.judge, "judge")()
    if not callable(judge):
        raise SystemExit("judge factory must return a callable judge")
    thresholds = JudgeThresholds(
        min_agreement=args.min_agreement,
        max_position_flip_rate=args.max_position_flip,
        max_length_bias=args.max_length_bias,
    )
    try:
        report = calibrate_judge(judge, items, thresholds)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _record_conclusion(
        args,
        kind=KIND_JUDGE,
        title=f"judge calibration ({args.gold.name})",
        passed=report.usable_for_gate,
        summary={
            "items": report.items,
            "agreement": report.agreement,
            "ci_low": report.agreement_ci_low,
            "ci_high": report.agreement_ci_high,
            "position_flip_rate": report.position_flip_rate,
            "length_bias": report.length_bias,
        },
        payload=report.model_dump(mode="json"),
    )
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(format_judge_report(report))
    return 0 if report.usable_for_gate else 1


def _conclusion_store_from_args(args: argparse.Namespace) -> ConclusionStore:
    if args.home is not None:
        return ConclusionStore(Path(args.home) / REPORTS_DIRNAME)
    return ConclusionStore.default()


def _record_conclusion(args: argparse.Namespace, **fields: Any) -> None:
    """结论落盘是附带的持久化，失败只警告，不改变判定结果与退出码。"""

    try:
        write_conclusion(_conclusion_store_from_args(args), **fields)
    except OSError as exc:
        print(f"warning: could not persist conclusion: {exc}", file=sys.stderr)


def _cmd_serve(args: argparse.Namespace, store: RunStore) -> int:
    server = create_server(store, host=args.host, port=args.port)
    host = str(server.server_address[0])
    port = int(server.server_address[1])
    print(f"agenteval console: http://{host}:{port}")
    print(f"runs: {len(store.list_runs())}  home: {store.runs_dir.parent}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        server.server_close()
    return 0


def _cmd_load(args: argparse.Namespace, store: RunStore) -> int:
    if args.load_command != "run":
        return 2
    try:
        scenarios = load_scenarios(args.scenarios)
    except FileNotFoundError:
        print(f"scenario file not found: {args.scenarios}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    run = run_scenarios(
        scenarios,
        base_url=args.base_url,
        store=store,
        metadata={"scenarios_file": str(args.scenarios), "base_url": args.base_url},
    )
    reports = run.metadata.get("load_report") or []
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        _print_load(run, reports)
    return 1 if run.summary.failed or run.summary.errored else 0


def _print_load(run: Run, reports: list[Any]) -> None:
    print(f"run_id: {run.run_id}")
    for report in reports:
        if report.get("passed") is True:
            verdict = "PASS"
        elif report.get("passed") is False:
            verdict = "FAIL"
        else:
            verdict = "INFO"
        print(f"[{verdict}] {report.get('scenario_id')}  concurrency={report.get('concurrency')}")
        print(
            f"  requests: {report.get('completed')}  failed: {report.get('failed')}  "
            f"error_rate: {report.get('error_rate', 0.0):.4f}  "
            f"throughput: {report.get('throughput_rps', 0.0):.4f} rps"
        )
        print(
            f"  latency ms: p50={report.get('latency_p50_ms')} p90={report.get('latency_p90_ms')} "
            f"p95={report.get('latency_p95_ms')} p99={report.get('latency_p99_ms')}"
        )
        if report.get("ttfb_observed"):
            print(
                f"  ttfb ms: p50={report.get('ttfb_p50_ms')} p95={report.get('ttfb_p95_ms')}"
            )
        if report.get("error_kinds"):
            print(f"  errors: {json.dumps(report['error_kinds'], ensure_ascii=False)}")
        if report.get("fanout"):
            print(f"  fanout: {json.dumps(report['fanout'], ensure_ascii=False)}")
        for limitation in report.get("limitations") or []:
            print(f"  limitation: {limitation}")
        for reason in report.get("reasons") or []:
            print(f"  reason: {reason}")
    _print_metrics(run.metadata)


def _print_run(run: Run) -> None:
    summary = run.summary
    print(f"run_id: {run.run_id}")
    print(
        f"cases: {summary.total}  pass: {summary.passed}  "
        f"fail: {summary.failed}  error: {summary.errored}"
    )
    cassette = run.metadata.get("cassette")
    if isinstance(cassette, dict):
        unused = cassette.get("unused") or []
        print(
            f"cassette: {cassette.get('name')}  mode: {cassette.get('mode')}  "
            f"interactions: {cassette.get('interactions')}  unused: {len(unused)}"
        )
    _print_metrics(run.metadata)
    for verdict in run.verdicts:
        print(f"  [{verdict.status.value:<5}] {verdict.case_id}  ({verdict.duration_ms:.1f} ms)")
        for check in verdict.failed_checks:
            detail = check.message or "check failed"
            print(f"         - {check.name}: {detail}")
        if verdict.error:
            print(f"         - error: {verdict.error}")


def _print_metrics(metadata: dict[str, Any]) -> None:
    """打印运行级指标；用量的覆盖情况必须如实标注，不给出零消耗的结论。"""

    metrics = metadata.get("metrics")
    if not isinstance(metrics, dict):
        return
    usage = "reported" if metrics.get("usage_reported") else "not reported"
    print(
        f"metrics: calls={metrics.get('calls', 0)} retries={metrics.get('retries', 0)} "
        f"p50={metrics.get('duration_p50_ms', 0.0)}ms "
        f"p95={metrics.get('duration_p95_ms', 0.0)}ms "
        f"tokens={metrics.get('input_tokens', 0)}/{metrics.get('output_tokens', 0)} "
        f"usage={usage}"
    )
    violations = metrics.get("budget_violations") or []
    if violations:
        print(f"budget violations: {', '.join(violations)}")


def load_registry(ref: str | None) -> ToolRegistry:
    """按 ``module:factory`` 或 ``path/to/module.py:factory`` 载入工具注册表。"""

    factory = load_factory(ref, "registry")
    registry = factory()
    if not isinstance(registry, ToolRegistry):
        raise SystemExit("registry factory must return a ToolRegistry instance")
    return registry


def load_factory(ref: str | None, label: str) -> Callable[..., object]:
    """载入工厂函数本身，而不是它的调用结果。

    任务环境需要每个任务重建一次，因此这里必须拿到可重复调用的工厂。
    """

    if not ref or ":" not in ref:
        raise SystemExit(f"--{label} must look like 'package.module:factory' or 'path/to/module.py:factory'")
    target, _, attribute = ref.rpartition(":")
    module = _load_module(Path(target)) if _looks_like_path(target) else importlib.import_module(target)
    factory = getattr(module, attribute, None)
    if factory is None:
        raise SystemExit(f"{label} factory not found: {attribute}")
    if not callable(factory):
        raise SystemExit(f"{label} factory must be callable: {attribute}")
    return factory


def _looks_like_path(target: str) -> bool:
    return target.endswith(".py") or "/" in target or "\\" in target


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
