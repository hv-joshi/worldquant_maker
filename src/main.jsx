import React, { useState } from "react";
import { createRoot } from "react-dom/client";

const DEFAULT_DATASETS =
  "pv1, model110, model77, model26, model109, risk70, model38, model50, analyst69, model264, other460, fundamental23, other455, model138";
const CSV_HEADERS = ["alpha_id", "expression", "neutralization", "sharpe", "turnover", "status"];

async function api(path, body, appPassword) {
  const response = await fetch(`/api/${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-App-Password": appPassword,
    },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || `Request failed (${response.status}).`);
  }
  return payload;
}

function csvValue(value) {
  return `"${String(value ?? "").replace(/"/g, '""')}"`;
}

function downloadCsv(rows, iteration) {
  const csv = [
    CSV_HEADERS.join(","),
    ...rows.map((row) => CSV_HEADERS.map((header) => csvValue(row[header])).join(",")),
  ].join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `alpha-results-iteration-${iteration}.csv`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function Results({ results }) {
  return (
    <section className="results" aria-live="polite">
      {results.map((iteration, index) => {
        const rows = iteration.results || [];
        return (
          <article className="result-card" key={index}>
            <h3>Iteration {index + 1}</h3>
            <div className="metric">
              Successful: {iteration.submitted_count} · Best absolute Sharpe: {iteration.best_sharpe ?? "—"}
            </div>
            {iteration.generation_summary && <p className="muted">{iteration.generation_summary}</p>}
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Alpha ID</th>
                    <th>Expression</th>
                    <th>Neutralization</th>
                    <th>Sharpe</th>
                    <th>Turnover</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, rowIndex) => (
                    <tr key={row.alpha_id || rowIndex}>
                      <td>{row.alpha_id || "—"}</td>
                      <td className="expression">{row.expression}</td>
                      <td>{row.neutralization}</td>
                      <td>{row.sharpe}</td>
                      <td>{row.turnover}</td>
                      <td>{row.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <button className="download" type="button" onClick={() => downloadCsv(rows, index + 1)}>
              Download CSV
            </button>
          </article>
        );
      })}
    </section>
  );
}

function App() {
  const [appPassword, setAppPassword] = useState("");
  const [brainSession, setBrainSession] = useState("");
  const [approvalUrl, setApprovalUrl] = useState("");
  const [unlocked, setUnlocked] = useState(false);
  const [brainStatus, setBrainStatus] = useState("Sign in to continue.");
  const [message, setMessage] = useState("");
  const [isError, setIsError] = useState(false);
  const [isSigningIn, setIsSigningIn] = useState(false);
  const [isCheckingBiometric, setIsCheckingBiometric] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [results, setResults] = useState([]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [geminiKey, setGeminiKey] = useState("");
  const [region, setRegion] = useState("GLB");
  const [universe, setUniverse] = useState("MINVOL1M");
  const [datasetIds, setDatasetIds] = useState(DEFAULT_DATASETS);
  const [iterations, setIterations] = useState(1);
  const [numAlphas, setNumAlphas] = useState(3);
  const [description, setDescription] = useState(
    "Start with simple expressions using available data fields.",
  );

  function showMessage(text, error = false) {
    setMessage(text);
    setIsError(error);
    window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  }

  function clearMessage() {
    setMessage("");
    setIsError(false);
  }

  function completeSignIn() {
    setApprovalUrl("");
    setBrainStatus("Signed in to WorldQuant BRAIN.");
    clearMessage();
  }

  async function handleLogin(event) {
    event.preventDefault();
    clearMessage();
    setIsSigningIn(true);
    setBrainStatus("Signing in…");
    try {
      const result = await api(
        "login",
        { email: email.trim(), password },
        appPassword,
      );
      setPassword("");
      setBrainSession(result.session_token);
      if (result.status === "biometric_required") {
        setApprovalUrl(result.approval_url);
        setBrainStatus("Complete biometric approval, then check sign-in.");
      } else {
        completeSignIn();
      }
    } catch (error) {
      setBrainStatus("Sign-in failed.");
      showMessage(error.message, true);
    } finally {
      setIsSigningIn(false);
    }
  }

  async function handleBiometricCheck() {
    clearMessage();
    setIsCheckingBiometric(true);
    try {
      const result = await api("biometric", { session_token: brainSession }, appPassword);
      setBrainSession(result.session_token);
      if (result.status === "authenticated") {
        completeSignIn();
      } else {
        showMessage(
          "Approval is not complete yet. Approve the request in the opened page, then try again.",
        );
      }
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      setIsCheckingBiometric(false);
    }
  }

  function handleSignOut() {
    setBrainSession("");
    setApprovalUrl("");
    setPassword("");
    setGeminiKey("");
    setBrainStatus("Sign in to continue.");
    setResults([]);
    clearMessage();
  }

  async function handleRun(event) {
    event.preventDefault();
    clearMessage();
    setIsRunning(true);
    setResults([]);
    showMessage("Fetching data fields, generating expressions, and running simulations. Keep this page open.");
    try {
      const result = await api(
        "run",
        {
          session_token: brainSession,
          gemini_api_key: geminiKey || null,
          region: region.trim(),
          universe: universe.trim(),
          dataset_ids: datasetIds,
          iterations: Number(iterations),
          num_alphas: Number(numAlphas),
          initial_description: description.trim(),
        },
        appPassword,
      );
      setBrainSession(result.session_token);
      setGeminiKey("");
      setResults(result.results);
      showMessage("Run complete.");
    } catch (error) {
      setGeminiKey("");
      showMessage(error.message, true);
    } finally {
      setIsRunning(false);
    }
  }

  return (
    <main className="shell">
      <header className="top">
        <div className="brand-mark" aria-hidden="true">B</div>
        <div>
          <p className="eyebrow">RESEARCH WORKSPACE</p>
          <h1>BRAIN Alpha Generator</h1>
          <p className="subtitle">Generate expressions and run simulations with your own accounts.</p>
        </div>
      </header>

      {!unlocked ? (
        <section className="card">
          <h2>App access</h2>
          <p>Enter the password configured for this deployment.</p>
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              clearMessage();
              setUnlocked(true);
            }}
          >
            <label htmlFor="app-password">App password</label>
            <input
              id="app-password"
              type="password"
              autoComplete="current-password"
              value={appPassword}
              onChange={(event) => setAppPassword(event.target.value)}
              required
            />
            <button type="submit">Unlock</button>
          </form>
        </section>
      ) : (
        <>
          <section className="card">
            <div className="section-heading">
              <div>
                <p className="eyebrow">ACCOUNT</p>
                <h2>WorldQuant BRAIN</h2>
              </div>
              {brainSession && (
                <button className="button-secondary" type="button" onClick={handleSignOut}>
                  Sign out
                </button>
              )}
            </div>
            <p className="muted">{brainStatus}</p>
            {!brainSession && !approvalUrl && (
              <form className="stack" onSubmit={handleLogin}>
                <label htmlFor="brain-email">Email</label>
                <input
                  id="brain-email"
                  type="email"
                  autoComplete="username"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                />
                <label htmlFor="brain-password">Password</label>
                <input
                  id="brain-password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                />
                <button type="submit" disabled={isSigningIn}>
                  {isSigningIn ? "Signing in…" : "Sign in"}
                </button>
              </form>
            )}
            {approvalUrl && (
              <div className="stack">
                <a className="button-link" href={approvalUrl} target="_blank" rel="noopener noreferrer">
                  Open biometric approval
                </a>
                <button type="button" disabled={isCheckingBiometric} onClick={handleBiometricCheck}>
                  {isCheckingBiometric ? "Checking…" : "I approved — check sign-in"}
                </button>
              </div>
            )}
          </section>

          {brainSession && !approvalUrl && (
            <section className="card">
              <p className="eyebrow">SIMULATION SETUP</p>
              <h2>Initial parameters</h2>
              <form className="stack" onSubmit={handleRun}>
                <label htmlFor="region">Region (rg)</label>
                <input id="region" value={region} maxLength="32" onChange={(event) => setRegion(event.target.value)} required />
                <label htmlFor="universe">Universe (un)</label>
                <input id="universe" value={universe} maxLength="64" onChange={(event) => setUniverse(event.target.value)} required />
                <label htmlFor="datasets">Dataset IDs</label>
                <textarea id="datasets" rows="3" value={datasetIds} onChange={(event) => setDatasetIds(event.target.value)} required />
                <label htmlFor="gemini-key">Gemini API key</label>
                <input
                  id="gemini-key"
                  type="password"
                  autoComplete="off"
                  placeholder="Needed unless configured on the server"
                  value={geminiKey}
                  onChange={(event) => setGeminiKey(event.target.value)}
                />
                <div className="split">
                  <div className="stack compact">
                    <label htmlFor="iterations">Iterations (1–2)</label>
                    <input id="iterations" type="number" min="1" max="2" value={iterations} onChange={(event) => setIterations(event.target.value)} required />
                  </div>
                  <div className="stack compact">
                    <label htmlFor="alpha-count">Alphas per iteration (1–5)</label>
                    <input id="alpha-count" type="number" min="1" max="5" value={numAlphas} onChange={(event) => setNumAlphas(event.target.value)} required />
                  </div>
                </div>
                <label htmlFor="direction">Generation direction</label>
                <input id="direction" value={description} maxLength="1000" onChange={(event) => setDescription(event.target.value)} required />
                <button type="submit" disabled={isRunning}>
                  {isRunning ? "Running simulations…" : "Generate and simulate"}
                </button>
              </form>
              <p className="notice">Runs are capped to fit serverless execution limits. Each expression is tested with both Statistical and Subindustry neutralization.</p>
            </section>
          )}
        </>
      )}

      {results.length > 0 && <Results results={results} />}
      {message && <div className={`message${isError ? " error" : ""}`} role="status">{message}</div>}
      <footer>Use over HTTPS only. Your BRAIN credentials are encrypted between requests and are not saved to disk.</footer>
    </main>
  );
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
