#!/usr/bin/env python3
"""
Monitor de precos: eBay "Hammond Collection" (Jurassic World, Mattel) -> Discord + site

O que faz:
  1. Autentica na eBay Browse API (OAuth client credentials).
  2. Busca anuncios ativos para a linha "Hammond Collection" da Mattel.
  3. Classifica cada anuncio por especie de dinossauro (Rex, Raptor, etc.),
     e separa lotes/leiloes/anuncios com mais de um bicho num tab proprio.
  4. Ordena cada grupo do menor pro maior preco.
  5. Descarta anuncios com preco muito acima da mediana (outliers).
  6. Salva tudo em data/listings.json (o site le esse arquivo).
  7. Compara com os anuncios ja notificados (seen_items.json) e manda os
     anuncios novos (e nao-outliers) para um webhook do Discord.

Variaveis de ambiente esperadas:
  EBAY_CLIENT_ID       - Client ID (App ID) do eBay Developer Program (Production)
  EBAY_CLIENT_SECRET   - Client Secret (Cert ID) do eBay Developer Program (Production)
  DISCORD_WEBHOOK_URL  - URL do webhook do canal do Discord (opcional: se nao
                          estiver definida, o script so atualiza o site e pula
                          o Discord)
"""

import base64
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

import requests

# ----------------------------------------------------------------------------
# Configuracao
# ----------------------------------------------------------------------------

SEARCH_QUERIES = [
    "Jurassic World Hammond Collection",
]
MARKETPLACE_ID = "EBAY_US"
RESULTS_LIMIT = 200          # max por chamada na Browse API
SORT = "newlyListed"

OUTLIER_MULTIPLIER = 1.5
SEEN_TTL_DAYS = 45

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "seen_items.json"
DATA_FILE = ROOT / "data" / "listings.json"

EBAY_OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

# Especies/tabs. A ordem aqui define a ordem das abas no site.
SPECIES = [
    ("T-Rex", re.compile(r"\bt-?\s?rex\b|tyrannosaurus", re.I)),
    ("Raptor", re.compile(r"raptor", re.I)),  # velociraptor, indoraptor, atrociraptor, blue, charlie, delta, echo...
    ("Triceratops", re.compile(r"triceratops", re.I)),
    ("Spinosaurus", re.compile(r"spinosaurus", re.I)),
    ("Stegosaurus", re.compile(r"stegosaurus", re.I)),
    ("Brachiosaurus", re.compile(r"brachiosaurus", re.I)),
    ("Mosasaurus", re.compile(r"mosasaurus", re.I)),
    ("Pteranodon", re.compile(r"pteranodon", re.I)),
    ("Indominus Rex", re.compile(r"indominus", re.I)),
    ("Parasaurolophus", re.compile(r"parasaurolophus", re.I)),
    ("Ankylosaurus", re.compile(r"ankylosaurus", re.I)),
    ("Dilophosaurus", re.compile(r"dilophosaurus", re.I)),
    ("Giganotosaurus", re.compile(r"giganotosaurus", re.I)),
    ("Carnotaurus", re.compile(r"carnotaurus", re.I)),
    ("Baryonyx", re.compile(r"baryonyx", re.I)),
    ("Allosaurus", re.compile(r"allosaurus", re.I)),
    ("Pachycephalosaurus", re.compile(r"pachycephalosaurus", re.I)),
    ("Compsognathus", re.compile(r"compsognathus", re.I)),
]

LOT_PATTERN = re.compile(
    r"\blot\b|\blote\b|\bbundle\b|\bset of\b|\bx\s?\d\b|\d\s?x\b|\bcollection of\b",
    re.I,
)


# ----------------------------------------------------------------------------
# eBay
# ----------------------------------------------------------------------------

def get_ebay_token(client_id: str, client_secret: str) -> str:
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        EBAY_OAUTH_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def search_ebay(token: str, query: str) -> list[dict]:
    items = []
    offset = 0
    while True:
        resp = requests.get(
            EBAY_SEARCH_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
            },
            params={
                "q": query,
                "limit": str(min(RESULTS_LIMIT, 200)),
                "offset": str(offset),
                "sort": SORT,
            },
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        batch = payload.get("itemSummaries", [])
        items.extend(batch)
        total = payload.get("total", 0)
        offset += len(batch)
        if not batch or offset >= total or offset >= RESULTS_LIMIT:
            break
    return items


# ----------------------------------------------------------------------------
# Classificacao
# ----------------------------------------------------------------------------

def classify(title: str, buying_options: list[str]) -> str:
    matches = [name for name, pattern in SPECIES if pattern.search(title)]
    is_auction = "AUCTION" in (buying_options or [])
    is_lot_text = bool(LOT_PATTERN.search(title))
    if is_auction or is_lot_text or len(matches) > 1:
        return "Lotes e Leilao"
    if len(matches) == 1:
        return matches[0]
    return "Outros"


def item_price(it: dict):
    try:
        return float(it["price"]["value"])
    except (KeyError, TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------
# Estado (itens ja notificados no Discord)
# ----------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True))


def prune_state(state: dict) -> dict:
    cutoff = time.time() - SEEN_TTL_DAYS * 86400
    return {item_id: ts for item_id, ts in state.items() if ts >= cutoff}


# ----------------------------------------------------------------------------
# Discord
# ----------------------------------------------------------------------------

def send_discord_alert(webhook_url: str, item: dict, category: str) -> None:
    title = item.get("title", "Anuncio sem titulo")
    price = item.get("price", {})
    price_str = f"{price.get('value', '?')} {price.get('currency', '')}".strip()
    url = item.get("itemWebUrl", "")
    condition = item.get("condition", "")
    image = (item.get("image") or {}).get("imageUrl")
    seller = (item.get("seller") or {}).get("username", "desconhecido")

    embed = {
        "title": title[:256],
        "url": url,
        "color": 0x2ECC71,
        "fields": [
            {"name": "Categoria", "value": category, "inline": True},
            {"name": "Preco", "value": price_str or "N/A", "inline": True},
            {"name": "Condicao", "value": condition or "N/A", "inline": True},
            {"name": "Vendedor", "value": seller, "inline": True},
        ],
    }
    if image:
        embed["thumbnail"] = {"url": image}

    payload = {
        "content": "Novo anuncio de Hammond Collection no eBay!",
        "embeds": [embed],
    }

    resp = requests.post(webhook_url, json=payload, timeout=30)
    if resp.status_code == 429:
        retry_after = resp.json().get("retry_after", 1)
        time.sleep(float(retry_after) + 0.5)
        resp = requests.post(webhook_url, json=payload, timeout=30)
    resp.raise_for_status()


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main() -> int:
    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")

    missing = [
        name
        for name, val in [
            ("EBAY_CLIENT_ID", client_id),
            ("EBAY_CLIENT_SECRET", client_secret),
        ]
        if not val
    ]
    if missing:
        print(f"Faltam variaveis de ambiente: {', '.join(missing)}", file=sys.stderr)
        return 1

    token = get_ebay_token(client_id, client_secret)

    all_items = {}
    for q in SEARCH_QUERIES:
        for it in search_ebay(token, q):
            item_id = it.get("itemId")
            if item_id:
                all_items[item_id] = it
    items = list(all_items.values())

    if not items:
        print("Nenhum anuncio encontrado nesta busca.")
        return 0

    prices = [p for p in (item_price(it) for it in items) if p is not None]
    median_price = statistics.median(prices) if prices else None
    outlier_ceiling = median_price * OUTLIER_MULTIPLIER if median_price else None

    state = prune_state(load_state())
    now = time.time()

    groups: dict[str, list[dict]] = {}
    new_count = 0
    skipped_outliers = 0

    for it in items:
        item_id = it.get("itemId")
        if not item_id:
            continue

        price_val = item_price(it)
        if outlier_ceiling is not None and price_val is not None and price_val > outlier_ceiling:
            skipped_outliers += 1
            continue

        title = it.get("title", "")
        buying_options = it.get("buyingOptions", [])
        category = classify(title, buying_options)

        price = it.get("price", {})
        entry = {
            "id": item_id,
            "title": title,
            "price": price_val,
            "currency": price.get("currency"),
            "url": it.get("itemWebUrl"),
            "image": (it.get("image") or {}).get("imageUrl"),
            "condition": it.get("condition"),
            "seller": (it.get("seller") or {}).get("username"),
            "buyingOptions": buying_options,
        }
        groups.setdefault(category, []).append(entry)

        already_seen = item_id in state
        state[item_id] = now
        if already_seen:
            continue

        if webhook_url:
            send_discord_alert(webhook_url, it, category)
            time.sleep(1)
        new_count += 1

    for cat in groups:
        groups[cat].sort(key=lambda e: (e["price"] is None, e["price"]))

    # ordem fixa das abas: especies na ordem definida, "Outros" e "Lotes e
    # Leilao" por ultimo
    ordered_categories = [name for name, _ in SPECIES if name in groups]
    for extra in ("Outros", "Lotes e Leilao"):
        if extra in groups:
            ordered_categories.append(extra)

    output = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "median_price": median_price,
        "total_items": sum(len(v) for v in groups.values()),
        "categories": [
            {"name": cat, "items": groups[cat]} for cat in ordered_categories
        ],
    }

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False))

    save_state(state)

    print(
        f"OK. {len(items)} anuncios verificados, {new_count} novos, "
        f"{skipped_outliers} outliers ignorados, mediana={median_price}. "
        f"Categorias: {', '.join(ordered_categories)}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
