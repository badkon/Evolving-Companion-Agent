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
cue for expression. No state history, automatic inference, decay, or world
simulation is implemented.

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
