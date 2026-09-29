# A4 — Character State v0.1

## Status

A4 v0.1 implements one current, explicitly managed Character State snapshot in
SQLite. The Character key is the fixed `identity.internal_id` UUID from the
SI-001 seed; `development_id` is not used for State ownership.

## Model and persistence

`CharacterState` contains energy (`low` / `medium` / `high`), attention
(`scattered` / `normal` / `focused`), mood tendency (`low` / `neutral` /
`positive`), social engagement (`withdrawn` / `normal` / `engaged`), optional
current activity, and UTC `updated_at`. SQLite stores one row per internal UUID
in `character_state`; an absent row is initialized once with defaults. Explicit
partial updates preserve omitted values. Passing `current_activity=None` clears
the activity, while omitting it leaves it unchanged.

The state service is the explicit write boundary. Conversation loads the state
after archiving the user message and includes a compact, separate state section
in the prompt. It does not write State based on user or model text. State is not
Memory, Personality, Identity, or World State; it is only a weak current-context
cue for expression. No state history, automatic inference, passive background
decay, or world simulation is implemented.

## CLI and verification

The local CLI supports the developer command `/state` to inspect current values
and `/state energy low`, `/state attention focused`, `/state mood positive`,
`/state social engaged`, or `/state activity <text>` to explicitly update them.
Use `/state activity none` to clear the activity. These commands do not invoke
the LLM. The offline smoke check is:

```bash
python scripts/run_character_state_smoke.py
```

It uses a temporary SQLite database and verifies defaults, updates, persistence
after reopening, and prompt projection.

## A4.1 State Transition v0.1

A4.1 adds deterministic, bounded transitions to the same current-state row.
Events are transient values; no State history or event table is created.
Conversation applies elapsed-time rules after archiving the user's message and
before Memory recall and prompt construction. It does not infer State from
conversation content.

Supported explicit events are `rest_started`, `focused_task_started`,
`focused_task_ended`, `activity_set`, `activity_cleared`, `mood_up`,
`mood_down`, `mood_reset`, `social_engagement_up`, and
`social_engagement_down`. `conversation_started` and
`conversation_turn_completed` are recognized no-op events. `time_elapsed` is
handled by `apply_elapsed_time`. Each result reports before/after state,
changed fields, event type, an explanation, and any diagnostics.

### Transition rules

- **Energy:** `rest_started` moves up one level. Starting focus or completing a
  conversation turn does not spend energy. For elapsed time, at 3–8 hours only
  `low` becomes `medium`; at 8 hours or more energy moves up one level (`high`
  stays `high`). Less than 3 hours leaves energy unchanged.
- **Attention:** focus start sets `focused`; focus end sets `normal`. After at
  least one elapsed hour, `focused` returns to `normal`. It is never changed to
  `scattered` by these rules.
- **Mood:** ordinary events and elapsed time do not change mood. Explicit mood
  events move one level up/down or reset to `neutral`.
- **Social engagement:** explicit up/down events move at most one level and
  stop at the endpoints.
- **Activity:** set/clear events update it explicitly. A focused task may set
  it when an activity name is provided; ending the task does not clear it.

All event times must be timezone-aware and are normalized to UTC. When `now` is
earlier than `updated_at`, no State field or timestamp is changed and the result
includes the `clock_moved_backwards` diagnostic. Later elapsed calculations
continue from the preserved timestamp anchor.
The rules are engineering heuristics, not a physiological or psychological
model. There is no inference, event history, offline life simulation, world
event driver, activity scheduler, relationship coupling, personality evolution,
or probabilistic transition. This version also excludes sentiment analysis,
emotion classifiers, valence/arousal, sleep, hunger, health, affection, trust,
intimacy, relationship State, autonomous decisions, NPCs, background scheduling,
cron, and QQ integration.

### Conversation and debug commands

Before each LLM request, Conversation archives the user message, applies the
elapsed-time transition using current UTC time, then performs the existing Memory
recall and prompt construction. State is not changed based on user phrasing or
the LLM response. Transition results are not injected into the prompt or saved
as Memory.

The local developer commands include `/state event rest`, `/state event
focus-start`, `/state event focus-end`, `/state event mood-up`, `/state event
mood-down`, `/state event mood-reset`, `/state event social-up`, and `/state
event social-down`. `/state activity <text>` sets an activity;
`/state activity none` clears it. Existing direct `/state` inspection/update
commands remain available.

Run the offline deterministic smoke test with:

```bash
python scripts/run_character_state_transition_smoke.py
```

`--real-llm` optionally sends one prompt for qualitative observation; it is not
part of deterministic verification. The rules are intentionally small and may
be revised after observing use. Future work such as more detailed time or life
simulation requires a separate design decision.
