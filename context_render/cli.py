"""typer entry point: argument parsing and assembly only.

Exit codes: 0=success; 1=reserved/unused; 2=CLI argument error
(typer default); 3=precondition/environment error (PreconditionError).
Reports go to stdout; warnings and diagnostics go to stderr. Core flow makes
zero API calls.
"""

from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path

import typer

from . import __version__, hookinstall
from .attributor import attribute
from .attributor.facts import FACTS_EXTRACTOR_VERSION, extract_facts
from .attributor.stale import STALE_EXTRACTOR_VERSION, extract_stale
from .config import Config, audit_dir, find_repo_root, load_config
from .errors import PreconditionError
from .inventory.scanner import (
    load_manifest,
    manifest_path,
    merge_refresh,
    scan_components,
    write_manifest,
)
from .parser import discover_sessions, parse_file
from .pipeline import (
    build_rows,
    open_store,
    parse_since,
    scan_repo,
)
from .report.aggregate import aggregate_session, aggregate_window
from .report.ansi import Style
from .report.charts import hbar_chart, pad_to, truncate_display
from .report.render_md import render_md, write_md
from .report.render_term import render_term
from .report.selfderive import aggregate_analyze, emit_prompt_text, select_row
from .report.timeline import fmt_local_minute
from .store import Store

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  context_settings={"help_option_names": ["-h", "--help"]})

EXIT_PRECONDITION = 3


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"context-render {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True,
        help="Show version"
    ),
):
    """context-render: scaffolding-layer observability — see which of your scaffolding the agent actually used."""


def _guard(fn):
    try:
        return fn()
    except PreconditionError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(EXIT_PRECONDITION) from None


def _load_config_flags(repo_root: Path, no_timeline: bool, no_graph: bool) -> Config:
    """load_config plus the two report toggles; a flag only ever switches a section off."""
    config = load_config(repo_root)
    if no_timeline:
        config.timeline = False
    if no_graph:
        config.graph = False
    return config


def _emit(agg: dict, config: Config, repo_root: Path, md: bool, full: bool = False) -> None:
    if md:
        path = write_md(agg, config, repo_root)
        typer.echo(render_md(agg, config))
        typer.echo(f"Wrote {path}", err=True)
    else:
        # color=True: keep our own ANSI styling (Style already gates on tty/NO_COLOR)
        typer.echo(render_term(agg, config, full=full), color=True)


@app.command()
def init(
    refresh: bool = typer.Option(False, "--refresh", help="Incrementally update existing manifest"),
    yes: bool = typer.Option(False, "--yes", help="Skip interactive confirmation"),
    hook: bool = typer.Option(None, "--hook/--no-hook", help="Install / skip SessionEnd hook"),
):
    """Scan scaffolding, produce manifest.yaml, inventory summary, and optional hook install."""

    def run():
        repo_root = find_repo_root()
        mpath = manifest_path(repo_root)
        if mpath.exists() and not refresh:
            raise PreconditionError(
                f"manifest already exists ({mpath}); to update run ctxr init --refresh"
            )
        new_comps = scan_components(repo_root)
        if refresh and mpath.exists():
            comps = merge_refresh(load_manifest(repo_root), new_comps)
        else:
            comps = new_comps

        # Inventory summary: split by type/provenance, static/dynamic + token-share bar chart
        active = [c for c in comps if not c.missing]
        by_type = Counter(c.type for c in active)
        by_prov = Counter(c.provenance for c in active)
        static_total = sum(c.static_tokens for c in active)
        dynamic_total = sum(
            (c.tokens_body_est or 0) if c.type == "skill"
            else (c.tokens_est or 0) if c.context == "dynamic" else 0
            for c in active
        )
        typer.echo(f"{len(active)} components: " +
                   ", ".join(f"{t}×{n}" for t, n in sorted(by_type.items())))
        typer.echo("provenance: " + ", ".join(f"{p}×{n}" for p, n in sorted(by_prov.items())))
        typer.echo(f"static total {static_total:,} tokens / dynamic total {dynamic_total:,} tokens (estimated)")
        items = sorted(
            ((c.id, float(c.static_tokens)) for c in active if c.static_tokens > 0),
            key=lambda x: -x[1],
        )[:12]
        if items:
            style = Style.detect()
            typer.echo("")
            typer.echo(style.bold("static context token share (estimated)"), color=True)
            for line in hbar_chart(items, bar_paint=style.cyan):
                typer.echo(line, color=True)

        # ask BEFORE writing anything: declining must leave the repo exactly as it was
        # (a leftover manifest would turn the user's next `init` into exit 3)
        if static_total < 1000 and not yes:
            typer.echo("")
            typer.echo("static context total < 1,000 tokens — you may not need this tool yet.")
            if not typer.confirm("Continue anyway?", default=True):
                raise typer.Exit(0)

        path = write_manifest(repo_root, comps)
        typer.echo(f"manifest written to {path}")

        # .gitignore append (skip if already present)
        gi = repo_root / ".gitignore"
        entries = [".context-render/db.sqlite", ".context-render/reports/"]
        existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
        to_add = [e for e in entries if e not in existing]
        if to_add:
            with open(gi, "a", encoding="utf-8") as fh:
                if existing and not existing.endswith("\n"):
                    fh.write("\n")
                fh.write("\n".join(to_add) + "\n")
            typer.echo(f".gitignore appended: {', '.join(to_add)}")

        # SessionEnd hook: interactive prompt; --hook/--no-hook skips the prompt.
        # --yes takes the prompt's default answer (No) — only an explicit --hook installs unattended.
        do_hook = hook
        if do_hook is None:
            do_hook = False if yes else typer.confirm(
                "Install SessionEnd hook (auto sync & ingest when a session ends)?", default=False
            )
        if do_hook:
            if hookinstall.install(repo_root):
                typer.echo("SessionEnd hook installed (.claude/settings.json); "
                           "remove with ctxr remove-hook")
            else:
                typer.echo("SessionEnd hook already exists (skipped)")

    _guard(run)


@app.command()
def sync(
    since: str = typer.Option(None, "--since", help="Only sync this window (30d / 12w / 2026-06-01)"),
    force: bool = typer.Option(False, "--force", help="Re-parse everything"),
):
    """Discover transcripts → parse → attribute → idempotent ingest."""

    def run():
        repo_root = find_repo_root()
        config = load_config(repo_root)
        summary = scan_repo(repo_root, config, since=since, force=force)
        typer.echo(summary.line())

    _guard(run)


def _session_report(session: str, md: bool, evidence: bool,
                    no_timeline: bool, no_graph: bool, full: bool = False) -> None:
    repo_root = find_repo_root()
    config = _load_config_flags(repo_root, no_timeline, no_graph)
    components = load_manifest(repo_root)
    sessions = discover_sessions(repo_root)
    if not sessions:
        raise PreconditionError("No transcript found for this repo; check the Claude Code project path")
    matches = [s for s in sessions if s.session_id.startswith(session)]
    if not matches:
        raise PreconditionError(
            f"No session found with id prefix {session!r}; "
            "use ctxr sessions to list them"
        )
    if len(matches) > 1:
        ids = ", ".join(s.session_id[:12] for s in matches)
        raise PreconditionError(f"prefix {session!r} matches multiple: {ids}; lengthen the prefix")
    target = matches[0]
    parsed = parse_file(target.path, target.sidechain_paths)
    att = attribute(parsed, components, repo_root)
    facts = extract_facts(parsed, repo_root)
    stale = extract_stale(parsed, repo_root)
    # Ingest on the spot if not yet stored (idempotent)
    store = open_store(repo_root)
    try:
        if store.needs_update(target.session_id, target.mtime, target.size,
                              FACTS_EXTRACTOR_VERSION, STALE_EXTRACTOR_VERSION):
            srow, urows, frows, strows = build_rows(
                parsed, att, components, target, repo_root, config, facts=facts, stale=stale)
            store.replace_session(srow, urows, frows, strows)
    finally:
        store.close()
    agg = aggregate_session(parsed, att, components, config, include_evidence=evidence,
                            facts=facts.facts, facts_tool_output=facts.tool_output_tokens_est,
                            stale=stale)
    _emit(agg, config, repo_root, md, full=full)


def _window_report(since: str, md: bool, no_timeline: bool, no_graph: bool,
                   emit_prompt: str | None = None) -> None:
    repo_root = find_repo_root()
    config = _load_config_flags(repo_root, no_timeline, no_graph)
    components = load_manifest(repo_root)
    since_dt = parse_since(since)
    since_iso = since_dt.isoformat() if since_dt else None
    since_label = f"last {since}" if since else "all history"
    store = open_store(repo_root)
    try:
        if emit_prompt:
            sd_agg, fact_rows = aggregate_analyze(store, config, since_iso=since_iso,
                                                  since_label=since_label)
            row = select_row(sd_agg["rows"], emit_prompt)
            if row is None:
                raise PreconditionError(
                    f"--emit-prompt {emit_prompt!r} matches no row; give a row number from "
                    "the current SELF-DERIVATION table or a canonical key (run ctxr report first)"
                )
            typer.echo(emit_prompt_text(sd_agg, fact_rows, row))
            return
        agg = aggregate_window(store, components, config,
                               since_iso=since_iso, since_label=since_label)
    finally:
        store.close()
    # session_count == 0 → aggregate_window degrades every status to "no data" and puts
    # the warning in the report body itself, so both term and --md carry it
    _emit(agg, config, repo_root, md)


HELP_TEXT = """\
context-render — scaffolding-layer observability: see which of your scaffolding the agent actually used.

Command overview

  init        scan scaffolding → produce .context-render/manifest.yaml + inventory summary
              --refresh incremental update (append new components, mark disappeared as missing, keep manual edits)
              --hook/--no-hook install/skip SessionEnd hook; --yes skip confirmation

  remove-hook remove the SessionEnd hook that init --hook installed (.claude/settings.json;
              other settings untouched)

  sync        parse transcripts → three-state attribution → idempotent ingest (re-runs don't double-count)
              --since <spec> sync window only; --force full re-parse

  sessions    list ingested sessions (id, time, turns, task digest), newest on top
              --since <spec> filter window
  sessions <id-prefix>
              full report for that session: three-state attribution, file loads
              (context injection order), context window map (▼ injections above / ▲ actions
              below; event-sized color blocks per lane + window-occupancy bar;
              labels = timeline row numbers),
              numbered timeline (with [read]/[edit]/[write]/[bash] action events),
              and a SELF-DERIVATION block (top info-needs the agent answered itself)
              --evidence attach raw event evidence; --md write to file; --full untruncated
              terminal output (keeps color); --no-timeline/--no-graph

  report      cross-session aggregate: invocation count / last used / status per component
              (active / low-use / unused / MISS), daily-activity histogram, cost estimate,
              and a SELF-DERIVATION block — every agent search is a question the harness
              didn't answer: what the agent went after, with token and window-occupancy
              cost per information need (no scores, no verdicts)
              --since 30d (default); --md write to file (complete SELF-DERIVATION table)
              --emit-prompt <#|key> print one SELF-DERIVATION row's full evidence as a
              drafting prompt (plain text; deciding what scaffold to write is left to the reader)

  map         routing map: measure guidance as a map and what it fails to cover — per-carrier
              prose share + label quality, loading guarantees (auto-inject / @import /
              dir-entry / plain reference), structure + hop depth, dead routes, and Python
              reachability with unreachable files sorted by observed self-derivation cost
              (facts with literature notes, no scores)
              --md write to file; --since 30d join window; no transcripts needed

  map init    generate a routing-map skeleton (paths + TODO labels) plus fill
              instructions for your agent; never overwrites (existing CLAUDE.md sends
              the skeleton to .context-render/map-proposal.md)
              --shape auto|flat|tree; --output <path>

  clear       clear record data (db.sqlite and reports/); manifest/config kept.
              sync only rebuilds sessions whose transcripts still exist — those Claude Code
              has expired (cleanupPeriodDays, default 30d) are named, then lost for good.
              --yes skip confirmation

  help        show this help

Common conventions

  --since     accepts 30d / 12w / 2026-06-01 (by session start time, local timezone)
  --md        write full markdown report to .context-render/reports/ and also print it
  exit codes  0 success; 2 argument error; 3 precondition error (not init'd, transcripts not found)
  per-command details: ctxr <command> --help

Usage loops

  inner loop (after each task): sync → sessions → sessions <id-prefix> → "why wasn't skill X
              loaded?" → fix trigger → check the next session's report: did the fix land?
  outer loop (weekly): report --since 30d → review unused components → delete/rewrite low-use
              ones; the SELF-DERIVATION block shows what the agent kept going to find itself

Three states: R registered → L loaded (content injected) → I invoked (actually called)
Marks: ~ = heuristic attribution (drill down with --evidence to verify); amounts/tokens are estimates
"""


map_app = typer.Typer(add_completion=False, invoke_without_command=True,
                      help="Routing map: measure guidance as a map (default), "
                           "or generate a skeleton (init)")
app.add_typer(map_app, name="map")


@map_app.callback()
def map_report(
    ctx: typer.Context,
    md: bool = typer.Option(False, "--md", help="Output markdown and write to file"),
    since: str = typer.Option("30d", "--since",
                              help="Observed-cost join window (30d / 12w / 2026-06-01)"),
):
    """Measure the repo's guidance as a routing map and what it fails to cover: per-carrier
    prose share and label quality, loading guarantees (auto-inject / @import / dir-entry /
    plain reference), structure and hop depth, dead routes, and Python reachability with
    observed self-derivation cost. Purely static — no transcripts needed; joins self-derivation
    facts when a db exists. Measurements with a literature note, never scores."""
    if ctx.invoked_subcommand is not None:
        return

    def run():
        from .guidance.refs import FileIndex
        from .mapdev.audit import aggregate_map

        repo_root = find_repo_root()
        config = load_config(repo_root)
        since_dt = parse_since(since)
        store = None
        if (audit_dir(repo_root) / "db.sqlite").is_file():  # never create a db here
            store = open_store(repo_root)
        try:
            agg = aggregate_map(
                FileIndex(repo_root), store=store,
                since_iso=since_dt.isoformat() if since_dt else None,
                window_label=f"last {since}")
        finally:
            if store is not None:
                store.close()
        _emit(agg, config, repo_root, md)

    _guard(run)


@map_app.command("init")
def map_init(
    shape: str = typer.Option("auto", "--shape",
                              help="auto (flat ≤300 files, tree beyond) | flat | tree"),
    output: Path | None = typer.Option(None, "--output",
                                       help="Write the skeleton here instead"),
):
    """Generate a routing-map skeleton (paths + TODO labels) plus fill instructions for
    your agent. Never overwrites: an existing CLAUDE.md sends the skeleton to
    .context-render/map-proposal.md; an existing proposal is refused."""

    def run():
        from .guidance.refs import FileIndex
        from .mapdev.initgen import build_skeleton, fill_instructions

        if shape not in ("auto", "flat", "tree"):
            raise PreconditionError(f"unknown --shape '{shape}' (auto|flat|tree)")
        repo_root = find_repo_root()
        target = output
        if target is None:
            root_map = repo_root / "CLAUDE.md"
            target = (audit_dir(repo_root) / "map-proposal.md"
                      if root_map.exists() else root_map)
        if target.exists():
            raise PreconditionError(
                f"{target} already exists — move it aside or pass --output")
        index = FileIndex(repo_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(build_skeleton(index, shape), encoding="utf-8")
        instr = audit_dir(repo_root) / "map-fill-instructions.md"
        instr.parent.mkdir(parents=True, exist_ok=True)
        instr.write_text(fill_instructions(), encoding="utf-8")
        typer.echo(f"Wrote {target} (skeleton, TODO labels)")
        typer.echo(f"Wrote {instr} — hand it to your agent to fill the labels, "
                   "then run `ctxr map`")

    _guard(run)


@app.command()
def help():  # CLI subcommand name, deliberately shadows the builtin
    """Explain which commands context-render provides."""
    typer.echo(HELP_TEXT)


@app.command("remove-hook")
def remove_hook():
    """Remove the SessionEnd hook that `init --hook` installed (.claude/settings.json)."""

    def run():
        repo_root = find_repo_root()
        if hookinstall.uninstall(repo_root):
            typer.echo("SessionEnd hook removed (.claude/settings.json)")
        else:
            typer.echo("No SessionEnd hook installed (nothing to remove)")

    _guard(run)


def _unreproducible(db: Path):
    """Sessions in the DB that sync could not rebuild; None if the DB itself can't be opened."""
    try:
        store = Store(db)
    except Exception:  # noqa: BLE001 - an unopenable db must degrade to None, not crash clear
        return None
    try:
        return store.unreproducible_sessions()
    finally:
        store.close()


@app.command()
def clear(
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation"),
):
    """Clear all record data (db.sqlite and reports/); manifest and config are kept.

    sync rebuilds only what is still on disk: Claude Code expires transcripts on a rolling
    window (cleanupPeriodDays, default 30d), and sessions past it exist nowhere but this DB.
    Those are named before the confirmation prompt.
    """

    def run():
        repo_root = find_repo_root()
        adir = audit_dir(repo_root)
        db = adir / "db.sqlite"
        reports = adir / "reports"
        targets = [p for p in (db, reports) if p.exists()]
        if not targets:
            typer.echo("Nothing to clear (neither db.sqlite nor reports/ exists)")
            return
        typer.echo("Will clear:")
        for p in targets:
            if p.is_dir():
                n = sum(1 for _ in p.iterdir())
                typer.echo(f"  {p} ({n} report files)")
            else:
                typer.echo(f"  {p} ({p.stat().st_size:,} bytes)")
        typer.echo("manifest.yaml and config.yaml are unaffected.")
        lost = _unreproducible(db) if db.exists() else []
        if lost is None:
            typer.secho(
                "WARNING: db.sqlite could not be read, so it is unknown which sessions sync could "
                "rebuild. Copy it aside before clearing.",
                fg="yellow",
            )
        elif lost:
            typer.secho(
                f"WARNING: {len(lost)} recorded session(s) can NOT be rebuilt by sync — Claude Code "
                "has already expired their transcripts (cleanupPeriodDays, default 30d), so this DB "
                "is the last copy. Clearing destroys them permanently:",
                fg="red",
            )
            for r in lost[:5]:
                typer.echo(f"    {r['id'][:8]}  {(r['started_at'] or '?')[:10]}")
            if len(lost) > 5:
                typer.echo(f"    … and {len(lost) - 5} more")
            typer.echo("  Copy db.sqlite aside first if this history is worth keeping.")
        else:
            typer.echo("Every recorded session still has its transcript on disk; sync can rebuild them all.")
        if not yes and not typer.confirm("Confirm clear?", default=False):
            raise typer.Exit(0)
        for p in targets:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        typer.echo("Cleared.")

    _guard(run)


@app.command()
def sessions(
    id_prefix: str = typer.Argument(
        None, metavar="[ID-PREFIX]",
        help="Show the full report for this session instead of listing"
    ),
    since: str = typer.Option(None, "--since", help="Only list this window (30d / 12w / 2026-06-01)"),
    md: bool = typer.Option(False, "--md", help="(with ID-PREFIX) Output markdown and write to file"),
    evidence: bool = typer.Option(False, "--evidence", help="(with ID-PREFIX) Attach raw event evidence"),
    full: bool = typer.Option(False, "--full", help="(with ID-PREFIX) Complete terminal report, no truncation"),
    no_timeline: bool = typer.Option(False, "--no-timeline"),
    no_graph: bool = typer.Option(False, "--no-graph"),
):
    """List ingested sessions; with an id prefix, show that session's full report."""

    def run():
        if id_prefix:
            _session_report(id_prefix, md, evidence, no_timeline, no_graph, full)
            return

        repo_root = find_repo_root()
        load_manifest(repo_root)  # not init'd → exit 3
        since_dt = parse_since(since)
        store = open_store(repo_root)
        try:
            rows = store.sessions_since(since_dt.isoformat() if since_dt else None)
        finally:
            store.close()
        if not rows:
            typer.echo("(no ingested sessions; run ctxr sync first)", err=True)
            return
        style = Style.detect()
        typer.echo(
            style.dim(
                f"{pad_to('id', 10)}{pad_to('started', 18)}{pad_to('turns', 7)}"
                f"{pad_to('status', 10)}task digest"
            ),
            color=True,
        )
        for r in reversed(rows):  # newest on top
            started = r["started_at"] or ""
            if started:
                started = fmt_local_minute(started)
            flag = "⚠" if r["parse_status"] == "degraded" else "ok"
            flag_cell = (style.yellow if flag == "⚠" else style.dim)(pad_to(flag, 10))
            digest = truncate_display(r["prompt_digest"] or "—", 46)
            typer.echo(
                f"{pad_to(r['id'][:8], 10)}{style.dim(pad_to(started or '—', 18))}"
                f"{pad_to(str(r['turns'] or 0), 7)}{flag_cell}{digest}",
                color=True,
            )

    _guard(run)


@app.command()
def report(
    since: str = typer.Option("30d", "--since", help="Observation window (30d / 12w / 2026-06-01)"),
    md: bool = typer.Option(False, "--md"),
    no_timeline: bool = typer.Option(False, "--no-timeline"),
    no_graph: bool = typer.Option(False, "--no-graph"),
    emit_prompt: str = typer.Option(
        None, "--emit-prompt", metavar="<#|KEY>",
        help="Print one SELF-DERIVATION row's full evidence as a drafting prompt (plain text "
             "to stdout; row numbers are unstable across runs, the canonical key isn't)"),
):
    """Cross-session aggregate report: per-component status, daily-activity histogram, and
    the SELF-DERIVATION block (what the agent went to find itself, sorted by token cost)."""
    _guard(lambda: _window_report(since, md, no_timeline, no_graph, emit_prompt))


def main():
    app()


if __name__ == "__main__":
    main()
