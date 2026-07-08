"""Batch dubbing queue — POST videos with settings, process sequentially.

This is a lightweight batch orchestrator. Each job is a dub project that
runs through the same ingest→transcribe→translate→generate pipeline as
a manual dub, but driven by the queue instead of the UI.

The queue is in-memory (lives for the process lifetime). Jobs persist to
the SQLite `jobs` table for history, but the queue itself restarts empty
on backend restart — intentional, since GPU jobs can't be safely resumed.
"""
import os
import uuid
import time
import asyncio
import logging
import json
import re
from typing import Optional, List, Any

from fastapi import APIRouter, File, UploadFile, HTTPException, Form
from pydantic import BaseModel, Field

from core.config import DATA_DIR
from core import failure

router = APIRouter()
logger = logging.getLogger("omnivoice.batch")

# ── In-memory queue ─────────────────────────────────────────────────────

_queue: asyncio.Queue = None       # Lazily initialised
_worker_task: asyncio.Task = None  # Background consumer
_jobs: dict = {}                   # job_id → status dict
_templates: dict = {}              # template_id → render template dict
_render_batches: dict = {}         # batch_id → batch dict
_render_items: dict = {}           # item_id → per-template render item dict
_render_queue: asyncio.Queue = None
_render_worker_task: asyncio.Task = None


class TemplateTextBox(BaseModel):
    x: float = 0.1
    y: float = 0.72
    width: float = 0.8
    height: float = 0.18

class BatchRenderTemplateInput(BaseModel):
    name: str = Field(..., min_length=1)
    frame_image: str = ""
    font_file: str = ""
    font_family: str = "Inter"
    font_size: int = 64
    caption_text: str = "{caption}"
    text_box: TemplateTextBox = Field(default_factory=TemplateTextBox)
    horizontal_align: str = "center"
    vertical_align: str = "middle"
    text_color: str = "#ffffff"
    stroke_color: str = "#000000"
    stroke_width: int = 2
    intro_duration: float = 3.0
    intro_effect: str = "fade"

class BatchSourceInput(BaseModel):
    kind: str = "url"
    url: str = ""
    title: str = ""
    caption: str = ""
    file_path: str = ""

class BatchRenderOutputInput(BaseModel):
    local_root: str = "outputs/batches"
    drive_enabled: bool = False

class BatchRenderRequest(BaseModel):
    sources: list[BatchSourceInput]
    template_ids: list[str]
    settings: dict[str, Any] = Field(default_factory=dict)
    output: BatchRenderOutputInput = Field(default_factory=BatchRenderOutputInput)

class BatchJobStatus(BaseModel):
    id: str
    status: str  # "queued" | "running" | "done" | "failed" | "cancelled"
    filename: str
    langs: List[str]
    voice_id: Optional[str] = None
    preserve_bg: bool = True
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    progress: Optional[dict] = None


def _now() -> float:
    return time.time()

def _safe_slug(value: str, fallback: str = "item") -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", (value or "").strip()).strip("-").lower()
    return value[:80] or fallback

def _template_to_record(template_id: str, payload: BatchRenderTemplateInput, now: float) -> dict:
    data = payload.model_dump()
    data.update({"id": template_id, "created_at": now, "updated_at": now})
    return data

def _db_conn_or_none():
    try:
        from core.db import db_conn
        return db_conn
    except Exception:
        return None

def _ensure_render_tables() -> bool:
    db_conn = _db_conn_or_none()
    if db_conn is None:
        return False
    with db_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS batch_templates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS batch_render_batches (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                settings_json TEXT NOT NULL,
                output_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                finished_at REAL,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS batch_render_items (
                id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                source_index INTEGER NOT NULL,
                template_id TEXT NOT NULL,
                status TEXT NOT NULL,
                phase TEXT NOT NULL,
                progress REAL NOT NULL,
                error TEXT,
                output_path TEXT,
                drive_file_id TEXT,
                drive_link TEXT,
                source_artifact_key TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_batch_render_items_batch ON batch_render_items(batch_id);
            """
        )
    return True

def _persist_template(record: dict) -> None:
    if not _ensure_render_tables():
        return
    db_conn = _db_conn_or_none()
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO batch_templates (id, name, data_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (record["id"], record["name"], json.dumps(record), record["created_at"], record["updated_at"]),
        )

def _persist_render_batch(batch: dict) -> None:
    if not _ensure_render_tables():
        return
    db_conn = _db_conn_or_none()
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO batch_render_batches (id, status, settings_json, output_json, created_at, updated_at, finished_at, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (batch["id"], batch["status"], json.dumps(batch.get("settings", {})), json.dumps(batch.get("output", {})), batch["created_at"], batch["updated_at"], batch.get("finished_at"), batch.get("error")),
        )

def _persist_render_item(item: dict) -> None:
    if not _ensure_render_tables():
        return
    db_conn = _db_conn_or_none()
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO batch_render_items (id, batch_id, source_index, template_id, status, phase, progress, error, output_path, drive_file_id, drive_link, source_artifact_key, data_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item["id"], item["batch_id"], item["source_index"], item["template_id"], item["status"], item.get("phase", "queued"), float(item.get("progress", 0)), item.get("error"), item.get("output_path"), item.get("drive_file_id"), item.get("drive_link"), item["source_artifact_key"], json.dumps(item), item["created_at"], item["updated_at"]),
        )

def _load_render_state() -> None:
    if not _ensure_render_tables():
        return
    db_conn = _db_conn_or_none()
    with db_conn() as conn:
        for row in conn.execute("SELECT data_json FROM batch_templates"):
            record = json.loads(row["data_json"])
            _templates[record["id"]] = record
        for row in conn.execute("SELECT * FROM batch_render_batches"):
            _render_batches[row["id"]] = {"id": row["id"], "status": row["status"], "settings": json.loads(row["settings_json"] or "{}"), "output": json.loads(row["output_json"] or "{}"), "created_at": row["created_at"], "updated_at": row["updated_at"], "finished_at": row["finished_at"], "error": row["error"]}
        for row in conn.execute("SELECT data_json FROM batch_render_items"):
            item = json.loads(row["data_json"])
            _render_items[item["id"]] = item

def _batch_with_items(batch_id: str) -> dict:
    batch = _render_batches.get(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    items = [i for i in _render_items.values() if i["batch_id"] == batch_id]
    items.sort(key=lambda i: (i["source_index"], i["template_id"], i["created_at"]))
    return {**batch, "items": items}

def _target_langs_from_settings(settings: dict) -> list[str]:
    raw = (
        settings.get("langs")
        or settings.get("target_languages")
        or settings.get("target_language")
        or settings.get("target_lang")
        or settings.get("language_code")
        or settings.get("language")
        or "en"
    )
    if isinstance(raw, str):
        langs = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, list):
        langs = [str(part).strip() for part in raw if str(part).strip()]
    else:
        langs = []
    return langs or ["en"]

def _set_render_batch_status(batch_id: str) -> None:
    batch = _render_batches.get(batch_id)
    if not batch:
        return
    items = [item for item in _render_items.values() if item["batch_id"] == batch_id]
    if not items:
        batch["status"] = "done"
        batch["finished_at"] = _now()
    elif any(item["status"] == "running" for item in items):
        batch["status"] = "running"
        batch["finished_at"] = None
    elif any(item["status"] == "queued" for item in items):
        batch["status"] = "queued"
        batch["finished_at"] = None
    elif any(item["status"] == "failed" for item in items):
        batch["status"] = "failed"
        batch["finished_at"] = _now()
        errors = [item.get("error") for item in items if item.get("error")]
        batch["error"] = errors[0] if errors else "One or more render items failed"
    else:
        batch["status"] = "done"
        batch["finished_at"] = _now()
        batch["error"] = None
    batch["updated_at"] = _now()
    _persist_render_batch(batch)

def _set_render_item_state(item: dict, status: str, phase: str, progress: float, error: str | None = None) -> None:
    item["status"] = status
    item["phase"] = phase
    item["progress"] = max(0, min(100, float(progress)))
    item["error"] = error
    item["updated_at"] = _now()
    _persist_render_item(item)
    _set_render_batch_status(item["batch_id"])

def _ensure_render_queue():
    global _render_queue, _render_worker_task
    if _render_queue is None:
        _render_queue = asyncio.Queue()
        _render_worker_task = asyncio.ensure_future(_render_worker())

def _enqueue_render_batch(batch_id: str) -> None:
    _ensure_render_queue()
    _render_queue.put_nowait(batch_id)

async def _render_worker():
    while True:
        batch_id = await _render_queue.get()
        try:
            await _process_render_batch(batch_id)
        except Exception as e:
            logger.error("Render batch %s failed: %s", batch_id, e, exc_info=True)
            batch = _render_batches.get(batch_id)
            if batch:
                batch["status"] = "failed"
                batch["error"] = failure.build_failure(e, stage="batch-render", include_diagnostic=False)["reason"]
                batch["updated_at"] = _now()
                batch["finished_at"] = batch["updated_at"]
                _persist_render_batch(batch)
        finally:
            _render_queue.task_done()

async def _process_render_batch(batch_id: str) -> None:
    batch = _render_batches.get(batch_id)
    if not batch:
        return
    queued_items = [item for item in _render_items.values() if item["batch_id"] == batch_id and item["status"] == "queued"]
    if not queued_items:
        _set_render_batch_status(batch_id)
        return

    batch["status"] = "running"
    batch["updated_at"] = _now()
    batch["finished_at"] = None
    batch["error"] = None
    _persist_render_batch(batch)

    by_artifact: dict[str, list[dict]] = {}
    for item in queued_items:
        by_artifact.setdefault(item["source_artifact_key"], []).append(item)

    for source_items in by_artifact.values():
        source_items.sort(key=lambda item: item["created_at"])
        try:
            source_output = await _prepare_render_source(batch, source_items)
            for item in source_items:
                template = _templates.get(item["template_id"])
                if not template:
                    raise RuntimeError(f"Template not found: {item['template_id']}")
                _set_render_item_state(item, "running", "template", 85)
                await _render_template_output(source_output, item, template)
                _set_render_item_state(item, "done", "done", 100)
        except Exception as e:
            reason = failure.build_failure(e, stage="batch-render", include_diagnostic=False)["reason"]
            for item in source_items:
                if item["status"] != "done":
                    _set_render_item_state(item, "failed", "failed", item.get("progress", 0), reason)
    _set_render_batch_status(batch_id)

async def _prepare_render_source(batch: dict, source_items: list[dict]) -> str:
    first = source_items[0]
    source = first["source"]
    for item in source_items:
        _set_render_item_state(item, "running", "source", 10)

    source_video = await _resolve_render_source_video(first)
    for item in source_items:
        _set_render_item_state(item, "running", "dub", 30)

    langs = _target_langs_from_settings(batch.get("settings", {}))
    pipeline_job_id = f"render-{first['source_artifact_key']}"
    pipeline_job = {
        "id": pipeline_job_id,
        "status": "queued",
        "filename": source.get("title") or os.path.basename(source_video),
        "video_path": source_video,
        "langs": langs,
        "voice_id": batch.get("settings", {}).get("voice_id"),
        "preserve_bg": bool(batch.get("settings", {}).get("preserve_bg", True)),
        "created_at": _now(),
    }
    await _run_batch_pipeline(pipeline_job_id, pipeline_job)
    outputs = pipeline_job.get("outputs") or {}
    output = outputs.get(langs[0]) or next(iter(outputs.values()), None)
    if not output or not os.path.exists(output):
        raise RuntimeError("Dub pipeline did not produce an output video")
    for item in source_items:
        item["source_job_id"] = pipeline_job_id
        item["source_output_path"] = output
        _set_render_item_state(item, "running", "dub", 80)
    return output

async def _resolve_render_source_video(item: dict) -> str:
    source = item["source"]
    kind = source.get("kind", "url")
    if kind == "file":
        path = source.get("file_path", "")
        if not path or not os.path.exists(path):
            raise RuntimeError(f"Source file not found: {path}")
        return path
    if kind != "url":
        raise RuntimeError(f"Unsupported source kind: {kind}")
    url = source.get("url", "").strip()
    if not url:
        raise RuntimeError("URL source requires url")
    from services import dub_pipeline
    artifact_dir = os.path.join(DATA_DIR, "batch_render_sources", item["source_artifact_key"])
    os.makedirs(artifact_dir, exist_ok=True)
    video_path, title, _subs = await asyncio.get_running_loop().run_in_executor(
        None, lambda: dub_pipeline.yt_download_sync(url, artifact_dir)
    )
    item["source"]["title"] = item["source"].get("title") or title
    _persist_render_item(item)
    return video_path

def _ffmpeg_color(value: str, fallback: str) -> str:
    value = str(value or fallback).strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        return "0x" + value[1:]
    return value or fallback

def _drawtext_filter(template: dict, item: dict) -> str:
    box = template.get("text_box") or {}
    x = max(0, min(1, float(box.get("x", 0.1))))
    y = max(0, min(1, float(box.get("y", 0.72))))
    width = max(0.05, min(1, float(box.get("width", 0.8))))
    height = max(0.05, min(1, float(box.get("height", 0.18))))
    align = template.get("horizontal_align", "center")
    valign = template.get("vertical_align", "middle")
    font_size = max(8, min(240, int(template.get("font_size", 64))))
    text_x = f"{x}*w"
    if align == "center":
        text_x = f"{x}*w+({width}*w-text_w)/2"
    elif align == "right":
        text_x = f"{x}*w+{width}*w-text_w"
    text_y = f"{y}*h"
    if valign == "middle":
        text_y = f"{y}*h+({height}*h-text_h)/2"
    elif valign == "bottom":
        text_y = f"{y}*h+{height}*h-text_h"
    source = item.get("source") or {}
    source_title = source.get("title") or item.get("template_name") or "OmniVoice"
    source_caption = source.get("caption") or source_title
    template_name = item.get("template_name") or template.get("name") or "Template"
    caption = str(template.get("caption_text") or "{caption}")
    caption = caption.replace("{title}", str(source_title)).replace("{caption}", str(source_caption)).replace("{template}", str(template_name))
    safe_text = caption.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("\n", "\\n")
    font_color = _ffmpeg_color(template.get("text_color", "#ffffff"), "white")
    stroke_color = _ffmpeg_color(template.get("stroke_color", "#000000"), "black")
    return (
        "drawtext="
        f"text='{safe_text}':"
        f"x='{text_x}':y='{text_y}':fontsize={font_size}:"
        f"fontcolor={font_color}:bordercolor={stroke_color}:"
        f"borderw={int(template.get('stroke_width', 2))}:box=0"
    )

async def _render_template_output(source_video: str, item: dict, template: dict) -> str:
    import shutil
    import subprocess
    from services.ffmpeg_utils import find_ffmpeg

    output_path = item["output_path"]
    if not os.path.isabs(output_path):
        output_path = os.path.join(DATA_DIR, output_path)
        item["output_path"] = output_path
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    ffmpeg = find_ffmpeg()
    frame_image = template.get("frame_image") or ""
    if not ffmpeg:
        shutil.copy2(source_video, output_path)
        return output_path

    draw = _drawtext_filter(template, item)
    cmd = [ffmpeg, "-y", "-i", source_video]
    if frame_image and os.path.exists(frame_image):
        cmd += ["-i", frame_image, "-filter_complex", f"[1:v][0:v]scale2ref[frame][base];[base][frame]overlay=0:0,{draw}[v]", "-map", "[v]", "-map", "0:a?"]
    else:
        cmd += ["-vf", draw]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "copy", "-movflags", "+faststart", output_path]

    def _run():
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900, check=True)

    await asyncio.get_running_loop().run_in_executor(None, _run)
    return output_path
def _ensure_queue():
    """Lazy-init the asyncio queue + worker on first use."""
    global _queue, _worker_task
    if _queue is None:
        _queue = asyncio.Queue()
        _worker_task = asyncio.ensure_future(_worker())


async def _worker():
    """Process jobs one at a time from the queue."""
    while True:
        job_id = await _queue.get()
        job = _jobs.get(job_id)
        if not job or job["status"] == "cancelled":
            _queue.task_done()
            continue

        job["status"] = "running"
        job["started_at"] = time.time()
        logger.info("Batch job %s starting: %s", job_id, job["filename"])

        try:
            await _run_batch_pipeline(job_id, job)
            if job["status"] != "cancelled":
                job["status"] = "done"
                job["finished_at"] = time.time()
                logger.info(
                    "Batch job %s completed in %.1fs",
                    job_id, job["finished_at"] - job["started_at"],
                )
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            job["finished_at"] = time.time()
        except Exception as e:
            job["status"] = "failed"
            # plan-04 (#131): guaranteed non-empty, structured reason.
            job["error"] = failure.build_failure(e, stage="batch", include_diagnostic=False)["reason"]
            job["finished_at"] = time.time()
            logger.error("Batch job %s failed: %s", job_id, e, exc_info=True)
        finally:
            _queue.task_done()


def _set_progress(job, stage, percent=0, **extra):
    """Update a job's progress dict."""
    job["progress"] = {"stage": stage, "percent": percent, **extra}


async def _run_batch_pipeline(job_id: str, job: dict):
    """Full batch dub pipeline: extract → transcribe → translate → generate → mix → export."""
    import subprocess

    loop = asyncio.get_running_loop()
    video_path = job["video_path"]
    langs = job["langs"]
    batch_dir = os.path.join(DATA_DIR, "batch", job_id)
    os.makedirs(batch_dir, exist_ok=True)

    # ── 1. Extract audio ──────────────────────────────────────────────
    _set_progress(job, "extract", 0)
    audio_path = os.path.join(batch_dir, "audio.wav")

    from services.ffmpeg_utils import find_ffmpeg
    ffmpeg = find_ffmpeg()

    def _extract():
        subprocess.run(
            [ffmpeg, "-y", "-i", video_path,
             "-vn", "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
             audio_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=300, check=True,
        )
        # Get duration
        result = subprocess.run(
            [ffmpeg, "-i", audio_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=30,
        )
        import re
        match = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", result.stderr.decode("utf-8", errors="replace"))
        if match:
            h, m, s, cs = match.groups()
            return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100
        return 0.0

    duration = await loop.run_in_executor(None, _extract)
    job["duration"] = duration
    _set_progress(job, "extract", 100)

    if job["status"] == "cancelled":
        return

    # ── 2. Transcribe ─────────────────────────────────────────────────
    _set_progress(job, "transcribe", 0)

    from services.asr_backend import get_active_asr_backend
    from services.model_manager import _gpu_pool, _cpu_pool, run_on_gpu_pool_guarded
    from services.segmentation import (
        segment_transcript, assign_speakers_heuristic,
    )

    def _transcribe():
        backend = get_active_asr_backend()
        result = backend.transcribe(audio_path, word_timestamps=True)
        detected_lang = result.get("language", "en")
        segments = segment_transcript(result, duration=duration)
        segments = assign_speakers_heuristic(segments)
        for i, s in enumerate(segments):
            s["id"] = f"s{i:05x}"
            s.setdefault("text_original", s.get("text", ""))
        try:
            backend.unload()
        except Exception:
            pass
        return segments, detected_lang

    # Bound the batch transcribe (#730) so a wedged whisperx/CTranslate2 call
    # can't hold its GPU-pool worker forever and starve the rest of the backend
    # ("can't reach backend"); run_transcribe_guarded also resets the pool on
    # timeout to restore capacity.
    from services.asr_backend import run_transcribe_guarded
    segments, source_lang = await run_transcribe_guarded(_gpu_pool, _transcribe, what="Batch")
    source_lang = (source_lang or "en").split("_")[0][:2].lower()
    job["segments"] = segments
    job["source_lang"] = source_lang
    _set_progress(job, "transcribe", 100, segments_count=len(segments))

    if job["status"] == "cancelled" or not segments:
        if not segments:
            job["error"] = "Transcription produced no segments"
            job["status"] = "failed"
        return

    # ── 3. Translate + Generate per language ───────────────────────────
    total_langs = len(langs)
    outputs = {}

    for lang_idx, target_lang in enumerate(langs):
        if job["status"] == "cancelled":
            return

        # ── 3a. Translate ─────────────────────────────────────────────
        _set_progress(
            job, "translate",
            percent=int((lang_idx / total_langs) * 100),
            current_lang=target_lang,
        )

        translated_segments = list(segments)  # copy
        if target_lang != source_lang:
            try:
                def _translate_batch(segs, src, tgt):
                    """Translate segment texts via Google Translate."""
                    from deep_translator import GoogleTranslator
                    TRANSLATE_CODES = {
                        "en": "en", "es": "es", "fr": "fr", "de": "de",
                        "it": "it", "pt": "pt", "ru": "ru", "ja": "ja",
                        "ko": "ko", "zh": "zh-CN", "ar": "ar", "hi": "hi",
                        "tr": "tr", "pl": "pl", "nl": "nl", "sv": "sv",
                    }
                    src_code = TRANSLATE_CODES.get(src, src) or "auto"
                    tgt_code = TRANSLATE_CODES.get(tgt, tgt)
                    translator = GoogleTranslator(source=src_code, target=tgt_code)
                    out = []
                    for s in segs:
                        s_copy = dict(s)
                        text = s.get("text", "").strip()
                        if text:
                            try:
                                s_copy["text"] = translator.translate(text) or text
                            except Exception as e:
                                logger.warning("Translate seg failed: %s", e)
                        out.append(s_copy)
                    return out

                translated_segments = await loop.run_in_executor(
                    _cpu_pool, _translate_batch,
                    segments, source_lang, target_lang,
                )
            except ImportError:
                logger.warning("deep_translator not installed, skipping translation for %s", target_lang)
            except Exception as e:
                logger.warning("Translation failed for %s: %s, using original", target_lang, e)
                translated_segments = segments

        if job["status"] == "cancelled":
            return

        # ── 3b. Generate TTS ──────────────────────────────────────────
        _set_progress(
            job, "generate",
            percent=int((lang_idx / total_langs) * 100),
            current_lang=target_lang,
            current_segment=0,
            total_segments=len(translated_segments),
        )

        from services.model_manager import get_model
        from services.audio_dsp import apply_mastering, normalize_audio
        from services.audio_io import atomic_save_wav
        import torch

        _model = await get_model()
        sr = _model.sampling_rate
        total_samples = int(duration * sr)
        full_audio = torch.zeros(1, total_samples)
        total_segs = len(translated_segments)

        for i, seg in enumerate(translated_segments):
            if job["status"] == "cancelled":
                return

            _set_progress(
                job, "generate",
                percent=int(((lang_idx + (i / total_segs)) / total_langs) * 100),
                current_lang=target_lang,
                current_segment=i + 1,
                total_segments=total_segs,
            )

            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            seg_duration = seg_end - seg_start
            seg_text = seg.get("text", "").strip()

            if seg_duration <= 0.05 or not seg_text:
                continue

            def _gen(text=seg_text, lang=target_lang, dur=seg_duration):
                ref_audio = None
                ref_text = None

                # Use voice_id if provided
                if job.get("voice_id"):
                    from core.db import db_conn
                    from core.config import VOICES_DIR as _VD
                    with db_conn() as conn:
                        row = conn.execute(
                            "SELECT * FROM voice_profiles WHERE id=?",
                            (job["voice_id"],),
                        ).fetchone()
                    if row:
                        if row["is_locked"] and row["locked_audio_path"]:
                            ref_audio = os.path.join(_VD, row["locked_audio_path"])
                        elif row["ref_audio_path"]:
                            ref_audio = os.path.join(_VD, row["ref_audio_path"])
                        ref_text = row.get("ref_text")

                try:
                    audios = _model.generate(
                        text=text, language=lang,
                        ref_audio=ref_audio, ref_text=ref_text,
                        duration=dur, num_step=16,
                        guidance_scale=2.0, speed=1.0,
                        denoise=True, postprocess_output=True,
                    )
                    audio_out = audios[0]
                    # TODO(#312): this route runs the OmniVoice model directly (not the active
                    # backend), so VoxCPM2 never reaches it. When these routes become
                    # engine-aware, guard with `if not getattr(backend, "applies_own_mastering", False)`.
                    mastered = apply_mastering(
                        audio_out,
                        sample_rate=sr,
                    )
                    return normalize_audio(mastered, target_dBFS=-2.0)
                except Exception as e:
                    logger.warning("TTS failed for seg %d (lang=%s): %s", i, lang, e)
                    return torch.zeros(1, int(dur * sr))

            try:
                # Bounded + pool-reset on hang so a wedged batch segment can't
                # starve the GPU pool and brick the backend (#730 class).
                audio_tensor = await run_on_gpu_pool_guarded(_gen, what="Batch generate")

                # Fit to slot
                target_samples_seg = int(seg_duration * sr)
                current_samples = audio_tensor.shape[-1]
                if target_samples_seg > current_samples:
                    audio_tensor = torch.nn.functional.pad(
                        audio_tensor, (0, target_samples_seg - current_samples)
                    )
                elif current_samples > target_samples_seg:
                    audio_tensor = audio_tensor[..., :target_samples_seg]

                # Crossfade
                fade_samples = int(0.015 * sr)
                wl = audio_tensor.shape[-1]
                if wl > fade_samples * 2:
                    ramp_up = torch.linspace(0, 1, fade_samples)
                    ramp_down = torch.linspace(1, 0, fade_samples)
                    audio_tensor[0, :fade_samples] *= ramp_up
                    audio_tensor[0, -fade_samples:] *= ramp_down

                s_idx = int(seg_start * sr)
                e_idx = min(s_idx + wl, total_samples)
                full_audio[:, s_idx:e_idx] += audio_tensor[:, :e_idx - s_idx]

            except Exception as e:
                logger.warning("Batch TTS seg %d failed: %s", i, e)

        # ── 3c. Save dubbed audio track ───────────────────────────────
        # Same assembly pattern as dub_generate.py:390 — `full_audio` is a
        # zero-init tensor that gets +='d from torch.cat-style slices, so
        # it can land non-contiguous + out-of-range. Go through the
        # audited + atomic helper to defend against #48 silent corruption
        # and partial-write truncation simultaneously.
        track_path = os.path.join(batch_dir, f"dubbed_{target_lang}.wav")
        atomic_save_wav(track_path, full_audio, sr)

        # ── 3d. Mix with original video ───────────────────────────────
        _set_progress(
            job, "mix",
            percent=int(((lang_idx + 0.8) / total_langs) * 100),
            current_lang=target_lang,
        )

        output_path = os.path.join(batch_dir, f"output_{target_lang}.mp4")

        def _mix(bg=job.get("preserve_bg", True)):
            if bg:
                # Mix dubbed audio with original background
                subprocess.run(
                    [ffmpeg, "-y",
                     "-i", video_path,
                     "-i", track_path,
                     "-filter_complex",
                     "[0:a]volume=0.15[bg];[1:a]volume=1.0[dub];[bg][dub]amix=inputs=2:duration=first[out]",
                     "-map", "0:v", "-map", "[out]",
                     "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                     "-shortest", output_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=600, check=True,
                )
            else:
                # Replace audio entirely
                subprocess.run(
                    [ffmpeg, "-y",
                     "-i", video_path,
                     "-i", track_path,
                     "-map", "0:v", "-map", "1:a",
                     "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                     "-shortest", output_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=600, check=True,
                )

        await loop.run_in_executor(None, _mix)
        outputs[target_lang] = output_path

    job["outputs"] = outputs
    _set_progress(job, "done", 100)


# ── Batch render templates + per-template items ──────────────────────────

@router.get("/batch/templates")
def list_batch_templates():
    _load_render_state()
    return sorted(_templates.values(), key=lambda t: t.get("created_at", 0), reverse=True)

@router.post("/batch/templates")
def create_batch_template(payload: BatchRenderTemplateInput):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Template name is required")
    now = _now()
    record = _template_to_record(str(uuid.uuid4())[:12], payload, now)
    record["name"] = name
    _templates[record["id"]] = record
    _persist_template(record)
    return record

@router.get("/batch/templates/{template_id}")
def get_batch_template(template_id: str):
    _load_render_state()
    template = _templates.get(template_id)
    if not template:
        raise HTTPException(404, "Template not found")
    return template

@router.patch("/batch/templates/{template_id}")
def update_batch_template(template_id: str, payload: dict):
    _load_render_state()
    template = _templates.get(template_id)
    if not template:
        raise HTTPException(404, "Template not found")
    allowed = set(BatchRenderTemplateInput.model_fields.keys())
    for key, value in payload.items():
        if key in allowed:
            template[key] = value
    if not str(template.get("name", "")).strip():
        raise HTTPException(status_code=422, detail="Template name is required")
    template["updated_at"] = _now()
    _templates[template_id] = template
    _persist_template(template)
    return template

@router.delete("/batch/templates/{template_id}")
def delete_batch_template(template_id: str):
    _load_render_state()
    if template_id not in _templates:
        raise HTTPException(404, "Template not found")
    _templates.pop(template_id, None)
    db_conn = _db_conn_or_none()
    if db_conn and _ensure_render_tables():
        with db_conn() as conn:
            conn.execute("DELETE FROM batch_templates WHERE id=?", (template_id,))
    return {"deleted": True}

@router.post("/batch/render-batches")
async def create_render_batch(payload: BatchRenderRequest):
    _load_render_state()
    if not payload.sources:
        raise HTTPException(422, "At least one source is required")
    if not payload.template_ids:
        raise HTTPException(422, "At least one template is required")
    missing = [tid for tid in payload.template_ids if tid not in _templates]
    if missing:
        raise HTTPException(404, f"Template not found: {missing[0]}")

    now = _now()
    batch_id = str(uuid.uuid4())[:12]
    batch = {
        "id": batch_id,
        "status": "queued",
        "settings": payload.settings,
        "output": payload.output.model_dump(),
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
        "error": None,
    }
    _render_batches[batch_id] = batch
    _persist_render_batch(batch)

    for source_index, source in enumerate(payload.sources):
        if source.kind == "url" and not source.url.strip():
            raise HTTPException(422, "URL source requires url")
        if source.kind == "file" and not source.file_path.strip():
            raise HTTPException(422, "File source requires file_path")
        source_title = source.title or source.url.rsplit("/", 1)[-1] or source.file_path.rsplit(os.sep, 1)[-1] or f"source-{source_index + 1}"
        artifact_key = f"source-{source_index}-{uuid.uuid4().hex[:8]}"
        for template_id in payload.template_ids:
            template = _templates[template_id]
            template_slug = _safe_slug(template.get("name", template_id), template_id)
            title_slug = _safe_slug(source_title, f"item-{source_index + 1}")
            output_path = os.path.join(
                payload.output.local_root,
                batch_id,
                template_slug,
                f"{title_slug}.mp4",
            )
            item_id = str(uuid.uuid4())[:12]
            item = {
                "id": item_id,
                "batch_id": batch_id,
                "source_index": source_index,
                "source": source.model_dump(),
                "template_id": template_id,
                "template_name": template.get("name", template_id),
                "status": "queued",
                "phase": "queued",
                "progress": 0,
                "error": None,
                "output_path": output_path,
                "drive_file_id": None,
                "drive_link": None,
                "source_artifact_key": artifact_key,
                "created_at": now,
                "updated_at": now,
            }
            _render_items[item_id] = item
            _persist_render_item(item)

    _enqueue_render_batch(batch_id)
    return _batch_with_items(batch_id)

@router.get("/batch/render-batches")
def list_render_batches(status: Optional[str] = None, limit: int = 50):
    _load_render_state()
    rows = list(_render_batches.values())
    if status:
        rows = [b for b in rows if b.get("status") == status]
    rows.sort(key=lambda b: b.get("created_at", 0), reverse=True)
    return [{**b, "items": [i for i in _render_items.values() if i["batch_id"] == b["id"]]} for b in rows[: max(1, min(limit, 200))]]

@router.get("/batch/render-batches/{batch_id}")
def get_render_batch(batch_id: str):
    _load_render_state()
    return _batch_with_items(batch_id)

@router.delete("/batch/render-batches/{batch_id}")
def delete_render_batch(batch_id: str):
    _load_render_state()
    if batch_id not in _render_batches:
        raise HTTPException(404, "Batch not found")
    _render_batches.pop(batch_id, None)
    for item_id in [i for i, item in _render_items.items() if item["batch_id"] == batch_id]:
        _render_items.pop(item_id, None)
    db_conn = _db_conn_or_none()
    if db_conn and _ensure_render_tables():
        with db_conn() as conn:
            conn.execute("DELETE FROM batch_render_items WHERE batch_id=?", (batch_id,))
            conn.execute("DELETE FROM batch_render_batches WHERE id=?", (batch_id,))
    return {"deleted": True}

@router.post("/batch/render-items/{item_id}/rerun")
async def rerun_render_item(item_id: str):
    _load_render_state()
    item = _render_items.get(item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    if item["status"] not in ("failed", "cancelled", "done"):
        return item
    item["status"] = "queued"
    item["phase"] = "queued"
    item["progress"] = 0
    item["error"] = None
    item["updated_at"] = _now()
    _render_items[item_id] = item
    _persist_render_item(item)
    batch = _render_batches.get(item["batch_id"])
    if batch and batch.get("status") in ("failed", "done", "cancelled"):
        batch["status"] = "queued"
        batch["updated_at"] = item["updated_at"]
        batch["finished_at"] = None
        batch["error"] = None
        _persist_render_batch(batch)
    _enqueue_render_batch(item["batch_id"])
    return item

@router.delete("/batch/render-items/{item_id}")
def delete_render_item(item_id: str):
    _load_render_state()
    if item_id not in _render_items:
        raise HTTPException(404, "Item not found")
    _render_items.pop(item_id, None)
    db_conn = _db_conn_or_none()
    if db_conn and _ensure_render_tables():
        with db_conn() as conn:
            conn.execute("DELETE FROM batch_render_items WHERE id=?", (item_id,))
    return {"deleted": True}
# ── Endpoints ───────────────────────────────────────────────────────────

@router.post("/batch/enqueue")
async def enqueue_batch_job(
    video: UploadFile = File(...),
    langs: str = Form("es"),            # comma-separated lang codes
    voice_id: Optional[str] = Form(None),
    preserve_bg: bool = Form(True),
):
    """Enqueue a video for batch dubbing.

    The video is saved to disk and a job is added to the queue.
    Returns the job ID for status polling.
    """
    _ensure_queue()

    job_id = str(uuid.uuid4())[:12]
    lang_list = [l.strip() for l in langs.split(",") if l.strip()]
    if not lang_list:
        raise HTTPException(400, "At least one target language is required")

    # Save the uploaded video
    batch_dir = os.path.join(DATA_DIR, "batch")
    os.makedirs(batch_dir, exist_ok=True)
    ext = os.path.splitext(video.filename or "video.mp4")[1] or ".mp4"
    video_path = os.path.join(batch_dir, f"{job_id}{ext}")

    with open(video_path, "wb") as f:
        content = await video.read()
        f.write(content)

    job = {
        "id": job_id,
        "status": "queued",
        "filename": video.filename or f"{job_id}{ext}",
        "video_path": video_path,
        "langs": lang_list,
        "voice_id": voice_id,
        "preserve_bg": preserve_bg,
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
        "error": None,
        "progress": None,
    }
    _jobs[job_id] = job
    await _queue.put(job_id)

    logger.info("Batch job %s enqueued: %s → %s", job_id, video.filename, lang_list)
    return {"job_id": job_id, "status": "queued", "queue_position": _queue.qsize()}


@router.get("/batch/jobs")
def list_batch_jobs(status: Optional[str] = None, limit: int = 50):
    """List batch jobs, optionally filtered by status."""
    jobs = list(_jobs.values())
    if status:
        if status == "active":
            jobs = [j for j in jobs if j["status"] in ("queued", "running")]
        else:
            jobs = [j for j in jobs if j["status"] == status]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jobs[:limit]


@router.get("/batch/jobs/{job_id}")
def get_batch_job(job_id: str):
    """Get the status of a specific batch job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.post("/batch/jobs/{job_id}/cancel")
def cancel_batch_job(job_id: str):
    """Cancel a queued or running batch job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] in ("done", "failed", "cancelled"):
        return {"already": job["status"]}
    job["status"] = "cancelled"
    job["finished_at"] = time.time()
    return {"cancelled": True}


@router.delete("/batch/jobs/{job_id}")
def delete_batch_job(job_id: str):
    """Delete a batch job record and its video file."""
    job = _jobs.pop(job_id, None)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("video_path") and os.path.exists(job["video_path"]):
        try:
            os.remove(job["video_path"])
        except Exception:
            pass
    return {"deleted": True}


@router.get("/batch/download/{job_id}/{lang}")
def download_batch_output(job_id: str, lang: str):
    """Download a completed batch job's output video for a given language."""
    from fastapi.responses import FileResponse

    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "done":
        raise HTTPException(400, f"Job is {job['status']}, not done")

    outputs = job.get("outputs", {})
    path = outputs.get(lang)
    if not path or not os.path.exists(path):
        raise HTTPException(404, f"No output for language '{lang}'")

    filename = f"{os.path.splitext(job['filename'])[0]}_{lang}.mp4"
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=filename,
    )



