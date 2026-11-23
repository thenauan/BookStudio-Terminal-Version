# BookStudio

**Audiobooks, e-book translation and conversion, and synced text — in the terminal, on Windows, macOS and Linux.**

BookStudio is the new name of **Audiobook Generator (terminal edition)**, now merged with **Ebook Studio**. One app, one menu:

| | What it does |
|---|---|
| 🎧 **Create an audiobook** | E-book (EPUB, PDF, MOBI, AZW3, TXT) → audiobook (M4B, MP3, Opus, FLAC, WAV) with chapters, cover, metadata and, if you want, synced text. 313 Microsoft neural voices in 73 languages. |
| 🌍 **Translate and create an audiobook** | Translates the book (AI or free Google), then narrates it in the new language. |
| 📝 **Generate synced text** | For an audiobook you already have: with the **audio and the e-book** (most accurate), **audio only** (AI transcription) or **text only** (no AI, approximate). |
| 🌍 **Translate and generate synced text** | Translates the e-book into the audiobook's language and aligns the two. |
| 📖 **Generate an e-book from an audiobook** | AI transcribes the audio into an e-book, one chapter per audio chapter. |
| 🔄 **Convert an e-book's format** | EPUB, PDF, TXT and, with [Calibre](https://calibre-ebook.com), MOBI, AZW3, DOCX, FB2, RTF, ODT. |
| 🌐 **Translate an e-book** | Keeps chapters, headings, italics, bold, links and images (EPUB), in the same format or another one. |
| ✏️ **Edit metadata** | Title, author, series, publisher, year, genres, ISBN, language, description. |

The interface speaks **English, Spanish, Portuguese, Russian, Arabic, French, Italian, Turkish, Hindi, Chinese and Urdu** and opens in your system language (English if yours isn't on the list).

```
╭───────────────────────────────────────────────────╮
│   📚  BookStudio  v2.0.0                          │
│   Audiobooks, e-book translation and conversion,  │
│   and synced text, in the terminal                │
╰───────────────────────────────────────────────────╯
› What do you want to do?
 » 🎧 Create an audiobook  audio and, if you want, synced text
   🌍 Translate and create an audiobook  translates the book and narrates it in the new language
   📝 Generate synced text  for an audiobook you already have
   …
```

## Installation

You need **Python 3.10+**. **FFmpeg** is recommended: without it, audiobooks only come out as MP3 (without chapter volume levelling).

### Download

Get the latest version from **[Releases](https://github.com/thenauan/BookStudio/releases/latest)** (`.zip` for Windows, `.tar.gz` for macOS and Linux) and unzip it anywhere. The first time it opens, BookStudio checks its Python libraries and offers to install the missing ones.

| System | Python and FFmpeg |
|---|---|
| **Windows** | [python.org](https://www.python.org/downloads/) (check *Add Python to PATH*) and `winget install Gyan.FFmpeg` |
| **macOS** | `brew install python python-tk ffmpeg` |
| **Linux** (Debian/Ubuntu) | `sudo apt install python3 python3-pip python3-tk ffmpeg` |
| **Linux** (Fedora) | `sudo dnf install python3 python3-pip python3-tkinter ffmpeg` |
| **Linux** (Arch) | `sudo pacman -S python python-pip tk ffmpeg` |

### Or with pipx (one command)

```bash
pipx install git+https://github.com/thenauan/BookStudio.git
bookstudio
```

Update with `pipx upgrade audiobook-generator-terminal` (the package keeps its old name so existing installs keep updating). `audiobook-generator`, the old command, still works.

### Or clone

```bash
git clone https://github.com/thenauan/BookStudio.git
cd BookStudio
python -m pip install -r requirements.txt
```

> On Linux distributions that block the system `pip` (*externally-managed-environment*), use a virtual environment:
> `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`.

Two heavy libraries are optional and installed by the app only when you use the feature that needs them: **litellm** (translating with AI, ~100 MB) and **faster-whisper** (synced text and e-books from audio, ~200 MB).

## Usage

| System | How to start |
|---|---|
| **Windows** | double-click `BookStudio.bat` |
| **macOS** | double-click `BookStudio.command`, or run `./bookstudio.sh` in Terminal |
| **Linux** | `./bookstudio.sh` |
| **Installed with pipx/pip** | `bookstudio` |
| **Any** | `python -m bookstudio` |

Menus work with the arrow keys and Enter; long lists (voices, languages) filter as you type. **Esc** goes back.

### Pause, cancel and resume

In any task — audiobook, translation, synced text — **Ctrl+C** asks what to do:

- **Continue**;
- **Pause and continue later**: the job is saved, and the next time you open BookStudio it offers to continue where it stopped. Narrated parts and translated passages are reused, never done (or paid for) twice;
- **Cancel**.

### Progress

Every task shows the percentage, the current step, the parts done, the download speed, the size so far and the time left, updated every second. The time left comes from the real pace of the task (recent pace plus overall average), so it doesn't jump around.

### Audiobooks

The steps: books → information, translation, synced text and voice → format → folder → summary → conversion. To hear a voice, pick "listen to a voice" in the voice list.

**Faster in 2.0.** In a test book (41,000 characters, M4B), the conversion went from 114 s to 63 s:

- Voice connections start at 16 and adjust by the total narrated per second (they used to drop because each request got a bit slower, even when the total was going up). Range: 3 to 32.
- Chapters are narrated in order, so each chapter's volume is adjusted while the next ones are still being narrated.
- Volume levelling measures the loudness and applies a fixed gain with a peak limiter: the same −18 LUFS result, about 8× faster than before.
- M4B is encoded chapter by chapter in parallel.
- With the window in front (Windows), the conversion runs at normal priority with up to 32 connections; minimized, it uses a bit less so you can keep using the computer.

**Lite mode** (one book at a time, 4 connections, lowest priority) turns on by itself on lighter computers, and you can turn it on or off in ⚙️ Settings or with `--lite` / `--no-lite`.

#### What goes inside each format

| Format | Title, author, genre, year, narrator | Cover | Chapters | Synced text inside the file |
|---|---|---|---|---|
| **M4B** | ✔ | ✔ | ✔ | ✔ |
| **MP3** | ✔ | ✔ | ✔ | ✔ |
| **MP3 per chapter** | ✔ | ✔ | each file is a chapter | separate files only |
| **Opus** | ✔ | ✔ | ✔ (`CHAPTERxxx` tags) | ✔ |
| **FLAC** | ✔ | ✔ | ✔ (`CHAPTERxxx` tags) | ✔ |
| **WAV** | only some programs show them | only in some players | only in some players | separate files only |

Poetry books are split by poem (title and poem), and books with chapters and subtitles keep the subtitles inside their chapter.

#### From the command line (as in 1.x)

With files on the command line, BookStudio creates the audiobook straight away:

```bash
bookstudio *.epub --voice en-US-AndrewNeural --format m4b --no-sync --yes -o ~/Audiobooks
```

| Option | What it does |
|---|---|
| `--voice NAME` | voice (e.g. `pt-BR-AntonioNeural`, `en-GB-SoniaNeural`) |
| `--format` | `m4b`, `mp3`, `mp3-chapters`, `opus`, `flac` or `wav` |
| `--quality` | `voice` (default), `standard`, `high` or `max` |
| `--translate LANG` | translate the books before narrating |
| `--sync` / `--no-sync` | create (or not) synced text |
| `--sync-detail` | `both` (default: sentences and words), `sentences` or `words` |
| `--sync-save` | `both` (default), `metadata` (inside the audiobook) or `file` (separate files) |
| `-y`, `--yes` | no questions (uses your last choices) |
| `--lite` / `--no-lite` | lite mode on or off |
| `-o`, `--output` | output folder |
| `--lang` | interface language for this run; with no value, opens the list and saves your choice |

### Synced text

Synced text highlights the sentence — and the word — being read, in audiobook players that support it. It is saved inside the audiobook (M4B, MP3, Opus, FLAC) and/or as `.lrc`, `.srt` and `.sync.json` next to it. Words keep the book's punctuation, dialogue stays together, and very long sentences are split at natural points.

For an audiobook you already have:

| Mode | How | Accuracy |
|---|---|---|
| **Audio and e-book** | AI listens to the audio and aligns the book's own text to it | the best: the book's exact words, with each word's timing |
| **Audio only** | AI transcribes what is spoken | good; **may get names, similar words and punctuation wrong** |
| **Text only** | the book's sentences are spread over each audio chapter | approximate, no AI |

**Generate an e-book from an audiobook** uses the same transcription, so the e-book **may contain mistakes** too. BookStudio says so before starting.

Transcription uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on your computer (nothing is uploaded), with the `small` model (`base` in lite mode). It uses an NVIDIA GPU when there is one.

### Translation

| Engine | Quality | Cost | Needs |
|---|---|---|---|
| 🤖 **AI** (Claude, GPT, Gemini, DeepSeek, Mistral, OpenRouter) | High | paid to the provider, per use | an API key |
| 🤖 **Local AI** (Ollama) | Good, depends on the computer | free | Ollama and a downloaded model |
| 🌐 **Free Google** | Average | free | nothing |

- The book's language is detected automatically; you can change it.
- Before starting, you see the passages, the requests, the **estimated cost** and the time. A **spending cap** per book (default US$ 5) pauses the translation when reached.
- API keys are kept in the system vault (Windows Credential Manager, macOS Keychain…), never in a text file. Environment variables work too: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `MISTRAL_API_KEY`, `OPENROUTER_API_KEY`.
- Each translated passage is cached: pausing, resuming or retrying never pays twice.
- Each answer is checked (missing paragraphs, empty text, broken formatting, text still in the original language) and redone if needed. Names are kept consistent through a glossary built from the book.

**Faster in 2.0:** long chapters are translated in parallel lanes instead of one batch at a time, and free Google sends 4,500 characters per request instead of 1,800 (fewer requests and fewer blocks).

### E-book formats

| Format | Read | Write | Needs Calibre |
|---|---|---|---|
| EPUB | ✔ | ✔ | no |
| TXT | ✔ | ✔ | no |
| PDF | ✔ | ✔ | only for PDF with images (without it: simple text PDF) |
| MOBI, AZW3 | ✔ | ✔ | yes |
| DOCX, FB2, RTF, ODT | ✔ | ✔ | yes |

PDFs are read as running text (page layout and images aren't kept). Scanned PDFs (images only) and files with DRM aren't supported.

## Updates

When it opens, BookStudio checks GitHub for a new version in the background. If there is one, it shows what's new — translated into the interface language — and offers to update right there, the right way for how you installed it (pipx, pip, git or the downloaded package).

## Privacy

- With online AI or Google, the book's text is sent to that service to be translated. With Ollama, everything stays on your computer.
- Narration uses Microsoft's online voice service. Transcription (faster-whisper) runs on your computer.
- Use it only with books you own, for personal use.

## For developers

```
BookStudio.bat / .command / bookstudio.sh   launchers
bookstudio/app.py                           menu, arguments, resuming paused jobs
bookstudio/ui.py                            look: Rich panels, questionary menus, progress panel, time estimate
bookstudio/audiobook/                       audiobook flow (books, choices, engine runner)
bookstudio/ebook/                           e-books: formats, translation (AI/Google), cache, menus
bookstudio/sincronia.py                     synced text and e-book from audio (UI)
bookstudio/sincronia_motor.py               transcription and alignment, in a separate process
bookstudio/motor/                           conversion engine, shared with the Audiobook Generator desktop app
bookstudio/dados/                           voices, language names, interface translations
ferramentas/                                maintenance scripts and tests
```

```bash
python -m pip install pytest
python -m pytest ferramentas/testes
```

`python ferramentas/atualizar_motor.py` copies the engine from the desktop app. With `BOOKSTUDIO_MOTOR_TESTE=1`, a test translation engine (no network) shows up in the menu.
