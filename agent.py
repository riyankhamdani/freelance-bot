import html
import json
import os
import re
import time
from datetime import datetime, timedelta
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = "seen_urls.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
}

# Role WAJIB di Judul / Header
INFRA_ROLE_REGEX = re.compile(
    r"\b(devops|sre|site reliability|platform"
    r" engineer|infrastructure|infra|kubernetes|k8s|cloud engineer|cloud"
    r" architect|systems engineer|sysadmin|openshift|terraform|ansible|ai"
    r" trainer|coding tasks|llm evaluation)\b",
    re.IGNORECASE,
)

# Blacklist role non-teknis / non-infra
EXCLUDE_ROLES_REGEX = re.compile(
    r"\b(business development|sales|account"
    r" executive|marketing|recruiter|product"
    r" manager|frontend|react|mobile|ios|android|designer|ui/ux|commission-only|equity"
    r" only|unpaid)\b",
    re.IGNORECASE,
)

# Blacklist regional tertutup (US, UK, EU, Canada, India, dll)
GEO_EXCLUDE_REGEX = re.compile(
    r"(?:remote\s*[-–(:]\s*(?:us|usa|u\.s\.|uk|eu|europe|canada|india|latam|americas|uk\s+or\s+eu|eu\s+or\s+uk)[^)\n]*\)?|"
    r"\b(?:us|usa|uk|eu|canada|india)\s+only\b|"
    r"\b(?:us|usa|uk|eu)\s+citizens?\b|"
    r"\bw2\s+only\b|"
    r"\bsecurity\s+clearance\b|"
    r"\bbased\s+in\s+(?:the\s+)?(?:us|usa|uk|eu|europe)\b)",
    re.IGNORECASE,
)

# Skill mapping profil CV Riyan Khamdani
PROFILE_KEYWORDS = {
    "OpenShift / K8s": [
        "openshift",
        "kubernetes",
        "k8s",
        "gke",
        "podman",
        "docker",
    ],
    "IaC & Automation": ["terraform", "ansible", "ci/cd", "gitops"],
    "Cloud & Linux": [
        "aws",
        "gcp",
        "google cloud",
        "alibaba cloud",
        "rhel",
        "linux",
    ],
    "Virtualization": ["vmware", "vsphere", "nsx-t", "zerto"],
    "AI Infra / RLHF": [
        "ai platform",
        "dataiku",
        "mlops",
        "llm",
        "ai trainer",
        "coding",
        "sre",
        "devops",
        "platform",
    ],
}

# Pola deteksi proyek lepas / kontrak / fractional / hourly
CONTRACT_REGEX = re.compile(
    r"(?<!smart\s)(?<!federal\s)(?<!government\s)\b(contract|contractor|freelance|freelancer|b2b|c2c|hourly|retainer|part-time|part"
    r" time|fractional|1099|project-based|short-term)\b",
    re.IGNORECASE,
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
      "disable_web_page_preview": True,
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


def is_valid_gig(
    header_or_title: str, full_text: str, is_freelance_board: bool = False
) -> bool:
  combined_head = f"{header_or_title} {full_text[:400]}"

  # Drop lowongan Full-time permanen
  if re.search(
      r"\b(full[\s-]time|permanent\s+role|w2\s+employee)\b",
      combined_head,
      re.IGNORECASE,
  ):
    if not re.search(
        r"\b(contract|freelance|b2b|part[\s-]time|fractional)\b",
        combined_head,
        re.IGNORECASE,
    ):
      return False

  # Drop batasan regional tertutup & clearance
  if GEO_EXCLUDE_REGEX.search(combined_head) or GEO_EXCLUDE_REGEX.search(
      full_text
  ):
    return False

  # Drop role non-infra
  if EXCLUDE_ROLES_REGEX.search(header_or_title):
    return False

  # Wajib role infra / cloud / AI task
  if not INFRA_ROLE_REGEX.search(combined_head):
    return False

  # Jika sumbernya bukan khusus papan freelance, wajib ada kata kunci kontrak/freelance/hourly
  if not is_freelance_board:
    return bool(
        CONTRACT_REGEX.search(combined_head) or CONTRACT_REGEX.search(full_text)
    )

  return True


def fetch_hn_freelance_gigs() -> list:
  """Scan thread khusus 'Seeking Freelancer' & postingan kontrak terbaru di Hacker News."""
  jobs = []
  try:
    url = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=12"
    res = requests.get(url, headers=HEADERS, timeout=15).json()
    hits = res.get("hits", [])

    # Prioritaskan thread 'Freelancer? Seeking freelancer?' dan 'Who is hiring?' terbaru
    target_stories = [
        h
        for h in hits
        if "freelancer" in h.get("title", "").lower()
        or "hiring" in h.get("title", "").lower()
    ]

    for story in target_stories[:4]:
      story_id = story["objectID"]
      story_title = story.get("title", "").lower()
      is_freelancer_thread = "freelancer" in story_title

      query_str = (
          "devops OR kubernetes OR terraform OR sre OR infrastructure OR aws OR"
          " cloud"
      )
      comments_url = (
          f"https://hn.algolia.com/api/v1/search"
          f"?tags=comment,story_{story_id}"
          f"&query={requests.utils.quote(query_str)}"
          f"&hitsPerPage=500"
      )
      comments_res = requests.get(
          comments_url, headers=HEADERS, timeout=15
      ).json()

      for hit in comments_res.get("hits", []):
        raw_html = hit.get("comment_text", "")
        text = html.unescape(raw_html)
        text_with_newlines = re.sub(r"</?p>", "\n", text)
        clean_text = re.sub(r"<[^>]+>", " ", text_with_newlines).strip()
        upper = clean_text.upper()

        # Di thread Freelancer, skip orang yang mempromosikan diri sendiri
        if "SEEKING WORK" in upper or "FOR HIRE" in upper:
          continue

        lines = [
            line.strip() for line in clean_text.splitlines() if line.strip()
        ]
        first_line = lines[0] if lines else clean_text[:140]

        if "ONSITE" in first_line.upper() and "REMOTE" not in first_line.upper():
          continue

        if is_valid_gig(
            first_line, clean_text, is_freelance_board=is_freelancer_thread
        ):
          matched_skills = extract_matched_skills(clean_text)
          if matched_skills:
            snippet = clean_text[:150].replace("\n", " ") + "..."
            tag_label = (
                "HN Seeking Freelancer"
                if is_freelancer_thread
                else "HN Contract"
            )
            jobs.append({
                "source": tag_label,
                "title": snippet,
                "company": hit.get("author", "HN Client"),
                "url": (
                    f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                ),
                "date": hit.get("created_at", "")[:10],
                "matches": matched_skills,
            })
  except Exception as e:
    print(f"[-] HN error: {e}")

  print(f"[*] HN freelance/kontrak valid: {len(jobs)} listing")
  return jobs


def fetch_remotive_contracts() -> list:
  """Mengambil proyek kontrak/freelance kategori DevOps & Sysadmin dari Remotive."""
  jobs = []
  try:
    url = "https://remotive.com/api/remote-jobs?category=devops&limit=50"
    res = requests.get(url, headers=HEADERS, timeout=15).json()
    for item in res.get("jobs", []):
      job_type = str(item.get("job_type") or "").lower()
      title = str(item.get("title") or "").strip()
      desc = re.sub(r"<[^>]+>", " ", str(item.get("description") or ""))
      geo = str(item.get("candidate_required_location") or "").lower()

      # Hanya ambil contract, freelance, atau part_time
      if job_type not in ["contract", "freelance", "part_time"]:
        continue

      if geo and not any(
          term in geo
          for term in [
              "worldwide",
              "anywhere",
              "global",
              "apac",
              "asia",
              "indonesia",
          ]
      ):
        continue

      header_meta = f"{title} | {geo} | {job_type}"
      if is_valid_gig(header_meta, desc, is_freelance_board=True):
        matched = extract_matched_skills(f"{title} {desc}")
        if matched:
          jobs.append({
              "source": f"Remotive ({job_type.title()})",
              "title": title,
              "company": str(item.get("company_name") or "Client").strip(),
              "url": str(item.get("url") or ""),
              "date": str(item.get("publication_date") or "")[:10],
              "matches": matched,
          })
  except Exception as e:
    print(f"[-] Remotive error: {e}")

  print(f"[*] Remotive kontrak/freelance valid: {len(jobs)} listing")
  return jobs


def fetch_remoteok_contracts() -> list:
  """Mengambil listing kontrak/freelance dari RemoteOK API."""
  jobs = []
  try:
    url = "https://remoteok.com/api?tag=devops"
    res = requests.get(url, headers=HEADERS, timeout=15).json()
    # Elemen pertama RemoteOK adalah metadata legal
    listings = res[1:] if isinstance(res, list) and len(res) > 1 else []

    for item in listings[:60]:
      title = str(item.get("position") or "").strip()
      tags = " ".join(str(t) for t in item.get("tags", [])).lower()
      desc = re.sub(r"<[^>]+>", " ", str(item.get("description") or ""))
      location = str(item.get("location") or "").lower()

      if location and not any(
          term in location
          for term in ["worldwide", "anywhere", "global", "remote", "apac"]
      ):
        continue

      header_meta = f"{title} | {location} | {tags}"
      if is_valid_gig(header_meta, desc, is_freelance_board=False):
        matched = extract_matched_skills(f"{title} {tags} {desc}")
        if matched:
          jobs.append({
              "source": "RemoteOK (Contract)",
              "title": title,
              "company": str(item.get("company") or "Client").strip(),
              "url": str(
                  item.get("url")
                  or f"https://remoteok.com/remote-jobs/{item.get('id')}"
              ),
              "date": str(item.get("date") or "")[:10],
              "matches": matched,
          })
  except Exception as e:
    print(f"[-] RemoteOK error: {e}")

  print(f"[*] RemoteOK kontrak/freelance valid: {len(jobs)} listing")
  return jobs


def fetch_ai_coding_gigs() -> list:
  """Radar task-based AI Coding / Infra Evaluation (seperti Askable Labs / Turing / Scale)."""
  jobs = []
  boards = [
      ("Turing (AI/Infra Task)", "turing"),
      ("Scale AI (Coding/RLHF)", "scaleai"),
  ]
  for label, board_slug in boards:
    try:
      url = f"https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs"
      res = requests.get(url, headers=HEADERS, timeout=12).json()
      for item in res.get("jobs", []):
        title = str(item.get("title") or "").strip()
        loc = str(item.get("location", {}).get("name") or "").lower()

        if GEO_EXCLUDE_REGEX.search(f"{title} {loc}"):
          continue

        if re.search(
            r"\b(devops|kubernetes|cloud|infrastructure|sre|platform|ai"
            r" trainer|coding)\b",
            title,
            re.IGNORECASE,
        ):
          if any(
              term in loc
              for term in ["remote", "indonesia", "apac", "anywhere", "global"]
          ):
            jobs.append({
                "source": label,
                "title": title,
                "company": label.split(" ")[0],
                "url": str(item.get("absolute_url") or ""),
                "date": str(item.get("updated_at") or "")[:10],
                "matches": ["AI Infra / RLHF", "Cloud & Linux"],
            })
    except Exception as e:
      print(f"[-] {label} error: {e}")

  print(f"[*] AI Task / Freelance Platform valid: {len(jobs)} listing")
  return jobs


def main():
  seen_urls = load_seen_urls()
  print(f"[*] Total history seen URLs: {len(seen_urls)}")

  all_jobs = []
  all_jobs.extend(fetch_hn_freelance_gigs())
  all_jobs.extend(fetch_remotive_contracts())
  all_jobs.extend(fetch_remoteok_contracts())
  all_jobs.extend(fetch_ai_coding_gigs())

  new_jobs = [
      job for job in all_jobs if job["url"] and job["url"] not in seen_urls
  ]
  print(
      f"[*] Total lolos kurasi freelance: {len(all_jobs)}, listing baru:"
      f" {len(new_jobs)}"
  )

  if not new_jobs:
    print(
        "[!] Tidak ada proyek freelance/side-gig baru hari ini. Bot standby."
    )
    return

  top_jobs = new_jobs[:6]

  message = (
      "<b>⚡ GLOBAL FREELANCE & SIDE-GIG RADAR</b>\n"
      "<i>Filter: Hourly, Fractional, B2B Contract & AI Tasks (No"
      " Full-Time)</i>\n"
      "====================================\n\n"
  )

  for idx, job in enumerate(top_jobs, 1):
    tags_formatted = " | ".join(job["matches"])
    message += (
        f"<b>{idx}. {html.escape(job['title'])}</b>\n"
        f"🎯 <b>Match:</b> <code>{tags_formatted}</code>\n"
        f"🏢 <b>Client:</b> {html.escape(job['company'])} | <b>Via:</b>"
        f" {job['source']}\n"
        f"📅 <b>Date:</b> {job['date']}\n"
        f"🔗 <a href='{job['url']}'>Direct Apply / Contact</a>\n\n"
    )
    seen_urls.add(job["url"])

  send_telegram_message(message)
  save_seen_urls(seen_urls)
  print(
      f"[✓] Berhasil mengirim {len(top_jobs)} listing freelance ke Telegram."
  )


if __name__ == "__main__":
  main()
