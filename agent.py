import html
import json
import os
import re
from datetime import datetime
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = "seen_urls.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

KEYWORDS = [
    "kubernetes", "k8s", "openshift", "ceph", "terraform",
    "aws", "sre", "devops", "platform engineer", "infrastructure", "linux"
]
CONTRACT_TERMS = [
    "contract", "contractor", "freelance", "hourly", 
    "retainer", "b2b", "consultant", "part-time"
]


def load_seen_urls() -> set:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"[!] Gagal membaca {STATE_FILE}: {e}")
    return set()


def save_seen_urls(seen_urls: set):
    urls_to_save = list(seen_urls)[-1000:]
    with open(STATE_FILE, "w") as f:
        json.dump(urls_to_save, f, indent=2)


def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum diset.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code != 200:
            print(f"[!] Gagal kirim Telegram: {resp.text}")
    except Exception as e:
        print(f"[!] Error Telegram API: {e}")


def fetch_hn_freelance() -> list:
    """100% Gratis: Kontak direct ke email/web founder di Hacker News."""
    jobs = []
    try:
        search_url = (
            "https://hn.algolia.com/api/v1/search_by_date"
            "?query=Ask%20HN%20Seeking%20Freelancer"
            "&tags=story"
            "&hitsPerPage=10"
        )
        res = requests.get(search_url, headers=HEADERS, timeout=15).json()
        if not res.get("hits"):
            return jobs

        story = None
        for hit in res.get("hits", []):
            t = hit.get("title", "").lower()
            if "seeking freelancer" in t or "freelancer?" in t:
                story = hit
                break

        if not story:
            return jobs

        story_id = story["objectID"]
        comments_url = f"https://hn.algolia.com/api/v1/search?tags=comment,story_{story_id}&hitsPerPage=100"
        comments_res = requests.get(comments_url, headers=HEADERS, timeout=15).json()

        for hit in comments_res.get("hits", []):
            text = html.unescape(hit.get("comment_text", ""))
            clean_text = re.sub(r"<[^>]+>", " ", text).strip()
            upper_text = clean_text.upper()

            # Hanya ambil post klien/hiring
            if "SEEKING WORK" in upper_text or "SEEKING FREELANCER" not in upper_text:
                continue

            lower_text = clean_text.lower()
            if any(kw in lower_text for kw in KEYWORDS):
                snippet = clean_text[:140].replace("\n", " ") + "..."
                comment_id = hit.get("objectID")
                jobs.append({
                    "source": "HackerNews (Direct)",
                    "title": snippet,
                    "company": hit.get("author", "HN Client"),
                    "url": f"https://news.ycombinator.com/item?id={comment_id}",
                    "date": hit.get("created_at", "")[:10]
                })
    except Exception as e:
        print(f"[-] HN error: {e}")
    return jobs


def fetch_remoteok_devops() -> list:
    """100% Gratis: Endpoint publik RemoteOK langsung ke ATS perusahaan."""
    jobs = []
    try:
        url = "https://remoteok.com/api?tag=devops"
        res = requests.get(url, headers=HEADERS, timeout=15).json()
        # Elemen pertama remoteok API adalah notice/disclaimer, skip res[0]
        for item in res[1:]:
            title = item.get("position", "")
            company = item.get("company", "Company")
            desc = item.get("description", "").lower()
            tags = " ".join(item.get("tags", [])).lower()
            combined = f"{title.lower()} {desc} {tags}"

            # Filter kontrak/freelance & keyword infra
            is_contract = any(t in combined for t in CONTRACT_TERMS) or "contract" in tags
            has_infra = any(kw in combined for kw in KEYWORDS)

            if is_contract and has_infra:
                # URL apply langsung (bukan lewat paywall)
                apply_link = item.get("apply_url") or item.get("url")
                if apply_link:
                    pub_date = item.get("date", "")[:10]
                    jobs.append({
                        "source": "RemoteOK (Direct ATS)",
                        "title": title.strip(),
                        "company": company.strip(),
                        "url": apply_link,
                        "date": pub_date
                    })
    except Exception as e:
        print(f"[-] RemoteOK error: {e}")
    return jobs


def fetch_reddit_gigs() -> list:
    """100% Gratis: Lowongan freelance r/forhire langsung ke klien."""
    jobs = []
    try:
        url = "https://www.reddit.com/r/forhire/new.json?limit=30"
        res = requests.get(url, headers=HEADERS, timeout=15).json()
        posts = res.get("data", {}).get("children", [])

        for post in posts:
            data = post.get("data", {})
            title = data.get("title", "")
            selftext = data.get("selftext", "").lower()
            combined = f"{title.lower()} {selftext}"

            # Pastikan hanya thread klien [Hiring], bukan [For Hire]
            if "[hiring]" in title.lower():
                if any(kw in combined for kw in KEYWORDS):
                    permalink = f"https://reddit.com{data.get('permalink')}"
                    created_date = datetime.fromtimestamp(data.get("created_utc", 0)).strftime("%Y-%m-%d")
                    jobs.append({
                        "source": "Reddit r/forhire",
                        "title": title.replace("[Hiring]", "").replace("[HIRING]", "").strip()[:140],
                        "company": f"u/{data.get('author')}",
                        "url": permalink,
                        "date": created_date
                    })
    except Exception as e:
        print(f"[-] Reddit error: {e}")
    return jobs


def main():
    seen_urls = load_seen_urls()
    print(f"[*] Total history seen URLs: {len(seen_urls)}")

    all_jobs = []
    all_jobs.extend(fetch_hn_freelance())
    all_jobs.extend(fetch_remoteok_devops())
    all_jobs.extend(fetch_reddit_gigs())

    # Filter URL yang belum pernah dikirim
    new_jobs = [job for job in all_jobs if job["url"] not in seen_urls]
    print(f"[*] Ditemukan {len(all_jobs)} peluang, {len(new_jobs)} listing baru.")

    if not new_jobs:
        print("[!] Tidak ada listing kontrak baru hari ini.")
        return

    # Ambil maksimal 5 listing teratas
    top_jobs = new_jobs[:5]

    message = (
        "<b>🛠️ TOP 5 FREELANCE INFRA GIGS (FREE TO APPLY)</b>\n"
        f"<i>Target: Kubernetes, OpenShift, Ceph, Cloud/SRE</i>\n"
        "====================================\n\n"
    )

    for idx, job in enumerate(top_jobs, 1):
        message += (
            f"<b>{idx}. {html.escape(job['title'])}</b>\n"
            f"🏢 <b>Client:</b> {html.escape(job['company'])} | <b>Via:</b> {job['source']}\n"
            f"📅 <b>Date:</b> {job['date']}\n"
            f"🔗 <a href='{job['url']}'>Direct Apply / Contact</a>\n\n"
        )
        seen_urls.add(job["url"])

    send_telegram_message(message)
    save_seen_urls(seen_urls)
    print(f"[✓] Berhasil mengirim {len(top_jobs)} listing dalam 1 chat bubble.")


if __name__ == "__main__":
    main()
