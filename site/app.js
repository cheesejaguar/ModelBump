/* ModelBump site. The bump for every catalog slug is computed ahead of time by
   the real planner (scripts/build_site.py); this file finds slugs in pasted
   code with the same matching rules as the scanner and shows the results. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const SITE = "https://cheesejaguar.github.io/ModelBump";
  const ACTION = "cheesejaguar/ModelBump@v0";

  let data = null;

  // ---------------------------------------------------------------- utilities

  const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const money = (v) => (v == null ? "–" : v >= 0.1 ? `$${v.toFixed(2)}` : `$${v.toFixed(3)}`);

  function toast(text) {
    const el = $("#toast");
    el.textContent = text;
    el.classList.add("show");
    clearTimeout(toast.t);
    toast.t = setTimeout(() => el.classList.remove("show"), 1600);
  }

  async function copy(text, button) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = Object.assign(document.createElement("textarea"), { value: text });
      document.body.append(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    toast("Copied to clipboard");
    if (button) {
      button.classList.add("copied");
      const state = $(".copy-state", button);
      if (state) state.textContent = "Copied";
      setTimeout(() => {
        button.classList.remove("copied");
        if (state) state.textContent = "Copy";
      }, 1600);
    }
  }

  document.addEventListener("click", (e) => {
    const direct = e.target.closest("[data-copy]");
    if (direct) return copy(direct.dataset.copy, direct);
    const from = e.target.closest("[data-copy-from]");
    if (from) return copy($("#" + from.dataset.copyFrom).textContent, from);
  });

  function wireTabs(list, onSelect) {
    const tabs = $$('[role="tab"]', list);
    const select = (tab, focus) => {
      tabs.forEach((t) => {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        const panel = t.getAttribute("aria-controls");
        if (panel) $("#" + panel).hidden = !on;
      });
      if (focus) tab.focus();
      onSelect?.(tab);
    };
    tabs.forEach((t, i) => {
      t.addEventListener("click", () => select(t, false));
      t.addEventListener("keydown", (e) => {
        const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
        if (!step) return;
        e.preventDefault();
        select(tabs[(i + step + tabs.length) % tabs.length], true);
      });
    });
  }

  // ---------------------------------------------------------------- nav

  const nav = $(".nav");
  const onScroll = () => nav.classList.toggle("scrolled", window.scrollY > 8);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // ---------------------------------------------------------------- hero

  function splitDiff(a, b) {
    let p = 0;
    while (p < a.length && p < b.length && a[p] === b[p]) p++;
    let s = 0;
    while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++;
    return { pre: a.slice(0, p), oldMid: a.slice(p, a.length - s), newMid: b.slice(p, b.length - s), post: a.slice(a.length - s) };
  }

  // Width of text in the slug's font (CSS ch units are off for this font's
  // expanded width axis).
  function measure(slug, text) {
    const probe = document.createElement("span");
    probe.style.cssText = "position:absolute;visibility:hidden;white-space:pre";
    probe.textContent = text;
    slug.append(probe);
    const w = probe.getBoundingClientRect().width;
    probe.remove();
    return w;
  }

  function startHero(pairs) {
    const slug = $("#hero-slug");
    const caption = $("#hero-caption");
    if (!pairs.length) return;
    let i = 0;
    const show = () => {
      const [from, to] = pairs[i % pairs.length];
      const d = splitDiff(from, to);
      slug.classList.remove("rolled");
      $(".slug-pre", slug).textContent = d.pre;
      $(".slug-old", slug).textContent = d.oldMid;
      $(".slug-new", slug).textContent = d.newMid;
      $(".slug-post", slug).textContent = d.post;
      slug.style.setProperty("--chars", Math.max(from.length, to.length, 12));
      slug.style.setProperty("--w-old", `${measure(slug, d.oldMid)}px`);
      slug.style.setProperty("--w-new", `${measure(slug, d.newMid)}px`);
      const kind = data?.slugs[from]?.k ?? "";
      caption.innerHTML = `<code>${esc(from)}</code> <span class="arrow">→</span> <code>${esc(to)}</code>${kind ? `<span class="kind">${esc(kind)}</span>` : ""}`;
      slug.classList.remove("fade");
      setTimeout(() => slug.classList.add("rolled"), reducedMotion ? 0 : 1300);
      setTimeout(() => {
        if (reducedMotion || pairs.length === 1) return;
        slug.classList.add("fade");
        setTimeout(() => { i++; show(); }, 300);
      }, reducedMotion ? 4000 : 4600);
      if (reducedMotion && pairs.length > 1) setTimeout(() => { i++; show(); }, 4000);
    };
    show();
    window.addEventListener("resize", () => {
      const [from, to] = pairs[i % pairs.length];
      const d = splitDiff(from, to);
      slug.style.setProperty("--w-old", `${measure(slug, d.oldMid)}px`);
      slug.style.setProperty("--w-new", `${measure(slug, d.newMid)}px`);
    });
  }

  // ---------------------------------------------------------------- matching

  const BEFORE = String.raw`(?<![A-Za-z0-9_\-^])(?<!anthropic\.)`;
  const AFTER = String.raw`(?:${":(?:nitro|floor|online|exacto)"})?(?![A-Za-z0-9_($]|\\[.dwsDWSb\-(\[{]|[.\-:@][A-Za-z0-9])`;
  const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\/-]/g, "\\$&");
  let matchers = null;

  function buildMatchers(slugs) {
    const byLen = (a, b) => b.length - a.length;
    const sep = slugs.filter((s) => /[-./:@]/.test(s)).sort(byLen);
    const bare = slugs.filter((s) => !/[-./:@]/.test(s)).sort(byLen);
    return [
      new RegExp(`${BEFORE}(${sep.map(reEsc).join("|")})${AFTER}`, "g"),
      new RegExp(`(?<=["'\`])(${bare.map(reEsc).join("|")})(?=["'\`])`, "g"),
    ];
  }

  const CMP_BEFORE = /(?:[=!]==?|\b(?:startswith|endswith|startsWith|endsWith|includes|contains|indexOf|match|search)\(|\bcase)\s*[rbf]?["'`]$/;
  const CMP_AFTER = /^["'`]\s*(?:[=!]==?|in\b|not\s+in\b)/;
  const KEY_AFTER = /^["'`]?\s*:(?!:)\s*\S/;

  function context(line, col) {
    const before = line.slice(0, col);
    if (/[a-z][a-z0-9+.-]*:\/\/\S*$/i.test(before)) return "a URL";
    if (before.includes("<!--") && !before.slice(before.lastIndexOf("<!--")).includes("-->")) return "a comment";
    const stripped = before.trimStart();
    if (stripped.startsWith("#") || stripped.startsWith("//") || /(^|\s)(#|\/\/)/.test(before)) return "a comment";
    return null;
  }

  function scan(text) {
    const lines = text.split("\n");
    const starts = [0];
    for (let i = 0; i < text.length; i++) if (text[i] === "\n") starts.push(i + 1);
    const lineOf = (pos) => {
      let lo = 0, hi = starts.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (starts[mid] <= pos) lo = mid; else hi = mid - 1;
      }
      return lo;
    };
    const found = [];
    for (const re of matchers) {
      re.lastIndex = 0;
      for (const m of text.matchAll(re)) {
        const slug = m[1];
        const start = m.index + m[0].indexOf(slug);
        const li = lineOf(start);
        const line = lines[li].replace(/\r$/, "");
        const col = start - starts[li];
        if (/modelbump:\s*ignore(?!-)/i.test(line) || (li > 0 && /modelbump:\s*ignore-next-line/i.test(lines[li - 1]))) continue;
        found.push({ slug, line: li, col, lineText: line });
      }
    }
    return found.sort((a, b) => a.line - b.line || a.col - b.col);
  }

  function plan(text) {
    const occ = scan(text);
    const perFamily = new Map();
    const allModels = new Set();
    for (const o of occ) {
      const m = data.slugs[o.slug].m;
      allModels.add(m);
      const fam = m.split("@")[0];
      if (!perFamily.has(fam)) perFamily.set(fam, new Set());
      perFamily.get(fam).add(m);
    }
    const keyed = new Set(occ.filter((o) => KEY_AFTER.test(o.lineText.slice(o.col + o.slug.length))).map((o) => `${o.line}:${o.slug}`));

    return occ.map((o) => {
      const entry = data.slugs[o.slug];
      const model = data.models[entry.m];
      const out = { ...o, entry, model };
      const where = context(o.lineText, o.col);
      if (where) return { ...out, skip: `left alone: in ${where}` };
      if (keyed.has(`${o.line}:${o.slug}`)) return { ...out, skip: "left alone: used as a key in a model table" };
      const versions = perFamily.get(entry.m.split("@")[0]).size;
      if (versions >= 3 || allModels.size >= 8) {
        return { ...out, skip: `left alone: this text names ${versions >= 3 ? `${versions} versions of one model line` : `${allModels.size} different models`}, so it looks like a model list` };
      }
      if (!entry.to) return { ...out, skip: entry.why ? `no change: ${entry.why}` : "already the newest release in its line" };
      if (entry.tm !== entry.m && allModels.has(entry.tm)) return { ...out, skip: `left alone: ${data.models[entry.tm].name} is already used here` };
      const notes = [...(entry.n || [])];
      const after = o.lineText.slice(o.col + o.slug.length);
      if (CMP_BEFORE.test(o.lineText.slice(0, o.col)) || CMP_AFTER.test(after)) {
        notes.push(`This line compares against \`${o.slug}\`; check that the condition should now match \`${entry.to}\`.`);
      }
      return { ...out, to: entry.to, target: data.models[entry.tm], kind: entry.k, notes };
    });
  }

  // ---------------------------------------------------------------- demo

  const SAMPLES = {
    python: `import anthropic
from openai import OpenAI

claude = anthropic.Anthropic()
router = OpenAI(base_url="https://openrouter.ai/api/v1")

# The planner needs claude-opus-5 for long tickets
PLANNER = "claude-opus-5"
CLASSIFIER = "claude-3-5-haiku-20241022"
BULK = "deepseek/deepseek-v4-flash"


def triage(ticket: str):
    plan = claude.messages.create(
        model=PLANNER,
        max_tokens=1024,
        messages=[{"role": "user", "content": ticket}],
    )
    return router.chat.completions.create(
        model="openai/gpt-5-mini",
        messages=[{"role": "user", "content": plan.content[0].text}],
    )
`,
    typescript: `import { generateText } from "ai";
import { anthropic } from "@ai-sdk/anthropic";
import { openai } from "@ai-sdk/openai";
import { bedrock } from "@ai-sdk/amazon-bedrock";

export const models = {
  chat: anthropic("claude-sonnet-5"),
  cheap: openai("gpt-5-mini"),
  enterprise: bedrock("us.anthropic.claude-sonnet-4-20250514-v1:0"),
};

// Exports still run on gpt-4o until the March review
export const exporter = openai("gpt-4o-2024-08-06");

export function supportsPdf(id: string) {
  return id === "claude-sonnet-5";
}
`,
    yaml: `# LiteLLM proxy
model_list:
  - model_name: smart
    litellm_params:
      model: anthropic/claude-opus-4-5-20251101
  - model_name: fast
    litellm_params:
      model: gemini/gemini-2.5-flash
  - model_name: open
    litellm_params:
      model: together_ai/Qwen/Qwen3-235B-A22B-Instruct-2507
  - model_name: grok
    litellm_params:
      model: xai/grok-4.3
`,
  };

  const input = $("#demo-input");
  const diffEl = $("#demo-diff");
  const prsEl = $("#demo-prs");
  const summaryEl = $("#demo-summary");

  function markLine(line, items, useNew) {
    let out = "";
    let pos = 0;
    for (const it of items) {
      out += esc(line.slice(pos, it.col));
      out += `<mark>${esc(useNew ? it.to : it.slug)}</mark>`;
      pos = it.col + it.slug.length;
    }
    return out + esc(line.slice(pos));
  }

  const row = (cls, no, sign, html) =>
    `<div class="dl ${cls}"><span class="no">${no}</span><span class="sign">${sign}</span><span class="text">${html}</span></div>`;

  function render() {
    if (!data) return;
    const text = input.value;
    const results = plan(text);
    const lines = text.replace(/\n$/, "").split("\n");
    const byLine = new Map();
    for (const r of results) {
      if (!byLine.has(r.line)) byLine.set(r.line, []);
      byLine.get(r.line).push(r);
    }
    const changes = results.filter((r) => r.to);
    summaryEl.textContent = results.length
      ? `${changes.length} of ${results.length} model reference${results.length === 1 ? "" : "s"} would change`
      : "Proposed changes";

    if (!text.trim() || !results.length) {
      diffEl.innerHTML = `<p class="empty">${text.trim() ? "No known model IDs found." : "Paste some code."} Try <code>claude-opus-5</code>, <code>"openai/gpt-5-mini"</code> or <code>gemini-2.5-flash</code>.</p>`;
      prsEl.innerHTML = "";
      return;
    }

    let html = "";
    lines.forEach((line, i) => {
      const items = byLine.get(i) || [];
      const changed = items.filter((r) => r.to);
      if (changed.length) {
        html += row("del", i + 1, "−", markLine(line, changed, false));
        html += row("add", "", "+", markLine(line, changed, true));
        for (const r of changed) {
          const head = `<b>${esc(r.kind)}</b> · ${esc(r.model.name)} → ${esc(r.target.name)} · ${esc(data.styles[r.entry.s] || r.entry.s)}`;
          html += row("note", "", "", head + r.notes.map((n) => `<br>${esc(n).replace(/`([^`]+)`/g, "<code>$1</code>")}`).join(""));
        }
      } else {
        html += row("ctx", i + 1, " ", esc(line) || " ");
      }
      for (const r of items.filter((x) => x.skip)) {
        html += row("note untouched", "", "", `<code>${esc(r.slug)}</code> ${esc(r.skip)}`);
      }
    });
    diffEl.innerHTML = html;
    renderPRs(changes);
  }

  function renderPRs(changes) {
    if (!changes.length) {
      prsEl.innerHTML = "";
      return;
    }
    const groups = new Map();
    for (const c of changes) {
      const key = c.kind === "snapshot" ? `${c.slug}>${c.to}` : `${c.entry.m}>${c.entry.tm}`;
      if (!groups.has(key)) groups.set(key, { ...c, count: 0 });
      groups.get(key).count++;
    }
    const icon = '<svg class="pr-icon" viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M1.5 3.25a2.25 2.25 0 1 1 3 2.12v5.26a2.25 2.25 0 1 1-1.5 0V5.37A2.25 2.25 0 0 1 1.5 3.25Zm5.677-.177L9.573.677A.25.25 0 0 1 10 .854V2.5h1A2.5 2.5 0 0 1 13.5 5v5.628a2.251 2.251 0 1 1-1.5 0V5a1 1 0 0 0-1-1h-1v1.646a.25.25 0 0 1-.427.177L7.177 3.427a.25.25 0 0 1 0-.354ZM3.75 2.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm0 9.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm8.25.75a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0Z"/></svg>';
    let html = `<h3>Pull requests ModelBump would open</h3>`;
    for (const g of groups.values()) {
      const title = g.kind === "snapshot" ? `Bump ${g.slug} to ${g.to}` : `Bump ${g.model.name} to ${g.target.name}`;
      const a = g.model.blended, b = g.target.blended;
      let price = "";
      if (a != null && b != null && g.target !== g.model) {
        const pct = Math.round((b / a - 1) * 100);
        const cls = pct < 0 ? "down" : pct > 0 ? "up" : "";
        price = ` · ${money(a)} → ${money(b)} per 1M tokens <span class="${cls}">(${pct > 0 ? "+" : ""}${pct}%)</span>`;
      }
      const status = g.model.status !== "active" ? `<span class="badge warn">${esc(g.model.status)}</span>` : `<span class="badge">${esc(g.kind)}</span>`;
      html += `<div class="pr">${icon}<div><div class="pr-title">${esc(title)}</div><div class="pr-meta">${g.count} reference${g.count === 1 ? "" : "s"}${price}</div></div>${status}</div>`;
    }
    prsEl.innerHTML = html;
  }

  function loadSample(name) {
    input.value = SAMPLES[name];
    render();
  }

  wireTabs($(".pane-input .tabs"), (tab) => loadSample(tab.dataset.sample));
  $("#demo-clear").addEventListener("click", () => {
    input.value = "";
    render();
    input.focus();
  });
  let pending = 0;
  input.addEventListener("input", () => {
    cancelAnimationFrame(pending);
    pending = requestAnimationFrame(render);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Tab" && !e.shiftKey && !e.altKey) {
      e.preventDefault();
      const { selectionStart: s, selectionEnd: t } = input;
      input.setRangeText("    ", s, t, "end");
      render();
    }
  });
  input.value = SAMPLES.python;

  // ---------------------------------------------------------------- ecosystems

  const ECO_NAMES = { "chinese-open": "Chinese open-weight labs" };

  function renderEcosystems() {
    const el = $("#ecosystems");
    const vendors = new Map(data.vendors.map((v) => [v.key, v]));
    const chips = (list) => list.map((v) => `<li>${esc(v.name)}<span>${v.models}</span></li>`).join("");
    const solo = [];
    let groups = "";
    for (const [eco, members] of Object.entries(data.ecosystems)) {
      const list = members.map((k) => vendors.get(k)).filter((v) => v && v.models);
      if (list.length === 1) solo.push(list[0]);
      else if (list.length > 1) {
        groups += `<div class="eco"><h4>${esc(ECO_NAMES[eco] || eco)} · can swap with each other</h4><ul>${chips(list)}</ul></div>`;
      }
    }
    el.innerHTML = `<div class="eco"><h4>Stay with their own lab</h4><ul>${chips(solo)}</ul></div>${groups}`;
  }

  // ---------------------------------------------------------------- setup builder

  const form = $("#settings");

  function settings() {
    const fd = new FormData(form);
    return {
      strategy: fd.get("strategy"),
      updates: fd.getAll("update"),
      schedule: fd.get("schedule"),
      grouping: fd.get("grouping"),
      prerelease: fd.get("prerelease") === "on",
    };
  }

  function workflowYaml(s) {
    const cron = s.schedule === "daily" ? "0 6 * * *" : "0 6 * * 1";
    return `# Opens pull requests when newer versions of hard-coded LLM models ship.
# ${SITE}/
name: ModelBump

on:
  schedule:
    - cron: "${cron}"
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write

jobs:
  modelbump:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: ${ACTION}
        env:
          # Optional. Benchmark scores for strategy: cost-optimized.
          ARTIFICIAL_ANALYSIS_API_KEY: \${{ secrets.ARTIFICIAL_ANALYSIS_API_KEY }}
`;
  }

  function configYaml(s) {
    let y = `# ModelBump settings: ${SITE}/#configure
version: 1

# same-family: newer release of the same model line (Opus -> newer Opus)
# cost-optimized: same benchmark score or better, lower price, same ecosystem
strategy: ${s.strategy}

same-family:
  update-types: [${s.updates.join(", ")}]
`;
    if (s.prerelease) y += "  allow-prerelease: true\n";
    if (s.strategy === "cost-optimized") {
      y += `
# Needs benchmark scores: add the ARTIFICIAL_ANALYSIS_API_KEY secret, or list
# your own scores under catalog.scores.
cost-optimized:
  max-score-drop: 0.02
  min-savings: 0.10
`;
      if (s.prerelease) y += "  allow-prerelease: true\n";
    }
    if (s.grouping === "single") y += "\npull-requests:\n  grouping: single\n";
    y += `
ignore: []
#  - model: "gpt-4o*"        # never touch these slugs
#  - paths: ["legacy/**"]
`;
    return y;
  }

  function highlight(yaml) {
    return yaml
      .split("\n")
      .map((l) => {
        const hash = l.search(/(^|\s)#/);
        const code = hash === -1 ? l : l.slice(0, hash);
        const comment = hash === -1 ? "" : l.slice(hash);
        const m = code.match(/^(\s*-?\s*)([\w.-]+)(:)(.*)$/);
        const body = m ? `${esc(m[1])}<span class="k">${esc(m[2])}</span>${m[3]}<span class="v">${esc(m[4])}</span>` : esc(code);
        return body + (comment ? `<span class="c">${esc(comment)}</span>` : "");
      })
      .join("\n");
  }

  function scriptCommand(s) {
    const args = [];
    if (s.strategy !== "same-family") args.push("--strategy", s.strategy);
    if (s.schedule !== "weekly") args.push("--schedule", s.schedule);
    if (s.updates.join(",") !== "snapshot,minor,major") args.push("--updates", s.updates.join(","));
    if (s.grouping !== "per-model") args.push("--grouping", s.grouping);
    if (s.prerelease) args.push("--prerelease");
    return `curl -fsSL ${SITE}/install.sh | bash${args.length ? " -s -- " + args.join(" ") : ""}`;
  }

  function parseRepo(value) {
    const v = value.trim().replace(/\.git$/, "").replace(/\/+$/, "");
    const m = v.match(/^(?:https?:\/\/github\.com\/|git@github\.com:)?([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)$/);
    return m ? `${m[1]}/${m[2]}` : null;
  }

  function updateSetup() {
    const s = settings();
    const boxes = $$('input[name="update"]', form);
    boxes.forEach((b) => (b.disabled = s.updates.length === 1 && b.checked));

    const wf = workflowYaml(s);
    const cfg = configYaml(s);
    $("#workflow-yaml").innerHTML = highlight(wf.trimEnd());
    $("#config-yaml").innerHTML = highlight(cfg.trimEnd());
    $("#workflow-yaml").dataset.raw = wf;

    const cmd = scriptCommand(s);
    const btn = $("#script-command");
    btn.dataset.copy = cmd;
    $(".command-text", btn).textContent = cmd;

    const raw = $("#repo").value;
    const repo = parseRepo(raw);
    const branch = ($("#branch").value.trim() || "main").split("/").map(encodeURIComponent).join("/");
    $("#repo-error").hidden = !raw.trim() || !!repo;
    const links = {
      "open-workflow": repo && `https://github.com/${repo}/new/${branch}/.github/workflows?filename=modelbump.yml&value=${encodeURIComponent(wf)}`,
      "open-config": repo && `https://github.com/${repo}/new/${branch}/.github?filename=modelbump.yml&value=${encodeURIComponent(cfg)}`,
      "open-settings": repo && `https://github.com/${repo}/settings/actions`,
    };
    for (const [id, href] of Object.entries(links)) {
      const a = $("#" + id);
      a.href = href || "#";
      a.setAttribute("aria-disabled", String(!href));
      if (href) {
        a.target = "_blank";
        a.rel = "noopener";
      }
    }
    try {
      localStorage.setItem("modelbump.repo", raw);
    } catch {}
  }

  // The copy buttons in "Copy files" should copy raw YAML, not highlighted HTML.
  document.addEventListener(
    "click",
    (e) => {
      const b = e.target.closest("[data-copy-from]");
      if (!b) return;
      e.stopImmediatePropagation();
      const s = settings();
      copy(b.dataset.copyFrom === "workflow-yaml" ? workflowYaml(s) : configYaml(s), b);
    },
    true,
  );

  try {
    $("#repo").value = localStorage.getItem("modelbump.repo") || "";
  } catch {}
  form.addEventListener("change", updateSetup);
  $("#repo").addEventListener("input", updateSetup);
  $("#branch").addEventListener("input", updateSetup);
  wireTabs($(".method-tabs"));
  updateSetup();

  // ---------------------------------------------------------------- data

  fetch("data/catalog.json")
    .then((r) => {
      if (!r.ok) throw new Error(`catalog.json: HTTP ${r.status}`);
      return r.json();
    })
    .then((d) => {
      data = d;
      matchers = buildMatchers(Object.keys(d.slugs));
      const date = d.generated.slice(0, 10);
      $("#hero-meta").textContent = `${d.stats.models} models from ${d.stats.vendors} labs in today's catalog · updated ${date}`;
      $("#footer-version").textContent = `v${d.version}`;
      $("#footer-data").textContent = `Model data from OpenRouter and vendor documentation, refreshed ${date}.`;
      renderEcosystems();
      render();
      startHero(d.hero);
    })
    .catch((err) => {
      diffEl.innerHTML = `<p class="empty">The model catalog didn't load (${esc(String(err.message || err))}). Reload the page to try again.</p>`;
      startHero([["claude-opus-5", "claude-opus-5-5"]]);
    });
})();
