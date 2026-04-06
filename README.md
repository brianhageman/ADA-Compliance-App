# ADA Compliance Bot

A Vercel-friendly web app for teachers who just want to upload a classroom document, review accessibility fixes, and download the improved copy.

## What this version does

- Upload `.docx` and `.pptx` files directly from the browser
- Apply the safest automatic fixes immediately
- Show an audit summary with score, changes made, and remaining review items
- Let a teacher approve, defer, or edit suggested accessibility text in the browser
- Download the remediated file immediately
- Download a plain-text accessibility report for documentation

## What it fixes today

- Missing image alt text in Word
- Missing image alt text in PowerPoint
- Generic Word hyperlink text such as `click here`
- Audit flags for:
  - likely heading-style paragraphs in Word
  - pasted raw URLs in Word
  - slides with no visible text that may need title or reading-order review

## Current limits

- Best support is still `.docx` and `.pptx`
- PDFs are not auto-remediated yet
- Alt text suggestions are placeholders and should be reviewed by a human
- Color contrast, table headers, language metadata, and full reading-order repair are not fully automated yet
- Review decisions currently live in the browser and the downloaded report, not inside the document itself
- Large files may be constrained by browser upload size and Vercel function limits

## Project structure

- `index.html`, `app.js`, `styles.css`: static Vercel frontend
- `api/remediate.py`: upload and remediation API
- `api/healthz.py`: lightweight health check
- `app/accessibility.py`: core document remediation and audit logic
- `vercel.json`: Vercel configuration

## Local testing

You can still test the Python remediation logic locally with:

```bash
cd /Users/bhageman/Documents/Arduino/ADA\ Compliance\ Bot
python3 -m py_compile app/accessibility.py api/remediate.py api/healthz.py
```

## Deploy on Vercel

1. Push the repo to GitHub.
2. In Vercel, create a new project from `brianhageman/ADA-Compliance-App`.
3. Keep the default framework setting as `Other` if Vercel asks.
4. Deploy.
5. After deploy, test:
   - `/`
   - `/api/healthz`
   - uploading a sample `.docx` or `.pptx`

## Why this version fits Vercel better

- No Google login flow
- No server-side session persistence
- No SQLite dependency
- No saved output files between requests
- The remediated file is returned directly to the browser for download

## Recommended next steps

1. Improve alt text generation with safer content-aware suggestions.
2. Add PDF auditing with clearer teacher guidance even before true PDF remediation exists.
3. Add optional AI-assisted review text that teachers can accept or edit.
4. Add stronger Office-format auditing for headings, contrast, tables, and lists.
