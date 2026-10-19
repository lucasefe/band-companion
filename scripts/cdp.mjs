// Minimal Chrome DevTools Protocol helper for the check scripts.
import { spawn } from 'node:child_process'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

export async function launchChrome(port = 9333) {
  const chrome = spawn(
    CHROME,
    [
      '--headless=new',
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${mkdtempSync(join(tmpdir(), 'band-chrome-'))}`,
      '--autoplay-policy=no-user-gesture-required',
      '--no-first-run',
      'about:blank',
    ],
    { stdio: 'ignore' },
  )
  process.on('exit', () => chrome.kill())

  let version = null
  for (let i = 0; i < 50 && !version; i++) {
    try {
      version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json()
    } catch {
      await sleep(200)
    }
  }
  if (!version) throw new Error('Chrome did not start')

  const browser = await connect(version.webSocketDebuggerUrl)
  return {
    // Each context has its own storage, so roles stay independent.
    async newPage(url) {
      const { browserContextId } = await browser.send('Target.createBrowserContext')
      const { targetId } = await browser.send('Target.createTarget', { url: 'about:blank', browserContextId })
      const page = await connect(`ws://127.0.0.1:${port}/devtools/page/${targetId}`)
      await page.send('Page.enable')
      await page.send('Runtime.enable')
      await page.send('Network.enable')
      await page.send('Log.enable')
      if (url) await page.navigate(url)
      return page
    },
    close() {
      browser.ws.close()
      chrome.kill()
    },
  }
}

async function connect(url) {
  const ws = new WebSocket(url)
  await new Promise((resolve, reject) => {
    ws.onopen = resolve
    ws.onerror = reject
  })
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

  const page = {
    ws,
    events,
    send,
    async evaluate(expression) {
      const { result, exceptionDetails } = await send('Runtime.evaluate', {
        expression,
        awaitPromise: true,
        returnByValue: true,
      })
      if (exceptionDetails) throw new Error(exceptionDetails.exception?.description ?? 'evaluate failed')
      return result.value
    },
    async waitFor(expression, timeout = 15000) {
      const start = Date.now()
      while (Date.now() - start < timeout) {
        if (await page.evaluate(expression)) return true
        await sleep(150)
      }
      return false
    },
    async navigate(url, settle = 1500) {
      await send('Page.navigate', { url })
      await sleep(settle)
    },
    device(width, height) {
      return send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 2, mobile: true })
    },
    offline(offline) {
      return send('Network.emulateNetworkConditions', { offline, latency: 0, downloadThroughput: -1, uploadThroughput: -1 })
    },
    async screenshot(path) {
      const { data } = await send('Page.captureScreenshot', { format: 'png' })
      writeFileSync(path, Buffer.from(data, 'base64'))
    },
    consoleErrors() {
      return events
        .filter((e) => e.method === 'Log.entryAdded' && e.params.entry.level === 'error')
        .map((e) => e.params.entry.text)
    },
  }
  return page
}

export function reporter() {
  const results = []
  return {
    check(name, ok, detail = '') {
      results.push({ name, ok, detail })
      console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? ` \u2014 ${detail}` : ''}`)
    },
    exitCode: () => (results.every((r) => r.ok) ? 0 : 1),
  }
}
