// Worker di Prague Rock: raccoglie i voti a stelle e inoltra i messaggi del form su Telegram.
//
// Variabili da impostare nel pannello Cloudflare:
//   KV binding  VOTES      namespace per il totale dei voti e i limiti per IP
//   secret      TG_TOKEN   token del bot Telegram
//   secret      TG_CHAT    id della chat dove arrivano i messaggi
//
// Endpoint:
//   GET  /stats     -> { "avg": 4.3, "n": 17 }
//   POST /rate      { "stars": 1-5 }                       -> { "avg", "n" }
//   POST /contact   { "subject", "from", "text", "lang" }  -> { "ok": true }
//   GET  /visits    -> { "count": 123 }
//   POST /visit     conta una visita nuova (max una per IP al giorno) -> { "count" }
//
// Le visite stanno qui e non solo su GoatCounter perche' Brave e gli ad-blocker di Chrome
// bloccano goatcounter.com (lista EasyPrivacy): chi lo blocca non veniva contato e non vedeva il numero.

const ORIGINS = [
  "https://praguerock.cz",
  "https://www.praguerock.cz",
  "https://bigthursday.github.io",
  "http://127.0.0.1:8777",
  "http://localhost:8777",
];

const VOTE_KEY = "rating:total";
const VISIT_KEY = "visits:total";
const DAY = 86400;

async function readVisits(env) {
  return Number(await env.VOTES.get(VISIT_KEY)) || 0;
}

function cors(origin) {
  const allow = ORIGINS.includes(origin) ? origin : ORIGINS[0];
  return {
    "access-control-allow-origin": allow,
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "content-type",
    "access-control-max-age": "86400",
    "vary": "origin",
  };
}

function json(body, origin, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...cors(origin) },
  });
}

// Una chiave per IP con scadenza: basta a fermare chi ricarica e rivota, non e' una fortezza.
async function tooOften(env, kind, ip, seconds) {
  const key = `seen:${kind}:${ip}`;
  if (await env.VOTES.get(key)) return true;
  await env.VOTES.put(key, "1", { expirationTtl: seconds });
  return false;
}

async function readTotal(env) {
  const raw = await env.VOTES.get(VOTE_KEY);
  if (!raw) return { sum: 0, n: 0 };
  try {
    const t = JSON.parse(raw);
    return { sum: Number(t.sum) || 0, n: Number(t.n) || 0 };
  } catch {
    return { sum: 0, n: 0 };
  }
}

function score(t) {
  return { avg: t.n ? Math.round((t.sum / t.n) * 100) / 100 : 0, n: t.n };
}

// Torna null se e' andata, altrimenti la spiegazione di Telegram: serve a capire
// subito se e' il token, la chat, o il bot che non e' mai stato avviato.
async function tell(env, text) {
  if (!env.TG_TOKEN) return "TG_TOKEN mancante";
  if (!env.TG_CHAT) return "TG_CHAT mancante";
  const r = await fetch(`https://api.telegram.org/bot${env.TG_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: env.TG_CHAT, text, disable_web_page_preview: true }),
  });
  if (r.ok) return null;
  let why = "HTTP " + r.status;
  try {
    const b = await r.json();
    if (b && b.description) why = b.description;
  } catch {}
  return why;
}

function clean(s, max) {
  return String(s == null ? "" : s).replace(/\s+/g, " ").trim().slice(0, max);
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("origin") || "";
    const url = new URL(request.url);
    const ip = request.headers.get("cf-connecting-ip") || "0.0.0.0";

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(origin) });

    if (url.pathname === "/stats" && request.method === "GET") {
      return json(score(await readTotal(env)), origin);
    }

    if (url.pathname === "/visits" && request.method === "GET") {
      return json({ count: await readVisits(env) }, origin);
    }

    if (url.pathname === "/visit" && request.method === "POST") {
      const count = await readVisits(env);
      if (await tooOften(env, "visit", ip, DAY)) return json({ count }, origin);
      await env.VOTES.put(VISIT_KEY, String(count + 1));
      return json({ count: count + 1 }, origin);
    }

    if (url.pathname === "/rate" && request.method === "POST") {
      let stars = 0;
      try {
        stars = Math.round(Number((await request.json()).stars));
      } catch {
        return json({ error: "bad json" }, origin, 400);
      }
      if (!(stars >= 1 && stars <= 5)) return json({ error: "stars 1-5" }, origin, 400);

      // Un voto per browser, per sempre: lo blocca la pagina (localStorage). Niente blocco per IP:
      // chi divide la connessione (ufficio, operatore mobile) non verrebbe mai contato.
      const total = await readTotal(env);
      const next = { sum: total.sum + stars, n: total.n + 1 };
      await env.VOTES.put(VOTE_KEY, JSON.stringify(next));
      return json(score(next), origin);
    }

    if (url.pathname === "/contact" && request.method === "POST") {
      let body;
      try {
        body = await request.json();
      } catch {
        return json({ error: "bad json" }, origin, 400);
      }
      const text = clean(body.text, 4000);
      if (text.length < 2) return json({ error: "empty" }, origin, 400);
      if (await tooOften(env, "say", ip, 300)) return json({ error: "slow down" }, origin, 429);

      const subject = clean(body.subject, 120) || "?";
      const from = clean(body.from, 200);
      const lang = clean(body.lang, 5);
      const why = await tell(
        env,
        `PRAGUE ROCK\n${subject}\n\n${text}\n\n${from ? "rispondi a: " + from : "nessun indirizzo"}  [${lang}]`
      );
      if (why) return json({ error: "not delivered", why }, origin, 502);
      return json({ ok: true }, origin);
    }

    return json({ error: "not found" }, origin, 404);
  },
};
