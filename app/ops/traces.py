"""Inspect and prune generation traces.

    python -m app.ops.traces show <generation-id>   # the full record for one answer
    python -m app.ops.traces recent --limit 20      # newest traces, one line each
    python -m app.ops.traces prune                  # apply TRACE_RETENTION_DAYS
    python -m app.ops.traces prune --days 30 --yes

Traces hold the user's query and the answer served, so they are covered by
account export, the account delete cascade, and a retention window. `prune` is
the retention window's enforcement; run it on a schedule (see OBSERVABILITY.md).
"""

import argparse
import asyncio
import json
import sys
from uuid import UUID

from app.db import PostgresDatabase
from app.observability.repository import PostgresTraceRepository
from app.runtime_config import RuntimeSettings, load_runtime_settings
from app.schemas.trace import RetrievalStage


async def _show(settings: RuntimeSettings, args: argparse.Namespace) -> int:
    database = PostgresDatabase(settings.database_url.get_secret_value())
    await database.open()
    try:
        trace = await PostgresTraceRepository(database).get(UUID(args.generation_id))
    finally:
        await database.close()
    if trace is None:
        print(f"no trace for {args.generation_id}")
        return 1
    if args.json:
        print(json.dumps(trace.model_dump(mode="json"), indent=2))
        return 0

    print(f"generation  : {trace.generation_id}")
    print(f"created     : {trace.created_at:%Y-%m-%d %H:%M:%S %Z}")
    print(f"corner      : {trace.corner.value}")
    print(f"cat/session : {trace.cat_id} / {trace.session_id}")
    print(f"mode        : {trace.answer_mode or trace.response_kind}")
    print(f"prompt      : {trace.prompt_version}")
    print(f"query       : {trace.query}")
    print("")
    print("retrieval")
    for stage in RetrievalStage:
        rows = [row for row in trace.retrieval if row.stage is stage]
        if not rows:
            print(f"  {stage.value:<20} (none)")
            continue
        print(f"  {stage.value}")
        for row in sorted(rows, key=lambda item: item.rank):
            scores = " ".join(
                f"{name}={value:.4f}"
                for name, value in (
                    ("lex", row.lexical),
                    ("sem", row.semantic),
                    ("rrk", row.rerank),
                )
                if value is not None
            )
            print(f"    {row.rank:>2}. {row.entry_id:<28} {scores}")
    consensus = trace.consensus
    agree = [
        name
        for name, value in (
            ("semantic", consensus.semantic_agrees),
            ("lexical", consensus.lexical_agrees),
            ("rerank", consensus.rerank_agrees),
        )
        if value
    ]
    print(
        f"  consensus: top={consensus.top_entry_id} agreed={agree or 'none'}"
        + (
            f" coverage={consensus.coverage_ratio:.2f}"
            if consensus.coverage_ratio is not None
            else ""
        )
    )
    print("")
    print("model calls")
    if not trace.model_calls:
        print("  (none — the deterministic gate answered without a model)")
    for call in trace.model_calls:
        print(
            f"  {call.purpose:<9} {call.model:<28} in={call.input_tokens:<6} "
            f"out={call.output_tokens:<6} cache_r={call.cache_read_tokens:<6} "
            f"cache_w={call.cache_write_tokens:<6} {call.latency_ms:>7.1f}ms "
            f"{call.validation} x{call.attempts} ${call.cost_usd:.6f}"
        )
    print("")
    print(
        f"latency     : retrieval={trace.latency.retrieval_ms:.1f}ms "
        f"generation={trace.latency.generation_ms:.1f}ms "
        f"validation={trace.latency.validation_ms:.1f}ms "
        f"total={trace.latency.total_ms:.1f}ms"
    )
    print(f"tokens      : in={trace.total_input_tokens} out={trace.total_output_tokens}")
    print(f"cost        : ${trace.cost_usd:.6f}")
    print(f"groundedness: {trace.groundedness.value}")
    print(
        f"red flag    : {trace.red_flag_fired}"
        + (f" rules={trace.red_flag_rules}" if trace.red_flag_rules else "")
        + (f" canned={trace.canned_response_id}" if trace.canned_response_id else "")
    )
    return 0


async def _recent(settings: RuntimeSettings, args: argparse.Namespace) -> int:
    database = PostgresDatabase(settings.database_url.get_secret_value())
    await database.open()
    try:
        rows = await database.fetch_all(
            """
            SELECT generation_id, created_at, corner, answer_mode, response_kind,
                   groundedness, red_flag_fired, model_call_count, cost_usd
            FROM generation_traces
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (args.limit,),
        )
    finally:
        await database.close()
    if not rows:
        print("no traces recorded")
        return 0
    for row in rows:
        outcome = row["answer_mode"] or row["response_kind"] or "-"
        flag = " RED-FLAG" if row["red_flag_fired"] else ""
        print(
            f"{row['created_at']:%Y-%m-%d %H:%M:%S}  {str(row['generation_id'])[:8]}  "
            f"{row['corner']:<9} {outcome:<24} {row['groundedness']:<18} "
            f"calls={row['model_call_count']} ${float(row['cost_usd']):.6f}{flag}"
        )
    return 0


async def _prune(settings: RuntimeSettings, args: argparse.Namespace) -> int:
    days = args.days or settings.trace_retention_days
    database = PostgresDatabase(settings.database_url.get_secret_value())
    await database.open()
    try:
        row = await database.fetch_one(
            "SELECT count(*) AS doomed FROM generation_traces"
            " WHERE created_at < now() - make_interval(days => %s)",
            (days,),
        )
        doomed = int(row["doomed"]) if row else 0
        if doomed == 0:
            print(f"nothing older than {days} days")
            return 0
        if not args.yes:
            print(f"About to delete {doomed} trace(s) older than {days} days.")
            if input("Type 'prune' to confirm: ").strip() != "prune":
                print("aborted")
                return 1
        removed = await PostgresTraceRepository(database).prune(days)
        print(f"pruned {removed} trace(s) older than {days} days")
    finally:
        await database.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run one subcommand."""
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.traces", description=__doc__.split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="print the full trace for one generation")
    show.add_argument("generation_id")
    show.add_argument("--json", action="store_true", help="emit the raw record")

    recent = sub.add_parser("recent", help="list the newest traces")
    recent.add_argument("--limit", type=int, default=20)

    prune = sub.add_parser("prune", help="apply the retention window")
    prune.add_argument("--days", type=int, help="override TRACE_RETENTION_DAYS")
    prune.add_argument("--yes", action="store_true", help="skip the confirmation")

    args = parser.parse_args(argv)
    settings = load_runtime_settings()
    handlers = {"show": _show, "recent": _recent, "prune": _prune}
    return asyncio.run(handlers[args.command](settings, args))


if __name__ == "__main__":
    sys.exit(main())
