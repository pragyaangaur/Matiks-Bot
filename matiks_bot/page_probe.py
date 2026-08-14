"""JavaScript injected into the Matiks page to read game state.

Everything the bot knows about the page comes from here. It is written as a
heuristic scan rather than a list of pinned CSS selectors on purpose: Matiks is
a live app, and class names in a bundled frontend change on every deploy. A
scan that looks for "the biggest visible chunk of text that parses as
arithmetic" survives a redesign; `.css-1x7fk2q > span` does not.
"""

READ_STATE_JS = r"""
(config) => {
  // The trailing \d*%\ alternative catches "25% of 80", where the operator is
  // followed by a word rather than a digit. MATH_ONLY below is the strict
  // gate, so this hint can afford to be generous.
  const MATH_HINT = /\d\s*(?:[-+*\/^%]|×|÷|−|⋅|x)\s*\d|√\s*\d|\d+\s*!|\d\s*%/;
  // Scoreboards read as arithmetic ("Final score 21 - 18", "Score 12 - 9").
  // Without this the bot answers the scoreboard instead of clicking Play Again.
  const NOT_A_QUESTION = /\b(score|final|rating|elo|points?|streak|prev|previous|best|record|round|rank|level|xp|wins?|losses?|correct|accuracy)\b/i;
  const CLOCK = /\b\d{1,2}:\d{2}\b/;
  // "1/6" is question-1-of-6, not a division problem. A real division question
  // is rendered in the prompt's large type; a counter is small. Size is what
  // separates them, so this only rejects a bare small fraction in small text.
  const COUNTER = /^\d{1,3}\s*\/\s*\d{1,3}$/;
  const minFont = config.minQuestionFontPx || 22;

  // The decisive filter. Requiring text to merely *contain* something
  // math-shaped let "Daily ChallengesComplete to earn rewards1/6" through on
  // the home screen. A prompt is math and nothing else, so after removing the
  // few wrapper words a question may carry, every remaining character must
  // belong to an expression.
  const WRAPPER_WORDS = /\b(what|is|solve|calculate|answer|the|value|of)\b/gi;
  const MATH_ONLY = /^[\d\s+\-*\/^%().,×÷−–—⋅·✕✖∕⁄√!²³⁰¹⁴⁵⁶⁷⁸⁹=?xX]+$/;

  function visible(el) {
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    if (parseFloat(style.opacity || '1') < 0.1) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return false;
    if (rect.bottom < 0 || rect.top > window.innerHeight) return false;
    return true;
  }

  function text(el) {
    return (el.textContent || '').trim().replace(/\s+/g, ' ');
  }

  // React Native Web nests text several layers deep, and the wrapper that
  // holds the whole expression usually inherits a default 16px while the
  // digits inside are 56px. Size the candidate by the largest font it
  // actually renders, not by its own computed style.
  function renderedFontSize(el) {
    let max = parseFloat(window.getComputedStyle(el).fontSize) || 0;
    for (const kid of el.querySelectorAll('*')) {
      const size = parseFloat(window.getComputedStyle(kid).fontSize) || 0;
      if (size > max) max = size;
    }
    return max;
  }

  const all = Array.from(document.querySelectorAll('body *'));

  // --- question ---------------------------------------------------------
  // Collect every element whose *full* text is a short arithmetic expression,
  // then keep the innermost one. A child holding only "47" fails the operator
  // test, and an outer wrapper holding the whole page fails the length test,
  // so the survivor with the fewest descendants is the tight box around the
  // prompt — regardless of how many layers the framework wrapped it in.
  const candidates = [];
  for (const el of all) {
    if (!visible(el)) continue;
    const t = text(el);
    if (!t || t.length > 60) continue;
    if (!MATH_HINT.test(t)) continue;
    if (NOT_A_QUESTION.test(t) || CLOCK.test(t)) continue;
    if (!MATH_ONLY.test(t.replace(WRAPPER_WORDS, ' ').trim())) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width > window.innerWidth * 0.95 && rect.height > window.innerHeight * 0.5) continue;
    const size = renderedFontSize(el);
    if (size < minFont) continue;
    if (COUNTER.test(t) && size < minFont * 1.6) continue;
    candidates.push({
      text: t,
      fontSize: size,
      top: rect.top,
      depth: el.querySelectorAll('*').length,
    });
  }
  // Fewest descendants first (innermost complete match), largest type as the
  // tie-break when several nodes wrap exactly the same text.
  candidates.sort((a, b) => a.depth - b.depth || b.fontSize - a.fontSize);
  const question = candidates.length ? candidates[0] : null;

  // --- prompt tokens ----------------------------------------------------
  // The prompt's digits are SVG outlines, so they appear in no textContent at
  // all; only the operator stays as text. Walk the document once and emit both
  // kinds in DOM order, which for this layout is exactly reading order.
  //
  // Deliberately no geometry filtering here: an earlier version dropped
  // glyphs whose boxes measured small and silently turned "46 + 55" into
  // "46 + 5". Position is not needed — order is — and unknown paths are
  // discarded later by the digit table, which is what excludes icons.
  const tokens = [];
  for (const el of all) {
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    if (parseFloat(style.opacity || '1') < 0.1) continue;

    if (el.tagName.toLowerCase() === 'path') {
      const d = el.getAttribute('d');
      if (d) tokens.push({kind: 'glyph', d});
      continue;
    }
    if (el.children.length) continue;
    const t = text(el);
    if (/^[+\-−–—×÷*\/]$/.test(t)) tokens.push({kind: 'op', text: t});
  }

  // --- answer field -----------------------------------------------------
  let field = null;
  const active = document.activeElement;
  const isField = (el) => el && (
    el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable
  );
  if (isField(active) && visible(active)) {
    field = { focused: true, value: active.value !== undefined ? active.value : active.textContent };
  } else {
    for (const el of all) {
      if (!isField(el) || !visible(el)) continue;
      if (el.type === 'password' || el.type === 'checkbox') continue;
      field = { focused: false, value: el.value !== undefined ? el.value : el.textContent };
      break;
    }
  }

  // --- buttons and phase ------------------------------------------------
  // Matiks is React Native Web: the real controls are <div>s with
  // cursor:pointer, not <button>/<a>. Scanning only for semantic tags finds
  // nothing on this app, so treat cursor:pointer as the signal.
  const clickable = [];
  for (const el of all) {
    if (!visible(el)) continue;
    const tag = el.tagName;
    const role = el.getAttribute('role');
    const pointer = window.getComputedStyle(el).cursor === 'pointer';
    if (tag !== 'BUTTON' && tag !== 'A' && role !== 'button' && !pointer) continue;
    const label = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
    if (label && label.length <= 60 && !clickable.includes(label)) clickable.push(label);
  }

  const bodyText = (document.body.innerText || '').toLowerCase();
  const hasAny = (needles) => needles.some((n) => bodyText.includes(n.toLowerCase()));

  // Only built on demand: sizing every node is O(n^2) and this runs in a
  // polling loop. Shows the biggest text on screen so a missed question can
  // be diagnosed from the log instead of guessed at.
  let debugTexts = null;
  if (config.debug) {
    const seen = new Set();
    debugTexts = [];
    for (const el of all) {
      if (!visible(el) || el.children.length) continue;
      const t = text(el);
      if (!t || t.length > 60 || seen.has(t)) continue;
      seen.add(t);
      debugTexts.push({text: t, size: Math.round(renderedFontSize(el))});
    }
    debugTexts.sort((a, b) => b.size - a.size);
    debugTexts = debugTexts.slice(0, 12);
  }

  return {
    url: location.href,
    // Cheap per-poll signal: the duel timer lives in here, and capture
    // gating needs it on every tick, not just on debug ticks.
    bodySample: bodyText.slice(0, 400),
    tokens,
    question,
    candidates: candidates.slice(0, 5),
    debugTexts,
    field,
    buttons: clickable,
    gameOver: hasAny(config.gameOverText || []),
    inGame: !!question,
    waiting: hasAny(config.waitingText || []),
  };
}
"""
