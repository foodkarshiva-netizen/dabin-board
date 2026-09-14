// HTML 카드 → PNG 렌더러. 사용: node render_card.js jobs.json
// jobs.json: [{"html": "...", "width": 1200, "height": 630, "out": "/path/a.png"}, ...]
const fs = require("fs");
const path = require("path");

function loadPlaywright() {
  try { return require("playwright"); } catch (e) {}
  const globalRoot = require("child_process").execSync("npm root -g").toString().trim();
  return require(path.join(globalRoot, "playwright"));
}

(async () => {
  const jobs = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const { chromium } = loadPlaywright();
  const browser = await chromium.launch();
  try {
    for (const job of jobs) {
      const page = await browser.newPage({ viewport: { width: job.width, height: job.height }, deviceScaleFactor: 1 });
      await page.setContent(job.html, { waitUntil: "load" });
      await page.evaluate(() => document.fonts.ready);
      // job.height 는 최소 높이. 내용이 더 길면 잘리지 않도록 뷰포트를 늘린다.
      const need = await page.evaluate(() => {
        const c = document.querySelector(".card");
        return c ? Math.ceil(c.scrollHeight) : 0;
      });
      if (need > job.height) await page.setViewportSize({ width: job.width, height: need });
      await page.screenshot({ path: job.out, type: "png", fullPage: false });
      await page.close();
      console.log("rendered", job.out);
    }
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
