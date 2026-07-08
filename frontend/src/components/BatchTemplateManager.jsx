import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Image, LayoutTemplate, Move, Plus, Save, Type, Upload } from 'lucide-react';
import { Button, Input, Panel, Select } from '../ui';

const DEFAULT_TEMPLATE = {
  name: '',
  frame_image: '',
  font_family: '',
  caption_text: '{caption}',
  font_size: 64,
  horizontal_align: 'center',
  vertical_align: 'middle',
  text_color: '#ffffff',
  stroke_color: '#000000',
  stroke_width: 2,
  intro_duration: 3,
  intro_effect: 'fade',
  text_box: { x: 0.1, y: 0.72, width: 0.8, height: 0.18 },
};

const MIN_BOX = 0.04;
const PREVIEW_TITLE = 'Clip title';
const PREVIEW_CAPTION = 'Caption preview text';

function clamp(value, min = 0, max = 1) {
  return Math.min(max, Math.max(min, value));
}

function toDraft(template) {
  if (!template) return DEFAULT_TEMPLATE;
  return {
    ...DEFAULT_TEMPLATE,
    ...template,
    frame_image: template.frame_image || '',
    font_family: template.font_family || '',
    caption_text: template.caption_text || '{caption}',
    font_size: template.font_size || 64,
    text_box: {
      ...DEFAULT_TEMPLATE.text_box,
      ...(template.text_box || {}),
    },
  };
}

function numberValue(value, fallback = 0) {
  const next = Number(value);
  return Number.isFinite(next) ? next : fallback;
}

function previewCaption(text) {
  return String(text || '{caption}')
    .replaceAll('{title}', PREVIEW_TITLE)
    .replaceAll('{caption}', PREVIEW_CAPTION)
    .replaceAll('{template}', 'Template');
}

function boxToStyle(box) {
  return {
    left: `${box.x * 100}%`,
    top: `${box.y * 100}%`,
    width: `${box.width * 100}%`,
    height: `${box.height * 100}%`,
  };
}

function alignmentClasses(horizontal, vertical) {
  const justify = horizontal === 'right' ? 'justify-end text-right' : horizontal === 'left' ? 'justify-start text-left' : 'justify-center text-center';
  const align = vertical === 'bottom' ? 'items-end' : vertical === 'top' ? 'items-start' : 'items-center';
  return `${justify} ${align}`;
}

export default function BatchTemplateManager({ templates = [], onCreateTemplate, onUpdateTemplate }) {
  const previewRef = useRef(null);
  const dragRef = useRef(null);
  const [selectedId, setSelectedId] = useState('');
  const [newName, setNewName] = useState('');
  const [draft, setDraft] = useState(DEFAULT_TEMPLATE);
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState(false);
  const [framePreviewUrl, setFramePreviewUrl] = useState('');
  const [dragOver, setDragOver] = useState(false);

  const selected = useMemo(
    () => templates.find((template) => template.id === selectedId) || templates[0] || null,
    [templates, selectedId],
  );

  useEffect(() => {
    if (selected && selected.id !== selectedId) setSelectedId(selected.id);
    setDraft(toDraft(selected));
  }, [selected, selectedId]);

  useEffect(() => () => {
    if (framePreviewUrl) URL.revokeObjectURL(framePreviewUrl);
  }, [framePreviewUrl]);

  const updateDraft = (patch) => setDraft((prev) => ({ ...prev, ...patch }));
  const updateBox = useCallback((patch) => {
    setDraft((prev) => {
      const next = { ...prev.text_box, ...patch };
      next.width = clamp(numberValue(next.width, prev.text_box.width), MIN_BOX, 1);
      next.height = clamp(numberValue(next.height, prev.text_box.height), MIN_BOX, 1);
      next.x = clamp(numberValue(next.x, prev.text_box.x), 0, 1 - next.width);
      next.y = clamp(numberValue(next.y, prev.text_box.y), 0, 1 - next.height);
      return { ...prev, text_box: next };
    });
  }, []);

  const createTemplate = async () => {
    const name = newName.trim();
    if (!name) return;
    setCreating(true);
    try {
      const created = await onCreateTemplate?.(name);
      if (created?.id) setSelectedId(created.id);
      setNewName('');
    } finally {
      setCreating(false);
    }
  };

  const saveTemplate = async () => {
    if (!selected?.id || !draft.name.trim()) return;
    setSaving(true);
    try {
      await onUpdateTemplate?.(selected.id, {
        name: draft.name.trim(),
        frame_image: draft.frame_image.trim(),
        font_family: draft.font_family.trim(),
        font_size: numberValue(draft.font_size, 64),
        caption_text: draft.caption_text.trim() || '{caption}',
        horizontal_align: draft.horizontal_align,
        vertical_align: draft.vertical_align,
        text_color: draft.text_color,
        stroke_color: draft.stroke_color,
        stroke_width: numberValue(draft.stroke_width, 2),
        intro_duration: numberValue(draft.intro_duration, 0),
        intro_effect: draft.intro_effect,
        text_box: draft.text_box,
      });
    } finally {
      setSaving(false);
    }
  };

  const clientToNorm = useCallback((clientX, clientY) => {
    const rect = previewRef.current?.getBoundingClientRect();
    if (!rect) return null;
    return {
      x: clamp((clientX - rect.left) / rect.width),
      y: clamp((clientY - rect.top) / rect.height),
    };
  }, []);

  const startBoxGesture = useCallback((event, mode) => {
    event.preventDefault();
    event.stopPropagation();
    const point = clientToNorm(event.clientX, event.clientY);
    if (!point) return;
    dragRef.current = { mode, start: point, box: { ...draft.text_box } };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }, [clientToNorm, draft.text_box]);

  const handlePointerMove = useCallback((event) => {
    const drag = dragRef.current;
    if (!drag) return;
    const point = clientToNorm(event.clientX, event.clientY);
    if (!point) return;
    const dx = point.x - drag.start.x;
    const dy = point.y - drag.start.y;
    const box = drag.box;

    if (drag.mode === 'move') {
      updateBox({ x: box.x + dx, y: box.y + dy });
      return;
    }


    if (drag.mode === 'draw') {
      const left = Math.min(drag.start.x, point.x);
      const top = Math.min(drag.start.y, point.y);
      const right = Math.max(drag.start.x, point.x);
      const bottom = Math.max(drag.start.y, point.y);
      updateBox({ x: left, y: top, width: right - left, height: bottom - top });
      return;
    }
    let next = { ...box };
    if (drag.mode.includes('e')) next.width = box.width + dx;
    if (drag.mode.includes('s')) next.height = box.height + dy;
    if (drag.mode.includes('w')) {
      next.x = box.x + dx;
      next.width = box.width - dx;
    }
    if (drag.mode.includes('n')) {
      next.y = box.y + dy;
      next.height = box.height - dy;
    }
    updateBox(next);
  }, [clientToNorm, updateBox]);

  const stopBoxGesture = useCallback(() => {
    dragRef.current = null;
  }, []);

  useEffect(() => {
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', stopBoxGesture);
    window.addEventListener('pointercancel', stopBoxGesture);
    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', stopBoxGesture);
      window.removeEventListener('pointercancel', stopBoxGesture);
    };
  }, [handlePointerMove, stopBoxGesture]);

  const handleFrameDrop = useCallback((event) => {
    event.preventDefault();
    setDragOver(false);
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    const path = file.path || file.name;
    updateDraft({ frame_image: path });
    if (file.type?.startsWith('image/')) {
      if (framePreviewUrl) URL.revokeObjectURL(framePreviewUrl);
      setFramePreviewUrl(URL.createObjectURL(file));
    }
  }, [framePreviewUrl]);

  const captionStyle = {
    color: draft.text_color,
    fontFamily: draft.font_family || undefined,
    fontSize: `${Math.max(8, Math.min(240, numberValue(draft.font_size, 64))) * 0.42}px`,
    WebkitTextStroke: `${numberValue(draft.stroke_width, 2)}px ${draft.stroke_color}`,
    textShadow: numberValue(draft.stroke_width, 2) > 0 ? `0 1px 2px ${draft.stroke_color}` : undefined,
  };

  return (
    <div className="grid min-h-0 gap-[var(--space-4)] lg:grid-cols-[260px_minmax(0,1fr)]">
      <Panel variant="flat" padding="md" className="flex min-h-0 flex-col gap-[var(--space-3)]">
        <div className="inline-flex items-center gap-[var(--space-2)] text-[var(--text-sm)] font-semibold text-fg">
          <LayoutTemplate size={14} /> Frame Templates
        </div>
        <div className="flex gap-[6px]">
          <Input size="sm" value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="Template name" />
          <Button variant="subtle" size="sm" onClick={createTemplate} loading={creating} disabled={!newName.trim()} leading={!creating && <Plus size={11} />}>
            Add
          </Button>
        </div>
        <div className="flex min-h-0 flex-col gap-[6px] overflow-y-auto">
          {templates.length === 0 && (
            <div className="rounded-[6px] border border-[var(--chrome-border)] bg-bg-elev-1 p-[10px] text-[var(--text-xs)] text-fg-muted">
              Create a template, then place the caption box directly on the preview.
            </div>
          )}
          {templates.map((template) => (
            <button
              key={template.id}
              type="button"
              onClick={() => setSelectedId(template.id)}
              className={`flex cursor-pointer items-center gap-[8px] rounded-[6px] border px-[10px] py-[8px] text-left text-[var(--text-sm)] transition-colors ${
                selected?.id === template.id
                  ? 'border-[var(--chrome-accent-border)] bg-[var(--chrome-accent-bg)] text-fg'
                  : 'border-[var(--chrome-border)] bg-bg-elev-1 text-fg-muted hover:text-fg'
              }`}
            >
              <Image size={13} />
              <span className="min-w-0 flex-1 truncate">{template.name}</span>
            </button>
          ))}
        </div>
      </Panel>

      <Panel variant="flat" padding="md" className="grid min-h-0 gap-[var(--space-4)]">
        {!selected ? (
          <div className="text-[var(--text-sm)] text-fg-muted">No frame template selected.</div>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-[var(--space-3)]">
              <div className="inline-flex items-center gap-[var(--space-2)] text-[var(--text-sm)] font-semibold text-fg">
                <Type size={14} /> Template editor
              </div>
              <span className="flex-1" />
              <Button variant="primary" size="sm" onClick={saveTemplate} loading={saving} disabled={!draft.name.trim()} leading={!saving && <Save size={11} />}>
                Save template
              </Button>
            </div>

            <div className="grid min-h-0 gap-[var(--space-4)] xl:grid-cols-[minmax(520px,1fr)_340px]">
              <div className="grid content-start gap-[var(--space-3)]">
                <div className="grid gap-[8px] md:grid-cols-2">
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Template name
                    <Input size="sm" value={draft.name} onChange={(e) => updateDraft({ name: e.target.value })} />
                  </label>
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Frame image path
                    <Input size="sm" value={draft.frame_image} onChange={(e) => updateDraft({ frame_image: e.target.value })} placeholder="Drop image on preview or paste path" />
                  </label>
                </div>

                <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                  Caption text
                  <textarea
                    value={draft.caption_text}
                    onChange={(e) => updateDraft({ caption_text: e.target.value })}
                    placeholder="{caption}"
                    rows={3}
                    className="min-h-[72px] resize-y rounded-[6px] border border-[var(--chrome-border)] bg-bg-elev-1 px-[10px] py-[8px] text-[var(--text-sm)] text-fg outline-none focus:border-[var(--chrome-accent-border)]"
                  />
                </label>

                <div
                  ref={previewRef}
                  role="application"
                  aria-label="Frame template preview"
                  onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={handleFrameDrop}
                  onPointerDown={(event) => startBoxGesture(event, 'draw')}
                  className={`relative mx-auto aspect-[9/16] max-h-[72vh] w-full max-w-[420px] overflow-hidden rounded-[8px] border bg-[#111820] ${dragOver ? 'border-[var(--chrome-accent)]' : 'border-[var(--chrome-border)]'}`}
                >
                  <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(135deg,#121923,#263241_48%,#171b22)]" />
                  <div className="pointer-events-none absolute inset-0 opacity-40 [background-image:linear-gradient(rgba(255,255,255,0.05)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.05)_1px,transparent_1px)] [background-size:48px_48px]" />
                  {(framePreviewUrl || draft.frame_image) && (
                    framePreviewUrl ? (
                      <img src={framePreviewUrl} alt="" className="absolute inset-0 h-full w-full object-fill" />
                    ) : (
                      <div className="absolute left-[10px] top-[10px] inline-flex items-center gap-[6px] rounded-[5px] bg-black/45 px-[8px] py-[4px] font-mono text-[10px] text-white/80">
                        <Upload size={12} /> {draft.frame_image}
                      </div>
                    )
                  )}

                  <div
                    className="absolute z-[2] cursor-move select-none border border-dashed border-[#fabd2f] bg-black/15 [box-shadow:0_0_0_1px_rgba(0,0,0,0.35),0_0_24px_rgba(250,189,47,0.18)]"
                    style={boxToStyle(draft.text_box)}
                    onPointerDown={(event) => startBoxGesture(event, 'move')}
                  >
                    <div className={`flex h-full w-full px-[10px] py-[8px] font-bold leading-tight ${alignmentClasses(draft.horizontal_align, draft.vertical_align)}`} style={captionStyle}>
                      <span className="max-w-full whitespace-pre-wrap break-words">{previewCaption(draft.caption_text)}</span>
                    </div>
                    {['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'].map((handle) => (
                      <button
                        key={handle}
                        type="button"
                        aria-label={`Resize caption ${handle}`}
                        onPointerDown={(event) => startBoxGesture(event, handle)}
                        className={`absolute h-[10px] w-[10px] rounded-[2px] border border-black bg-[#fabd2f] ${
                          handle.includes('n') ? 'top-[-5px]' : handle.includes('s') ? 'bottom-[-5px]' : 'top-1/2 -translate-y-1/2'
                        } ${handle.includes('w') ? 'left-[-5px]' : handle.includes('e') ? 'right-[-5px]' : 'left-1/2 -translate-x-1/2'}`}
                      />
                    ))}
                  </div>

                  <div className="pointer-events-none absolute bottom-[10px] left-[10px] inline-flex items-center gap-[6px] rounded-[5px] bg-black/45 px-[8px] py-[4px] font-mono text-[10px] text-white/75">
                    <Move size={12} /> draw or drag caption box · drop frame image
                  </div>
                </div>
              </div>

              <div className="grid content-start gap-[var(--space-3)]">
                <div className="grid gap-[8px] sm:grid-cols-2 xl:grid-cols-1">
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Font family
                    <Input size="sm" value={draft.font_family} onChange={(e) => updateDraft({ font_family: e.target.value })} placeholder="Inter, Arial" />
                  </label>
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Horizontal align
                    <Select size="sm" value={draft.horizontal_align} onChange={(e) => updateDraft({ horizontal_align: e.target.value })}>
                      <option value="left">Left</option>
                      <option value="center">Center</option>
                      <option value="right">Right</option>
                    </Select>
                  </label>
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Vertical align
                    <Select size="sm" value={draft.vertical_align} onChange={(e) => updateDraft({ vertical_align: e.target.value })}>
                      <option value="top">Top</option>
                      <option value="middle">Middle</option>
                      <option value="bottom">Bottom</option>
                    </Select>
                  </label>
                </div>

                <div className="grid grid-cols-2 gap-[8px]">
                  {['x', 'y', 'width', 'height'].map((key) => (
                    <label key={key} className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                      Box {key.toUpperCase()}
                      <Input size="sm" type="number" min="0" max="1" step="0.01" value={draft.text_box[key]} onChange={(e) => updateBox({ [key]: e.target.value })} />
                    </label>
                  ))}
                </div>

                <div className="grid grid-cols-2 gap-[8px]">
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Text color
                    <input type="color" value={draft.text_color} onChange={(e) => updateDraft({ text_color: e.target.value })} className="h-[32px] w-full rounded-[4px] border border-[var(--chrome-border)] bg-transparent" />
                  </label>
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Stroke color
                    <input type="color" value={draft.stroke_color} onChange={(e) => updateDraft({ stroke_color: e.target.value })} className="h-[32px] w-full rounded-[4px] border border-[var(--chrome-border)] bg-transparent" />
                  </label>
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Font size
                    <Input size="sm" type="number" min="8" max="240" value={draft.font_size} onChange={(e) => updateDraft({ font_size: numberValue(e.target.value, 64) })} />
                  </label>
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Stroke width
                    <Input size="sm" type="number" min="0" max="16" value={draft.stroke_width} onChange={(e) => updateDraft({ stroke_width: numberValue(e.target.value, 2) })} />
                  </label>
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Intro seconds
                    <Input size="sm" type="number" min="0" max="60" step="0.1" value={draft.intro_duration} onChange={(e) => updateDraft({ intro_duration: numberValue(e.target.value, 0) })} />
                  </label>
                </div>

                <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                  Intro effect
                  <Select size="sm" value={draft.intro_effect} onChange={(e) => updateDraft({ intro_effect: e.target.value })}>
                    <option value="fade">Fade</option>
                    <option value="cut">Cut</option>
                    <option value="slide-up">Slide up</option>
                  </Select>
                </label>
              </div>
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}
