const BASE_URL = import.meta.env.VITE_API_BASE_URL

async function request(path, options) {
  let response
  try {
    response = await fetch(`${BASE_URL}${path}`, options)
  } catch {
    throw new Error('Could not reach the TrendCast API. Is the backend running?')
  }

  if (!response.ok) {
    let detail = ''
    try {
      const body = await response.json()
      detail = body?.detail ? `: ${body.detail}` : ''
    } catch {
      // response had no JSON body
    }
    throw new Error(`Request failed (${response.status})${detail}`)
  }

  return response.json()
}

export function getHealth() {
  return request('/health')
}

export function getChannels() {
  return request('/channels')
}

export function getChannelVideos(channelId) {
  return request(`/channels/${encodeURIComponent(channelId)}/videos`)
}

export function getVideoTimeseries(videoId) {
  return request(`/videos/${encodeURIComponent(videoId)}/timeseries`)
}

export function postForecast({ title, thumbnailUrl, scheduledUploadTime, channelId }) {
  return request('/forecast', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title,
      thumbnail_url: thumbnailUrl,
      scheduled_upload_time: scheduledUploadTime,
      channel_id: channelId || undefined,
    }),
  })
}
