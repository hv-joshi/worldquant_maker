(() => {
  const state = {
    appPassword: "",
    brainSession: "",
    pendingApprovalUrl: "",
  };
  const $ = (id) => document.getElementById(id);
  const accessCard = $("access-card");
  const brainCard = $("brain-card");
  const runCard = $("run-card");
  const loginForm = $("login-form");
  const biometricPanel = $("biometric-panel");
  const message = $("message");

  function showMessage(text, isError = false) {
    message.textContent = text;
    message.classList.toggle("error", isError);
    message.classList.remove("hidden");
    message.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function clearMessage() {
    message.textContent = "";
    message.classList.add("hidden");
    message.classList.remove("error");
  }

  async function api(path, body) {
    const response = await fetch(`/api/${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-App-Password": state.appPassword,
      },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `Request failed (${response.status}).`);
    }
    return payload;
  }

  function unlock() {
    accessCard.classList.add("hidden");
    brainCard.classList.remove("hidden");
    if (state.brainSession) {
      loginForm.classList.add("hidden");
      $("brain-status").textContent = "Signed in to WorldQuant BRAIN.";
      $("sign-out").classList.remove("hidden");
      runCard.classList.remove("hidden");
    }
  }

  $("access-form").addEventListener("submit", (event) => {
    event.preventDefault();
    clearMessage();
    state.appPassword = $("app-password").value;
    unlock();
    $("app-password").value = "";
  });

  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearMessage();
    const button = loginForm.querySelector("button[type=submit]");
    button.disabled = true;
    $("brain-status").textContent = "Signing in…";
    try {
      const result = await api("login", {
        email: $("brain-email").value.trim(),
        password: $("brain-password").value,
      });
      $("brain-password").value = "";
      state.brainSession = result.session_token;
      if (result.status === "biometric_required") {
        state.pendingApprovalUrl = result.approval_url;
        $("approval-link").href = result.approval_url;
        $("brain-status").textContent = "Complete biometric approval, then check sign-in.";
        biometricPanel.classList.remove("hidden");
      } else {
        completeSignIn();
      }
    } catch (error) {
      $("brain-status").textContent = "Sign-in failed.";
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  function completeSignIn() {
    state.pendingApprovalUrl = "";
    biometricPanel.classList.add("hidden");
    loginForm.classList.add("hidden");
    $("brain-status").textContent = "Signed in to WorldQuant BRAIN.";
    $("sign-out").classList.remove("hidden");
    runCard.classList.remove("hidden");
    clearMessage();
  }

  $("check-biometric").addEventListener("click", async () => {
    clearMessage();
    $("check-biometric").disabled = true;
    try {
      const result = await api("biometric", { session_token: state.brainSession });
      state.brainSession = result.session_token;
      if (result.status === "authenticated") {
        completeSignIn();
      } else {
        showMessage("Approval is not complete yet. Approve the request in the opened page, then try again.");
      }
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      $("check-biometric").disabled = false;
    }
  });

  $("sign-out").addEventListener("click", () => {
    state.brainSession = "";
    state.pendingApprovalUrl = "";
    $("brain-password").value = "";
    $("gemini-key").value = "";
    $("brain-status").textContent = "Sign in to continue.";
    $("sign-out").classList.add("hidden");
    loginForm.classList.remove("hidden");
    biometricPanel.classList.add("hidden");
    runCard.classList.add("hidden");
    $("results").replaceChildren();
    clearMessage();
  });

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[character]);
  }

  function renderResults(results) {
    $("results").replaceChildren();
    results.forEach((iteration, index) => {
      const article = document.createElement("article");
      article.className = "result-card";
      const rows = iteration.results || [];
      const tableRows = rows.map((row) => `
        <tr>
          <td>${escapeHtml(row.alpha_id || "—")}</td>
          <td class="expression">${escapeHtml(row.expression)}</td>
          <td>${escapeHtml(row.neutralization)}</td>
          <td>${escapeHtml(row.sharpe)}</td>
          <td>${escapeHtml(row.turnover)}</td>
          <td>${escapeHtml(row.status)}</td>
        </tr>`).join("");
      article.innerHTML = `
        <h3>Iteration ${index + 1}</h3>
        <div class="metric">Successful: ${escapeHtml(iteration.submitted_count)} · Best absolute Sharpe: ${escapeHtml(iteration.best_sharpe ?? "—")}</div>
        ${iteration.generation_summary ? `<p class="muted">${escapeHtml(iteration.generation_summary)}</p>` : ""}
        <div class="table-wrap">
          <table>
            <thead><tr><th>Alpha ID</th><th>Expression</th><th>Neutralization</th><th>Sharpe</th><th>Turnover</th><th>Status</th></tr></thead>
            <tbody>${tableRows}</tbody>
          </table>
        </div>
        <button class="download" type="button">Download CSV</button>`;
      const downloadButton = article.querySelector(".download");
      downloadButton.addEventListener("click", () => {
        const headers = ["alpha_id", "expression", "neutralization", "sharpe", "turnover", "status"];
        const csvValue = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;
        const csv = [headers.join(","), ...rows.map((row) => headers.map((header) => csvValue(row[header])).join(","))].join("\r\n");
        const link = document.createElement("a");
        link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
        link.download = `alpha-results-iteration-${index + 1}.csv`;
        link.click();
        URL.revokeObjectURL(link.href);
      });
      $("results").append(article);
    });
    $("results").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  $("run-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    clearMessage();
    const button = $("run-button");
    button.disabled = true;
    button.textContent = "Running simulations…";
    $("key-configured").textContent = "";
    $("results").replaceChildren();
    showMessage("Fetching data fields, generating expressions, and running simulations. Keep this page open.");
    const apiKey = $("gemini-key").value;
    try {
      const result = await api("run", {
        session_token: state.brainSession,
        gemini_api_key: apiKey || null,
        region: $("region").value.trim(),
        universe: $("universe").value.trim(),
        dataset_ids: $("datasets").value,
        iterations: Number($("iterations").value),
        num_alphas: Number($("alpha-count").value),
        initial_description: $("direction").value.trim(),
      });
      state.brainSession = result.session_token;
      $("gemini-key").value = "";
      renderResults(result.results);
      showMessage("Run complete.");
    } catch (error) {
      $("gemini-key").value = "";
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "Generate and simulate";
    }
  });
})();
