import hmac
import json
import os
import time
from typing import Any
from urllib.parse import urljoin, urlparse
import pandas as pd
import requests
import streamlit as st
from google import genai
import ace_lib as ace


DEFAULT_REGION = "HKG"
DEFAULT_UNIVERSE = "TOP800"
DEFAULT_DATASET_IDS = "fundamental17, other466, fundamental86"
ALPHA_SCHEMA = {
    "type": "object",
    "properties": {
        "alphas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["expression", "rationale"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["alphas"],
}


def parse_dataset_ids(value: str) -> list[str]:
    dataset_ids = [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]
    if not dataset_ids:
        raise ValueError("Enter at least one dataset ID.")
    return list(dict.fromkeys(dataset_ids))


def _secret(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    try:
        return st.secrets.get(name)
    except FileNotFoundError:
        return None


def _valid_persona_url(response: requests.Response) -> str:
    location = response.headers.get("Location")
    if not location:
        raise RuntimeError("The sign-in response did not include a biometric approval link.")
    approval_url = urljoin(response.url, location)
    parsed = urlparse(approval_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname == "worldquantbrain.com" or hostname.endswith(".worldquantbrain.com")
    ):
        raise RuntimeError("The sign-in response included an unexpected biometric approval URL.")
    return approval_url


def _capture_login() -> None:
    st.session_state["_submitted_login"] = (
        st.session_state.get("brain_email_input", "").strip(),
        st.session_state.get("brain_password_input", ""),
    )
    st.session_state["brain_password_input"] = ""


def _sign_in() -> None:
    submitted = st.session_state.pop("_submitted_login", None)
    if submitted is not None:
        email, password = submitted
        if not email or not password:
            st.session_state["_login_error"] = "Enter both your WorldQuant BRAIN email and password."
        else:
            session = ace.SingleSession()
            session.auth = (email, password)
            try:
                response = session.post(ace.brain_api_url + "/authentication")
            except requests.RequestException as exc:
                st.session_state["_login_error"] = f"Could not reach WorldQuant BRAIN: {exc}"
            else:
                if response.status_code == requests.codes.created:
                    st.session_state["brain_session"] = session
                    st.session_state.pop("_pending_brain_session", None)
                    st.session_state.pop("_login_error", None)
                elif response.status_code == requests.codes.unauthorized and response.headers.get(
                    "WWW-Authenticate"
                ) == "persona":
                    try:
                        approval_url = _valid_persona_url(response)
                    except RuntimeError as exc:
                        st.session_state["_login_error"] = str(exc)
                    else:
                        st.session_state["_pending_brain_session"] = (session, approval_url)
                        st.session_state.pop("_login_error", None)
                elif response.status_code == requests.codes.unauthorized:
                    st.session_state["_login_error"] = "WorldQuant BRAIN rejected the email or password."
                else:
                    st.session_state["_login_error"] = (
                        f"WorldQuant BRAIN sign-in failed (HTTP {response.status_code})."
                    )

    pending = st.session_state.get("_pending_brain_session")
    if pending is not None and st.session_state.pop("_check_biometrics", False):
        session, approval_url = pending
        try:
            response = session.post(approval_url)
        except requests.RequestException as exc:
            st.session_state["_login_error"] = f"Could not check biometric approval: {exc}"
        else:
            if response.status_code == requests.codes.created:
                st.session_state["brain_session"] = session
                st.session_state.pop("_pending_brain_session", None)
                st.session_state.pop("_login_error", None)
            else:
                st.session_state["_login_error"] = (
                    "Biometric approval is not complete yet. Approve the request, then check again."
                )


def _logout() -> None:
    for key in (
        "brain_session",
        "_pending_brain_session",
        "_login_error",
        "_submitted_login",
        "brain_password_input",
        "gemini_api_key_input",
        "_run_request",
        "workflow_results",
    ):
        st.session_state.pop(key, None)


def _render_access_gate() -> bool:
    expected_password = _secret("APP_PASSWORD")
    if not expected_password:
        st.warning(
            "The app access gate is not configured. Set APP_PASSWORD before exposing this app publicly."
        )
        return True
    if st.session_state.get("app_access_granted"):
        return True

    with st.form("app_access_form"):
        password = st.text_input("App access password", type="password")
        submitted = st.form_submit_button("Unlock")
    if submitted:
        if hmac.compare_digest(password, expected_password):
            st.session_state["app_access_granted"] = True
            st.rerun()
        st.error("Incorrect app access password.")
    return False


def _render_sign_in() -> None:
    if st.session_state.get("brain_session") is not None:
        st.success("Signed in to WorldQuant BRAIN.")
        if st.button("Sign out", key="sign_out"):
            _logout()
            st.rerun()
        return

    with st.form("brain_sign_in"):
        email = st.text_input("WorldQuant BRAIN email", key="brain_email_input")
        password = st.text_input("WorldQuant BRAIN password", type="password", key="brain_password_input")
        st.form_submit_button("Sign in", on_click=_capture_login)

    if "_login_error" in st.session_state:
        st.error(st.session_state["_login_error"])

    pending = st.session_state.get("_pending_brain_session")
    if pending is not None:
        _, approval_url = pending
        st.info("Open the approval page, complete the biometric check, then return here.")
        st.link_button("Open biometric approval", approval_url)
        st.button(
            "I completed approval — check sign-in",
            key="check_biometrics_button",
            on_click=lambda: st.session_state.update({"_check_biometrics": True}),
        )


def _build_initial_prompt(
    num_alphas: int, operators: pd.DataFrame, data_fields: pd.DataFrame
) -> str:
    return "\n".join(
        [
            "Return JSON matching the provided schema with an 'alphas' array; each item needs 'expression' and 'rationale'.",
            f"Available operators: {operators[operators['scope'] == 'REGULAR'].to_json()}",
            f"Available data fields: {data_fields.to_json()}",
            "Do not use data fields other than those listed.",
            "MATRIX fields can be used as inputs to arithmetic, cross-sectional, time-series, logical, and transformational operators.",
            "VECTOR fields must first be wrapped with vec_count, vec_max, vec_sum, vec_avg, vec_stddev, vec_min, or vec_range.",
            "GROUP fields may only be used as the group parameter of group operators.",
            "Use ts_backfill for low-coverage MATRIX fields when useful.",
            "Prefer underused fields with low alphaCount and build complex expressions that combine multiple operators.",
            "Prefer expressions based on a single dataset where possible.",
            "Do not use pv1 fields by themselves; combine any pv1 field with at least one non-pv1 field.",
            f"Generate {num_alphas} new alpha expressions.",
        ]
    )


def _build_variation_prompt(
    summary: str,
    previous_alphas: list[dict[str, Any]],
    num_alphas: int,
    operators: pd.DataFrame,
    data_fields: pd.DataFrame,
) -> str:
    best = sorted(
        (alpha for alpha in previous_alphas if alpha.get("sharpe") is not None),
        key=lambda alpha: alpha["sharpe"],
        reverse=True,
    )[:3]
    return "\n".join(
        [
            "Generate variations of the previous alpha expressions.",
            f"Previous iteration summary: {summary}",
            f"Previous high-Sharpe expressions: {json.dumps(best)}",
            f"Available operators: {operators[operators['scope'] == 'REGULAR'].to_json()}",
            f"Available data fields: {data_fields.to_json()}",
            "Do not use data fields other than those listed.",
            "MATRIX fields can be used as inputs to arithmetic, cross-sectional, time-series, logical, and transformational operators.",
            "VECTOR fields must first be wrapped with vec_count, vec_max, vec_sum, vec_avg, vec_stddev, vec_min, or vec_range.",
            "GROUP fields may only be used as the group parameter of group operators.",
            "Use ts_backfill for low-coverage MATRIX fields when useful.",
            "Prefer underused fields with low alphaCount and build complex expressions that combine multiple operators.",
            "Do not use pv1 fields by themselves; combine any pv1 field with at least one non-pv1 field.",
            "Return only JSON matching the schema.",
            f"Generate {num_alphas} new expressions with a brief rationale.",
        ]
    )


def _generate_alphas(prompt: str, api_key: str, model: str) -> tuple[list[dict[str, str]], str]:
    client = genai.Client(api_key=api_key)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": ALPHA_SCHEMA,
                },
            )
            payload = json.loads(response.text or "")
            alphas = payload.get("alphas")
            if not isinstance(alphas, list):
                raise ValueError("Gemini did not return an 'alphas' list.")
            valid_alphas = [
                {"expression": item["expression"].strip(), "rationale": item["rationale"].strip()}
                for item in alphas
                if isinstance(item, dict)
                and isinstance(item.get("expression"), str)
                and item["expression"].strip()
                and isinstance(item.get("rationale"), str)
            ]
            if not valid_alphas:
                raise ValueError("Gemini returned no usable alpha expressions.")
            return valid_alphas, str(payload.get("summary", ""))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            last_error = exc
            if attempt == 2:
                raise RuntimeError(f"Could not parse Gemini's response: {exc}") from exc
        except Exception as exc:
            last_error = exc
            if attempt == 2:
                raise RuntimeError(f"Gemini generation failed: {exc}") from exc
        time.sleep(attempt + 1)
    raise RuntimeError(f"Gemini generation failed: {last_error}")


def _stat_value(stats: Any, name: str) -> float | None:
    if stats is None or not hasattr(stats, "get"):
        return None
    value = stats.get(name)
    if value is None:
        return None
    if hasattr(value, "max"):
        value = value.max()
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(numeric_value) else numeric_value


def _fetch_data_fields(
    session: ace.SingleSession, region: str, universe: str, dataset_ids: list[str]
) -> pd.DataFrame:
    fields = ace.get_datafields(
        session,
        region=region,
        universe=universe,
        delay=1,
    )
    if fields.empty:
        raise RuntimeError("No data fields were returned for these region, universe, and dataset IDs.")
    if "dataset_id" not in fields.columns:
        raise RuntimeError("The data fields response did not include dataset IDs.")
    fields = fields[fields["dataset_id"].isin(dataset_ids)].reset_index(drop=True)
    if fields.empty:
        raise RuntimeError("No data fields were returned for the selected dataset IDs.")
    if "alphaCount" in fields.columns:
        fields = fields[fields["alphaCount"] < 20].reset_index(drop=True)
    fields = fields.drop(
        columns=[
            "subcategory",
            "category",
            "region",
            "delay",
            "universe",
            "pyramidMultiplier",
            "themes",
            "category_id",
            "category_name",
            "subcategory_id",
            "dateCreated",
            "userCount",
            "coverage",
            "dateCoverage",
        ],
        errors="ignore",
    )
    if fields.empty:
        raise RuntimeError("No data fields remain after filtering out fields used by 20 or more alphas.")
    return fields


def run_workflow(
    session: ace.SingleSession,
    api_key: str,
    model: str,
    region: str,
    universe: str,
    dataset_ids: list[str],
    iterations: int,
    num_alphas: int,
    initial_description: str,
    on_iteration: Any,
) -> list[dict[str, Any]]:
    data_fields = _fetch_data_fields(session, region, universe, dataset_ids)
    operators = ace.get_operators(session)
    results_by_iteration = []
    previous_summary = initial_description
    previous_alphas: list[dict[str, Any]] = []

    for iteration in range(iterations):
        on_iteration(iteration + 1, iterations, "Generating expressions")
        prompt = (
            _build_initial_prompt(num_alphas, operators, data_fields)
            if iteration == 0
            else _build_variation_prompt(
                previous_summary, previous_alphas, num_alphas, operators, data_fields
            )
        )
        generated, generation_summary = _generate_alphas(prompt, api_key, model)
        alpha_list = [
            ace.generate_alpha(
                alpha["expression"],
                region=region,
                universe=universe,
                neutralization=neutralization,
                max_trade="OFF",
                delay=1,
                simulation_mode="QUICK",
                visualization=False,
            )
            for alpha in generated
            for neutralization in ("STATISTICAL", "SUBINDUSTRY")
        ]

        on_iteration(iteration + 1, iterations, "Simulating expressions")
        simulation_results = ace.simulate_alpha_list_multi(session, alpha_list)
        rows = []
        for result in simulation_results:
            simulate_data = result.get("simulate_data", {})
            stats = result.get("is_stats")
            expression = simulate_data.get("regular", "")
            sharpe = _stat_value(stats, "sharpe")
            rows.append(
                {
                    "alpha_id": result.get("alpha_id"),
                    "expression": expression,
                    "neutralization": simulate_data.get("settings", {}).get("neutralization"),
                    "sharpe": sharpe,
                    "turnover": _stat_value(stats, "turnover"),
                    "status": "Simulated" if result.get("alpha_id") else "Failed",
                }
            )

        successful = [row for row in rows if row["alpha_id"]]
        sharpe_values = [abs(row["sharpe"]) for row in rows if row["sharpe"] is not None]
        best_sharpe = max(sharpe_values) if sharpe_values else None
        previous_summary = (
            f"Gemini: {generation_summary}; best absolute Sharpe: {best_sharpe}; "
            f"successful simulations: {len(successful)} of {len(rows)}."
        )
        previous_alphas = [
            {
                "expression": row["expression"],
                "alpha_id": row["alpha_id"],
                "sharpe": row["sharpe"],
                "turnover": row["turnover"],
            }
            for row in rows
            if row["alpha_id"]
        ]
        results_by_iteration.append(
            {
                "generated": generated,
                "generation_summary": generation_summary,
                "best_sharpe": best_sharpe,
                "submitted_count": len(successful),
                "results": rows,
            }
        )
    return results_by_iteration


def _capture_workflow_request() -> None:
    api_key = _secret("GENAI_API_KEY") or st.session_state.get("gemini_api_key_input", "")
    st.session_state["_run_request"] = {
        "api_key": api_key,
        "model": st.session_state.get("gemini_model_input", "gemini-2.5-flash"),
        "region": st.session_state.get("region_input", "").strip(),
        "universe": st.session_state.get("universe_input", "").strip(),
        "dataset_ids": st.session_state.get("dataset_ids_input", ""),
        "iterations": st.session_state.get("iterations_input", 3),
        "num_alphas": st.session_state.get("num_alphas_input", 50),
        "initial_description": st.session_state.get("description_input", "").strip(),
    }
    st.session_state["gemini_api_key_input"] = ""


def _render_workflow() -> None:
    if st.session_state.get("brain_session") is None:
        st.info("Sign in to WorldQuant BRAIN before starting a run.")
        return

    with st.form("workflow_form"):
        st.subheader("Initial parameters")
        region = st.text_input("Region (rg)", DEFAULT_REGION, key="region_input")
        universe = st.text_input("Universe (un)", DEFAULT_UNIVERSE, key="universe_input")
        dataset_ids = st.text_area("Dataset IDs (comma- or newline-separated)", DEFAULT_DATASET_IDS, key="dataset_ids_input")
        st.subheader("Generation")
        if _secret("GENAI_API_KEY"):
            st.caption("Gemini API key is configured on the server.")
        else:
            st.text_input(
                "Google Gemini API key",
                type="password",
                key="gemini_api_key_input",
                help="Used only for this run; it is not written to a file.",
            )
        st.text_input("Gemini model", "gemini-2.5-flash", key="gemini_model_input")
        st.number_input("Iterations", min_value=1, max_value=5, value=3, key="iterations_input")
        st.number_input("Alphas per iteration", min_value=1, max_value=50, value=50, key="num_alphas_input")
        st.text_input(
            "Initial generation direction",
            "Start with simple expressions using available data fields.",
            key="description_input",
        )
        st.form_submit_button("Generate and simulate", on_click=_capture_workflow_request)

    request = st.session_state.pop("_run_request", None)
    if request is not None:
        try:
            ids = parse_dataset_ids(request["dataset_ids"])
            if not request["region"] or not request["universe"]:
                raise ValueError("Region and universe are required.")
            if not request["api_key"]:
                raise ValueError("Enter a Gemini API key.")
            progress = st.progress(0, text="Preparing the run")

            def update_progress(current: int, total: int, message: str) -> None:
                progress.progress(
                    min((current - 1) / total, 0.95),
                    text=f"Iteration {current}/{total}: {message}",
                )

            with st.spinner("Generating and simulating alphas. This may take several minutes."):
                results = run_workflow(
                    session=st.session_state["brain_session"],
                    api_key=request["api_key"],
                    model=request["model"],
                    region=request["region"],
                    universe=request["universe"],
                    dataset_ids=ids,
                    iterations=request["iterations"],
                    num_alphas=request["num_alphas"],
                    initial_description=request["initial_description"],
                    on_iteration=update_progress,
                )
            progress.progress(1.0, text="Run complete")
            st.session_state["workflow_results"] = results
        except (
            KeyError,
            PermissionError,
            requests.RequestException,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            st.error(str(exc))

    for iteration_number, iteration in enumerate(st.session_state.get("workflow_results", []), 1):
        st.header(f"Iteration {iteration_number}")
        st.write(
            f"Successful simulations: {iteration['submitted_count']} · "
            f"Best absolute Sharpe: {iteration['best_sharpe']}"
        )
        if iteration["generation_summary"]:
            st.caption(iteration["generation_summary"])
        if iteration["generated"]:
            with st.expander("Generated expressions and rationale"):
                for alpha in iteration["generated"]:
                    st.code(alpha["expression"])
                    st.caption(alpha["rationale"])
        results = pd.DataFrame(iteration["results"])
        if not results.empty:
            st.dataframe(results, use_container_width=True, hide_index=True)
            st.download_button(
                f"Download iteration {iteration_number} results",
                results.to_csv(index=False).encode("utf-8"),
                file_name=f"alpha-results-iteration-{iteration_number}.csv",
                mime="text/csv",
                key=f"download_results_{iteration_number}",
            )


def main() -> None:
    st.set_page_config(page_title="BRAIN Alpha Generator", page_icon="📈", layout="wide")
    st.title("BRAIN Alpha Generator")
    st.caption("Generate and simulate alpha expressions using your WorldQuant BRAIN account.")

    if not _render_access_gate():
        return
    _sign_in()
    _render_sign_in()
    _render_workflow()


if __name__ == "__main__":
    main()
