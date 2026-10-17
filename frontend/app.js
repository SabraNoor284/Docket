"use strict";

const API = "/api";
const STATUSES = ["todo", "in_progress", "done"];
const STATUS_LABEL = { todo: "To do", in_progress: "In progress", done: "Done" };
const PRIORITY_LABEL = { low: "Low", medium: "Medium", high: "High" };

const state = { tasks: [], editingId: null };
const $ = (id) => document.getElementById(id);

/* ------------------------------ API layer ------------------------------ */

function errorMessage(data, status) {
  if (data && typeof data.detail === "string") return data.detail;
  if (data && Array.isArray(data.detail)) {
    return data.detail
      .map((e) => `${(e.loc || []).slice(1).join(".") || "request"}: ${e.msg}`)
      .join("; ");
  }
  return `Request failed (${status}).`;
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const key = sessionStorage.getItem("docket_key");
  if (key) headers["X-API-Key"] = key;

  const res = await fetch(API + path, { ...options, headers });

  if (res.status === 401) {
    sessionStorage.removeItem("docket_key");
    const entered = window.prompt("This server requires an API key. Enter it to continue:");
    if (!entered) throw new Error("An API key is required.");
    sessionStorage.setItem("docket_key", entered);
    return api(path, options);
  }
  if (res.status === 204) return null;

  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(errorMessage(data, res.status));
  return data;
}

const fetchTasks = (params) => api("/tasks?" + new URLSearchParams(params));
const fetchStats = () => api("/stats");
const createTask = (body) => api("/tasks", { method: "POST", body: JSON.stringify(body) });
const updateTask = (id, body) => api(`/tasks/${id}`, { method: "PATCH", body: JSON.stringify(body) });
const deleteTask = (id) => api(`/tasks/${id}`, { method: "DELETE" });

/* ------------------------------ Rendering ------------------------------ */

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  node.append(...children.filter(Boolean));
  return node;
}

const todayISO = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, local time

function formatDue(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function nextAction(task) {
  if (task.status === "todo") return { label: "Start", to: "in_progress" };
  if (task.status === "in_progress") return { label: "Mark done", to: "done" };
  return { label: "Reopen", to: "todo" };
}

function renderCard(task) {
  const overdue = task.status !== "done" && task.due_date && task.due_date < todayISO();
  const action = nextAction(task);

  const meta = el("div", { class: "meta" }, el("span", { text: `${PRIORITY_LABEL[task.priority]} priority` }));
  if (task.due_date) {
    meta.append(el("span", { class: overdue ? "late" : "", text: `${overdue ? "Overdue · " : "Due "}${formatDue(task.due_date)}` }));
  }

  const deleteBtn = el("button", { type: "button", class: "btn small danger", text: "Delete" });
  let armTimer;
  deleteBtn.addEventListener("click", async () => {
    if (!deleteBtn.classList.contains("armed")) {
      deleteBtn.classList.add("armed");
      deleteBtn.textContent = "Confirm delete";
      armTimer = setTimeout(() => {
        deleteBtn.classList.remove("armed");
        deleteBtn.textContent = "Delete";
      }, 3000);
      return;
    }
    clearTimeout(armTimer);
    await run(() => deleteTask(task.id), "Task deleted");
  });

  const card = el(
    "li",
    { class: `card ${task.status === "done" ? "done" : ""}`, "data-id": task.id, "data-priority": task.priority, draggable: "true" },
    el("h3", { text: task.title }),
    task.description ? el("p", { class: "notes", text: task.description }) : null,
    meta,
    el(
      "div",
      { class: "card-actions" },
      el("button", {
        type: "button",
        class: "btn small",
        text: action.label,
        onclick: () => run(() => updateTask(task.id, { status: action.to }), `Moved to ${STATUS_LABEL[action.to]}`),
      }),
      el("button", { type: "button", class: "btn small", text: "Edit", onclick: () => openDialog(task) }),
      deleteBtn
    )
  );

  card.addEventListener("dragstart", (e) => {
    e.dataTransfer.setData("text/plain", String(task.id));
    e.dataTransfer.effectAllowed = "move";
    card.classList.add("dragging");
  });
  card.addEventListener("dragend", () => card.classList.remove("dragging"));
  return card;
}

function renderBoard() {
  for (const status of STATUSES) {
    const list = document.querySelector(`[data-list="${status}"]`);
    const tasks = state.tasks.filter((t) => t.status === status);
    list.replaceChildren(
      ...(tasks.length
        ? tasks.map(renderCard)
        : [el("li", { class: "empty", text: status === "todo" ? "No tasks here. Add one to get started." : "Nothing here. Drag a task in." })])
    );
    document.querySelector(`[data-count="${status}"]`).textContent = tasks.length;
  }
}

function renderStats(s) {
  $("n-todo").textContent = s.todo;
  $("n-progress").textContent = s.in_progress;
  $("n-done").textContent = s.done;
  $("n-overdue").textContent = s.overdue;
  $("n-overdue").classList.toggle("has", s.overdue > 0);
  $("n-rate").textContent = `${s.completion_rate}%`;

  const pct = (n) => (s.total ? (n / s.total) * 100 : 0) + "%";
  document.querySelector(".seg-todo").style.width = pct(s.todo);
  document.querySelector(".seg-progress").style.width = pct(s.in_progress);
  document.querySelector(".seg-done").style.width = pct(s.done);
  $("bar").setAttribute(
    "aria-label",
    s.total ? `${s.done} of ${s.total} tasks done, ${s.in_progress} in progress, ${s.todo} to do` : "No tasks yet"
  );
}

/* ------------------------------ Data flow ------------------------------ */

function currentFilters() {
  const params = { sort: $("f-sort").value };
  if ($("f-q").value.trim()) params.q = $("f-q").value.trim();
  if ($("f-priority").value) params.priority = $("f-priority").value;
  return params;
}

async function refresh() {
  const [tasks, stats] = await Promise.all([fetchTasks(currentFilters()), fetchStats()]);
  state.tasks = tasks;
  renderBoard();
  renderStats(stats);
}

let toastTimer;
function toast(message, isError = false) {
  const t = $("toast");
  t.textContent = message;
  t.classList.toggle("error", isError);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2800);
}

/** Run a mutation, then reload from the server so the UI always shows stored data. */
async function run(mutation, successMessage) {
  try {
    await mutation();
    await refresh();
    if (successMessage) toast(successMessage);
  } catch (err) {
    toast(err.message, true);
  }
}

/* ------------------------------ Dialog form ------------------------------ */

function openDialog(task = null) {
  state.editingId = task ? task.id : null;
  $("dialog-title").textContent = task ? "Edit task" : "Add task";
  $("t-title").value = task ? task.title : "";
  $("t-description").value = task ? task.description : "";
  $("t-status").value = task ? task.status : "todo";
  $("t-priority").value = task ? task.priority : "medium";
  $("t-due").value = task && task.due_date ? task.due_date : "";
  $("form-error").hidden = true;
  $("dialog").showModal();
  $("t-title").focus();
}

async function submitForm(e) {
  e.preventDefault();
  const title = $("t-title").value.trim();
  const showError = (msg) => {
    $("form-error").textContent = msg;
    $("form-error").hidden = false;
  };
  if (!title) return showError("Enter a title for the task.");

  const body = {
    title,
    description: $("t-description").value.trim(),
    status: $("t-status").value,
    priority: $("t-priority").value,
    due_date: $("t-due").value || null,
  };

  $("save").disabled = true;
  try {
    if (state.editingId) await updateTask(state.editingId, body);
    else await createTask(body);
    $("dialog").close();
    await refresh();
    toast(state.editingId ? "Task saved" : "Task added");
  } catch (err) {
    showError(err.message);
  } finally {
    $("save").disabled = false;
  }
}

/* ------------------------------ Events ------------------------------ */

function debounce(fn, ms) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

function init() {
  $("new-task").addEventListener("click", () => openDialog());
  $("cancel").addEventListener("click", () => $("dialog").close());
  $("task-form").addEventListener("submit", submitForm);
  $("filters").addEventListener("submit", (e) => e.preventDefault());

  const reload = () => refresh().catch((err) => toast(err.message, true));
  $("f-q").addEventListener("input", debounce(reload, 250));
  $("f-priority").addEventListener("change", reload);
  $("f-sort").addEventListener("change", reload);

  // Drag a card onto a column to change its status.
  document.querySelectorAll(".column").forEach((col) => {
    col.addEventListener("dragover", (e) => {
      e.preventDefault();
      col.classList.add("over");
    });
    col.addEventListener("dragleave", (e) => {
      if (!col.contains(e.relatedTarget)) col.classList.remove("over");
    });
    col.addEventListener("drop", (e) => {
      e.preventDefault();
      col.classList.remove("over");
      const id = Number(e.dataTransfer.getData("text/plain"));
      const task = state.tasks.find((t) => t.id === id);
      const to = col.dataset.status;
      if (task && task.status !== to) run(() => updateTask(id, { status: to }), `Moved to ${STATUS_LABEL[to]}`);
    });
  });

  reload();
}

document.addEventListener("DOMContentLoaded", init);
