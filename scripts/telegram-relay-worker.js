// Cloudflare Worker — Telegram Bot API relay.
//
// Why: some hosts (Hugging Face Spaces, locked-down networks) black-hole direct
// outbound traffic to Telegram's IP range, so the backend's TLS handshake to
// api.telegram.org times out ("_ssl.c:999: handshake operation timed out").
// Cloudflare Workers run on 443 from Cloudflare's network (which CAN reach
// Telegram), and HF can reach *.workers.dev fine — so this relay restores
// delivery with zero servers to manage.
//
// Security: a secret path prefix keeps this from being an open Telegram proxy.
// Requests must arrive as  https://<worker>.workers.dev/<SECRET>/bot<token>/...
// Anything else gets a 403. Generate a long random SECRET (e.g. `openssl rand -hex 24`).
//
// Deploy (dashboard):
//   1. dash.cloudflare.com → Workers & Pages → Create → Worker → name it, Deploy.
//   2. Edit code → paste this file → set SECRET below → Deploy.
//   3. Copy the worker URL, e.g. https://my-relay.<acct>.workers.dev
//   4. In the trading app: Settings → Automation & Alerts →
//        "Telegram API base URL" = https://my-relay.<acct>.workers.dev/<SECRET>
//      and clear "Outbound proxy URL". Save, then Run diagnostics.
//
// Deploy (wrangler, optional): `npx wrangler deploy` with this as the entry.

const SECRET = "PUT_A_LONG_RANDOM_STRING_HERE";

const TELEGRAM = "https://api.telegram.org";

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const prefix = "/" + SECRET;

    // Require the secret prefix; reject probes / scanners otherwise.
    if (SECRET === "PUT_A_LONG_RANDOM_STRING_HERE") {
      return new Response("relay not configured: set SECRET", { status: 500 });
    }
    if (!url.pathname.startsWith(prefix + "/")) {
      return new Response("forbidden", { status: 403 });
    }

    // Strip the secret, forward the rest (…/bot<token>/sendMessage?…) upstream.
    const forwardPath = url.pathname.slice(prefix.length); // keeps leading "/"
    const target = TELEGRAM + forwardPath + url.search;

    const init = {
      method: request.method,
      headers: { "Content-Type": request.headers.get("Content-Type") || "application/json" },
    };
    if (request.method !== "GET" && request.method !== "HEAD") {
      init.body = await request.arrayBuffer();
    }

    const upstream = await fetch(target, init);
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "Content-Type": "application/json" },
    });
  },
};
