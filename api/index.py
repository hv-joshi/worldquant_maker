import base64
import hashlib
import json
import os
import secrets
import time
from typing import Any

import requests
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

import ace_lib as ace
from app import DEFAULT_DATASET_IDS, DEFAULT_REGION, DEFAULT_UNIVERSE, parse_dataset_ids, run_workflow


app = FastAPI(title="BRAIN Alpha Generator API")
SESSION_TTL_SECONDS = 12 * 60 * 60
MAX_ITERATIONS = 2
MAX_ALPHAS_PER_ITERATION = 5


class SignInRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class BiometricRequest(BaseModel):
    session_token: str


class RunRequest(BaseModel):
    session_token: str
    gemini_api_key: str | None = Field(default=None, max_length=512)
    region: str = Field(default=DEFAULT_REGION, min_length=1, max_length=32)
    universe: str = Field(default=DEFAULT_UNIVERSE, min_length=1, max_length=64)
    dataset_ids: str = Field(default=DEFAULT_DATASET_IDS, min_length=1, max_length=4096)
    iterations: int = Field(default=1, ge=1, le=MAX_ITERATIONS)
    num_alphas: int = Field(default=3, ge=1, le=MAX_ALPHAS_PER_ITERATION)
    initial_description: str = Field(
        default="Start with simple expressions using available data fields.",
        min_length=1,
        max_length=1000,
    )


def _fernet() -> Fernet:
    secret = os.environ.get("VERCEL_SESSION_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(
            status_code=503,
            detail="Set VERCEL_SESSION_SECRET to a random value of at least 32 characters.",
        )
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _check_app_password(password: str | None) -> None:
    expected = os.environ.get("APP_PASSWORD", "")
    if not expected:
        raise HTTPException(status_code=503, detail="The app administrator has not configured APP_PASSWORD.")
    if password is None or not secrets.compare_digest(password, expected):
        raise HTTPException(status_code=401, detail="Incorrect app password.")


def _pack_session(session: requests.Session, *, pending_url: str | None = None) -> str:
    credentials = session.auth
    if not isinstance(credentials, tuple) or len(credentials) != 2:
        raise HTTPException(status_code=401, detail="The BRAIN sign-in session is invalid. Sign in again.")
    payload = {
        "email": credentials[0],
        "password": credentials[1],
        "cookies": [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": cookie.secure,
                "expires": cookie.expires,
                "rest": cookie._rest,
            }
            for cookie in session.cookies
        ],
        "pending_url": pending_url,
        "expires": int(time.time()) + SESSION_TTL_SECONDS,
    }
    return _fernet().encrypt(json.dumps(payload).encode("utf-8")).decode("ascii")


def _unpack_session(token: str) -> tuple[requests.Session, dict[str, Any]]:
    try:
        payload = json.loads(_fernet().decrypt(token.encode("ascii")).decode("utf-8"))
    except (InvalidToken, UnicodeError, json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=401, detail="The BRAIN session token is invalid. Sign in again.")
    if not isinstance(payload, dict) or payload.get("expires", 0) < time.time():
        raise HTTPException(status_code=401, detail="The BRAIN session expired. Sign in again.")

    session = ace.SingleSession()
    session.auth = (payload["email"], payload["password"])
    for saved_cookie in payload.get("cookies", []):
        session.cookies.set_cookie(
            requests.cookies.create_cookie(
                name=saved_cookie["name"],
                value=saved_cookie["value"],
                domain=saved_cookie.get("domain") or "",
                path=saved_cookie.get("path") or "/",
                secure=saved_cookie.get("secure", False),
                expires=saved_cookie.get("expires"),
                rest=saved_cookie.get("rest") or {},
            )
        )
    return session, payload


def _approved_persona_url(response: requests.Response) -> str:
    from app import _valid_persona_url

    try:
        return _valid_persona_url(response)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/health")
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/login")
@app.post("/login")
def login(
    request: SignInRequest,
    x_app_password: str | None = Header(default=None),
) -> dict[str, str]:
    _check_app_password(x_app_password)
    session = ace.SingleSession()
    session.auth = (request.email.strip(), request.password)
    try:
        response = session.post(ace.brain_api_url + "/authentication", timeout=30)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Could not reach WorldQuant BRAIN.") from exc

    if response.status_code == requests.codes.created:
        return {"status": "authenticated", "session_token": _pack_session(session)}
    if response.status_code == requests.codes.unauthorized and response.headers.get("WWW-Authenticate") == "persona":
        approval_url = _approved_persona_url(response)
        return {
            "status": "biometric_required",
            "approval_url": approval_url,
            "session_token": _pack_session(session, pending_url=approval_url),
        }
    if response.status_code == requests.codes.unauthorized:
        raise HTTPException(status_code=401, detail="WorldQuant BRAIN rejected the email or password.")
    raise HTTPException(status_code=502, detail=f"WorldQuant BRAIN sign-in failed (HTTP {response.status_code}).")


@app.post("/api/biometric")
@app.post("/biometric")
def complete_biometric(
    request: BiometricRequest,
    x_app_password: str | None = Header(default=None),
) -> dict[str, str]:
    _check_app_password(x_app_password)
    session, payload = _unpack_session(request.session_token)
    approval_url = payload.get("pending_url")
    if not approval_url:
        raise HTTPException(status_code=400, detail="There is no pending biometric approval.")
    try:
        response = session.post(approval_url, timeout=30)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Could not check biometric approval.") from exc
    if response.status_code == requests.codes.created:
        return {"status": "authenticated", "session_token": _pack_session(session)}
    return {
        "status": "biometric_required",
        "approval_url": approval_url,
        "session_token": _pack_session(session, pending_url=approval_url),
    }


@app.post("/api/run")
@app.post("/run")
def run(
    request: RunRequest,
    x_app_password: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_app_password(x_app_password)
    session, _ = _unpack_session(request.session_token)
    api_key = request.gemini_api_key or os.environ.get("GENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="Set GENAI_API_KEY or enter a Gemini API key.")
    try:
        dataset_ids = parse_dataset_ids(request.dataset_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        results = run_workflow(
            session=session,
            api_key=api_key,
            model="gemini-2.5-flash",
            region=request.region.strip(),
            universe=request.universe.strip(),
            dataset_ids=dataset_ids,
            iterations=request.iterations,
            num_alphas=request.num_alphas,
            initial_description=request.initial_description.strip(),
            on_iteration=lambda *_: None,
        )
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"results": results, "session_token": _pack_session(session)}
