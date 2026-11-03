# Brain Monkey

Five-pillar daily mental training: FastAPI + MongoDB Atlas + vanilla JS. Day boundary is **IST (UTC+5:30)**.

## Run locally
```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env      # set MONGODB_URI, JWT_SECRET, ADMIN_EMAILS
uvicorn app.main:app --reload --port 10000
pytest                    # 30 tests, no database needed (in-memory Mongo)
```
With `EMAIL_PROVIDER=console` the sign-up link is printed in the server log.

## First run
1. Sign up with the email in `ADMIN_EMAILS`. You are admin, exempt from the 100-seat cap.
2. Open **Admin** in the nav to invite, view and remove users. Invites hold a seat.
3. Set `REGISTRATION_MODE=invite_only` to close public signup entirely.

## Daily content on Render's free tier (no cron service)
Render free has no cron jobs, so use an external scheduler. Schedule **two** calls five minutes apart
(the first may only wake the sleeping instance; the endpoint is idempotent):
```
POST https://<your-app>.onrender.com/internal/generate
Header: X-Cron-Secret: <INTERNAL_CRON_SECRET>
```
cron-job.org (free) works; set 00:05 and 00:10 in Asia/Kolkata. Without it, content is still built lazily
on the first request of the day, and users never wait on Gemini: they get yesterday's verified items
or the bundled bank until the new pool lands.

## Atlas
Render free has no static IP, so allow `0.0.0.0/0` in Atlas Network Access and use a long generated DB password.
