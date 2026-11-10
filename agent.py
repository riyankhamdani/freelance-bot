import html
import json
import os
import re
from datetime import datetime
import feedparser
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = "seen_urls.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

INFRA_KEYWORDS = [
    "kubernetes", "k8s", "openshift", "ceph", "terraform",
    "aws", "sre", "devops", "platform engineer", "infrastructure", "linux", "cloud"
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
    """Ambil thread bulanan 'Seeking Freelancer' resmi dari akun whoishiring."""
    jobs = []
    try:
        search_url = (
            "https://hn.algolia.com/api/v1/search_by_date"
            "?tags=story,author_whoishiring"
            "&query=freelancer"
            "&hitsPerPage=3"
        )
        res = requests.get(search_url, headers=HEADERS, timeout=15).json()
        hits = res.get("hits", [])
        if not hits:
            return jobs

        story_id = hits[0]["objectID"]
        comments_url = f"https://hn.algolia.com/api/v1/search?tags=comment,story_{story_id}&hitsPerPage=100"
        comments_res = requests.get(comments_url, headers=HEADERS, timeout=15).json()

        for hit in comments_res.get("hits", []):
            text = html.unescape(hit.get("comment_text", ""))
            clean_text = re.sub(r"<[^>]+>", " ", text).strip()
            upper_text = clean_text.upper()

            # Tolak pencari kerja, ambil postingan klien hiring
            if "SEEKING WORK" in upper_text or "SEEKING FREELANCER" not in upper_text:
                continue

            lower_text = clean_text.lower()
            if any(kw in lower_text for kw in INFRA_KEYWORDS):
                snippet = clean_text[:140].replace("\n", " ") + "..."
                comment_id = hit.get("objectID")
                jobs.append({
                    "source": "HackerNews (Direct Founder)",
                    "title": snippet,
                    "company": hit.get("author", "HN Client"),
                    "url": f"https://news.ycombinator.com/item?id={comment_id}",
                    "date": hit.get("created_at", "")[:10]
                })
    except Exception as e:
        print(f"[-] HN error: {e}")
    return jobs


def fetch_jobicy() -> list:
    """100% Gratis & Ramah Bot: API DevOps resmi dari Jobicy langsung ke portal apply."""
    jobs = []
    try:
        url = "https://jobicy.com/api/v2/remote-jobs?count=50&industry=devops"
        res = requests.get(url, headers=HEADERS, timeout=15).json()
        job_list = res.get("jobs", [])

        for item in job_list:
            title = item.get("jobTitle", "")
            desc = item.get("jobDescription", "").lower()
            job_type = item.get("jobType", "").lower()
            combined = f"{title.lower()} {desc} {job_type}"

            # Filter kontrak / freelance dan keyword infra
            is_contract = any(t in combined for t in CONTRACT_TERMS) or "contract" in job_type
            has_infra = any(kw in combined for kw in INFRA_KEYWORDS)

            if is_contract and has_infra:
                jobs.append({
                    "source": "Jobicy (Direct Apply)",
                    "title": title.strip(),
                    "company": item.get("companyName", "Tech Company").strip(),
                    "url": item.get("url"),
                    "date": item.get("pubDate", "")[:10]
                })
    except Exception as e:
        print(f"[-] Jobicy error: {e}")
    return jobs


def fetch_himalayas() -> list:
    """RSS Himalayas langsung menuju sistem ATS perusahaan."""
    jobs = []
    try:
        feed = feedparser.parse("https://himalayas.app/jobs/rss")
        for entry in feed.entries:
            title = entry.title.lower()
            summary = getattr(entry, "summary", "").lower()
            combined = f"{title} {summary}"

            has_infra = any(kw in combined for kw in INFRA_KEYWORDS)
            is_contract = any(t in combined for t in CONTRACT_TERMS)

            if has_infra and is_contract:
                pub_date = (
                    datetime(*entry.published_parsed[:3]).strftime("%Y-%m-%d")
                    if hasattr(entry, "published_parsed")
                    else "Recent"
                )
                jobs.append({
                    "source": "Himalayas (Direct ATS)",
                    "title": entry.title.strip(),
                    "company": getattr(entry, "author", "Company"),
                    "url": entry.link,
                    "date": pub_date
                })
    except Exception as e:
        print(f"[-] Himalayas error: {e}")
    return jobs


def main():
    seen_urls = load_seen_urls()
    print(f"[*] Total history seen URLs: {len(seen_urls)}")

    all_jobs = []
    all_jobs.extend(fetch_hn_freelance())
    all_jobs.extend(fetch_jobicy())
    all_jobs.extend(fetch_himalayas())

    new_jobs = [job for job in all_jobs if job["url"] not in seen_urls]
    print(f"[*] Ditemukan {len(all_jobs)} peluang, {len(new_jobs)} listing baru lolos filter.")

    if not new_jobs:
        print("[!] Tidak ada listing kontrak baru hari ini.")
        return

    # Ambil maksimal 5 listing teratas
    top_jobs = new_jobs[:5]

    message = (
        "<b>🛠️ TOP 5 FREELANCE INFRA GIGS (FREE APPLY)</b>\n"
        f"<i>Scope: Kubernetes, OpenShift, Ceph, Cloud/SRE</i>\n"
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
    print(f"[✓] Berhasil mengirim {len(top_jobs)} listing dalam 1 chat.")


if __name__ == "__main__":
    main()
