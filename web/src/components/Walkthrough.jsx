// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useRef } from "react";
import { driver } from "driver.js";
import "driver.js/dist/driver.css";

const STEPS = [
  {
    element: "#nav-workspace",
    popover: {
      title: "Workspace",
      description:
        "Your command center. Get a bird's-eye view of every client, their connected sources, and key performance metrics, all in one glance. Switch between client workspaces from the top bar.",
      side: "right",
      align: "start",
    },
  },
  {
    element: "#nav-dashboards",
    popover: {
      title: "Dashboards",
      description:
        "Live, auto-updating dashboards for Google Analytics, Google Ads, Meta Ads, HubSpot, and Klaviyo. Filter by date range, drill into campaigns, and spot trends without leaving the platform.",
      side: "right",
      align: "start",
    },
  },
  {
    element: "#nav-reports",
    popover: {
      title: "Reports",
      description:
        "Build client-ready PDF reports from your connected data. AI drafts the commentary from your actual numbers, you review and edit it, then export a PDF with your branding.",
      side: "right",
      align: "start",
    },
  },
  {
    element: "#nav-integrations",
    popover: {
      title: "Integrations",
      description:
        "Connect data sources with one click: Google Ads, Meta, GA4, Shopify, HubSpot, Klaviyo, and more. Each connector syncs automatically so your dashboards always reflect the latest data.",
      side: "right",
      align: "start",
    },
  },
  {
    element: "#nav-settings",
    popover: {
      title: "Settings",
      description:
        "Manage your account, invite team members, handle billing, and configure security preferences. Everything you need to keep your workspace running smoothly.",
      side: "right",
      align: "start",
    },
  },
  {
    element: "#nav-profile",
    popover: {
      title: "Your Profile",
      description:
        "Access your account, switch to branding settings, manage data sources, or log out. Your workspace identity lives here.",
      side: "bottom",
      align: "end",
    },
  },
];

export default function Walkthrough({ active, completed, onFinish, onCancel }) {
  const driverRef = useRef(null);
  // Distinguishes "user clicked Finish on the last step" from every other
  // way the tour can end (Close button, Escape, overlay click) so onFinish
  // (which also navigates to Workspace) only fires for a genuine finish,
  // never for a cancel.
  const finishedRef = useRef(false);
  // Set right before *we* call driver.destroy() during our own effect
  // cleanup (dep change or unmount) - onDestroyed fires either way, but a
  // React-initiated destroy must never count as the user closing/finishing
  // the tour, or it would end the tour on route changes, background state
  // refreshes, or any other unrelated re-render.
  const cleaningUpRef = useRef(false);

  useEffect(() => {
    if (!active) return undefined;
    if (completed) return undefined;

    const timeout = setTimeout(() => {
      const d = driver({
        showProgress: true,
        animate: true,
        overlayColor: "rgba(0, 0, 0, 0.6)",
        stagePadding: 10,
        stageRadius: 14,
        popoverOffset: 16,
        popoverClass: "bs-walkthrough",
        nextBtnText: "Next",
        prevBtnText: "Back",
        doneBtnText: "Finish",
        progressText: "Step {{current}} of {{total}}",
        steps: STEPS,
        // driver.js renders the progress text inside the footer by default;
        // moving it to sit in normal flow just above the title (a small
        // "kicker" label) instead of absolute-positioning a pill over the
        // card is what lets the title/description sit wherever they
        // naturally need to regardless of step length, rather than every
        // step needing hand-tuned clearance margins. Swapping the close
        // button's default "&times;" for the app's own Tabler icon keeps it
        // visually consistent with every other dismiss control in the app.
        onPopoverRender: (popover) => {
          popover.closeButton.innerHTML = '<i class="ti ti-x" aria-hidden="true"></i>';
          if (popover.progress && popover.title && popover.progress.nextSibling !== popover.title) {
            popover.title.parentElement.insertBefore(popover.progress, popover.title);
          }
        },
        onDoneClick: (_element, _step, opts) => {
          finishedRef.current = true;
          opts.driver.destroy();
        },
        onDestroyed: () => {
          if (cleaningUpRef.current) return;
          if (finishedRef.current) onFinish?.();
          else onCancel?.();
          finishedRef.current = false;
        },
      });

      driverRef.current = d;
      d.drive();
    }, 800);

    return () => {
      clearTimeout(timeout);
      if (driverRef.current) {
        cleaningUpRef.current = true;
        driverRef.current.destroy();
        driverRef.current = null;
        cleaningUpRef.current = false;
      }
    };
  }, [active, completed, onFinish, onCancel]);

  return null;
}
