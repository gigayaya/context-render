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
