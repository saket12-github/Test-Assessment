import { useState } from "react";
import CustomerView from "./CustomerView.jsx";
import AgentView from "./AgentView.jsx";

function loadMode() {
  try {
    return localStorage.getItem("mode") || "customer";
  } catch {
    return "customer";
  }
}

export default function App() {
  const [mode, setMode] = useState(loadMode);

  const switchTo = (m) => {
    setMode(m);
    try {
      localStorage.setItem("mode", m);
    } catch {
      /* storage unavailable */
    }
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">Verdant &amp; Co <span>Support</span></div>
        <div className="toggle" role="tablist" aria-label="View">
          {["customer", "agent"].map((m) => (
            <button key={m} role="tab" aria-selected={mode === m} className={mode === m ? "active" : ""} onClick={() => switchTo(m)}>
              {m === "customer" ? "Customer view" : "Agent view"}
            </button>
          ))}
        </div>
      </header>
      <main className="content">{mode === "customer" ? <CustomerView /> : <AgentView />}</main>
    </div>
  );
}
