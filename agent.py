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

# Skill mapping spesifik sesuai CV (Riyan Khamdani)
PROFILE_KEYWORDS = {
    "OpenShift / K8s": ["openshift", "kubernetes", "k8s", "gke", "podman"],
    "IaC & Automation": ["terraform", "ansible"],
    "Cloud & Linux": ["aws", "gcp", "google cloud", "alibaba cloud", "rhel", "linux"],
    "Virtualization": ["vmware", "vsphere", "nsx-t", "zerto"],
    "AI Infra / SRE": ["llm", "ai platform", "dataiku", "sre", "platform engineer", "devops"]
}

FLATTENED_KEYWORDS = [kw for group in PROFILE_KEYWORDS.values() for kw in group]

STRICT_CONTRACT_TERMS = [
    "contract", "contractor", "freelance", "hourly", 
    "retainer", "b2b", "consultant", "consulting", "part-time"
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


def is_contract(text: str) -> bool:
    lower = text.lower()
    return any(term in lower for term in STRICT_CONTRACT_TERMS)


def extract_matched_skills(text: str) -> list:
    lower = text.lower()
    matched = []
    for category, kws in PROFILE_KEYWORDS.items():
        if any(re.search(r"\b" + re.escape(kw) + r"\b", lower) for kw in kws):
            matched.append(category)
    return matched


def fetch_hn_contracts() -> list:
    jobs = []
    queries = ["Seeking Freelancer", "Who is hiring"]
    
    for q in queries:
        try:
            search_url = (
                f"https://hn.algolia.com/api/v1/search_by_date"
                f"?tags=story,author_whoishiring"
                f"&query={requests.utils.quote(q)}"
                f"&hitsPerPage=2"
            )
            res = requests.get(search_url, headers=HEADERS, timeout=15).json()
            hits = res.get("hits", [])
            if not hits:
                continue

            story_id = hits[0]["objectID"]
            comments_url = f"https://hn.algolia.com/api/v1/search?tags=comment,story_{story_id}&hitsPerPage=100"
            comments_res = requests.get(comments_url, headers=HEADERS, timeout=15).json()

            for hit in comments_res.get("hits", []):
                text = html.unescape(hit.get("comment_text", ""))
                clean_text = re.sub(r"<[^>]+>", " ", text).strip()
                upper = clean_text.upper()

                if "SEEKING WORK" in upper:
                    continue

                matched_skills = extract_matched_skills(clean_text)
                if matched_skills and is_contract(clean_text):
                    snippet = clean_text[:140].replace("\n", " ") + "..."
                    comment_id = hit.get("objectID")
                    jobs.append({
                        "source": "HackerNews (Direct)",
                        "title": snippet,
                        "company": hit.get("author", "HN Client"),
                        "url": f"https://news.ycombinator.com/item?id={comment_id}",
                        "date": hit.get("created_at", "")[:10],
                        "matches": matched_skills
                    })
        except Exception as e:
            print(f"[-] HN ({q}) error: {e}")

    print(f"[*] HN kontrak cocok CV: {len(jobs)} listing")
    return jobs


def fetch_jobicy_contracts() -> list:
    jobs = []
    try:
        url = "https://jobicy.com/api/v2/remote-jobs?count=50&tag=devops"
        res = requests.get(url, headers=HEADERS, timeout=15).json()
        job_list = res.get("jobs", [])

        for item in job_list:
            title = item.get("jobTitle", "")
            desc = item.get("jobDescription", "")
            job_type = item.get("jobType", "").lower()
            combined = f"{title} {desc} {job_type}"

            if "full-time" in job_type and not is_contract(combined):
                continue

            matched_skills = extract_matched_skills(combined)
            if matched_skills and (is_contract(job_type) or is_contract(combined)):
                jobs.append({
                    "source": "Jobicy (Contract)",
                    "title": title.strip(),
                    "company": item.get("companyName", "Tech Company").strip(),
                    "url": item.get("url"),
                    "date": item.get("pubDate", "")[:10],
                    "matches": matched_skills
                })
    except Exception as e:
        print(f"[-] Jobicy error: {e}")

    print(f"[*] Jobicy kontrak cocok CV: {len(jobs)} listing")
    return jobs


def fetch_wwr_contracts() -> list:
    jobs = []
    try:
        feed = feedparser.parse("https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss")
        for entry in feed.entries:
            title = entry.title
            summary = getattr(entry, "summary", "")
            combined = f"{title} {summary}"

            matched_skills = extract_matched_skills(combined)
            if matched_skills and is_contract(combined):
                pub_date = (
                    datetime(*entry.published_parsed[:3]).strftime("%Y-%m-%d")
                    if hasattr(entry, "published_parsed")
                    else "Recent"
                )
                jobs.append({
                    "source": "WWR (Contract)",
                    "title": title.strip(),
                    "company": getattr(entry, "author", "Company"),
                    "url": entry.link,
                    "date": pub_date,
                    "matches": matched_skills
                })
    except Exception as e:
        print(f"[-] WWR error: {e}")

    print(f"[*] WWR kontrak cocok CV: {len(jobs)} listing")
    return jobs


def main():
    seen_urls = load_seen_urls()
    print(f"[*] Total history seen URLs: {len(seen_urls)}")

    all_jobs = []
    all_jobs.extend(fetch_hn_contracts())
    all_jobs.extend(fetch_jobicy_contracts())
    all_jobs.extend(fetch_wwr_contracts())

    new_jobs = [job for job in all_jobs if job["url"] not in seen_urls]
    print(f"[*] Total listing cocok CV: {len(all_jobs)}, listing baru: {len(new_jobs)}")

    if not new_jobs:
        print("[!] Tidak ada proyek kontrak baru yang cocok dengan CV saat ini.")
        return

    top_jobs = new_jobs[:5]

    message = (
        "<b>🎯 RIYAN'S FREELANCE & CONTRACT RADAR</b>\n"
        "<i>Filter: OpenShift, K8s, Terraform, Multi-Cloud, AI Ops (Contract/B2B Only)</i>\n"
        "====================================\n\n"
    )

    for idx, job in enumerate(top_jobs, 1):
        tags_formatted = " | ".join(job["matches"])
        message += (
            f"<b>{idx}. {html.escape(job['title'])}</b>\n"
            f"🎯 <b>Match:</b> <code>{tags_formatted}</code>\n"
            f"🏢 <b>Client:</b> {html.escape(job['company'])} | <b>Via:</b> {job['source']}\n"
            f"📅 <b>Date:</b> {job['date']}\n"
            f"🔗 <a href='{job['url']}'>Direct Apply / Contact</a>\n\n"
        )
        seen_urls.add(job["url"])

    send_telegram_message(message)
    save_seen_urls(seen_urls)
    print(f"[✓] Berhasil mengirim {len(top_jobs)} peluang relevan ke Telegram.")


if __name__ == "__main__":
    main()
