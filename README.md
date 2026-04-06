# ADA Compliance Bot

A phased, teacher-ready local web app for remediating common document accessibility issues.

## Product shape

This version is designed around a practical school workflow:

- Teachers can upload Word and PowerPoint files directly.
- Teachers can sign in with Google and browse recent Drive files.
- Google Docs and Google Slides are exported into Office formats first.
- The app remediates the Office copy.
- The remediated file is uploaded back to Drive as a new accessible copy.
- The app creates a teacher review queue for issues that still need human judgment.

That gives us a real Google Workspace flow without pretending that fully automatic, perfect native-file remediation already exists.

## What works now

- Local upload remediation for `.docx` and `.pptx`
- Google OAuth sign-in
- Drive file listing for Docs, Slides, Office files, and PDFs
- Export from Google Docs to `.docx`
- Export from Google Slides to `.pptx`
- Upload of the remediated accessible copy back to Drive
- Audit summary with score, auto-applied fixes, and remaining manual checks
- Teacher review queue with approve, defer, and edit-in-place suggestion review
- Downloadable accessibility report for documentation
- Automatic fixes for:
  - missing image alt text in Word
  - missing image alt text in PowerPoint
  - generic Word hyperlink text such as `click here`
- Audit checks for:
  - possible heading-style paragraphs in Word
  - pasted raw URLs in Word
  - slides with no visible text that may need title or reading-order review

## Current limits

- Alt text is placeholder-quality and should still be reviewed by a human
- PDF remediation is not automated yet
- Native Google Docs and Slides are not edited in place
- Color contrast, heading structure, reading order, table headers, and language metadata are not fully remediated yet
- SQLite-backed sessions are suitable for one deployed instance, but not yet for scaled multi-instance hosting
- Teacher approvals are currently tracked in the browser and exported in the report, not written back into the document yet

## Setup

1. Create a Google Cloud OAuth client for a web application.
2. Add an authorized redirect URI matching your local app or hosted app, for example:

```text
http://127.0.0.1:8000/auth/google/callback
```

3. Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

4. Add your Google credentials to `.env`.
5. Set `PUBLIC_BASE_URL` to the exact URL teachers will use.

## Run

```bash
cd /Users/bhageman/Documents/Arduino/ADA\ Comliance\ Bot
python3 app/server.py
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Deploy

This project now includes:

- [Procfile](/Users/bhageman/Documents/Arduino/ADA%20Comliance%20Bot/Procfile) for process-based deployment
- [render.yaml](/Users/bhageman/Documents/Arduino/ADA%20Comliance%20Bot/render.yaml) for Render
- SQLite-backed session and report storage in `data/ada_bot.db`
- Production environment settings for host binding, cookie security, public URL, and upload size limits

### Render checklist

1. Push the project to a Git repository.
2. Create a new Render web service from that repository.
3. Set these environment variables in Render:
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
   - `PUBLIC_BASE_URL`
   - `GOOGLE_REDIRECT_URI`
   - `COOKIE_SECURE=true`
4. In Google Cloud, add the hosted callback URL, for example:

```text
https://your-app-name.onrender.com/auth/google/callback
```

5. Deploy and verify [healthz](/Users/bhageman/Documents/Arduino/ADA%20Comliance%20Bot/app/server.py#L46) responds successfully.

### Production notes

- This is ready for a single-instance deployment.
- Uploaded files and generated outputs still live on disk, so long-term hosting should add cleanup or object storage.
- Review reports are persisted in SQLite, but teacher approvals are not yet written back into the document file itself.

## Recommended next phases

1. Add richer document auditing:
   heading order, table headers, contrast checks, reading order, list semantics, and document language.
2. Add AI-assisted suggestions:
   better alt text, clearer link labels, and teacher approval before applying risky changes.
3. Add Google-native writeback:
   use Docs and Slides APIs for targeted native edits where that is safer than export-import.
4. Add admin reporting:
   school-wide dashboards, remediation queues, and exportable compliance summaries.
5. Harden further for production:
   object storage, scheduled cleanup, background jobs, admin controls, and multi-instance session storage.
