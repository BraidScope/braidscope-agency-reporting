// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import ConnectorLogo from "./ConnectorLogo";
import { getChatConfig, streamChat } from "../lib/chat";
import {
  listAIProviderModels,
  saveBYOKAIProvider,
  startClaudeCodeAuth,
  submitClaudeCodeCode,
  startOpenAICodexAuth,
  startOpenRouterOAuth,
} from "../lib/dataSources";

const SUGGESTIONS = [
  "How many active users do we have right now?",
  "What were our top traffic sources this month?",
  "What are our top 5 pages by views this month?",
  "How much did we spend on Google Ads this month?",
  "Which campaigns performed best this month?",
  "Show me my Klaviyo account details?",
  "How did our last email campaign perform?",
  "What's our Placed Order revenue over the last 30 days?",
  "List my recent HubSpot contacts",
  "What deals are in my pipeline right now?",
  "Show me my companies in HubSpot",
  "How did our Meta Ads campaigns perform this month?",
  "How much did we spend on Meta Ads and what was our ROAS?",
];

// Presentation only: how long the featured question stays before the next one
// fades in.
const SUGGESTION_ROTATE_MS = 6500;
const SUGGESTION_SWAP_MS = 240;

const INTEGRATION_LABELS = {
  google_ads: "Google Ads",
  google_analytics_4: "GA4",
  meta_ads: "Meta Ads",
  klaviyo: "Klaviyo",
  hubspot_crm: "HubSpot",
  shopify: "Shopify",
  // Connected AI providers also arrive in `sources`; show their real names
  // instead of the raw connector ids.
  openrouter_ai: "OpenRouter",
  openai_ai: "OpenAI",
  anthropic_ai: "Claude",
};

const isProviderType = (type) => typeof type === "string" && type.endsWith("_ai");

// Small provider marks for the model selector (same colours as PROVIDER_SETUP).
const PROVIDER_MARKS = {
  openrouter: { bg: "#111827", mark: "O" },
  openai: { bg: "#10A37F", mark: "AI" },
  openai_codex: { bg: "#10A37F", mark: "AI" },
  anthropic: { bg: "#D97757", mark: "C" },
  claude_code: { bg: "#D97757", mark: "C" },
};

function providerMark(provider) {
  return PROVIDER_MARKS[provider] || { bg: "#3a3a34", mark: "?" };
}

// Which catalog entry (lib/data.js SOURCES) carries each provider's real logo.
const PROVIDER_SOURCE_TYPE = {
  openrouter: "openrouter_ai",
  openai: "openai_ai",
  openai_codex: "openai_ai",
  anthropic: "anthropic_ai",
  claude_code: "anthropic_ai",
};

// The provider's genuine brand mark on a light tile (readable on the dark
// theme too, same treatment as the onboarding provider cards). Falls back to
// the old lettered tile when the catalog entry is not available.
function ProviderLogo({ provider, sources, size = "md" }) {
  const source = (sources || []).find((item) => item.type === PROVIDER_SOURCE_TYPE[provider]);
  if (source) {
    // Brand-coloured tile with the real mark in white (like the Claude tile in
    // the design reference); the mask colour comes from source.bg, so pass a
    // white copy of the entry.
    return (
      <span className={`aid-plogo ${size}`} style={{ background: source.bg }} title={source.name}>
        <ConnectorLogo source={{ ...source, bg: "#fff" }} className="aid-plogo-inner" />
      </span>
    );
  }
  return (
    <span className={`aid-plogo ${size} is-letter`} style={{ background: providerMark(provider).bg }}>
      {providerMark(provider).mark}
    </span>
  );
}

const AI_PROVIDER_LABELS = {
  openrouter: "OpenRouter",
  openai: "OpenAI",
  // The backend reports the existing ChatGPT/Codex connection under this
  // distinct provider id (py/app/chat_service.py's ai_providers list) -
  // never "openai", which stays reserved for the separate raw-API-key BYOK
  // path (still fully supported, untouched). Same display label either
  // way since both are "OpenAI" from the user's point of view.
  openai_codex: "OpenAI",
  anthropic: "Claude",
  // Per-user Claude Code CLI sign-in (py/app/claude_code.py). "anthropic"
  // above stays reserved for the hidden raw-API-key BYOK path.
  claude_code: "Claude",
};

const PROVIDER_SETUP = [
  { id: "openrouter", name: "OpenRouter", desc: "One login, hundreds of models", tag: "OAuth", bg: "#111827", mark: "O" },
  { id: "openai", name: "OpenAI", desc: "Connect with your ChatGPT account", tag: "ChatGPT", bg: "#10A37F", mark: "AI" },
  { id: "anthropic", name: "Claude", desc: "Sign in with your Claude account", tag: "Account", bg: "#D97757", mark: "C" },
];

const DEFAULT_MODELS = {
  openrouter: "moonshotai/kimi-k2.6:free",
  // Deliberately no suggested default for the ChatGPT/Codex connection
  // (used to be "gpt-4.1-mini") - that's an OpenAI Responses-API model id,
  // not one Codex CLI's ChatGPT-account auth mode accepts at all (confirmed
  // live: rejected outright), and there is no way to know which models this
  // account can actually use before it has authenticated. The real,
  // verified model list is only discoverable after connecting (see
  // py/app/codex.py's discover_codex_models), which is exactly why this
  // field is left blank rather than suggesting a model that would just be
  // wrong.
  openai: "",
  // No suggested default for the Claude account sign-in: the usable models
  // depend on the signed-in account and are discovered after connecting.
  anthropic: "",
};

// How often / how long the picker re-asks while the server reports that a
// provider's model list is still being verified in the background.
const MODELS_POLL_MS = 3000;
const MODELS_POLL_MAX_ATTEMPTS = 60;

// Flip to true to expose the hidden Claude API-key (BYOK) form again.
const SHOW_CLAUDE_BYOK = false;

function label(name) {
  return INTEGRATION_LABELS[name] || name;
}

// Claude Code model ids are aliases ("opus"); show the discovered real name
// ("Opus 5.5") and flag models that need usage credits, like Claude Code's own
// picker. Other providers keep their raw ids exactly as before.
function modelOptionLabel(provider, entry) {
  if (provider !== "claude_code") return entry.id;
  return `${entry.name || entry.id}${entry.requires_credits ? " · requires usage credits" : ""}`;
}

function providerLabel(provider) {
  return AI_PROVIDER_LABELS[provider] || provider || "AI provider";
}

/* ---------------- suggested questions (presentation only) ---------------- */

// One featured question at a time, crossfading to the next. Clicking it calls
// the same onPick (the drawer's existing send) as every other question.
function RotatingSuggestion({ questions, onPick, disabled }) {
  const [index, setIndex] = useState(0);
  const [leaving, setLeaving] = useState(false);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (paused || questions.length < 2) {
      setLeaving(false);
      return undefined;
    }
    let swap;
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      setLeaving(true);
      swap = window.setTimeout(() => {
        setIndex((current) => (current + 1) % questions.length);
        setLeaving(false);
      }, SUGGESTION_SWAP_MS);
    }, SUGGESTION_ROTATE_MS);
    return () => {
      window.clearInterval(timer);
      window.clearTimeout(swap);
    };
  }, [paused, questions.length]);

  const current = questions[index];
  return (
    <>
      <div className="aid-starter-row">
        <span className="aid-starter-label">Try asking</span>
      </div>
      <button
        type="button"
        className={`aid-suggest${leaving ? " is-leaving" : ""}`}
        onClick={() => onPick(current)}
        disabled={disabled}
        title={current}
        onMouseEnter={() => setPaused(true)}
        onMouseLeave={() => setPaused(false)}
        onFocus={() => setPaused(true)}
        onBlur={() => setPaused(false)}
      >
        <span className="aid-suggest-ico"><i className="ti ti-arrow-up-right" /></span>
        <span className="aid-suggest-text" key={index}>{current}</span>
        <i className="ti ti-chevron-right aid-suggest-go" aria-hidden="true" />
      </button>
    </>
  );
}

// The "what I can see" row: connected DATA SOURCES only, on a single line. When
// they all fit it is completely static; only when they do not does it drift
// slowly (a seamless loop over a duplicated, aria-hidden copy) and pause on
// hover. Purely presentational - it renders the chips it is given.
function ContextRow({ chips }) {
  const viewportRef = useRef(null);
  const groupRef = useRef(null);
  const [scrolls, setScrolls] = useState(false);
  const [groupWidth, setGroupWidth] = useState(0);
  const signature = chips.map((chip) => `${chip.type}:${chip.name}`).join("|");

  useLayoutEffect(() => {
    const measure = () => {
      const viewport = viewportRef.current;
      const group = groupRef.current;
      if (!viewport || !group) return;
      const width = group.getBoundingClientRect().width - 6; // minus trailing gap
      setGroupWidth(width);
      setScrolls(width > viewport.clientWidth + 1);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    if (viewportRef.current) observer.observe(viewportRef.current);
    return () => observer.disconnect();
  }, [signature]);

  const renderGroup = (copy) => (
    <div className="aid-ctx-group" ref={copy ? undefined : groupRef} aria-hidden={copy ? true : undefined}>
      {chips.map((chip) => (
        <span
          key={chip.type}
          role={copy ? undefined : "listitem"}
          className={`aid-ctx-chip${chip.live ? "" : " off"}`}
          title={chip.live ? "Live" : "Connected, not queried"}
        >
          <ConnectorLogo source={chip.source} className="aid-clogo" />
          <span className="aid-ctx-name">{chip.name}</span>
        </span>
      ))}
    </div>
  );

  return (
    <div className="aid-ctx">
      <span className="aid-ctx-label">Context</span>
      <div className={`aid-ctx-viewport${scrolls ? " is-scrolling" : ""}`} ref={viewportRef} role="list" aria-label="Connected data sources">
        <div
          className="aid-ctx-track"
          style={scrolls ? { "--ctx-duration": `${Math.max(24, Math.round(groupWidth / 14))}s` } : undefined}
        >
          {renderGroup(false)}
          {scrolls && renderGroup(true)}
        </div>
      </div>
    </div>
  );
}

/* ---------------- answer rendering ---------------- */

// The model is told to write compact prose and bullets for a narrow drawer, so
// the renderer only needs paragraphs, bullets and simple "Label: value" rows.
function AnswerBody({ text }) {
  const lines = (text || "").split("\n").map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return null;

  const blocks = [];
  let bullets = [];

  const flush = () => {
    if (!bullets.length) return;
    blocks.push(
      <ul key={`ul-${blocks.length}`} className="aid-list">
        {bullets.map((item, i) => <li key={i}>{item}</li>)}
      </ul>,
    );
    bullets = [];
  };

  for (const line of lines) {
    const bullet = line.match(/^[-*•]\s+(.*)$/) || line.match(/^\d+[.)]\s+(.*)$/);
    if (bullet) {
      bullets.push(bullet[1]);
      continue;
    }
    flush();
    blocks.push(<p key={`p-${blocks.length}`}>{line}</p>);
  }
  flush();

  return <div className="aid-prose">{blocks}</div>;
}

/* ---------------- clarifying question ---------------- */

function QuestionCard({ question, onAnswer, answered }) {
  const [picked, setPicked] = useState([]);

  const toggle = (labelText) => {
    if (answered) return;
    if (question.multi) {
      setPicked((current) => (
        current.includes(labelText)
          ? current.filter((item) => item !== labelText)
          : [...current, labelText]
      ));
      return;
    }
    onAnswer(labelText);
  };

  return (
    <div className={`aid-clarify${answered ? " answered" : ""}`}>
      <div className="aid-clarify-head">
        <span className="aid-clarify-badge"><i className="ti ti-help-circle" /> Quick question</span>
        {!answered && !question.multi && (
          <button className="aid-clarify-skip" onClick={() => onAnswer("You decide — pick the most sensible option.")}>
            Skip &amp; decide for me
          </button>
        )}
        {question.multi && <span className="aid-clarify-multi">Pick any</span>}
      </div>

      <div className="aid-clarify-q">{question.question}</div>

      <div className="aid-clarify-opts">
        {question.options.map((option, index) => {
          const isPicked = picked.includes(option.label);
          return (
            <button
              key={option.label}
              className={`aid-clarify-opt${isPicked ? " picked" : ""}`}
              onClick={() => toggle(option.label)}
              disabled={answered}
            >
              <span className={question.multi ? "aid-cbox" : "aid-key"}>
                {question.multi ? (isPicked ? <i className="ti ti-check" /> : null) : index + 1}
              </span>
              <span>
                <span className="aid-opt-t">{option.label}</span>
                {option.hint && <span className="aid-opt-s">{option.hint}</span>}
              </span>
            </button>
          );
        })}
      </div>

      {question.multi && !answered && (
        <button
          className="aid-clarify-cta"
          disabled={!picked.length}
          onClick={() => onAnswer(picked.join(", "))}
        >
          {picked.length ? `Continue with ${picked.length} selected` : "Pick at least one"}
        </button>
      )}
    </div>
  );
}

/* ---------------- drawer ---------------- */

export default function AiDrawer({ open, onClose, sourceSummary, client, sources }) {
  const [turns, setTurns] = useState([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [config, setConfig] = useState(null);
  const [configError, setConfigError] = useState("");

  const [modelOpen, setModelOpen] = useState(false);
  const [popClosing, setPopClosing] = useState(false);
  const triggerRef = useRef(null);
  const popRef = useRef(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  // Model lists are kept per client AND per provider (key "clientId:provider")
  // so one provider's catalog can never be shown for another, nor one
  // client's for the next.
  const [modelsByProvider, setModelsByProvider] = useState({});
  const modelsRequestedRef = useRef(new Set());
  const clientIdRef = useRef(client?.id);
  clientIdRef.current = client?.id;
  const modelOptions = modelsByProvider[`${client?.id}:${provider}`] || [];

  const [setupProvider, setSetupProvider] = useState(null);
  const [setupKey, setSetupKey] = useState("");
  const [claudeAwaitingCode, setClaudeAwaitingCode] = useState(false);
  const [setupModel, setSetupModel] = useState("");
  const [setupSaving, setSetupSaving] = useState(false);
  const [setupError, setSetupError] = useState("");

  const bodyRef = useRef(null);
  const needsProvider = Boolean(config) && !config.openai_configured;
  const ready = Boolean(config?.ready);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" });
  }, [turns, loading]);

  // The drawer stays mounted across client switches, so without this it kept
  // showing the PREVIOUS client's config (e.g. "Pick a model to get started"
  // from a client with no AI provider) and its provider/model choice until
  // the new client's config arrived. Start every client from a clean slate.
  useEffect(() => {
    setConfig(null);
    setConfigError("");
    setProvider("");
    setModel("");
    setModelOpen(false);
  }, [client?.id]);

  useEffect(() => {
    if (!open) return;
    if (!client?.id) {
      setConfig(null);
      setConfigError("Select a client workspace to use Ask AI.");
      return;
    }
    const requestedFor = client.id;
    getChatConfig(client, sources)
      .then((next) => {
        // Ignore a response that belongs to a client we've since left.
        if (clientIdRef.current !== requestedFor) return;
        setConfig(next);
        setConfigError("");
      })
      .catch((error) => {
        if (clientIdRef.current !== requestedFor) return;
        // This effect re-runs on every `sources` change, which includes
        // routine background refreshes (data-source polling, the client
        // switcher, etc.) while the drawer is just sitting open - not only
        // the drawer's own first load. A transient failure on one of those
        // background refetches (a momentary Supabase blip, a slow
        // connection) used to null out an ALREADY-WORKING config and throw
        // up a red "Could not load client data sources" banner over an
        // otherwise fine, mid-conversation session. Only actually clear the
        // config (and disable the composer) when there was nothing good to
        // fall back on yet; otherwise keep using the last known-good config
        // and just log it - the next successful poll clears itself anyway.
        setConfig((current) => {
          if (current) {
            console.warn("[Braidscope] Ask AI config refresh failed, keeping last known-good config:", error.message);
            return current;
          }
          setConfigError(error.message);
          return null;
        });
      });
  }, [client, open, sources]);

  useEffect(() => {
    const providers = config?.ai_providers || [];
    if (!providers.length) return;
    setProvider((current) => (
      providers.some((entry) => entry.provider === current) ? current : providers[0].provider
    ));
    setModel((current) => current || providers[0].model || "");
  }, [config]);

  // Every connected provider's model list is loaded as soon as the drawer is
  // open (in parallel), not only the currently selected provider's - a slow
  // discovery (Claude's can take ~20s cold) then finishes in the background
  // instead of starting only when that provider is picked. Provider catalogs
  // stay strictly separate: each list is stored under its own
  // "clientId:provider" key and only ever read back for that same provider
  // (Codex's verified list must never show OpenRouter/OpenAI-API entries or
  // vice versa), so nothing from another provider or client can appear.
  const providerKey = (config?.ai_providers || []).map((entry) => entry.provider).join(",");
  useEffect(() => {
    if (!open || !client?.id || !providerKey) return undefined;
    let alive = true;
    const timers = new Set();
    const polling = new Set();
    const load = (entryProvider, attempt) => {
      const key = `${client.id}:${entryProvider}`;
      listAIProviderModels(client, entryProvider)
        .then((result) => {
          if (result.ok && (result.models || []).length) {
            setModelsByProvider((current) => ({ ...current, [key]: result.models }));
            // An earlier empty answer removed this mark; once models exist it is
            // "loaded" again (so switching clients back and forth re-asks nothing).
            modelsRequestedRef.current.add(key);
          } else {
            // Nothing usable yet (e.g. discovery still warming up) - allow the
            // next open / provider change to try again.
            modelsRequestedRef.current.delete(key);
          }
          // Codex verifies its models in the background on the server and says
          // so with `loading` (the request itself returns immediately). Keep
          // asking, gently and for a bounded time, so the list fills in on its
          // own - including partial lists that grow as models are verified.
          if (alive && result.ok && result.loading && attempt < MODELS_POLL_MAX_ATTEMPTS) {
            polling.add(key);
            const timer = window.setTimeout(() => {
              timers.delete(timer);
              if (alive) load(entryProvider, attempt + 1);
            }, MODELS_POLL_MS);
            timers.add(timer);
          } else {
            polling.delete(key);
          }
        })
        .catch(() => { modelsRequestedRef.current.delete(key); polling.delete(key); });
    };
    providerKey.split(",").forEach((entryProvider) => {
      const key = `${client.id}:${entryProvider}`;
      if (modelsRequestedRef.current.has(key)) return;
      modelsRequestedRef.current.add(key);
      load(entryProvider, 0);
    });
    return () => {
      alive = false;
      timers.forEach((timer) => window.clearTimeout(timer));
      // A poll interrupted mid-way (drawer closed, client switched) must be
      // resumable on the next open instead of being treated as "already loaded".
      polling.forEach((key) => modelsRequestedRef.current.delete(key));
    };
  }, [open, client?.id, providerKey]);

  const modelDisplay = provider === "claude_code"
    ? (modelOptions.find((entry) => entry.id === model)?.name || model)
    : model;

  // Presentation-only helpers for the model selector: close with a short exit
  // transition, keyboard navigation and focus handling. None of these touch
  // provider/model state - selection still goes through setProvider/setModel.
  const closePop = () => {
    if (!modelOpen || popClosing) return;
    setPopClosing(true);
    window.setTimeout(() => {
      setModelOpen(false);
      setPopClosing(false);
    }, 120);
  };

  useEffect(() => {
    if (!modelOpen) return;
    const selected = popRef.current?.querySelector('[data-pop-option][aria-selected="true"]')
      || popRef.current?.querySelector("[data-pop-option]");
    selected?.focus({ preventScroll: false });
  }, [modelOpen]);

  const handlePopKeyDown = (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      closePop();
      triggerRef.current?.focus();
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const options = Array.from(popRef.current?.querySelectorAll("[data-pop-option]") || []);
    if (!options.length) return;
    event.preventDefault();
    const at = options.indexOf(document.activeElement);
    const next = event.key === "ArrowDown"
      ? options[(at + 1) % options.length]
      : options[(at - 1 + options.length) % options.length];
    next.focus();
  };

  const modelNameFor = (providerId, modelId) => {
    if (!modelId) return "";
    if (providerId !== "claude_code") return modelId;
    const options = modelsByProvider[`${client?.id}:${providerId}`] || [];
    return options.find((entry) => entry.id === modelId)?.name || modelId;
  };

  const contextChips = useMemo(() => {
    const active = new Set(config?.active_integrations || []);
    const connected = (sources || []).filter((source) => source.connected);
    return connected.map((source) => ({
      type: source.type,
      name: label(source.type),
      live: active.has(source.type),
      source,
    }));
  }, [config, sources]);

  // The context row shows connected DATA SOURCES only. Provider connections
  // ("*_ai") still live in `sources`/`config` and feed the model selector.
  const dataSourceChips = contextChips.filter((chip) => !isProviderType(chip.type));

  /* ---------------- sending ---------------- */

  const send = async (text) => {
    const questionText = (text ?? draft).trim();
    if (!questionText || loading) return;

    setDraft("");
    setLoading(true);
    setTurns((current) => [
      ...current,
      { role: "user", text: questionText },
      { role: "ai", text: "", tools: [], question: null, error: "" },
    ]);

    // Index of the assistant turn we just appended.
    const replyAt = turns.length + 1;
    const patch = (updater) => setTurns((current) => current.map((turn, index) => (
      index === replyAt ? updater(turn) : turn
    )));

    try {
      await streamChat({
        question: questionText,
        client,
        sources,
        aiProvider: provider,
        aiModel: model,
        onDelta: (delta) => patch((turn) => ({ ...turn, text: turn.text + delta })),
        onTool: (tool) => patch((turn) => ({ ...turn, tools: [...turn.tools, tool] })),
        onQuestion: (payload) => patch((turn) => ({ ...turn, question: payload })),
      });
      patch((turn) => (
        turn.text.trim() || turn.question
          ? turn
          : { ...turn, text: "No answer was generated. Try a narrower question." }
      ));
    } catch (error) {
      patch((turn) => ({ ...turn, error: error.message }));
    } finally {
      setLoading(false);
    }
  };

  const answerQuestion = (turnIndex, answer) => {
    setTurns((current) => current.map((turn, index) => (
      index === turnIndex ? { ...turn, questionAnswered: true } : turn
    )));
    send(answer);
  };

  /* ---------------- provider setup ---------------- */

  const beginSetup = (id) => {
    setSetupProvider(id);
    setSetupKey("");
    setSetupModel(DEFAULT_MODELS[id] || "");
    setSetupError("");
    setClaudeAwaitingCode(false);
  };

  const completeSetup = async () => {
    setSetupSaving(true);
    setSetupError("");

    if (setupProvider === "openrouter") {
      const result = await startOpenRouterOAuth(client, setupModel || DEFAULT_MODELS.openrouter);
      if (!result.ok) {
        setSetupError(result.error || "Could not start OpenRouter OAuth");
        setSetupSaving(false);
      }
      return;
    }
    if (setupProvider === "openai") {
      const result = await startOpenAICodexAuth(client, setupModel || DEFAULT_MODELS.openai, { newTab: true });
      setSetupSaving(false);
      if (!result.ok) {
        setSetupError(result.error || "Could not start ChatGPT authentication");
        return;
      }
      setSetupProvider(null);
      return;
    }

    if (setupProvider === "anthropic" && !SHOW_CLAUDE_BYOK) {
      if (!claudeAwaitingCode) {
        const started = await startClaudeCodeAuth(client, { newTab: true });
        setSetupSaving(false);
        if (!started.ok) {
          setSetupError(started.error || "Could not start Claude sign-in");
          return;
        }
        setClaudeAwaitingCode(true);
        setSetupKey("");
        return;
      }
      const finished = await submitClaudeCodeCode(client, setupKey);
      setSetupSaving(false);
      if (!finished.ok) {
        setSetupError(finished.error || "Could not finish Claude sign-in");
        setClaudeAwaitingCode(false);
        setSetupKey("");
        return;
      }
      setSetupProvider(null);
      setSetupKey("");
      setClaudeAwaitingCode(false);
      const nextConfig = await getChatConfig(client, sources).catch(() => null);
      if (nextConfig) setConfig(nextConfig);
      return;
    }

    const result = await saveBYOKAIProvider(client, setupProvider, setupKey, setupModel);
    setSetupSaving(false);
    if (!result.ok) { setSetupError(result.error || "Could not save the key"); return; }

    setSetupProvider(null);
    setSetupKey("");
    const next = await getChatConfig(client, sources).catch(() => null);
    if (next) setConfig(next);
  };

  /* ---------------- render ---------------- */

  return (
    <aside className={`aid${open ? " open" : ""}`} aria-hidden={!open}>
      <header className="aid-head">
        <span className="aid-spark"><i className="ti ti-sparkles" /></span>
        <div className="aid-title-wrap">
          <div className="aid-title">Ask AI</div>
          <div className="aid-title-sub">{client?.name || "No client selected"}</div>
        </div>
        <div className="aid-head-btns">
          <button className="aid-ico" title="New conversation" aria-label="New conversation" onClick={() => setTurns([])}>
            <i className="ti ti-plus" />
          </button>
          <button className="aid-ico" title="Close" aria-label="Close Ask AI" onClick={onClose}><i className="ti ti-x" /></button>
        </div>
      </header>

      {dataSourceChips.length > 0 && !needsProvider && <ContextRow chips={dataSourceChips} />}

      {configError && <div className="aid-alert error"><i className="ti ti-alert-triangle" />{configError}</div>}

      {config && !config.ready && !needsProvider && (
        <div className="aid-alert">
          <i className="ti ti-plug-off" />
          {config.missing_integrations?.length
            ? `Add credentials for ${config.missing_integrations.map(label).join(", ")}.`
            : "Connect Google Ads, Klaviyo or HubSpot to use Ask AI."}
        </div>
      )}

      <div className="aid-body" ref={bodyRef}>
        {/* ---- provider setup takes over only when nothing is connected ---- */}
        {needsProvider ? (
          <div className="aid-setup">
            <div className="aid-hero">
              <span className="aid-hero-orb key"><i className="ti ti-lock" /></span>
              <div className="aid-hero-t">Pick a model to get started</div>
              <div className="aid-hero-s">Ask AI needs one provider for this client. Takes about a minute.</div>
            </div>

            {setupError && <div className="aid-alert error"><i className="ti ti-alert-triangle" />{setupError}</div>}

            {PROVIDER_SETUP.map((entry) => (
              <div key={entry.id} className="aid-prov-wrap">
                <button className="aid-prov" onClick={() => beginSetup(setupProvider === entry.id ? null : entry.id)}>
                  <span className="aid-prov-logo"><ProviderLogo provider={entry.id} sources={sources} size="lg" /></span>
                  <span>
                    <span className="aid-prov-n">{entry.name}</span>
                    <span className="aid-prov-d">{entry.desc}</span>
                  </span>
                  <span className="aid-prov-tag">{entry.tag}</span>
                </button>

                {setupProvider === entry.id && (
                  <div className="aid-prov-form">
                    {entry.id === "anthropic" && (SHOW_CLAUDE_BYOK || claudeAwaitingCode) && (
                      <label className="aid-field">
                        <span>{SHOW_CLAUDE_BYOK ? "API key" : "Code from Claude"}</span>
                        <input
                          type="password"
                          value={setupKey}
                          placeholder={SHOW_CLAUDE_BYOK ? "sk-ant-..." : "Paste the code Claude shows you"}
                          onChange={(event) => setSetupKey(event.target.value)}
                        />
                      </label>
                    )}
                    {!(entry.id === "anthropic" && !SHOW_CLAUDE_BYOK) && (
                    <label className="aid-field">
                      <span>Default model</span>
                      <input
                        value={setupModel}
                        onChange={(event) => setSetupModel(event.target.value)}
                        placeholder={
                          entry.id === "openai"
                            ? "Detected automatically after connecting"
                            : DEFAULT_MODELS[entry.id]
                        }
                      />
                    </label>
                    )}
                    <button
                      className="aid-prov-go"
                      onClick={completeSetup}
                      disabled={setupSaving || (entry.id === "anthropic" && (SHOW_CLAUDE_BYOK ? setupKey.trim().length < 12 : claudeAwaitingCode && setupKey.trim().length < 8))}
                    >
                      {setupSaving
                        ? "Connecting..."
                        : entry.id === "openrouter"
                          ? "Continue with OpenRouter"
                          : entry.id === "openai"
                            ? "Connect with ChatGPT"
                            : entry.id === "anthropic" && !SHOW_CLAUDE_BYOK
                              ? (claudeAwaitingCode ? "Finish sign-in" : "Sign in with Claude")
                              : `Save ${entry.name} key`}
                    </button>
                  </div>
                )}
              </div>
            ))}

            <div className="aid-note">
              <i className="ti ti-info-circle" />
              Keys are stored per client on the backend and never touch the browser. You can also
              manage them from Integrations.
            </div>
          </div>
        ) : turns.length === 0 ? (
          /* ---- first run ---- */
          <div className="aid-empty">
            <div className="aid-hero">
              <span className="aid-hero-orb"><i className="ti ti-sparkles" /></span>
              <div className="aid-hero-copy">
                <div className="aid-hero-t">What should we look at?</div>
                <div className="aid-hero-s">
                  I can read {client?.name || "this client"}&apos;s connected data.
                </div>
              </div>
            </div>
          </div>
        ) : (
          /* ---- conversation ---- */
          turns.map((turn, index) => (
            turn.role === "user" ? (
              <div key={index} className="aid-turn-user"><div className="aid-bubble">{turn.text}</div></div>
            ) : (
              <div key={index} className="aid-turn-ai">
                <div className="aid-byline">
                  <ProviderLogo provider={provider} sources={sources} size="xs" />
                  {providerLabel(provider)}{modelDisplay ? ` · ${modelDisplay}` : ""}
                </div>

                {turn.tools.length > 0 && (
                  <div className="aid-trace">
                    {turn.tools.map((tool, toolIndex) => (
                      <div key={toolIndex} className={`aid-trace-row${tool.status === "failed" ? " failed" : ""}`}>
                        <span className="aid-trace-ico">
                          <i className={`ti ti-${tool.status === "failed" ? "alert-triangle" : "database"}`} />
                        </span>
                        <span className="aid-trace-name">{tool.server}</span>
                        <span className="aid-trace-arg">{tool.name}</span>
                        <i className={`ti ti-${tool.status === "failed" ? "x" : "check"} aid-trace-state`} />
                      </div>
                    ))}
                  </div>
                )}

                {turn.text && <AnswerBody text={turn.text} />}

                {turn.question && (
                  <QuestionCard
                    question={turn.question}
                    answered={Boolean(turn.questionAnswered)}
                    onAnswer={(answer) => answerQuestion(index, answer)}
                  />
                )}

                {turn.error && (
                  <div className="aid-alert error"><i className="ti ti-alert-triangle" />{turn.error}</div>
                )}

                {loading && index === turns.length - 1 && !turn.text && !turn.question && (
                  <div className="aid-streaming"><span className="aid-orb" />Working…</div>
                )}
              </div>
            )
          ))
        )}
      </div>

      {!needsProvider && (
        <div className="aid-foot">
          <div className="aid-suggest-block">
            <RotatingSuggestion
              questions={SUGGESTIONS}
              onPick={(question) => send(question)}
              disabled={loading || !ready}
            />
          </div>

          <div className={`aid-composer${ready ? "" : " is-disabled"}`}>
            <textarea
              rows={2}
              value={draft}
              placeholder={ready ? `Ask about ${client?.name || "this client"}'s data…` : "Connect a source to start asking…"}
              disabled={loading || !ready}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  send();
                }
              }}
            />
            <div className="aid-composer-bar">
              <div className="aid-model-wrap" onKeyDown={modelOpen ? handlePopKeyDown : undefined}>
                <button
                  ref={triggerRef}
                  className={`aid-model-chip${modelOpen ? " is-open" : ""}`}
                  onClick={() => (modelOpen ? closePop() : setModelOpen(true))}
                  disabled={!config?.openai_configured}
                  aria-haspopup="listbox"
                  aria-expanded={modelOpen}
                  aria-label={`${providerLabel(provider)} model: ${modelDisplay || "default"}. Change model`}
                  title={`${providerLabel(provider)} · ${modelDisplay || "default"}`}
                >
                  {provider ? (
                    <ProviderLogo provider={provider} sources={sources} size="md" />
                  ) : (
                    <i className="ti ti-plug-off aid-pmark-none" aria-hidden="true" />
                  )}
                  <span className="aid-model-name">{modelDisplay || providerLabel(provider)}</span>
                  <i className="ti ti-chevron-down aid-model-caret" />
                </button>

                {modelOpen && (
                  <>
                    <div className="aid-pop-scrim" onClick={closePop} />
                    <div className={`aid-pop${popClosing ? " is-closing" : ""}`} ref={popRef} role="dialog" aria-label="Choose a model">
                      <div className="aid-pop-head">
                        <span className="aid-pop-title">Model</span>
                        <span className="aid-pop-sub">
                          {(config?.ai_providers || []).length <= 1 && (
                            <ProviderLogo provider={provider} sources={sources} size="xs" />
                          )}
                          {providerLabel(provider)}
                        </span>
                      </div>

                      {(config?.ai_providers || []).length > 1 && (
                        <div className="aid-pop-tabs" role="group" aria-label="Provider">
                          {(config?.ai_providers || []).map((entry) => (
                            <button
                              key={entry.provider}
                              type="button"
                              className={`aid-pop-tab${entry.provider === provider ? " active" : ""}`}
                              aria-pressed={entry.provider === provider}
                              onClick={() => { setProvider(entry.provider); setModel(entry.model || ""); }}
                              title={`${providerLabel(entry.provider)} · ${modelNameFor(entry.provider, entry.model) || "default"}`}
                            >
                              <ProviderLogo provider={entry.provider} sources={sources} size="sm" />
                              <span className="aid-pop-tab-name">
                                {providerLabel(entry.provider)}
                                {/* Only when two connections share a name (CLI sign-in + legacy API key). */}
                                {(entry.provider === "openai" || entry.provider === "anthropic")
                                  && (config?.ai_providers || []).some((other) => other.provider !== entry.provider && providerLabel(other.provider) === providerLabel(entry.provider))
                                  ? " · API key" : ""}
                              </span>
                            </button>
                          ))}
                        </div>
                      )}

                      <div className="aid-pop-models" role="listbox" aria-label={`Models for ${providerLabel(provider)}`}>
                        {model && !modelOptions.some((entry) => entry.id === model) && (
                          <button
                            type="button"
                            role="option"
                            aria-selected="true"
                            data-pop-option
                            className="aid-opt is-selected"
                            onClick={closePop}
                            title={modelDisplay || model}
                          >
                            <span className="aid-opt-main">
                              <span className="aid-opt-name">{modelDisplay || model}</span>
                            </span>
                            <i className="ti ti-check aid-opt-check" />
                          </button>
                        )}
                        {modelOptions.map((entry) => {
                          const selected = entry.id === model;
                          const isClaude = provider === "claude_code";
                          return (
                            <button
                              key={entry.id}
                              type="button"
                              role="option"
                              aria-selected={selected}
                              data-pop-option
                              className={`aid-opt${selected ? " is-selected" : ""}`}
                              onClick={() => { setModel(entry.id); closePop(); }}
                              title={modelOptionLabel(provider, entry)}
                            >
                              <span className="aid-opt-main">
                                <span className={`aid-opt-name${isClaude ? "" : " mono"}`}>
                                  {isClaude ? (entry.name || entry.id) : entry.id}
                                </span>
                                {isClaude && (entry.description || entry.requires_credits) && (
                                  <span className="aid-opt-desc">
                                    {entry.requires_credits ? "Requires usage credits" : entry.description}
                                  </span>
                                )}
                              </span>
                              {entry.default && <span className="aid-opt-badge">Default</span>}
                              <i className="ti ti-check aid-opt-check" />
                            </button>
                          );
                        })}
                        {modelOptions.length === 0 && !model && (
                          <div className="aid-pop-empty">Models appear here once this provider finishes loading.</div>
                        )}
                      </div>

                      <div className="aid-pop-foot">Provider keys live in Integrations</div>
                    </div>
                  </>
                )}
              </div>

              <button
                className="aid-send"
                onClick={() => send()}
                disabled={loading || !ready || !draft.trim()}
                aria-label="Send"
                title="Send"
              >
                <i className="ti ti-send" />
              </button>
            </div>
          </div>
          <div className="aid-foot-hint"><kbd>↵</kbd> send · <kbd>⇧↵</kbd> new line</div>
        </div>
      )}
    </aside>
  );
}
