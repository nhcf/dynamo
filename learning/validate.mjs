// SPDX-License-Identifier: Apache-2.0
// Offline browser checks; no cluster or inference requests.
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
const page = process.argv[2] || "pd-trace.html";
const expected = {
  "observability.html": 32,
  "planner.html": 45,
  "pd-trace.html": 31,
}[page];
if (!expected) throw Error("Unsupported tutorial page: " + page);
const out = dirname(fileURLToPath(import.meta.url)),
  profile = await mkdtemp(join(tmpdir(), "dynamo-tutorial-browser-"));
const chrome = spawn(
  process.env.CHROME_BIN || "/opt/google/chrome/chrome",
  [
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--remote-debugging-port=0",
    "--user-data-dir=" + profile,
    "about:blank",
  ],
  { stdio: "ignore" },
);
let ws,
  seq = 0;
const pending = new Map(),
  errors = [],
  checks = [];
try {
  let port;
  for (let i = 0; i < 100; i++) {
    try {
      port = (
        await readFile(join(profile, "DevToolsActivePort"), "utf8")
      ).split("\n")[0];
      break;
    } catch {}
    await new Promise((r) => setTimeout(r, 100));
  }
  if (!port) throw Error("Chrome did not start");
  const targets = await (
    await fetch("http://127.0.0.1:" + port + "/json/list")
  ).json();
  ws = new WebSocket(
    targets.find((t) => t.type === "page").webSocketDebuggerUrl,
  );
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = reject;
  });
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id) {
      const p = pending.get(m.id);
      if (p) {
        pending.delete(m.id);
        m.error
          ? p.reject(Error(JSON.stringify(m.error)))
          : p.resolve(m.result);
      }
    } else if (m.method === "Runtime.exceptionThrown") {
      errors.push(m.params.exceptionDetails.text);
    }
  };
  const call = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++seq;
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params }));
    });
  await call("Page.enable");
  await call("Runtime.enable");
  async function evaluate(expression) {
    const r = await call("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }
  function check(name, result) {
    checks.push({ name, pass: !!result });
    if (!result) throw Error(name);
  }
  for (const file of ["index.html", page]) {
    await call("Page.navigate", { url: pathToFileURL(join(out, file)).href });
    for (let i = 0; i < 50; i++) {
      if (
        await evaluate(
          "document.readyState==='complete'&&!!document.getElementById('progress-label')?.textContent",
        )
      )
        break;
      await new Promise((r) => setTimeout(r, 100));
    }
    check(
      file + " loaded",
      await evaluate(
        "!!document.querySelector('h1')&&document.getElementById('progress-label').textContent.includes('已读')",
      ),
    );
  }
  if (page === "observability.html") {
    check(
      "switch to OTLP",
      await evaluate(
        "document.querySelector('[data-tab=push]').click();!document.getElementById('panel-push').hidden&&document.getElementById('panel-pull').hidden",
      ),
    );
    check(
      "switch to scrape",
      await evaluate(
        "document.querySelector('[data-tab=pull]').click();!document.getElementById('panel-pull').hidden",
      ),
    );
    check(
      "slider calculation",
      await evaluate(
        "document.getElementById('tokens').value='8';document.getElementById('tokens').dispatchEvent(new Event('input'));document.getElementById('sim-output').textContent.includes('15.00 ms')",
      ),
    );
  }
  if (page === "planner.html") {
    check(
      "switch to SLA",
      await evaluate(
        "document.querySelector('[data-tab=sla]').click();!document.getElementById('plpanel-sla').hidden&&document.getElementById('plpanel-easy').hidden",
      ),
    );
    check(
      "switch to easy",
      await evaluate(
        "document.querySelector('[data-tab=easy]').click();!document.getElementById('plpanel-easy').hidden",
      ),
    );
    check(
      "weighted initial example",
      await evaluate(
        "document.getElementById('pl-weighted').textContent.includes('910.00 ms')",
      ),
    );
    check(
      "weighted slider calculation",
      await evaluate(
        "document.getElementById('pl-n').value='90';document.getElementById('pl-n').dispatchEvent(new Event('input'));document.getElementById('pl-weighted').textContent.includes('550.00 ms')",
      ),
    );
  }
  check(
    "source excerpts",
    await evaluate(
      "document.querySelectorAll('details.source').length===" + expected,
    ),
  );
  check(
    "search empty state",
    await evaluate(
      "document.getElementById('search').value='unfindable_92783';document.getElementById('search').dispatchEvent(new Event('input'));!document.getElementById('no-results').hidden&&[...document.querySelectorAll('section')].every(x=>x.hidden)",
    ),
  );
  check(
    "clear search",
    await evaluate(
      "document.getElementById('search').value='';document.getElementById('search').dispatchEvent(new Event('input'));[...document.querySelectorAll('section')].every(x=>!x.hidden)",
    ),
  );
  check(
    "expand sources",
    await evaluate(
      "document.getElementById('expand').click();[...document.querySelectorAll('details.source')].every(x=>x.open)",
    ),
  );
  check(
    "collapse sources",
    await evaluate(
      "document.getElementById('collapse').click();[...document.querySelectorAll('details.source')].every(x=>!x.open)",
    ),
  );
  check(
    "reading progress",
    await evaluate(
      "document.querySelector('[data-read]').click();document.getElementById('progress-label').textContent.includes('1 / ')",
    ),
  );
  await evaluate("document.querySelector('[data-read]').click()");
  for (const width of [1440, 390]) {
    await call("Emulation.setDeviceMetricsOverride", {
      width,
      height: 1000,
      deviceScaleFactor: 1,
      mobile: false,
    });
    const overflow = await evaluate(
      "document.documentElement.scrollWidth<=innerWidth+1",
    );
    if (!overflow)
      console.log(
        await evaluate(
          "JSON.stringify([...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1&&getComputedStyle(e).position!=='fixed').slice(0,12).map(e=>({tag:e.tagName,cls:e.className,right:e.getBoundingClientRect().right,text:e.textContent.slice(0,70)})))",
        ),
      );
    check("no page overflow " + width, overflow);
  }
  check("no JavaScript exceptions", errors.length === 0);
  console.log(JSON.stringify({ page, checks: checks.length, pass: true }));
  await call("Browser.close").catch(() => {});
  ws.close();
} catch (e) {
  console.error(e);
  ws?.close();
  chrome.kill();
  process.exitCode = 1;
} finally {
  await new Promise((resolve) => {
    if (chrome.exitCode !== null) resolve();
    else chrome.once("exit", resolve);
  });
  await rm(profile, {
    recursive: true,
    force: true,
    maxRetries: 5,
    retryDelay: 100,
  });
}
