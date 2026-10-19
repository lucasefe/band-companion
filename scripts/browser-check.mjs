// Drives headless Chrome through the DevTools protocol to exercise the app at
// phone / tablet sizes: play a track, download for offline, play offline.
// Usage: node scripts/browser-check.mjs [base-url] [screenshot-dir]
import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = process.argv[2] ?? 'http://127.0.0.1:8787'
const OUT = process.argv[3] ?? mkdtempSync(join(tmpdir(), 'band-check-'))
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const PORT = 9333

mkdirSync(OUT, { recursive: true })

const chrome = spawn(
  CHROME,
  [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${mkdtempSync(join(tmpdir(), 'band-chrome-'))}`,
    '--autoplay-policy=no-user-gesture-required',
    '--no-first-run',
    'about:blank',
  ],
  { stdio: 'ignore' },
)
process.on('exit', () => chrome.kill())

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function targetUrl() {
  for (let i = 0; i < 50; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`)
      const list = await res.json()
      const page = list.find((t) => t.type === 'page')
      if (page) return page.webSocketDebuggerUrl
    } catch {}
    await sleep(200)
  }
  throw new Error('Chrome did not start')
}

const ws = new WebSocket(await targetUrl())
await new Promise((r) => (ws.onopen = r))
let seq = 0
const pending = new Map()
const events = []
ws.onmessage = (m) => {
  const msg = JSON.parse(m.data)
  if (msg.id) {
    const { resolve, reject } = pending.get(msg.id)
    pending.delete(msg.id)
    msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result)
  } else events.push(msg)
}
const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const id = ++seq
    pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
const evaluate = async (expression) => {
  const { result, exceptionDetails } = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  if (exceptionDetails) throw new Error(exceptionDetails.exception?.description ?? 'evaluate failed')
  return result.value
}
const shot = async (name) => {
  const { data } = await send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(join(OUT, `${name}.png`), Buffer.from(data, 'base64'))
}
const device = (width, height) =>
  send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 2, mobile: true })

await send('Page.enable')
await send('Runtime.enable')
await send('Network.enable')
await send('Log.enable')

const results = []
const check = (name, ok, detail = '') => {
  results.push({ name, ok, detail })
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`)
}

const navigate = async (url) => {
  await send('Page.navigate', { url })
  await sleep(1500)
}

const waitFor = async (expression, timeout = 15000) => {
  const start = Date.now()
  while (Date.now() - start < timeout) {
    if (await evaluate(expression)) return true
    await sleep(200)
  }
  return false
}

await device(402, 874)
await navigate(`${BASE}/`)
check('songs listed', (await evaluate('document.querySelectorAll(".song").length')) > 0)
await shot('phone-list')

await evaluate('document.querySelector(".song").click()')
await sleep(300)
check('track loaded', await evaluate('!document.querySelector(".now-playing").hidden'))
await shot('phone-loaded')

await evaluate('document.querySelector(".transport-play").click()')
check('playing', await waitFor('document.getElementById("audio").currentTime > 1', 10000), `t=${await evaluate('document.getElementById("audio").currentTime')}`)
check('no media error', await evaluate('document.getElementById("audio").error === null'))
await shot('phone-playing')
await evaluate('document.querySelector(".transport-pause").click()')

// Offline: download everything, cut the network, reload, play from cache.
check('offline chip visible', await waitFor('!document.querySelector(".chip-offline").hidden'))
await evaluate('document.querySelector(".chip-offline").click()')
check('download finished', await waitFor('document.querySelector(".chip-offline").textContent.startsWith("Offline")', 60000), await evaluate('document.querySelector(".chip-offline").textContent'))
check('service worker active', await waitFor('navigator.serviceWorker.controller !== null', 10000))

await send('Network.emulateNetworkConditions', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 })
await navigate(`${BASE}/`)
check('offline reload', (await evaluate('document.querySelectorAll(".song").length')) > 0)
await evaluate('document.querySelector(".song").click()')
await sleep(300)
await evaluate('document.querySelector(".transport-play").click()')
check('offline playing', await waitFor('document.getElementById("audio").currentTime > 1', 10000), `t=${await evaluate('document.getElementById("audio").currentTime')}`)
check('offline no media error', await evaluate('document.getElementById("audio").error === null'))
await shot('phone-offline-playing')
await send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 })

await device(1024, 744)
await navigate(`${BASE}/`)
await evaluate('document.querySelector(".song").click()')
await sleep(300)
await shot('tablet-loaded')

const errors = events
  .filter((e) => e.method === 'Log.entryAdded' && e.params.entry.level === 'error')
  .map((e) => e.params.entry.text)
check('no console errors', errors.length === 0, errors.join(' | '))

console.log(`\nscreenshots in ${OUT}`)
ws.close()
chrome.kill()
process.exit(results.every((r) => r.ok) ? 0 : 1)
