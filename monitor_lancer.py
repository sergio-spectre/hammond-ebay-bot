#!/usr/bin/env python3
"""
Monitor de anuncios: cambio manual Mitsubishi Lancer MIVEC 2.0 (2008-2019)
no Mercado Livre e OLX -> Discord + site (data/lancer.json).

O que faz:
  1. Busca anuncios no Mercado Livre (API publica de busca) e no OLX
     (raspagem do HTML/JSON embutido da pagina de busca).
  2. Filtra por palavras-chave (cambio manual + lancer + mivec/2.0),
     sem limite de preco (o usuario pediu sem teto de preco).
  3. Salva tudo em data/lancer.json (pro site), ordenado do mais barato
     pro mais caro.
  4. Compara com os anuncios ja notificados (seen_items_lancer.json) e
     manda os novos para um webhook do Discord.

Variaveis de ambiente esperadas:
  DISCORD_WEBHOOK_URL - URL do webhook do canal do Discord (opcional: se
                         nao estiver definida, o script so atualiza o site
                         e pula o Discord)

Qualquer fonte (Mercado Livre ou OLX) que falhar e ignorada sem derrubar
o script inteiro -- o site e atualizado com o que der certo.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

STATE_FILE = Path("seen_items_lancer.json")
DATA_FILE = Path("data/lancer.json")
SEEN_TTL_DAYS = 60

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Termos de busca: cambio manual do Lancer com motor 2.0 MIVEC (2008-2019).
# Varias variacoes porque gente escreve de jeitos diferentes (com/sem
# acento, "caixa de cambio", etc.).
ML_QUERIES = [
    "cambio manual lancer mivec",
    "cambio manual lancer 2.0",
    "caixa cambio manual lancer",
    "transmissao manual lancer mivec",
]
OLX_QUERIES = [
    "cambio manual lancer mivec",
    "cambio manual lancer 2.0",
    "caixa cambio lancer manual",
]

# Confirma que o anuncio e mesmo sobre o Lancer (nao outro carro que por
# acaso apareceu na busca) e sobre cambio MANUAL (nao automatico/CVT).
LANCER_PATTERN = re.compile(r"\blancer\b", re.I)
MANUAL_PATTERN = re.compile(r"\bmanual\b", re.I)
AUTOMATIC_EXCLUDE = re.compile(r"\bautomatic[oa]\b|\bcvt\b|\btiptronic\b", re.I)
CAMBIO_PATTERN = re.compile(r"c[âa]mbio|c[aâ]ixa\s*de\s*c[âa]mbio|transmiss[ãa]o", re.I)


def is_relevant(title: str) -> bool:
    if not LANCER_PATTERN.search(title):
        return False
    if not CAMBIO_PATTERN.search(title):
        return False
    if not MANUAL_PATTERN.search(title):
        return False
    if AUTOMATIC_EXCLUDE.search(title):
        return False
    return True


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def prune_state(state: dict) -> dict:
    cutoff = time.time() - SEEN_TTL_DAYS * 86400
    return {k: v for k, v in state.items() if v >= cutoff}


# ----------------------------------------------------------------------------
# Mercado Livre
# ----------------------------------------------------------------------------

def search_mercado_livre(query: str) -> list[dict]:
    url = "https://api.mercadolibre.com/sites/MLB/search"
    params = {"q": query, "limit": 50}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        print(f"[ML] query={query!r} status={resp.status_code}", file=sys.stderr)
        if resp.status_code != 200:
            print(f"[ML] corpo da resposta (primeiros 300 chars): {resp.text[:300]}", file=sys.stderr)
            return []
        data = resp.json()
    except Exception as exc:
        print(f"[ML] erro na busca {query!r}: {exc}", file=sys.stderr)
        return []

    out = []
    for it in data.get("results", []):
        title = it.get("title", "")
        if not is_relevant(title):
            continue
        address = it.get("address") or {}
        out.append({
            "id": "ml-" + str(it.get("id")),
            "title": title,
            "price": it.get("price"),
            "currency": it.get("currency_id", "BRL"),
            "url": it.get("permalink"),
            "image": (it.get("thumbnail") or "").replace("http://", "https://"),
            "condition": it.get("condition"),
            "location": ", ".join(filter(None, [address.get("city_name"), address.get("state_name")])),
            "source": "Mercado Livre",
        })
    return out


# ----------------------------------------------------------------------------
# OLX
# ----------------------------------------------------------------------------

def search_olx(query: str) -> list[dict]:
    url = "https://www.olx.com.br/brasil"
    params = {"q": query}
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        print(f"[OLX] query={query!r} status={resp.status_code} len={len(resp.text)}", file=sys.stderr)
        if resp.status_code != 200:
            print(f"[OLX] corpo da resposta (primeiros 300 chars): {resp.text[:300]}", file=sys.stderr)
            return []
        html = resp.text
    except Exception as exc:
        print(f"[OLX] erro na busca {query!r}: {exc}", file=sys.stderr)
        return []

    # O OLX (Next.js) embute os resultados da busca num <script
    # id="__NEXT_DATA__" type="application/json">...</script> no proprio
    # HTML inicial, sem precisar executar JS. A estrutura exata do JSON
    # pode mudar; por isso procuramos de forma tolerante por qualquer
    # objeto que pareca um anuncio (tem "title"/"subject" + "price" + uma
    # forma de montar a URL) em vez de depender de um caminho fixo.
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html, re.S,
    )
    if not m:
        print("[OLX] nao encontrei __NEXT_DATA__ no HTML (layout pode ter mudado)", file=sys.stderr)
        return []

    try:
        payload = json.loads(m.group(1))
    except Exception as exc:
        print(f"[OLX] __NEXT_DATA__ nao e JSON valido: {exc}", file=sys.stderr)
        return []

    ads = []
    _collect_olx_ads(payload, ads)

    out = []
    seen_ids = set()
    for ad in ads:
        title = ad.get("title") or ad.get("subject") or ""
        if not title or not is_relevant(title):
            continue
        ad_id = str(ad.get("listId") or ad.get("id") or ad.get("adId") or "")
        if not ad_id or ad_id in seen_ids:
            continue
        seen_ids.add(ad_id)
        price = _parse_olx_price(ad)
        link = ad.get("url") or ad.get("friendlyUrl") or ad.get("link")
        if link and not link.startswith("http"):
            link = "https://www.olx.com.br" + link
        image = None
        images = ad.get("images") or ad.get("thumbs") or []
        if images and isinstance(images, list):
            first = images[0]
            image = first if isinstance(first, str) else (first.get("original") or first.get("url"))
        location = ad.get("locationName") or ad.get("location") or ""
        out.append({
            "id": "olx-" + ad_id,
            "title": title,
            "price": price,
            "currency": "BRL",
            "url": link,
            "image": image,
            "condition": None,
            "location": location,
            "source": "OLX",
        })
    return out


def _collect_olx_ads(node, out: list, depth: int = 0) -> None:
    # Varredura recursiva e tolerante pelo JSON do Next.js procurando
    # listas de objetos que parecam anuncios (tem titulo e preco).
    if depth > 12:
        return
    if isinstance(node, dict):
        has_title = any(k in node for k in ("title", "subject"))
        has_price = "price" in node or "priceValue" in node
        if has_title and has_price:
            out.append(node)
            return
        for v in node.values():
            _collect_olx_ads(v, out, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _collect_olx_ads(item, out, depth + 1)


def _parse_olx_price(ad: dict) -> float | None:
    for key in ("priceValue", "price"):
        val = ad.get(key)
        if val is None:
            continue
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            digits = re.sub(r"[^\d,\.]", "", val).replace(".", "").replace(",", ".")
            try:
                return float(digits)
            except ValueError:
                continue
    return None


# ----------------------------------------------------------------------------
# Discord
# ----------------------------------------------------------------------------

def send_discord_alert(webhook_url: str, item: dict) -> None:
    price_txt = f"R$ {item['price']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if item.get("price") else "preco sob consulta"
    embed = {
        "title": item["title"][:250],
        "url": item.get("url"),
        "color": 0xB06A6A,
        "fields": [
            {"name": "Preco", "value": price_txt, "inline": True},
            {"name": "Fonte", "value": item.get("source", "—"), "inline": True},
            {"name": "Local", "value": item.get("location") or "—", "inline": True},
        ],
    }
    if item.get("image"):
        embed["thumbnail"] = {"url": item["image"]}
    payload = {"content": "🔧 Novo anuncio de cambio manual Lancer MIVEC!", "embeds": [embed]}
    resp = requests.post(webhook_url, json=payload, timeout=30)
    resp.raise_for_status()


def send_discord_summary(webhook_url: str, items: list[dict]) -> None:
    lines = []
    for it in items[:15]:
        price_txt = f"R$ {it['price']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if it.get("price") else "preco sob consulta"
        lines.append(f"• [{it['title'][:70]}]({it.get('url')}) — {price_txt} ({it.get('source')})")
    extra = len(items) - 15
    if extra > 0:
        lines.append(f"...e mais {extra} anuncio(s). Confira o site pra ver todos.")
    embed = {
        "title": f"🔧 {len(items)} anuncios novos de cambio manual Lancer MIVEC",
        "description": "\n".join(lines),
        "color": 0xB06A6A,
    }
    payload = {"embeds": [embed]}
    resp = requests.post(webhook_url, json=payload, timeout=30)
    resp.raise_for_status()


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main() -> int:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")

    all_items: dict[str, dict] = {}

    for q in ML_QUERIES:
        for it in search_mercado_livre(q):
            all_items.setdefault(it["id"], it)
        time.sleep(0.5)

    for q in OLX_QUERIES:
        for it in search_olx(q):
            all_items.setdefault(it["id"], it)
        time.sleep(0.5)

    items = list(all_items.values())
    items.sort(key=lambda e: (e["price"] is None, e["price"]))

    state = prune_state(load_state())
    now = time.time()
    new_items = []
    for it in items:
        already_seen = it["id"] in state
        state[it["id"]] = now
        if not already_seen:
            new_items.append(it)

    output = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_items": len(items),
        "items": items,
    }

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    save_state(state)

    discord_errors = 0
    if webhook_url and new_items:
        if len(new_items) > 15:
            try:
                send_discord_summary(webhook_url, new_items)
            except Exception as exc:
                discord_errors += 1
                print(f"Aviso: falha ao mandar resumo pro Discord: {exc}", file=sys.stderr)
        else:
            for it in new_items:
                try:
                    send_discord_alert(webhook_url, it)
                except Exception as exc:
                    discord_errors += 1
                    print(f"Aviso: falha ao notificar item no Discord: {exc}", file=sys.stderr)
                time.sleep(1)

    print(
        f"OK. {len(items)} anuncios encontrados, {len(new_items)} novos, "
        f"{discord_errors} erros no Discord."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
