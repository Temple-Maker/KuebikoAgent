// In-page helpers for kuebiko scan. Injected after axe-core; defines window.__kuebiko.
(() => {
  const TABBABLE = 'a[href],area[href],button,input:not([type=hidden]),select,textarea,iframe,summary,[tabindex],[contenteditable=""],[contenteditable=true]';
  // Content that may scroll in two dimensions under WCAG 1.4.10.
  const TWO_D = 'table,pre,code,img,video,canvas,svg,iframe,object,embed,math,[role=img],[role=application],[role=grid]';

  const unique = (sel) => document.querySelectorAll(sel).length === 1;

  // Deterministic CSS selector: nearest unique id, then a child path with nth-of-type.
  function selectorOf(el) {
    const parts = [];
    for (; el && el.nodeType === 1; el = el.parentElement) {
      if (el.id && unique('#' + CSS.escape(el.id))) { parts.unshift('#' + CSS.escape(el.id)); break; }
      if (el === document.body || el === document.documentElement) { parts.unshift(el.localName); break; }
      const same = [...el.parentElement.children].filter((s) => s.localName === el.localName);
      parts.unshift(same.length > 1 ? `${el.localName}:nth-of-type(${same.indexOf(el) + 1})` : el.localName);
    }
    return parts.join(' > ');
  }

  const openingTag = (el) => el.outerHTML.slice(0, 2000);

  function find(sel) {
    try { return document.querySelector(sel); } catch { return null; }
  }

  function describe(el) {
    if (!el || el === document.body || el === document.documentElement) return null;
    const r = el.getBoundingClientRect();
    return {
      selector: selectorOf(el), html: openingTag(el), tag: el.localName,
      rect: { x: r.x, y: r.y, width: r.width, height: r.height },
      viewport: { width: innerWidth, height: innerHeight },
    };
  }

  window.__kuebiko = {
    selectorOf,

    // Rewrite axe targets into our selectors so every check names a place the same way.
    ownSelector(target) {
      const el = target.length === 1 && typeof target[0] === 'string' ? find(target[0]) : null;
      return el ? selectorOf(el) : null;
    },

    active: () => describe(document.activeElement),

    tabbableCount: () => [...document.querySelectorAll(TABBABLE)]
      .filter((el) => el.tabIndex >= 0 && !el.disabled && el.checkVisibility()).length,

    // Walk the page with Guidepup's virtual screen reader. Returns every phrase, and
    // for each selector the phrase spoken when the cursor reached that element.
    async srWalk(source, selectors, maxSteps) {
      const url = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
      const { virtual } = await import(url);
      URL.revokeObjectURL(url);
      const log = [];
      const nodes = [];
      await virtual.start({ container: document.body, displayCursor: false });
      try {
        for (let i = 0; i < maxSteps; i++) {
          const phrase = await virtual.lastSpokenPhrase();
          log.push(phrase);
          nodes.push(virtual.activeNode);
          if (phrase === 'end of document') break;
          await virtual.next();
        }
      } finally {
        await virtual.stop();
      }
      const bySelector = {};
      for (const sel of selectors) {
        const el = find(sel);
        if (!el) continue;
        let i = nodes.indexOf(el);
        if (i < 0) i = nodes.findIndex((n) => n && el.contains(n));
        if (i >= 0) bySelector[sel] = log[i];
      }
      return { log, bySelector };
    },

    // Outermost visible elements that stick out past the viewport's right edge.
    overflowing(limit) {
      const W = document.documentElement.clientWidth;
      if (document.documentElement.scrollWidth <= W + 1) return [];
      const out = [];
      for (const el of [document.body, ...document.body.querySelectorAll('*')]) {
        if (out.length >= limit) break;
        if (el.closest(TWO_D) || !el.checkVisibility()) continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.right <= W + 1) continue;
        if (el.parentElement.getBoundingClientRect().right > W + 1 && el !== document.body) continue;
        // body's overflow belongs to the viewport, so only containers below it count.
        const root = (p) => !p || p === document.body || p === document.documentElement;
        let p = el.parentElement;
        while (!root(p) && !['auto', 'scroll'].includes(getComputedStyle(p).overflowX)) p = p.parentElement;
        if (!root(p)) continue;  // it scrolls inside its own container
        out.push({ selector: selectorOf(el), html: openingTag(el), right: r.right, viewport: W });
      }
      return out;
    },

    // Visible containers that cut off their own text.
    clipped() {
      const out = {};
      for (const el of document.body.querySelectorAll('*')) {
        if (el.clientWidth <= 1 || el.clientHeight <= 1 || !el.checkVisibility() || !el.innerText.trim()) continue;
        const s = getComputedStyle(el);
        const clipX = ['hidden', 'clip'].includes(s.overflowX) && el.scrollWidth > el.clientWidth + 1;
        const clipY = ['hidden', 'clip'].includes(s.overflowY) && el.scrollHeight > el.clientHeight + 1;
        if (clipX || clipY) {
          out[selectorOf(el)] = {
            html: openingTag(el),
            client: [el.clientWidth, el.clientHeight], scroll: [el.scrollWidth, el.scrollHeight],
          };
        }
      }
      return out;
    },
  };
})();
