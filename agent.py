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

KEYWORDS = [
    "kubernetes", "k8s", "aws", "terraform", "infra", "linux", 
    "cloud", "devops", "sre", "ci/cd", "platform", "ansible", "docker"
]
CONTRACT_TERMS = [
    "contract", "contractor", "freelance", "hourly", 
    "retainer", "b2b", "part-time", "consultant"
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
    # Simpan maksimal 2000 URL terakhir agar file tidak membengkak
    urls_to_save = list(seen_urls)[-2000:]
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
        print(f"[!] Error saat menghubungi Telegram API: {e}")


def fetch_remotive() -> list:
    jobs = []
    try:
        url = "https://remotive.com/api/remote-jobs?category=devops"
        res = requests.get(url, timeout=15).json()
        for job in res.get("jobs", []):
            job_type = job.get("job_type", "").lower()
            title = job.get("title", "")
            desc = job.get("description", "").lower()

            if "contract" in job_type or any(t in desc for t in CONTRACT_TERMS):
                if any(kw in (title.lower() + " " + desc) for kw in KEYWORDS):
                    jobs.append({
                        "source": "Remotive",
                        "title": title,
                        "company": job.get("company_name", "N/A"),
                        "url": job.get("url"),
                        "date": job.get("publication_date", "")[:10]
                    })
    except Exception as e:
        print(f"[-] Remotive fetch error: {e}")
    return jobs


def fetch_wwr() -> list:
    jobs = []
    try:
        url = "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss"
        feed = feedparser.parse(url)
        for entry in feed.entries:
            content = (entry.title + " " + getattr(entry, "summary", "")).lower()
            if any(term in content for term in CONTRACT_TERMS):
                if any(kw in content for kw in KEYWORDS):
                    pub_date = (
                        datetime(*entry.published_parsed[:3]).strftime("%Y-%m-%d")
                        if hasattr(entry, "published_parsed")
                        else "Recent"
                    )
                    jobs.append({
                        "source": "WWR",
                        "title": entry.title,
                        "company": getattr(entry, "author", "N/A"),
                        "url": entry.link,
                        "date": pub_date
                    })
    except Exception as e:
        print(f"[-] WWR fetch error: {e}")
    return jobs


def fetch_hn_freelance() -> list:
    jobs = []
    try:
        search_url = "https://hn.algolia.com/api/v1/search?query=Ask%20HN%20Seeking%20Freelancer&tags=story&hitsPerPage=1"
        res = requests.get(search_url, timeout=15).json()
        if not res.get("hits"):
            return jobs

        story_id = res["hits"][0]["objectID"]
        comments_url = f"https://hn.algolia.com/api/v1/search?tags=comment,story_{story_id}&hitsPerPage=50"
        comments_res = requests.get(comments_url, timeout=15).json()

        for hit in comments_res.get("hits", []):
            text = html.unescape(hit.get("comment_text", ""))
            clean_text = re.sub(r"<[^>]+>", " ", text).strip()
            lower = clean_text.lower()

            if any(kw in lower for kw in KEYWORDS) and any(term in lower for term in ["remote", "contract", "freelance"]):
                snippet = clean_text[:160].replace("\n", " ") + "..."
                comment_id = hit.get("objectID")
                jobs.append({
                    "source": "HackerNews",
                    "title": snippet,
                    "company": hit.get("author", "HN User"),
                    "url": f"https://news.ycombinator.com/item?id={comment_id}",
                    "date": hit.get("created_at", "")[:10]
                })
    except Exception as e:
        print(f"[-] HN fetch error: {e}")
    return jobs


def main():
    seen_urls = load_seen_urls()
    print(f"[*] Total history seen URLs: {len(seen_urls)}")

    all_jobs = []
    all_jobs.extend(fetch_remotive())
    all_jobs.extend(fetch_wwr())
    all_jobs.extend(fetch_hn_freelance())

    # Filter hanya yang belum pernah dikirim
    new_jobs = [job for job in all_jobs if job["url"] not in seen_urls]
    print(f"[*] Ditemukan {len(all_jobs)} peluang, {len(new_jobs)} listing baru.")

    if not new_jobs:
        print("[!] Tidak ada listing kontrak baru untuk dikirim.")
        return

    header = (
        "<b>🛠️ FREELANCE / CONTRACT INFRA DIGEST</b>\n"
        f"<i>Scope: DevOps, SRE, Cloud, Kubernetes, IaC</i>\n"
        f"<i>New Gigs: {len(new_jobs)} peluang baru</i>\n"
        "====================================\n\n"
    )

    messages = []
    current_msg = header

    for idx, job in enumerate(new_jobs, 1):
        item_text = (
            f"<b>{idx}. {html.escape(job['title'])}</b>\n"
            f"🏢 <b>Client:</b> {html.escape(job['company'])} | <b>Via:</b> {job['source']}\n"
            f"📅 <b>Date:</b> {job['date']}\n"
            f"🔗 <a href='{job['url']}'>Tap to View / Apply</a>\n\n"
        )

        if len(current_msg) + len(item_text) > 3800:
            messages.append(current_msg)
            current_msg = item_text
        else:
            current_msg += item_text

        seen_urls.add(job["url"])

    if current_msg:
        messages.append(current_msg)

    for msg in messages:
        send_telegram_message(msg)

    # Simpan update seen_urls agar di-push oleh actions
    save_seen_urls(seen_urls)
    print(f"[✓] Berhasil mengirim {len(messages)} pesan dan update state.")


if __name__ == "__main__":
    main()
