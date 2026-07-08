import React, { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Upload, Film, Globe, X, Plus, Link, LayoutTemplate } from 'lucide-react';
import { Button, Dialog, Select, Input, Textarea } from '../ui';
import MultiLangPicker from './MultiLangPicker';
import { PRESETS } from '../utils/constants';

/**
 * BatchAddDialog — multi-file drop zone + shared settings for batch dubbing.
 *
 * Users drop N video files, pick languages + voice, then click "Add to Queue".
 * Each file is POSTed as a separate job to the batch endpoint.
 */
export default function BatchAddDialog({
  open,
  onClose,
  profiles = [],
  templates = [],
  onCreateTemplate,
  onUpdateTemplate,
  onEnqueue, // async (files, settings) => void
}) {
  const { t } = useTranslation();
  const [files, setFiles] = useState([]);
  const [urlsText, setUrlsText] = useState('');
  const [selectedTemplateIds, setSelectedTemplateIds] = useState([]);
  const [newTemplateName, setNewTemplateName] = useState('');
  const [templateDraft, setTemplateDraft] = useState(null);
  const [savingTemplate, setSavingTemplate] = useState(false);
  const [langs, setLangs] = useState([{ lang: 'Spanish', code: 'es' }]);
  const [voiceId, setVoiceId] = useState('');
  const [title, setTitle] = useState('');
  const [caption, setCaption] = useState('');
  const [ttsScriptMode, setTtsScriptMode] = useState('caption');
  const [preserveBg, setPreserveBg] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef(null);

  const selectedTemplate = useMemo(
    () => templates.find((tpl) => tpl.id === selectedTemplateIds[0]) || null,
    [templates, selectedTemplateIds],
  );

  useEffect(() => {
    if (!selectedTemplate) {
      setTemplateDraft(null);
      return;
    }
    setTemplateDraft({
      name: selectedTemplate.name || '',
      horizontal_align: selectedTemplate.horizontal_align || 'center',
      vertical_align: selectedTemplate.vertical_align || 'middle',
      text_color: selectedTemplate.text_color || '#ffffff',
      stroke_color: selectedTemplate.stroke_color || '#000000',
      stroke_width: selectedTemplate.stroke_width ?? 2,
      intro_duration: selectedTemplate.intro_duration ?? 3,
      intro_effect: selectedTemplate.intro_effect || 'fade',
      text_box: {
        x: selectedTemplate.text_box?.x ?? 0.1,
        y: selectedTemplate.text_box?.y ?? 0.72,
        width: selectedTemplate.text_box?.width ?? 0.8,
        height: selectedTemplate.text_box?.height ?? 0.18,
      },
    });
  }, [selectedTemplate]);

  const updateDraft = (patch) => setTemplateDraft((prev) => ({ ...(prev || {}), ...patch }));
  const updateDraftBox = (key, value) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return;
    setTemplateDraft((prev) => ({
      ...(prev || {}),
      text_box: { ...(prev?.text_box || {}), [key]: numeric },
    }));
  };

  const saveTemplateDraft = async () => {
    if (!selectedTemplate || !templateDraft) return;
    setSavingTemplate(true);
    try {
      await onUpdateTemplate?.(selectedTemplate.id, templateDraft);
    } finally {
      setSavingTemplate(false);
    }
  };


  const handleDrop = useCallback((e) => {
    e.preventDefault();
    const dropped = Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith('video/'));
    if (dropped.length) setFiles((prev) => [...prev, ...dropped]);
  }, []);

  const removeFile = (idx) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleSubmit = async () => {
    const urls = urlsText
      .split(/\r?\n/)
      .map((u) => u.trim())
      .filter(Boolean);
    if ((!files.length && !urls.length) || (!langs.length && !selectedTemplateIds.length)) return;
    setSubmitting(true);
    try {
      await onEnqueue?.(files, {
        langs,
        voiceId,
        preserveBg,
        urls,
        templateIds: selectedTemplateIds,
        title: title.trim(),
        caption: caption.trim(),
        ttsScriptMode,
      });
      setFiles([]);
      setUrlsText('');
      setTitle('');
      setCaption('');
      onClose?.();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="md"
      title={
        <span className="inline-flex items-center gap-[6px]">
          <Plus size={14} /> {t('batch.add_to_queue_title')}
        </span>
      }
      footer={
        <>
          <span className="flex-1 font-mono text-[0.68rem] text-[var(--chrome-fg-dim)]">
            {files.length > 0 || urlsText.trim()
              ? t('batch.estimate', {
                  videos: files.length + urlsText.split(/\r?\n/).filter((u) => u.trim()).length,
                  langs: Math.max(langs.length, selectedTemplateIds.length || 1),
                  jobs: files.length * langs.length + urlsText.split(/\r?\n/).filter((u) => u.trim()).length * Math.max(1, selectedTemplateIds.length),
                })
              : t('batch.select_files_langs')}
          </span>
          <Button variant="ghost" size="sm" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={handleSubmit}
            disabled={(!files.length && !urlsText.trim()) || (!langs.length && !selectedTemplateIds.length) || submitting}
            loading={submitting}
            leading={!submitting && <Plus size={10} />}
          >
            {t('batch.add_to_queue')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-[14px]">
        {/* Drop zone */}
        <div
          className={`flex cursor-pointer flex-col items-center justify-center gap-[6px] rounded-[10px] border-2 border-dashed px-4 py-7 text-[0.82rem] transition-all hover:border-[var(--chrome-accent)] hover:bg-white/[0.02] hover:text-[var(--chrome-fg)] ${
            dragOver
              ? 'border-[var(--chrome-accent)] bg-white/[0.02] text-[var(--chrome-fg)]'
              : 'border-transparent text-[var(--chrome-fg-muted)]'
          }`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            setDragOver(false);
            handleDrop(e);
          }}
          onClick={() => fileInputRef.current?.click()}
        >
          <Upload size={24} />
          <span>{t('batch.drop_hint_text')}</span>
          <span className="font-mono text-[0.65rem] text-[var(--chrome-fg-dim)]">
            {t('batch.drop_formats')}
          </span>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept="video/*"
          multiple
          className="hidden"
          onChange={(e) => {
            const added = Array.from(e.target.files);
            if (added.length) setFiles((prev) => [...prev, ...added]);
            e.target.value = '';
          }}
        />

        {/* File list */}
        {files.length > 0 && (
          <div className="flex flex-col gap-[4px]">
            <span className="mb-[4px] flex items-center gap-[4px] font-mono text-[0.62rem] font-semibold uppercase tracking-[0.04em] text-[var(--chrome-fg-dim)]">
              {t('batch.files_kicker', { count: files.length })}
            </span>
            {files.map((f, i) => (
              <div
                key={`${f.name}-${i}`}
                className="flex items-center gap-[6px] rounded-[6px] bg-[var(--chrome-hover-bg)] p-[4px_8px] text-[0.76rem]"
              >
                <Film size={10} />
                <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap text-[var(--chrome-fg)]">
                  {f.name}
                </span>
                <span className="shrink-0 font-mono text-[0.68rem] text-[var(--chrome-fg-dim)]">
                  {t('batch.file_size_mb', { size: (f.size / 1024 / 1024).toFixed(1) })}
                </span>
                <button
                  type="button"
                  className="cursor-pointer rounded-[4px] border-0 bg-transparent p-[2px] text-[var(--chrome-fg-dim)] hover:text-[var(--color-danger)]"
                  onClick={() => removeFile(i)}
                >
                  <X size={9} />
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="flex flex-col gap-[6px]">
          <span className="mb-[4px] flex items-center gap-[4px] font-mono text-[0.62rem] font-semibold uppercase tracking-[0.04em] text-[var(--chrome-fg-dim)]">
            <Link size={9} /> Batch URLs
          </span>
          <Textarea
            size="sm"
            rows={3}
            value={urlsText}
            onChange={(e) => setUrlsText(e.target.value)}
            placeholder={"https://example.com/video-1.mp4\nhttps://example.com/video-2.mp4"}
          />
        </div>

        <div className="grid gap-[8px] rounded-[6px] border border-[var(--chrome-border)] bg-[var(--chrome-hover-bg)] p-[10px]">
          <span className="font-mono text-[0.62rem] font-semibold uppercase tracking-[0.04em] text-[var(--chrome-fg-dim)]">
            Intro content
          </span>
          <Input
            size="sm"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Video title"
          />
          <Textarea
            size="sm"
            rows={3}
            value={caption}
            onChange={(e) => setCaption(e.target.value)}
            placeholder="Caption shown inside the template box"
          />
          <Select size="sm" value={ttsScriptMode} onChange={(e) => setTtsScriptMode(e.target.value)}>
            <option value="caption">TTS script: caption</option>
            <option value="title_caption">TTS script: title + caption</option>
          </Select>
        </div>

        <div className="flex flex-col gap-[6px]">
          <span className="mb-[4px] flex items-center gap-[4px] font-mono text-[0.62rem] font-semibold uppercase tracking-[0.04em] text-[var(--chrome-fg-dim)]">
            <LayoutTemplate size={9} /> Templates
          </span>
          <div className="flex flex-col gap-[5px] rounded-[6px] border border-[var(--chrome-border)] bg-[var(--chrome-hover-bg)] p-[8px]">
            {templates.length === 0 && (
              <span className="text-[0.72rem] text-[var(--chrome-fg-dim)]">No templates yet.</span>
            )}
            {templates.map((tpl) => (
              <label key={tpl.id} className="flex cursor-pointer items-center gap-2 text-[0.76rem] text-[var(--chrome-fg)]">
                <input
                  type="checkbox"
                  checked={selectedTemplateIds.includes(tpl.id)}
                  onChange={(e) =>
                    setSelectedTemplateIds((prev) =>
                      e.target.checked ? [...prev, tpl.id] : prev.filter((id) => id !== tpl.id),
                    )
                  }
                />
                <span className="min-w-0 flex-1 truncate">{tpl.name}</span>
              </label>
            ))}
            <div className="mt-[4px] flex gap-[6px]">
              <Input
                size="sm"
                value={newTemplateName}
                onChange={(e) => setNewTemplateName(e.target.value)}
                placeholder="New template name"
              />
              <Button
                variant="subtle"
                size="xs"
                onClick={async () => {
                  const name = newTemplateName.trim();
                  if (!name) return;
                  const created = await onCreateTemplate?.(name);
                  setNewTemplateName('');
                  if (created?.id) setSelectedTemplateIds((prev) => [...prev, created.id]);
                }}
                leading={<Plus size={9} />}
              >
                Add
              </Button>
            </div>
            {selectedTemplate && templateDraft && (
              <div className="mt-[8px] grid gap-[10px] border-t border-[var(--chrome-border)] pt-[10px] md:grid-cols-[1fr_180px]">
                <div className="grid gap-[8px]">
                  <div className="grid grid-cols-2 gap-[6px]">
                    <Input
                      size="sm"
                      value={templateDraft.name}
                      onChange={(e) => updateDraft({ name: e.target.value })}
                      placeholder="Template name"
                    />
                    <Input
                      size="sm"
                      type="number"
                      min="0"
                      max="16"
                      value={templateDraft.stroke_width}
                      onChange={(e) => updateDraft({ stroke_width: Number(e.target.value) })}
                      placeholder="Stroke"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-[6px]">
                    <Select
                      size="sm"
                      value={templateDraft.horizontal_align}
                      onChange={(e) => updateDraft({ horizontal_align: e.target.value })}
                    >
                      <option value="left">Left</option>
                      <option value="center">Center</option>
                      <option value="right">Right</option>
                    </Select>
                    <Select
                      size="sm"
                      value={templateDraft.vertical_align}
                      onChange={(e) => updateDraft({ vertical_align: e.target.value })}
                    >
                      <option value="top">Top</option>
                      <option value="middle">Middle</option>
                      <option value="bottom">Bottom</option>
                    </Select>
                  </div>
                  <div className="grid grid-cols-4 gap-[6px]">
                    {['x', 'y', 'width', 'height'].map((key) => (
                      <Input
                        key={key}
                        size="sm"
                        type="number"
                        min="0"
                        max="1"
                        step="0.01"
                        value={templateDraft.text_box[key]}
                        onChange={(e) => updateDraftBox(key, e.target.value)}
                        aria-label={`Text box ${key}`}
                      />
                    ))}
                  </div>
                  <div className="flex items-center gap-[8px]">
                    <input
                      type="color"
                      value={templateDraft.text_color}
                      onChange={(e) => updateDraft({ text_color: e.target.value })}
                      aria-label="Text color"
                      className="h-[28px] w-[38px] cursor-pointer rounded-[4px] border border-[var(--chrome-border)] bg-transparent p-0"
                    />
                    <input
                      type="color"
                      value={templateDraft.stroke_color}
                      onChange={(e) => updateDraft({ stroke_color: e.target.value })}
                      aria-label="Stroke color"
                      className="h-[28px] w-[38px] cursor-pointer rounded-[4px] border border-[var(--chrome-border)] bg-transparent p-0"
                    />
                    <Button
                      variant="subtle"
                      size="xs"
                      onClick={saveTemplateDraft}
                      loading={savingTemplate}
                      disabled={!templateDraft.name.trim()}
                    >
                      Save
                    </Button>
                  </div>
                </div>
                <div className="relative aspect-video overflow-hidden rounded-[6px] border border-[var(--chrome-border)] bg-[linear-gradient(135deg,#15171d,#242833)]">
                  <div className="absolute inset-x-[12%] top-[18%] h-[1px] bg-white/10" />
                  <div className="absolute inset-y-[18%] left-[18%] w-[1px] bg-white/10" />
                  <div
                    className="absolute flex px-[4px] py-[2px] text-[10px] font-semibold leading-tight"
                    style={{
                      left: `${templateDraft.text_box.x * 100}%`,
                      top: `${templateDraft.text_box.y * 100}%`,
                      width: `${templateDraft.text_box.width * 100}%`,
                      height: `${templateDraft.text_box.height * 100}%`,
                      alignItems:
                        templateDraft.vertical_align === 'bottom'
                          ? 'flex-end'
                          : templateDraft.vertical_align === 'top'
                            ? 'flex-start'
                            : 'center',
                      justifyContent:
                        templateDraft.horizontal_align === 'right'
                          ? 'flex-end'
                          : templateDraft.horizontal_align === 'left'
                            ? 'flex-start'
                            : 'center',
                      color: templateDraft.text_color,
                      WebkitTextStroke: `${templateDraft.stroke_width}px ${templateDraft.stroke_color}`,
                    }}
                  >
                    Clip title
                  </div>
                </div>
              </div>
            )}          </div>
        </div>
        {/* Settings */}
        <div className="flex flex-col gap-[12px]">
          <div className="flex flex-col gap-[6px]">
            <span className="mb-[4px] flex items-center gap-[4px] font-mono text-[0.62rem] font-semibold uppercase tracking-[0.04em] text-[var(--chrome-fg-dim)]">
              <Globe size={9} /> {t('batch.target_languages')}
            </span>
            <MultiLangPicker selected={langs} onChange={setLangs} />
          </div>

          <div className="flex flex-col gap-[6px]">
            <span className="mb-[4px] flex items-center gap-[4px] font-mono text-[0.62rem] font-semibold uppercase tracking-[0.04em] text-[var(--chrome-fg-dim)]">
              {t('batch.voice_kicker')}
            </span>
            <Select size="sm" value={voiceId} onChange={(e) => setVoiceId(e.target.value)}>
              <option value="">{t('batch.default_option')}</option>
              {profiles.filter((p) => !p.instruct).length > 0 && (
                <optgroup label={t('batch.clone_profiles')}>
                  {profiles
                    .filter((p) => !p.instruct)
                    .map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                </optgroup>
              )}
              {PRESETS.length > 0 && (
                <optgroup label={t('batch.presets')}>
                  {PRESETS.map((p) => (
                    <option key={p.id} value={`preset:${p.id}`}>
                      {p.name}
                    </option>
                  ))}
                </optgroup>
              )}
            </Select>
          </div>

          <label className="flex cursor-pointer items-center gap-2 text-[0.78rem] text-[var(--chrome-fg)]">
            <input
              type="checkbox"
              className="cursor-pointer"
              checked={preserveBg}
              onChange={(e) => setPreserveBg(e.target.checked)}
            />
            <span>{t('batch.preserve_bg')}</span>
          </label>
        </div>
      </div>
    </Dialog>
  );
}



