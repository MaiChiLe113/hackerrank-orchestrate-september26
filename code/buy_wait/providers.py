"""Optional evidence providers; stdlib HTTP, bounded retries and offline OCR.

OpenAI API shape verified against official Structured Outputs / Images guides.
Credentials are read only from OPENAI_API_KEY. Financial data is sent only when
the caller explicitly enables the model path. No live financial APIs are used.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable
from urllib import error, request

SCHEMA_VERSION = "financial-evidence-1"
FACT_TYPES = ("amount", "salary_change", "salary_delay", "income_stop", "income_confirmed", "recurring_expense_change", "due_date_change", "cancellation", "payment_status_change", "transaction_clarification")
FACT_FIELDS = {
    "fact_type": {"type": "string", "enum": list(FACT_TYPES)},
    "related_event_id": {"type": ["string", "null"]},
    "amount": {"type": ["string", "null"]},
    "currency": {"type": ["string", "null"]},
    "effective_date": {"type": ["string", "null"]},
    "status": {"type": ["string", "null"]},
    "category": {"type": ["string", "null"]},
    "description": {"type": ["string", "null"]},
    "scope": {"type": "string", "enum": ["single", "ongoing"]},
    "evidence_reference": {"type": "string"},
    "needs_review": {"type": "boolean"},
    "percentage": {"type": ["string", "null"]},
    "clarification": {"type": ["string", "null"]},
    "household_scope": {"type": "boolean"},
    "first_salary": {"type": "boolean"},
    "new_employer": {"type": "boolean"},
    "one_time_arrears": {"type": "boolean"},
}
EVIDENCE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "facts": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": FACT_FIELDS, "required": list(FACT_FIELDS)}},
        "unresolved": {"type": "array", "items": {"type": "string"}},
    }, "required": ["facts", "unresolved"],
}


class ProviderError(RuntimeError):
    """Safe diagnostic: never include request headers, input or response bodies."""


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, prefix=".evidence-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def content_key(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cost(usage: dict[str, Any], model: str) -> str | None:
    """Caller supplies dated USD per-million prices, including cached tokens."""
    try:
        prices = json.loads(os.environ.get("BUY_WAIT_MODEL_PRICES", "{}")).get(model)
        if not prices:
            return None
        cached = usage["cached_input_tokens"]
        if cached and "cached_input_per_million" not in prices:
            return None
        amount = ((Decimal(usage["input_tokens"] - cached) * Decimal(str(prices["input_per_million"])))
                  + Decimal(cached) * Decimal(str(prices.get("cached_input_per_million", 0)))
                  + Decimal(usage["output_tokens"]) * Decimal(str(prices["output_per_million"]))) / Decimal(1000000)
        return str(amount)
    except (ValueError, TypeError, KeyError, ArithmeticError):
        return None


class OpenAIProvider:
    """Responses API provider with injectable transport for network-free tests."""

    def __init__(self, model: str, cache_dir: Path, *, transport: Callable[..., Any] | None = None,
                 sleep: Callable[[float], None] = time.sleep, attempts: int = 3):
        if not model:
            raise ProviderError("Configure BUY_WAIT_MODEL or --model before enabling model extraction")
        self.model, self.cache_dir = model, Path(cache_dir)
        self.transport, self.sleep = transport or request.urlopen, sleep
        self.attempts = min(5, max(1, attempts))
        self.usage: list[dict[str, Any]] = []

    def extract(self, source_id: str, context: dict[str, Any], prompt: str,
                image_path: Path | None = None) -> dict[str, Any]:
        encoded_image = base64.b64encode(image_path.read_bytes()).decode("ascii") if image_path else None
        identity = {"provider": "openai", "model": self.model, "schema": EVIDENCE_SCHEMA,
                    "schema_version": SCHEMA_VERSION, "prompt": prompt, "context": context,
                    "image_sha256": hashlib.sha256(base64.b64decode(encoded_image)).hexdigest() if encoded_image else None}
        key = content_key(identity)
        cache_path = self.cache_dir / "model" / f"{key}.json"
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if cached["cache_key"] == key and isinstance(cached["result"], dict):
                    usage = dict(cached["usage"], cache_hit=True, source_id=source_id, fresh_calls=0)
                    self.usage.append(usage)
                    return cached["result"]
            except (ValueError, KeyError, TypeError):
                pass  # A partial/invalid cache never becomes financial evidence.
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ProviderError("OPENAI_API_KEY is not configured; no network request made")
        content = [{"type": "input_text", "text": json.dumps(context, ensure_ascii=False)}]
        if encoded_image:
            content.append({"type": "input_image", "image_url": f"data:image/png;base64,{encoded_image}", "detail": "high"})
        body = {"model": self.model, "store": False, "instructions": prompt,
                "input": [{"role": "user", "content": content}],
                "text": {"format": {"type": "json_schema", "name": "financial_evidence", "strict": True, "schema": EVIDENCE_SCHEMA}}}
        encoded = json.dumps(body).encode("utf-8")
        for attempt in range(self.attempts):
            req = request.Request("https://api.openai.com/v1/responses", data=encoded,
                                  headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
            try:
                with self.transport(req, timeout=90) as response:
                    payload = json.load(response)
                break
            except error.HTTPError as exc:
                transient = exc.code in {408, 409, 429} or exc.code >= 500
                self.usage.append({"provider": "openai", "model": self.model, "source_id": source_id, "cache_key": key,
                                   "cache_hit": False, "fresh_calls": 1, "status": f"http_{exc.code}", "tokens_unknown": True})
                if not transient or attempt + 1 == self.attempts:
                    raise ProviderError(f"OpenAI request failed with HTTP {exc.code}") from None
                self.sleep(min(8, 2 ** attempt))
            except (error.URLError, TimeoutError, OSError, ValueError):
                self.usage.append({"provider": "openai", "model": self.model, "source_id": source_id, "cache_key": key,
                                   "cache_hit": False, "fresh_calls": 1, "status": "transport_error", "tokens_unknown": True})
                # Transport timeout can mean the server completed; do not replay a billable call.
                raise ProviderError("OpenAI transport failed; outcome unknown, automatic replay disabled") from None
        raw_usage = payload.get("usage") or {}
        usage = {"provider": "openai", "model": payload.get("model", self.model), "source_id": source_id,
                 "response_id": payload.get("id"), "created_at": datetime.now(timezone.utc).isoformat(),
                 "cache_hit": False, "cache_key": key, "fresh_calls": 1, "status": payload.get("status", "unknown"),
                 "input_tokens": raw_usage.get("input_tokens", 0), "output_tokens": raw_usage.get("output_tokens", 0),
                 "cached_input_tokens": (raw_usage.get("input_tokens_details") or {}).get("cached_tokens", 0),
                 "tokens_unknown": not bool(raw_usage)}
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        usage["estimated_cost_usd"] = _cost(usage, self.model) if not usage["tokens_unknown"] else None
        self.usage.append(usage)
        if payload.get("status") != "completed":
            raise ProviderError("OpenAI response did not complete; extracted facts discarded")
        outputs = [c for item in payload.get("output", []) for c in item.get("content", [])]
        if any(c.get("type") == "refusal" for c in outputs):
            raise ProviderError("OpenAI declined extraction; facts unresolved")
        try:
            result = json.loads("".join(c.get("text", "") for c in outputs if c.get("type") == "output_text"))
        except ValueError:
            raise ProviderError("OpenAI response was not valid JSON") from None
        if not isinstance(result, dict) or set(result) != {"facts", "unresolved"}:
            raise ProviderError("OpenAI response did not match the evidence envelope")
        atomic_json(cache_path, {"cache_key": key, "result": result, "usage": usage})
        return result


_WINDOWS_OCR = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.IsGenericMethod })[0]
function Await-Result($op, $type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $task.Wait()
    $task.Result
}
$file = Await-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:BUY_WAIT_OCR_IMAGE)) ([Windows.Storage.StorageFile])
$stream = Await-Result ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
$decoder = Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await-Result ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'No OCR language pack installed' }
$result = Await-Result ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$words = @($result.Lines | ForEach-Object { $_.Words | ForEach-Object { @{text=$_.Text; x=$_.BoundingRect.X; y=$_.BoundingRect.Y; height=$_.BoundingRect.Height} } })
ConvertTo-Json -InputObject $words -Compress
$bitmap.Dispose()
$stream.Dispose()
'''


def local_ocr(path: Path, *, backend: str = "auto") -> tuple[str, str]:
    """Return text/backend without downloaded models or platform dependencies."""
    if backend not in {"auto", "windows", "tesseract", "none"}:
        raise ProviderError("Unsupported local OCR backend")
    if backend == "none":
        raise ProviderError("Local OCR disabled")
    tesseract = shutil.which("tesseract")
    if tesseract and backend in {"auto", "tesseract"}:
        result = subprocess.run([tesseract, str(path.resolve()), "stdout", "--psm", "6"], capture_output=True, timeout=60)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.decode("utf-8", errors="replace"), "tesseract"
    if os.name == "nt" and backend in {"auto", "windows"}:
        env = dict(os.environ, BUY_WAIT_OCR_IMAGE=str(path.resolve()))
        command = base64.b64encode(_WINDOWS_OCR.encode("utf-16-le")).decode("ascii")
        try:
            result = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
                                    env=env, capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            raise ProviderError("Windows OCR unavailable or timed out") from None
        if result.returncode == 0 and result.stdout.strip():
            try:
                words = json.loads(result.stdout.decode("utf-8-sig", errors="replace"))
                rows: list[list[dict[str, Any]]] = []
                for word in sorted(words, key=lambda w: w["y"] + w["height"] / 2):
                    center = word["y"] + word["height"] / 2
                    if rows:
                        prior = rows[-1]
                        prior_center = sum(w["y"] + w["height"] / 2 for w in prior) / len(prior)
                        tolerance = max(3, min(word["height"], max(w["height"] for w in prior)) * .55)
                    if rows and abs(center - prior_center) <= tolerance:
                        rows[-1].append(word)
                    else:
                        rows.append([word])
                return "\n".join(" ".join(w["text"] for w in sorted(row, key=lambda w: w["x"])) for row in rows), "windows_ocr"
            except (ValueError, KeyError, TypeError):
                raise ProviderError("Windows OCR returned malformed geometry") from None
    raise ProviderError("No usable local OCR result; configure OCR or enable vision extraction")
