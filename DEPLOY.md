# Deploying FinPilot

FinPilot runs as three containers on one machine, behind one domain:

```
https://your-domain/        ─▶ frontend  (Next.js)
https://your-domain/api/... ─▶ backend   (FastAPI, SQLite, uploads, vector index)
                 ▲
               Caddy (automatic HTTPS)
```

Why one machine and one domain: the backend keeps its database, uploads and
document index on disk and runs uploads on a background thread, so it needs a
long-lived process with a persistent disk (serverless hosts like Vercel can't
run it). Serving the app and the API from the same domain keeps the login
cookie first-party.

## 1. Get a server

Any Linux VM with Docker works. For Indian users pick an India region
(Mumbai / Bangalore).

| Provider | Plan | Approx. cost |
|---|---|---|
| DigitalOcean (BLR1) | 2 vCPU / 4 GB | ~$24 / month |
| AWS Lightsail (Mumbai) | 2 vCPU / 4 GB | ~$24 / month |
| Hetzner | CPX21 (no India region) | ~€8 / month |

4 GB RAM is the comfortable minimum (pandas, scikit-learn and the embedding
model). Use Ubuntu 24.04.

## 2. Point your domain at it

At your domain registrar, add an **A record**: `finpilot.yourdomain.com → <server IP>`.
Wait until `ping finpilot.yourdomain.com` answers from that IP.

## 3. Install Docker on the server

```bash
ssh root@<server IP>
curl -fsSL https://get.docker.com | sh
```

Open ports 80 and 443 in the provider's firewall (and `ufw allow 80,443/tcp` if
ufw is on).

## 4. Put the code on the server

```bash
git clone <your repo URL> finpilot && cd finpilot
# (or copy the FInpilot folder up with scp/rsync)
```

## 5. Configure

```bash
cp .env.production.example .env
nano .env        # DOMAIN, GEMINI_API_KEY, Razorpay keys
```

## 6. Start it

```bash
docker compose up -d --build
docker compose ps          # all three should be "running" / "healthy"
docker compose logs -f backend
```

Open `https://finpilot.yourdomain.com`. Caddy fetches the HTTPS certificate on
the first request (a few seconds).

## 7. Payments

1. Create a Razorpay account and complete KYC. Razorpay needs a live HTTPS
   site with a **privacy policy, terms, refund policy and contact page** before
   it activates live payments.
2. Start with **test keys** (`rzp_test_...`) in `.env`, then
   `docker compose up -d` and buy Pro with a Razorpay test card.
3. Switch to live keys once activated.

Keep `PAYMENTS_DEV_MODE=false` in production: dev mode upgrades accounts
without payment.

## Day-to-day

**Update to a new version**

```bash
git pull && docker compose up -d --build
```

**Logs**: `docker compose logs -f backend` (or `frontend`, `caddy`).

**Back up** everything that matters (database, uploads, reports, vectors) --
run nightly from cron and copy the file off the server:

```bash
docker run --rm -v finpilot_finpilot-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/finpilot-$(date +%F).tgz -C /data .
```

**Restore**: stop the stack, untar into the same volume, start it again.

## Compliance checklist (do these before real users)

| | What | Where |
|---|---|---|
| ☐ | **Enable billing on the Gemini API key** -- on the free tier Google may use prompts to improve its products. Paid tier: it doesn't. | Google AI Studio → Billing |
| ☐ | Fill in operator name, address, emails, grievance officer and jurisdiction, get a lawyer to review the four policy pages, then set `draft: false` | `finpilot/frontend/src/lib/legal.ts` |
| ☐ | Fill the bracketed provider names in the Privacy Policy (hosting, email) and the GST line in the Terms | `src/app/privacy`, `src/app/terms` |
| ☐ | Appoint the Grievance Officer and make sure that inbox is watched | -- |
| ☐ | Configure SMTP so password-reset emails actually send | `.env` |
| ☐ | Razorpay KYC; then live keys; `PAYMENTS_DEV_MODE=false` | `.env` |
| ☐ | Keep the server clock synced (`timedatectl set-ntp true`) -- CERT-In requires accurate log times | server |
| ☐ | Turn on your provider's encrypted disks and nightly backups | provider console |
| ☐ | GST registration / invoices if applicable | your accountant |

What the app already does: consent (Terms + Privacy + 18+) is recorded at sign-up with its
version; users can download all their data and delete their account in-app; right after each
analysis they choose "delete now" or "keep for 30 days", every upload is erased 30 days
after its analysis (Pro can extend), with reminder emails 7 days and 1 day before; account, card, UPI, phone and email identifiers are masked
before storage; app/audit logs (`/data/logs`) and proxy access logs (`caddy-logs` volume)
are kept 180 days; reports and chat carry a not-financial-advice disclaimer.

## If there is a data breach

1. **Contain** -- stop the stack (`docker compose stop`), rotate secrets: Gemini key,
   Razorpay keys, SMTP password. Sign everyone out by clearing sessions:
   `docker compose exec backend python -c "from app.db.session import session_scope; from app.db.models import AuthSession; s=session_scope().__enter__(); s.query(AuthSession).delete(); s.commit()"`.
2. **Preserve evidence** -- copy `/data/logs` and the `caddy-logs` volume before anything rotates.
3. **Report to CERT-In within 6 hours** of noticing it (incident@cert-in.org.in; form at cert-in.org.in).
4. **Notify the Data Protection Board of India and every affected user** without delay:
   what happened, what data, what you're doing, what they should do (e.g. watch for phishing).
5. **Fix, then restore** from the last clean backup; write down what happened and what changed.

## Before real users

- [x] Privacy policy, terms, refund and contact pages (drafts -- see the checklist above).
- [x] Consent at sign-up; download and delete my data in-app.
- [x] Password reset by email (configure SMTP).
- [x] Sign-in rate limiting.
- [ ] Set a Gemini spending limit in Google AI Studio, and watch per-user token
      use (`usage_events` table) before sizing the free tier.
- [ ] Nightly off-site backups (above).

## Scaling later

The backend deliberately runs a single process (in-process job queue, SQLite).
That serves hundreds of users. Beyond that, the path is: Postgres instead of
SQLite, a real job queue (Redis + RQ/Celery) instead of the thread pool,
object storage (S3/R2) for uploads, and a managed vector store -- then the
backend can run several replicas.
