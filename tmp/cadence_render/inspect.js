const { chromium } = require('playwright');
const path = require('path');
(async() => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
  const url = 'file:///' + path.resolve('index.html').replace(/\\/g, '/');
  await page.goto(url, { waitUntil: 'load' });
  await page.screenshot({ path: 'hero-full.png', fullPage: false });
  const data = await page.evaluate(() => {
    const root = document.querySelector('.container--0- > .container-0-1-0');
    const hero = root?.children?.[0];
    const info = [];
    function describe(el, label) {
      if (!el) return null;
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return {
        label,
        tag: el.tagName,
        className: el.className,
        rect: { x: r.x, y: r.y, width: r.width, height: r.height },
        background: s.background,
        backgroundColor: s.backgroundColor,
        backgroundImage: s.backgroundImage,
        filter: s.filter,
        backdropFilter: s.backdropFilter,
        opacity: s.opacity,
        boxShadow: s.boxShadow,
        borderRadius: s.borderRadius,
        mixBlendMode: s.mixBlendMode,
      };
    }
    if (hero) {
      info.push(describe(hero, 'hero-child-1'));
      Array.from(hero.children).forEach((child, i) => info.push(describe(child, `hero-child-1-${i+1}`)));
      const firstInner = hero.children[0];
      if (firstInner) Array.from(firstInner.children).forEach((child, i) => info.push(describe(child, `hero-inner-${i+1}`)));
    }
    return info;
  });
  console.log(JSON.stringify(data, null, 2));
  await browser.close();
})();
