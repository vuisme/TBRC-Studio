/**
 * Batch dubbing API — wraps the /batch/* backend endpoints.
 *
 * Used by BatchQueue and BatchAddDialog to enqueue, monitor, and
 * manage batch dub jobs.
 */
import { apiJson, apiPost, apiDelete, apiFetch, API } from './client';

export interface BatchJob {
  id: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  filename: string;
  langs: string[];
  voice_id?: string;
  preserve_bg: boolean;
  created_at: number;
  started_at?: number;
  finished_at?: number;
  error?: string;
  progress?: {
    stage: string;
    percent: number;
    current_lang?: string;
    current_segment?: number;
    total_segments?: number;
    segments_count?: number;
  };
  outputs?: Record<string, string>;
}

/** List batch jobs, optionally filtered by status. */
export async function listBatchJobs(status?: string, limit = 50): Promise<BatchJob[]> {
  const qs = new URLSearchParams();
  if (status) qs.set('status', status);
  qs.set('limit', String(limit));
  return apiJson<BatchJob[]>(`/batch/jobs?${qs.toString()}`);
}

/** Get a single batch job (used to resolve why a job left the active list). */
export async function getBatchJob(id: string): Promise<BatchJob> {
  return apiJson<BatchJob>(`/batch/jobs/${id}`);
}

/** Enqueue a video for batch dubbing. */
export async function enqueueBatchJob(
  file: File,
  langs: string[],
  voiceId?: string,
  preserveBg = true,
): Promise<{ job_id: string; status: string; queue_position: number }> {
  const form = new FormData();
  form.append('video', file);
  form.append('langs', langs.join(','));
  if (voiceId) form.append('voice_id', voiceId);
  form.append('preserve_bg', String(preserveBg));
  return apiPost('/batch/enqueue', form);
}

/** Cancel a batch job. */
export async function cancelBatchJob(id: string): Promise<unknown> {
  return apiPost(`/batch/jobs/${id}/cancel`, {});
}

/** Delete a batch job and its files. */
export async function deleteBatchJob(id: string): Promise<unknown> {
  const res = await apiDelete(`/batch/jobs/${id}`);
  return res.json();
}

export interface BatchTemplate {
  id: string;
  name: string;
  frame_image?: string;
  font_family?: string;
  text_box?: { x: number; y: number; width: number; height: number };
  horizontal_align?: string;
  vertical_align?: string;
  text_color?: string;
  stroke_color?: string;
  stroke_width?: number;
  intro_duration?: number;
  intro_effect?: string;
  created_at: number;
  updated_at: number;
}

export interface RenderBatchItem {
  id: string;
  batch_id: string;
  source_index: number;
  template_id: string;
  template_name: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  phase: string;
  progress: number;
  error?: string | null;
  output_path?: string | null;
  drive_link?: string | null;
  source_artifact_key: string;
}

export interface RenderBatch {
  id: string;
  status: string;
  settings: Record<string, unknown>;
  output: Record<string, unknown>;
  created_at: number;
  updated_at: number;
  finished_at?: number | null;
  error?: string | null;
  items: RenderBatchItem[];
}

export async function listBatchTemplates(): Promise<BatchTemplate[]> {
  return apiJson<BatchTemplate[]>('/batch/templates');
}

export async function createBatchTemplate(name: string): Promise<BatchTemplate> {
  return apiPost<BatchTemplate>('/batch/templates', { name });
}

export async function updateBatchTemplate(
  id: string,
  patch: Partial<Omit<BatchTemplate, 'id' | 'created_at' | 'updated_at'>>,
): Promise<BatchTemplate> {
  const res = await apiFetch(`/batch/templates/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  return res.json();
}
export async function createRenderBatch(input: {
  sources: Array<{ kind: 'url'; url: string; title?: string }>;
  template_ids: string[];
  settings?: Record<string, unknown>;
  output?: { local_root?: string; drive_enabled?: boolean };
}): Promise<RenderBatch> {
  return apiPost<RenderBatch>('/batch/render-batches', input);
}


export async function deleteRenderBatch(id: string): Promise<unknown> {
  const res = await apiDelete(`/batch/render-batches/${id}`);
  return res.json();
}
export async function listRenderBatches(status?: string, limit = 50): Promise<RenderBatch[]> {
  const qs = new URLSearchParams();
  if (status) qs.set('status', status);
  qs.set('limit', String(limit));
  return apiJson<RenderBatch[]>(`/batch/render-batches?${qs.toString()}`);
}

export async function rerunRenderItem(id: string): Promise<RenderBatchItem> {
  return apiPost<RenderBatchItem>(`/batch/render-items/${id}/rerun`, {});
}
