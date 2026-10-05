// HTML 애니메이션 → 프레임 이미지. 사용: node render_frames.js job.json
// job.json: {"html": "...", "width": 1080, "height": 1920, "fps": 30, "duration": 24.5, "outDir": "...", "quality": 90}
// 페이지는 window.__setT(초) 를 제공해야 한다. 시간을 직접 지정해 찍으므로 프레임이 끊기지 않는다.
const fs = require("fs");
const path = require("path");

function loadPlaywright() {
  try { return require("playwright"); } catch (e) {}
  const globalRoot = require("child_process").execSync("npm root -g").toString().trim();
  return require(path.join(globalRoot, "playwright"));
}

(async () => {
  const job = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const { chromium } = loadPlaywright();
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: job.width, height: job.height }, deviceScaleFactor: 1 });
    await page.setContent(job.html, { waitUntil: "load" });
    await page.evaluate(() => document.fonts.ready);
    fs.mkdirSync(job.outDir, { recursive: true });
    const n = Math.round(job.duration * job.fps);
    for (let i = 0; i < n; i++) {
      await page.evaluate((t) => window.__setT(t), i / job.fps);
      await page.screenshot({ path: path.join(job.outDir, `f_${String(i).padStart(5, "0")}.jpg`), type: "jpeg", quality: job.quality || 90 });
    }
    console.log("frames", n);
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
