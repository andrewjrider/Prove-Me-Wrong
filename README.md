# Prove Me Wrong

A minimal MVP for a "structured disagreement" voting site (provemewrong.info).

## MVP scope

1. A claim (one sentence, admin-entered via `/admin?token=...`)
2. A binary vote: agree / disagree
3. A structured response area for short reasoning/evidence text
4. An AI-generated summary of the strongest arguments on both sides (currently a
   stubbed placeholder in `prove_me_wrong/summarizer.py` — swap in a real LLM
   call later without touching the route)
5. A shareable result card at `/claim/<id>/card` showing the current verdict
   and vote split

No accounts, no user profiles, no monetization, no creator tools. Out of scope
for this MVP.

## Stack

Plain Flask + stdlib `sqlite3`, no ORM — same lightweight pattern as
Street-Acquisition-Agent. No JS framework; server-rendered Jinja templates.

## Running locally

```bash
python -m venv .venv
.venv\Scripts\activate       # Windows
pip install -r requirements.txt
copy .env.example .env       # then edit ADMIN_TOKEN / SECRET_KEY
python run.py
```

App runs at http://127.0.0.1:5050

### Adding a claim (admin, no accounts yet)

Visit `/admin?token=<ADMIN_TOKEN>` (token from your `.env`) and submit a claim.

## Deploy

Not yet deployed. Once verified locally, this repo will be pushed to GitHub
(`Prove-Me-Wrong`) and deployed via a Render Blueprint pointed at
`provemewrong.info`, mirroring the Vehicle Acquisition AI setup.
