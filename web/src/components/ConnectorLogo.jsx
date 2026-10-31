// SPDX-License-Identifier: AGPL-3.0-only
import { connectorFaviconUrl, connectorLogoUrl } from "../lib/data";

export default function ConnectorLogo({ source, className = "" }) {
  // A bundled local asset (logoAsset) takes priority over the CDN-hosted
  // Simple Icons slug — used when a brand's mark isn't in the pinned CDN
  // release (see OpenAI in lib/data.js).
  const maskUrl = source.logoAsset || connectorLogoUrl(source.logoSlug);
  const faviconUrl = (source.logoAsset || source.logoSlug) ? null : connectorFaviconUrl(source.logoDomain);

  return (
    <div className={`conn-logo${className ? ` ${className}` : ""}`} aria-hidden="true">
      {maskUrl ? (
        <span
          className="conn-logo-mark"
          style={{ "--logo-url": `url("${maskUrl}")`, "--logo-color": source.bg }}
        />
      ) : faviconUrl ? (
        <img className="conn-logo-img" src={faviconUrl} alt="" />
      ) : (
        <span className="conn-logo-fallback" style={{ color: source.bg }}>{source.l}</span>
      )}
    </div>
  );
}
