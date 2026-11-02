# BRAIN Alpha Generator

A mobile-friendly Streamlit app based on `general copy.ipynb`. It fetches BRAIN data fields for the supplied region, universe, and dataset IDs; generates expressions with Gemini; simulates each expression with `STATISTICAL` and `SUBINDUSTRY` neutralization; and iterates using the previous results.

## Run locally

Requires Python 3.10 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

In the app, sign in with your WorldQuant BRAIN email and password, complete any biometric approval in the opened page, and enter a Google Gemini API key when starting a run. Region, universe, and dataset IDs are editable and default to the notebook's values.

To open the local app from a phone on the same Wi-Fi, run:

```powershell
streamlit run app.py --server.address 0.0.0.0
```

Then open `http://<computer-local-IP>:8501` on the phone. The computer and phone must be on the same network, and the computer firewall must allow port 8501.

## Deploy to Vercel

This repository includes a Vercel serverless API and a static, mobile-friendly web interface. The local Streamlit app remains available separately.

1. Push the project to a GitHub repository and import it in Vercel.
2. Keep the project root as the Vercel root directory. `pyproject.toml` selects the FastAPI entrypoint; `public/` is served as static frontend assets.
3. In **Project → Settings → Environment Variables**, set:
   - `APP_PASSWORD`: a long, unique password to protect the hosted app.
   - `VERCEL_SESSION_SECRET`: a randomly generated secret with at least 32 characters. For example, generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
   - `GENAI_API_KEY`: optional. Set this to keep the Gemini key server-side; otherwise, users enter a key for each run.
4. Deploy with the default Vercel build settings.
5. Open the deployment's HTTPS URL, enter the app password, and sign in to WorldQuant BRAIN.

The API encrypts the BRAIN credentials and session cookies in a short-lived token held in browser memory. Do not use a weak or shared `VERCEL_SESSION_SECRET`; changing it invalidates existing tokens. Each run is capped at 2 iterations and 5 expressions per iteration, and the function has a 60-second timeout to support Vercel Hobby deployments. BRAIN simulations can exceed that limit; reduce the run size or use a plan that allows a longer function duration and increase `maxDuration` in `vercel.json` accordingly. This app does not persist run history between page reloads.

Do not commit credentials, API keys, or environment files to source control.

## Credential handling

- In the local Streamlit app, BRAIN credentials are kept in the current session's memory and are not written to the notebook helper's credentials file.
- For Vercel, BRAIN credentials and cookies are encrypted using `VERCEL_SESSION_SECRET`; the token is kept in browser memory and expires after 12 hours.
- A server-configured Gemini API key stays out of the app form; otherwise, the key is requested per run and is not saved.
- Sign out to clear the BRAIN session and session-held run data.
- Use a trusted HTTPS host for access over the internet. This app is not affiliated with or endorsed by WorldQuant.

If a Gemini key was previously embedded in a notebook or shared, revoke it and create a replacement before deploying.
