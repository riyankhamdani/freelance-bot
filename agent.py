import html
import json
import os
import re
from datetime import datetime, timezone
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = "seen_urls.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

# Skill mapping spesifik sesuai profil CV Riyan Khamdani
PROFILE_KEYWORDS = {
    "OpenShift / K8s": ["openshift", "kubernetes", "k8s", "gke", "podman"],
    "IaC & Automation": ["terraform", "ansible"],
    "Cloud & Linux": ["aws", "gcp", "google cloud", "alibaba cloud", "rhel", "linux"],
    "Virtualization": ["vmware", "vsphere", "nsx-t", "zerto"],
    "AI Infra / SRE": ["llm", "ai platform", "dataiku", "sre", "platform engineer", "devops"]
}

# Wajib format kontrak murni
CONTRACT_REGEX = re.compile(
    r"\b(contract|contractor|freelance|freelancer|b2b|c2c|hourly|retainer|part-time|1099)\b", 
    re.IGNORECASE
)

# Blacklist onsite, hybrid, full-time murni, clearance, dan pembatasan region tertutup
EXCLUDE_REGEX = re.compile(
    r"\b(onsite|on-site|hybrid|in-office|w2 only|security clearance|polygraph|us citizenship required|us citizen only|remote \(india\)|remote \(us only\))\b",
    re.IGNORECASE
)


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


def extract_matched_skills(text: str) -> list:
    lower = text.lower()
    matched = []
    for category, kws in PROFILE_KEYWORDS.items():
        if any(re.search(r"\b" + re.escape(kw) + r"\b", lower) for kw in kws):
            matched.append(category)
    return matched


def is_valid_contract(text: str) -> bool:
    if EXCLUDE_REGEX.search(text):
        return False
    # Jika full-time tapi tidak ada kata kontrak eksplisit, drop
    if re.search(r"\bfull-time\b|\bfull time\b", text, re.I) and not CONTRACT_REGEX.search(text):
        return False
    return bool(CONTRACT_REGEX.search(text))


def fetch_hn_contracts() -> list:
    """100% Gratis: Direct contact founder/CTO dari Hacker News thread bulan berjalan."""
    jobs = []
    try:
        url = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=6"
        res = requests.get(url, headers=HEADERS, timeout=15).json()
        hits = res.get("hits", [])
        
        target_stories = [
            h for h in hits 
            if "hiring" in h.get("title", "").lower() or "freelancer" in h.get("title", "").lower()
        ]

        for story in target_stories[:2]:
            story_id = story["objectID"]
            comments_url = f"https://hn.algolia.com/api/v1/search?tags=comment,story_{story_id}&hitsPerPage=100"
            comments_res = requests.get(comments_url, headers=HEADERS, timeout=15).json()

            for hit in comments_res.get("hits", []):
                text = html.unescape(hit.get("comment_text", ""))
                clean_text = re.sub(r"<[^>]+>", " ", text).strip()
                upper = clean_text.upper()

                if "SEEKING WORK" in upper:
                    continue

                first_lines = " ".join(clean_text.splitlines()[:3]).upper()
                if "ONSITE" in first_lines or "HYBRID" in first_lines:
                    continue

                matched_skills = extract_matched_skills(clean_text)
                if matched_skills and is_valid_contract(clean_text):
                    snippet = clean_text[:140].replace("\n", " ") + "..."
                    jobs.append({
                        "source": "HackerNews (Direct Founder)",
                        "title": snippet,
                        "company": hit.get("author", "HN Client"),
                        "url": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                        "date": hit.get("created_at", "")[:10],
                        "matches": matched_skills
                    })
    except Exception as e:
        print(f"[-] HN error: {e}")

    print(f"[*] HN kontrak murni lolos: {len(jobs)} listing")
    return jobs


def fetch_jobicy_contracts() -> list:
    """100% Gratis: Direct ATS apply (Greenhouse/Lever) khusus lowongan kontrak/freelance."""
    jobs = []
    try:
        url = "https://jobicy.com/api/v2/remote-jobs?count=50&tag=devops"
        res = requests.get(url, headers=HEADERS, timeout=15).json()
        for item in res.get("jobs", []):
            job_type = item.get("jobType", "").lower()
            title = item.get("jobTitle", "")
            desc = item.get("jobDescription", "")
            geo = item.get("jobGeo", "").lower()
            combined = f"{title} {desc} {job_type} {geo}"

            if "full-time" in job_type:
                continue

            if ("contract" in job_type or "freelance" in job_type or CONTRACT_REGEX.search(title)) and not EXCLUDE_REGEX.search(combined):
                matched = extract_matched_skills(combined)
                if matched:
                    jobs.append({
                        "source": "Jobicy (Direct ATS)",
                        "title": title.strip(),
                        "company": item.get("companyName", "Tech Company").strip(),
                        "url": item.get("url"),
                        "date": item.get("pubDate", "")[:10],
                        "matches": matched
                    })
    except Exception as e:
        print(f"[-] Jobicy error: {e}")

    print(f"[*] Jobicy kontrak lolos: {len(jobs)} listing")
    return jobs


def main():
    seen_urls = load_seen_urls()
    print(f"[*] Total history seen URLs: {len(seen_urls)}")

    all_jobs = []
    all_jobs.extend(fetch_hn_contracts())
    all_jobs.extend(fetch_jobicy_contracts())

    new_jobs = [job for job in all_jobs if job["url"] not in seen_urls]
    print(f"[*] Total lolos kurasi: {len(all_jobs)}, listing baru: {len(new_jobs)}")

    if not new_jobs:
        print("[!] Tidak ada proyek kontrak/freelance baru yang lolos kurasi. Bot standby.")
        return

    top_jobs = new_jobs[:5]

    message = (
        "<b>⚡ STRICT FREELANCE & CONTRACT RADAR</b>\n"
        "<i>Filter: OpenShift, K8s, Terraform, Multi-Cloud (100% Free Apply & Direct)</i>\n"
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
    print(f"[✓] Berhasil mengirim {len(top_jobs)} listing valid ke Telegram.")


if __name__ == "__main__":
    main()
