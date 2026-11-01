// SPDX-License-Identifier: AGPL-3.0-only
// After a deploy, a tab that was open before it still references the old
// hashed chunk files, which no longer exist on the server. Any lazy import
// then fails. The fix is a one-time reload, which fetches the new index.

const RELOAD_FLAG = "braidscope:chunk-reload";

export function isChunkLoadError(error) {
  const text = String(error?.message || error || "");
  return /dynamically imported module|Importing a module script failed|Loading chunk|Loading CSS chunk|error loading dynamically imported module/i.test(text);
}

// Reloads at most once per failure cycle: a second failure right after a
// reload means the problem is not a stale tab, so the error is shown instead.
export function reloadForNewVersion() {
  try {
    if (sessionStorage.getItem(RELOAD_FLAG)) return false;
    sessionStorage.setItem(RELOAD_FLAG, String(Date.now()));
  } catch {
    // Storage blocked: still try the reload once.
  }
  window.location.reload();
  return true;
}

export function installChunkReload() {
  window.addEventListener("vite:preloadError", (event) => {
    if (reloadForNewVersion()) event.preventDefault();
  });
  window.addEventListener("load", () => {
    try {
      // The new index loaded fine; a later deploy may reload again.
      window.setTimeout(() => sessionStorage.removeItem(RELOAD_FLAG), 10000);
    } catch {
      // ignore
    }
  });
}

export const NEW_VERSION_MESSAGE = "Braidscope was just updated. Reload the page and try again.";
