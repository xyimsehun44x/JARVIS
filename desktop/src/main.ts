import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import "./styles.css";

const PROTOCOL_VERSION = 1;
const THREAD_ID = "desktop-main";

type IpcError = { code: string; message: string };
type TurnResult = {
  response?: string;
  needs_input: boolean;
  prompt?: string;
  interrupt_kind?: string;
  proposal?: Record<string, unknown>;
  response_streamed: boolean;
};
type IpcMessage = {
  protocol_version: number;
  type: "event" | "response";
  event?: "ready" | "assistant.delta" | "voice.state" | "voice.transcript";
  request_id?: string;
  ok?: boolean;
  payload?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: IpcError;
};
type PendingTurn = {
  bubble: HTMLDivElement;
  text: string;
};
type DesktopHotkeyStatus = {
  shortcut: string;
  registered: boolean;
  error?: string;
  source: "default" | "environment" | "saved" | string;
};
type BackendMethod =
  | "health"
  | "turn"
  | "voice.start"
  | "voice.stop"
  | "voice.cancel"
  | "memory.list"
  | "memory.correct"
  | "memory.forget"
  | "memory.restore"
  | "shutdown";
type MemoryStatus = "active" | "superseded" | "forgotten";
type MemoryRecord = {
  schema_version: number;
  memory_id: string;
  key: string;
  value: string;
  kind: "preference" | "fact" | "identity" | "instruction" | "other";
  reason: string;
  provenance: string;
  confidence: number;
  sensitivity: "normal" | "sensitive";
  status: MemoryStatus;
  created_at: string;
  updated_at: string;
  supersedes_id?: string;
  superseded_by_id?: string;
  status_reason?: string;
};
type MemoryListResult = {
  active: MemoryRecord[];
  history: MemoryRecord[];
};
type PendingBackendRequest = {
  resolve: (result: Record<string, unknown>) => void;
  reject: (error: Error) => void;
};

const conversation = required<HTMLDivElement>("conversation");
const emptyState = required<HTMLDivElement>("empty-state");
const composer = required<HTMLFormElement>("composer");
const messageInput = required<HTMLTextAreaElement>("message");
const sendButton = required<HTMLButtonElement>("send");
const microphoneButton = required<HTMLButtonElement>("microphone");
const status = required<HTMLDivElement>("status");
const orb = required<HTMLDivElement>("orb");
const approval = required<HTMLElement>("approval");
const approvalSummary = required<HTMLParagraphElement>("approval-summary");
const approvalPayload = required<HTMLPreElement>("approval-payload");
const approveButton = required<HTMLButtonElement>("approve");
const rejectButton = required<HTMLButtonElement>("reject");
const settingsToggle = required<HTMLButtonElement>("settings-toggle");
const settingsPanel = required<HTMLElement>("settings-panel");
const settingsClose = required<HTMLButtonElement>("settings-close");
const shortcutForm = required<HTMLFormElement>("shortcut-form");
const shortcutInput = required<HTMLInputElement>("shortcut-input");
const shortcutSave = required<HTMLButtonElement>("shortcut-save");
const shortcutCurrent = required<HTMLParagraphElement>("shortcut-current");
const settingsFeedback = required<HTMLParagraphElement>("settings-feedback");
const diagnosticBackend = required<HTMLElement>("diagnostic-backend");
const diagnosticMode = required<HTMLElement>("diagnostic-mode");
const diagnosticProvider = required<HTMLElement>("diagnostic-provider");
const diagnosticVoice = required<HTMLElement>("diagnostic-voice");
const diagnosticEmail = required<HTMLElement>("diagnostic-email");
const diagnosticCalendar = required<HTMLElement>("diagnostic-calendar");
const memoryRefresh = required<HTMLButtonElement>("memory-refresh");
const memoryFeedback = required<HTMLParagraphElement>("memory-feedback");
const memoryActiveCount = required<HTMLElement>("memory-active-count");
const memoryHistoryCount = required<HTMLElement>("memory-history-count");
const memoryActiveList = required<HTMLDivElement>("memory-active-list");
const memoryHistoryList = required<HTMLDivElement>("memory-history-list");
const pendingTurns = new Map<string, PendingTurn>();
const pendingBackendRequests = new Map<string, PendingBackendRequest>();
const voiceTranscriptBubbles = new Map<string, HTMLDivElement>();

let backendReady = false;
let voiceAvailable = false;
let busy = true;
let recording = false;
let memoryBusy = false;
let currentHotkey: DesktopHotkeyStatus | undefined;

function required<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing required element: ${id}`);
  return element as T;
}

function setStatus(
  label: string,
  state: "starting" | "ready" | "busy" | "listening" | "error",
): void {
  status.querySelector("span")!.textContent = label;
  status.dataset.state = state;
  orb.dataset.state = state;
  updateControls();
}

function setDiagnostic(
  element: HTMLElement,
  value: string,
  state?: "ready" | "warning" | "error",
): void {
  element.textContent = value;
  if (state) element.dataset.state = state;
  else delete element.dataset.state;
  element.title = value;
}

function setBackendDiagnostic(
  value: string,
  state: "ready" | "warning" | "error",
): void {
  setDiagnostic(diagnosticBackend, value, state);
}

function updateControls(): void {
  sendButton.disabled = !backendReady || busy || recording;
  microphoneButton.disabled =
    !backendReady || !voiceAvailable || (busy && !recording);
  microphoneButton.classList.toggle("recording", recording);
  microphoneButton.setAttribute("aria-pressed", String(recording));
  microphoneButton.setAttribute(
    "aria-label",
    recording ? "Stop and send voice input" : "Start voice input",
  );
  microphoneButton.title = voiceAvailable
    ? recording
      ? "Stop listening and send"
      : "Speak to Jarvis"
    : "Install the Python voice extras to enable voice input";
}

function appendBubble(role: "user" | "assistant" | "system", text = ""): HTMLDivElement {
  emptyState.hidden = true;
  const row = document.createElement("div");
  row.className = `message-row ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = text;
  row.appendChild(bubble);
  conversation.appendChild(row);
  conversation.scrollTop = conversation.scrollHeight;
  return bubble;
}

function newRequest(
  method: BackendMethod,
  params = {},
): Record<string, unknown> {
  return {
    protocol_version: PROTOCOL_VERSION,
    request_id: crypto.randomUUID(),
    method,
    params,
  };
}

async function sendRequest(request: Record<string, unknown>): Promise<void> {
  await invoke("backend_send", { request });
}

function requestBackend(
  method: BackendMethod,
  params: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  const request = newRequest(method, params);
  const requestId = request.request_id as string;
  return new Promise((resolve, reject) => {
    pendingBackendRequests.set(requestId, { resolve, reject });
    void sendRequest(request).catch((error: unknown) => {
      pendingBackendRequests.delete(requestId);
      reject(error instanceof Error ? error : new Error(String(error)));
    });
  });
}

async function sendTurn(text: string): Promise<void> {
  const clean = text.trim();
  if (!clean || !backendReady) return;
  hideApproval();
  appendBubble("user", clean);
  const bubble = appendBubble("assistant");
  bubble.classList.add("streaming");
  const request = newRequest("turn", { text: clean, thread_id: THREAD_ID });
  const requestId = request.request_id as string;
  pendingTurns.set(requestId, { bubble, text: "" });
  busy = true;
  setStatus("Thinking", "busy");
  try {
    await sendRequest(request);
  } catch {
    pendingTurns.delete(requestId);
    bubble.classList.remove("streaming");
    bubble.textContent = "The backend connection is unavailable.";
    busy = false;
    setStatus("Backend unavailable", "error");
  }
}

async function toggleVoice(): Promise<void> {
  if (!backendReady || !voiceAvailable) return;
  if (!recording) {
    hideApproval();
    busy = true;
    setStatus("Loading voice", "busy");
    try {
      await sendRequest(newRequest("voice.start", { thread_id: THREAD_ID }));
    } catch {
      busy = false;
      setStatus("Microphone unavailable", "error");
    }
    return;
  }

  recording = false;
  busy = true;
  const request = newRequest("voice.stop", { thread_id: THREAD_ID });
  const requestId = request.request_id as string;
  const transcriptBubble = appendBubble("user", "Transcribing...");
  voiceTranscriptBubbles.set(requestId, transcriptBubble);
  const responseBubble = appendBubble("assistant");
  responseBubble.classList.add("streaming");
  pendingTurns.set(requestId, { bubble: responseBubble, text: "" });
  setStatus("Transcribing", "busy");
  try {
    await sendRequest(request);
  } catch {
    pendingTurns.delete(requestId);
    voiceTranscriptBubbles.delete(requestId);
    responseBubble.classList.remove("streaming");
    responseBubble.textContent = "The backend connection is unavailable.";
    transcriptBubble.textContent = "Voice input could not be processed.";
    busy = false;
    setStatus("Backend unavailable", "error");
  }
}

function applyHealth(payload: Record<string, unknown> | undefined): void {
  const capabilities = payload?.capabilities;
  const runtime = payload?.diagnostics;
  let gmailSend = false;
  let calendarWrites = false;
  if (capabilities && typeof capabilities === "object") {
    const values = capabilities as Record<string, unknown>;
    voiceAvailable = values.voice_push_to_talk === true;
    gmailSend = values.gmail_send === true;
    calendarWrites = values.calendar_writes === true;
  }
  const mode = typeof payload?.mode === "string" ? payload.mode : "Unknown";
  setDiagnostic(diagnosticMode, mode, "ready");
  if (runtime && typeof runtime === "object") {
    const values = runtime as Record<string, unknown>;
    const provider = typeof values.llm_provider === "string" ? values.llm_provider : "Unknown";
    const model = typeof values.llm_model === "string" ? values.llm_model : "Unknown model";
    const sttModel = typeof values.stt_model === "string" ? values.stt_model : "Unknown model";
    setDiagnostic(diagnosticProvider, `${provider} · ${model}`, "ready");
    setDiagnostic(
      diagnosticVoice,
      voiceAvailable ? `Available · ${sttModel}` : "Unavailable",
      voiceAvailable ? "ready" : "warning",
    );
  } else {
    setDiagnostic(diagnosticProvider, "Available", "ready");
    setDiagnostic(
      diagnosticVoice,
      voiceAvailable ? "Available" : "Unavailable",
      voiceAvailable ? "ready" : "warning",
    );
  }
  setDiagnostic(
    diagnosticEmail,
    gmailSend ? "Enabled externally" : "Locked",
    gmailSend ? "warning" : "ready",
  );
  setDiagnostic(
    diagnosticCalendar,
    calendarWrites ? "Enabled externally" : "Locked",
    calendarWrites ? "warning" : "ready",
  );
  setBackendDiagnostic("Ready", "ready");
  backendReady = true;
  busy = false;
  setStatus("Ready", "ready");
  if (!settingsPanel.hidden && !memoryBusy) void loadMemories();
}

function handleVoiceState(message: IpcMessage): void {
  const state = message.payload?.state;
  if (typeof state !== "string") return;
  if (state === "listening") {
    recording = true;
    busy = false;
    setStatus("Listening - click mic to send", "listening");
  } else if (state === "transcribing") {
    recording = false;
    busy = true;
    setStatus("Transcribing", "busy");
  } else if (state === "thinking") {
    busy = true;
    setStatus("Thinking", "busy");
  } else if (state === "speaking") {
    busy = true;
    setStatus("Speaking", "busy");
  } else if (state === "idle") {
    recording = false;
    busy = false;
    setStatus("Ready", "ready");
  } else if (state === "error") {
    recording = false;
    busy = false;
    setStatus("Voice failed", "error");
  } else {
    busy = true;
    setStatus("Loading voice", "busy");
  }
}

function handleVoiceTranscript(message: IpcMessage): void {
  if (!message.request_id) return;
  const bubble = voiceTranscriptBubbles.get(message.request_id);
  if (!bubble) return;
  const heard = message.payload?.heard;
  const understood = message.payload?.understood;
  const corrected = message.payload?.corrected === true;
  const display =
    (typeof understood === "string" && understood) ||
    (typeof heard === "string" && heard) ||
    "Audio was not clear.";
  bubble.textContent = display;
  if (corrected && typeof heard === "string" && heard) {
    const note = document.createElement("small");
    note.textContent = `Heard: "${heard}"`;
    bubble.appendChild(note);
  }
}

function handleIpc(message: IpcMessage): void {
  if (message.protocol_version !== PROTOCOL_VERSION) {
    setStatus("Protocol mismatch", "error");
    return;
  }
  if (message.type === "event") {
    if (message.event === "ready") {
      applyHealth(message.payload);
    } else if (message.event === "assistant.delta" && message.request_id) {
      const pending = pendingTurns.get(message.request_id);
      const delta = message.payload?.delta;
      if (pending && typeof delta === "string") {
        pending.text = message.payload?.replace === true ? delta : pending.text + delta;
        pending.bubble.textContent = pending.text;
        conversation.scrollTop = conversation.scrollHeight;
      }
    } else if (message.event === "voice.state") {
      handleVoiceState(message);
    } else if (message.event === "voice.transcript") {
      handleVoiceTranscript(message);
    }
    return;
  }

  if (!message.request_id) return;
  const pendingBackendRequest = pendingBackendRequests.get(message.request_id);
  if (pendingBackendRequest) {
    pendingBackendRequests.delete(message.request_id);
    if (message.ok) {
      pendingBackendRequest.resolve(message.result ?? {});
    } else {
      pendingBackendRequest.reject(
        new Error(message.error?.message ?? "Jarvis could not complete that request."),
      );
    }
    return;
  }
  if (!message.ok) {
    const pending = pendingTurns.get(message.request_id);
    if (pending) {
      pending.bubble.classList.remove("streaming");
      pending.bubble.textContent = message.error?.message ?? "Jarvis could not complete that request.";
      pendingTurns.delete(message.request_id);
    }
    voiceTranscriptBubbles.delete(message.request_id);
    busy = false;
    recording = false;
    setStatus("Request failed", "error");
    return;
  }
  if (message.result?.status === "ready") {
    applyHealth(message.result);
    return;
  }
  if (message.result?.status === "listening") {
    recording = true;
    busy = false;
    setStatus("Listening - click mic to send", "listening");
    return;
  }
  if (message.result?.status === "cancelled") {
    recording = false;
    busy = false;
    setStatus("Ready", "ready");
    return;
  }

  const pending = pendingTurns.get(message.request_id);
  if (!pending) return;
  const result = message.result as TurnResult;
  const finalText = result.response ?? result.prompt ?? pending.text;
  pending.bubble.classList.remove("streaming");
  pending.bubble.textContent = finalText;
  pendingTurns.delete(message.request_id);
  voiceTranscriptBubbles.delete(message.request_id);
  if (result.needs_input && result.interrupt_kind === "approval" && result.proposal) {
    showApproval(result.prompt ?? "Approve this action?", result.proposal);
  }
  busy = false;
  recording = false;
  setStatus(result.needs_input ? "Waiting for you" : "Ready", "ready");
  if (!settingsPanel.hidden && !memoryBusy) void loadMemories();
}

function showApproval(summary: string, proposal: Record<string, unknown>): void {
  approvalSummary.textContent = summary;
  approvalPayload.textContent = JSON.stringify(proposal, null, 2);
  approval.hidden = false;
}

function hideApproval(): void {
  approval.hidden = true;
  approvalSummary.textContent = "";
  approvalPayload.textContent = "";
}

function memoryLabel(key: string): string {
  return key.replaceAll("_", " ");
}

function memoryTimestamp(value: string): string {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.valueOf()) ? value : timestamp.toLocaleString();
}

function setMemoryFeedback(message: string, state?: "ready" | "error"): void {
  memoryFeedback.textContent = message;
  if (state) memoryFeedback.dataset.state = state;
  else delete memoryFeedback.dataset.state;
}

function setMemoryBusy(value: boolean): void {
  memoryBusy = value;
  memoryRefresh.disabled = value;
  for (const control of settingsPanel.querySelectorAll<
    HTMLButtonElement | HTMLTextAreaElement
  >(".memory-settings button, .memory-settings textarea")) {
    control.disabled = value;
  }
}

function memoryButton(label: string, className = "secondary"): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `${className} memory-action`;
  button.textContent = label;
  return button;
}

function memoryMetadata(entry: MemoryRecord): HTMLDivElement {
  const metadata = document.createElement("div");
  metadata.className = "memory-metadata";
  for (const value of [
    entry.kind,
    entry.sensitivity,
    entry.provenance.replaceAll("_", " "),
    memoryTimestamp(entry.updated_at),
  ]) {
    const item = document.createElement("span");
    item.textContent = value;
    metadata.appendChild(item);
  }
  return metadata;
}

function showMemoryCorrection(card: HTMLElement, entry: MemoryRecord): void {
  if (memoryBusy || card.querySelector(".memory-edit")) return;
  const actions = card.querySelector<HTMLElement>(".memory-actions");
  if (actions) actions.hidden = true;

  const form = document.createElement("form");
  form.className = "memory-edit";
  const current = document.createElement("p");
  current.className = "memory-current-value";
  current.textContent = `Current value: ${entry.value}`;
  const label = document.createElement("label");
  label.textContent = "Corrected value";
  const input = document.createElement("textarea");
  input.rows = 2;
  input.maxLength = 4000;
  input.required = true;
  input.value = entry.value;
  label.appendChild(input);
  const controls = document.createElement("div");
  controls.className = "memory-edit-actions";
  const cancel = memoryButton("Cancel");
  const save = memoryButton("Save correction", "primary");
  save.type = "submit";
  controls.append(cancel, save);
  form.append(current, label, controls);
  card.appendChild(form);
  input.focus();
  input.select();

  cancel.addEventListener("click", () => {
    form.remove();
    if (actions) actions.hidden = false;
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = input.value.trim();
    if (!value) {
      setMemoryFeedback("A corrected memory value cannot be blank.", "error");
      input.focus();
      return;
    }
    void (async () => {
      setMemoryBusy(true);
      setMemoryFeedback("Saving correction...");
      try {
        const result = await requestBackend("memory.correct", {
          memory_id: entry.memory_id,
          value,
        });
        const oldRecord = result.old as MemoryRecord;
        const newRecord = result.new as MemoryRecord;
        setMemoryFeedback(
          oldRecord.memory_id === newRecord.memory_id
            ? `${memoryLabel(entry.key)} is unchanged.`
            : `Corrected ${memoryLabel(entry.key)}: “${oldRecord.value}” → “${newRecord.value}”.`,
          "ready",
        );
        await loadMemories(false);
      } catch (error) {
        setMemoryFeedback(errorMessage(error), "error");
      } finally {
        setMemoryBusy(false);
      }
    })();
  });
}

function showForgetConfirmation(actions: HTMLElement, entry: MemoryRecord): void {
  if (memoryBusy) return;
  actions.replaceChildren();
  actions.classList.add("confirming");
  const prompt = document.createElement("p");
  prompt.textContent = `Forget “${memoryLabel(entry.key)}: ${entry.value}”?`;
  const cancel = memoryButton("Keep");
  const confirm = memoryButton("Confirm forget", "danger");
  actions.append(prompt, cancel, confirm);
  cancel.addEventListener("click", () => renderMemories());
  confirm.addEventListener("click", () => {
    void (async () => {
      setMemoryBusy(true);
      setMemoryFeedback("Forgetting memory...");
      try {
        await requestBackend("memory.forget", { memory_id: entry.memory_id });
        setMemoryFeedback(
          `Forgot ${memoryLabel(entry.key)}. It can be restored from History.`,
          "ready",
        );
        await loadMemories(false);
      } catch (error) {
        setMemoryFeedback(errorMessage(error), "error");
      } finally {
        setMemoryBusy(false);
      }
    })();
  });
}

let displayedMemories: MemoryListResult = { active: [], history: [] };

function memoryCard(entry: MemoryRecord): HTMLElement {
  const card = document.createElement("article");
  card.className = "memory-card";
  const heading = document.createElement("div");
  heading.className = "memory-card-heading";
  const title = document.createElement("strong");
  title.textContent = memoryLabel(entry.key);
  const statusBadge = document.createElement("span");
  statusBadge.className = `memory-badge ${entry.status}`;
  statusBadge.textContent = entry.status;
  heading.append(title, statusBadge);
  if (entry.sensitivity === "sensitive") {
    const sensitiveBadge = document.createElement("span");
    sensitiveBadge.className = "memory-badge sensitive";
    sensitiveBadge.textContent = "sensitive";
    heading.appendChild(sensitiveBadge);
  }
  const value = document.createElement("p");
  value.className = "memory-value";
  value.textContent = entry.value;
  const actions = document.createElement("div");
  actions.className = "memory-actions";

  if (entry.status === "active") {
    const edit = memoryButton("Correct");
    const forget = memoryButton("Forget");
    edit.addEventListener("click", () => showMemoryCorrection(card, entry));
    forget.addEventListener("click", () => showForgetConfirmation(actions, entry));
    actions.append(edit, forget);
  } else if (
    entry.status === "forgotten" &&
    !displayedMemories.active.some((active) => active.key === entry.key)
  ) {
    const restore = memoryButton("Restore");
    restore.addEventListener("click", () => {
      if (memoryBusy) return;
      void (async () => {
        setMemoryBusy(true);
        setMemoryFeedback("Restoring memory...");
        try {
          await requestBackend("memory.restore", { memory_id: entry.memory_id });
          setMemoryFeedback(`Restored ${memoryLabel(entry.key)}.`, "ready");
          await loadMemories(false);
        } catch (error) {
          setMemoryFeedback(errorMessage(error), "error");
        } finally {
          setMemoryBusy(false);
        }
      })();
    });
    actions.appendChild(restore);
  }

  card.append(heading, value, memoryMetadata(entry));
  if (actions.childElementCount) card.appendChild(actions);
  return card;
}

function renderMemoryList(container: HTMLElement, entries: MemoryRecord[]): void {
  container.replaceChildren();
  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "memory-empty";
    empty.textContent = "No memory records in this section.";
    container.appendChild(empty);
    return;
  }
  for (const entry of entries) container.appendChild(memoryCard(entry));
}

function renderMemories(): void {
  memoryActiveCount.textContent = String(displayedMemories.active.length);
  memoryHistoryCount.textContent = String(displayedMemories.history.length);
  renderMemoryList(memoryActiveList, displayedMemories.active);
  renderMemoryList(memoryHistoryList, displayedMemories.history);
}

async function loadMemories(showLoading = true): Promise<void> {
  if (!backendReady) {
    setMemoryFeedback("Memory controls will load when the backend is ready.");
    return;
  }
  setMemoryBusy(true);
  if (showLoading) setMemoryFeedback("Loading memories...");
  try {
    const result = await requestBackend("memory.list");
    displayedMemories = {
      active: Array.isArray(result.active) ? (result.active as MemoryRecord[]) : [],
      history: Array.isArray(result.history) ? (result.history as MemoryRecord[]) : [],
    };
    renderMemories();
    if (showLoading) {
      setMemoryFeedback(
        `${displayedMemories.active.length} active memories loaded.`,
        "ready",
      );
    }
  } catch (error) {
    setMemoryFeedback(errorMessage(error), "error");
  } finally {
    setMemoryBusy(false);
  }
}

function renderHotkey(hotkey: DesktopHotkeyStatus): void {
  currentHotkey = hotkey;
  shortcutInput.value = hotkey.shortcut;
  shortcutCurrent.textContent = hotkey.registered
    ? `Active: ${hotkey.shortcut} · ${hotkey.source}`
    : `Unavailable: ${hotkey.shortcut}`;
  shortcutCurrent.dataset.state = hotkey.registered ? "ready" : "error";
  status.title = hotkey.registered
    ? `Global show/hide shortcut: ${hotkey.shortcut}`
    : `Global shortcut unavailable: ${hotkey.shortcut}`;
}

async function loadDesktopHotkeyStatus(notifyFailure = true): Promise<void> {
  try {
    const hotkey = await invoke<DesktopHotkeyStatus>("desktop_hotkey_status");
    renderHotkey(hotkey);
    if (!hotkey.registered && notifyFailure) {
      appendBubble(
        "system",
        hotkey.error
          ? `${hotkey.error}. Tray controls remain available.`
          : `The global shortcut ${hotkey.shortcut} is unavailable. Tray controls remain available.`,
      );
    }
  } catch {
    shortcutCurrent.textContent = "Global shortcut status is unavailable.";
    shortcutCurrent.dataset.state = "error";
    if (notifyFailure) {
      appendBubble(
        "system",
        "Global shortcut status is unavailable. Tray controls remain available.",
      );
    }
  }
}

function setSettingsOpen(open: boolean): void {
  settingsPanel.hidden = !open;
  settingsToggle.setAttribute("aria-expanded", String(open));
  if (open) {
    settingsFeedback.textContent = "";
    delete settingsFeedback.dataset.state;
    if (currentHotkey) shortcutInput.value = currentHotkey.shortcut;
    void Promise.all([loadDesktopHotkeyStatus(false), loadMemories()]).then(() =>
      shortcutInput.focus(),
    );
  } else {
    settingsToggle.focus();
  }
}

function errorMessage(error: unknown): string {
  if (typeof error === "string") return error;
  if (error instanceof Error) return error.message;
  return "The request could not be completed.";
}

async function saveDesktopHotkey(): Promise<void> {
  settingsFeedback.textContent = "Checking shortcut...";
  delete settingsFeedback.dataset.state;
  shortcutInput.disabled = true;
  shortcutSave.disabled = true;
  try {
    const hotkey = await invoke<DesktopHotkeyStatus>("set_desktop_hotkey", {
      shortcut: shortcutInput.value,
    });
    renderHotkey(hotkey);
    settingsFeedback.textContent = `${hotkey.shortcut} is active and saved.`;
  } catch (error) {
    const previous = currentHotkey?.registered
      ? ` ${currentHotkey.shortcut} remains active.`
      : " Tray controls remain available.";
    settingsFeedback.textContent = `${errorMessage(error)}${previous}`;
    settingsFeedback.dataset.state = "error";
    if (currentHotkey) shortcutInput.value = currentHotkey.shortcut;
  } finally {
    shortcutInput.disabled = false;
    shortcutSave.disabled = false;
    shortcutInput.focus();
  }
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = messageInput.value;
  messageInput.value = "";
  messageInput.style.height = "auto";
  void sendTurn(text);
});

messageInput.addEventListener("input", () => {
  messageInput.style.height = "auto";
  messageInput.style.height = `${Math.min(messageInput.scrollHeight, 132)}px`;
});

messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

approveButton.addEventListener("click", () => void sendTurn("yes"));
rejectButton.addEventListener("click", () => void sendTurn("no"));
microphoneButton.addEventListener("click", () => void toggleVoice());
settingsToggle.addEventListener("click", () => setSettingsOpen(settingsPanel.hidden));
settingsClose.addEventListener("click", () => setSettingsOpen(false));
shortcutForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void saveDesktopHotkey();
});
memoryRefresh.addEventListener("click", () => {
  if (!memoryBusy) void loadMemories();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !settingsPanel.hidden) setSettingsOpen(false);
});

setStatus("Starting backend", "starting");
await listen<IpcMessage>("jarvis-ipc", ({ payload }) => handleIpc(payload));
await listen<{ code: number | null }>("jarvis-backend-exit", ({ payload }) => {
  backendReady = false;
  busy = false;
  recording = false;
  for (const pending of pendingBackendRequests.values()) {
    pending.reject(new Error("The backend connection closed."));
  }
  pendingBackendRequests.clear();
  setMemoryFeedback("Memory controls are unavailable.", "error");
  setBackendDiagnostic(payload.code === 0 ? "Stopped" : "Crashed", "error");
  setStatus(payload.code === 0 ? "Backend stopped" : "Backend crashed", "error");
});
await loadDesktopHotkeyStatus();

try {
  await sendRequest(newRequest("health"));
} catch {
  appendBubble("system", "Jarvis could not start its local backend.");
  setBackendDiagnostic("Unavailable", "error");
  setStatus("Backend unavailable", "error");
}
