# Vision Input & Sticker Understanding v0.1

## Architecture and scope

`OneBot image segment → MediaFetcher → VisionProvider → VisualObservation → Relevant Context → Tiny Planner → Replyer`

Only `SI_REPLY_PIPELINE=natural_simplified` consumes visual observations. Vision describes
what is shown; it never generates or sends Character speech. Tiny Planner still has
four expression fields (`focus`, `stance`, `boundary`, `ask`), and the existing combined
affective appraisal remains in that same call. Replyer retains Character, relationship,
affect, memory, Mini Life and grounding authority. Text replies still use two main calls.
`natural` / `natural_full` / `legacy` retain their original text pipelines and safely
describe image content as unavailable without calling Vision.

## Reuse and provider

Direct reuse of existing HTTPX (BSD-3-Clause) and Pydantic (MIT) dependencies; no new SDK,
framework, local model, OCR or embedding dependency. The small `VisionProvider` protocol
accepts image bytes plus optional accompanying text and returns a validated observation.
The external adapter uses OpenAI-compatible `/chat/completions`, text plus base64
`image_url`, strict JSON validation and no automatic retry. It follows the existing
HTTPX provider style without widening the text LLM interface.

References: [OneBot image segments](https://github.com/botuniverse/onebot-11/blob/master/message/segment.md),
[SiliconFlow vision API](https://docs.siliconflow.cn/docs/userguide/capabilities/vision),
[HTTPX TLS SNI extension](https://www.python-httpx.org/advanced/extensions/).
No external implementation code copied. An external model requires separately evaluated
provider privacy/retention terms; local ephemeral handling does not guarantee provider deletion.

## Config

Configure the existing ignored `config/si.env` (OS environment still takes precedence):

```ini
SI_REPLY_PIPELINE=natural_simplified
SI_VISION_ENABLED=true
SI_VISION_PROVIDER=openai_compatible
SI_VISION_BASE_URL=https://api.siliconflow.cn/v1
SI_VISION_MODEL=
SI_VISION_API_KEY=
```

Fill model with an image-capable model supported by the selected provider and configure
the secret locally; the example contains no working credentials. Default is disabled,
which requires no Vision key and performs no media download or Vision call. These are
manual env settings in v0.1, not new Console fields or connection tests. Restart Core
after changing them. Base URL must be HTTPS without embedded credentials/query/fragment.

`SI_VISION_MEDIA_HOSTS` optionally replaces the exact media-host allowlist. Defaults:
`gchat.qpic.cn,c2cpicdw.qpic.cn,multimedia.nt.qq.com.cn,multimedia.nt.qq.com`.
Only add trusted HTTPS media origins; wildcard hosts and private addresses are not supported.

## Observation and context

Frozen, extra-forbidden `VisualObservation` fields:

| Field | Meaning / bound |
| --- | --- |
| media_type / is_sticker | image or sticker; consistent boolean |
| summary | main visible content, 1–240 characters |
| visible_text | clear relevant text, null if uncertain, ≤360 characters |
| subjects / scene | ≤5 subjects of ≤40 characters; optional scene ≤80 characters |
| emotion / intent | bounded labels for image expression / possible communication intent |
| humor / confidence | none/mild/strong; finite 0–1 |

Sticker classification is semantic, not filename-based. Image emotion is not proof of
the user's emotion. Unreadable text may be null. No identity, sensitive-attribute or
invisible-story inference is requested. The strict fields, not raw responses/reasoning,
enter compact context. Image text is placed in an explicitly untrusted **user-role**
message for Replyer and in the Planner's user context, not its system instructions.
Both prompts reject image-borne system/developer/tool/secret instructions. This reduces
instruction confusion; it is not a mathematical guarantee against model prompt injection.

Images can activate existing relevant Seed traits (e.g. horror dislike) but never rewrite
them. Pictures only establish that they were shown, not user location, authorship,
identity, a realtime camera or a shared physical world. World State is never updated.

The current turn includes accompanying text and an explicit same-turn scope. An in-memory
cache per validated relationship-target UUID can support `就是这个`, `你看这个`, `好笑吧`,
`这个怎么样` within 180 seconds and the next two turns of that target. At most 32 targets
are retained. Unrelated turns consume the turn budget but do not inject images. New image
results replace old ones, including failed observations. Nothing is restored after restart.

## Media safety, privacy and failure

Authorization and message dedup happen before media fetching. Structured OneBot text/image
segments support pure images and mixed messages, first three images only (≤100 total
segments). Use `data.url`, or an HTTPS `data.file` value. File IDs, local paths and string
CQ-image messages are not resolved in v0.1. No `get_image` transport RPC is added.

Downloads accept only allowlisted HTTPS hosts/port 443 without credentials, fragments,
redirects or environment proxy configuration. All resolved IPs must be public; the selected
IP is pinned and original Host/TLS SNI retained against DNS rebinding. HTTP response size
is streamed and bounded to 8 MiB/image; declared MIME must match PNG/JPEG/static-WebP
signature. No decoder is installed: full image integrity is left to the external API.
GIF, animated WebP, SVG and video are unavailable. Limits live in `vision.py`.

Download timeout is 10 seconds (connect 5), Vision timeout 20 seconds (connect 5);
response-reading elapsed budgets and a 64 KiB Vision response limit additionally apply.
These are per-operation budgets, not a strict whole-turn deadline: OS DNS lookup and up
to three serial images can add latency. There is no API polling or background worker.
Timeouts, bad status, bad JSON, empty/invalid observations, MIME mismatch and oversized
images become a safe unavailable observation; the existing conversation still replies.
Neither raw errors nor fabricated descriptions are sent as Character speech.

Image bytes/base64 exist only during the request in memory; no temporary image files,
media DB tables, library or image retention. Archive retains accompanying user text plus
a count placeholder and the normal assistant reply; no URL, base64 or observation JSON.
Short-lived observations remain in memory. Image and explicit short-followup turns skip
automatic Memory Formation; later ordinary text follows unchanged formation rules.
There are no new database writes beyond existing archive/state/affect behavior.

Logs contain status/elapsed counts only, not image text or payload. Context-local HTTP
log filters suppress signed-URL request logs without changing global logging levels.
Existing generic env-secret redaction covers `SI_VISION_API_KEY`. No new Console inspector
or media-library UI is added.

## Offline acceptance / limitations

`tests/fixtures/vision_cases.json` contains eight synthetic observations: tired cat, anger,
celebration, speechless, refusal, teasing, text meme, ordinary photo. These validate
structure/wiring, not actual visual accuracy or naturalness.

Run `python scripts/run_vision_input_smoke.py` for four complete fake OneBot image → real
Conversation → Tiny Planner/Replyer cases, including failure and mixed text. It uses
temporary SQLite databases, MockTransport and FakeVisionProvider, measures preprocessing,
fake provider time/context size/two text calls, and makes zero external API calls.

Real Linux/QQ/SnowLuma image URL shape, CDN MIME, TLS IP pinning, selected provider/model
compatibility, OCR/sticker accuracy, latency, grounding and injection robustness still need
live acceptance. No real API or QQ validation is claimed. Unsupported images fall back
honestly. Future Sticker Library ingestion or Reply Intent → Sticker Selector → OneBot
image sending are separate work; neither is implemented here.
