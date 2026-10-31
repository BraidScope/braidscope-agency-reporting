// SPDX-License-Identifier: AGPL-3.0-only
import { Component } from "react";

// Keeps a Reports failure inside Reports: a render error here shows a local
// message instead of taking down the rest of the app.
export default class ReportsErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error, info) {
    console.error("[Reports] render failed", error, info);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="rp-card rp-empty-state" role="alert">
        <div className="rp-empty-icon" aria-hidden="true"><i className="ti ti-alert-triangle" /></div>
        <h2>Reports hit a problem</h2>
        <p>Nothing else in the app is affected, and anything you saved is safe. Try reloading this section.</p>
        <button type="button" className="primary-btn" onClick={() => this.setState({ failed: false })}>Reload Reports</button>
      </div>
    );
  }
}
