# ClipLingua Data Format (`schema_version = 2`)

Every lesson is stored locally under `lessons/<lesson_id>/` using plain UTF-8 JSON files written atomically (`.tmp` + `os.replace`).

## Directory Layout

```text
lessons/
├── review.json              # Cross-lesson spaced repetition state
└── <lesson_id>/
    ├── lesson.json          # Metadata, language, native_lang, status
    ├── video.mp4            # H.264 video (omitted for audio-only lessons)
    ├── audio.wav            # 16kHz mono WAV for ASR & range playback
    ├── thumb.jpg            # Generated thumbnail cache
    ├── transcript.json      # Sentences, word-level timestamps, OCR/AI suggestions
    ├── subtitles.json       # Cached RapidOCR frame outputs (regenerable)
    ├── captions.json        # Optional matched caption track
    ├── vocab.json           # Selected vocabulary keys & token positions
    ├── translation.json     # Imported bilingual sentences & word analyses
    ├── progress.json        # Stage progression & practice rounds
    └── recordings/          # Local shadowing recordings (s001_YYYYMMDD-HHMMSS.webm)
```

## Key Schemas

### `lesson.json`
```json
{
  "schema_version": 2,
  "id": "20261123-120000",
  "title": "Fika i Stockholm",
  "author": "",
  "language": "sv",
  "native_lang": "en",
  "source_url": "file:sample.mp4",
  "video_id": "",
  "duration": 42.5,
  "created_at": "2026-11-23T12:00:00+01:00",
  "status": "ready",
  "error": null
}
```
- `language` / `native_lang`: `"sv" | "en" | "es" | "zh"`.

### `translation.json`
```json
{
  "schema_version": 2,
  "native_lang": "en",
  "imported_at": "2026-11-23T12:05:00+01:00",
  "sentences": [
    { "id": 1, "text": "Sometimes a small café offers cinnamon buns.", "src": "Ibland erbjuder..." }
  ],
  "words": [
    {
      "key": "erbjuder",
      "text": "offers",
      "lemma": "erbjuda",
      "pos": "verb",
      "note": "erbjuder / erbjöd / erbjudit",
      "in_sentence": "offers"
    }
  ]
}
```
- `pos` uses language-neutral standard codes: `noun | verb | adj | adv | pron | prep | conj | num | interj | phrase`.

### `review.json`
- Entries are keyed by `lang|lemma|pos` (e.g., `sv|erbjuda|verb`, `es|empezar|verb`, `zh|语感|noun`).
- Levels `0..5` map to intervals `[0, 1, 3, 7, 15, 30]` days and modes `["listen", "listen", "recall", "recall", "spelling", "cloze"]`.
