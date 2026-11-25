import { writeFile, readFile, mkdir, unlink, copyFile } from "node:fs/promises"
import { resolve } from "node:path"
import assert from "node:assert/strict"

const [fixture, output] = process.argv.slice(2).map((value) => resolve(value))
const origin = process.env.SCANPAY_ACCEPTANCE_ORIGIN
const demo = process.env.SCANPAY_ACCEPTANCE_DEMO === "1"
if (!(demo ? /^https:\/\/127\.0\.0\.1:\d+$/ : /^http:\/\/127\.0\.0\.1:\d+$/).test(origin)) {
  throw Error("Use the loopback synthetic frontend.")
}
const chromePort = Number(process.env.SCANPAY_ACCEPTANCE_CHROME_PORT)
if (!chromePort) throw Error("Set the local Chrome debugging port.")
const delay = (ms) => new Promise((done) => setTimeout(done, ms))
const browsers = await fetch(`http://127.0.0.1:${chromePort}/json/list`).then((response) => response.json())
const rpc = new WebSocket(browsers.find((page) => page.type === "page").webSocketDebuggerUrl)
await new Promise((done, reject) => { rpc.addEventListener("open", done); rpc.addEventListener("error", reject) })
let messageId = 0
const pending = new Map()
const results = []
let failedRequest = null
rpc.addEventListener("message", ({ data }) => {
  const packet = JSON.parse(data)
  if (packet.method === "Fetch.requestPaused") {
    const { requestId, request } = packet.params
    const drop = failedRequest === request.method
    if (drop) failedRequest = null
    void call(drop ? "Fetch.failRequest" : "Fetch.continueRequest",
      drop ? { requestId, errorReason: "ConnectionReset" } : { requestId })
  }
  if (packet.method === "Network.responseReceived") {
    const response = packet.params.response
    if (response.url.startsWith(origin + "/api/")) {
      results.push([new URL(response.url).pathname, response.status])
    }
  }
  const callback = pending.get(packet.id)
  if (callback) {
    pending.delete(packet.id)
    if (packet.error) callback.reject(Error(packet.error.message))
    else callback.done(packet.result)
  }
})
function call(method, params = {}) {
  const id = ++messageId
  return new Promise((done, reject) => {
    pending.set(id, { done, reject })
    rpc.send(JSON.stringify({ id, method, params }))
  })
}
async function evaluate(expression) {
  const { result, exceptionDetails } = await call("Runtime.evaluate", {
    expression, awaitPromise: true, returnByValue: true,
  })
  if (exceptionDetails) throw Error(exceptionDetails.exception?.description ?? exceptionDetails.text)
  return result.value
}
async function until(expression, label) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (await evaluate(expression)) return
    await delay(100)
  }
  const alerts = await evaluate("Array.from(document.querySelectorAll('[role=alert]'), x => x.textContent)")
  throw Error(`Timed out at ${label} (${JSON.stringify(alerts)})`)
}
function byText(selector, text) {
  return `Array.from(document.querySelectorAll(${JSON.stringify(selector)})).find(el => el.textContent.trim() === ${JSON.stringify(text)})`
}
async function click(selector, label) {
  if (label !== undefined) {
    const button = byText(selector, label)
    await until(`!!(${button}) && !(${button}).disabled`, label)
    await evaluate(`(${button}).click()`)
  } else {
    await until(`!!document.querySelector(${JSON.stringify(selector)})`, selector)
    await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`)
  }
}
async function fill(selector, value) {
  await evaluate(`(() => { const field = document.querySelector(${JSON.stringify(selector)});
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(field, ${JSON.stringify(value)});
    field.dispatchEvent(new Event('input', {bubbles:true}));
    field.dispatchEvent(new Event('change', {bubbles:true}));
  })()`)
}
async function text(value) {
  await until(`document.body.innerText.includes(${JSON.stringify(value)})`, value)
}
async function login(role) {
  await text("Sign in")
  await fill('input[autocomplete="username"]', role === "admin" ? "scanpay_admin" : `acceptance_${role}`)
  await fill('input[autocomplete="current-password"]', process.env.SCANPAY_ACCEPTANCE_PASSWORD)
  await click("button", "Sign in")
  await text(`Acceptance ${role[0].toUpperCase() + role.slice(1)}`)
}
async function width(w) {
  await call("Emulation.setDeviceMetricsOverride", { width: w, height: 900, deviceScaleFactor: 1, mobile: false })
  await delay(100)
}
async function assertLayout(page, expected) {
  const statistics = await evaluate("({ width: innerWidth, content: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth })")
  assert.equal(statistics.width, expected)
  assert.ok(statistics.content <= statistics.viewport, `${page} overflows at ${expected}px`)
}
async function screenshot(name) {
  const { data } = await call("Page.captureScreenshot", { format: "png" })
  await writeFile(resolve(output, name + ".png"), Buffer.from(data, "base64"))
}

async function navigate(path) {
  await call("Page.navigate", { url: origin + path })
}

async function select(label, value) {
  const field = `Array.from(document.querySelectorAll('label')).find(el=>el.textContent.trim().startsWith(${JSON.stringify(label)}))?.querySelector('select,textarea')`
  await until(`!!(${field}) && !(${field}).disabled`, label)
  await evaluate(`(() => {
    const element = ${field};
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), "value").set.call(element, ${JSON.stringify(value)});
    element.dispatchEvent(new Event("input", {bubbles:true}));
    element.dispatchEvent(new Event("change", {bubbles:true}));
  })()`)
}

async function setFile(type, file) {
  const { root } = await call("DOM.getDocument")
  const { nodeId } = await call("DOM.querySelector", { nodeId: root.nodeId, selector: "#upload-" + type })
  assert.ok(nodeId, `Visible ${type} field`)
  await call("DOM.setFileInputFiles", { nodeId, files: [resolve(output, file)] })
}

async function layouts(page) {
  for (const pixels of [360, 768, 1024, 1440]) {
    await width(pixels)
    await assertLayout(page, pixels)
  }
}

const axePath = process.env.SCANPAY_ACCEPTANCE_AXE_PATH
const axeSource = axePath ? await readFile(axePath, "utf8") : null
async function accessibility(page) {
  if (!axeSource) return
  await evaluate(axeSource + "\nvoid 0")
  const violations = await evaluate(`axe.run(document, {runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})
    .then(({violations}) => violations.map(item=>[item.id,item.nodes.map(node=>node.target)]))`)
  assert.deepEqual(violations, [], page)
}

async function key(name, virtualCode) {
  for (const type of ["keyDown", "keyUp"]) {
    await call("Input.dispatchKeyEvent", { type, key: name, code: name, windowsVirtualKeyCode: virtualCode })
  }
}

async function openOverrideFromFocus() {
  const trigger = byText("button", "Override decision")
  await until(`!!(${trigger})`, "override button")
  await evaluate(`(() => { const button = ${trigger}; button.focus(); button.click() })()`)
  await until(
    "document.querySelector('dialog.override-dialog')?.contains(document.activeElement)",
    "focus enters override dialog",
  )
}

async function api(path) {
  const response = await evaluate(`fetch(${JSON.stringify(path)}).then(async response=>({
    status:response.status,data:await response.json()
  }))`)
  assert.equal(response.status, 200, path)
  return response.data
}

const runQuery = (id) => `?run_id=${id}`
const signOut = async () => {
  await click('button[aria-label="Sign out"]')
  await text("Sign in")
}
const nav = (name) => click(".desktop-sidebar a", name)
const recorded = (phrase) => until(`document.body.innerText.includes(${JSON.stringify(phrase)})`, phrase)
const clickRun = async (previousId = null, exerciseError = false) => {
  await click("a", "Use dataset for evaluation")
  if (exerciseError) {
    if (process.env.SCANPAY_ACCEPTANCE_PUBLICATION_FAULT === "1") {
      await click("button", "Start evaluation")
      await recorded("Failed")
      assert.ok(results.some(([path, status]) => path === "/api/evaluations" && status === 503))
      for (let attempt = 0; attempt < 60; attempt += 1) {
        try { await readFile(resolve(output, "publication-fault-cleared")); break } catch { await delay(100) }
      }
    } else {
      failedRequest = "POST"
      await call("Fetch.enable", { patterns: [{ urlPattern: origin + "/api/evaluations" }] })
      await click("button", "Start evaluation")
      await until(`!!(${byText("button", "Retry evaluation")})`, "interrupted evaluation enables retry")
      assert.equal(failedRequest, null)
      await call("Fetch.disable")
    }
  }
  const started = performance.now()
  const runButton = byText("button", exerciseError ? "Retry evaluation" : "Start evaluation")
  await until(`!!(${runButton}) && !(${runButton}).disabled`, "evaluation ready")
  await evaluate(`(() => { const button = ${runButton}; button.click(); button.click() })()`)
  await until("/Evaluation completed|Existing run reused/.test(document.body.innerText)", "evaluation result")
  const elapsedMs = Math.round(performance.now() - started)
  await click("button", "Inspect completed run")
  await click("button", "Use this run")
  await until(`new URLSearchParams(location.search).has('run_id') && new URLSearchParams(location.search).get('run_id') !== ${JSON.stringify(previousId)}`, "selected run")
  const id = await evaluate("new URLSearchParams(location.search).get('run_id')")
  return { id, elapsedMs }
}

await call("Page.enable")
await call("Network.enable")
await mkdir(output, { recursive: true })
await call("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: output })
const files = {
  teams: "teams.csv",
  history_transactions: "transactions_history.csv",
  history_payouts: "payouts_history.csv",
  latest_transactions: "transactions_10nov.csv",
  pending_payouts: "payouts_pending.csv",
}

async function demoWorkflow() {
  const write = async (path, body) => evaluate(`(async () => {
    const csrf = (await (await fetch("/api/auth/csrf")).json()).csrf_token;
    return (await fetch(${JSON.stringify(path)}, {method:"POST",
      headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},
      body:JSON.stringify(${JSON.stringify(body)})})).status;
  })()`)
  await width(1440)
  await navigate("/datasets")
  await login("admin")
  await text("Synthetic demo")
  assert.equal(await evaluate("document.querySelectorAll('input[type=file]').length"), 0)
  assert.equal(await write("/api/datasets/upload", {}), 403)
  await click("button", "Import synthetic preset")
  await click("button", "Import preset")
  await text("Dataset imported")
  const run = await clickRun()
  const query = runQuery(run.id)
  const payouts = await api("/api/payouts" + query)
  const target = payouts.items.find((item) => item.effective_decision === "MANUAL_REVIEW")
  assert.ok(target, "Demo preset has a payout that a reviewer can approve")
  const path = "/payouts/" + encodeURIComponent(target.payout_id)
  await signOut()

  await navigate(path + query)
  await login("analyst")
  await text("Version 1 is current")
  assert.equal(await write("/api" + path + "/overrides", {
    run_id: run.id, expected_version: 1, decision: "APPROVE",
    reason: "Verified the synthetic contract invoice.", evidence: [], cluster_id: null,
  }), 201)
  await call("Page.reload", { ignoreCache: true })
  await text("Version 2 is current")
  await signOut()

  await navigate(path + query)
  await login("reviewer")
  await text("Version 1 is current")
  assert.equal(await evaluate("document.body.innerText.includes('Verified the synthetic contract invoice.')"), false)
  await layouts("Demo reviewer investigation")
  if (process.env.SCANPAY_ACCEPTANCE_EXPIRY_CHECK === "1") {
    await delay(65_000)
    await call("Page.reload", { ignoreCache: true })
    await text("Sign in")
    assert.equal(await evaluate("fetch('/api/auth/me').then(r=>r.status)"), 401)
  } else {
    await signOut()
  }
  console.log("Demo browser verified preset-only access and independent reviewer histories.")
}

async function localWorkflow() {
  for (const file of Object.values(files)) await copyFile(resolve(fixture, file), resolve(output, file))
  await width(1440)
  await navigate("/datasets")
  await text("Sign in")
  await accessibility("Sign in")
  await key("Tab", 9)
  assert.ok(await evaluate("document.activeElement.matches('input,button,a') && document.activeElement.matches(':focus-visible')"))
  await login("admin")
  await text("Import CSV dataset")
  await text("No batches yet")
  assert.equal(await evaluate("document.querySelectorAll('input[type=file]').length"), 5)
  await layouts("Initial import")
  await accessibility("Initial import")
  await screenshot("initial-import")
  await fill('input[type="datetime-local"]', "2026-11-13T10:00")
  await click("input[type=checkbox]")
  const pending = await readFile(resolve(output, files.pending_payouts), "utf8")
  await writeFile(resolve(output, "invalid.csv"), pending.replace("P-01,0001,", "P-01,9999,"))
  for (const [type, file] of Object.entries(files)) await setFile(type, type === "pending_payouts" ? "invalid.csv" : file)
  await click("button", "Upload dataset")
  await recorded("Retry upload")
  await setFile("pending_payouts", files.pending_payouts)
  await click("button", "Retry upload")
  await text("Dataset imported")
  await text("Ready")
  for (const file of Object.values(files)) await unlink(resolve(output, file))
  await unlink(resolve(output, "invalid.csv"))

  const first = await clickRun(null, true)
  const query = runQuery(first.id)
  const started = performance.now()
  const initial = await api("/api/dashboard" + query)
  const dashboardMs = Math.round(performance.now() - started)
  assert.deepEqual(initial.total, { count: 20, cents: 212810 })
  assert.deepEqual(initial.categories.rejected, { count: 1, cents: 9177 })
  assert.deepEqual(initial.categories.open_manual_review, { count: 3, cents: 51833 })
  assert.deepEqual(initial.categories.automatically_approved, { count: 16, cents: 151800 })
  assert.equal(initial.historical.first_day, "2026-10-23")
  assert.equal(initial.historical.last_day, "2026-11-11")
  assert.ok(initial.ml.metadata.metrics.training_fitted > 0)
  assert.ok(initial.ml.metadata.metrics.validation_scored > 0)
  assert.ok(Number.isFinite(initial.ml.metadata.anomaly_threshold))
  await nav("Overview")
  await text("$2,128.10")
  await until("document.querySelectorAll('svg.recharts-surface').length >= 2", "Recharts plots")
  await layouts("Dashboard")
  await accessibility("Dashboard")
  await nav("Payout queue")
  await text("20 matching")
  await layouts("Queue")
  await accessibility("Payout queue")
  await select("Sort by", "amount")
  await until("new URLSearchParams(location.search).get('sort') === 'amount'", "amount sort applied")
  await select("Order", "desc")
  await until("new URLSearchParams(location.search).get('direction') === 'desc'", "descending order applied")
  await until("document.querySelector('.payout-table tbody th')?.textContent.trim()==='P-04'", "highest amount sorted first")
  const labels = {}
  for (let n = 1; n <= 20; n += 1) {
    const payout = `P-${String(n).padStart(2, "0")}`
    await click("a", payout)
    await text(`Payout ${payout}`)
    await text("Audit history")
    if (n === 1) {
      await text("Established Exposure")
      await layouts("Investigation")
      await accessibility("Payout investigation")
      for (const section of ["Covered transactions", "Statistical analysis", "Fraud analysis", "Isolation Forest analysis"]) {
        await text(section)
      }
    }
    const published = await api("/api/payouts/" + payout + query)
    const amount = 9150 + 27 * n + (n === 4 ? 24140 : 0)
    assert.equal(published.payout.payout_amount_cents, amount)
    assert.equal(published.payout.captured_count, 2)
    assert.deepEqual(published.transactions.map((tx) => tx.status).sort(), ["captured", "captured", "declined", "refunded"])
    assert.ok(Number.isFinite(published.ml.anomaly_score))
    const expected = n === 1 ? "REJECT" : n <= 4 ? "MANUAL_REVIEW" : "APPROVE"
    assert.equal(published.payout.effective_decision, expected, payout)
    labels[payout] = published.payout.effective_decision
    await nav("Payout queue")
    await text("20 matching")
  }
  assert.equal(labels["P-01"], "REJECT")
  assert.equal(labels["P-04"], "MANUAL_REVIEW")
  assert.ok(Object.values(labels).includes("APPROVE"))
  await select("Effective decision", "REJECT")
  await text("1 matching")
  await click("a", "P-01")
  await openOverrideFromFocus()
  await width(360)
  await assertLayout("Override dialog", 360)
  await select("New decision", "APPROVE")
  await select("Override reason", 'Verified the fictional invoice, payer and "shipment" reference.')
  await click("button", "Review change")
  await accessibility("Override confirmation")
  const concurrent = await evaluate(`(async () => {
    const csrf = (await (await fetch("/api/auth/csrf")).json()).csrf_token;
    return (await fetch("/api/payouts/P-01/overrides",{
      method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},
      body: JSON.stringify({run_id:${JSON.stringify(first.id)},expected_version:1,decision:"MANUAL_REVIEW",
        reason:"Another reviewer asked to check the fictional payment before clearing it.",
        evidence:[{type:"transaction",id:"CURRENT-01-1"}],cluster_id:null})
    })).status
  })()`)
  assert.equal(concurrent, 201)
  await click("button", "Confirm override")
  await text("Decision changed")
  await text("Version 2 is current")
  await openOverrideFromFocus()
  await select("New decision", "APPROVE")
  await select("Override reason", 'Verified the fictional invoice, payer and "shipment" reference.')
  await click("button", "Review change")
  await click("button", "Confirm override")
  await text("Version 3 is current")
  await width(1440)
  await nav("Overview")
  await until("Array.from(document.querySelectorAll('.risk-tile')).some(el => el.innerText.includes('Analyst approved') && el.innerText.includes('$91.77'))", "updated analyst approval")
  const updated = await api("/api/dashboard" + query)
  assert.deepEqual(updated.categories.analyst_approved, { count: 1, cents: 9177 })
  assert.deepEqual(updated.categories.rejected, { count: 0, cents: 0 })
  assert.deepEqual(updated.historical, initial.historical)
  await nav("Fraud insights")
  await text("Original automated cluster")
  await layouts("Fraud insights")
  await accessibility("Fraud insights")
  await nav("Model insights")
  await text("Selected-run model")
  await layouts("Model insights")
  await accessibility("Model insights")
  await nav("Payout queue")
  await select("Effective decision", "REJECT")
  await text("No matching payouts")
  await click("button", "Export decisions")
  await text("CSV download started.")
  const csvPath = resolve(output, "decisions.csv")
  let csv
  for (let attempt = 0; attempt < 40 && !csv; attempt += 1) {
    try { csv = await readFile(csvPath, "utf8") } catch { await delay(150) }
  }
  assert.ok(csv)
  assert.ok(csv.includes('""shipment""'))
  assert.equal(csv.trim().split(/\r?\n/).length, 21)

  await nav("Datasets")
  if (!(await evaluate("!!document.querySelector('input[type=file]')"))) {
    await click("button", "Initial or incremental import")
  }
  await select("Import mode", "INCREMENTAL")
  await fill('input[type="datetime-local"]', "2026-11-14T10:00")
  const newTx = (await readFile(resolve(fixture, files.latest_transactions), "utf8")).split(/\r?\n/, 2)[0]
  const newPayout = pending.split(/\r?\n/, 2)[0]
  await writeFile(resolve(output, "future-payments.csv"), newTx + "\nFUTURE-TX,0001,2026-11-11 13:00:00,100.00,2.25,ach,,,captured,Fictional buyer,00315\n")
  await writeFile(resolve(output, "future-payouts.csv"), newPayout + "\nFUTURE-PAYOUT,0001,2026-11-13 17:30:00,2026-11-11,2026-11-11,1,102.25,PENDING_DECISION\n")
  await setFile("latest_transactions", "future-payments.csv")
  await setFile("pending_payouts", "future-payouts.csv")
  await click("button", "Upload dataset")
  await text("Dataset imported")
  const second = await clickRun(first.id)
  assert.notEqual(first.id, second.id)
  assert.deepEqual((await api("/api/dashboard" + runQuery(second.id))).total, { count: 1, cents: 10225 })
  await navigate("/dashboard" + query)
  await text("$2,128.10")
  await call("Page.reload", { ignoreCache: true })
  await text("$2,128.10")
  await signOut()
  await navigate("/payouts/P-04" + query)
  await login("analyst")
  await text("Version 1 is current")
  await openOverrideFromFocus()
  await until("document.querySelector('dialog.override-dialog').open", "analyst override available")
  await call("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 })
  await call("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 })
  await until("!document.querySelector('dialog.override-dialog')?.open", "keyboard closes dialog")
  await until("document.activeElement?.textContent.trim()==='Override decision'", "focus returns to override button")
  await navigate("/datasets")
  await text("Dataset access is read-only")
  assert.equal(await evaluate("document.querySelectorAll('input[type=file]').length"), 0)
  await signOut()
  await navigate("/payouts/P-01" + query)
  await login("viewer")
  await text("Version 3 is current")
  assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).some(el=>el.innerText.trim()==='Override decision')"), false)
  await nav("Overview")
  await text("$2,128.10")
  assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).some(el=>el.innerText.trim()==='Export decisions')"), false)
  await signOut()
  assert.ok(results.some(([path, status]) => path === "/api/datasets/upload" && status === 422))
  assert.ok(results.some(([path, status]) => path === "/api/payouts/P-01/overrides" && status === 409))
  assert.equal(results.filter(([path, status]) => path === "/api/evaluations" && status === 201).length, 2)
  assert.ok(results.some(([path, status]) => path === "/api/exports/decisions.csv" && status === 200))
  const result = { runId: first.id, firstEvaluationMs: first.elapsedMs, incrementalEvaluationMs: second.elapsedMs,
    dashboardMs, initial, updatedCategories: updated.categories, labels }
  await writeFile(resolve(output, "synthetic-results.json"), JSON.stringify(result, null, 2))
  console.log("Browser verified invalid upload and retry, assessment import, 20 investigations, ML, override, export, incremental import, three roles and four viewport widths.")
}
try {
  await (demo ? demoWorkflow() : localWorkflow())
} catch (error) {
  await screenshot("failure")
  console.error(await evaluate("({path:location.pathname, query:location.search, first:document.querySelector('.payout-table tbody th')?.textContent})"))
  throw error
} finally {
  rpc.close()
}

