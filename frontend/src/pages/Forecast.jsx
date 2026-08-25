import { useEffect, useState } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { getChannels, postForecast } from '../api/client'
import { ErrorState, LoadingState } from '../components/AsyncState'
import { formatCompactNumber } from '../utils/format'

const emptyForm = { title: '', thumbnailUrl: '', scheduledUploadTime: '', channelId: '' }

export function Forecast() {
  const [form, setForm] = useState(emptyForm)
  const [forecast, setForecast] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [channels, setChannels] = useState([])

  useEffect(() => {
    getChannels()
      .then(setChannels)
      .catch(() => setChannels([])) // channel selector is optional - the form still works without it
  }, [])

  function updateField(field) {
    return (event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const result = await postForecast(form)
      setForecast(result)
    } catch (err) {
      setError(err)
      setForecast(null)
    } finally {
      setLoading(false)
    }
  }

  const curve = forecast?.curve

  return (
    <div>
      <div className="page-header">
        <h1>Forecast</h1>
        <p className="page-subtitle">
          Project a 7-day view trajectory for a planned upload.
        </p>
      </div>

      <form className="forecast-form" onSubmit={handleSubmit}>
        <label className="field">
          <span>Video title</span>
          <input
            type="text"
            required
            value={form.title}
            onChange={updateField('title')}
            placeholder="e.g. My next big upload"
          />
        </label>

        <label className="field">
          <span>Thumbnail URL</span>
          <input
            type="text"
            required
            value={form.thumbnailUrl}
            onChange={updateField('thumbnailUrl')}
            placeholder="https://example.com/thumbnail.jpg"
          />
        </label>

        <label className="field">
          <span>Scheduled upload time</span>
          <input
            type="datetime-local"
            required
            value={form.scheduledUploadTime}
            onChange={updateField('scheduledUploadTime')}
          />
        </label>

        {channels.length > 0 && (
          <label className="field">
            <span>Channel</span>
            <select value={form.channelId} onChange={updateField('channelId')}>
              <option value="">No specific channel</option>
              {channels.map((channel) => (
                <option key={channel.channel_id} value={channel.channel_id}>
                  {channel.channel_title}
                </option>
              ))}
            </select>
            <span className="field-hint">
              Selecting the creator's channel gives a more accurate forecast,
              since the model uses that channel's historical performance as
              its baseline.
            </span>
          </label>
        )}

        <button type="submit" className="btn-primary" disabled={loading}>
          {loading ? 'Forecasting…' : 'Generate forecast'}
        </button>
      </form>

      {loading && <LoadingState label="Generating forecast…" />}
      {!loading && error && <ErrorState error={error} onRetry={handleSubmit} />}

      {!loading && !error && curve && curve.length > 0 && (
        <>
          {!forecast.used_channel_context && (
            <div className="context-note">
              This forecast is based on dataset-wide averages rather than a
              specific channel's history, so it may be less precise.
            </div>
          )}

          <div className="forecast-stats">
            <div className="stat">
              <span className="stat-label">Estimated ceiling — where views level off</span>
              <span className="stat-value">{formatCompactNumber(forecast.v_inf)} views</span>
            </div>
            <div className="stat">
              <span className="stat-label">How fast it gets there</span>
              <span className="stat-value">{forecast.tau.toFixed(1)} hours</span>
            </div>
          </div>

          <div className="chart-panel">
            <ResponsiveContainer width="100%" height={360}>
              <LineChart data={curve} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis
                  dataKey="day"
                  type="number"
                  domain={[1, 7]}
                  ticks={[1, 2, 3, 4, 5, 6, 7]}
                  tick={{ fontSize: 12 }}
                  label={{ value: 'Day', position: 'insideBottom', offset: -4 }}
                />
                <YAxis
                  tick={{ fontSize: 12 }}
                  tickFormatter={formatCompactNumber}
                  width={56}
                />
                <Tooltip formatter={(value) => formatCompactNumber(value)} />
                <Line
                  type="monotone"
                  dataKey="views"
                  stroke="var(--accent)"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </div>
  )
}
