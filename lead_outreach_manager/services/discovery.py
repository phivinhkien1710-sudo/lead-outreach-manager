"""Bounded, deterministic domain/contact/candidate discovery for CSV imports.

No search API or LLM is used. Missing domains are inferred from company-name
variants and verified by DNS plus company-name presence on the homepage. Only
public network targets are fetched, and crawling stays on the verified host.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

REGION_TLDS = {
	"Singapore": (".com", ".sg", ".com.sg"),
	"Vietnam": (".com", ".vn", ".com.vn"),
}
DEFAULT_PATHS = ("/", "/contact", "/contact-us", "/about", "/about-us", "/team", "/leadership", "/management")
TITLE_WORDS = (
	"chief executive officer", "managing director", "general manager", "director", "founder",
	"co-founder", "owner", "partner", "principal", "president", "chairman", "sales manager",
)
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
NAME_RE = re.compile(r"\b([A-ZÀ-Ỹ][\w'’.-]+(?:\s+[A-ZÀ-Ỹ][\w'’.-]+){1,3})\b", re.UNICODE)
SUFFIX_RE = re.compile(r"\b(pte|ltd|limited|llp|llc|plc|inc|corp|corporation|co|company|private|holdings?)\b", re.I)
GENERIC_TOKENS = {"singapore", "vietnam", "asia", "international", "global", "group", "trading", "services", "solutions", "enterprise", "enterprises"}
NOISE_WORDS = {
	"about", "contact", "home", "learn", "more", "services", "solutions", "company", "group",
	"products", "quality", "technology", "management", "marketing", "support", "privacy", "policy",
	"careers", "news", "chief", "officer", "executive", "vice", "president", "chairman", "director",
	"founder", "manager", "board", "ltd", "pte", "limited", "singapore", "vietnam",
}
PLACEHOLDER_DOMAINS = {"example.com", "example.org", "example.net", "example.tld", "godaddy.com"}
HEADERS = {"User-Agent": "LeadOutreachManager/1.0 (+deterministic company research)"}


@dataclass
class DiscoveryResult:
	domain: str = ""
	website: str = ""
	domain_status: str = "no_domain_found"
	crawl_status: str = "skipped"
	domain_match_score: float = 0
	contact_points: list[dict] = field(default_factory=list)
	candidates: list[dict] = field(default_factory=list)
	detail: str = ""


def discover_rows(rows, *, workers=4, timeout=12, max_pages=6):
	"""Discover all rows concurrently while preserving their input order."""
	workers = max(1, min(int(workers or 4), 12))
	results = [None] * len(rows)
	errors = []
	with ThreadPoolExecutor(max_workers=workers) as executor:
		futures = {
			executor.submit(discover_lead, row, timeout=int(timeout), max_pages=int(max_pages)): index
			for index, row in enumerate(rows)
		}
		for future in as_completed(futures):
			index = futures[future]
			try:
				results[index] = future.result()
			except Exception as exc:
				results[index] = DiscoveryResult(detail=f"discovery failed: {exc}")
				errors.append(f"Row {rows[index]['row_number']}: discovery failed: {exc}")
	return results, errors


def discover_lead(row, *, timeout=12, max_pages=6):
	company_name = row["company_name"]
	country = row["country"]
	existing_domain = row.get("domain") or ""
	websites = []
	if existing_domain:
		websites = [row.get("website") or f"https://{existing_domain}", f"http://{existing_domain}"]
	else:
		for domain in candidate_domains(company_name, country):
			if public_domain_resolves(domain):
				websites.extend((f"https://{domain}", f"http://{domain}"))

	checked_domains = set()
	for website in websites:
		domain = (urlparse(website).hostname or "").lower()
		if not domain or domain in checked_domains and website.startswith("https"):
			continue
		if not public_domain_resolves(domain):
			continue
		try:
			pages = crawl_site(website, timeout=timeout, max_pages=max_pages)
		except requests.RequestException:
			continue
		if not pages:
			continue
		score = name_match_score(company_name, pages[0][1])
		if score < 0.6:
			checked_domains.add(domain)
			continue
		contacts = extract_contacts(pages, domain)
		candidates = extract_candidates(pages)
		return DiscoveryResult(
			domain=domain,
			website=website.rstrip("/"),
			domain_status="verified",
			crawl_status="crawled",
			domain_match_score=round(score, 3),
			contact_points=contacts,
			candidates=candidates,
			detail=f"verified {domain}; crawled {len(pages)} page(s)",
		)

	if existing_domain:
		return DiscoveryResult(
			domain=existing_domain,
			website=row.get("website") or f"https://{existing_domain}",
			domain_status="unverified",
			crawl_status="crawl_failed",
			detail="provided domain could not be verified against the company name",
		)
	return DiscoveryResult(detail="no generated domain candidate could be verified")


def candidate_domains(company_name, country):
	tokens = name_tokens(company_name)
	if not tokens:
		return []
	stems = []
	joined = "".join(tokens)
	if 3 <= len(joined) <= 30:
		stems.append(joined)
	if len(tokens) > 1:
		hyphenated = "-".join(tokens)
		if len(hyphenated) <= 30:
			stems.append(hyphenated)
		two = "".join(tokens[:2])
		if len(two) >= 4:
			stems.append(two)
	if len(tokens) >= 3:
		stems.append("".join(token[0] for token in tokens))
	return list(dict.fromkeys(f"{stem}{tld}" for stem in stems for tld in REGION_TLDS.get(country, (".com",))))


def name_tokens(company_name):
	cleaned = SUFFIX_RE.sub(" ", (company_name or "").lower())
	return [token for token in re.sub(r"[^a-z0-9]+", " ", cleaned).split() if token]


def name_match_score(company_name, page_text):
	tokens = [token for token in name_tokens(company_name) if token not in GENERIC_TOKENS] or name_tokens(company_name)
	if not tokens:
		return 0
	lowered = (page_text or "").lower()
	if "".join(tokens) in re.sub(r"[^a-z0-9]+", "", lowered):
		return 1
	return sum(token in lowered for token in tokens) / len(tokens)


def public_domain_resolves(domain):
	if not domain or domain.lower() == "localhost":
		return False
	try:
		addresses = {item[4][0] for item in socket.getaddrinfo(domain, 443, proto=socket.IPPROTO_TCP)}
	except OSError:
		return False
	return bool(addresses) and all(is_public_ip(address) for address in addresses)


def is_public_ip(address):
	ip = ipaddress.ip_address(address)
	return not any((ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_reserved, ip.is_multicast, ip.is_unspecified))


def crawl_site(website, *, timeout=12, max_pages=6):
	website = normalize_website(website)
	if not website:
		return []
	base_host = urlparse(website).hostname.lower()
	canonical_host = base_host.removeprefix("www.")
	candidates = [urljoin(website + "/", path.lstrip("/")) for path in DEFAULT_PATHS]
	pages = []
	attempted = set()
	session = requests.Session()
	session.headers.update(HEADERS)
	while candidates and len(pages) < max_pages:
		url = candidates.pop(0)
		url_host = (urlparse(url).hostname or "").lower()
		if url in attempted or url_host.removeprefix("www.") != canonical_host:
			continue
		attempted.add(url)
		if not public_domain_resolves(base_host):
			return pages
		time.sleep(0.1)
		try:
			response = session.get(url, timeout=timeout, allow_redirects=True)
			response.raise_for_status()
		except requests.RequestException:
			continue
		redirect_host = (urlparse(response.url).hostname or "").lower()
		if redirect_host.removeprefix("www.") != canonical_host or not public_domain_resolves(redirect_host):
			continue
		soup = BeautifulSoup(response.text, "html.parser")
		for tag in soup(["script", "style", "noscript", "svg"]):
			tag.decompose()
		text = " ".join(soup.get_text(" ", strip=True).split())
		pages.append((response.url, text, response.text))
		if len(pages) == 1:
			for link in soup.select("a[href]"):
				label = link.get_text(" ", strip=True).lower()
				if any(word in label for word in ("about", "team", "leadership", "contact", "management")):
					candidates.append(urljoin(response.url, link.get("href", "")))
	return pages


def extract_contacts(pages, domain):
	contacts = []
	seen = set()
	for url, text, html in pages:
		soup = BeautifulSoup(html, "html.parser")
		emails = set(EMAIL_RE.findall(text or ""))
		emails.update(link.get("href", "")[7:].split("?", 1)[0] for link in soup.select('a[href^="mailto:"]'))
		for value in emails:
			email = value.lower().strip()
			if not EMAIL_RE.fullmatch(email) or email.rsplit("@", 1)[-1] in PLACEHOLDER_DOMAINS:
				continue
			key = ("Email", email)
			if key not in seen:
				seen.add(key)
				contacts.append({"contact_type": "Email", "value": email, "is_generic": 1, "confidence": 0.9, "source_url": url})
		phone = PHONE_RE.search(text or "")
		if phone:
			value = " ".join(phone.group(0).split())
			key = ("Phone", value)
			if key not in seen:
				seen.add(key)
				contacts.append({"contact_type": "Phone", "value": value, "is_generic": 1, "confidence": 0.7, "source_url": url})
		if "contact" in urlparse(url).path.lower():
			key = ("Contact Form", url)
			if key not in seen:
				seen.add(key)
				contacts.append({"contact_type": "Contact Form", "value": url, "is_generic": 1, "confidence": 1, "source_url": url})
	return contacts


def extract_candidates(pages, window=80):
	results = []
	seen = set()
	for url, text, _html in pages:
		lowered = text.lower()
		for title in TITLE_WORDS:
			start = 0
			while True:
				index = lowered.find(title, start)
				if index < 0:
					break
				for candidate_text in (text[max(0, index - window):index], text[index + len(title):index + len(title) + window]):
					for match in NAME_RE.finditer(candidate_text):
						name = match.group(1).strip()
						words = {word.strip(".,:;|-/").lower() for word in name.split()}
						key = (name, title)
						if not words.intersection(NOISE_WORDS) and key not in seen:
							seen.add(key)
							results.append({"name_text": name, "title_text": title.title(), "source_url": url})
				start = index + len(title)
	return results


def normalize_website(value):
	value = (value or "").strip()
	if not value:
		return ""
	if not value.startswith(("http://", "https://")):
		value = f"https://{value}"
	parsed = urlparse(value)
	return value.rstrip("/") if parsed.hostname else ""
