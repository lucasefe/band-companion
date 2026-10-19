// Drives headless Chrome through the DevTools protocol against a running
// `npm run dev:worker` (or preview): leader playback, lyrics, offline, and
// followers receiving the position in real time.
// Usage: node scripts/browser-check.mjs [base-url] [screenshot-dir]
import { mkdirSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { launchChrome, reporter, sleep } from './cdp.mjs'

const BASE = process.argv[2] ?? 'http://127.0.0.1:8787'
const OUT = process.argv[3] ?? mkdtempSync(join(tmpdir(), 'band-check-'))
const KEY = 'goodkarma'
mkdirSync(OUT, { recursive: true })

const { check, exitCode } = reporter()
const chrome = await launchChrome()
const shot = (page, name) => page.screenshot(join(OUT, `${name}.png`))

const lead = async (page, key) => {
  await page.evaluate('document.querySelector(".chip-lead").click()')
  await page.evaluate(`document.querySelector(".lead-dialog input").value = ${JSON.stringify(key)}`)
  await page.evaluate('document.querySelector(".lead-form").requestSubmit()')
  await sleep(800)
}

// --- Leader on a phone -------------------------------------------------------

const leader = await chrome.newPage()
await leader.device(402, 874)
await leader.navigate(`${BASE}/`)
check('opens as follower', await leader.evaluate('document.getElementById("app").classList.contains("follow")'))
await shot(leader, 'follower-waiting')

await lead(leader, 'wrong')
check('wrong key rejected', await leader.waitFor('document.getElementById("app").classList.contains("follow") && !document.querySelector(".toast-notice").hidden', 5000), await leader.evaluate('document.querySelector(".toast-notice")?.textContent'))

await lead(leader, KEY)
check('leader UI mounted', await leader.waitFor('document.querySelectorAll(".song").length > 0', 5000))
check('leader chip live', await leader.waitFor('document.querySelector(".chip-live").textContent.startsWith("Leader · ")', 5000), await leader.evaluate('document.querySelector(".chip-live").textContent'))
await shot(leader, 'phone-list')

await leader.evaluate('document.querySelector(".song").click()')
await sleep(300)
check('track loaded', await leader.evaluate('!document.querySelector(".now-playing").hidden'))
check('lyrics view shown', await leader.evaluate('document.getElementById("app").classList.contains("view-lyrics")'))
check('lyrics rendered', (await leader.evaluate('document.querySelectorAll(".lyric").length')) > 10)
await shot(leader, 'phone-loaded')

await leader.evaluate('document.querySelector(".transport-play").click()')
check('playing', await leader.waitFor('document.getElementById("audio").currentTime > 1', 10000), `t=${await leader.evaluate('document.getElementById("audio").currentTime')}`)
check('no media error', await leader.evaluate('document.getElementById("audio").error === null'))
await leader.evaluate('document.getElementById("audio").currentTime = 52')
check('current line follows time', await leader.waitFor('document.querySelector(".lyric.is-current")?.textContent.startsWith("Cada momento, voy")', 3000), await leader.evaluate('document.querySelector(".lyric.is-current")?.textContent'))
await sleep(600)
await shot(leader, 'phone-playing')

// --- Follower on a second device --------------------------------------------

const follower = await chrome.newPage()
await follower.device(402, 874)
await follower.navigate(`${BASE}/`)
check('follower live', await follower.waitFor('document.querySelector(".chip-live").textContent === "Live"', 5000), await follower.evaluate('document.querySelector(".chip-live").textContent'))
check('follower sees title', await follower.waitFor('document.querySelector(".now-title").textContent.normalize("NFC") === "No podrás"', 5000), await follower.evaluate('document.querySelector(".now-title").textContent'))
check('follower line matches leader', await follower.waitFor('document.querySelector(".lyric.is-current")?.textContent === document.querySelector(".lyric.is-current")?.textContent && /Cada momento, voy|Y es que no queda/.test(document.querySelector(".lyric.is-current")?.textContent ?? "")', 5000), await follower.evaluate('document.querySelector(".lyric.is-current")?.textContent'))
check('leader counts follower', await leader.waitFor('document.querySelector(".chip-live").textContent === "Leader · 1"', 5000), await leader.evaluate('document.querySelector(".chip-live").textContent'))
await shot(follower, 'follower-live')

await leader.evaluate('document.querySelector(".transport-pause").click()')
await sleep(500)
const leaderTime = await leader.evaluate('document.getElementById("audio").currentTime')
await leader.evaluate('document.querySelector(".transport-restart").click()')
// The file opens with a [00:00.000] gap line, so position 0 lands on it.
check('follower follows restart', await follower.waitFor('document.querySelector(".lyric.is-current") === document.querySelector(".lyric")', 5000), `leader paused at ${leaderTime.toFixed(1)}s then restarted`)

// --- Takeover and step down ---------------------------------------------------

const other = await chrome.newPage()
await other.device(402, 874)
await other.navigate(`${BASE}/`)
await lead(other, KEY)
check('second device leads', await other.waitFor('document.querySelectorAll(".song").length > 0', 5000))
check('first device demoted', await leader.waitFor('document.getElementById("app").classList.contains("follow")', 5000), await leader.evaluate('document.querySelector(".toast-notice")?.textContent'))
await shot(leader, 'demoted')

await other.evaluate('window.confirm = () => true')
await other.evaluate('document.querySelector(".chip-live").click()')
check('step down', await other.waitFor('document.getElementById("app").classList.contains("follow")', 5000))
check('followers see leader offline', await follower.waitFor('document.querySelector(".chip-live").textContent === "Leader offline"', 5000))

// --- Offline on the leader device --------------------------------------------

await lead(leader, KEY)
await leader.waitFor('document.querySelectorAll(".song").length > 0', 5000)
check('offline chip visible', await leader.waitFor('!document.querySelector(".chip-offline").hidden'))
await leader.evaluate('document.querySelector(".chip-offline").click()')
check('download finished', await leader.waitFor('document.querySelector(".chip-offline").textContent.startsWith("Offline")', 60000), await leader.evaluate('document.querySelector(".chip-offline").textContent'))
check('service worker active', await leader.waitFor('navigator.serviceWorker.controller !== null', 10000))

await leader.offline(true)
await leader.navigate(`${BASE}/`)
check('offline reload keeps leader role', (await leader.evaluate('document.querySelectorAll(".song").length')) > 0)
await leader.evaluate('document.querySelector(".song").click()')
await sleep(300)
await leader.evaluate('document.querySelector(".transport-play").click()')
check('offline playing', await leader.waitFor('document.getElementById("audio").currentTime > 1', 10000), `t=${await leader.evaluate('document.getElementById("audio").currentTime')}`)
check('offline no media error', await leader.evaluate('document.getElementById("audio").error === null'))
check('offline chip says so', await leader.waitFor('document.querySelector(".chip-live").textContent === "Leader · offline"', 5000), await leader.evaluate('document.querySelector(".chip-live").textContent'))
await shot(leader, 'phone-offline-playing')
await leader.evaluate('document.querySelector(".transport-pause").click()')
await leader.offline(false)

// --- Tablet --------------------------------------------------------------------

await leader.device(1024, 744)
await leader.navigate(`${BASE}/`)
await leader.evaluate('document.querySelector(".song").click()')
await sleep(300)
await leader.evaluate('document.querySelector(".transport-play").click()')
await leader.evaluate('document.getElementById("audio").currentTime = 52')
await sleep(800)
check('tablet shows both panes', await leader.evaluate('getComputedStyle(document.querySelector(".songs")).display !== "none" && getComputedStyle(document.querySelector(".lyrics")).display !== "none"'))
await shot(leader, 'tablet-playing')
await leader.evaluate('document.querySelector(".transport-pause").click()')

await follower.device(1024, 744)
await sleep(300)
await shot(follower, 'tablet-follower')

const errors = [...leader.consoleErrors(), ...follower.consoleErrors(), ...other.consoleErrors()]
  .filter((text) => !text.includes('WebSocket connection') && !text.includes('net::ERR_INTERNET_DISCONNECTED'))
check('no console errors', errors.length === 0, errors.join(' | '))

console.log(`\nscreenshots in ${OUT}`)
chrome.close()
process.exit(exitCode())
