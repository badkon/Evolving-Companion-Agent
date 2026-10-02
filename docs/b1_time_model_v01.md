# B1 — Time Model v0.1

## 1. Time Model Definition

System Clock supplies the current instant; Character State, Conversation History, and World Time remain separate concepts. The Time Model interprets the current instant for the configured Character timezone. It does not simulate activity.

## 2. Clock Abstraction

`Clock.now_utc()` returns an aware UTC `datetime`. `SystemClock` reads the system clock; `FixedClock` provides deterministic `set` and `advance` operations for tests and smoke checks. State, transition, event, and Conversation runtime time paths accept an injected Clock.

Archive and Memory record creation timestamps remain generated within `storage.py`; they are not yet routed through the Clock.

## 3. Character Timezone

The Character Seed has an IANA timezone validated with `zoneinfo.ZoneInfo`. SI-001 currently uses `Asia/Shanghai` as an explicit development-time Character configuration. It is not inferred from the user's location or device timezone. Character Projection does not expose it as identity/personality data.

## 4. Time Snapshot

`CharacterTimeSnapshot` contains UTC and local now, timezone, local date/time, last successful interaction, offline duration, and diagnostics. It is an ephemeral interpretation, not a persisted snapshot.

## 5. Last Interaction Semantics

`last_interaction_at` is written only after an assistant response was successfully generated and archived. B8 uses the shared turn timestamp for this write (not a second wall-clock sample at completion); LLM latency is not included in the anchor. It is not an Archive creation timestamp, Memory Formation time, Event time, or State update time. A failed LLM response does not update it.

Assistant archival defines core conversation completion. If subsequent interaction timestamp persistence fails, the stored anchor may lag behind; Conversation returns the archived reply, preserves history, records only the error type in `last_interaction_error`, and does not invent a fallback timestamp.

## 6. Offline Duration

Offline duration is `now_utc - last_interaction_at`; no prior interaction yields `None`. `character_runtime` stores one row per `identity.internal_id` UUID. A backwards clock yields zero duration and `clock_moved_backwards`; it does not move the stored anchor backwards.

## 7. Conversation Integration

Conversation archives the user message, obtains one UTC timestamp, applies State reconciliation and builds a Time Snapshot with that same timestamp, then proceeds through recall, Prompt, and LLM. After successful assistant archival, in-memory history is updated first; completion timestamp persistence and Memory Formation then run independently as best-effort side effects. Timestamp failure does not skip formation. Assistant archival failure still propagates and prevents completed history, timestamp updates, and formation. Archive timestamps themselves remain storage-managed. B2 reconciliation uses persisted field-level State anchors independently of last interaction; see [B2](b2_temporal_state_reconciliation_v01.md).

## 8. State Integration

CharacterStateService and CharacterStateTransitionService accept the shared Clock. Conversation supplies the same `now_utc` to elapsed transition. Existing A4.1 rollback protection remains intact.

## 9. Event Integration

CharacterEventService fills omitted `occurred_at` from its injected Clock and evaluates the existing five-minute future-skew limit against that Clock. Event payload and State mapping semantics are unchanged.

## 10. Prompt Projection

The distinct `【当前时间】` section contains local date, local time, and a concise offline duration or “无记录”. It omits UTC timestamps, UUIDs, timezone object representations, raw timedeltas, and database metadata. Prompt instructions state that elapsed offline time does not establish lived experience.

## 11. Clock Rollback

Time Snapshot clamps negative offline duration to zero and emits a diagnostic without changing the interaction anchor. State rollback behavior remains A4.1's no-op with preserved `updated_at`.

## 12. Known Limitations

There is no routine, sleep/wake, calendar, scheduled activity, offline-life simulation, world-time simulation, timezone travel, natural-language date parsing, reminder system, or background scheduler. Archive/Memory timestamps are not yet injected from Clock.

## 13. Future Work

Further temporal behavior belongs to later explicitly scoped stages. B1 does not imply any inference about what the Character did while offline.
