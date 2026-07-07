import React, { useEffect, useMemo, useState } from 'react';
import { Image, LayoutTemplate, Plus, Save, Type } from 'lucide-react';
import { Button, Input, Panel, Select } from '../ui';

const DEFAULT_TEMPLATE = {
  name: '',
  frame_image: '',
  font_family: '',
  horizontal_align: 'center',
  vertical_align: 'middle',
  text_color: '#ffffff',
  stroke_color: '#000000',
  stroke_width: 2,
  intro_duration: 3,
  intro_effect: 'fade',
  text_box: { x: 0.1, y: 0.72, width: 0.8, height: 0.18 },
};

function toDraft(template) {
  if (!template) return DEFAULT_TEMPLATE;
  return {
    ...DEFAULT_TEMPLATE,
    ...template,
    frame_image: template.frame_image || '',
    font_family: template.font_family || '',
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

export default function BatchTemplateManager({ templates = [], onCreateTemplate, onUpdateTemplate }) {
  const [selectedId, setSelectedId] = useState('');
  const [newName, setNewName] = useState('');
  const [draft, setDraft] = useState(DEFAULT_TEMPLATE);
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState(false);

  const selected = useMemo(
    () => templates.find((template) => template.id === selectedId) || templates[0] || null,
    [templates, selectedId],
  );

  useEffect(() => {
    if (selected && selected.id !== selectedId) setSelectedId(selected.id);
    setDraft(toDraft(selected));
  }, [selected, selectedId]);

  const updateDraft = (patch) => setDraft((prev) => ({ ...prev, ...patch }));
  const updateBox = (key, value) => {
    setDraft((prev) => ({
      ...prev,
      text_box: { ...prev.text_box, [key]: numberValue(value, prev.text_box[key]) },
    }));
  };

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

  return (
    <div className="grid gap-[var(--space-4)] lg:grid-cols-[280px_minmax(0,1fr)]">
      <Panel variant="flat" padding="md" className="flex flex-col gap-[var(--space-3)]">
        <div className="inline-flex items-center gap-[var(--space-2)] text-[var(--text-sm)] font-semibold text-fg">
          <LayoutTemplate size={14} /> Frame Templates
        </div>
        <div className="flex gap-[6px]">
          <Input
            size="sm"
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            placeholder="Template name"
          />
          <Button
            variant="subtle"
            size="sm"
            onClick={createTemplate}
            loading={creating}
            disabled={!newName.trim()}
            leading={!creating && <Plus size={11} />}
          >
            Add
          </Button>
        </div>
        <div className="flex flex-col gap-[6px]">
          {templates.length === 0 && (
            <div className="rounded-[6px] border border-[var(--chrome-border)] bg-bg-elev-1 p-[10px] text-[var(--text-xs)] text-fg-muted">
              Create a frame template to render URL batches with a reusable overlay and intro.
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

      <Panel variant="flat" padding="md" className="grid gap-[var(--space-4)]">
        {!selected ? (
          <div className="text-[var(--text-sm)] text-fg-muted">No frame template selected.</div>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-[var(--space-3)]">
              <div className="inline-flex items-center gap-[var(--space-2)] text-[var(--text-sm)] font-semibold text-fg">
                <Type size={14} /> Template editor
              </div>
              <span className="flex-1" />
              <Button
                variant="primary"
                size="sm"
                onClick={saveTemplate}
                loading={saving}
                disabled={!draft.name.trim()}
                leading={!saving && <Save size={11} />}
              >
                Save template
              </Button>
            </div>

            <div className="grid gap-[var(--space-3)] xl:grid-cols-[minmax(0,1fr)_320px]">
              <div className="grid gap-[var(--space-3)]">
                <div className="grid gap-[8px] md:grid-cols-2">
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Template name
                    <Input size="sm" value={draft.name} onChange={(e) => updateDraft({ name: e.target.value })} />
                  </label>
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Frame image path
                    <Input
                      size="sm"
                      value={draft.frame_image}
                      onChange={(e) => updateDraft({ frame_image: e.target.value })}
                      placeholder="C:\\frames\\hook.png"
                    />
                  </label>
                </div>

                <div className="grid gap-[8px] md:grid-cols-3">
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Font family
                    <Input
                      size="sm"
                      value={draft.font_family}
                      onChange={(e) => updateDraft({ font_family: e.target.value })}
                      placeholder="Inter, Arial"
                    />
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

                <div className="grid gap-[8px] md:grid-cols-4">
                  {['x', 'y', 'width', 'height'].map((key) => (
                    <label key={key} className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                      Text box {key.toUpperCase()}
                      <Input
                        size="sm"
                        type="number"
                        min="0"
                        max="1"
                        step="0.01"
                        value={draft.text_box[key]}
                        onChange={(e) => updateBox(key, e.target.value)}
                      />
                    </label>
                  ))}
                </div>

                <div className="grid gap-[8px] md:grid-cols-5">
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Text color
                    <input type="color" value={draft.text_color} onChange={(e) => updateDraft({ text_color: e.target.value })} className="h-[32px] w-full rounded-[4px] border border-[var(--chrome-border)] bg-transparent" />
                  </label>
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Stroke color
                    <input type="color" value={draft.stroke_color} onChange={(e) => updateDraft({ stroke_color: e.target.value })} className="h-[32px] w-full rounded-[4px] border border-[var(--chrome-border)] bg-transparent" />
                  </label>
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Stroke width
                    <Input size="sm" type="number" min="0" max="16" value={draft.stroke_width} onChange={(e) => updateDraft({ stroke_width: numberValue(e.target.value, 2) })} />
                  </label>
                  <label className="grid gap-[5px] text-[var(--text-xs)] text-fg-muted">
                    Intro seconds
                    <Input size="sm" type="number" min="0" max="60" step="0.1" value={draft.intro_duration} onChange={(e) => updateDraft({ intro_duration: numberValue(e.target.value, 0) })} />
                  </label>
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

              <div className="grid gap-[8px] content-start">
                <div className="text-[var(--text-xs)] font-semibold uppercase tracking-[0.04em] text-fg-muted">
                  Preview
                </div>
                <div className="relative aspect-video overflow-hidden rounded-[8px] border border-[var(--chrome-border)] bg-[linear-gradient(135deg,#131820,#27303c)]">
                  {draft.frame_image ? (
                    <div className="absolute left-[8px] top-[8px] rounded-[4px] bg-black/40 px-[6px] py-[3px] font-mono text-[10px] text-white/80">
                      Frame image set
                    </div>
                  ) : null}
                  <div
                    className="absolute flex px-[6px] py-[3px] text-[13px] font-bold leading-tight"
                    style={{
                      left: `${draft.text_box.x * 100}%`,
                      top: `${draft.text_box.y * 100}%`,
                      width: `${draft.text_box.width * 100}%`,
                      height: `${draft.text_box.height * 100}%`,
                      alignItems:
                        draft.vertical_align === 'bottom'
                          ? 'flex-end'
                          : draft.vertical_align === 'top'
                            ? 'flex-start'
                            : 'center',
                      justifyContent:
                        draft.horizontal_align === 'right'
                          ? 'flex-end'
                          : draft.horizontal_align === 'left'
                            ? 'flex-start'
                            : 'center',
                      color: draft.text_color,
                      fontFamily: draft.font_family || undefined,
                      WebkitTextStroke: `${draft.stroke_width}px ${draft.stroke_color}`,
                    }}
                  >
                    Clip title preview
                  </div>
                </div>
                <div className="rounded-[6px] border border-[var(--chrome-border)] bg-bg-elev-1 p-[8px] text-[var(--text-xs)] text-fg-muted">
                  Intro: {draft.intro_duration || 0}s - {draft.intro_effect || 'fade'}
                </div>
              </div>
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}

