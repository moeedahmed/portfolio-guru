# Railway Environment Variables

Set these in the Railway dashboard → your service → Variables tab.

## Required

| Variable | Value | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` | From BWS: ANTHROPIC_API_KEY |
| `TELEGRAM_BOT_TOKEN` | `8625648730:AAG...` | From BWS: PORTFOLIO_GURU_TELEGRAM_BOT_TOKEN |
| `FERNET_SECRET_KEY` | generate below | 32-byte Fernet key — generate once, never change |
| `DATABASE_URL` | `sqlite:///./portfolio_guru.db` | Local SQLite for MVP |

## Generate FERNET_SECRET_KEY

Run this once locally and paste the output:
```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Optional (override credential store for testing)

| Variable | Value |
|---|---|
| `KAIZEN_USERNAME` | your Kaizen email |
| `KAIZEN_PASSWORD` | your Kaizen password |

## Notes
- Railway injects all env vars at runtime — no .env file needed
- DATABASE_URL uses SQLite for MVP; upgrade to Railway Postgres for multi-user V3
- Never commit real values to git — this file documents the keys only
