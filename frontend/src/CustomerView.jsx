import { useEffect, useRef, useState } from "react";
import { api, formatTime } from "./api.js";
import { usePolling } from "./usePolling.js";

const SAMPLES = [
  "Hi, my order #4471 hasn't arrived yet. Can I change the delivery address?",
  "What is your return policy?",
  "Do you deliver to Dubai?",
  "Is cash on delivery available for a ₹3,500 order?",
  "I think I'm allergic to the serum I bought. What should I do?",
];

const CUSTOM = "__custom__";

export default function CustomerView() {
  const [testCustomers, setTestCustomers] = useState([]);
  const [selected, setSelected] = useState("cust_1042");
  const [custom, setCustom] = useState({ name: "", phone: "" });
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef(null);

  useEffect(() => {
    api("/api/customer/test-customers").then(setTestCustomers).catch((e) => setError(e.message));
  }, []);

  const customCustomerReady = custom.name.trim() && custom.phone.trim();
  const identity =
    selected === CUSTOM
      ? customCustomerReady ? { name: custom.name.trim(), phone: custom.phone.trim() } : null
      : testCustomers.find((c) => c.id === selected) || null;
  // Typed-in customers are identified by phone (mirrors the backend rule).
  const externalId = identity ? identity.id || `phone_${identity.phone.replace(/[\s-]/g, "").replace(/^\+/, "")}` : null;

  usePolling(
    async () => {
      if (!externalId) return setMessages([]);
      setMessages(await api(`/api/customer/messages?external_id=${encodeURIComponent(externalId)}`));
    },
    3000,
    [externalId]
  );

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  const send = async (body) => {
    if (!identity || !body.trim() || sending) return;
    setSending(true);
    setError("");
    try {
      await api("/api/customer/messages", { method: "POST", body: { customer: identity, text: body } });
      setText("");
      setMessages(await api(`/api/customer/messages?external_id=${encodeURIComponent(externalId)}`));
    } catch (e) {
      setError(e.message);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="customer">
      <div className="panel identity">
        <label>
          You are
          <select value={selected} onChange={(e) => setSelected(e.target.value)}>
            {testCustomers.map((c) => (
              <option key={c.id} value={c.id}>{c.name} ({c.phone})</option>
            ))}
            <option value={CUSTOM}>Someone else…</option>
          </select>
        </label>
        {selected === CUSTOM && (
          <div className="row">
            <input placeholder="Name" value={custom.name} onChange={(e) => setCustom({ ...custom, name: e.target.value })} />
            <input placeholder="Phone, e.g. +919812345678" value={custom.phone} onChange={(e) => setCustom({ ...custom, phone: e.target.value })} />
          </div>
        )}
      </div>

      <div className="panel chat">
        <div className="chat-head">Chat with Verdant &amp; Co</div>
        <div className="thread">
          {!identity && <p className="muted center">Enter your name and phone number to start.</p>}
          {identity && messages.length === 0 && <p className="muted center">No messages yet. Say hello!</p>}
          {messages.map((m) => (
            <div key={m.id} className={`bubble ${m.sender === "customer" ? "mine" : "theirs"}`}>
              <div>{m.text}</div>
              <time>{formatTime(m.created_at)}</time>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        <div className="samples">
          {SAMPLES.map((s) => (
            <button key={s} className="chip" disabled={!identity || sending} onClick={() => send(s)} title="Send this sample message">
              {s}
            </button>
          ))}
        </div>
        {error && <div className="error">{error}</div>}
        <form className="composer" onSubmit={(e) => { e.preventDefault(); send(text); }}>
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={identity ? "Type a message" : "Choose who you are first"}
            disabled={!identity}
            maxLength={4096}
          />
          <button className="primary" disabled={!identity || !text.trim() || sending}>{sending ? "Sending…" : "Send"}</button>
        </form>
      </div>
    </div>
  );
}
