# app.py
"""
Simple Streamlit app that:
- Accepts an input of the form: https://some_characters://url
- Extracts the last part after '://', prepends 'https://'
- Fetches the HTML (with a browser-like User-Agent)
- If Google search pages are refused, attempts a simple fallback search (num=1)
- Sanitizes the HTML by removing/neutralizing elements that auto-fetch external resources
- Renders the sanitized HTML with components.v1.html
"""

import streamlit as st
import streamlit.components.v1 as components
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, quote_plus
from requests.exceptions import RequestException

# ---------------------------
# Configuration / constants
# ---------------------------
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 10  # seconds

st.set_page_config(page_title="", layout="centered")

# ---------------------------
# Helper functions
# ---------------------------

def normalize_input_to_url(user_input: str) -> str:
    """
    Given input like 'https://abc://example.com/page' returns
    'https://example.com/page'.
    """
    # Split by '://', take the last part and ensure https:// prefix
    parts = user_input.split("://")
    if not parts:
        return ""
    last = parts[-1].strip()
    if not last:
        return ""
    candidate = "https://" + last
    return candidate

def is_valid_url(url: str) -> bool:
    """
    Basic validation using urlparse: ensure we have a network location.
    """
    parsed = urlparse(url)
    return bool(parsed.scheme) and bool(parsed.netloc)

def fetch_url(url: str, headers=None) -> requests.Response:
    """
    Fetch URL using requests.get with timeout and optional headers.
    Raises RequestException on failure.
    """
    hdrs = headers or {"User-Agent": USER_AGENT}
    resp = requests.get(url, headers=hdrs, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp

def google_fallback_search(original_url: str) -> requests.Response:
    """
    If fetching a Google search page fails, try a simple fallback:
    - extract query parameter 'q' from the original URL if present
    - otherwise try to use the path as a query
    - perform a request to https://www.google.com/search?q=...&num=1
    """
    parsed = urlparse(original_url)
    qs = parse_qs(parsed.query)
    query = None
    if "q" in qs and qs["q"]:
        query = qs["q"][0]
    else:
        # try the path and the fragment
        path_candidate = parsed.path.strip("/ ")
        fragment = parsed.fragment.strip()
        query = path_candidate or fragment or ""
    query = query or ""
    safe_q = quote_plus(query) if query else ""
    search_url = f"https://www.google.com/search?q={safe_q}&num=1" if safe_q else "https://www.google.com/search?num=1"
    hdrs = {"User-Agent": USER_AGENT}
    resp = requests.get(search_url, headers=hdrs, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp

def sanitize_html_(html: str, base_url: str = "") -> str:
    """
    Remove or neutralize elements that may auto-fetch external resources:
    - <script>, <iframe>, <object>, <embed>, <link>, <meta http-equiv="refresh">, <base>, <noscript>
    - remove src/href attributes that point offsite (replace with empty or '#')
    - remove inline event handlers (attributes starting with 'on')
    - remove <form action="..."> targets (set action='#')
    Returns sanitized HTML string.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove dangerous tags entirely
    for tag_name in ("script", "iframe", "object", "embed", "noscript", "base"):
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Remove all <link> and <meta http-equiv="refresh"> tags
    for link in soup.find_all("link"):
        link.decompose()
    for meta in soup.find_all("meta"):
        # meta with refresh or dns-prefetch-like attributes removed
        http_equiv = meta.get("http-equiv", "")
        if http_equiv and http_equiv.lower() == "refresh":
            meta.decompose()
        else:
            # remove meta to avoid prefetch/meta-refresh/verification tags
            meta.decompose()

    # Remove <picture> source srcset attributes and other srcset occurrences
    for tag in soup.find_all(attrs={"srcset": True}):
        del tag["srcset"]

    # Neutralize src/href attributes to avoid auto-fetching external resources.
    # For images: remove the src attribute (keeps the <img> tag but prevents fetch).
    for img in soup.find_all("img"):
        if img.has_attr("src"):
            # preserve alt text if available, but remove src
            del img["src"]
        if img.has_attr("srcset"):
            del img["srcset"]
        # remove lazy-loading or other fetch attributes
        for attr in list(img.attrs):
            if attr.startswith("on"):  # onload, onclick, etc.
                del img[attr]

    # For elements with href (a tags, link, etc.), neutralize external navigation.
    for tag in soup.find_all(href=True):
        # Convert absolute external hrefs to '#' but keep internal anchors
        href = tag.get("href", "")
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            # leave as-is for anchors/mailto/js anchors
            continue
        # Replace actionable href with '#'
        tag["href"] = "#"

    # For elements with src (scripts removed earlier, but keep defensive)
    for tag in soup.find_all(src=True):
        # remove the src attribute to prevent fetch
        del tag["src"]

    # Remove inline event handlers (attributes starting with "on")
    for tag in soup.find_all():
        attrs = list(tag.attrs.keys())
        for a in attrs:
            if a.lower().startswith("on"):
                del tag.attrs[a]

    # Disable forms by setting action="#" and method to "get" and disabling submission inputs
    for form in soup.find_all("form"):
        form["action"] = "#"
        form["method"] = "get"
        # disable submit buttons inside forms
        for btn in form.find_all(["input", "button"]):
            if btn.name == "input" and btn.get("type", "").lower() in ("submit", "image", "button"):
                btn["disabled"] = "disabled"
            if btn.name == "button":
                btn["disabled"] = "disabled"

    # Remove <style>@import</style> that may pull external CSS (we'll remove style tags too).
    # for style in soup.find_all("style"):
    #     # If it contains @import we remove; to be safe remove all style blocks to avoid external pulls
    #     style.decompose()

    # Remove elements that could embed external resources via data attributes e.g., <video>, <audio>, <source>
    for tag_name in ("video", "audio", "source", "track"):
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Final precaution: remove comments
    for comment in soup.find_all(string=lambda text: isinstance(text, type(soup.Comment))):
        comment.extract()

    # Optional: add notice at top that page was sanitized
    notice = soup.new_tag("div")
    notice.string = "/* This page was sanitized: scripts, iframes, external links and auto-fetching resources removed */"
    # style as simple preformatted small text
    notice["style"] = "font-family:monospace; font-size:12px; opacity:0.85; padding:6px; border-bottom:1px solid #ddd;"
    # Insert as first element in body if exists, otherwise at top of document
    if soup.body:
        soup.body.insert(0, notice)
    else:
        soup.insert(0, notice)

    return str(soup)

def sanitize_html(html: str, base_url: str = "") -> str:
    """
    Light sanitizer:
    - Keeps all HTML structure
    - Disable link previews by rewriting <a href="URL"> → https://noice://URL
    - Disable image fetching by rewriting <img src="URL"> → https://noice://URL
    """
    soup = BeautifulSoup(html, "html.parser")

    # ---- Rewrite hyperlinks ----
    for tag in soup.find_all("a", href=True):
        original = tag["href"]

        # internal anchors allowed
        if original.startswith("#"):
            continue

        tag["href"] = f"https://noice://{original}"

    # ---- Rewrite images ----
    for img in soup.find_all("img", src=True):
        original = img["src"]

        # Replace img src with disabled URL so nothing loads
        img["src"] = f"https://noice://{original}"

        # Optional: provide alt text if none exists
        if not img.get("alt"):
            img["alt"] = "[image disabled]"

    return str(soup)


# ---------------------------
# Streamlit UI
# ---------------------------

# st.title("Fetch & Sanitize HTML (simple)")

with st.form(key="url_form", clear_on_submit=False):
    user_input = st.text_input(
        "Enter string (format: https://some_characters://url)",
        label_visibility="hidden",
        value="",
        placeholder="e.g. https://abc://example.com/page",
    )
    submit = st.form_submit_button("Fetch")

if submit:
    if not user_input or "://" not in user_input:
        st.error("Please enter a string containing '://'. Example: https://abc://example.com/page")
    else:
        # 1) Build normalized URL
        final_url = normalize_input_to_url(user_input)

        if not is_valid_url(final_url):
            st.error(f"Resulting URL is invalid: {final_url}")
        else:
            st.info(f"Attempting to fetch: {final_url}")
            try:
                # 2) Try to fetch with a browser-like user agent
                # headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
                headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;"
                    "q=0.9,image/avif,image/webp,*/*;q=0.8"
                ),
                "Referer": "https://www.google.com/",
                "Cookie": (
                    "SOCS=CAI; "
                    "CONSENT=PENDING+123; "
                    "AEC=SomeValidFallbackToken;"
                )}
                resp = fetch_url(final_url, headers=headers)
                html = resp.text

            except RequestException as e:
                # If it's a Google search page, attempt fallback (simple search using q & num=1)
                parsed = urlparse(final_url)
                netloc = parsed.netloc.lower()
                if "google." in netloc:
                    st.warning("Direct fetch failed for Google — attempting a simple fallback search (num=1) with browser user-agent.")
                    try:
                        resp = google_fallback_search(final_url)
                        html = resp.text
                    except RequestException as e2:
                        st.error(f"Failed to fetch page even with Google fallback: {e2}")
                        html = None
                else:
                    st.error(f"Failed to fetch URL: {e}")
                    html = None

            if html:
                # 3) Parse and sanitize HTML
                try:
                    sanitized = sanitize_html(html, base_url=final_url)
                except Exception as e:
                    st.error(f"Error while sanitizing HTML: {e}")
                    sanitized = "<p>Unable to sanitize content.</p>"

                # 4) Render sanitized HTML safely
                st.success("Rendering sanitized HTML below.")
                components.html(sanitized, height=800, scrolling=True)
