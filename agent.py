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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

# Role WAJIB di Judul / Header baris pertama
INFRA_ROLE_REGEX = re.compile(
    r"\b(devops|sre|site reliability|platform engineer|infrastructure|infra|kubernetes|k8s|cloud engineer|cloud architect|systems engineer|sysadmin|openshift|ceph)\b",
    re.IGNORECASE
)

# Blacklist role non-infra
EXCLUDE_ROLES_REGEX = re.compile(
    r"\b(business development|sales|account executive|marketing|recruiter|product manager|frontend|react|mobile|ios|android|designer|ui/ux|llvm|fuzzing|commission-only|equity only|unpaid)\b",
    re.IGNORECASE
)

# Blacklist regional tertutup (US, UK, EU, Canada, India, dll)
GEO_EXCLUDE_REGEX = re.compile(
    r"(?:remote\s*[-–(:]\s*(?:us|usa|u\.s\.|uk|eu|europe|canada|india|emea|latam|americas|uk\s+or\s+eu|eu\s+or\s+uk)[^)\n]*\)?|"
    r"\b(?:us|usa|uk|eu|canada|india)\s+only\b|"
    r"\b(?:us|usa|uk|eu)\s+citizens?\b|"
    r"\bbased\s+in\s+(?:the\s+)?(?:us|usa|uk|eu|europe)\b)",
    re.IGNORECASE
)

# Skill mapping spesifik CV Riyan Khamdani
PROFILE_KEYWORDS = {
    "OpenShift / K8s": ["openshift", "kubernetes", "k8s", "gke", "podman"],
    "IaC & Automation": ["terraform", "ansible"],
    "Cloud & Linux": ["aws", "gcp", "google cloud", "alibaba cloud", "rhel", "linux"],
    "Virtualization": ["vmware", "vsphere", "nsx-t", "zerto"],
    "AI Infra / SRE": ["ai platform", "dataiku", "mlops", "gpu infrastructure", "platform engineer", "sre", "devops"]
}

# Wajib term kontrak (hindari 'smart contract' atau 'federal contract')
CONTRACT_REGEX = re.compile(
    r"(?<!smart\s)(?<!federal\s)(?<!government\s)\b(contract|contractor|freelance|freelancer|b2b|c2c|hourly|retainer|part-time|1099)\b", 
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


def is_valid_contract_and_geo(header_or_title: str, full_text: str) -> bool:
    first_block = f"{header_or_title} {full_text[:350]}"

    # 1. Wajib buang semua yang bertuliskan Full-Time
    if re.search(r"\bfull[\s-]time\b", first_block, re.IGNORECASE):
        return False

    # 2. Cek blacklist lokasi tertutup (US, UK, EU, dll)
    if GEO_EXCLUDE_REGEX.search(first_block):
        return False

    # 3. Cek blacklist role
    if EXCLUDE_ROLES_REGEX.search(first_block):
        return False

    # 4. Wajib role infra
    if not INFRA_ROLE_REGEX.search(header_or_title) and not INFRA_ROLE_REGEX.search(full_text[:200]):
        return False

    # 5. Wajib ada kata kontrak/freelance murni
    return bool(CONTRACT_REGEX.search(first_block) or CONTRACT_REGEX.search(full_text))


def fetch_hn_contracts() -> list:
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
            comments_url = f"https://hn.algolia.com/api/v1/search?tags=comment,story_{story_id}&hitsPerPage=150"
            comments_res = requests.get(comments_url, headers=HEADERS, timeout=15).json()

            for hit in comments_res.get("hits", []):
                raw_html = hit.get("comment_text", "")
                text = html.unescape(raw_html)
                # Ubah tag <p> jadi newline agar baris header tetap terbaca rapi
                text_with_newlines = re.sub(r"</?p>", "\n", text)
                clean_text = re.sub(r"<[^>]+>", " ", text_with_newlines).strip()
                upper = clean_text.upper()

                if "SEEKING WORK" in upper:
                    continue

                lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
                first_line = lines[0] if lines else clean_text[:120]

                if "ONSITE" in first_line.upper() or "HYBRID" in first_line.upper():
                    continue

                if is_valid_contract_and_geo(first_line, clean_text):
                    matched_skills = extract_matched_skills(clean_text)
                    if matched_skills:
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

    print(f"[*] HN kontrak global valid: {len(jobs)} listing")
    return jobs


def fetch_jobicy_contracts() -> list:
    jobs = []
    try:
        url = "https://jobicy.com/api/v2/remote-jobs?count=50&tag=devops"
        res = requests.get(url, headers=HEADERS, timeout=15).json()
        for item in res.get("jobs", []):
            job_type = item.get("jobType", "").lower()
            title = item.get("jobTitle", "")
            desc = item.get("jobDescription", "")
            geo = item.get("jobGeo", "").lower()
            header_meta = f"{title} | {geo} | {job_type}"

            # Tolak lowongan full-time
            if "full-time" in job_type or "full time" in job_type:
                continue

            # Abaikan jika region terkunci dan bukan global/anywhere/worldwide
            if geo and not any(term in geo for term in ["anywhere", "worldwide", "global", "all"]):
                continue

            if is_valid_contract_and_geo(header_meta, desc):
                matched = extract_matched_skills(f"{title} {desc}")
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

    print(f"[*] Jobicy kontrak global valid: {len(jobs)} listing")
    return jobs


def main():
    seen_urls = load_seen_urls()
    print(f"[*] Total history seen URLs: {len(seen_urls)}")

    all_jobs = []
    all_jobs.extend(fetch_hn_contracts())
    all_jobs.extend(fetch_jobicy_contracts())

    new_jobs = [job for job in all_jobs if job["url"] not in seen_urls]
    print(f"[*] Total lolos kurasi ketat: {len(all_jobs)}, listing baru: {len(new_jobs)}")

    if not new_jobs:
        print("[!] Tidak ada proyek kontrak global murni yang lolos kurasi. Bot standby.")
        return

    top_jobs = new_jobs[:5]

    message = (
        "<b>⚡ STRICT GLOBAL FREELANCE / CONTRACT RADAR</b>\n"
        "<i>Role: DevOps, SRE, K8s, Cloud (Strict Remote Worldwide & Contract Only)</i>\n"
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
