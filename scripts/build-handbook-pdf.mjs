#!/usr/bin/env node
/**
 * Render the Hinglish handbook to PDF.
 *
 * The handbook is Markdown with a few HTML affordances the plain renderers do
 * not handle - a cover page, coloured callouts, explicit page breaks, a [TOC]
 * marker, and sixteen Mermaid diagrams. So this renders it the same way a
 * browser would and prints the result, rather than trying to find a Markdown
 * converter that understands all of that.
 *
 * It deliberately reuses Chromium from frontend/node_modules (Playwright is
 * already a dev dependency for the E2E journeys) instead of adding a
 * documentation toolchain. markdown-it and Mermaid come from a CDN at build
 * time, so nothing new is installed either.
 *
 * Usage, from the repository root:
 *   node scripts/build-handbook-pdf.mjs
 *
 * Requires network access for the two CDN modules. Output overwrites the .pdf
 * beside the .md.
 */

import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..");
const handbookDir = join(repoRoot, "docs", "handbook");
const baseName = "ENTERPRISE_KNOWLEDGE_AUTOMATION_COMPLETE_HANDBOOK";

// Playwright lives in the frontend workspace, not the repo root.
const requireFromFrontend = createRequire(join(repoRoot, "frontend", "package.json"));
let chromium;
try {
  ({ chromium } = requireFromFrontend("playwright"));
} catch {
  console.error(
    "Playwright not found. Run `npm install` in frontend/, then\n" +
      "`npx playwright install chromium`, and try again.",
  );
  process.exit(1);
}

const markdown = await readFile(join(handbookDir, `${baseName}.md`), "utf8");

const css = String.raw`
  @page { size: A4; margin: 16mm 14mm 18mm; }
  @page :first { margin: 0; }

  :root {
    --ink: #17202a;
    --muted: #5b6b7c;
    --rule: #dde4ea;
    --brand: #0b5cab;
    --code-bg: #f4f6f8;
  }

  * { box-sizing: border-box; }
  body {
    margin: 0;
    font: 10.5pt/1.55 "Segoe UI", "Noto Sans", system-ui, sans-serif;
    color: var(--ink);
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }

  h1, h2, h3, h4 { line-height: 1.25; margin: 1.25em 0 0.5em; page-break-after: avoid; }
  h1 { font-size: 20pt; color: var(--brand); border-bottom: 2px solid var(--brand); padding-bottom: 0.25em; page-break-before: auto; }
  h2 { font-size: 14pt; }
  h3 { font-size: 11.5pt; }
  p, ul, ol { margin: 0.5em 0; }
  li { margin: 0.2em 0; }
  a { color: var(--brand); text-decoration: none; }
  strong { color: #0d1620; }

  code {
    font: 9pt/1.4 "Cascadia Mono", Consolas, "Courier New", monospace;
    background: var(--code-bg);
    padding: 0.1em 0.3em;
    border-radius: 3px;
  }
  pre {
    background: var(--code-bg);
    border: 1px solid var(--rule);
    border-left: 3px solid var(--brand);
    border-radius: 4px;
    padding: 0.7em 0.9em;
    overflow-wrap: break-word;
    white-space: pre-wrap;
    page-break-inside: avoid;
  }
  pre code { background: none; padding: 0; font-size: 8.5pt; }

  table {
    width: 100%;
    border-collapse: collapse;
    margin: 0.8em 0;
    font-size: 9pt;
    page-break-inside: avoid;
  }
  th, td { border: 1px solid var(--rule); padding: 0.4em 0.55em; text-align: left; vertical-align: top; }
  th { background: #eef3f8; font-weight: 600; }
  tbody tr:nth-child(even) { background: #fafcfd; }

  blockquote { margin: 0.8em 0; padding: 0.1em 0 0.1em 0.9em; border-left: 3px solid var(--rule); color: var(--muted); }

  /* ---- the handbook's own HTML affordances ------------------------------ */
  .pagebreak { page-break-after: always; break-after: page; }

  /* The cover and the contents page already end a page. The handbook puts an
     explicit .pagebreak after each of them, which would otherwise leave a
     blank sheet between them. */
  .cover + .pagebreak,
  #toc + .pagebreak { page-break-after: auto; break-after: auto; }

  .callout {
    margin: 0.9em 0;
    padding: 0.7em 0.9em;
    border-radius: 5px;
    border-left: 4px solid;
    page-break-inside: avoid;
  }
  .callout > :first-child { margin-top: 0; }
  .callout > :last-child { margin-bottom: 0; }
  .callout.info   { background: #eef5fc; border-color: #2a72c8; }
  .callout.warn   { background: #fdf6e6; border-color: #d49a1a; }
  .callout.danger { background: #fdeeee; border-color: #c8342a; }
  .callout.ok     { background: #edf8f1; border-color: #248a52; }

  .cover {
    height: 297mm;
    padding: 45mm 24mm 24mm;
    background: linear-gradient(150deg, #0b2b52 0%, #0b5cab 55%, #1183c9 100%);
    color: #fff;
    page-break-after: always;
  }
  .cover .title { font-size: 30pt; font-weight: 700; line-height: 1.15; margin: 0 0 0.45em; }
  .cover .subtitle2 { font-size: 15pt; font-weight: 600; margin: 0 0 1.6em; color: #cfe6f8; }
  .cover .subtitle { font-size: 11.5pt; line-height: 1.6; margin: 0 0 2.4em; color: #e6f1fa; max-width: 60ch; }
  .cover .meta { font-size: 9.5pt; color: #bcd9ef; margin: 0; }
  .cover code { background: rgba(255, 255, 255, 0.14); color: #fff; }

  /* ---- generated table of contents ------------------------------------- */
  #toc { page-break-after: always; }
  #toc h2 { color: var(--brand); margin-top: 0; }
  #toc ol { list-style: none; padding-left: 0; margin: 0; font-size: 9.5pt; }
  #toc ol ol { padding-left: 1.4em; color: var(--muted); }
  #toc li { margin: 0.12em 0; }

  .mermaid { margin: 0.9em 0; text-align: center; page-break-inside: avoid; }
  .mermaid svg { max-width: 100%; height: auto; }
`;

const page_script = String.raw`
  import markdownIt from "https://esm.sh/markdown-it@14";
  import mermaid from "https://esm.sh/mermaid@11";

  const md = markdownIt({ html: true, linkify: true, typographer: false });
  const raw = JSON.parse(document.getElementById("source").textContent);

  /*
   * The handbook marks its callouts with markdown="1", a python-markdown
   * extension meaning "process the Markdown inside this div". CommonMark has no
   * such thing: it treats everything from <div> to the next blank line as raw
   * HTML, so a callout's **bold** and \`code\` would print literally.
   *
   * Blank lines are all CommonMark needs to close the HTML block, so insert
   * them around the tags. The div then passes through as HTML and its contents
   * are parsed as Markdown, which is exactly what markdown="1" asks for.
   */
  const source = raw
    .replace(/^(<div\b[^>]*\bmarkdown="1">)[ \t]*$/gm, "$1\n")
    .replace(/^<\/div>[ \t]*$/gm, "\n</div>");

  // Mermaid fences must survive Markdown rendering as divs, not <pre><code>.
  md.renderer.rules.fence = (tokens, idx, opts, env, self) => {
    const token = tokens[idx];
    if ((token.info || "").trim() === "mermaid") {
      return '<div class="mermaid">' + md.utils.escapeHtml(token.content) + "</div>";
    }
    return self.renderToken(tokens, idx, opts);
  };

  document.getElementById("doc").innerHTML = md.render(source);

  // Anchor every heading so the table of contents can link to it.
  const slugs = new Map();
  const headings = [...document.querySelectorAll("#doc h1, #doc h2")];
  for (const h of headings) {
    let slug = (h.textContent || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
    const seen = slugs.get(slug) ?? 0;
    slugs.set(slug, seen + 1);
    h.id = seen ? slug + "-" + seen : slug;
  }

  // Replace the literal [TOC] marker the handbook uses.
  const marker = [...document.querySelectorAll("#doc p")].find(
    (p) => p.textContent.trim() === "[TOC]",
  );
  if (marker) {
    const outer = document.createElement("nav");
    outer.id = "toc";
    const list = document.createElement("ol");
    let sub = null;
    for (const h of headings) {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.href = "#" + h.id;
      link.textContent = h.textContent;
      item.append(link);
      if (h.tagName === "H1") {
        list.append(item);
        sub = null;
      } else {
        if (!sub) {
          sub = document.createElement("ol");
          (list.lastElementChild ?? list).append(sub);
        }
        sub.append(item);
      }
    }
    outer.innerHTML = "<h2>Contents</h2>";
    outer.append(list);
    marker.replaceWith(outer);
  }

  mermaid.initialize({ startOnLoad: false, theme: "neutral", securityLevel: "loose" });
  await mermaid.run({ querySelector: ".mermaid" });

  window.__handbookReady = true;
`;

const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>${baseName}</title>
<style>${css}</style>
</head><body>
<script type="application/json" id="source">${JSON.stringify(markdown).replace(/<\//g, "<\\/")}</script>
<div id="doc"></div>
<script type="module">${page_script}</script>
</body></html>`;

/**
 * Playwright's bundled Chromium is preferred because it is pinned, but a
 * machine that has only run the backend tests will not have downloaded it. The
 * system browser prints identically, so fall back to it rather than making the
 * operator install a browser to rebuild a PDF.
 */
async function launchBrowser() {
  const attempts = [
    ["Playwright Chromium", {}],
    ["system Chrome", { channel: "chrome" }],
    ["system Edge", { channel: "msedge" }],
  ];
  const failures = [];
  for (const [label, options] of attempts) {
    try {
      const browser = await chromium.launch(options);
      console.log(`Using ${label}.`);
      return browser;
    } catch (err) {
      failures.push(`${label}: ${err.message.split("\n")[0]}`);
    }
  }
  console.error("No usable Chromium found.\n  " + failures.join("\n  "));
  console.error("\nInstall one with `npx playwright install chromium` in frontend/.");
  process.exit(1);
}

const browser = await launchBrowser();
const page = await browser.newPage();

page.on("pageerror", (err) => console.error("page error:", err.message));

await page.setContent(html, { waitUntil: "load" });

console.log("Rendering Markdown and Mermaid diagrams…");
try {
  await page.waitForFunction("window.__handbookReady === true", { timeout: 180_000 });
} catch {
  await browser.close();
  console.error(
    "Rendering did not finish. The CDN modules (markdown-it, mermaid) need\n" +
      "network access - check connectivity and retry.",
  );
  process.exit(1);
}

const outPath = join(handbookDir, `${baseName}.pdf`);
await page.pdf({
  path: outPath,
  format: "A4",
  printBackground: true,
  displayHeaderFooter: true,
  headerTemplate: "<div></div>",
  footerTemplate:
    '<div style="width:100%;font:8pt \'Segoe UI\',sans-serif;color:#5b6b7c;' +
    'padding:0 14mm;display:flex;justify-content:space-between">' +
    "<span>Enterprise Knowledge Based Automation — Handbook</span>" +
    '<span class="pageNumber"></span></div>',
  margin: { top: "16mm", bottom: "18mm", left: "14mm", right: "14mm" },
});

await browser.close();
console.log(`Wrote ${outPath}`);
