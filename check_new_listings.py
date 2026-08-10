import os
import re
import time
from playwright.sync_api import sync_playwright
import requests

DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

SEARCHES = [
    {
        "name": "It Takes Two Xbox",
        "search_text": "It Takes Two Xbox",
        "must_contain": ["it takes two"],
        "must_exclude": [
            "ps4", "ps5", "playstation", "switch", "nintendo", "pc", "steam", "epic",
            "code", "dematerialise", "dématérialisé", "digital", "telechargement", "téléchargement",
            "cle", "clé", "key",
        ],
        "must_contain_platform": ["xbox"],
        "price_max": 20,
        "color": 0x107C10,
    },
]

def is_relevant(title, must_contain, must_exclude, must_contain_platform):
    if title is None:
        return False
    lowered = title.lower()

    for bad_word in must_exclude:
        if bad_word in lowered:
            return False

    has_keyword = any(keyword in lowered for keyword in must_contain)
    if not has_keyword:
        return False

    has_platform = any(platform in lowered for platform in must_contain_platform)
    if not has_platform:
        return False

    return True

def extract_price_value(price_text):
    if price_text is None:
        return None
    match = re.search(r"([0-9]+[.,][0-9]{1,2})", price_text)
    if match:
        return float(match.group(1).replace(",", "."))
    match_int = re.search(r"([0-9]+)\s*\u20ac", price_text)
    if match_int:
        return float(match_int.group(1))
    return None

def send_discord_embed(title, url, price, image_url, source_name, source_color):
    embed = {
        "title": title[0:250],
        "url": url,
        "color": source_color,
        "description": "Prix : **" + str(price) + "**",
        "thumbnail": {"url": image_url} if image_url else {},
        "footer": {"text": "Alerte " + source_name},
        "fields": [
            {"name": "Lien direct", "value": "[Voir l\u2019annonce](" + url + ")", "inline": False}
        ]
    }
    payload = {"embeds": [embed]}
    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    if resp.status_code == 429:
        retry_after = resp.json().get("retry_after", 2)
        time.sleep(retry_after + 0.5)
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()
    time.sleep(1.5)

def accept_cookies(page):
    selectors = ["#didomi-notice-agree-button", "button[data-testid=cookie-banner-accept]"]
    for selector in selectors:
        try:
            page.click(selector, timeout=3000)
            return
        except Exception:
            pass
    texts = ["Accepter", "Tout accepter", "OK pour moi"]
    for text in texts:
        try:
            page.get_by_text(text).first.click(timeout=3000)
            return
        except Exception:
            pass

def check_vinted_search(page, search_config):
    search_text_encoded = search_config["search_text"].replace(" ", "+")
    url = "https://www.vinted.fr/catalog?search_text=" + search_text_encoded + "&order=newest_first"
    fresh = []
    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        accept_cookies(page)
        page.wait_for_selector("[data-testid=grid-item]", timeout=15000)
        items = page.locator("[data-testid=grid-item]").all()

        for item in items[0:30]:
            try:
                link_el = item.locator("a").first
                href = link_el.get_attribute("href")
                title = link_el.get_attribute("title")
                if href is None or not is_relevant(
                    title,
                    search_config["must_contain"],
                    search_config["must_exclude"],
                    search_config["must_contain_platform"],
                ):
                    continue

                price = "Prix non trouve"
                try:
                    price_el = item.locator("[data-testid$=price-text], p").first
                    price = price_el.inner_text().strip()
                except Exception:
                    pass

                price_value = extract_price_value(price)
                price_max = search_config.get("price_max")
                if price_max is not None:
                    if price_value is None or price_value > price_max:
                        continue

                image_url = None
                try:
                    img_el = item.locator("img").first
                    image_url = img_el.get_attribute("src")
                except Exception:
                    pass

                full_url = href if href.startswith("http") else "https://www.vinted.fr" + href

                fresh.append({
                    "key": full_url,
                    "title": str(title),
                    "url": full_url,
                    "price": price,
                    "image": image_url,
                    "source": search_config["name"] + " (Vinted)",
                    "color": search_config["color"],
                })
            except Exception:
                continue

    except Exception as e:
        print("ERROR Vinted " + search_config["name"] + ": " + str(e))
    finally:
        try:
            debug_name = "debug_vinted_" + search_config["name"].lower().replace(" ", "_") + ".html"
            with open(debug_name, "w", encoding="utf-8") as f:
                f.write(page.content())
        except Exception:
            pass

    return fresh

def load_seen(filename):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                return set(content.split(chr(10)))
    return set()

def save_seen(filename, seen_set):
    with open(filename, "w", encoding="utf-8") as f:
        f.write(chr(10).join(seen_set))

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT)

        all_results = []
        for search_config in SEARCHES:
            results = check_vinted_search(page, search_config)
            print(search_config["name"] + " pertinents trouves: " + str(len(results)))
            all_results.append((search_config, results))

        browser.close()

    for search_config, results in all_results:
        seen_file = "seen_vinted_" + search_config["name"].lower().replace(" ", "_") + ".txt"
        seen = load_seen(seen_file)
        new_items = [r for r in results if r["key"] not in seen]

        for listing in new_items:
            send_discord_embed(listing["title"], listing["url"], listing["price"], listing["image"], listing["source"], listing["color"])
            print("Alerte " + search_config["name"] + ": " + listing["title"])

        all_keys = set(r["key"] for r in results)
        save_seen(seen_file, all_keys | seen)

if __name__ == "__main__":
    main()
