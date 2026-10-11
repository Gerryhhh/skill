#!/usr/bin/env python3
"""
extract_book.py — Extract plain text + TOC from a book file for skill distillation.

Supports: .epub, .pdf, .txt, .md, .html/.xhtml

Usage:
    python3 extract_book.py <book_path> [output_txt]
    python3 extract_book.py <book_path> [output_txt] --toc toc.json
    python3 extract_book.py <book_path> --probe          # detect text layer only

PDF backend: PyMuPDF (fitz) preferred — it also yields a real TOC via doc.get_toc().
Falls back to pypdf if PyMuPDF is unavailable or fails to import.

Outputs plain text to output_txt (default: /tmp/<basename>_text.txt) and prints
the character count. Designed to feed a downstream distillation agent.

Chapter/page markers:
    ===== PAGE N =====   (pdf)
    ===== CHAPTER <spine-href> =====   (epub)
These markers let a distillation sub-agent cite locations accurately.
"""

import html
import json
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

# --------------------------------------------------------------------------- #
# HTML / XML helpers
# --------------------------------------------------------------------------- #

def strip_html(data: bytes) -> str:
    text = data.decode("utf-8", errors="ignore")
    text = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<h[1-6][^>]*>", "\n### ", text, flags=re.I)
    text = re.sub(r"</h[1-6]>", "\n", text, flags=re.I)
    text = re.sub(r"<(p|div|br|tr|li)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _local(tag: str) -> str:
    """Strip XML namespace: '{http://...}itemref' -> 'itemref'."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


# --------------------------------------------------------------------------- #
# EPUB — parse container.xml -> OPF -> spine (authoritative reading order)
# --------------------------------------------------------------------------- #

def _opf_path(z: zipfile.ZipFile) -> str:
    try:
        root = ET.fromstring(z.read("META-INF/container.xml"))
        for rf in root.iter():
            if _local(rf.tag) == "rootfile":
                return rf.get("full-path", "")
    except Exception:
        pass
    # Fallback: first .opf in the archive
    for n in z.namelist():
        if n.lower().endswith(".opf"):
            return n
    raise SystemExit("ERROR: malformed EPUB — no OPF found")


def _parse_opf(z: zipfile.ZipFile, opf: str):
    """Return (spine_hrefs_in_order, nav_href, opf_dir)."""
    opf_dir = os.path.dirname(opf)
    root = ET.fromstring(z.read(opf))

    manifest = {}   # id -> (href, properties)
    nav_href = None
    for el in root.iter():
        if _local(el.tag) == "item":
            iid = el.get("id")
            href = el.get("href")
            props = (el.get("properties") or "").lower()
            if not iid or not href:
                continue
            href = href.split("#", 1)[0]
            manifest[iid] = (href, props)
            if "nav" in props.split():
                nav_href = href

    spine = []
    for el in root.iter():
        if _local(el.tag) == "itemref":
            idref = el.get("idref")
            if not idref or idref not in manifest:
                continue
            href, props = manifest[idref]
            # Skip the navigation document itself — it is a TOC, not prose.
            if "nav" in props.split():
                continue
            if href and href not in spine:
                spine.append(href)

    def full(h: str) -> str:
        return f"{opf_dir}/{h}" if opf_dir else h

    return [full(h) for h in spine], (full(nav_href) if nav_href else None), opf_dir


def _epub_toc(z: zipfile.ZipFile, nav_href, opf_dir: str):
    """Prefer the EPUB3 nav document; fall back to the EPUB2 .ncx."""
    entries = []

    def full(h: str) -> str:
        return f"{opf_dir}/{h}" if opf_dir else h

    if nav_href and nav_href in z.namelist():
        try:
            xml = z.read(nav_href).decode("utf-8", errors="ignore")
            ns = os.path.dirname(nav_href)
            for m in re.finditer(r"<a[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                                 xml, flags=re.S | re.I):
                href, label = m.group(1), strip_html(m.group(2).encode())
                label = re.sub(r"\s+", " ", label).strip()
                if not label:
                    continue
                href = href.split("#", 1)[0]
                if ns:
                    href = f"{ns}/{href}"
                entries.append({"label": label, "href": href})
        except Exception:
            entries = []

    if not entries:
        for n in z.namelist():
            if not n.lower().endswith(".ncx"):
                continue
            try:
                root = ET.fromstring(z.read(n))
                nd = os.path.dirname(n)
                for np_ in root.iter():
                    if _local(np_.tag) != "navPoint":
                        continue
                    label, src = "", ""
                    for ch in np_.iter():
                        lt = _local(ch.tag)
                        if lt == "text" and not label:
                            label = (ch.text or "").strip()
                        if lt == "content":
                            src = (ch.get("src") or "").split("#", 1)[0]
                    if label:
                        entries.append({
                            "label": label,
                            "href": f"{nd}/{src}" if nd and src else src,
                        })
            except Exception:
                continue
            break

    return entries


def extract_epub(path: str):
    z = zipfile.ZipFile(path)
    opf = _opf_path(z)
    spine, nav_href, opf_dir = _parse_opf(z, opf)
    toc = _epub_toc(z, nav_href, opf_dir)

    if not spine:
        raise SystemExit("ERROR: EPUB spine is empty — cannot determine reading order")

    out = []
    for href in spine:
        if href not in z.namelist():
            continue
        body = strip_html(z.read(href))
        if not body:
            continue
        out.append(f"\n\n========== CHAPTER {href} ==========\n")
        out.append(body)

    return "\n".join(out), toc


# --------------------------------------------------------------------------- #
# PDF — PyMuPDF first (text + real TOC), pypdf as fallback
# --------------------------------------------------------------------------- #

def _load_pymupdf():
    """Import PyMuPDF. Prefer the modern `pymupdf` name; fall back to legacy `fitz`."""
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        import fitz  # deprecated shim, still present in older releases
        return fitz


def extract_pdf(path: str):
    text, toc = None, []

    try:
        fitz = _load_pymupdf()
        doc = fitz.open(path)
        pages = []
        for i, page in enumerate(doc):
            pages.append(f"\n\n========== PAGE {i + 1} ==========\n")
            try:
                pages.append(page.get_text() or "")
            except Exception as e:
                print(f"WARN: page {i + 1} extraction failed: {type(e).__name__}: {e}",
                      file=sys.stderr)
                pages.append("")
        text = "\n".join(pages)
        try:
            toc = [{"level": lv, "label": t, "page": p}
                   for lv, t, p in doc.get_toc()]
        except Exception:
            toc = []
        doc.close()
        if text and len(text.strip()) > 0:
            return text, toc
    except ImportError:
        pass
    except Exception as e:  # PyMuPDF present but broken (e.g. wrong-arch wheel)
        print(f"WARN: PyMuPDF failed ({type(e).__name__}: {e}); falling back to pypdf",
              file=sys.stderr)

    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit(
            "ERROR: neither PyMuPDF nor pypdf is installed.\n"
            "  Fix: <managed-venv>/bin/pip install pymupdf pypdf\n"
            "  (mirrors are per-machine — see SKILL.md 'Environment' before adding -i)"
        )

    reader = PdfReader(path)
    pages = []
    for i, page in enumerate(reader.pages):
        pages.append(f"\n\n========== PAGE {i + 1} ==========\n")
        try:
            pages.append(page.extract_text() or "")
        except Exception as e:
            print(f"WARN: page {i + 1} extraction failed: {type(e).__name__}: {e}",
                  file=sys.stderr)
            pages.append("")
    return "\n".join(pages), toc


def probe_pdf(path: str):
    """Sample pages and return (avg_chars_per_page, verdict)."""
    def verdict(avg: float) -> str:
        if avg >= 100:
            return "HAS text layer — extract directly, no OCR needed"
        if avg >= 20:
            return "THIN text layer — verify a few pages before trusting it"
        return "LIKELY SCANNED — needs OCR"

    sample_n = 20
    try:
        fitz = _load_pymupdf()
        doc = fitz.open(path)
        n = len(doc)
        step = max(1, n // sample_n)
        idxs = list(range(0, n, step))
        total = 0
        for i in idxs:
            try:
                total += len(doc[i].get_text() or "")
            except Exception:
                pass
        doc.close()
        avg = total / max(1, len(idxs))
        return round(avg, 1), verdict(avg)
    except Exception:
        pass

    from pypdf import PdfReader
    reader = PdfReader(path)
    n = len(reader.pages)
    step = max(1, n // sample_n)
    idxs = list(range(0, n, step))
    total = 0
    for i in idxs:
        try:
            total += len(reader.pages[i].extract_text() or "")
        except Exception:
            pass
    avg = total / max(1, len(idxs))
    return round(avg, 1), verdict(avg)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main():
    argv = sys.argv[1:]
    if not argv:
        raise SystemExit(
            "Usage: python3 extract_book.py <book_path> [output_txt] "
            "[--toc toc.json] [--probe]"
        )

    path = argv[0]
    rest = argv[1:]
    toc_out = None
    probe = False
    positional = []

    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--toc":
            toc_out = rest[i + 1] if i + 1 < len(rest) else "toc.json"
            i += 2
        elif a == "--probe":
            probe = True
            i += 1
        else:
            positional.append(a)
            i += 1

    if not os.path.exists(path):
        raise SystemExit(f"File not found: {path}")

    ext = os.path.splitext(path)[1].lower()

    if probe:
        if ext != ".pdf":
            print(f"NOTE: --probe only meaningful for PDF; '{ext}' is already a text format.")
        else:
            try:
                avg, verdict = probe_pdf(path)
                print(f"AVG_CHARS_PER_PAGE: {avg}")
                print(f"VERDICT: {verdict}")
            except Exception as e:
                print(f"PROBE FAILED: {type(e).__name__}: {e}")
        return

    toc = []
    if ext == ".epub":
        text, toc = extract_epub(path)
    elif ext == ".pdf":
        text, toc = extract_pdf(path)
    elif ext in (".txt", ".md"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    elif ext in (".html", ".xhtml", ".htm"):
        with open(path, "rb") as f:
            text = strip_html(f.read())
    else:
        raise SystemExit(f"Unsupported file type: {ext}")

    out_path = (
        positional[0]
        if positional
        else f"/tmp/{os.path.splitext(os.path.basename(path))[0]}_text.txt"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)

    if toc_out:
        with open(toc_out, "w", encoding="utf-8") as f:
            json.dump(toc, f, ensure_ascii=False, indent=2)
        print(f"TOC_ENTRIES: {len(toc)}")
        print(f"TOC: {toc_out}")

    print(f"CHARS: {len(text)}")
    print(f"OUTPUT: {out_path}")


if __name__ == "__main__":
    main()
