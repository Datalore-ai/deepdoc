import os, io, base64, hashlib
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
import fitz
import pdfplumber

from deepresearch.converters import (
    convert_doc_to_docx_linux,
    convert_image_to_pdf,
    convert_text_to_pdf,
    convert_docx_to_pdf,
    convert_pptx_to_pdf,
)
try:
    from mistralai import Mistral
except Exception:
    Mistral = None

SUPPORTED_EXTENSIONS = { "pdf","jpg","jpeg","png","gif","webp","bmp", "txt","md","doc","docx","pptx"}
MAX_BASE64_ENCODE_BYTES = 50 * 1024 * 1024
OCR_MODEL = "mistral-ocr-latest"

SKIPPED_DETAILS: List[Dict[str, str]] = []

def convert_to_pdf(file_bytes: bytes, filename: str) -> Optional[bytes]:
    ext = filename.lower().split(".")[-1]

    if ext not in SUPPORTED_EXTENSIONS:
        SKIPPED_DETAILS.append({"file": filename, "reason": "Unsupported file type"})
        return None

    if ext == "pdf":
        return file_bytes

    if ext in {"jpg", "jpeg", "png", "gif", "webp", "bmp"}:
        return convert_image_to_pdf(file_bytes)

    if ext in {"txt", "md"}:
        return convert_text_to_pdf(file_bytes)

    if ext in {"doc", "docx"}:
        if ext == "doc" and os.name == "posix":
            new = convert_doc_to_docx_linux(file_bytes)
            if not new:
                SKIPPED_DETAILS.append({"file": filename, "reason": "Failed .doc → .docx"})
                return None
            file_bytes = new
        return convert_docx_to_pdf(file_bytes)

    if ext == "pptx":
        return convert_pptx_to_pdf(file_bytes)

    return None

def _create_mistral_client(api_key: Optional[str] = None):      #  OCR + TEXT EXTRACTION
    key = api_key or os.getenv("MISTRAL_API_KEY")
    if not key or Mistral is None:
        return None
    try:
        return Mistral(api_key=key)
    except Exception:
        return None

def _encode_pdf_base64(b: bytes) -> Optional[str]:
    if len(b) > MAX_BASE64_ENCODE_BYTES:
        return None
    try:
        return base64.b64encode(b).decode()
    except Exception:
        return None

def _process_ocr_page(i, resp):
    try:
        if resp and hasattr(resp, "pages") and i < len(resp.pages):
            pg = resp.pages[i]
            return getattr(pg, "markdown", None) or getattr(pg, "text", None) or ""
        return ""
    except Exception as e:
        return f"Error page {i+1}: {e}"

def extract_text_from_pdf(pdf_bytes: bytes, advanced: bool = True, client=None):
    if not advanced:
        try:
            with fitz.open(stream=pdf_bytes, filetype="pdf") as d:
                return [p.get_text() for p in d]
        except Exception as e:
            return [f"Simple extraction error: {e}"]

    try:                    # page count
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            total = len(pdf.pages)
    except Exception:
        try:
            with fitz.open(stream=pdf_bytes, filetype="pdf") as d:
                total = len(d)
        except Exception as e:
            return [f"Error reading PDF: {e}"]

    encoded = _encode_pdf_base64(pdf_bytes)
    client = client or _create_mistral_client()

    if not encoded or not client:
        return extract_text_from_pdf(pdf_bytes, advanced=False)

    try:
        resp = client.ocr.process(
            model=OCR_MODEL,
            document={"type":"document_url","document_url":f"data:application/pdf;base64,{encoded}"},
            include_image_base64=True
        )
    except Exception as e:
        return [f"OCR error: {e}"]

    return [_process_ocr_page(i, resp) for i in range(total)]

#  CHUNKING
def _meta(filename, filetype, cid, page_number, content, prev=None):
    h = hashlib.sha256(f"{filename}-{filetype}-{cid}-{content}".encode()).hexdigest()
    return {
        "filename": filename,
        "filetype": filetype,
        "chunk_id": f"{filename}_{filetype}_{cid}",
        "page_number": page_number,
        "page_content": content,
        "chunk_hash": h,
        "previous_chunk_hash": prev,
    }

def _chunk_pages(filename, filetype, pages, start=1, prev=None):
    chunks = []
    cid = start
    last = prev
    for i, p in enumerate(pages):
        m = _meta(filename, "pdf", cid, i + 1, p, last)
        chunks.append(m)
        last = m["chunk_hash"]
        cid += 1
    return chunks, cid, last

def _chunk_words(filename, filetype, pages, size, buffer, start=1, prev=None):
    if not size or size <= 0:
        return [], start, prev

    words = " ".join(pages).split()
    chunks = []
    cid = start
    last = prev

    for i in range(0, len(words), size):
        end = min(i + size + buffer, len(words))
        content = " ".join(words[i:end])
        pn = (i // size) + 1
        m = _meta(filename, filetype, cid, pn, content, last)
        chunks.append(m)
        last = m["chunk_hash"]
        cid += 1

    return chunks, cid, last

#  MAIN PROCESSING
def create_chunks(directory_path: str, auto_continue=False, chunk_size=None, buffer=8, return_summary=False):

    p = Path(os.path.abspath(os.path.expanduser(directory_path)))
    if not p.exists() or not p.is_dir():
        raise ValueError(f"Directory not found: {p}")

    SKIPPED_DETAILS.clear()
    files = [f for f in p.iterdir() if f.is_file()]

    chunks = []
    errors = []
    skipped_files = []
    processed = 0
    ocr_used = False

    valid_files = []            # preconversion phase
    for fp in files:
        try:
            data = fp.read_bytes()
        except Exception as e:
            errors.append({"file": fp.name, "reason": f"Read error: {e}"})
            continue

        if convert_to_pdf(data, fp.name) is None:
            skipped_files.append(fp.name)
        else:
            valid_files.append(fp)
            processed += 1

    summary = {
        "processed": processed,
        "skipped": len(skipped_files),
        "errors": len(errors),
        "skipped_details": SKIPPED_DETAILS.copy(),
        "error_details": errors,
    }

    if not auto_continue:
        print("\nPre-Processing Summary")
        print("Processable:", processed)
        print("Skipped:", len(skipped_files))
        if SKIPPED_DETAILS:
            print("\nReasons:")
            for s in SKIPPED_DETAILS:
                print("-", s)

        if errors:
            print("\nErrors:")
            for e in errors:
                print("-", e)

        cont = input("\nContinue?: ").strip().lower()
        if cont not in ("y", "yes"):
            print("Cancelled.")
            return ([], summary) if return_summary else []

    # main extraction + chunking
    for fp in valid_files:
        name = fp.name
        ext = name.split(".")[-1].lower()
        try:
            raw = fp.read_bytes()
        except Exception as e:
            errors.append({"file": name, "reason": f"Read error in second pass: {e}"})
            continue

        pdf_bytes = convert_to_pdf(raw, name)
        if pdf_bytes is None:
            skipped_files.append(name)
            continue

        encoded = _encode_pdf_base64(pdf_bytes)
        client = _create_mistral_client()
        is_ocr = bool(encoded and client)

        try:
            if ext in {"txt", "md"}:
                pages = extract_text_from_pdf(pdf_bytes, advanced=False)
            else:
                pages = extract_text_from_pdf(pdf_bytes, advanced=True, client=client)
                if is_ocr:
                    ocr_used = True
        except Exception as e:
            pages = [f"Extraction error: {e}"]
            errors.append({"file": name, "reason": f"Extraction error: {e}"})

        # chunk by words OR full pages
        if chunk_size:
            new_chunks, _, _ = _chunk_words(name, ext, pages, chunk_size, buffer)
        else:
            new_chunks, _, _ = _chunk_pages(name, ext, pages)

        chunks.extend(new_chunks)

    summary.update({
        "chunks_generated": len(chunks),
        "ocr_enabled": ocr_used,
        "skipped_details": SKIPPED_DETAILS.copy(),
    })

    print("\nDone.")
    print("Files processed:", processed)
    print("Chunks created:", len(chunks))

    return (chunks, summary) if return_summary else chunks

if __name__ == "__main__":
    d = input("Enter directory path: ").strip()
    chunks = create_chunks(d, auto_continue=False)
    print("Chunks:", len(chunks))
