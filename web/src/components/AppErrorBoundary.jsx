// SPDX-License-Identifier: AGPL-3.0-only
import React from "react";

export default class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("[Braidscope] Render failed", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;

    const message = this.state.error?.message || "The app hit a rendering error.";

    return (
      <main className="app-error-shell">
        <section className="app-error-card">
          <div className="app-error-icon">
            <i className="ti ti-alert-triangle" aria-hidden="true" />
          </div>
          <h1>Something broke while opening Braidscope</h1>
          <p>{message}</p>
          <div className="app-error-actions">
            <button
              type="button"
              className="btn-solid"
              onClick={() => {
                window.history.replaceState(null, "", "/workspace");
                window.location.reload();
              }}
            >
              Back to workspace
            </button>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => window.location.reload()}
            >
              Reload
            </button>
          </div>
        </section>
      </main>
    );
  }
}
