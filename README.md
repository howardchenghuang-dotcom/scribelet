<div align="center">

# Scribelet

**Record the lecture. Get bilingual study notes. Keep everything on your own server.**

A self-hosted lecture recorder for people who study in English and think in Chinese.<br>
It records in the browser without losing a minute, then turns the class into English–Chinese notes you can actually revise from.

[中文说明](README.zh-CN.md) · [Quick start](#quick-start) · [How it works](#how-it-works) · [Roadmap](#roadmap)

<img src="docs/note.png" alt="A Scribelet note: mind map, core concepts and exam questions, each in English and Chinese" width="860">

</div>

---

## Why this exists

You are three weeks into a course taught in English. The lecture moves fast, the slides skip the reasoning, and by the time you have looked up one term the professor is two ideas ahead.

Recording apps give you a wall of English text. Translation apps give you a wall of Chinese text. Neither gives you what you need the night before the exam: **the structure of the lecture, the key ideas in both languages, and a way to jump back to the exact minute you didn't understand.**

Scribelet does that. It also assumes the things that go wrong will go wrong: the hotel Wi-Fi drops, the laptop sleeps, the API runs out of quota halfway through a three-hour seminar. None of those should cost you a lecture.

## What you get after class

Every recording becomes one note with:

- **A mind map of the whole lecture** — every branch in English and Chinese
- **Core concepts** — definitions, examples and explanations, each paired line by line
- **Framework / process** — steps, when they apply, and the usual mistakes
- **Exam watch** — likely questions with short answers in both languages
- **Timeline** — timestamps to find the part you want to hear again
- **To verify** — anything the model was unsure of, flagged instead of made up
- **The full transcript**, segment by segment, with a Chinese translation under each line

If part of the recording had no usable speech, the note says so at the top instead of quietly leaving gaps.

## Built so you don't lose a class

| What goes wrong | What Scribelet does |
|---|---|
| Network drops mid-lecture | Audio is saved in the browser every ~5 seconds; uploads resume part by part when you're back online |
| You close the tab after class | Unsent recordings resume uploading the next time you open the page |
| The daily model budget runs out halfway | Finished steps are checkpointed; tomorrow it continues where it stopped, without paying twice |
| You want a better note from the same class | Regenerate from the saved transcript, with no new transcription cost |
| The browser "cleans up" your microphone | Echo cancellation, noise suppression and auto-gain are off, so a distant lecturer isn't filtered out as noise |
| A download is cut short | Audio downloads declare their length and are checked part by part, so a short file can't pass as complete |

## Your server, your data

- **Single owner.** No sign-up page and no shared accounts. Password stored as an scrypt hash, with a global login rate limit, CSRF protection and strict host checks.
- **Any OpenAI-compatible provider.** Point it at SiliconFlow (default), OpenAI, a local gateway, or anything else that serves `/audio/transcriptions` and `/chat/completions`.
- **Cost guardrails.** Daily limits on jobs, audio minutes and tokens are reserved *before* each call, then refunded down to what the provider actually billed.
- **A real notes library.** Edit, keep the last 20 versions, trash and restore, permanently delete, import your old Markdown notes, export.
- **Hardened container.** Read-only filesystem, all Linux capabilities dropped, memory and process limits, automatic HTTPS through Caddy.

<p align="center"><img src="docs/capture.png" alt="Recording screen: course title, audio source and the record button" width="760"></p>

## Quick start

You need a server or always-on computer with Docker, a domain pointing at it, and an API key from a model provider.

```bash
git clone https://github.com/howardchenghuang-dotcom/scribelet.git
cd scribelet
python3 setup_private.py      # domain, login, password, provider — secrets stay on this machine
docker compose up -d --build  # Caddy fetches the HTTPS certificate on first start
```

Open `https://your-domain`, sign in, and start recording.

### Choosing a model provider

`setup_private.py` writes these to `private.env`. You can change them at any time and restart.

| Setting | Default | Meaning |
|---|---|---|
| `SCRIBELET_API_BASE` | `https://api.siliconflow.cn/v1` | Any OpenAI-compatible endpoint |
| `SCRIBELET_API_KEY` | — | Your key for that endpoint |
| `SCRIBELET_ASR_MODEL` | `FunAudioLLM/SenseVoiceSmall` | Speech-to-text model (e.g. `whisper-1` on OpenAI) |
| `SCRIBELET_MODEL` | `Qwen/Qwen3.5-35B-A3B` | Model that writes the notes and translations |
| `SCRIBELET_EXTRA_BODY` | — | Extra JSON fields some providers need, e.g. `{"enable_thinking": false}` |
| `SCRIBELET_DAILY_TOKENS` | `1200000` | Daily token budget; enough for a 4-hour recording |

## How it works

```text
 Browser (laptop / iPad)                          Your server
 ───────────────────────                          ───────────
 record tab audio or mic                          FastAPI + SQLite, one worker
   │  saved to IndexedDB every ~5 s                 │
   │                                                ▼
   └── 2 MB parts, resumable ──────────────▶  ffmpeg → 16 kHz mono
                                              cut every ~45 s, inside a pause
                                                    │
                                              speech-to-text, per segment
                                                    │
                                              translate each segment → Chinese
                                                    │
                                              structured bilingual note
                                              (checkpointed at every step)
```

- Cuts land inside natural pauses (ffmpeg `silencedetect`), so a sentence is not split between two segments. The plan is saved, so a retry produces exactly the same segments.
- Long lectures are summarised in batches and then merged, so a 4-hour seminar fits the same pipeline as a 20-minute one.
- Recordings up to 4 hours / 256 MB. Audio and video files can be imported too, as can old Markdown or text notes.

## Status and limits

Scribelet has been in use by its author since September 2026. The test suite has 130+ cases, covering interrupted uploads, truncated downloads, quota exhaustion mid-lecture, restarts and resumes.

Be aware that:

- **The interface is in Simplified Chinese**, and notes are English → Chinese. Other language pairs are on the roadmap.
- **Notes arrive after class, not live.** Live captions are the next big feature (see below).
- **The browser must stay open** while recording. On iPad, browsers cannot capture another app's audio. Use screen recording, then import the file; the app shows you how.
- Scripts for running natively on macOS (launchd services, encrypted off-site backups to iCloud with [age](https://github.com/FiloSottile/age)) are included and tested, but not yet packaged as a one-step installer. Docker is the supported install path.

## Roadmap

- [ ] **Live captions while you listen** — optional and off by default, using a local [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit) server or a cloud streaming API. The full-recording note after class stays the source of truth.
- [ ] English interface
- [ ] Target languages other than Chinese
- [ ] One-step macOS installer, including encrypted off-site backup

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q   # server
npm ci && npm test              # browser behaviour (IndexedDB, upload queue)
```

For a local preview without HTTPS, set `SCRIBELET_LOCAL_PREVIEW=1` and `SCRIBELET_ORIGIN=http://127.0.0.1:8000`.

## License

[MIT](LICENSE)
