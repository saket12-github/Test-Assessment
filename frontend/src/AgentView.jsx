import { useEffect, useRef, useState } from "react";
import { api, formatTime } from "./api.js";
import { usePolling } from "./usePolling.js";

export default function AgentView() {
  const [agent, setAgent] = useState(undefined); // undefined = checking, null = signed out

  useEffect(() => {
    api("/api/auth/me").then(setAgent).catch(() => setAgent(null));
  }, []);

  if (agent === undefined) return <p className="muted center">Loading…</p>;
  if (agent === null) return <Login onSignedIn={setAgent} />;
  return <Inbox agent={agent} onSignedOut={() => setAgent(null)} />;
}

function Login({ onSignedIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onSignedIn(await api("/api/auth/login", { method: "POST", body: { email, password } }));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="panel login" onSubmit={submit}>
      <h2>Agent sign in</h2>
      <label>Email<input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
      <label>Password<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
      {error && <div className="error">{error}</div>}
      <button className="primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form>
  );
}

function Inbox({ agent, onSignedOut }) {
  const [filter, setFilter] = useState("open");
  const [conversations, setConversations] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [error, setError] = useState("");

  const handleAuthError = (e) => {
    if (e.status === 401) onSignedOut();
    else setError(e.message);
  };

  const loadList = async () => {
    try {
      setConversations(await api(`/api/conversations?status=${filter}`));
      setError("");
    } catch (e) {
      handleAuthError(e);
    }
  };

  usePolling(loadList, 4000, [filter]);

  const logout = async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    onSignedOut();
  };

  return (
    <div className="agent">
      <aside className="panel list">
        <div className="list-head">
          <div className="tabs">
            {["open", "closed"].map((f) => (
              <button key={f} className={filter === f ? "active" : ""} onClick={() => { setFilter(f); setSelectedId(null); }}>
                {f === "open" ? "Open" : "Closed"}
              </button>
            ))}
          </div>
          <div className="who">
            {agent.name} · <button className="link" onClick={logout}>Sign out</button>
          </div>
        </div>
        {error && <div className="error">{error}</div>}
        {conversations.length === 0 && <p className="muted center">No {filter} conversations.</p>}
        <ul>
          {conversations.map((c) => (
            <li key={c.id} className={c.id === selectedId ? "selected" : ""} onClick={() => setSelectedId(c.id)}>
              <div className="li-top">
                <strong>{c.customer.name}</strong>
                <time>{formatTime(c.last_message_at)}</time>
              </div>
              <div className="preview">
                {c.last_message.sender === "agent" && <span className="muted">You: </span>}
                {c.last_message.text}
              </div>
              <div className="badges">
                {c.awaiting_reply && <span className="badge warn">Awaiting reply</span>}
                {c.draft_status === "ready" && <span className="badge ok">Draft ready</span>}
                {c.draft_status === "pending" && <span className="badge">Drafting…</span>}
                {c.draft_status === "failed" && <span className="badge bad">Draft failed</span>}
              </div>
            </li>
          ))}
        </ul>
      </aside>
      <section className="panel conversation">
        {selectedId ? (
          <Conversation key={selectedId} id={selectedId} onAuthError={handleAuthError} onChanged={loadList} />
        ) : (
          <p className="muted center">Select a conversation.</p>
        )}
      </section>
    </div>
  );
}

function Conversation({ id, onAuthError, onChanged }) {
  const [conv, setConv] = useState(null);
  const [loadError, setLoadError] = useState("");
  // The editor is tied to one draft id so polling never overwrites what the agent is typing.
  const [editor, setEditor] = useState({ draftId: null, text: "", dirty: false, loadedStatus: null });
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const bottomRef = useRef(null);

  const load = async () => {
    try {
      const data = await api(`/api/conversations/${id}`);
      setConv(data);
      setLoadError("");
      setEditor((ed) => syncEditor(ed, data.draft));
    } catch (e) {
      if (e.status === 401) onAuthError(e);
      else setLoadError(e.message);
    }
  };

  usePolling(load, 3000, [id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [conv?.messages.length]);

  if (!conv) return <p className="muted center">{loadError || "Loading…"}</p>;

  const draft = conv.draft;
  // The agent has typed, and the server has something newer than what their text was based on.
  const newerDraft = draft && editor.dirty && editor.draftId !== draft.id;
  const draftArrived =
    draft && editor.dirty && editor.draftId === draft.id && draft.status === "ready" && editor.loadedStatus !== "ready";
  const open = conv.status === "open";

  const sendReply = async () => {
    setBusy(true);
    setActionError("");
    try {
      await api(`/api/conversations/${id}/reply`, {
        method: "POST",
        body: { text: editor.text, draft_id: editor.draftId },
      });
      setEditor({ draftId: null, text: "", dirty: false, loadedStatus: null });
      await load();
      onChanged();
    } catch (e) {
      if (e.status === 401) onAuthError(e);
      else setActionError(e.message);
      await load();
    } finally {
      setBusy(false);
    }
  };

  const regenerate = async () => {
    setActionError("");
    try {
      await api(`/api/conversations/${id}/draft/regenerate`, { method: "POST" });
      setEditor((ed) => ({ ...ed, dirty: false, loadedStatus: null }));
      await load();
    } catch (e) {
      if (e.status === 401) onAuthError(e);
      else setActionError(e.message);
    }
  };

  const close = async () => {
    if (!window.confirm("Close this conversation? The customer's next message will start a new one.")) return;
    try {
      await api(`/api/conversations/${id}/close`, { method: "POST" });
      await load();
      onChanged();
    } catch (e) {
      setActionError(e.message);
    }
  };

  return (
    <>
      <div className="conv-head">
        <div>
          <strong>{conv.customer.name}</strong> <span className="muted">{conv.customer.phone}</span>
          {!open && <span className="badge">Closed</span>}
        </div>
        {open && <button onClick={close}>Close conversation</button>}
      </div>
      {loadError && <div className="error">Connection problem: {loadError}</div>}

      <div className="thread">
        {conv.messages.map((m) => (
          <div key={m.id} className={`bubble ${m.sender === "agent" ? "mine" : "theirs"}`}>
            <div>{m.text}</div>
            <time>{m.sender === "agent" && m.agent_name ? `${m.agent_name} · ` : ""}{formatTime(m.created_at)}</time>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {open && (
        <div className="draft">
          <DraftStatus draft={draft} onRegenerate={regenerate} />
          {(newerDraft || draftArrived) && (
            <div className="notice">
              {newerDraft ? "The customer sent a new message since you started typing." : "The AI draft is ready."}{" "}
              <button className="link" onClick={() => setEditor(syncEditor({ dirty: false }, draft))}>
                {newerDraft ? "Load the latest draft" : "Use the AI draft"}
              </button>{" "}
              (replaces your text)
            </div>
          )}
          <textarea
            rows={5}
            value={editor.text}
            maxLength={4096}
            placeholder={draft?.status === "pending" ? "Waiting for the AI draft… you can also write a reply yourself." : "Write a reply"}
            onChange={(e) => setEditor({ ...editor, text: e.target.value, dirty: true })}
          />
          {actionError && <div className="error">{actionError}</div>}
          <div className="actions">
            <span className="muted small">Nothing is sent to the customer until you approve.</span>
            <button className="primary" disabled={busy || !editor.text.trim()} onClick={sendReply}>
              {busy ? "Saving…" : editor.draftId && draft?.status === "ready" ? "Approve & send" : "Send reply"}
            </button>
          </div>
        </div>
      )}
    </>
  );
}

// Decide what the editor should show when fresh data arrives from the server.
function syncEditor(ed, draft) {
  if (!draft) {
    // No active draft (already answered, or conversation closed). Keep typed text as a manual reply.
    return ed.dirty ? { ...ed, draftId: null } : { draftId: null, text: "", dirty: false, loadedStatus: null };
  }
  if (ed.dirty) return ed; // never overwrite the agent's edits; the UI offers to load the new draft
  if (ed.draftId === draft.id && ed.loadedStatus === draft.status) return ed;
  return { draftId: draft.id, text: draft.status === "ready" ? draft.text : "", dirty: false, loadedStatus: draft.status };
}

function DraftStatus({ draft, onRegenerate }) {
  if (!draft) return <div className="draft-head">Reply</div>;
  const stuck = draft.status === "pending" && Date.now() - new Date(draft.updated_at).getTime() > 90_000;

  return (
    <div className="draft-head">
      <div>
        <strong>AI draft</strong>{" "}
        {draft.status === "pending" && <span className="badge">Drafting…</span>}
        {draft.status === "ready" && <span className="badge ok">Ready</span>}
        {draft.status === "failed" && <span className="badge bad">Failed</span>}
        {draft.kb_topics.length > 0 && <span className="muted small"> · Based on: {draft.kb_topics.join(", ")}</span>}
      </div>
      {(draft.status === "failed" || draft.status === "ready" || stuck) && (
        <button onClick={onRegenerate}>{draft.status === "ready" ? "Regenerate" : "Retry"}</button>
      )}
      {draft.status === "failed" && (
        <div className="error full">Could not generate a draft: {draft.error}. Retry, or write the reply yourself.</div>
      )}
      {draft.status === "ready" && draft.needs_human && (
        <div className="notice full">⚠ Needs your attention{draft.note ? `: ${draft.note}` : ". The policies may not fully cover this."}</div>
      )}
    </div>
  );
}
