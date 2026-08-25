import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Sidebar from "../components/Sidebar";
import Topbar from "../components/Topbar";
import * as api from "../lib/api";
import { ApiError } from "../lib/api";

const CATEGORIES = ["Financial Performance", "User Engagement", "Market Trends", "Tech Review", "Gadgets"];

export default function NewPrediction() {
  const navigate = useNavigate();
  const fileInputRef = useRef(null);

  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("");
  const [tags, setTags] = useState([]);
  const [tagInput, setTagInput] = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [targetTime, setTargetTime] = useState("");
  const [file, setFile] = useState(null);

  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(null); // null | "draft" | "complete"

  function addTag() {
    const value = tagInput.trim();
    if (value && !tags.includes(value)) setTags([...tags, value]);
    setTagInput("");
  }

  function handleTagKeyDown(e) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addTag();
    }
  }

  function removeTag(tag) {
    setTags(tags.filter((t) => t !== tag));
  }

  function handleFileChange(e) {
    setFile(e.target.files?.[0] || null);
  }

  async function handleSubmit(e, draft) {
    e.preventDefault();
    if (!title.trim()) {
      setError("Give the prediction a title first.");
      return;
    }

    setError(null);
    setSubmitting(draft ? "draft" : "complete");

    const form = new FormData();
    form.set("title", title.trim());
    if (category) form.set("category", category);
    form.set("tags", tags.join(","));
    if (targetDate) form.set("target_date", targetDate);
    if (targetTime) form.set("target_time", targetTime);
    form.set("save_as_draft", String(draft));
    if (file) {
      // A single dropzone covers both thumbnails and datasets, matching the
      // design — route it to the right form field by file type.
      form.set(file.type.startsWith("image/") ? "thumbnail" : "dataset", file);
    }

    try {
      const result = await api.createPrediction(form);
      navigate(draft ? "/dashboard" : `/prediction-result/${result.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the prediction. Please try again.");
      setSubmitting(null);
    }
  }

  return (
    <div className="font-body-md text-body-md antialiased min-h-screen overflow-x-hidden bg-background text-on-background">
      <Sidebar active="predictions" />
      <Topbar searchPlaceholder="Search insights..." />

      <main className="ml-64 pt-24 px-margin-desktop pb-24 max-w-container-max mx-auto">
        <div className="mb-12">
          <h2 className="font-headline-lg text-headline-lg text-on-background mb-2">
            Create New Prediction
          </h2>
          <p className="font-body-lg text-body-lg text-on-surface-variant">
            Define the parameters for your next predictive model run.
          </p>
        </div>

        {error && (
          <div className="mb-6 rounded-lg bg-error-container/60 text-on-error-container px-4 py-3 font-body-md text-body-md text-sm">
            {error}
          </div>
        )}

        <form className="grid grid-cols-1 lg:grid-cols-12 gap-gutter" onSubmit={(e) => handleSubmit(e, false)}>
          {/* Left column */}
          <div className="lg:col-span-7 flex flex-col gap-8">
            <div className="glass-panel p-8 rounded-xl shadow-[0_10px_30px_rgba(0,0,0,0.04)] hover:shadow-[0_20px_40px_rgba(0,0,0,0.08)] transition-shadow duration-300">
              <h3 className="font-headline-md text-headline-md text-on-background mb-6 border-b border-outline-variant/50 pb-4">
                Basic Information
              </h3>
              <div className="space-y-6">
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-2">
                    Prediction Title
                  </label>
                  <input
                    className="input-field font-body-md text-body-md text-on-surface"
                    placeholder="e.g., Q4 Revenue Forecast"
                    type="text"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                  />
                </div>
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-2">
                    Category
                  </label>
                  <div className="relative">
                    <select
                      className="input-field appearance-none font-body-md text-body-md text-on-surface cursor-pointer"
                      value={category}
                      onChange={(e) => setCategory(e.target.value)}
                    >
                      <option value="">Select a category</option>
                      {CATEGORIES.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                    <span className="material-symbols-outlined absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-on-surface-variant">
                      expand_more
                    </span>
                  </div>
                </div>
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-2">Tags</label>
                  <input
                    className="input-field font-body-md text-body-md text-on-surface mb-3"
                    placeholder="Add tags and press enter"
                    type="text"
                    value={tagInput}
                    onChange={(e) => setTagInput(e.target.value)}
                    onKeyDown={handleTagKeyDown}
                    onBlur={addTag}
                  />
                  {tags.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {tags.map((tag) => (
                        <button
                          key={tag}
                          type="button"
                          onClick={() => removeTag(tag)}
                          className="px-3 py-1 bg-tertiary-fixed text-on-tertiary-fixed-variant rounded-full font-label-sm text-label-sm flex items-center gap-1 cursor-pointer hover:bg-tertiary-fixed-dim transition-colors"
                        >
                          {tag} <span className="material-symbols-outlined text-[14px]">close</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="glass-panel p-8 rounded-xl shadow-[0_10px_30px_rgba(0,0,0,0.04)] hover:shadow-[0_20px_40px_rgba(0,0,0,0.08)] transition-shadow duration-300">
              <h3 className="font-headline-md text-headline-md text-on-background mb-6 border-b border-outline-variant/50 pb-4">
                Schedule
              </h3>
              <div className="grid grid-cols-2 gap-6">
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-2">
                    Target Date
                  </label>
                  <div className="relative">
                    <input
                      className="input-field font-body-md text-body-md text-on-surface pl-10"
                      type="date"
                      value={targetDate}
                      onChange={(e) => setTargetDate(e.target.value)}
                    />
                    <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none text-on-surface-variant">
                      calendar_today
                    </span>
                  </div>
                </div>
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-2">
                    Target Time (Optional)
                  </label>
                  <div className="relative">
                    <input
                      className="input-field font-body-md text-body-md text-on-surface pl-10"
                      type="time"
                      value={targetTime}
                      onChange={(e) => setTargetTime(e.target.value)}
                    />
                    <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none text-on-surface-variant">
                      schedule
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Right column */}
          <div className="lg:col-span-5 flex flex-col gap-8">
            <div className="glass-panel p-8 rounded-xl shadow-[0_10px_30px_rgba(0,0,0,0.04)] flex-grow">
              <h3 className="font-headline-md text-headline-md text-on-background mb-6">Thumbnail &amp; Dataset</h3>
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                accept=".csv,.json,.png,.jpg,.jpeg"
                onChange={handleFileChange}
              />
              <div
                onClick={() => fileInputRef.current?.click()}
                className="border-2 border-dashed border-primary-fixed-dim bg-surface-container-lowest/50 rounded-xl p-8 flex flex-col items-center justify-center text-center cursor-pointer hover:bg-surface-container-lowest transition-colors h-64 mb-6 relative overflow-hidden group"
              >
                <div className="absolute inset-0 bg-primary/5 opacity-0 group-hover:opacity-100 transition-opacity" />
                {file ? (
                  <>
                    <span className="material-symbols-outlined fill text-4xl text-primary mb-4">
                      check_circle
                    </span>
                    <p className="font-body-md text-body-md text-on-surface font-medium mb-1 break-all px-4">
                      {file.name}
                    </p>
                    <p className="font-label-sm text-label-sm text-on-surface-variant">
                      Click to choose a different file
                    </p>
                  </>
                ) : (
                  <>
                    <span className="material-symbols-outlined fill text-4xl text-primary mb-4">
                      cloud_upload
                    </span>
                    <p className="font-body-md text-body-md text-on-surface font-medium mb-1">
                      Click to browse from device
                    </p>
                    <p className="font-label-sm text-label-sm text-on-surface-variant">
                      or drag and drop (drag-and-drop coming soon)
                    </p>
                    <p className="font-label-sm text-label-sm text-outline mt-4">
                      Supports .csv, .json, .png, .jpg (Max 50MB)
                    </p>
                  </>
                )}
              </div>

              <div className="bg-surface-container rounded-xl p-6 mt-8">
                <div className="flex justify-between items-center mb-4">
                  <span className="font-label-md text-label-md text-on-surface">Initial Confidence Estimate</span>
                  <span className="material-symbols-outlined text-tertiary">info</span>
                </div>
                <div className="w-full bg-surface-container-highest rounded-full h-2 mb-2 overflow-hidden">
                  <div
                    className="bg-gradient-to-r from-primary to-secondary h-2 rounded-full transition-all duration-500"
                    style={{ width: file ? "65%" : "15%" }}
                  />
                </div>
                <p className="font-label-sm text-label-sm text-on-surface-variant text-right">
                  {file ? "Ready — confidence finalizes on submit" : "Pending file upload..."}
                </p>
              </div>
            </div>

            <div className="flex gap-4 justify-end">
              <button
                className="px-6 py-3 bg-surface-container text-on-surface rounded-xl font-label-md text-label-md hover:bg-surface-container-highest transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
                type="button"
                disabled={submitting !== null}
                onClick={(e) => handleSubmit(e, true)}
              >
                {submitting === "draft" ? "Saving…" : "Save as Draft"}
              </button>
              <button
                className="px-6 py-3 bg-gradient-to-r from-primary to-secondary text-on-primary rounded-xl font-label-md text-label-md shadow-lg shadow-primary/20 hover:scale-[1.02] transition-transform active:scale-95 flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed disabled:hover:scale-100"
                type="submit"
                disabled={submitting !== null}
              >
                <span className="material-symbols-outlined text-[20px]">magic_button</span>
                {submitting === "complete" ? "Predicting…" : "Initialize Prediction"}
              </button>
            </div>
          </div>
        </form>
      </main>
    </div>
  );
}
