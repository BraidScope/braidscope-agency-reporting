// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useState } from "react";
import { useUnsavedChanges } from "../lib/UnsavedChangesContext.jsx";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import ConnectorLogo from "./ConnectorLogo";
import "../styles/connect-modal.css";

const OPENROUTER_MODELS = [
  "moonshotai/kimi-k2.6:free",
  "openai/gpt-4.1-mini",
  "anthropic/claude-sonnet-4",
  "google/gemini-2.5-pro",
];

const OPENAI_MODELS = ["gpt-4.1-mini", "gpt-4.1", "gpt-5"];
const ANTHROPIC_MODELS = [
  "claude-sonnet-4-20250514",
  "claude-opus-4-20250514",
  "claude-3-5-haiku-latest",
];

function PlanCard({ icon, title, children }) {
  return (
    <Card className="bs-plan-card">
      <CardHeader className="bs-card-header">
        <CardTitle className="bs-card-title">
          <i className={`ti ${icon}`} />
          {title}
        </CardTitle>
        <CardDescription className="bs-card-desc">
          {children}
        </CardDescription>
      </CardHeader>
    </Card>
  );
}

function Field({ label, children }) {
  return (
    <label className="bs-field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function ConnectorOptionCard({ icon, title, description, badge, children }) {
  return (
    <Card className="bs-option-card">
      <CardHeader className="bs-card-header">
        <div className="bs-option-head">
          <div>
            <CardTitle className="bs-card-title">
              <i className={`ti ${icon}`} />
              {title}
            </CardTitle>
            <CardDescription className="bs-card-desc">
              {description}
            </CardDescription>
          </div>
          {badge && <Badge className="bs-badge" variant="outline">{badge}</Badge>}
        </div>
      </CardHeader>
      {children && <CardContent className="bs-card-content">{children}</CardContent>}
    </Card>
  );
}

export default function ConnectModal({
  data,
  onClose,
  onSave,
  onOAuth,
  onSubmitCode,
  saving,
  error,
  cancelLabel = "Cancel",
  showCloseButton = true,
  onConnected,
  disableDismiss = false,
}) {
  const [shopDomain, setShopDomain] = useState("");
  const [openRouterModel, setOpenRouterModel] = useState("moonshotai/kimi-k2.6:free");
  const [byokApiKey, setByokApiKey] = useState("");
  const [byokModel, setByokModel] = useState("");
  const [savedSnapshot, setSavedSnapshot] = useState(null);
  // Claude sign-in is two steps: open Claude's own page, then paste the code it shows.
  const [claudeStarted, setClaudeStarted] = useState(false);
  const [claudeUrl, setClaudeUrl] = useState("");
  const [claudeCode, setClaudeCode] = useState("");
  const [claudeBusy, setClaudeBusy] = useState(false);
  const [claudeError, setClaudeError] = useState("");
  const { registerChanges, clearChanges, confirmNavigation } = useUnsavedChanges();

  useEffect(() => {
    if (!data) return;
    const initialOpenRouterModel = data.config?.model || data.defaultModel || "moonshotai/kimi-k2.6:free";
    const initialByokModel = data.config?.model || data.defaultModel || "";
    setClaudeStarted(false);
    setClaudeUrl(data.config?.authorization_url || "");
    setClaudeCode("");
    setClaudeError("");
    setShopDomain("");
    setOpenRouterModel(initialOpenRouterModel);
    setByokApiKey("");
    setByokModel(initialByokModel);
    setSavedSnapshot(JSON.stringify({ shopDomain: "", openRouterModel: initialOpenRouterModel, byokApiKey: "", byokModel: initialByokModel }));
  }, [data]);

  const currentSnapshot = JSON.stringify({ shopDomain, openRouterModel, byokApiKey, byokModel });
  const dirty = Boolean(data && savedSnapshot !== null && currentSnapshot !== savedSnapshot);

  useEffect(() => {
    if (!data) { clearChanges(); return undefined; }
    registerChanges({
      isDirty: dirty,
      onSave: async () => {
        const result = isOAuth
          ? await onOAuth({ ...data, model: oauthModel })
          : await onSave(data, { shopDomain, apiKey: byokApiKey, model: byokModel });
        return result !== false;
      },
      onDiscard: () => {},
    });
    return () => clearChanges();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, dirty, shopDomain, openRouterModel, byokApiKey, byokModel, registerChanges, clearChanges]);

  if (!data) return null;

  const isShopify = data.type === "shopify";
  const isKlaviyo = data.type === "klaviyo";
  const isHubSpot = data.type === "hubspot_crm";
  const isGoogleAds = data.type === "google_ads";
  const isMetaAds = data.type === "meta_ads";
  const isGA4 = data.type === "google_analytics_4";
  const isOpenRouter = data.type === "openrouter_ai";
  const isOpenAI = data.type === "openai_ai";
  const isAnthropic = data.type === "anthropic_ai";
  const isOpenAIChatGPT = isOpenAI && data.authType === "oauth";
  const isClaudeCode = isAnthropic && data.authType === "oauth";
  const isBYOKAI = (!isOpenAIChatGPT && !isClaudeCode && (isOpenAI || isAnthropic));
  const isOAuth = isKlaviyo || isHubSpot || isOpenRouter || isGoogleAds || isMetaAds || isGA4 || isOpenAIChatGPT || isClaudeCode;
  const claudeAwaitingCode = isClaudeCode && (claudeStarted || data.status === "pending");
  const providerName = isOpenRouter
    ? "OpenRouter"
    : isOpenAI
      ? "OpenAI"
      : isAnthropic
        ? "Claude"
        : isKlaviyo
          ? "Klaviyo"
          : isGoogleAds
            ? "Google Ads"
            : isMetaAds
              ? "Meta Ads"
              : isGA4
                ? "Google Analytics 4"
                : "HubSpot";
  const oauthModel = isOpenAIChatGPT ? (byokModel || data.defaultModel || "gpt-4.1-mini") : openRouterModel;

  const startClaudeSignIn = async () => {
    setClaudeError("");
    const result = await onOAuth({ ...data });
    if (result === false) return;
    if (result?.authorizationUrl) setClaudeUrl(result.authorizationUrl);
    setClaudeStarted(true);
  };

  const finishClaudeSignIn = async () => {
    setClaudeBusy(true);
    setClaudeError("");
    const result = await onSubmitCode?.(data, claudeCode);
    setClaudeBusy(false);
    if (!result?.ok) {
      setClaudeError(result?.error || "Could not finish Claude sign-in");
      // The CLI consumes the sign-in on a wrong code, so start over.
      setClaudeStarted(false);
      setClaudeCode("");
      return;
    }
    setClaudeCode("");
    onConnected?.(data);
    onClose();
  };

  const requestClose = () => {
    confirmNavigation(onClose);
  };

  return (
    <>
    <Dialog open={Boolean(data)} onOpenChange={(open) => { if (!open && !disableDismiss) requestClose(); }}>
      <DialogContent
        className="bs-connect-dialog"
        showCloseButton={showCloseButton}
        onEscapeKeyDown={disableDismiss ? (event) => event.preventDefault() : undefined}
        onPointerDownOutside={disableDismiss ? (event) => event.preventDefault() : undefined}
      >
        <DialogHeader className="bs-dialog-head">
          <div className="bs-dialog-title-row">
            <ConnectorLogo source={data} className="bs-dialog-logo" />
            <div className="bs-dialog-title-copy">
              <DialogTitle className="bs-dialog-title">Connect {data.name}</DialogTitle>
              <DialogDescription className="bs-dialog-desc">
                Configure access for this client workspace. Credentials stay encrypted on the backend.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className="bs-dialog-body">
          {isShopify ? (
            <>
              <PlanCard icon="ti-plug-connected" title="Shopify API connector">
                This connector uses server-side OAuth/token storage. The browser never stores Shopify access tokens.
              </PlanCard>
              <Field label="Shopify store">
                <Input
                  placeholder="your-store.myshopify.com"
                  value={shopDomain}
                  onChange={(event) => setShopDomain(event.target.value)}
                />
              </Field>
              {(data.scopes || []).length > 0 && (
                <div className="bs-scope-list">
                  {data.scopes.map((scope) => (
                    <Badge key={scope} className="bs-badge" variant="outline">{scope}</Badge>
                  ))}
                </div>
              )}
              <p className="bs-help-text">
                You will be sent to Shopify to approve access. Tokens are exchanged and encrypted by the backend.
              </p>
            </>
          ) : isClaudeCode ? (
            <>
              <PlanCard icon="ti-brand-anthropic" title="Claude · your account">
                Sign in with your own Claude account. This connection belongs to you only - teammates connect their own accounts, and Ask AI uses the account of whoever is asking.
              </PlanCard>
              {claudeAwaitingCode ? (
                <>
                  <p className="bs-help-text">
                    1. Sign in on Claude&apos;s page (it opened in a new tab).{" "}
                    {claudeUrl && (
                      <a href={claudeUrl} target="_blank" rel="noopener noreferrer">Open it again</a>
                    )}
                    <br />
                    2. Claude shows you a code. Copy it and paste it below.
                  </p>
                  <Field label="Code from Claude">
                    <Input
                      className="bs-input mono"
                      value={claudeCode}
                      onChange={(event) => setClaudeCode(event.target.value)}
                      placeholder="Paste the code here"
                      autoComplete="off"
                      spellCheck={false}
                    />
                  </Field>
                  {claudeError && <p className="bs-help-text" role="alert">{claudeError}</p>}
                  <Button
                    className="bs-primary-action"
                    onClick={finishClaudeSignIn}
                    disabled={claudeBusy || claudeCode.trim().length < 8}
                  >
                    <i className="ti ti-lock" />
                    {claudeBusy ? "Connecting..." : "Finish sign-in"}
                  </Button>
                </>
              ) : (
                <>
                  {claudeError && <p className="bs-help-text" role="alert">{claudeError}</p>}
                  <Button className="bs-primary-action" onClick={startClaudeSignIn} disabled={saving}>
                    <i className="ti ti-lock" />
                    Sign in with Claude
                  </Button>
                  <p className="bs-help-text">
                    You sign in on Anthropic&apos;s own page. Your Claude credentials are kept by the Claude Code CLI in an isolated folder on the backend and are never shown to the browser.
                  </p>
                </>
              )}
            </>
          ) : isOAuth ? (
            <>
              <PlanCard icon="ti-lock" title={`${isOpenRouter ? "OpenRouter AI" : providerName} OAuth`}>
                Sign in to {providerName} and allow access. Tokens stay on the backend and are never stored in the browser.
              </PlanCard>
              {isOpenRouter && (
                <Field label="Default AI model">
                  <Input
                    className="bs-input mono"
                    value={openRouterModel}
                    onChange={(event) => setOpenRouterModel(event.target.value)}
                    placeholder="moonshotai/kimi-k2.6:free"
                    list="openrouter-models"
                  />
                  <datalist id="openrouter-models">
                    {OPENROUTER_MODELS.map((model) => (
                      <option key={model} value={model} />
                    ))}
                  </datalist>
                </Field>
              )}
              <Button
                className="bs-primary-action"
                onClick={() => onOAuth({ ...data, model: oauthModel })}
                disabled={saving}
              >
                <i className="ti ti-lock" />
                {isOpenAIChatGPT ? "Connect with ChatGPT" : `Continue with ${providerName}`}
              </Button>
              <p className="bs-help-text">
                {isOpenRouter
                  ? "The selected model will power this client's Ask AI requests. You can reconnect later to change it."
                  : "Tokens are stored server-side per client and are never exposed to the browser."}
              </p>
            </>
          ) : isOpenAIChatGPT ? (
            <>
              <PlanCard icon="ti-brand-openai" title="OpenAI · ChatGPT">
                Use the official ChatGPT/Codex subscription flow for this client workspace. The connection stays isolated to this workspace and user context, while the legacy API-key path remains available behind the scenes.
              </PlanCard>
              <p className="bs-help-text">
                The old OpenAI API-key setup remains available for backend compatibility, but the regular user flow now routes through ChatGPT/Codex-only authentication.
              </p>
            </>
          ) : isBYOKAI ? (
            <>
              <PlanCard icon="ti-key" title={`${isOpenAI ? "OpenAI" : "Claude"} BYOK`}>
                Paste this client's own {isOpenAI ? "OpenAI" : "Anthropic"} API key. The key is encrypted on the backend and used only for this client's Ask AI.
              </PlanCard>
              <Field label="API key">
                <Input
                  className="bs-input mono"
                  type="password"
                  placeholder={isOpenAI ? "sk-..." : "sk-ant-..."}
                  value={byokApiKey}
                  onChange={(event) => setByokApiKey(event.target.value)}
                />
              </Field>
              <Field label="Default model">
                <Input
                  className="bs-input mono"
                  value={byokModel}
                  onChange={(event) => setByokModel(event.target.value)}
                  placeholder={data.defaultModel}
                  list={isOpenAI ? "openai-models" : "anthropic-models"}
                />
                <datalist id="openai-models">
                  {OPENAI_MODELS.map((model) => (
                    <option key={model} value={model} />
                  ))}
                </datalist>
                <datalist id="anthropic-models">
                  {ANTHROPIC_MODELS.map((model) => (
                    <option key={model} value={model} />
                  ))}
                </datalist>
              </Field>
              <p className="bs-help-text">
                {isOpenAI
                  ? "OpenAI BYOK supports the same Responses API MCP flow used by Ask AI."
                  : "Claude BYOK summarizes backend MCP results. For broad dynamic remote MCP tool calling, OpenAI BYOK is the safer choice."}
              </p>
            </>
          ) : (
            <>
              <ConnectorOptionCard
                icon="ti-shield-lock"
                title="OAuth connector"
                description={`${data.name} OAuth is not fully wired yet. Use this when the backend OAuth flow is added for this source.`}
                badge="Coming soon"
              >
                <Button
                  variant="outline"
                  className="bs-secondary-action"
                  onClick={() => onSave(data)}
                  disabled={saving}
                >
                  <i className="ti ti-lock" />
                  Mark as connected for demo
                </Button>
              </ConnectorOptionCard>

              <div className="bs-separator-row">
                <Separator className="bs-separator" />
                Manual key
                <Separator className="bs-separator" />
              </div>

              <ConnectorOptionCard
                icon="ti-key"
                title="API key"
                description="Use this only after the backend connector supports this source. The key will be encrypted per client."
                badge="Manual"
              >
                <Field label="API key">
                  <Input className="bs-input mono" placeholder="Paste API key" />
                </Field>
              </ConnectorOptionCard>
            </>
          )}

          {error && (
            <Card className="bs-error-card">
              <CardContent>
                {error}
              </CardContent>
            </Card>
          )}
        </div>

        <DialogFooter className="bs-dialog-foot">
          <Button className="bs-cancel-action" variant="outline" onClick={requestClose}>{cancelLabel}</Button>
          {!isOAuth && (
            <Button
              className="bs-save-action"
              onClick={async () => {
                const result = await onSave(data, { shopDomain, apiKey: byokApiKey, model: byokModel });
                if (result !== false) onConnected?.(data);
              }}
              disabled={saving}
            >
              {saving ? "Connecting..." : isShopify ? "Continue to Shopify" : isBYOKAI ? "Save key" : "Save & connect"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
    </>
  );
}
