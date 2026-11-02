# -*- coding: utf-8 -*-
r"""PRAGA ROCK - tutti i concerti rock a Praga, con avviso Telegram per quelli nuovi.

Nessuna fonte da sola li ha tutti (GoOut non ha Avatar, Songkick non ha mezza scena
underground), quindi si leggono piu' fonti, si uniscono i doppioni e si tiene il rock.

    python update.py                giro completo: dati, pagina, Telegram, stato
    python update.py --no-send      come sopra ma senza mandare nulla e senza toccare lo stato
    python update.py --preview X    scrive anche una copia della pagina con i dati dentro

Il primo giro in assoluto NON manda messaggi: segna tutto come gia' visto. Da quel momento
Telegram riceve solo i concerti mai visti prima. Lo stesso vale per una fonte al suo primo
giro riuscito, altrimenti arriverebbero centinaia di "nuovi" tutti insieme.
"""
import argparse
import base64
import html
import json
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

try:
    import truststore  # sul PC Avast intercetta il TLS: senza questo requests fallisce

    truststore.inject_into_ssl()
except ImportError:
    pass

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
CONCERTS_FILE = DOCS / "concerts.json"
STATUS_FILE = DOCS / "status.json"
STATE_FILE = ROOT / "data" / "state.json"
ARTISTS_FILE = ROOT / "data" / "artist_genres.json"

PRAGUE = ZoneInfo("Europe/Prague")
AGENT = "PragaRock/1.0 (+https://github.com/BigThursday/praga-rock)"
HEADERS = {"User-Agent": AGENT, "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9,cs;q=0.8"}
LOOKUP_AGENT = {"User-Agent": AGENT, "Accept": "application/json"}

WARN_AFTER_FAILS = 8  # con un giro ogni 3 ore: un giorno intero di fonte rotta prima di avvisare
MAX_TELEGRAM_CONCERTS = 40
REMIND_DAYS = (10, 7, 3)  # promemoria dei BIG, giorni prima del concerto
REMIND_FROM_HOUR = 9


def log(*a):
    print(*a, flush=True)


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("ł", "l").replace("ø", "o")
    return re.sub(r"[\W_]+", " ", s).strip()


def get(session, url, *, params=None, tries=3, wait=8, timeout=40):
    last = None
    for i in range(tries):
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                if (r.encoding or "").lower() in ("", "iso-8859-1"):  # sito che non dichiara il charset: sono tutti UTF-8
                    r.encoding = "utf-8"
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = type(e).__name__
        if i < tries - 1:
            time.sleep(wait * (i + 1))
    raise RuntimeError(f"{urlparse(url).netloc}: {last}")


def web_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


STATUS_IN_TITLE = re.compile(r"\s*[-–—|:(]*\s*\b(VYPRODÁNO|VYPRODANO|SOLD[ -]?OUT|ZRUŠENO|ZRUSENO|CANCELLED|CANCELED)\b[!)]*", re.I)


def raw(source, sid, title, day, *, names=(), end=None, at=None, venue="", url="", genres=(), weak=(), house=(), status="", price=""):
    """genres: dichiarati dalla fonte. house: la fonte fa solo rock (club, promoter), vale se nessuno dice altro.
    weak: indizio scarso, vale solo se anche la ricerca sulla band non trova nulla."""
    flag = STATUS_IN_TITLE.search(title or "")
    if flag:  # "Horkyze Slize - VYPRODANO"
        status = status or ("cancelled" if norm(flag.group(1)) in ("zruseno", "cancelled", "canceled") else "soldout")
        title = title[:flag.start()] + title[flag.end():]
    return {
        "source": source, "id": f"{source}:{sid}", "title": clean_title(title), "names": [n for n in names if n],
        "date": day, "end": end if end and end > day else None, "time": at, "venue": canon_venue(venue),
        "url": url, "genres": list(genres), "weak": list(weak), "house": list(house), "status": status, "price": price,
    }


def clean_title(t):
    t = re.sub(r"\s+", " ", t or "").strip()
    t = re.sub(r"\s*[-–|]\s*(Praha|Prague)\s*$", "", t, flags=re.I)
    return t


def jsonld_events(soup):
    out = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and "Event" in str(item.get("@type")):
                out.append(item)
    return out


# ---------------------------------------------------------------- locali

# Lo stesso locale ha nomi diversi su ogni fonte. Il nome canonico serve a riconoscere i
# doppioni e a tenere pulito il menu dei locali nella pagina.
VENUES = [
    (r"archa", "ARCHA+"),
    (r"\b007\b|\bsedmicka\b", "Klub 007 Strahov"),
    (r"meet ?factory", "MeetFactory"),
    (r"akropolis", "Palác Akropolis"),
    (r"forum karl", "Forum Karlín"),
    (r"jazz ?dock", "Jazz Dock"),
    (r"(cafe|kavarna) ve? lese", "Café V lese"),
    (r"music ?bar.*lucern|lucerna music ?bar", "Lucerna Music Bar"),
    (r"(velky sal|great hall).*lucern|lucern.*(velky sal|great hall)", "Lucerna, Velký sál"),
    (r"futurum", "Futurum Music Bar"),
    (r"rock cafe", "Rock Café"),
    (r"modra vopice", "Modrá Vopice"),
    (r"\broxy\b", "Roxy"),
    (r"sasazu", "SaSaZu"),
    (r"o2 arena", "O2 arena"),
    (r"o2 universum", "O2 universum"),
    (r"fortuna", "Sportovní hala Fortuna"),
    (r"bike ?jesus", "Bike Jesus"),
    (r"cross ?club", "Cross Club"),
    (r"subzero", "Subzero"),
    (r"fuchs ?2", "Fuchs2"),
    (r"cargo gallery", "Cargo Gallery"),
    (r"klubovna", "Klubovna"),
    (r"(kampus|campus) hybernska", "Kampus Hybernská"),
    (r"hybernia", "Divadlo Hybernia"),
    (r"malostranska beseda", "Malostranská beseda"),
    (r"\bvagon\b", "Vagon"),
    (r"\box club\b", "OX Club"),
    (r"black pes", "Black Pes"),
    (r"nova spirala", "Nová Spirála"),
    (r"kastan", "Kaštan"),
    (r"(citarna|reading room).*unijazz", "Čítárna Unijazzu"),
    (r"kamina", "Kamina Boat"),
    (r"barikadnik", "Barikádník"),
    (r"distrikt", "Distrikt"),
    (r"vila stvanice", "Vila Štvanice"),
    (r"zizkov.*atrium|atrium.*zizkov", "Atrium Žižkov"),
    (r"pitevna", "Pivní klub Pitevna"),
    (r"studio rubin", "A studio Rubín"),
    (r"divadlo abc|abc theatre", "Divadlo ABC"),
    (r"krizik", "Křižíkovy pavilony"),
]


CANON_VENUES = {label for _, label in VENUES}
ONE_SHOW_VENUES = {"O2 arena", "Forum Karlín", "Sportovní hala Fortuna", "Lucerna, Velký sál"}  # un concerto a sera
VAGUE_VENUE = re.compile(r"\b(tba|various|ruzna mista|ruzne)\b")


def canon_venue(name):
    name = re.sub(r"\s*,\s*(Prague|Praha)\s*$", "", (name or "").replace("_", " ")).strip()
    n = norm(name)
    for pattern, label in VENUES:
        if re.search(pattern, n):
            return label
    return name


# ---------------------------------------------------------------- fonti
# Ogni fonte torna (eventi, completa). "completa" False = ha risposto solo in parte: quello che
# ha dato si usa, e i concerti che aveva dato ieri restano in pagina.


def fetch_goout(today):
    session = requests.Session()
    session.headers.update({"User-Agent": AGENT, "Accept": "application/json"})
    before = (today + timedelta(days=1100)).isoformat()
    params = [
        ("languages[]", "en"), ("categories[]", "concerts"), ("categories[]", "festivals"),
        ("after", f"{today.isoformat()}T00:00:00.000Z"), ("before", f"{before}T22:59:59.999Z"),
        ("sort", "timestamp:asc"), ("notScheduleTags[]", "online"), ("limit", "100"),
        ("cityIds[]", "101748113"), ("countryIsos[]", "cz"), ("include", "events,venues,performers"),
    ]
    events, performers, venues, schedules = {}, {}, {}, {}
    scroll, complete = None, False
    for _ in range(50):
        page = get(session, "https://goout.net/services/entities/v1/schedules", params=params + ([("scrollId", scroll)] if scroll else [])).json()
        inc = page.get("included", {})
        events.update({e["id"]: e for e in inc.get("events", [])})
        performers.update({p["id"]: p for p in inc.get("performers", [])})
        venues.update({v["id"]: v for v in inc.get("venues", [])})
        batch = page.get("schedules", [])
        before_count = len(schedules)
        schedules.update({s["id"]: s for s in batch})
        scroll = page.get("meta", {}).get("nextScrollId")
        if not batch or not scroll or len(schedules) == before_count:
            complete = True
            break
        time.sleep(0.5)

    out = []
    for s in schedules.values():
        a = s["attributes"]
        rel = s.get("relationships", {})
        ev = events.get((rel.get("event") or {}).get("id"))
        if not ev or a.get("state") != "approved" or a.get("isPermanent"):
            continue
        if ev["attributes"].get("mainCategory") in ("play", "exhibitions", "gastronomy", "for_children"):
            continue
        venue = (venues.get((rel.get("venue") or {}).get("id")) or {}).get("locales", {}).get("en", {}).get("name", "")
        if re.search(r"(czechia|slovakia).*various", venue, re.I):  # tour nazionali, le date singole arrivano a parte
            continue
        pfs = [performers[p["id"]] for p in ev.get("relationships", {}).get("performers", []) if p["id"] in performers]
        pfs = [p for p in pfs if p["attributes"].get("mainCategory") != "organizer"]  # Live Nation & co. stanno tra i "musicisti"
        names = [p["locales"].get("en", {}).get("name", "") for p in pfs]
        tags = ev["attributes"].get("tags", [])
        if not tags:  # i generi delle band contano solo se l'evento non ne ha: i supporter sporcano
            tags = sorted({t for p in pfs for t in p["attributes"].get("tags", [])})
        state = a.get("ticketingState")
        status = "cancelled" if state == "cancelled" or "cancelled" in a.get("tags", []) else "soldout" if state == "sold_out" else ""
        price = f"{a['pricing']} Kč" if a.get("pricing") and a.get("currency") == "czk" else ""
        start, end = a["startAt"], a.get("endAt") or ""
        out.append(raw(
            "goout", s["id"], ev["locales"].get("en", {}).get("name", ""), start[:10],
            names=names, end=end[:10] if end[:10] > start[:10] else None, at=start[11:16] if a.get("hasTime") else None,
            venue=venue, url=s.get("locales", {}).get("en", {}).get("siteUrl", ""), genres=tags, status=status, price=price,
        ))
    return out, complete


def fetch_songkick(today):
    base = "https://www.songkick.com/metro-areas/28425-czech-republic-prague"
    session = web_session()
    out, complete = {}, True

    def parse(soup, weak=()):
        added = 0
        for item in jsonld_events(soup):
            m = re.search(r"/(?:concerts|festivals)/[^/]*?(\d+)", item.get("url") or "")
            city = ((item.get("location") or {}).get("address") or {}).get("addressLocality") or ""
            if not m or (city and not re.search(r"prague|praha", norm(city))):
                continue
            perf = item.get("performer") or []
            names = [p.get("name") for p in (perf if isinstance(perf, list) else [perf]) if isinstance(p, dict)]
            start = item.get("startDate") or ""
            at = start[11:16] if len(start) > 10 and start[11:16] != "00:00" else None
            title = " + ".join(names) or re.sub(r"\s*@.*$", "", item.get("name") or "")
            key = m.group(1)
            if key in out:
                out[key]["weak"] = list(set(out[key]["weak"]) | set(weak))
                continue
            added += 1
            status = "cancelled" if "Cancelled" in str(item.get("eventStatus")) else ""
            out[key] = raw("songkick", key, title, start[:10], names=names, at=at, venue=(item.get("location") or {}).get("name", ""),
                           url=(item.get("url") or "").split("?")[0], weak=weak, status=status)
        return added

    for page in range(1, 16):
        try:
            r = get(session, base + (f"?page={page}" if page > 1 else ""), tries=3, wait=12)
        except RuntimeError as e:
            log("  songkick:", e)
            complete = False
            break
        if not parse(BeautifulSoup(r.text, "lxml")):
            break
        time.sleep(4)
    try:  # la loro lista rock e' larga (ci finisce James Blunt): vale solo se non si sa altro
        r = get(session, base + "/genre/rock", tries=2, wait=12)
        parse(BeautifulSoup(r.text, "lxml"), weak=("rock",))
    except RuntimeError as e:
        log("  songkick rock:", e)
    return list(out.values()), complete and bool(out)


def next_cursor(soup):
    """La pagina ha anche i link all'indietro: il cursore dice da che parte punta."""
    for a in soup.find_all("a", href=re.compile(r"cursor=")):
        cursor = parse_qs(urlparse(a["href"]).query).get("cursor", [""])[0]
        try:
            info = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        except ValueError:
            continue
        if info.get("_pointsToNextItems"):
            return cursor
    return None


def fetch_festivaly(today):
    session = web_session()
    base = [("tag[]", t) for t in ("rock", "metal", "punk", "hardcore", "ska", "alternative", "indie")]
    out, cursor, complete = {}, None, False
    for _ in range(90):
        r = get(session, "https://festivaly.eu/koncerty/praha", params=base + ([("cursor", cursor)] if cursor else []))
        soup = BeautifulSoup(r.text, "lxml")
        cards = {}
        for small in soup.find_all("small"):
            link = small.find_next_sibling("a", href=True)
            if not link or "∙" not in small.get_text() and "Koncert" not in small.get_text():
                continue
            when = link.find_next_sibling("span")
            hour = re.search(r"\b(\d{1,2}:\d{2})\b", when.get_text() if when else "")
            cards[link["href"].split("?")[0].rstrip("/").split("/")[-1]] = {
                "tags": [t.strip() for t in small.get_text().split("∙")[1:] if t.strip()],
                "at": hour.group(1).zfill(5) if hour else None,
            }
        added = 0
        for item in jsonld_events(soup):
            url = item.get("url") or ""
            slug = url.rstrip("/").split("/")[-1]
            if not slug or slug in out or not item.get("startDate"):
                continue
            place = item.get("location") or {}
            city = (place.get("address") or {}).get("addressLocality") or ""
            if city and not re.search(r"prague|praha", norm(city)):
                continue
            perf = item.get("performer") or []
            names = [p.get("name") for p in (perf if isinstance(perf, list) else [perf]) if isinstance(p, dict)]
            card = cards.get(slug, {})
            status = "cancelled" if "Cancelled" in str(item.get("eventStatus")) else ""
            out[slug] = raw("festivaly", slug, item.get("name"), item["startDate"][:10], names=names, end=(item.get("endDate") or "")[:10] or None,
                            at=card.get("at"), venue=place.get("name", ""), url=url, genres=card.get("tags", []), status=status)
            added += 1
        cursor = next_cursor(soup)
        if not cursor or not added:
            complete = True
            break
        time.sleep(1)
    return list(out.values()), complete


def fetch_kdykam(today):
    session = web_session()
    out, complete = {}, True
    for genre in ("rock", "metal"):
        try:
            r = get(session, f"https://kdykam.com/en/koncerty/{genre}/", timeout=60)
        except RuntimeError as e:
            log("  kdykam:", e)
            complete = False
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for node in soup.select("article.vote-card"):
            if node.get("data-city") != "Praha" or node.get("data-kind") != "concert":
                continue
            section = node.find_parent(attrs={"data-month": True})  # le card senza sezione sono concerti passati
            spans = [s.get_text(strip=True) for s in node.select("h3 + div span")]
            when = re.search(r"(\d{1,2})\.(\d{1,2})\.", spans[0] if spans else "")
            if not when or not section:
                continue
            until = re.search(r"–\s*\S*\s*(\d{1,2})\.(\d{1,2})\.", spans[0])
            d, mo = int(when.group(1)), int(when.group(2))
            y = int(section["data-month"][:4])
            day = f"{y}-{mo:02d}-{d:02d}"
            end = None
            if until:
                ey = y + 1 if int(until.group(2)) < mo else y
                end = f"{ey}-{int(until.group(2)):02d}-{int(until.group(1)):02d}"
            at = next((s for s in spans[1:] if re.fullmatch(r"\d{1,2}:\d{2}", s)), None)
            place = next((s for s in spans[1:] if "," in s and "Kč" not in s and "€" not in s and not s.startswith("(")), "")
            if not place or re.search(r"různá místa|various", place, re.I):
                continue
            sid = node.get("data-event-id")
            chips = [re.sub(r"[^\w\s/&-]", "", c.get_text(" ", strip=True)).strip() for c in node.select(".event-chip")]
            tags = [c for c in chips if c and c.lower() not in ("concert", "other genres")]
            if sid in out:
                out[sid]["genres"] = sorted(set(out[sid]["genres"]) | set(tags))
                continue
            out[sid] = raw("kdykam", sid, node.select_one("h3").get_text(strip=True), day, end=end, at=at.zfill(5) if at else None,
                           venue=place.rsplit(",", 1)[0], url=f"https://kdykam.com/en/event/{sid}/", genres=tags)
        time.sleep(1)
    return list(out.values()), complete and bool(out)


def fetch_eventon(source, url, venue):
    """Klub 007 e Modra Vopice usano lo stesso plugin WordPress: tutto quello che serve e' nella pagina."""
    session = web_session()
    soup = BeautifulSoup(get(session, url, timeout=60).text, "lxml")
    out = {}
    for ev in soup.select(".eventon_list_event"):
        start = ev.select_one('meta[itemprop="startDate"]')
        title = ev.select_one(".evcal_event_title")
        m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})T(\d{1,2}):(\d{2})", start.get("content", "") if start else "")
        if not m or not title:
            continue
        y, mo, d, h, mi = (int(x) for x in m.groups())
        day = f"{y}-{mo:02d}-{d:02d}"
        sid = f"{ev.get('data-event_id')}-{day}"
        if sid in out:
            continue
        link = ev.select_one('a[itemprop="url"]')
        sub = ev.select_one(".evcal_event_subtitle")
        sub = sub.get_text(" ", strip=True) if sub else ""
        name = title.get_text(" ", strip=True)
        is_price = bool(re.search(r"Kč|,-", sub))
        names = [re.sub(r"\(.*?\)", "", n).strip() for n in re.split(r",|\+", name)]
        genre = "" if is_price else sub
        # Modra Vopice titola "KONCERT" e mette le band nella descrizione: "ATROPHY (Usa – thrash metal) CONFESS (Iran – groove metal)"
        desc = ev.select_one(".eventon_desc_in")
        lineup = re.findall(r"([^()]{2,60}?)\s*\(([^()]*)\)", desc.get_text(" ", strip=True)) if desc else []
        said = [info.split("–", 1)[1].strip() for _, info in lineup if "–" in info]
        if not name_keys(name) and lineup:
            names = [band.strip() for band, _ in lineup]
            name = " + ".join(names)
        declared = [g for g in [genre] + said if buckets([g])]
        out[sid] = raw(source, sid, name, day, names=names, at=f"{h:02d}:{mi:02d}", venue=venue, url=link.get("href", "") if link else url,
                       house=venue_default(name, genre), genres=declared,
                       status="cancelled" if "cancelled" in ev.get("class", []) else "", price=sub.replace(",-", "") if is_price else "")
    return list(out.values()), True


def fetch_klub007(today):
    return fetch_eventon("klub007", "https://klub007strahov.cz/program/", "Klub 007 Strahov")


def fetch_modravopice(today):
    return fetch_eventon("modravopice", "https://www.modravopice.cz/", "Modrá Vopice")


def fetch_rockcafe(today):
    session = web_session()
    soup = BeautifulSoup(get(session, "https://rockcafe.cz/cs/program/").text, "lxml")
    out = []
    for item in soup.select("a.list-item"):
        kind = item.select_one(".type")
        title = item.select_one("h2")
        when = item.select_one(".date")
        m = re.search(r"(\d{1,2})\.(\d{1,2})\.(20\d\d)(?:,\s*(\d{1,2}:\d{2}))?", when.get_text() if when else "")
        if not m or not title or not kind or norm(kind.get_text()) != "hudba":
            continue
        name = title.get_text(" ", strip=True)
        out.append(raw("rockcafe", item.get("data-edit-on-title-id") or item.get("href"), name, f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}",
                       at=m.group(4).zfill(5) if m.group(4) else None, venue="Rock Café", url=item.get("href", ""), house=venue_default(name, "")))
    return out, True


def fetch_lucerna(today):
    """Lucerna Music Bar suona di tutto: il genere lo decidono le altre fonti o la ricerca sulla band."""
    session = web_session()
    soup = BeautifulSoup(get(session, "https://musicbar.cz/cs/program/").text, "lxml")
    out = []
    for item in soup.select("a.program-item"):
        title = item.select_one("p")
        stamp = item.get("data-timestamp", "")
        if not title or not stamp.isdigit():
            continue
        name = title.get_text(" ", strip=True)
        if NOT_ROCK_NIGHT.search(norm(name)):
            continue
        when = datetime.fromtimestamp(int(stamp), PRAGUE)
        out.append(raw("lucerna", item["href"].rstrip("/").split("/")[-1], name, when.date().isoformat(), at=when.strftime("%H:%M"),
                       venue="Lucerna Music Bar", url=item["href"]))
    return out, True


def fetch_futurum(today):
    session = web_session()
    soup = BeautifulSoup(get(session, "https://futurum.musicbar.cz/program/").text, "lxml")
    out = []
    for post in soup.select("div.post.type-post"):
        title = post.select_one(".event-lines_title")
        when = post.select_one(".line-date")
        m = re.search(r"(\d{2})\.(\d{2})\.(20\d\d)", when.get_text() if when else "")
        if not title or not m:
            continue
        name = title.get_text(" ", strip=True)
        hour = post.select_one(".event-lines-doors")
        hour = hour.get_text(strip=True) if hour else ""
        link = post.find_parent("a") or post.find("a", href=True)
        out.append(raw("futurum", (post.get("class") or ["?"])[0], name, f"{m.group(3)}-{m.group(2)}-{m.group(1)}",
                       at=hour if re.fullmatch(r"\d{2}:\d{2}", hour) else None, venue="Futurum Music Bar",
                       url=link.get("href", "") if link else "https://futurum.musicbar.cz/program/", house=venue_default(name, "")))
    return out, True


def fetch_obscure(today):
    """Obscure Promotion: il grosso del metal che passa da Praga, spesso prima che compaia altrove."""
    session = web_session()
    soup = BeautifulSoup(get(session, "https://obscure.cz/cs").text, "lxml")
    months = {m: i + 1 for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"))}
    out, year, last = {}, today.year, (today.month, 1)
    for card in soup.select(".ticket.product-item"):
        when = card.select_one(".eventDate")
        link = card.select_one(".ticket_name a")
        m = re.match(r"(\d{1,2}) (\w{3})", when.get_text(strip=True) if when else "")
        if not m or not link or m.group(2) not in months:
            continue
        current = (months[m.group(2)], int(m.group(1)))
        if current < last:  # la lista e' in ordine di data e l'anno non c'e': quando il mese torna indietro e' l'anno dopo
            year += 1
        last = current
        place = card.select_one(".venue")
        place = place.get_text(strip=True) if place else ""
        title = link.get_text(strip=True)
        if not re.match(r"pra(ha|gue)\b", norm(place)):
            continue
        hour = card.select_one(".eventTime")
        hour = hour.get_text(strip=True) if hour else ""
        money = card.select_one(".money")
        price = re.sub(r"\.00", "", money.get_text(strip=True)) if money else ""
        sid = link["href"].rstrip("/").split("/")[-1]
        title = re.sub(r"\s*-\s*Praha\s*$", "", title, flags=re.I)
        out[sid] = raw("obscure", sid, title, f"{year}-{current[0]:02d}-{current[1]:02d}", names=[n.strip() for n in title.split(",")],
                       at=hour if re.fullmatch(r"\d{2}:\d{2}", hour) else None, venue=re.sub(r"^\w+\s*-\s*|\s*\(CZ\)\s*$", "", place),
                       url=link["href"], house=["metal"], price=price)
    return list(out.values()), True


SOURCES = [
    # (chiave, nome in pagina, funzione, sotto questo numero il parser si e' rotto)
    ("goout", "GoOut", fetch_goout, 100),
    ("festivaly", "festivaly.eu", fetch_festivaly, 20),
    ("songkick", "Songkick", fetch_songkick, 50),
    ("obscure", "Obscure Promotion", fetch_obscure, 3),
    ("futurum", "Futurum", fetch_futurum, 3),
    ("lucerna", "Lucerna Music Bar", fetch_lucerna, 3),
    ("rockcafe", "Rock Café", fetch_rockcafe, 3),
    ("klub007", "Klub 007", fetch_klub007, 3),
    ("modravopice", "Modrá Vopice", fetch_modravopice, 1),
    ("kdykam", "kdykam", fetch_kdykam, 20),
]
PRIORITY = {key: i for i, (key, *_) in enumerate(SOURCES)}
SOURCE_LABEL = {key: label for key, label, *_ in SOURCES}


# ---------------------------------------------------------------- generi

NOT_ROCK_NIGHT = re.compile(r"\b(reggae|dub|dj|jazz|stand ?up|divadlo|workshop|prednaska|disco|techno|drum and bass|dnb|hip ?hop|rap|poezie|autorske cteni|promitani|"
                            r"video party|generation party|dance party|retro party|korzo narodni)\b")
# Concerti "rock" che rock non sono: quartetti d'archi a lume di candela, tributi in chiesa, festival jazz.
NOT_A_GIG_PLACE = re.compile(r"\b(kostel\w*|church|chapel|kaple|synagog\w*|bazilika|basilica|klaster\w*|monastery|convent|rudolfinum|planetarium|planetarij)\b")
NOT_A_GIG_TITLE = re.compile(r"\b(jazz|jazzfest|candlelight|gospely?|hodina zpivani|pro deti)\b")
# "Retro Dance Party" fuori, "Rock Party" e "Metal Disco" dentro.
PARTY_TITLE = re.compile(r"\b(party|parties|disco|diskoteka|dance)\b")
ROCK_TITLE = re.compile(r"\b(rock\w*|metal\w*|punk\w*)\b")


def venue_default(title, sub):
    """Club rock: se non si sa altro, il genere e' rock. Tranne le serate che dichiarano altro."""
    return [] if NOT_ROCK_NIGHT.search(norm(f"{title} {sub}")) else ["rock"]


ELECTRONIC = re.compile(r"\b(techno|trance|house|dubstep|edm|uk garage|hip hop|rap)\b")

# Generi fini in pagina: un concerto puo' averne piu' d'uno. "rock" resta per chi non ha altro.
STYLES = [(key, re.compile(rx)) for key, rx in (
    ("hard", r"\b(hard rock|classic rock|heavy rock|glam|blues rock|arena rock|southern rock|aor|boogie)\b"),
    ("stoner", r"\b(stoner|psych\w*|desert rock|space rock|krautrock|fuzz|acid rock)\b"),
    ("prog", r"\b(prog\w*|post rock|math rock|art rock|experimental rock)\b"),
    ("garage", r"\b(garage|rock n roll|rock and roll|rockabilly|psychobilly|surf|r n r)\b"),
    ("alt", r"\b(indie|alternative|grunge|shoegaze|noise rock|new wave|post punk|britpop|dream pop)\b"),
)]
STYLE_ORDER = ["hard", "stoner", "prog", "garage", "metal", "punk", "alt", "rock"]

# BIG: band con molti fan su Deezer, oppure locale da grandi nomi.
BIG_FANS = 20000
BIG_VENUES = {"O2 arena", "O2 universum", "Forum Karlín", "Sportovní hala Fortuna"}


def buckets(tags):
    out = set()
    for tag in tags:
        t = norm(tag.replace("_", " "))
        if not t or "party" in t or ELECTRONIC.search(t):
            continue
        if "metal" in t or re.search(r"\b(deathcore|grindcore|goregrind|djent|thrash|doom|sludge|black n roll)\b", t):
            out.add("metal")
        if "punk" in t or re.search(r"\b(hardcore|hc|ska|oi|crust|emo|screamo|powerviolence)\b", t):
            out.add("punk")
        if "rock" in t or re.search(r"\b(grunge|garage|psychedelic|psych|stoner|rockabilly|r n r|rock n roll|industrial|post hardcore)\b", t):
            out.add("rock")
        if re.fullmatch(r"(concert )?(indie|alternative)|new wave|shoegaze|noise|alternative indie|indie pop", t):
            out.add("alt")
    return out


def is_music_tag(tag):
    return not re.match(r"(in_city|play_|exhibitions|for_children|gastronomy|festivals_|festival_|sport_|organizer_)", tag)


def lookup_itunes(name):
    r = requests.get("https://itunes.apple.com/search", params={"term": name, "entity": "musicArtist", "limit": 5}, headers=LOOKUP_AGENT, timeout=20)
    r.raise_for_status()
    for a in r.json().get("results", []):
        if norm(a.get("artistName")) == norm(name) and a.get("primaryGenreName"):
            return [] if a["primaryGenreName"] == "Hardcore" else [a["primaryGenreName"]]  # per Apple e' techno
    return []


def lookup_musicbrainz(name):
    q = '"' + name.replace('"', "").replace("\\", "") + '"'
    for wait in (0, 4, 10):  # MusicBrainz risponde 503 quando e' carico: basta riprovare poco dopo
        time.sleep(wait)
        r = requests.get("https://musicbrainz.org/ws/2/artist/", params={"query": f"artist:{q}", "fmt": "json", "limit": 5}, headers=LOOKUP_AGENT, timeout=20)
        if r.status_code != 503:
            break
    r.raise_for_status()
    best, score = [], 0
    for a in r.json().get("artists", []):
        tags = a.get("tags") or []
        total = sum(t.get("count", 0) for t in tags)
        if norm(a.get("name")) == norm(name) and total > score:
            # solo i primi tre: piu' in basso si trovano "folk rock" su un cantante country
            best, score = [t["name"] for t in sorted(tags, key=lambda t: -t.get("count", 0))[:3]], total
    return best


def lookup_deezer(name):
    """Fan su Deezer: misura grezza ma gratuita di quanto e' famosa una band."""
    r = requests.get("https://api.deezer.com/search/artist", params={"q": name, "limit": 5}, headers=LOOKUP_AGENT, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "error" in data:  # quota superata: Deezer risponde 200 con l'errore dentro
        raise ValueError(data["error"].get("message", "errore Deezer"))
    return max((a.get("nb_fan") or 0 for a in data.get("data", []) if norm(a.get("name")) == norm(name)), default=0)


def artist_info(name, cache, budget):
    """Generi (MusicBrainz, Apple Music) e fan (Deezer) di una band, una volta sola: poi restano in cache.
    Torna None se non si sa nulla. Senza "fans" la ricerca su Deezer e' fallita e si riprova al giro dopo."""
    key = norm(name)
    hit = cache.get(key)
    today = date.today().isoformat()
    known = bool(hit and (hit["tags"] or hit["at"] > (date.today() - timedelta(days=30)).isoformat()))
    if known and "fans" in hit:
        return hit
    if not known:
        if budget["left"] <= 0:
            return None
        budget["left"] -= 1
        tags, answered = [], False
        for lookup, pause in ((lookup_musicbrainz, 1.5), (lookup_itunes, 3.2)):
            try:
                tags += lookup(name)
                answered = True
            except (requests.RequestException, ValueError) as e:
                log("  ricerca genere fallita:", name, lookup.__name__, type(e).__name__)
            time.sleep(pause)
        if not answered:
            return None
        hit = cache[key] = {"tags": tags, "at": today}
    try:  # Deezer e' veloce e non conta nel budget
        hit["fans"] = lookup_deezer(name)
    except (requests.RequestException, ValueError) as e:
        log("  ricerca fan fallita:", name, type(e).__name__)
    time.sleep(0.3)
    if not known and budget["left"] % 20 == 0:  # un giro interrotto non deve rifare tutte le ricerche
        save_json(ARTISTS_FILE, cache)
    return hit


# ---------------------------------------------------------------- unione dei doppioni

NOISE = {"koncert", "concert", "live", "tour", "praha", "prague", "support", "host", "hoste", "guests", "guest", "special guest",
         "special guests", "band", "fest", "festival", "vip", "tba", "the", "krest", "dalsi", "a dalsi", "and more", "party",
         "vanocni koncert", "christmas concert", "novorocni koncert", "tribute", "tribute band"}
GENERIC_WORDS = re.compile(r"\b(20\d\d|tour|turne|live|in prague|v praze|koncert|concert)\b")
SPLITTER = re.compile(r"\s*(?:\+|&|,|/|;|:|\||–|—|\s-\s|\sx\s|\sa\s|\sand\s|\sfeat\.?\s|\sft\.?\s|\svs\.?\s|\bsupport:?\s|\bhost:?\s|\bhosté:?\s)\s*", re.I)


def name_keys(title, names=()):
    keys = set()
    plain = re.sub(r"\(.*?\)|\[.*?\]", " ", title or "")
    for part in [plain] + SPLITTER.split(plain) + list(names):
        n = GENERIC_WORDS.sub(" ", norm(part))
        n = re.sub(r"\s+", " ", n).strip()
        if len(n) >= 3 and n not in NOISE:
            keys.add(n)
    return keys


def similar_names(a, b):
    """Stesso locale, stessa sera, nome quasi uguale: "Nessi Gomes" e "Nessi Gomez"."""
    return any(SequenceMatcher(None, x, y).ratio() >= 0.85 for x in a for y in b if len(x) >= 5 and len(y) >= 5)


def same_place(a, b):
    if not a or not b or VAGUE_VENUE.search(norm(a)) or VAGUE_VENUE.search(norm(b)) or a == b:
        return True
    if a in CANON_VENUES and b in CANON_VENUES:
        return False
    noise = {"club", "klub", "music", "bar", "praha", "prague", "cafe", "kavarna", "the", "hall", "sal", "divadlo", "theatre", "theater",
             "kulturni", "centrum", "center", "centre", "dum", "house", "pivni", "restaurace", "hudebni", "pub"}
    words = lambda v: {w for w in norm(v).split() if len(w) > 2 and w not in noise}
    return bool(words(a) & words(b))


def keys_overlap(a, b):
    for x in a:
        for y in b:
            if x == y or (len(x) >= 4 and f" {x} " in f" {y} ") or (len(y) >= 4 and f" {y} " in f" {x} "):
                return True
    return False


def same_concert(c, r):
    if not same_place(c["venue"], r["venue"]):
        # Locali diversi: conta solo il titolo. I nomi dei musicisti no: "Olympic" suona al suo concerto
        # e figura anche tra gli ospiti di un tributo, lo stesso giorno, in un altro locale.
        return keys_overlap(c["title_keys"], r["title_keys"])
    if keys_overlap(c["keys"], r["keys"]):
        return True
    if not c["venue"] or not r["venue"]:
        return False
    if not c["keys"] or not r["keys"]:  # "KONCERT" e basta: il locale e la data bastano
        return True
    if r["source"] in c["sources"]:  # la stessa fonte li elenca a parte: sono due concerti
        return False
    if c["venue"] in ONE_SHOW_VENUES and c["venue"] == r["venue"]:
        return True
    return (c["time"] and c["time"] == r["time"]) or similar_names(c["keys"], r["keys"])


def merge(events):
    days = defaultdict(list)
    for r in sorted(events, key=lambda e: (PRIORITY[e["source"]], e["id"])):
        r = dict(r, keys=name_keys(r["title"], r["names"]), title_keys=name_keys(r["title"]))
        for c in days[r["date"]]:
            if same_concert(c, r):
                c["keys"] |= r["keys"]
                c["title_keys"] |= r["title_keys"]
                c["sources"].add(r["source"])
                c["ids"].append(r["id"])
                c["genres"] += r["genres"]
                c["weak"] += r["weak"]
                c["house"] += r["house"]
                c["links"].append({"source": r["source"], "url": r["url"]})
                for field in ("end", "time", "venue", "price", "status"):
                    c[field] = c[field] or r[field]
                if r["names"] and not c["names"]:
                    c["names"] = r["names"]
                if not name_keys(c["title"]):  # prima fonte con un titolo vuoto come "KONCERT"
                    c["title"] = r["title"]
                break
        else:
            days[r["date"]].append({
                "ids": [r["id"]], "title": r["title"], "names": r["names"], "date": r["date"], "end": r["end"], "time": r["time"],
                "venue": r["venue"], "price": r["price"], "status": r["status"], "genres": list(r["genres"]), "weak": list(r["weak"]), "house": list(r["house"]),
                "links": [{"source": r["source"], "url": r["url"]}], "keys": set(r["keys"]), "title_keys": set(r["title_keys"]), "sources": {r["source"]},
            })
    return [c for day in days.values() for c in day]


def drop_ghosts(concerts):
    """kdykam a volte ripete un concerto il giorno prima o dopo (Bryan Adams il 13 e il 14). Se un'altra fonte
    lo conferma sul giorno vicino, nello stesso locale, quello di kdykam e' un doppione."""
    days = defaultdict(list)
    for c in concerts:
        days[c["date"]].append(c)
    kept = []
    for c in concerts:
        if c["sources"] == {"kdykam"}:
            day = date.fromisoformat(c["date"])
            near = days[(day - timedelta(days=1)).isoformat()] + days[(day + timedelta(days=1)).isoformat()]
            twin = next((x for x in near if x["sources"] - {"kdykam"} and x["venue"] == c["venue"] and keys_overlap(x["keys"], c["keys"])), None)
            if twin:
                twin["ids"] += c["ids"]
                continue
        kept.append(c)
    return kept


def styles(tags, coarse):
    """Generi fini per la pagina. Se i tag non dicono nulla di piu', restano quelli grossi."""
    out = set()
    for tag in tags:
        t = norm(tag.replace("_", " "))
        if not t or "party" in t or ELECTRONIC.search(t):
            continue
        out |= {key for key, rx in STYLES if rx.search(t)}
    out |= buckets(tags) & {"metal", "punk"}
    out |= set(coarse) & {"metal", "punk", "alt"}
    return sorted(out or set(coarse), key=STYLE_ORDER.index)


def headliner(c):
    head = c["names"][0] if c["names"] else (SPLITTER.split(c["title"])[0] or c["title"])
    return re.sub(r"\(.*?\)", " ", head).strip()


def classify(concerts, cache, budget):
    kept = []
    for c in concerts:
        if NOT_A_GIG_PLACE.search(norm(c["venue"])) or NOT_A_GIG_TITLE.search(norm(c["title"])):
            continue
        head = headliner(c)
        lookup = (lambda: artist_info(head, cache, budget)) if name_keys(head) else (lambda: None)
        strong = [t for t in c["genres"] if is_music_tag(t)]
        found = buckets(strong)
        info = None
        title = norm(c["title"])
        if PARTY_TITLE.search(title) and not ROCK_TITLE.search(title):
            # Serata da ballo, anche se la fonte scrive "rock". Resta solo se la band in testa e' rock: "Party Cannon".
            info = lookup()
            if not (info and buckets(info["tags"]) & {"rock", "metal", "punk"}):
                continue
        if not strong and c["house"]:
            found = buckets(c["house"])
        elif not strong:
            info = info or lookup()
            looked = info["tags"] if info else None
            found = buckets(looked or [])
            if not found and not looked:  # la ricerca non sa nulla: restano locale e lista Songkick
                found = buckets(c["weak"])
        if found:
            info = info or lookup()  # solo per i concerti che restano: fan e generi fini
            c["buckets"] = sorted(found, key=["rock", "metal", "punk", "alt"].index)
            c["styles"] = styles(strong + (c["house"] if not strong else []) + (info["tags"] if info else []), c["buckets"])
            c["big"] = c["venue"] in BIG_VENUES or "tipsport" in norm(c["venue"]) or bool(info and (info.get("fans") or 0) >= BIG_FANS)
            kept.append(c)
    return kept


def is_rock(c):
    return bool(set(c["buckets"]) & {"rock", "metal", "punk"})


# ---------------------------------------------------------------- stato e nuovi concerti


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def save_concerts(data):
    """Un concerto per riga: tra un giro e l'altro git vede solo le righe cambiate."""
    CONCERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = ",\n".join(json.dumps(c, ensure_ascii=False) for c in data)
    CONCERTS_FILE.write_text(f"[\n{lines}\n]\n", encoding="utf-8", newline="\n")


def fingerprint(c):
    return f"{c['date']}|{min(c['keys'], key=lambda k: (len(k), k)) if c['keys'] else norm(c['title'])}"


def find_new(concerts, state, today_iso):
    """Il concerto e' nuovo se nessuna fonte l'aveva mai dato prima. Qui si riempie anche first_seen."""
    seen, prints = state["seen"], state["fingerprints"]
    baselined = set(state["baselined"])
    new = []
    for c in concerts:
        if not is_rock(c):
            c["first_seen"] = min((seen[i]["first"] for i in c["ids"] if i in seen), default=today_iso)
            continue
        known = [seen[i]["first"] for i in c["ids"] if i in seen]
        fp = fingerprint(c)
        if fp in prints:
            known.append(prints[fp])
        c["first_seen"] = min(known) if known else today_iso
        if known or c["status"] == "cancelled" or c["date"] < today_iso or not c["big"]:  # su Telegram solo i BIG
            continue
        if not any(i.split(":")[0] in baselined for i in c["ids"]):  # fonte al primo giro: niente valanghe
            continue
        new.append(c)
    return sorted(new, key=lambda c: (c["date"], c["time"] or ""))


def remember(concerts, state, today_iso):
    cutoff = (date.fromisoformat(today_iso) - timedelta(days=45)).isoformat()
    for c in concerts:
        for i in c["ids"]:
            state["seen"].setdefault(i, {"first": c.get("first_seen", today_iso), "date": c["date"]})["date"] = c["date"]
        state["fingerprints"].setdefault(fingerprint(c), c.get("first_seen", today_iso))
    state["seen"] = {k: v for k, v in state["seen"].items() if v["date"] >= cutoff}
    state["fingerprints"] = {k: v for k, v in state["fingerprints"].items() if k[:10] >= cutoff}


# ---------------------------------------------------------------- Telegram

WEEKDAYS = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
MONTHS = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
BUCKET_LABEL = {"rock": "Rock", "metal": "Metal", "punk": "Punk", "alt": "Indie/Alt"}


def pretty_date(c):
    d = date.fromisoformat(c["date"])
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}" + (f", {c['time']}" if c.get("time") else "")


def telegram_send(token, chat, text):
    for _ in range(3):
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30,
                              data={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"})
        except requests.RequestException as e:  # il messaggio dell'eccezione contiene l'URL col token: non va nei log
            raise RuntimeError(f"Telegram non raggiungibile ({type(e).__name__})") from None
        if r.status_code == 429:
            time.sleep(int(r.json().get("parameters", {}).get("retry_after", 5)) + 1)
            continue
        if not r.ok:
            raise RuntimeError(f"Telegram {r.status_code}: {r.json().get('description', '')}")
        return
    raise RuntimeError("Telegram: troppe richieste")


def quiet_send(token, chat, text):
    try:
        telegram_send(token, chat, text)
        return None
    except RuntimeError as e:
        log("Telegram:", e)
        return str(e)


def concert_block(c):
    title = html.escape(c["title"])
    genres = " · ".join(BUCKET_LABEL[b] for b in c["buckets"] if b != "alt")
    lines = [f"<b>{title}</b>", f"📅 {pretty_date(c)}"]
    if c.get("venue"):
        lines.append(f"📍 {html.escape(c['venue'])}" + (f" · {html.escape(c['price'])}" if c.get("price") else ""))
    if genres:
        lines.append(f"🎸 {genres}")
    if c["links"] and c["links"][0]["url"]:
        lines.append(f"🎟 <a href=\"{html.escape(c['links'][0]['url'], quote=True)}\">Dettagli e biglietti</a>")
    return "\n".join(lines)


def due_reminders(concerts, state, today):
    """BIG in arrivo tra 10, 7 o 3 giorni, non ancora ricordati e non appena annunciati."""
    due = []
    for c in concerts:
        days = (date.fromisoformat(c["date"]) - today).days
        key = f"{fingerprint(c)}|{days}"
        if c["big"] and days in REMIND_DAYS and c["status"] != "cancelled" and c.get("first_seen") != today.isoformat() and key not in state["reminded"]:
            due.append((days, key, c))
    return sorted(due, key=lambda x: (x[0], x[2]["time"] or ""))


def reminder_messages(due):
    messages, current = [], ""
    for days in sorted({d for d, _, _ in due}):
        group = [c for d, _, c in due if d == days]
        head = f"⏰ <b>Tra {days} giorni</b>"
        current = (current + "\n\n" if current else "") + head
        for c in group:
            block = concert_block(c)
            if len(current) + len(block) + 2 > 3800:
                messages.append(current)
                current = block
            else:
                current += "\n\n" + block
    if current:
        messages.append(current)
    return messages


def telegram_messages(new, page_url):
    shown = new[:MAX_TELEGRAM_CONCERTS]
    head = "🤘 <b>Nuovo concerto rock a Praga</b>" if len(new) == 1 else f"🤘 <b>{len(new)} nuovi concerti rock a Praga</b>"
    tail = []
    if len(new) > len(shown):
        tail.append(f"…e altri {len(new) - len(shown)}.")
    if page_url:
        tail.append(f"<a href=\"{html.escape(page_url, quote=True)}\">Tutti i concerti</a>")
    messages, current = [], head
    for c in shown:
        block = concert_block(c)
        if len(current) + len(block) + 2 > 3800:
            messages.append(current)
            current = block
        else:
            current += "\n\n" + block
    if tail:
        current += "\n\n" + "\n".join(tail)
    messages.append(current)
    return messages


# ---------------------------------------------------------------- pagina


def public_concert(c):
    links, seen = [], set()
    for l in c["links"]:
        if l["url"] and l["source"] not in seen:
            seen.add(l["source"])
            links.append({"name": SOURCE_LABEL[l["source"]], "url": l["url"]})
    return {
        "id": c["ids"][0], "date": c["date"], "end": c["end"], "time": c["time"], "title": c["title"], "venue": c["venue"],
        "genres": c["styles"], "buckets": c["buckets"], "big": c["big"], "status": c["status"], "price": c["price"], "links": links, "first_seen": c["first_seen"],
    }


def write_preview(path, data, status):
    template = (DOCS / "index.html").read_text(encoding="utf-8")
    template = re.sub(r"<!doctype html>|</?html[^>]*>|</?head>|</?body[^>]*>|<meta charset[^>]*>|<meta name=\"viewport\"[^>]*>", "", template, flags=re.I)
    payload = json.dumps({"concerts": data, "status": dict(status, preview=True)}, ensure_ascii=False).replace("</", "<\\/")
    Path(path).write_text(template.replace("<!--DATA-->", f"<script>window.__DATA__={payload}</script>"), encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- giro


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-send", action="store_true", help="niente Telegram e stato invariato")
    ap.add_argument("--preview", help="scrive una copia unica della pagina con i dati dentro")
    ap.add_argument("--max-lookups", type=int, default=60, help="ricerche di genere per giro")
    ap.add_argument("--only", help="solo queste fonti, separate da virgola (per le prove)")
    args = ap.parse_args()

    now = datetime.now(PRAGUE)
    today = now.date()
    today_iso = today.isoformat()
    stale_iso = (today - timedelta(days=14)).isoformat()  # "in corso" da settimane: abbonamenti, non concerti
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    page_url = os.environ.get("PAGE_URL", "").strip()

    state = load_json(STATE_FILE, None)
    first_run = state is None
    state = state or {}
    for key, default in (("seen", {}), ("fingerprints", {}), ("baselined", []), ("health", {})):
        state.setdefault(key, default)

    events, report, unfinished = [], [], set()
    wanted = set(args.only.split(",")) if args.only else None
    for key, label, fn, minimum in SOURCES:
        if wanted and key not in wanted:
            continue
        started = time.time()
        error = None
        try:
            got, complete = fn(today)
        except Exception as e:  # una fonte rotta non deve fermare le altre
            got, complete, error = [], False, f"{type(e).__name__}: {e}"
        got = [e for e in got if (e["end"] or e["date"]) >= today_iso and e["date"] >= stale_iso and e["title"]]
        ok = complete and len(got) >= minimum
        if not complete or not ok:
            unfinished.add(key)
        log(f"{label:14} {len(got):>5} eventi  {'ok' if ok else 'PROBLEMA ' + (error or 'pochi eventi o risposta parziale')}  ({time.time() - started:.0f}s)")
        events += got
        report.append({"key": key, "name": label, "count": len(got), "ok": ok, "error": error})

    cache = load_json(ARTISTS_FILE, {})
    concerts = classify(drop_ghosts(merge(events)), cache, {"left": args.max_lookups})
    save_json(ARTISTS_FILE, cache)

    # Una fonte giu' non deve svuotare la pagina: restano i concerti che solo lei conosceva.
    previous = load_json(CONCERTS_FILE, [])
    carried = 0
    for old in previous:
        if (old.get("end") or old["date"]) < today_iso:
            continue
        sources = {i.split(":")[0] for i in old.get("ids", [])}
        if not sources or not sources <= unfinished:
            continue
        keys = name_keys(old["title"])
        if any(c["date"] == old["date"] and (old["id"] in c["ids"] or keys_overlap(keys, c["keys"])) for c in concerts):
            continue
        concerts.append({
            "ids": old["ids"], "title": old["title"], "names": [], "date": old["date"], "end": old.get("end"), "time": old.get("time"),
            "venue": old.get("venue", ""), "price": old.get("price", ""), "status": old.get("status", ""), "genres": [], "weak": [], "house": [],
            "links": [{"source": k, "url": u} for k, u in old.get("source_links", [])], "keys": keys,
            "buckets": old.get("buckets", old["genres"]), "styles": old["genres"], "big": old.get("big", False),
        })
        carried += 1

    new = find_new(concerts, state, today_iso)
    concerts.sort(key=lambda c: (c["date"], c["time"] or "99", norm(c["title"])))
    rock = [c for c in concerts if is_rock(c)]
    log(f"\n{len(concerts)} concerti in pagina, {len(rock)} rock/metal/punk, {carried} tenuti da ieri, {len(new)} nuovi")

    published = []
    for c in concerts:
        item = public_concert(c)
        item["ids"] = c["ids"]
        item["source_links"] = [[l["source"], l["url"]] for l in c["links"]]
        published.append(item)
    status = {"updated": now.isoformat(timespec="minutes"), "sources": [{k: s[k] for k in ("name", "count", "ok")} for s in report]}

    status["baseline"] = state.get("baseline", today_iso)
    if args.preview:
        write_preview(args.preview, published, status)
    if args.no_send:
        for c in new[:30]:
            log("  NUOVO:", pretty_date(c), "|", c["title"], "|", c["venue"], "|", c["buckets"])
        save_concerts(published)
        save_json(STATUS_FILE, status)
        return

    # Prima si manda, poi si scrive lo stato: se Telegram fallisce i nuovi restano nuovi al giro dopo.
    sendable = bool(token and chat)
    delivered, failure = not new, None
    if first_run:
        log("Primo giro: segno tutto come visto, nessun messaggio.")
        state["baseline"] = today_iso
        delivered = True
    elif new and sendable:
        try:
            for text in telegram_messages(new, page_url):
                telegram_send(token, chat, text)
            log(f"Telegram: avvisati {len(new)} concerti.")
            delivered = True
        except RuntimeError as e:
            failure = str(e)
            log("Telegram:", failure)
    elif new:
        log("Telegram non configurato: nessun messaggio, i nuovi restano da mandare.")

    # Promemoria dei BIG: al primo giro dopo le 9, cosi' non arrivano di notte.
    state.setdefault("reminded", {})
    state["reminded"] = {k: v for k, v in state["reminded"].items() if v >= today_iso}
    due = due_reminders(rock, state, today) if sendable and not first_run and not failure and now.hour >= REMIND_FROM_HOUR else []
    if due:
        try:
            for text in reminder_messages(due):
                telegram_send(token, chat, text)
            for _, key, c in due:
                state["reminded"][key] = c["date"]
            log(f"Telegram: {len(due)} promemoria.")
        except RuntimeError as e:
            failure = str(e)
            log("Telegram:", failure)

    if delivered:
        remember(rock, state, today_iso)
    for s in report:
        h = state["health"].setdefault(s["key"], {"fails": 0, "warned": False})
        if s["ok"]:
            if s["key"] not in state["baselined"]:
                state["baselined"].append(s["key"])
            if h["warned"] and sendable and not failure:
                failure = quiet_send(token, chat, f"✅ {html.escape(s['name'])} ha ripreso a rispondere.")
            state["health"][s["key"]] = {"fails": 0, "warned": False}
        else:
            h["fails"] += 1
            if h["fails"] >= WARN_AFTER_FAILS and not h["warned"] and sendable and not failure:
                failure = quiet_send(token, chat, f"⚠️ {html.escape(s['name'])} non risponde da {h['fails']} controlli di fila. "
                                                  f"I suoi concerti restano in pagina ma quelli nuovi potrebbero mancare.")
                h["warned"] = not failure

    status["baseline"] = state.get("baseline", today_iso)
    save_concerts(published)
    save_json(STATUS_FILE, status)
    save_json(STATE_FILE, state)
    if failure:  # il giro su GitHub risulta fallito e arriva la mail: e' l'unico canale se il bot e' morto
        sys.exit(f"Telegram non consegna: {failure}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
