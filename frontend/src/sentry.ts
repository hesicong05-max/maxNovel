/**
 * Sentry initialization for React frontend.
 *
 * If VITE_SENTRY_DSN is not set, Sentry is not initialized (no-op).
 * This allows development without a Sentry account.
 */

import * as Sentry from "@sentry/react";

const SENTRY_DSN = import.meta.env.VITE_SENTRY_DSN as string | undefined;

export function initSentry(): boolean {
  if (!SENTRY_DSN) {
    // eslint-disable-next-line no-console
    console.info("[Sentry] DSN not set — error monitoring disabled");
    return false;
  }

  Sentry.init({
    dsn: SENTRY_DSN,
    integrations: [
      Sentry.browserTracingIntegration(),
      Sentry.replayIntegration({
        maskAllText: false,
        blockAllMedia: false,
      }),
    ],
    // Performance Monitoring: capture 10% of transactions
    tracesSampleRate: 0.1,
    // Session Replay: capture 10% of sessions
    replaysSessionSampleRate: 0.1,
    // Session Replay: capture 100% of sessions with errors
    replaysOnErrorSampleRate: 1.0,
  });

  // eslint-disable-next-line no-console
  console.info("[Sentry] Initialized successfully");
  return true;
}
