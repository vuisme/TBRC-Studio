"""Tests for batch dubbing API endpoints.

These tests create a minimal FastAPI app with only the batch router,
avoiding the heavy main app import chain. The batch module is
lightweight — it only imports os, uuid, time, asyncio, logging,
fastapi, and pydantic at module level.
"""
import asyncio
import io
import os
import sys
import pytest

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub core.config before batch imports it
import types
config_mod = types.ModuleType("core.config")
config_mod.DATA_DIR = "/tmp/omnivoice_test_data"
sys.modules["core.config"] = config_mod

from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routers.batch import router, _jobs, _render_batches, _render_items, _templates, _set_progress, _process_render_batch, _drawtext_filter


@pytest.fixture(autouse=True)
def reset_state():
    """Clear in-memory state between tests and disable the workers."""
    import api.routers.batch as batch
    batch._jobs.clear()
    batch._templates.clear()
    batch._render_batches.clear()
    batch._render_items.clear()
    batch._queue = None
    batch._render_queue = None
    if batch._worker_task and not batch._worker_task.done():
        batch._worker_task.cancel()
    if batch._render_worker_task and not batch._render_worker_task.done():
        batch._render_worker_task.cancel()
    batch._worker_task = None
    batch._render_worker_task = None

    original_ensure = batch._ensure_queue
    original_enqueue_render = batch._enqueue_render_batch

    def _test_ensure_queue():
        if batch._queue is None:
            import asyncio

            async def _noop():
                while True:
                    await batch._queue.get()
                    batch._queue.task_done()

            batch._queue = asyncio.Queue()
            batch._worker_task = asyncio.ensure_future(_noop())

    batch._ensure_queue = _test_ensure_queue
    batch._enqueue_render_batch = lambda batch_id: None
    yield
    batch._ensure_queue = original_ensure
    batch._enqueue_render_batch = original_enqueue_render
    batch._jobs.clear()
    batch._templates.clear()
    batch._render_batches.clear()
    batch._render_items.clear()
    batch._render_queue = None
@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def fake_video():
    return b"\x00\x00\x00\x1c\x66\x74\x79\x70" + b"\x00" * 1016  # 1KB


def _enqueue(client, video_bytes, langs="es", voice_id="", preserve_bg="true"):
    return client.post(
        "/batch/enqueue",
        files={"video": ("test.mp4", io.BytesIO(video_bytes), "video/mp4")},
        data={"langs": langs, "preserve_bg": preserve_bg, **({"voice_id": voice_id} if voice_id else {})},
    )


class TestEnqueue:
    def test_returns_job_id(self, client, fake_video):
        resp = _enqueue(client, fake_video, "es,fr")
        assert resp.status_code == 200
        body = resp.json()
        assert "job_id" in body
        assert body["status"] == "queued"

    def test_empty_langs_fails(self, client, fake_video):
        """Empty langs string should return 400."""
        # Send with no langs field at all
        resp = client.post(
            "/batch/enqueue",
            files={"video": ("test.mp4", io.BytesIO(fake_video), "video/mp4")},
            data={"langs": ",,,", "preserve_bg": "true"},
        )
        assert resp.status_code == 400

    def test_multi_lang_splits(self, client, fake_video):
        resp = _enqueue(client, fake_video, "es,fr,de")
        job_id = resp.json()["job_id"]
        job = client.get(f"/batch/jobs/{job_id}").json()
        assert job["langs"] == ["es", "fr", "de"]

    def test_preserves_filename(self, client, fake_video):
        resp = _enqueue(client, fake_video)
        job_id = resp.json()["job_id"]
        job = client.get(f"/batch/jobs/{job_id}").json()
        assert job["filename"] == "test.mp4"


class TestListJobs:
    def test_empty(self, client):
        resp = client.get("/batch/jobs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_enqueued(self, client, fake_video):
        _enqueue(client, fake_video)
        _enqueue(client, fake_video)
        jobs = client.get("/batch/jobs").json()
        assert len(jobs) == 2

    def test_filter_active(self, client, fake_video):
        r1 = _enqueue(client, fake_video).json()
        r2 = _enqueue(client, fake_video).json()
        client.post(f"/batch/jobs/{r2['job_id']}/cancel")

        active = client.get("/batch/jobs?status=active").json()
        assert len(active) == 1
        assert active[0]["id"] == r1["job_id"]

    def test_filter_cancelled(self, client, fake_video):
        r = _enqueue(client, fake_video).json()
        client.post(f"/batch/jobs/{r['job_id']}/cancel")

        cancelled = client.get("/batch/jobs?status=cancelled").json()
        assert len(cancelled) == 1


class TestGetJob:
    def test_not_found(self, client):
        assert client.get("/batch/jobs/nope").status_code == 404

    def test_found(self, client, fake_video):
        r = _enqueue(client, fake_video).json()
        job = client.get(f"/batch/jobs/{r['job_id']}").json()
        assert job["id"] == r["job_id"]
        assert job["status"] == "queued"


class TestCancelJob:
    def test_cancel_queued(self, client, fake_video):
        r = _enqueue(client, fake_video).json()
        resp = client.post(f"/batch/jobs/{r['job_id']}/cancel")
        assert resp.json()["cancelled"] is True
        job = client.get(f"/batch/jobs/{r['job_id']}").json()
        assert job["status"] == "cancelled"

    def test_cancel_already_done(self, client, fake_video):
        r = _enqueue(client, fake_video).json()
        _jobs[r["job_id"]]["status"] = "done"
        resp = client.post(f"/batch/jobs/{r['job_id']}/cancel")
        assert resp.json()["already"] == "done"

    def test_cancel_not_found(self, client):
        assert client.post("/batch/jobs/nope/cancel").status_code == 404


class TestDeleteJob:
    def test_delete_cancelled(self, client, fake_video):
        r = _enqueue(client, fake_video).json()
        client.post(f"/batch/jobs/{r['job_id']}/cancel")
        resp = client.delete(f"/batch/jobs/{r['job_id']}")
        assert resp.json()["deleted"] is True
        assert client.get(f"/batch/jobs/{r['job_id']}").status_code == 404

    def test_delete_not_found(self, client):
        assert client.delete("/batch/jobs/nope").status_code == 404


class TestSetProgress:
    def test_basic(self):
        job = {}
        _set_progress(job, "transcribe", 50, segments_count=10)
        assert job["progress"]["stage"] == "transcribe"
        assert job["progress"]["percent"] == 50
        assert job["progress"]["segments_count"] == 10

    def test_overwrite(self):
        job = {"progress": {"stage": "extract", "percent": 100}}
        _set_progress(job, "generate", 25, current_lang="es")
        assert job["progress"]["stage"] == "generate"
        assert job["progress"]["current_lang"] == "es"


class TestTemplateFilter:
    def test_drawtext_filter_escapes_ffmpeg_expression_commas(self):
        filt = _drawtext_filter({"name": "Frame A"}, {"source": {"title": "Clip A"}})
        assert "drawtext=" in filt
        assert "max(18\\,min(" in filt
        assert "Clip A" in filt

    def test_drawtext_filter_uses_template_caption_text(self):
        filt = _drawtext_filter({"name": "Frame A", "caption_text": "Hook: {title}"}, {"source": {"title": "Clip A"}})
        assert "Hook\\: Clip A" in filt
        assert "text='Clip A'" not in filt

    def test_drawtext_filter_uses_source_caption_placeholder(self):
        filt = _drawtext_filter({"name": "Frame A", "caption_text": "{caption}"}, {"source": {"title": "Clip A", "caption": "Main caption"}})
        assert "Main caption" in filt
        assert "Clip A" not in filt

    def test_drawtext_filter_uses_font_size(self):
        filt = _drawtext_filter({"name": "Frame A", "font_size": 88}, {"source": {"title": "Clip A"}})
        assert "fontsize=88" in filt

class TestRenderTemplates:
    def test_create_and_list_template(self, client):
        resp = client.post(
            "/batch/templates",
            json={
                "name": "Lower third",
                "frame_image": "frames/lower.png",
                "text_box": {"x": 0.1, "y": 0.72, "width": 0.8, "height": 0.18},
                "horizontal_align": "center",
                "vertical_align": "middle",
                "font_family": "Inter",
                "text_color": "#ffffff",
                "stroke_color": "#000000",
                "stroke_width": 2,
                "intro_duration": 3,
                "intro_effect": "fade",
            },
        )
        assert resp.status_code == 200, resp.text
        created = resp.json()
        assert created["id"]
        assert created["name"] == "Lower third"

        listed = client.get("/batch/templates").json()
        assert [t["id"] for t in listed] == [created["id"]]

    def test_template_requires_name(self, client):
        resp = client.post("/batch/templates", json={"name": " "})
        assert resp.status_code == 422

class TestRenderBatchContract:
    def _template(self, client, name):
        return client.post("/batch/templates", json={"name": name}).json()

    def test_create_batch_expands_sources_by_templates_and_reuses_source_artifact(self, client):
        t1 = self._template(client, "Frame A")
        t2 = self._template(client, "Frame B")

        resp = client.post(
            "/batch/render-batches",
            json={
                "sources": [
                    {"kind": "url", "url": "https://example.com/a.mp4", "title": "Clip A"},
                    {"kind": "url", "url": "https://example.com/b.mp4", "title": "Clip B"},
                ],
                "template_ids": [t1["id"], t2["id"]],
                "settings": {"target_language": "vi", "preserve_bg": True},
                "output": {"local_root": "outputs/batches"},
            },
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "queued"
        assert len(body["items"]) == 4
        assert {item["template_id"] for item in body["items"]} == {t1["id"], t2["id"]}
        by_source = {}
        for item in body["items"]:
            by_source.setdefault(item["source_index"], set()).add(item["source_artifact_key"])
        assert len(by_source) == 2
        assert all(len(keys) == 1 for keys in by_source.values())

    def test_rerun_failed_item_sets_it_back_to_queued(self, client):
        t1 = self._template(client, "Frame A")
        batch = client.post(
            "/batch/render-batches",
            json={
                "sources": [{"kind": "url", "url": "https://example.com/a.mp4"}],
                "template_ids": [t1["id"]],
            },
        ).json()
        item_id = batch["items"][0]["id"]
        _render_items[item_id]["status"] = "failed"
        _render_items[item_id]["error"] = "boom"

        resp = client.post(f"/batch/render-items/{item_id}/rerun")
        assert resp.status_code == 200, resp.text
        item = resp.json()
        assert item["status"] == "queued"
        assert item["error"] is None

    def test_delete_batch_removes_items(self, client):
        t1 = self._template(client, "Frame A")
        batch = client.post(
            "/batch/render-batches",
            json={
                "sources": [{"kind": "url", "url": "https://example.com/a.mp4"}],
                "template_ids": [t1["id"]],
            },
        ).json()

        resp = client.delete(f"/batch/render-batches/{batch['id']}")
        assert resp.status_code == 200
        assert client.get(f"/batch/render-batches/{batch['id']}").status_code == 404
        assert _render_items == {}


    def test_process_render_batch_reuses_source_once_for_multiple_templates(self, client, monkeypatch):
        import api.routers.batch as batch_mod

        t1 = self._template(client, "Frame A")
        t2 = self._template(client, "Frame B")
        batch = client.post(
            "/batch/render-batches",
            json={
                "sources": [{"kind": "url", "url": "https://example.com/a.mp4", "title": "Clip A"}],
                "template_ids": [t1["id"], t2["id"]],
                "settings": {"target_language": "vi"},
            },
        ).json()

        prepared = []
        rendered = []

        async def fake_prepare(render_batch, source_items):
            prepared.append([item["id"] for item in source_items])
            return "dubbed-source.mp4"

        async def fake_render(source_output, item, template):
            rendered.append((source_output, item["id"], template["id"]))
            item["output_path"] = f"/tmp/{item['id']}.mp4"
            return item["output_path"]

        monkeypatch.setattr(batch_mod, "_prepare_render_source", fake_prepare)
        monkeypatch.setattr(batch_mod, "_render_template_output", fake_render)

        asyncio.run(_process_render_batch(batch["id"]))

        body = client.get(f"/batch/render-batches/{batch['id']}").json()
        assert body["status"] == "done"
        assert len(prepared) == 1
        assert len(prepared[0]) == 2
        assert len(rendered) == 2
        assert {item["status"] for item in body["items"]} == {"done"}
        assert {item["phase"] for item in body["items"]} == {"done"}
        assert {item["progress"] for item in body["items"]} == {100}
