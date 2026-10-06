"""命令行入口：run / list / show。"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agenteval.loading import LoadError
from agenteval.loading import load_factory as load_factory_impl
from agenteval.loading import load_registry as load_registry_impl
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
from agenteval.otel import ExportError, export_run
from agenteval.redteam import RedTeamRunner, load_probes
from agenteval.dialogue import DialogueRunner, load_dialogue_cases
from agenteval.bridge import (
    BridgeAgent,
    BridgeError,
    load_agent_registry,
    parse_direct_reference,
)
from agenteval.mcp import McpRunner, load_mcp_cases
from agenteval.reflow import (
    REFLOW_DIRNAME,
    default_paths as reflow_default_paths,
    format_report as format_reflow_report,
    reflow as run_incremental_reflow,
)
from agenteval.sandbox import (
    DockerSandbox,
    SandboxError,
    SandboxSession,
    load_sandbox_spec,
)
from agenteval.selection import (
    SelectionCall,
    SelectionRunner,
    load_selection_cases,
)
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
from agenteval.ui import UiRunner, build_driver_factory, load_flows


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
    parser.add_argument(
        "--agents",
        type=Path,
        default=None,
        help="agent registry file (JSON or YAML) used by '@app-id' references",
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
    task_run_parser.add_argument(
        "--sandbox", type=Path, help="sandbox definition for per-case environment isolation"
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

    ui_parser = subparsers.add_parser("ui", help="run declarative UI flows in a browser")
    ui_subparsers = ui_parser.add_subparsers(dest="ui_command", required=True)
    ui_run_parser = ui_subparsers.add_parser("run", help="execute UI flows")
    ui_run_parser.add_argument("--flows", type=Path, required=True, help="flow file (JSON or YAML)")
    ui_run_parser.add_argument("--base-url", help="override the base address of every flow")
    ui_run_parser.add_argument("--driver", default="playwright", help="driver name (default: playwright)")
    ui_run_parser.add_argument("--headed", action="store_true", help="show the browser window")
    ui_run_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")

    otel_parser = subparsers.add_parser("otel", help="export runs as OTLP traces")
    otel_subparsers = otel_parser.add_subparsers(dest="otel_command", required=True)
    otel_export_parser = otel_subparsers.add_parser(
        "export", help="export a run to an OTLP/HTTP endpoint"
    )
    otel_export_parser.add_argument("--run", dest="run_id", help="run id (default: latest)")
    otel_export_parser.add_argument(
        "--endpoint", required=True, help="OTLP/HTTP traces endpoint, e.g. http://host:4318/v1/traces"
    )
    otel_export_parser.add_argument("--json", action="store_true", help="print the OTLP payload")

    redteam_parser = subparsers.add_parser("redteam", help="run security probes against an agent")
    redteam_subparsers = redteam_parser.add_subparsers(dest="redteam_command", required=True)
    redteam_run_parser = redteam_subparsers.add_parser("run", help="execute red team probes")
    redteam_run_parser.add_argument("--probes", type=Path, required=True, help="probe file (JSON or YAML)")
    redteam_run_parser.add_argument(
        "--target", required=True, help="target factory as 'package.module:factory'"
    )
    redteam_run_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")

    dialogue_parser = subparsers.add_parser("dialogue", help="run multi-turn dialogue cases")
    dialogue_subparsers = dialogue_parser.add_subparsers(dest="dialogue_command", required=True)
    dialogue_run_parser = dialogue_subparsers.add_parser("run", help="execute dialogue cases")
    dialogue_run_parser.add_argument(
        "--cases", type=Path, required=True, help="dialogue case file (JSON or YAML)"
    )
    dialogue_run_parser.add_argument(
        "--agent", required=True, help="turn-based agent factory as 'package.module:factory'"
    )
    dialogue_run_parser.add_argument(
        "--registry", help="environment factory; defaults to an empty tool registry"
    )
    dialogue_run_parser.add_argument(
        "--simulator", help="optional user simulator factory; defaults to the scripted simulator"
    )
    dialogue_run_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")

    mcp_parser = subparsers.add_parser("mcp", help="verify an MCP server's protocol and tool contracts")
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command", required=True)
    mcp_run_parser = mcp_subparsers.add_parser("run", help="execute MCP contract cases")
    mcp_run_parser.add_argument("--cases", type=Path, required=True, help="MCP case file (JSON or YAML)")
    mcp_run_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")

    selection_parser = subparsers.add_parser("selection", help="score how an agent selects tools")
    selection_subparsers = selection_parser.add_subparsers(dest="selection_command", required=True)
    selection_run_parser = selection_subparsers.add_parser("run", help="execute selection scoring")
    selection_run_parser.add_argument(
        "--cases", type=Path, required=True, help="selection case file (JSON or YAML)"
    )
    selection_run_parser.add_argument(
        "--agent", help="optional factory producing actual calls from a case"
    )
    selection_run_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")

    sandbox_parser = subparsers.add_parser(
        "sandbox", help="manage a compose-backed evaluation sandbox"
    )
    sandbox_subparsers = sandbox_parser.add_subparsers(dest="sandbox_command", required=True)
    sandbox_up = sandbox_subparsers.add_parser("up", help="start the sandbox and wait for health")
    sandbox_down = sandbox_subparsers.add_parser("down", help="stop the sandbox")
    sandbox_health = sandbox_subparsers.add_parser("health", help="check sandbox health")
    sandbox_reset = sandbox_subparsers.add_parser("reset", help="reset to a known state")
    sandbox_snapshot = sandbox_subparsers.add_parser("snapshot", help="save the volumes")
    for sandbox_sub in (
        sandbox_up,
        sandbox_down,
        sandbox_health,
        sandbox_reset,
        sandbox_snapshot,
    ):
        sandbox_sub.add_argument("--sandbox", type=Path, required=True, help="sandbox definition")
        sandbox_sub.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sandbox_down.add_argument("--volumes", action="store_true", help="also remove data volumes")
    sandbox_snapshot.add_argument("--name", help="snapshot name")

    reflow_parser = subparsers.add_parser(
        "reflow", help="turn newly seen failures into a regression dataset"
    )
    reflow_subparsers = reflow_parser.add_subparsers(dest="reflow_command", required=True)
    reflow_run_parser = reflow_subparsers.add_parser(
        "run", help="scan stored runs and emit new regression cases"
    )
    reflow_run_parser.add_argument(
        "--dataset", type=Path, help="dataset path (default: <home>/reflow/regression_cases.json)"
    )
    reflow_run_parser.add_argument(
        "--trace-prefix",
        default=DEFAULT_TRACE_PREFIX,
        help="prefix for saved evidence traces (default: repro)",
    )
    reflow_run_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
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
    if args.command == "ui":
        return _cmd_ui(args, store)
    if args.command == "otel":
        return _cmd_otel(args, store)
    if args.command == "redteam":
        return _cmd_redteam(args, store)
    if args.command == "dialogue":
        return _cmd_dialogue(args, store)
    if args.command == "mcp":
        return _cmd_mcp(args, store)
    if args.command == "selection":
        return _cmd_selection(args, store)
    if args.command == "sandbox":
        return _cmd_sandbox(args, store)
    if args.command == "reflow":
        return _cmd_reflow(args, store)
    return 2


def _store_from_args(args: argparse.Namespace) -> RunStore:
    if args.home is not None:
        return RunStore(Path(args.home) / RUNS_DIRNAME)
    return RunStore.default()


def _resolve_agent(
    ref: str, agents_path: Path | None, capability: str, label: str = "agent"
) -> Any:
    """解析被测 agent 引用。

    ``@app-id`` 走注册表，``http(s)://`` 与 ``cmd:`` 直连，其余按既有的
    ``module:factory`` 与 ``path/to/module.py:factory`` 载入。返回 Bridge 实例
    或尚未调用的工厂函数，由调用方决定调用时机。
    """

    if ref.startswith("@"):
        if agents_path is None:
            raise SystemExit("--agents <registry file> is required for '@app-id' references")
        registry = load_agent_registry(agents_path)
        return BridgeAgent(registry.get(ref[1:]))
    direct = parse_direct_reference(capability, ref)
    if direct is not None:
        return BridgeAgent(direct)
    return load_factory(ref, label)


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
    try:
        environment = load_factory(args.registry, "registry")
        resolved = _resolve_agent(args.agent, args.agents, "task")
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (BridgeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    bridge = resolved if isinstance(resolved, BridgeAgent) else None
    if bridge is not None:
        agent = bridge.for_task()
    else:
        agent_factory = resolved

        def agent(registry: ToolRegistry, task: Any) -> None:
            # 每个任务重建一次 agent，使 agent 自身的状态也不会跨任务残留
            runner = agent_factory()
            if not callable(runner):
                raise TypeError("agent factory must return a callable agent")
            runner(registry, task)

    metadata: dict[str, Any] = {
        "tasks_file": str(args.tasks),
        "registry": args.registry,
        "agent": args.agent,
    }
    if bridge is not None:
        bridge.record_metadata(metadata)

    sandbox_session: SandboxSession | None = None
    if args.sandbox is not None:
        try:
            sandbox_session = SandboxSession(DockerSandbox(load_sandbox_spec(args.sandbox)))
        except FileNotFoundError:
            print(f"sandbox definition not found: {args.sandbox}", file=sys.stderr)
            return 2
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        metadata["sandbox_file"] = str(args.sandbox)

    try:
        run = TaskRunner(
            agent=agent, environment=environment, store=store, sandbox=sandbox_session
        ).run(tasks, metadata=metadata)
    except SandboxError as exc:
        print(f"sandbox error: {exc}", file=sys.stderr)
        return 2
    if bridge is not None:
        bridge.apply_usage(run)
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

    try:
        run = run_scenarios(
            scenarios,
            base_url=args.base_url,
            store=store,
            metadata={"scenarios_file": str(args.scenarios), "base_url": args.base_url},
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    reports = run.metadata.get("load_report") or []
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        _print_load(run, reports)
    return 1 if run.summary.failed or run.summary.errored else 0


def _cmd_ui(args: argparse.Namespace, store: RunStore) -> int:
    if args.ui_command != "run":
        return 2
    try:
        flows = load_flows(args.flows)
    except FileNotFoundError:
        print(f"flow file not found: {args.flows}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.base_url:
        flows = [flow.model_copy(update={"base_url": args.base_url}) for flow in flows]
    try:
        factory = build_driver_factory(args.driver, headless=not args.headed)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    runner = UiRunner(
        driver_factory=factory,
        store=store,
        artifacts_dir=store.runs_dir.parent / "ui",
    )
    run = runner.run(flows, metadata={"flows_file": str(args.flows), "driver": args.driver})
    if args.json:
        print(json.dumps(run.metadata.get("ui_artifacts") or [], ensure_ascii=False, indent=2))
    else:
        print(f"run_id: {run.run_id}")
        print(
            f"flows: {run.summary.total}  pass: {run.summary.passed}  "
            f"fail: {run.summary.failed}  error: {run.summary.errored}"
        )
        for verdict in run.verdicts:
            print(f"  [{verdict.status.value:<5}] {verdict.case_id}  ({verdict.duration_ms:.0f} ms)")
            for check in verdict.failed_checks:
                print(f"         - {check.name}: {check.message or 'check failed'}")
            if verdict.error:
                print(f"         - error: {verdict.error}")
        for artifact in run.metadata.get("ui_artifacts") or []:
            if artifact.get("screenshot"):
                print(f"  screenshot: {artifact['screenshot']}")
    return 1 if run.summary.failed or run.summary.errored else 0


def _cmd_otel(args: argparse.Namespace, store: RunStore) -> int:
    if args.otel_command != "export":
        return 2
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
        result = export_run(run, args.endpoint)
    except ExportError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result.payload, ensure_ascii=False, indent=2))
    else:
        print(f"exported {result.spans} spans from run {run_id} to {result.endpoint}")
    return 0


def _cmd_redteam(args: argparse.Namespace, store: RunStore) -> int:
    if args.redteam_command != "run":
        return 2
    try:
        probes = load_probes(args.probes)
    except FileNotFoundError:
        print(f"probe file not found: {args.probes}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        resolved = _resolve_agent(args.target, args.agents, "redteam", label="target")
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (BridgeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    bridge = resolved if isinstance(resolved, BridgeAgent) else None
    if bridge is not None:
        target = bridge.for_redteam()
    else:
        target = resolved()
        if not callable(target):
            print("target factory must return a callable target", file=sys.stderr)
            return 2

    metadata: dict[str, Any] = {"probes_file": str(args.probes), "target": args.target}
    if bridge is not None:
        bridge.record_metadata(metadata)

    run = RedTeamRunner(target=target, store=store).run(probes, metadata=metadata)
    if bridge is not None:
        bridge.apply_usage(run)
    if args.json:
        print(json.dumps(run.metadata.get("redteam_report") or {}, ensure_ascii=False, indent=2))
    else:
        print(f"run_id: {run.run_id}")
        report = run.metadata.get("redteam_report") or {}
        print(
            f"probes: {report.get('total', 0)}  passed: {report.get('passed', 0)}  "
            f"failed: {report.get('failed', 0)}  errored: {report.get('errored', 0)}"
        )
        for verdict in run.verdicts:
            print(f"  [{verdict.status.value:<5}] {verdict.case_id}")
            for check in verdict.failed_checks:
                print(f"         - {check.name}: {check.message or 'detector fired'}")
            if verdict.error:
                print(f"         - error: {verdict.error}")
        for category, counts in sorted((report.get("by_category") or {}).items()):
            print(
                f"  {category}: total={counts['total']} passed={counts['passed']} "
                f"failed={counts['failed']} errored={counts['errored']}"
            )
    return 1 if run.summary.failed or run.summary.errored else 0


def _cmd_dialogue(args: argparse.Namespace, store: RunStore) -> int:
    if args.dialogue_command != "run":
        return 2
    try:
        cases = load_dialogue_cases(args.cases)
    except FileNotFoundError:
        print(f"dialogue file not found: {args.cases}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        resolved = _resolve_agent(args.agent, args.agents, "dialogue")
        environment = (
            load_factory(args.registry, "registry") if args.registry else (lambda: ToolRegistry())
        )
        simulator = load_factory(args.simulator, "simulator")() if args.simulator else None
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (BridgeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    bridge = resolved if isinstance(resolved, BridgeAgent) else None
    if bridge is not None:
        agent = bridge.for_dialogue()
    else:
        agent = resolved()
        if not callable(agent):
            print("agent factory must return a callable agent", file=sys.stderr)
            return 2

    metadata: dict[str, Any] = {"cases_file": str(args.cases), "agent": args.agent}
    if bridge is not None:
        bridge.record_metadata(metadata)

    run = DialogueRunner(
        agent=agent,
        environment=environment,
        simulator=simulator,
        store=store,
    ).run(cases, metadata=metadata)
    if bridge is not None:
        bridge.apply_usage(run)
    if args.json:
        print(json.dumps(run.metadata.get("dialogues") or {}, ensure_ascii=False, indent=2))
    else:
        print(f"run_id: {run.run_id}")
        print(
            f"cases: {run.summary.total}  pass: {run.summary.passed}  "
            f"fail: {run.summary.failed}  error: {run.summary.errored}"
        )
        for verdict in run.verdicts:
            print(f"  [{verdict.status.value:<5}] {verdict.case_id}  ({verdict.duration_ms:.0f} ms)")
            for check in verdict.failed_checks:
                print(f"         - {check.name}: {check.message or 'check failed'}")
            if verdict.error:
                print(f"         - error: {verdict.error}")
    return 1 if run.summary.failed or run.summary.errored else 0


def _cmd_mcp(args: argparse.Namespace, store: RunStore) -> int:
    if args.mcp_command != "run":
        return 2
    try:
        case_set = load_mcp_cases(args.cases)
    except FileNotFoundError:
        print(f"MCP case file not found: {args.cases}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    run = McpRunner(store=store).run(case_set, metadata={"cases_file": str(args.cases)})
    if args.json:
        print(
            json.dumps(
                [verdict.model_dump(mode="json") for verdict in run.verdicts],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"run_id: {run.run_id}")
        print(
            f"cases: {run.summary.total}  pass: {run.summary.passed}  "
            f"fail: {run.summary.failed}  error: {run.summary.errored}"
        )
        for verdict in run.verdicts:
            print(f"  [{verdict.status.value:<5}] {verdict.case_id}  ({verdict.duration_ms:.0f} ms)")
            for check in verdict.failed_checks:
                print(f"         - {check.name}: {check.message or 'check failed'}")
            if verdict.error:
                print(f"         - error: {verdict.error}")
    return 1 if run.summary.failed or run.summary.errored else 0


def _cmd_selection(args: argparse.Namespace, store: RunStore) -> int:
    if args.selection_command != "run":
        return 2
    try:
        cases = load_selection_cases(args.cases)
    except FileNotFoundError:
        print(f"selection case file not found: {args.cases}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    bridge: BridgeAgent | None = None
    if args.agent:
        try:
            resolved = _resolve_agent(args.agent, args.agents, "selection")
        except SystemExit as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except (BridgeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if isinstance(resolved, BridgeAgent):
            bridge = resolved
            agent: Any = bridge.for_selection()
        else:
            agent = resolved()
            if not callable(agent):
                print("agent factory must return a callable agent", file=sys.stderr)
                return 2
        try:
            cases = [_case_with_actual(case, agent) for case in cases]
        except Exception as exc:  # noqa: BLE001 - 产生调用失败即命令错误
            print(f"agent failed to produce calls: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

    metadata: dict[str, Any] = {"cases_file": str(args.cases), "agent": args.agent or ""}
    if bridge is not None:
        bridge.record_metadata(metadata)
    run = SelectionRunner(store=store).run(
        cases, metadata=metadata
    )
    if bridge is not None:
        bridge.apply_usage(run)
    if args.json:
        print(
            json.dumps(
                [verdict.model_dump(mode="json") for verdict in run.verdicts],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"run_id: {run.run_id}")
        print(
            f"cases: {run.summary.total}  pass: {run.summary.passed}  "
            f"fail: {run.summary.failed}  error: {run.summary.errored}"
        )
        for verdict in run.verdicts:
            print(f"  [{verdict.status.value:<5}] {verdict.case_id}")
            for check in verdict.failed_checks:
                print(f"         - {check.name}: {check.message or 'check failed'}")
            if verdict.error:
                print(f"         - error: {verdict.error}")
    return 1 if run.summary.failed or run.summary.errored else 0


def _case_with_actual(case: Any, agent: Callable[..., object]) -> Any:
    produced = agent(case)
    calls = [
        item if isinstance(item, SelectionCall) else SelectionCall.model_validate(item)
        for item in produced  # type: ignore[union-attr]
    ]
    return case.model_copy(update={"actual": calls})


def _cmd_sandbox(args: argparse.Namespace, store: RunStore) -> int:
    try:
        spec = load_sandbox_spec(args.sandbox)
    except FileNotFoundError:
        print(f"sandbox definition not found: {args.sandbox}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    sandbox = DockerSandbox(spec)
    payload: dict[str, Any]
    try:
        if args.sandbox_command == "up":
            sandbox.up()
            sandbox.wait_until_healthy()
            payload = sandbox.version()
        elif args.sandbox_command == "down":
            sandbox.down(remove_volumes=args.volumes)
            payload = {"project": spec.project, "removed_volumes": bool(args.volumes)}
        elif args.sandbox_command == "health":
            healthy, problems = sandbox.health()
            _print_sandbox_payload(
                {"project": spec.project, "healthy": healthy, "problems": problems}, args.json
            )
            return 0 if healthy else 1
        elif args.sandbox_command == "reset":
            sandbox.reset()
            payload = {"project": spec.project, "reset_strategy": spec.reset}
        elif args.sandbox_command == "snapshot":
            payload = sandbox.snapshot(getattr(args, "name", None))
        else:
            return 2
    except SandboxError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_sandbox_payload(payload, args.json)
    return 0


def _print_sandbox_payload(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def _cmd_reflow(args: argparse.Namespace, store: RunStore) -> int:
    if args.reflow_command != "run":
        return 2
    default_dataset, default_ledger = reflow_default_paths(
        store.runs_dir.parent / REFLOW_DIRNAME
    )
    dataset = args.dataset or default_dataset
    report = run_incremental_reflow(
        store,
        _trace_store_from_args(args),
        dataset,
        default_ledger,
        trace_prefix=args.trace_prefix,
    )
    if args.json:
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(format_reflow_report(report))
    return 0


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
    """载入工具注册表；失败时以 SystemExit 结束，保持命令行行为。"""

    try:
        return load_registry_impl(ref)
    except LoadError as exc:
        raise SystemExit(str(exc)) from exc


def load_factory(ref: str | None, label: str) -> Callable[..., object]:
    """载入工厂函数本身；失败时以 SystemExit 结束，保持命令行行为。"""

    try:
        return load_factory_impl(ref, label)
    except LoadError as exc:
        raise SystemExit(str(exc)) from exc
