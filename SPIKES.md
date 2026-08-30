# Spike verdict record (W1, 2026-07-11)

Per implementation plan v3 §3.2, each item was validated against 14 real local transcripts (cc 2.1.156–2.1.207, 739 lines).
**Once recorded, a verdict is frozen; overturning one later is a change-management event.**

| # | Item | Verdict | Basis (observed shape) |
|---|---|---|---|
| 1 | Line-level fields | **Primary plan** | `type`/`uuid`/`parentUuid`/`timestamp`/`cwd`/`version`/`gitBranch`/`sessionId` all present; ts is ISO 8601 millisecond-precision UTC (`2026-07-11T06:35:38.906Z`); assistant lines carry `message.usage` with input/cache_creation/cache_read/output tokens plus `message.model` |
| 2 | cwd presence | **Primary plan** | Every event line has `cwd` (absolute path, CJK path samples included); cwd is the basis for session→repo attribution, the munged directory name is only a pre-filter |
| 3 | Skill expansion events | **Primary plan (exact)** | I: `tool_use` name=="Skill", input `{"skill": "<name|plugin:name>", "args": ...}` + matching tool_result; L: user message text starting with `Base directory for this skill: <path>`; there is also a line-level `attributionSkill` field (subsequent lines attributed to the skill) usable as auxiliary L evidence. The degraded Read-SKILL.md heuristic is kept as a fallback |
| 4 | Command markers | **Primary plan (exact)** | User messages contain `<command-name>/<name></command-name>` (plus `<command-message>`/`<command-args>`); prompt-prefix matching is kept as a fallback |
| 5 | Hook execution records | **Primary plan (observable)** | Two shapes: (a) `type=attachment`, `attachment.type ∈ {hook_success, hook_additional_context}` with `hookName` (e.g. `SessionStart:startup`) and `hookEvent`; (b) `type=system`, `subtype=stop_hook_summary` with `hookCount`/`hookInfos[].command`. Per the plan, confidence is always marked heuristic (event→manifest-entry mapping is best-effort) |
| 6 | Subdirectory CLAUDE.md loading | **Degraded (heuristic)** | No identifiable subdirectory CLAUDE.md load events in the samples → use the directory-activity heuristic (Read/Edit/Write/Bash target under that directory → L); a Read that hits that CLAUDE.md path directly is still recorded as exact |
| 7 | Plugin install paths | **Primary plan** | `~/.claude/plugins/installed_plugins.json` (version 2, `plugins.<name@marketplace>[].installPath`) + `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`; enabled state comes from settings.json `enabledPlugins` |
| 8 | Usage fields | **Primary plan** | assistant lines have `message.usage` in full: `input_tokens`/`cache_creation_input_tokens`/`cache_read_input_tokens`/`output_tokens`, plus `message.model` → pricing per model via the built-in price table is feasible |
| 9 | SessionEnd hook | **Primary plan** | Transcripts show `SessionStart:startup` hook executions (the hook mechanism works); Claude Code settings support the `SessionEnd` event → init installs SessionEnd; Stop is the degraded fallback |
| 10 | Version support matrix | Initial = **2.1.x** | Sampled 2.1.156 / 2.1.177 / 2.1.195 / 2.1.206 / 2.1.207; matrix set to `2.1.*`, other versions get a warning + best-effort parse |
| 11 | Compaction events | **Degraded + hand-made fixture** | No compaction events in local samples (no `compact_boundary`/`isCompactSummary`). The parser implements recognition of the known shapes (`system`/`subtype=compact_boundary`, user lines with `isCompactSummary=true` → `kind=compaction`, exact), locked in by a hand-made regression fixture (AC9); re-validate once real samples are obtained |

## Incidental observations (affecting implementation)

- Beyond user/assistant/system/summary, line-level types also include auxiliary ones: `attachment`, `last-prompt`, `mode`, `permission-mode`, `file-history-snapshot`, `ai-title`, `agent-name`. These are listed as **known auxiliary types** (kind=system) and do not trigger degraded; only genuinely unknown types count toward degraded (AC5).
- Full observed set of `attachment.type`: `hook_success`, `hook_additional_context`, `deferred_tools_delta`, `skill_listing`, `task_reminder`, `plan_mode`, `plan_mode_exit`, `file`, `agent_listing_delta`, `mcp_instructions_delta`, `goal_status`, `command_permissions`.
- The `isSidechain` field exists (subagent sidechain events share the file with the main chain).
- Price table (built-in, config-overridable; cache write = 1.25× in, cache read = 0.1× in): fable-5 $10/$50, opus-4-x $5/$25, sonnet $3/$15, haiku-4-5 $1/$5 per MTok.

# Spike verdict record (W2, 2026-07-12)

Per the subagent observability-gap investigation: 3 real sessions in this repo (cc 2.1.207, 6 subagent transcripts) + a line-by-line scan of the main files of all 9 project directories on this machine.
**Once recorded, a verdict is frozen; overturning one later is a change-management event.**

| # | Item | Verdict | Basis (observed shape) |
|---|---|---|---|
| 12 | Subagent transcript location | **Primary plan: separate file** | `<proj>/<sessionId>/subagents/agent-<agentId>.jsonl`; line-level fields include `isSidechain:true`, `agentId`, `sessionId` (= parent session), `promptId`, `entrypoint`, with `version`/`cwd`/`timestamp` in the same shape as the main chain; the same-named `.meta.json` has `agentType`/`description`/`toolUseId` (matching the id of the dispatching tool_use in the main chain)/`spawnDepth`. Zero `isSidechain:true` lines across all main files on this machine → the W1 incidental observation "sidechain events share the file with the main chain" does not hold in the current version (that entry was an incidental observation, not a numbered verdict; this entry supersedes it) |
| 13 | Subagent dispatch tool name | **Primary plan (exact): `Agent`; legacy name `Task` also supported** | Main-chain tool_use `name=="Agent"` (observed 17 times in this repo's main files), input contains `subagent_type` (same shape as Task); `"Task"` observed 0 times. The attributor accepts both names |

## Incidental decisions (affecting implementation)

- Sidechain events are merged into the parent session's event stream: merged by timestamp (lines missing ts are fill-forwarded, in-file order preserved), idx renumbered over the merged stream; without sidechain files idx stays the original file line number (single-file evidence refs unchanged).
- Window-separation principle: each subagent has its own context window — sidechain assistant usage does not enter context samples, its file reads do not enter file loads (context injection order), and it is not drawn on the context map; but component three-state attribution is still recorded (evidence carries a `[subagent:<agentType>]` tag), the timeline still lists it (same tag), and cost and total tokens still count it (real spend).
- File freshness: SessionFile.mtime = max over the main file + all sidechain files, size = sum over the set → a late-arriving subagent file (e.g. a background agent) automatically triggers re-ingest of that session, no --force needed.

# Spike verdict record (W3, 2026-07-16)

Per the `analyze` self-derivation-cost design: two dry-run rounds over 68 files / 67 sessions / 7 projects on this machine. The design was rewritten by this data before implementation.
**Once recorded, a verdict is frozen; overturning one later is a change-management event.**

| # | Item | Verdict | Basis (observed shape) |
|---|---|---|---|
| 14 | Cross-session repeated keyword searches | **Thin** | After removing extraction contamination (`find -prune` exclusion arguments, pipeline-filter greps) only single digits / a few thousand tokens remained. Must not be the headline; `analyze` instead lists every information need sorted by cost, with no cross-session-repetition requirement |
| 15 | Manual follow-up invocations (user typing /command after describing the task for several turns) | **0 occurrences in the local corpus** | Automatic triggering is healthy (superpowers-family skills auto-triggered 8/4/4/3 times). Detector removed from `analyze` |
| 16 | Repeated bash sequences → scaffold candidates | **Weak** | Catches were mostly already-documented routine (pytest, git status). Detector removed from `analyze` |
| 17 | Guidance-strength contrast | Suggestive, not conclusive (n=1) | Large unfamiliar repo without a harness ≈ 14.5 searches/session vs 2–3.5 in projects with one. Intra-session rummaging is the bulk, supporting verdict #14's reframing |
| 18 | Repo-structure mapping ritual | **Largest repeated action** | `find -type d` in 15 sessions / 18 times, plus `ls` chains. Becomes the first action-row detector (`repo layout`) |
| 19 | Extraction contamination cases | Direct basis for decontamination rules | `find -path '*/node_modules*' -prune` and `\| grep -v ".git/"` were misread as keywords. Rules: `grep -v` segments excluded; mid-pipeline path-less non-recursive greps are filters, excluded; `find` `-path`/`-prune`-related arguments never produce keywords. Locked in by regression tests |
| 20 | Search channel coverage | Local Grep/Glob tool calls = 0 (all searching goes through Bash); only 1 subagent sidechain file in the corpus | Detection must cover both the tool-call path and the bash path |

# Spike verdict record (W4, 2026-07-18)

Per the syntax-token classification design (specs/2026-07-18): dry-run over 65 files /
65 parsed sessions on this machine (main files only; the corpus' single sidechain file,
per W3 #20, was not scanned). **Once recorded, a verdict is frozen; overturning one
later is a change-management event.**

| # | Item | Verdict | Basis (observed shape) |
|---|---|---|---|
| 21 | Declaration-prefix set | **Locked as proposed** (`def class function func fn struct interface impl trait enum const import from use require #include package`), with two shape rules: a trailing escape-class quantifier reads as a separator (`def\s+_short` → keyword `_short`), and a multi-word remainder is prose, left untouched | (a) caught `def`/`^def` 12x, `import`/`^import`/`^from`/`^class` 9x; (b) harvested 34 distinct clean keywords (`def test`→`test`, `class TimelineEntry`→`TimelineEntry`, `function calcs.buildOutput`→`calcs.buildOutput`). One false kill: grep `'use --md for all'` (English verb, not a Rust `use` probe) → multi-word-remainder rule, frozen as a regression |
| 22 | Syntax stoplist | **Locked as proposed** (`len self this super return import print init main async yield await lambda null none nil true false`); heuristic confidence boundary upheld; generic English words (`error`, `test`, `config`) stay out | Only hit in the corpus: bare grep `main` 1x (heuristic). kept-top-40 scan found no missed syntax tokens — `uninstall`, `file_loads`, `is_supported` etc. are all real project vocabulary; zero false positives beats coverage |
| 23 | Pure-structure glob rule | **Locked with two amendments**: the glob layer requires an actual glob metacharacter or path separator (a literal filename is an information need), and short all-ASCII path segments (fold ≤ 3: `src`, `lib`) read as navigation, not vocabulary | Extension globs dominated (a): `*.py` 10x, `*.jsonl` 5x, `*.md` 3x + 7 more extensions. Stem harvest quality good (`agent-*.jsonl` → `agent`). One false kill: `find -name ".claude.local.md"` (2x) — a literal dotfile classified as pure structure → metacharacter-required rule, frozen as a regression |

# Spike verdict record (W5, 2026-07-19)

Per the guidance-reachability design (specs/2026-07-19): cross-project dry-run over 5
repos on this machine — context-render itself, scaffold-audit, gigachang-skills,
example_project and PyCon-DAA-Demo (the last two near-identical; effective distinct
sample 3 external + self). Driver: scripts/w5_dryrun.py, running the real guidance
modules. **Once recorded, a verdict is frozen; overturning one later is a
change-management event.**

| # | Item | Verdict | Basis (observed shape) |
|---|---|---|---|
| 24 | Fenced-block references | **Counted (FENCED_DEFAULT = True)** — overturns the n=1 pre-verdict leaning to exclude | Fenced edges are real routing in 3 of 4 external repos: INDEX/project-structure files put file references inside fenced tree diagrams (20 resolving edges in example_project, contributing the final 21/21 py file; skills-repo READMEs route agents/commands the same way). The feared command-example pollution self-filters: hypothetical names (`user_actions.py`, `cart_actions.py`) never resolve, so they land in the stale channel, never as edges — resolution itself is the pollution filter |
| 25 | Non-CLAUDE.md markdown as carrier | **Allowed (ALL_MD_DEFAULT = True)** | Decisive: projects whose routing lives in guidelines/knowledge-base markdown go 0/21 → 16/21 py (83/83 symbols) on this toggle alone; gigachang-skills 0/10 → 7/10. Exactly the "root points at README/architecture.md" shape the spec predicted |
| 26 | Dir reference → direct children (ls edge) | **Allowed (DIR_CHILDREN_DEFAULT = True)** | Adds genuine one-ls-away coverage everywhere it fires (+4 py in example_project; caught context-render's own just-added-but-not-yet-routed files). Noise is vendored-submodule content, which is honest — those files ARE one ls away. `ls` provenance stays distinct from `reference` |
| 27 | Unique-basename resolution layer (layer 3) | **Kept, heuristic** | 21 edges across the corpus (6+3+9+3), all hand-checked correct, zero mis-links; ambiguity abstention fired correctly on multi-`SKILL.md` repos. scaffold-audit would drop from 5/41 to near-zero py reachability without it |
| 28 | Stale/index hygiene (finding-4 extension) | **Three exclusions locked**: `<`/`>` template placeholders (`lib/<domain>/x.py`) never stale; leading-`/` strings (absolute paths `/usr/src/insect`, slash-commands `/docs-drift`, XML tags `/svg`) never candidates; FileIndex skips dotfiles (same rule as dot-dirs) | Placeholder and leading-`/` strings flooded the stale channel in 3 repos (15+ entries each in example_project and gigachang-skills); `.DS_Store`/`.gitignore`/submodule `.git` gitlinks polluted counts and toggle deltas. All three frozen as regressions |

# Pre-verdict record (W6, 2026-07-21) — routing topology

Per the routing-topology design (specs/2026-07-21-w6-routing-topology.md): origin is the
hypothesis "the optimal routing strategy is a B+ tree with level ≤ 3", decomposed into
independently falsifiable claims. **Pre-verdicts are frozen here BEFORE any Phase A data
is examined** (W5 #24 precedent: a pre-verdict's job is to let the data overturn it on
the record). Verdicts land when the corresponding phase gate closes: Phase A is
observational (zero API, W1–W5 style), Phase B interventional — the first W-series spike
to spend real sessions (the zero-API hard constraint binds `ctxr` itself, not
experiments run *on* it).

Observability note, recorded at freeze (from reading the join code, not from data):
`needed(f)` — the agent required file f, regardless of how it found it — is **not
observable in Phase A**. The facts join only sees chain_reads downstream of searches, so
a file the agent navigated to directly leaves no fact. Phase A therefore reports raw
touch-rates and cost-intensity among touched files; conditioning on `needed` is
Phase B-only (task targets are known there by construction).

| # | Item | Pre-verdict (frozen 2026-07-21) | Verdict | Basis (observed shape) |
|---|---|---|---|---|
| 29 | H1-weak: navigation cost rises monotonically with hop depth | **Leaning confirm** — hop overhead (tool call + permanent window occupancy) and per-decision error compounding both charge depth's account | **Confirmed (cost, not success; conditional)** | B1: actions-to-target = hop depth exactly (1/1/2/2/4), mean tool calls 3.38→4.22→5.72 monotone; B2: gradient flattens when 9-hop code traversal dominates (V2≈V3≈V0 at 13–14 calls). Depth bills only where routing lies on the agent's actual path. Accuracy ceiling'd 100% in all 1,080 runs — depth cost never surfaced as failure |
| 30 | H1-strong: depth 3 is a knee, not a gradient | **Leaning refute** — expect a soft gradient, no categorical break; also expect the A1 adequacy gate to fail (too little depth-≥4 mass in the corpus), routing this claim to Phase B only | **Refuted (as pre-verdicted)** | no categorical break at 3→4 in either environment: B1 slope 2→4 hops matches 1→2; B2 V3 (hop 5–6) degrades smoothly, 120/120 correct |
| 31 | H2: high fanout is cheap up to ~30–50 entries when labels are good | **Leaning confirm**, with the sharper sub-prediction: labeled flat-fat (V1) lands closer to B+ ≤3 (V2) than intuition expects, while unlabeled flat-dump (V1b) collapses toward baseline — width's cost is label entropy, not width itself | **Confirmed, twice sharpened; ranking objective-sensitive** | fanout 53 (B1) and 435 (B2) both won on action cost; B1 labels>bare, B2 bare>labels → label value = label semantics − path semantics. Context lens inverts ranking: pure hierarchical routing has the tightest context tail (V2 p90 vs V0 −3.6k tok, permutation p=0.001; V2 vs V3 p=0.279 — class-level claim only). Map tax +8–12k tok at 435 files → scale ceiling |
| 32 | H3: internal nodes should be pure routers (the "+" in B+) | **Leaning confirm** — predicted failure mode is stop-early on mixed content+routing nodes (agent reads the prose half, satisfies, never reaches the table). Lowest confidence, most novel claim | **Confirmed (behavioral; cost delta small)** | B1: V4 vs V2 same topology, searches 2.0× (0.75 vs 0.38), routers read 1.33 vs 1.65; B2: mixed routers ignored outright (V4 ≈ V0, highest search rate of routed arms). Direction consistent both rounds |
| 33 | H4: leaf-depth balance matters | **Leaning refute** — expect leaf-depth variance to be noise at repo scale | **Untested** | no unbalanced arm in either round; recorded as untested, not refuted |

H5 (leaf sibling links — lateral "see also" edges at the content layer, the forgotten
B+ property) is parked unnumbered so the idea isn't lost; not tested in W6.

# Pre-verdict record (W7, 2026-07-23) — context-noise causality

Per specs/2026-07-23-w7-context-noise.md (§0 program north star: context economy
is a first-class objective — user directive; single-task designs structurally
understate pollution cost; p90 × N is what accumulates). **Pre-verdicts frozen
here BEFORE any pilot or arm data is examined.** Design amendment recorded at
freeze: subject codebase is **atlas** (118k lines, 3 semantic domains, cross-
domain chain — user directive 2026-07-23), superseding the spec draft's
obsidian; noise blocks are drawn from atlas, and the task battery gains a
13th cross-domain task. W6 final verdicts (#29–#33 above) frozen the same day.

**Verdicts frozen 2026-07-24**, after the rev2 full run (780 runs = 6 residue
arms × 13 tasks × 10 reps, agentic on atlas, sonnet/medium; 104 pilot runs
preceded and set the lever — report: scripts/w8/results/W7_report.md). Two
design events recorded before any full-run data, per spec §2b (rev2 amendment,
2026-07-23, user directive): (a) injection design replaced by agentic
session-residue design after both pilots showed the injection ceiling is
unbreakable at any model tier; (b) **#37's operationalization re-scoped** from
the position arm (H8 as drafted at pre-verdict freeze) to the Rstale
in-context-stale-copy arm — pre-verdict direction unchanged (leaning refute =
expect the agent to re-verify against the repo). #37's basis wording below is
the 2026-07-24 corrected version, after transcript audit of all failing runs.

| # | Item | Pre-verdict (frozen 2026-07-23) | Verdict | Basis (observed shape) |
|---|---|---|---|---|
| 34 | H5: error rate rises monotonically with irrelevant-context volume | **Leaning confirm, weak slope** — long-context models degrade slowly on ≤45k of noise; expect a real but small monotone effect | **Refuted** | zero value errors in 520 runs across the entire 0→45k neutral/decoy volume range (R0/R15n/R15d/R45n all 0%); the predicted weak slope does not exist for tool-equipped agents — they read the residue, put it down, and re-verify against the repo |
| 35 | H6: confusable noise (same-name decoys) hurts more than neutral noise at equal tokens | **Leaning confirm, strong** — the load-bearing half of the premise: quality over quantity | **Confirmed** | quality gradient at fixed 45k dose: neutral 0% → same-name decoys 1.5% (Fisher one-sided p=0.249, suggestive) → stale copy of the answer file 4.6% (6/130 vs R0 0/130, p=0.0147, significant); what drives error is how closely the residue impersonates the answer, not tokens |
| 36 | H7: noise-induced errors are predominantly decoy-value substitutions | **Leaning confirm** — verifiable verbatim against the planted decoy inventory | **Confirmed, 6/6** | every Rstale value error reproduced a planted trap value exactly (stale _FRACTION=0.31 → 220, ×2; stale remainder-dropped logic → 30.0, ×4); zero fabrications and zero random errors across all 780 runs — noise-induced hallucination is trusting the wrong copy, not creativity |
| 37 | H8: noise adjacent to evidence hurts more than distant noise | **Leaning refute** — expect position to matter less than confusability at these scales | **Held, with a critical carve-out** (re-scoped to Rstale per §2b; wording corrected 2026-07-24 by transcript audit) | agents re-read the live file in 100% of audited failing runs (10/10 T11, 10/10 T01 opened the real repo file) — yet still fell: with live and stale copies both in context, synthesis sometimes picked the stale one. Asymmetry: constant conflicts resolve to live almost always (stale wins 2/20), logic conflicts resolve to stale **4/10**. Pre-verdict direction was right (re-verification is real and mostly wins); the blind spot: re-verification cannot neutralize an in-context stale copy — failure sits at synthesis, not checking, and structure loses to staleness far more often than values do |

# Pre-verdict record (W7b, 2026-07-24) — stale-logic asymmetry

Per specs/2026-07-24-w7b-logic-trap.md: dedicated test of #37's carve-out
asymmetry (constant conflicts resolve stale 2/20, logic conflicts 4/10),
which currently rests on ONE logic task (T11). Battery: 8 tasks identical
to their W7 counterparts, 7 fully paired (same hero file admits both a
const-mutation and a logic-mutation; all outcomes exec-verified pairwise
distinct); logic mutations span five styles (rounding, scan order, formula
structure, discretization/indexing, branch deletion). Arms at W7's Rstale
dose (15k): A0 / Aconst / Alogic; L08's Aconst is a verbatim-duplicate
control. All levers (model sonnet/medium, grading, residue mechanics)
frozen from W7 — not re-tuned. **Pre-verdict frozen BEFORE any pilot or
arm data is examined.**

| # | Item | Pre-verdict (frozen 2026-07-24) | Verdict | Basis (observed shape) |
|---|---|---|---|---|
| 38 | H9: at equal dose on identical tasks, stale-LOGIC residue induces stale-copy wins at a higher rate than stale-CONSTANT residue | **Leaning confirm** — W7 observed the 4/10 vs 2/20 gap, and the mechanism story (a stale constant is falsified by one live glance; stale structure must be re-derived) predicts survival under style diversification. Honest caveat at freeze: the logic side is n=1 task; if T11-specific, expect Alogic ≈ Aconst everywhere except L08. Overturned by: Alogic ≈ or < Aconst across the 7 paired tasks, or the effect concentrating entirely in L08 | **Refuted as a class claim; residual confirmed narrowly** (verdict frozen 2026-07-24, user-reviewed) | both pre-named overturn conditions fired: paired battery shows no logic>const gap (audited stale-wins Alogic 6/35 vs Aconst 9/35 — direction reversed, n.s.), and the logic effect concentrates in L08 (4/5 vs 6/35 pooled other logic styles, p=0.010). Surviving residual: stale copies as a class bite hard (Aconst 9/40 p=0.0012, Alogic 10/40 p=0.0005, vs A0 0/40); all 19 audited falls are verbatim trap reproductions (zero fabrication, #36 extends); synthesis-stage failure replicates #37 (failing runs quote stale derivations while claiming repo verification). Predictive axis reframed post-hoc: derivational self-sufficiency of the stale copy (table lookups 4/5, prose-stated rules 4/5, re-derivation-forcing edits 0/5) — pre-registered as #39/W7c. Cross-experiment anomaly recorded: byte-identical residue fell 2/120 in W7 vs 11/56 in W7b — absolute rates are experiment-local; within-experiment comparisons unaffected (report finding 5) |

# Pre-verdict record (W7c, 2026-07-24) — derivational self-sufficiency

Per specs/2026-07-24-w7c-self-sufficiency.md: pre-registration of the axis
that #38's wreckage pointed at. Same mutation, same trap value, same dose —
only the presentation varies: Dwork (structural mutation, no explanatory
prose, conclusion must be re-derived) vs Dself (same mutation + one
docstring sentence stating the wrong rule, no worked numbers). A0 re-run
fresh as battery-drift gate (absolute rates are experiment-local per W7b
finding 5). All other levers frozen from W7b. **Pre-verdict frozen BEFORE
any run.**

| # | Item | Pre-verdict (frozen 2026-07-24) | Verdict | Basis (observed shape) |
|---|---|---|---|---|
| 39 | H10: at equal dose and equal trap value, a stale copy that STATES its wrong rule as adoptable prose induces more stale-copy wins than one forcing re-derivation | **Leaning confirm, strong** — W7b extremes (table 4/5, prose rule 4/5, re-derivation trap 0/5) and the #37 synthesis mechanism point the same way. Caveats at freeze: the prose line adds salience as well as self-sufficiency (confound recorded in spec §4); Dwork carries nonzero W7b-style rates, so the gap may be modest. Overturned by: Dself ≈ Dwork across the battery, or the effect appearing only on L08 | **Confirmed** (verdict frozen 2026-07-24, user-reviewed) | same mutation + one wrong-rule docstring sentence roughly doubles audited stale-wins: Dwork 9/40 → Dself 19/40 (Fisher one-sided p=0.017; excluding L08: 5/35 → 14/35, p=0.015 — not an L08 artifact). A0 gate 0/40; Dwork reproduces W7b Alogic (9/40 vs 10/40, same mutations, same-day cohort) — the presentation axis moved Dself, not batch noise. Gradient observed: bare-`pass` L08 (code shape shows the conclusion) still falls 3/5, narrated version 5/5; one-glance formula tasks immune (L03/L07 Dself 0/5) — prose wins where checking is expensive, loses where it is free. Failing runs cite the docstring's claim as the rule's semantics even while claiming live-file verification (agents trust prose over code). Salience confound stands as recorded at freeze (spec §4); equally-salient non-conclusive-prose control is future work. One grading false-pass corrected by audit (Dwork:L07:r3); hidden-false-pass scan found zero others |

# Pre-verdict record (W8, 2026-07-24) — stacked sessions

Per specs/2026-07-24-w8-stacked-sessions.md: the program's central claim
tested in its native habitat — ONE persistent session, 30 sequential
tasks, context never reset, files mutating exogenously between tasks via
a neutral task server (tick.py), six W6 routing variants on atlas, 5 reps
(user directives: all six variants, N=30). Key inherited design point:
a question recurring after its hero's mutation makes the agent's OWN
earlier answer the in-context stale copy — W7c's derivational
self-sufficiency at its maximum. Ground truth is per-occurrence (the
schedule generator execs the repo state at each index); the pre-mutation
answer becomes that occurrence's trap string. **Pre-verdicts frozen
BEFORE any machinery run.**

| # | Item | Pre-verdict (frozen 2026-07-24) | Verdict | Basis (observed shape) |
|---|---|---|---|---|
| 40 | H11: post-mutation recurrences fail toward the agent's own prior answer at elevated rates | **Leaning confirm, strong** — nothing is more derivationally self-sufficient than one's own worked conclusion; expected the largest effect in the program. Overturned by: recurrences ≈ first occurrences, or errors not reproducing the old answer | **Confirmed — largest effect in the program** (frozen 2026-07-24, user-reviewed) | 58.6% of post-mutation occurrences (492/840) returned the verbatim old answer vs 99.9% correct on pre+ctrl (959/960); V0 and V3 fell 140/140 each — every single opportunity — including T13's transitive staleness (dependency mutated, orchestrator unchanged); session-level distribution is near-purely bimodal (stale counts 0 or 14) |
| 41 | H12: pure accumulation stays innocent under stacking — never-mutated questions show no drift with task index | **Leaning confirm** — direct extension of #34 to the stacked regime. Overturned by: late-index drift on never-mutated questions | **Confirmed** (frozen 2026-07-24, user-reviewed) | pre+ctrl 959/960 correct across all indices and all six variants; no late-index drift on never-mutated questions; #34 extends cleanly to the stacked regime |
| 42 | H13: context-growth slope inverts — V0 steepest (non-reusable grep output), V1/V2 amortize; V1-vs-V2 crossing deliberately not pre-called (open, W6 showed ranking is objective-sensitive) | **Leaning confirm for V0-steepest only** | **Refuted, with a mechanism rewrite** (frozen 2026-07-24, user-reviewed) | V0's peak context is the LOWEST (63k avg vs V1 74k): the prediction assumed agents re-pay navigation per task; instead they reuse in-context conclusions — cheap, and 100% stale once the world changes. Saving context and manufacturing stale answers are the same behavior; re-verification (the correctness-preserving act) is where context goes. The V1-vs-V2 crossing question is mooted. Program objective function rewritten: not "least context" but "cheapest re-visit". Compaction covariate unused (never triggered at this scale) |
| 43 | H14: the "read → mutated → never re-read" gauge state predicts elevated stale answers, with the W7b/c carve-out (re-reading under-protects against narrated/structural mutations) | **Leaning confirm, with carve-out** — this is context-render's planned gauge, pre-registered. Overturned by: gauge state uncorrelated, or re-reading fully protective across mutation types | **Confirmed; carve-out nearly absent** (frozen 2026-07-24, user-reviewed) | re-visit behavior separates sessions near-deterministically: caught sessions (stale ≤2) average 44.6 hero-file accesses (23.9 late-half) vs 30.5 (16.7) for uncaught; variant determines the habit (V1/V1b 8/10, V2 7/10 caught vs V4 1/10, V0/V3 0/10 — V4's prose-diluted routers suppress re-navigation despite V2-identical topology, upgrading W6 #32 from cost to correctness). Residual stale among catchers is single-digit — habitual in-the-moment re-reading protects, unlike W7b/c's residue-vs-live co-presence (difference recorded). Zero file-change system reminders in all 60 transcripts; two agents' claims of such reminders were confabulated (echoes #37). Gauge validated as detecting the accident site |

# Pre-verdict record (W8b/W8c, 2026-07-29) — prose localization & model generality

Per docs/paper-skeleton.md gap list (G-A, G-D; user-approved 2026-07-29).
W8b localizes the prose-suppression effect: new variant V4h (prose in the
CLAUDE.md head ONLY, routers pure) x10 + fresh V2 anchor x5 (guards the
cross-day comparability of W8's V2/V4 cells). W8c tests model generality
of the own-answer trap: V0 vs V1, haiku x10 reps and opus x5 reps, same
harness/schedule. All other levers frozen from W8. **Pre-verdicts frozen
BEFORE any run.**

| # | Item | Pre-verdict (frozen 2026-07-29) | Verdict | Basis (observed shape) |
|---|---|---|---|---|
| 44 | H15: router-node prose is the active suppressor of the re-visit habit — V4h (head prose, pure routers) recovers V2-level catching | **Leaning confirm** — W6 #32 located the ignoring behavior at mixed ROUTERS specifically; head prose was never the trigger. Overturned by: V4h ≈ V4 (any prose preamble suppresses), or V2-fresh diverging from W8's V2 (cross-day drift voids the comparison) | **Refuted as localized; broader suppression confirmed** (frozen 2026-07-29, user-reviewed) | the named overturn fired in the STRONGER direction: V4h (prose in CLAUDE.md head only, routers fully pure) caught 0/10 with 140/140 stale — worse than V4's 1/10; V2f anchor 3/5 ≈ W8's V2 validates the cross-day comparison. ANY architectural-prose preamble suppresses the re-visit habit; router purity alone does not save you. Mechanism converges with #39/W7c: prose that confers understanding substitutes for looking. Design rule rewritten: guidance files should be routing-only (path + one-line label); explanatory prose is a behavioral suppressant, not neutral decoration |
| 45 | H16: the own-answer trap and its routing mediation are model-general — stale rate(V0) ≫ stale rate(V1) in both a weaker (haiku) and a stronger (opus) model | **Leaning confirm for direction; magnitudes open** — the trap is an incentive structure, not a capability gap; but a weaker model may also fail V1 (habit needs competence) and a stronger one may self-verify in V0 (trap shrinks). Overturned by: either model showing V0 ≈ V1 | **Trap generality confirmed; the routing rescue is capability-gated** (frozen 2026-07-29, user-reviewed) | V0 stale is high at every tier — haiku 123/140, sonnet 140/140, opus 70/70 (even the strongest model falls on every opportunity without a map: the trap is an incentive structure, as pre-called); but the V1 rescue fails for haiku (0/10 caught, V0 ≈ V1 — the named overturn fired for the weak model) while working for sonnet (8/10) and opus (3/5). The re-visit habit needs cheap re-visits AND the capability to use them; weaker models need external invalidation (the gauge), not better maps |

# Pre-verdict record (W8d, 2026-07-29) — robustness arms

Per specs/2026-07-29-w8d-robustness.md (anticipated-reviews items 1–2,
user-approved): W8d-para (paraphrased recurrences, V0 x10) and W8d-warn
(generic change notice at mutation points, V0 x10) + 3 fresh V0-verbatim
anchor sessions. All other levers frozen from W8. **Pre-verdicts frozen
BEFORE any run.**

| # | Item | Pre-verdict (frozen 2026-07-29) | Verdict | Basis (observed shape) |
|---|---|---|---|---|
| 46 | H17: the own-answer trap survives paraphrased recurrence (stale ≥ half of verbatim rate) | **Leaning confirm, attenuated** — W8 reuse looked semantic, not string-matched. Overturned by: collapse toward baseline (headline re-scoped to verbatim repetition) or zero attenuation | **Confirmed, stronger than called: ZERO attenuation** (frozen 2026-07-30, user-reviewed) | paraphrased recurrences (semantics/inputs identical, wording fully rewritten): 140/140 stale, 0/10 caught — identical to verbatim anchor (42/42) and W8's V0 (140/140). Recognition is semantic (agents enumerate 'task 6/24 ≈ task 1' across paraphrase); verbatim wording was never the driver. Review threat 1 (headline-as-artifact) neutralized by data; the second overturn branch fired in the headline-hardening direction |
| 47 | H18: one generic change notice restores substantial re-verification on V0 (stale drops ≥ half vs 140/140) | **Leaning confirm** — the W8 failure mode is absence of any invalidation signal. Caveat: V0's expensive re-visits may leave the signal acknowledged-but-unacted (#43/#45). Overturned by: signal ignored, stale ≈ 100% — which would sharpen "signals without cheap re-visits do not protect" | **Confirmed at ceiling: complete protection** (frozen 2026-07-30, user-reviewed) | one generic notice at mutation points ('some workspace files may have been modified since your earlier reads' — no file names) took V0 from 140/140 stale to 0/140, 10/10 sessions caught, post-mutation 140/140 correct; the acknowledged-but-unacted caveat never materialized even with expensive re-visits. Defensive hierarchy rewritten: change signal (complete, near-free) > flat/shallow maps (70–80%, capability-gated #45) > deep/prose/none (0–10%). Quantifies the production file-change-reminder mechanism: 100% failure → 0%. Review threat 2 converted into the program's strongest practical finding; the gauge's online form IS this sentence |
