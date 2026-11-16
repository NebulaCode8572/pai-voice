# VPS deployment (voice-only first release)

This deployment profile runs the Python WebSocket core as a long-lived service. It deliberately disables video, screen sharing, and vision features. The App can provide character replies using its existing browser-side chat engine; an optional server-side Adapter remains available as a fallback.

## Runtime requirements

- Linux server with Python 3.11+ (the Dockerfile uses Python 3.12).
- Install the voice-only dependencies from `requirements-voice.txt`.
- The service binds to `127.0.0.1:8780` by default. Put Nginx or another TLS reverse proxy in front of it and expose only HTTPS/WSS publicly.
- `PORT` is supported as a platform fallback; `PAIVOICE_PORT` takes precedence.
- `/healthz` returns HTTP 200 for process health checks.

Do not expose the raw Python port directly to the public Internet. Production WebSocket traffic must use TLS (`wss://`).

## Authentication model

Production requires `PAIVOICE_AUTH_SECRET` (at least 32 bytes). Generate a value with `openssl rand -hex 32`; never use the example placeholder or commit the generated value. The secret is shared only between PaiVoice and a trusted server-side component in your App. Never put it in browser JavaScript, PWA configuration, or a mobile bundle.

Before opening each WebSocket connection, the App backend issues a short-lived ticket and returns it to the authenticated user. The browser passes that ticket in the first `start` message:

```json
{"type":"start","ticket":"<short-lived-ticket>","video":false,"sample_rate":16000}
```

Ticket format is `base64url(payload).base64url(HMAC-SHA256(payload))`. Payload fields:

- `sub`: authenticated user identifier.
- `aud`: exactly `paivoice`.
- `iat`: issued-at Unix timestamp (seconds).
- `exp`: expiry Unix timestamp (seconds), no more than 300 seconds after `iat`.
- `jti`: unique random identifier; each ticket can be used once.

Example Node.js code for the trusted App backend (not browser code):

```js
import { createHmac, randomUUID } from "node:crypto";

export function issuePaiVoiceTicket(userId) {
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    sub: userId,
    aud: "paivoice",
    iat: now,
    exp: now + 120,
    jti: randomUUID(),
  };
  const encoded = Buffer.from(JSON.stringify(payload)).toString("base64url");
  const signature = createHmac("sha256", process.env.PAIVOICE_AUTH_SECRET)
    .update(encoded)
    .digest("base64url");
  return encoded + "." + signature;
}
```

The browser-side `VoiceCall` receives a `getAuthTicket` callback. It calls the callback for the initial connection and again for every reconnect, so a fresh one-use ticket is used each time:

```js
const call = new VoiceCall({
  url: "wss://pai.example.com/",
  video: false,
  getAuthTicket: async () => {
    const response = await fetch("/api/paivoice/ticket", {
      method: "POST",
      credentials: "include",
    });
    if (!response.ok) throw new Error("Unable to authorize PaiVoice call");
    return (await response.json()).ticket;
  },
});
```

The App endpoint shown above is an integration contract/example; it must authenticate the current user, apply call authorization/rate limits, and sign tickets on the server. It is not implemented in this PaiVoice repository.

On reconnect, the ticket subject must match the original call's user. The server rejects reused, expired, incorrectly signed, or incorrectly scoped tickets. Tickets are intentionally short-lived and single-use.

For isolated local development only, set `PAIVOICE_ALLOW_INSECURE_LOCAL=1` with `PAIVOICE_HOST=127.0.0.1` and leave `PAIVOICE_AUTH_SECRET` unset. The server refuses this mode on a non-loopback bind. Never use this setting on a public server.

The service enforces one active call per authenticated account. A reconnect that presents the existing call ID resumes that call; opening a second fresh call for the same account is rejected.

## Browser-side App reply flow

The App integration can generate character replies in the browser, so the user's existing character settings and provider credentials do not need to be copied to the PaiVoice VPS.

After ASR, the core sends a `transcript` event containing `turn_id` and `text`, then waits up to 120 seconds for a matching message:

```json
{"type":"client_reply","turn_id":"<turn-id-from-transcript>","text":"<generated-character-reply>"}
```

The core returns the reply text and synthesized audio over the same WebSocket. If the browser reply times out and `PAIVOICE_ADAPTER_URL` is configured, the core falls back to that server-side Adapter. Interrupting a turn cancels its pending browser reply so an outdated response is not spoken after the user starts a new turn.

The browser must only receive short-lived tickets. The HMAC signing secret remains on the trusted App server and the PaiVoice service.

## Environment and providers

Copy `.env.example` to a server-only environment file. Do not commit real values.

- Start with `PAIVOICE_ASR_PROVIDER=mock` and `PAIVOICE_TTS_PROVIDER=mock` to verify transport and protocol. Mock ASR intentionally returns no transcript and mock TTS produces no audio.
- Configure Groq ASR and ElevenLabs TTS only after transport tests pass.
- Keep the Char adapter URL and token server-side.
- Set `PAIVOICE_ALLOWED_ORIGINS` to the exact browser origins used by the App. Leave no wildcard. Native WebView origins must be confirmed during device integration before tightening this list.

## systemd + Nginx

Example files:

- `deploy/paivoice.service`: run the process as an unprivileged `paivoice` account and restart it on failure.
- `deploy/nginx-paivoice.conf`: TLS reverse proxy and WebSocket upgrade headers. Replace the sample domain and certificate paths before use.

Create the service account and Python environment, install `requirements-voice.txt`, place the repository at `/opt/paivoice`, create `/etc/paivoice/paivoice.env` with mode 600, then install the systemd unit. Review every path and ownership before enabling it.

## Health and operational checks

- `GET /healthz` should return `200 OK`.
- WebSocket clients connect with `wss://`, not `ws://`, in production.
- Monitor service logs for startup failures and provider errors. Do not log ticket values, API keys, raw audio, or transcripts.
- The in-memory call registry and one-use ticket cache are process-local. Run one service process for this first deployment; horizontal/multi-process scaling requires shared call state and replay protection.
- Back up configuration separately; never put secrets in Git.

## License notice

This repository is licensed under AGPL-3.0. If you modify PaiVoice and let users interact with that modified version remotely over a network, AGPL-3.0 section 13 requires a prominent, no-charge way for those users to obtain the corresponding source code. Keep the license and copyright notices, and add a source-code link/offer in the user-facing App before public rollout. This is a compliance note, not legal advice.

## First-release scope

`PAIVOICE_ENABLE_VIDEO=false` by default. Video, camera frames, screen sharing, companion mode, and vision calls are rejected/ignored until explicitly enabled in a later release. This keeps the first deployment focused on voice transport and reduces unused dependencies.
