const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto("http://127.0.0.1:58090/webui/", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2000);
  const out = await page.evaluate(() => {
    const icons = Array.from(document.querySelectorAll(".control-button .material-icons-outlined, .control-button .material-icons")).slice(0, 6);
    return {
      icons: icons.map((icon) => {
        const style = getComputedStyle(icon);
        return {
          text: icon.textContent || "",
          className: icon.className,
          fontFamily: style.fontFamily,
          width: icon.getBoundingClientRect().width,
          visibility: style.visibility,
        };
      }),
    };
  });
  console.log(JSON.stringify(out));
  await browser.close();
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
