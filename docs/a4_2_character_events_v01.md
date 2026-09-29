# A4.2 — Explicit Character Events v0.1

## 1. Event Definition

`CharacterEvent` describes an explicitly supplied fact about something that
just happened. Each event has a UUID `event_id`, UUID `character_id` bound to
`identity.internal_id`, a supported `event_type`, timezone-aware UTC
`occurred_at`, an optional typed payload, and a constrained `source`.
Timestamps more than five minutes in the future are rejected rather than
silently accepted; smaller clock differences are normalized to UTC and then
subject to A4.1 rollback protection.

## 2–3. Event, State, and Memory Boundaries

Event ≠ State: an event is a transient occurrence; the transition service
decides whether/how it changes the one current `CharacterState` row.
Event ≠ Memory: handling an event never creates or retrieves a Memory.
Event ≠ World State and Event ≠ Conversation Message: only explicit typed
events supplied by a caller are accepted; chat text is not parsed into events.

## 4. Event Types and Payload

The supported types are `conversation_session_started`,
`conversation_session_ended`, `activity_started`, `activity_ended`,
`rest_started`, `rest_ended`, `focused_task_started`, and
`focused_task_ended`. Sources are limited to `developer`, `system`,
`conversation`, and `activity`.

The only payload type is `ActivityEventPayload(activity_name: str)`. It is
required for `activity_started`, optional for `activity_ended` (missing name is
a safe no-op), and optional for focused-task events. Other events reject a
payload. Unknown fields and blank activity names are rejected.

## 5–6. Event Handling Flow

`CharacterEventService` is scoped to one configured Character UUID. A mismatched
`event.character_id` returns `handled=False` without creating or changing State.
For accepted events, it maps to the existing A4.1
`CharacterStateTransitionService`; it does not write State directly, modify
Character Data, call an LLM, or create Memory. `EventHandlingResult` includes
event identity/type/key, optional transition result, handled status, reason,
before/after snapshots, and diagnostics. A4.1 clock rollback diagnostics are
passed through unchanged. Event validation errors raise a clear Pydantic
validation error before handling.

## 7. State Mapping and Activity Safety

- Session start/end map to no-state-change transition events.
- Activity start maps to `activity_set`; activity end clears only when the
  payload name exactly matches the current activity.
- Rest start maps to A4.1 `rest_started`; rest end has no direct State change.
- Focus start/end map to A4.1 focus transitions. A supplied activity name on
  focus start may set the activity; focus end does not implicitly clear it.

An activity-end event with no name or a non-matching name is unhandled and
diagnosed, leaving the current activity untouched. This prevents a delayed end
event for an old activity from clearing a newer one.

## 8. Time Semantics

Event times must be timezone-aware, are normalized to UTC, and are rejected if
more than five minutes ahead of the local UTC clock. If an event predates the
current State anchor, A4.1 rollback protection preserves all State fields and
`updated_at`, returning `clock_moved_backwards`.

## 9. Persistence Boundary

Events live only in memory while being handled. There is no event table, log,
replay, or event sourcing. The resulting current State continues to be saved in
the existing single-row-per-Character `character_state` table; its key remains
the fixed `identity.internal_id` UUID.

## 10–11. Known Limitations and Future Work

There is no event persistence or replay, autonomous event generation, LLM event
inference, World event integration, scheduler, offline life simulation,
relationship coupling, event priority, or concurrency control beyond current
single-process assumptions. The CLI exposes only a few local developer event
commands. Any event history, cross-process delivery, or additional event sources
requires a separate design decision.
