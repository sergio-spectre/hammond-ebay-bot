#!/usr/bin/env python3
"""
Monitor de precos: eBay "Hammond Collection" (Jurassic World, Mattel) -> Discord

O que faz:
  1. Autentica na eBay Browse API (OAuth client credentials).
  2. Busca anuncios ativos para a linha "Hammond Collection" da Mattel.
  3. Descarta anuncios com preco muito acima da mediana (outliers).
  4. Compara com os anuncios ja notificados (seen_items.json).
  5. Envia os anuncios novos (e nao-outliers) para um webhook do Discord.
  6. Atualiza seen_items.json (o workflow do GitHub Actions faz o commit de volta).

Variaveis de ambiente esperadas:
  EBAY_CLIENT_ID       - Client ID (App ID) do eBay Developer Program
  EBAY_CLIENT_SECRET   - Client Secret (Cert ID) do eBay Developer Program
  DISCORD_WEBHOOK_URL  - URL do webhook do canal do Discord

Ajustes rapidos ficam nas constantes logo abaixo.
"""

import base64
import json
import os
import statistics
import sys
import time
from pathlib import Path

import requests

# ----------------------------------------------------------------------------
# Configuracao
# ----------------------------------------------------------------------------

SEARCH_QUERY = "Jurassic World Hammond Collection"
MARKETPLACE_ID = "EBAY_US"          # troque para EBAY_GB, EBAY_DE, etc. se quiser outro site do eBay
RESULTS_LIMIT = 50                  # max 200 por chamada na Browse API
SORT = "newlyListed"                # prioriza os anuncios mais recentes

# Um anuncio e considerado "outlier" (preco muito acima dos outros) e NAO e
# enviado se o preco dele for maior que OUTLIER_MULTIPLIER vezes a mediana
# dos precos encontrados nesta mesma busca.
OUTLIER_MULTIPLIER = 1.5

# Por quantos dias mantemos um item na lista de "ja visto" antes de esquecer
# dele (evita que seen_items.json cresca para sempre).
SEEN_TTL_DAYS = 45

STATE_FILE = Path(__file__).parent / "seen_items.json"

EBAY_OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"


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


def search_ebay(token: str) -> list[dict]:
    resp = requests.get(
        EBAY_SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
        },
        params={
            "q": SEARCH_QUERY,
            "limit": str(RESULTS_LIMIT),
            "sort": SORT,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("itemSummaries", [])


# ----------------------------------------------------------------------------
# Estado (itens ja notificados)
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

def send_discord_alert(webhook_url: str, item: dict) -> None:
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
    # Discord manda 429 com Retry-After quando o rate limit estoura
    if resp.status_code == 429:
        retry_after = resp.json().get("retry_after", 1)
        time.sleep(float(retry_after) + 0.5)
        resp = requests.post(webhook_url, json=payload, timeout=30)
    resp.raise_for_status()


def send_discord_text(webhook_url: str, content: str) -> None:
    resp = requests.post(webhook_url, json={"content": content}, timeout=30)
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
            ("DISCORD_WEBHOOK_URL", webhook_url),
        ]
        if not val
    ]
    if missing:
        print(f"Faltam variaveis de ambiente: {', '.join(missing)}", file=sys.stderr)
        return 1

    token = get_ebay_token(client_id, client_secret)
    items = search_ebay(token)

    if not items:
        print("Nenhum anuncio encontrado nesta busca.")
        return 0

    prices = []
    for it in items:
        try:
            prices.append(float(it["price"]["value"]))
        except (KeyError, TypeError, ValueError):
            continue

    median_price = statistics.median(prices) if prices else None
    outlier_ceiling = median_price * OUTLIER_MULTIPLIER if median_price else None

    state = prune_state(load_state())

    new_count = 0
    skipped_outliers = 0
    now = time.time()

    for it in items:
        item_id = it.get("itemId")
        if not item_id:
            continue

        # marca como visto de qualquer forma (mesmo se for outlier, nao
        # queremos alertar sobre ele mais tarde se o preco nao mudar)
        already_seen = item_id in state
        state[item_id] = now

        if already_seen:
            continue

        try:
            price_val = float(it["price"]["value"])
        except (KeyError, TypeError, ValueError):
            price_val = None

        if outlier_ceiling is not None and price_val is not None and price_val > outlier_ceiling:
            skipped_outliers += 1
            print(f"Ignorando outlier: {it.get('title')} ({price_val})")
            continue

        send_discord_alert(webhook_url, it)
        new_count += 1
        time.sleep(1)  # nao martelar o webhook

    save_state(state)

    print(
        f"OK. {len(items)} anuncios verificados, {new_count} novos enviados, "
        f"{skipped_outliers} outliers ignorados, mediana={median_price}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
