/* nkProbe: one read of the Naukri page + chat drawer.

   Shared by the bot's chat engine (naukri_bot/chatdrawer, Selenium execute_script) and the
   failure watcher (naukri_watch, CDP Runtime.evaluate), so the actor and the observer judge
   the SAME model of the screen.

   Read-only, except one passive MutationObserver that timestamps chat-drawer changes
   (drawer.quietMs / drawer.mutN), so callers can wait for "drawer settled" instead of sleeping.

     (SRC)({})                   -> JSON string of the model               (watcher)
     (SRC)({withElements: true}) -> {json: <model JSON>, els: [DOM nodes]} (bot; every "ref" indexes els)
     (SRC)({withHtml: true})     -> adds drawer.html (trimmed outerHTML)    (format sampling)

   Naukri drawer markup (sampled live 2026-09-15):
     div.chatbot_Drawer > div.chatbot_Nav (span.chatBot-ic-cross)
       > div.chatbot_MessageContainer > li.botItem (div.botMsg) | li.userItem (div.userMsg) | li.botItem.loader
       > div.singleselect-radiobutton-container > div.ssrc__radio-btn-container > input.ssrc__radio + label.ssrc__label
     div.footerWrapper > div.chatbot_SendMessageContainer (div.textArea[contenteditable]; d-none for choice questions)
       > div.chipsContainer > .chatbot_Chip.chipItem > span        ("Try again" chip = chatbot error)
       > div.sendMsgbtn_container > div.sendMsg "Save"             (SENDS the current answer)
*/
(function nkProbe(opts) {
  'use strict';
  opts = opts || {};
  var els = [];

  function ref(el) {
    if (!opts.withElements || !el) return -1;
    var i = els.indexOf(el);
    if (i < 0) { els.push(el); i = els.length - 1; }
    return i;
  }
  function norm(s) { return String(s == null ? '' : s).replace(/\s+/g, ' ').trim(); }
  function txt(el) { return el ? norm(el.innerText !== undefined ? el.innerText : el.textContent) : ''; }
  function lc(s) { return String(s == null ? '' : s).toLowerCase(); }
  function cls(el) {
    var c = el && el.className;
    return String(c && c.baseVal !== undefined ? c.baseVal : (c || ''));
  }
  function vis(el) {
    if (!el || !el.getBoundingClientRect) return false;
    var r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    var s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.05;
  }
  function precedes(a, b) { return !!(a.compareDocumentPosition(b) & 4); }
  function hintOf(el) {
    return lc([el.getAttribute('placeholder'), el.getAttribute('data-placeholder'),
      el.getAttribute('aria-label'), el.getAttribute('name'), el.id, cls(el),
      el.getAttribute('title')].join(' '));
  }
  function hash(s) {
    var h = 5381;
    s = String(s || '');
    for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return (h >>> 0).toString(36);
  }

  // ---- passive change clock for the chat drawer (the only side effect)
  var mut = window.__nkMut;
  if (!mut) {
    mut = window.__nkMut = {last: Date.now(), n: 0};
    try {
      new MutationObserver(function (recs) {
        for (var i = 0; i < recs.length; i++) {
          var t = recs[i].target;
          if (t && t.nodeType !== 1) t = t.parentElement;
          if (t && t.closest && t.closest('[class*="chatbot"]')) { mut.last = Date.now(); mut.n++; return; }
        }
      }).observe(document.documentElement, {subtree: true, childList: true, characterData: true,
        attributes: true, attributeFilter: ['class', 'disabled', 'aria-disabled', 'checked', 'value']});
    } catch (e) {}
  }

  // ---- page phase, job id, server verdict
  var url = location.href, lu = lc(url), phase = 'other';
  if (lu.indexOf('/myapply/') >= 0) phase = 'result';
  else if (lu.indexOf('/job-listings-') >= 0) phase = 'job';
  else if (/nlogin|\/login/.test(lu)) phase = 'login';
  else if (/naukri\.com\/[^?#]*-jobs|[?&]k=/.test(lu)) phase = 'search';
  var jm = lu.match(/job-listings-[^?#]*?-(\d{9,})(?:[?#/]|$)/) || lu.match(/(?:strjobsarr=\[?|file=)(\d{9,})/);
  var applyResp = null;
  try {
    var am = decodeURIComponent(url).match(/multiApplyResp=(\{[^}]*\})/i);
    if (am) applyResp = JSON.parse(am[1]);
  } catch (e) {}
  var pw = document.querySelectorAll('input[type=password]'), loginWall = phase === 'login';
  for (var pi = 0; pi < pw.length && !loginWall; pi++) if (vis(pw[pi])) loginWall = true;
  if (!loginWall && /\blogin\b/i.test(document.title) && phase !== 'job' && phase !== 'search') loginWall = true;

  var model = {
    v: 2, ts: Date.now(), url: url, title: document.title, ready: document.readyState,
    phase: phase, jobId: jm ? jm[1] : null, applyResp: applyResp, loginWall: loginWall,
    banners: [], applyControls: [], drawer: null
  };

  // ---- verdict banners: short visible blocks only (never whole-body substring matching)
  var REJECT = /oops|not accepted|incomplete information|answer all mandatory/i;
  var ERROR = /there was an error|something went wrong|try again later|could not be (?:submitted|processed)|unable to process|limit (?:reached|exceeded)|quota/i;
  var REDIRECT = /redirected to the company website/i;
  var SUCCESS = /\bapplied to\b|successfully applied|application (?:sent|submitted)|applied successfully|already applied/i;
  var seenBanner = {};
  function scanBanners(root, allowSuccess) {
    if (!root) return;
    var tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null), n, k = 0;
    while ((n = tw.nextNode()) && k < 6000 && model.banners.length < 6) {
      k++;
      var s = n.nodeValue;
      if (!s || s.length < 6) continue;
      var kind = REJECT.test(s) ? 'reject' : ERROR.test(s) ? 'error' : REDIRECT.test(s) ? 'redirect'
        : (allowSuccess && SUCCESS.test(s)) ? 'success' : null;
      if (!kind) continue;
      var b = n.parentElement;
      for (var up = 0; up < 3 && b && txt(b).length < 40; up++) b = b.parentElement;
      if (!b || !vis(b)) continue;
      var t = txt(b).slice(0, 240);
      if (seenBanner[t]) continue;
      seenBanner[t] = 1;
      model.banners.push({kind: kind, text: t});
    }
  }

  // ---- job page: every Apply-like control and what kind it is
  if (phase === 'job') {
    var cand = document.querySelectorAll('button, a, [role=button], [id*="apply" i]');
    for (var ci = 0; ci < cand.length && model.applyControls.length < 10; ci++) {
      var ce = cand[ci], ct = txt(ce), idc = lc((ce.id || '') + ' ' + cls(ce)), ctl = lc(ct);
      if (ct.length > 50 || !/apply|applied|interested/.test(ctl + ' ' + idc)) continue;
      if (ce.closest('[class*="chatbot"]')) continue;
      var kind = null;
      if (/\bapplied\b/.test(ctl) || /already-applied/.test(idc)) kind = 'applied';
      else if (/company site|company-site/.test(ctl + ' ' + idc)) kind = 'external';
      else if (/interested/.test(ctl)) kind = 'interested';
      else if (/^apply( now)?$|easy apply/.test(ctl) || /(^|[\s_])apply-button/.test(idc)) kind = 'easy';
      if (!kind) continue;
      var cr = ce.getBoundingClientRect();
      model.applyControls.push({kind: kind, text: ct, id: ce.id || '', tag: lc(ce.tagName),
        visible: vis(ce), disabled: !!ce.disabled || ce.getAttribute('aria-disabled') === 'true',
        top: Math.round(cr.top + window.scrollY), ref: ref(ce)});
    }
  }

  // ---- chat drawer
  function findDrawer() {
    var best = null, bestA = 0, maxW = Math.max(900, 0.75 * window.innerWidth);
    var cs = document.querySelectorAll('[class*="chatbot_Drawer"], [class*="chatbot_drawer"], [class*="chatbotDrawer"]');
    for (var i = 0; i < cs.length; i++) {
      var c = cs[i];
      if (!vis(c)) continue;
      var r = c.getBoundingClientRect();
      if (r.width < 280 || r.width > maxW || r.height < 200 || r.right <= 0 || r.left >= window.innerWidth) continue;
      if (r.width * r.height > bestA) { best = c; bestA = r.width * r.height; }
    }
    if (best) return {el: best, via: 'class'};
    // Fallback: a real dialog/drawer (never a page column: job cards carry "Save" bookmark
    // buttons, which is how the old Save-anchored finder matched whole pages).
    var dl = document.querySelectorAll('[role=dialog], [aria-modal="true"], [class*="drawer" i], [class*="modal" i]');
    for (var j = 0; j < dl.length; j++) {
      var p = dl[j];
      if (!vis(p)) continue;
      var pr = p.getBoundingClientRect();
      if (pr.width < 300 || pr.width > maxW || pr.height < 200) continue;
      if (!p.querySelector('input:not([type=hidden]), textarea, select, [contenteditable]')) continue;
      if (!/(^|\s)(save|submit|save & apply)(\s|$)/i.test(txt(p))) continue;
      return {el: p, via: 'dialog'};
    }
    return null;
  }

  var found = findDrawer();
  if (found) {
    var d = found.el;
    var dr = d.getBoundingClientRect();
    var footer = null;
    var fcs = d.querySelectorAll('[class*="footer" i]');
    for (var fi = fcs.length - 1; fi >= 0; fi--) { if (vis(fcs[fi])) { footer = fcs[fi]; break; } }
    var dm = {
      via: found.via, id: d.id || '', cls: cls(d).slice(0, 120),
      rect: [Math.round(dr.width), Math.round(dr.height), Math.round(dr.left), Math.round(dr.top)],
      quietMs: Date.now() - mut.last, mutN: mut.n, busy: false, messages: [], msgCount: 0,
      activeQuestion: null, qsig: '', lastWho: null, widgets: [], save: null, send: null,
      closeRef: -1, skippable: false, errorChip: false, sig: ''
    };

    var busyEls = d.querySelectorAll('li.loader, #typing, [class*="typing" i], [class*="loader" i], [class*="chatBotdot" i]');
    for (var bi = 0; bi < busyEls.length; bi++) { if (vis(busyEls[bi])) { dm.busy = true; break; } }

    var closers = d.querySelectorAll('[class*="ic-cross" i], [class*="close" i], [class*="cross" i], [aria-label="Close" i]');
    for (var xi = 0; xi < closers.length; xi++) { if (vis(closers[xi])) { dm.closeRef = ref(closers[xi]); break; } }

    // -- widgets
    var claimed = [];   // subtrees owned by widgets (their text is not chat transcript)
    function claim(el) { if (el && claimed.indexOf(el) < 0) claimed.push(el); }
    function isClaimed(el) {
      for (var i = 0; i < claimed.length; i++) if (claimed[i] === el || claimed[i].contains(el)) return true;
      return false;
    }
    function labelFor(inp) {
      var lab = inp.closest('label'), t = '';
      if (!lab && inp.id) {  // option ids repeat across questions (id="Yes"): search the option's own box first
        var scope = inp.parentElement;
        for (var s = 0; s < 3 && scope && !lab; s++, scope = scope.parentElement) {
          try { lab = scope.querySelector('label[for="' + CSS.escape(inp.id) + '"]'); } catch (e) {}
        }
      }
      if (lab) t = txt(lab);
      var clickEl = lab && vis(lab) ? lab : null;
      if (!clickEl) {  // custom pill: nearest visible ancestor that carries the option text
        var p = inp.parentElement;
        for (var k = 0; k < 3 && p && p !== d; k++, p = p.parentElement) {
          if (vis(p) && txt(p)) { clickEl = p; if (!t) t = txt(p); break; }
        }
      }
      if (!t) t = norm(inp.value);
      return {text: t.slice(0, 100), clickEl: clickEl || inp};
    }

    // radio / checkbox groups: one group per question container (every Naukri radio is name="radio-button")
    var groups = {}, gorder = [], holders = [];
    var choice = d.querySelectorAll('input[type=radio], input[type=checkbox]');
    for (var gi = 0; gi < choice.length; gi++) {
      var inp = choice[gi];
      var holder = inp.closest('.singleselect-radiobutton-container, [class*="multiselect" i], [class*="checkbox-container" i], fieldset');
      if (!holder) {
        for (var hp = inp.parentElement; hp && hp !== d; hp = hp.parentElement) {
          if (hp.querySelectorAll('input[type=' + inp.type + ']').length >= 2) { holder = hp; break; }
        }
      }
      var hIdx = holder ? holders.indexOf(holder) : -1;
      if (holder && hIdx < 0) { holders.push(holder); hIdx = holders.length - 1; }
      var key = inp.type + '|' + (holder ? 'h' + hIdx : 'solo' + gi);
      if (!groups[key]) { groups[key] = {type: inp.type, inputs: [], holder: holder}; gorder.push(key); }
      groups[key].inputs.push(inp);
    }
    gorder.forEach(function (key) {
      var g = groups[key], opts = [], anyVis = false, checked = [];
      g.inputs.forEach(function (inp) {
        var lf = labelFor(inp);
        if (vis(lf.clickEl)) anyVis = true;
        opts.push({text: lf.text, checked: !!inp.checked, ref: ref(lf.clickEl), inputRef: ref(inp)});
        if (inp.checked) checked.push(lf.text);
      });
      if (!anyVis) return;  // stale off-screen template
      var root = g.holder || g.inputs[0].parentElement;
      claim(root);
      dm.widgets.push({kind: g.type === 'radio' ? 'radio' : 'checkbox', el: root, options: opts,
        filled: checked.length > 0, value: checked.join(', '),
        required: g.inputs.some(function (i) { return i.required || i.getAttribute('aria-required') === 'true'; }) || null});
    });

    // date parts, selects, text-like controls
    function dateRoleFromHint(h) {
      if (/\b(dd|day)\b|[_-]day|day[_-]/.test(h)) return 'day';
      if (/\b(mm|month)\b|[_-]month|month[_-]/.test(h)) return 'month';
      if (/\b(yy|yyyy|year)\b|[_-]year|year[_-]/.test(h)) return 'year';
      return null;
    }
    function selectRole(sel) {
      var texts = [], nums = [];
      for (var i = 0; i < sel.options.length; i++) {
        var t = norm(sel.options[i].text);
        texts.push(t);
        if (/^\d{1,4}$/.test(t)) nums.push(Number(t));
      }
      if (texts.some(function (t) { return /^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i.test(t); })) return 'month';
      if (nums.length >= 20 && nums.every(function (x) { return x >= 1900 && x <= 2100; })) return 'year';
      if (nums.length >= 28 && Math.max.apply(null, nums) <= 31) return 'day';
      if (nums.length === 12 && Math.max.apply(null, nums) === 12) return 'month';
      return dateRoleFromHint(hintOf(sel));
    }
    var dateParts = [];
    var sels = d.querySelectorAll('select');
    for (var si = 0; si < sels.length; si++) {
      var sel = sels[si];
      if (!vis(sel) && !vis(sel.parentElement)) continue;
      var sopts = [], selIdx = sel.selectedIndex;
      for (var oi = 0; oi < sel.options.length; oi++) sopts.push(norm(sel.options[oi].text));
      var phFirst = sopts.length && (/^(select|choose|--)/i.test(sopts[0]) || sel.options[0].value === '');
      var sfilled = selIdx >= 0 && !(phFirst && selIdx === 0) && !!sopts[selIdx];
      var srole = selectRole(sel);
      var sw = {kind: 'select', el: sel, ref: ref(sel), options: sopts.slice(0, 80).map(function (t) { return {text: t}; }),
        filled: sfilled, value: sfilled ? sopts[selIdx] : '', required: sel.required || null};
      if (srole) { sw.role = srole; dateParts.push(sw); } else { claim(sel); dm.widgets.push(sw); }
    }
    var PH = /^(type message here|type your message|write a message|enter your answer|type here|type your answer|enter message|write here)\.*…?$/i;
    var texts = d.querySelectorAll('input, textarea, [contenteditable="true"], [contenteditable=""], [role=textbox]');
    for (var ti = 0; ti < texts.length; ti++) {
      var te = texts[ti], type = lc(te.getAttribute('type') || '');
      if (te.tagName === 'INPUT' && /^(radio|checkbox|file|hidden|submit|button|password|image|reset)$/.test(type)) continue;
      if (!vis(te)) continue;
      var isInput = te.tagName === 'INPUT' || te.tagName === 'TEXTAREA';
      var val = isInput ? norm(te.value) : txt(te);
      var ph = norm(te.getAttribute('placeholder') || te.getAttribute('data-placeholder') || '');
      if (val && (val === ph || PH.test(val))) val = '';
      var h = hintOf(te), role = null, kind;
      if (/^(date|month|datetime-local)$/.test(type)) kind = 'date';
      else if (/dd\s*[\/\-.]\s*mm\s*[\/\-.]\s*yy/i.test(ph)) kind = 'date_text';
      else if ((role = dateRoleFromHint(h)) && isInput && (te.maxLength === 2 || te.maxLength === 4 || /^(dd|mm|yy|yyyy)$/i.test(ph))) kind = 'datepart';
      else if (te.tagName === 'TEXTAREA') kind = 'textarea';
      else if (!isInput) kind = 'composer';
      else kind = 'text';
      var tw_ = {kind: kind, el: te, ref: ref(te), filled: !!val, value: val.slice(0, 200), placeholder: ph.slice(0, 60),
        inputType: type || (isInput ? 'text' : 'contenteditable'), maxLength: isInput && te.maxLength > 0 ? te.maxLength : null,
        required: te.required || te.getAttribute('aria-required') === 'true' || null};
      if (kind === 'datepart') { tw_.role = role; dateParts.push(tw_); } else { claim(te); dm.widgets.push(tw_); }
    }
    // stitch day/month/year parts that share a container into one date_split widget
    while (dateParts.length) {
      var first = dateParts.shift(), grp = [first], cont = first.el.parentElement;
      for (var lvl = 0; lvl < 5 && cont && cont !== d; lvl++, cont = cont.parentElement) {
        var mates = dateParts.filter(function (p) { return cont.contains(p.el); });
        if (mates.length) { grp = grp.concat(mates); dateParts = dateParts.filter(function (p) { return mates.indexOf(p) < 0; }); break; }
      }
      if (grp.length === 1) {
        first.kind = first.kind === 'datepart' ? 'text' : first.kind;
        delete first.role;
        claim(first.el);
        dm.widgets.push(first);
        continue;
      }
      var root = cont && cont !== d ? cont : grp[0].el.parentElement;
      claim(root);
      dm.widgets.push({kind: 'date_split', el: root, filled: grp.every(function (p) { return p.filled; }),
        value: grp.map(function (p) { return p.value || '_'; }).join('/'), required: null,
        parts: grp.map(function (p) {
          return {role: p.role, tag: lc(p.el.tagName), ref: p.ref, value: p.value, filled: p.filled,
            placeholder: p.placeholder || '', maxLength: p.maxLength || null,
            options: p.options ? p.options.slice(0, 40) : undefined};
        })});
    }

    // file inputs (usually hidden behind an "Upload" chip). Naukri leaves this <input> mounted
    // in the DOM (hidden) for the rest of the conversation once a resume question has ever
    // rendered, so it must NOT be surfaced as a widget on every later, unrelated question —
    // sampled live, that misattributed a resume-upload requirement onto "are you an EX LTM?"
    // and other questions entirely, which is exactly the kind of stale-question pairing this
    // probe exists to prevent. Only report it while the CURRENT question is actually about a
    // resume/CV (checked against the most recent bot message, not the whole drawer).
    var files = d.querySelectorAll('input[type=file]');
    if (files.length) {
      var _lastBotLi = null, _bLis = d.querySelectorAll('li.botItem');
      for (var _bi = _bLis.length - 1; _bi >= 0; _bi--) {
        if (!/ (loader|botLogo) /.test(' ' + cls(_bLis[_bi]) + ' ') && vis(_bLis[_bi])) { _lastBotLi = _bLis[_bi]; break; }
      }
      // 2026-09-24: bare "upload|attach" dropped — "PAN Number (Mandatory for uploading your profile
      // to the employer)" matched it, so v2 kept trying to attach a resume to this hidden uploader instead of
      // answering the PAN question, and aborted the job as STUCK every cycle.
      var _resumeCtx = /resume|\bcv\b|biodata|curriculum vitae/i;
      var isResumeAsk = _lastBotLi ? _resumeCtx.test(txt(_lastBotLi)) : _resumeCtx.test(txt(d));
      var fileShown = /\.(pdf|docx?|rtf)\b/i.test(txt(d));
      if (isResumeAsk || fileShown) {
        for (var fli = 0; fli < files.length; fli++) {
          var fe = files[fli];
          dm.widgets.push({kind: 'file', el: fe, ref: ref(fe), accept: fe.accept || '',
            files: fe.files ? fe.files.length : -1, filled: (fe.files && fe.files.length > 0) || fileShown,
            value: fe.files && fe.files.length ? fe.files[0].name : '', required: fe.required || null});
        }
      }
    }

    // chips / quick replies (Naukri keeps them in the footer): leaf-most clickable short texts
    var SAVE_RE = /^(save|submit|save & apply|save and apply|done|next|continue|send)$/i;
    var chipNodes = d.querySelectorAll('.chatbot_Chip, .chipItem, [class*="chip" i], [class*="pill" i], [class*="suggest" i], [class*="quick" i], button, [role=button]');
    var chipCands = [];
    for (var hi = 0; hi < chipNodes.length; hi++) {
      var ch = chipNodes[hi];
      if (!vis(ch) || isClaimed(ch) || ch.querySelector('input, textarea, select, [contenteditable]')) continue;
      if (ch.closest('[class*="sendMsg" i], [class*="chatbot_Nav"], [class*="textArea" i], li.userItem')) continue;
      var cht = txt(ch);
      if (!cht || cht.length > 60 || SAVE_RE.test(cht) || /^(close|cancel|×|x)$/i.test(cht)) continue;
      chipCands.push({el: ch, text: cht});
    }
    var chips = chipCands.filter(function (c) {
      return !chipCands.some(function (o) { return o !== c && c.el.contains(o.el); });
    });
    if (chips.length) {
      chips.forEach(function (c) { claim(c.el); });
      dm.skippable = chips.some(function (c) { return /skip|later/i.test(c.text); });
      dm.errorChip = chips.some(function (c) { return /^(try again|retry)$/i.test(c.text); });
      dm.widgets.push({kind: 'chips', el: chips[0].el, filled: false, value: '',
        options: chips.map(function (c) { return {text: c.text, ref: ref(c.el)}; }), required: null});
    }

    // unknown interactive controls = formats nobody handles yet (reported, never guessed at)
    var odd = d.querySelectorAll('[role=combobox], [role=listbox], [role=slider], [role=spinbutton], [role=switch], [aria-haspopup], [class*="dropdown" i], [class*="calendar" i], [class*="datepicker" i], [class*="picker" i]');
    for (var ui = 0; ui < odd.length; ui++) {
      var ue = odd[ui];
      if (!vis(ue) || isClaimed(ue)) continue;
      claim(ue);
      dm.widgets.push({kind: 'unknown', el: ue, ref: ref(ue), filled: null, value: txt(ue).slice(0, 80),
        role: ue.getAttribute('role') || '', html: (ue.outerHTML || '').slice(0, 1200), required: null});
    }

    // -- transcript
    function ownText(el) {
      var out = [], w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null), n;
      while ((n = w.nextNode())) {
        if (n.parentElement && !isClaimed(n.parentElement) && norm(n.nodeValue)) out.push(norm(n.nodeValue));
      }
      return out.join(' ');
    }
    var bubbles = [];
    var lis = d.querySelectorAll('li.botItem, li.userItem');
    if (lis.length) {
      for (var li = 0; li < lis.length; li++) {
        var le = lis[li], lcl = ' ' + cls(le) + ' ';
        if (/ (loader|botLogo) /.test(lcl) || !vis(le)) continue;
        var lt = ownText(le);
        if (lt) bubbles.push({el: le, who: / userItem /.test(lcl) ? 'user' : 'bot', text: lt.slice(0, 400)});
      }
    } else {  // generic chat markup: smallest text blocks outside widgets and footer
      var walker = document.createTreeWalker(d, NodeFilter.SHOW_TEXT, null), tn;
      while ((tn = walker.nextNode())) {
        if (!norm(tn.nodeValue)) continue;
        var be = tn.parentElement;
        if (!be || isClaimed(be) || (footer && footer.contains(be))) continue;
        var bub = be;
        for (var bu = 0; bu < 4 && bub && bub !== d; bu++) {
          if (/msg|message|bubble|item/i.test(cls(bub).replace(/chatbot_\S*/g, ''))) break;
          bub = bub.parentElement;
        }
        if (!bub || bub === d || !vis(bub)) bub = be;
        if (bubbles.some(function (b) { return b.el === bub || b.el.contains(bub); })) continue;
        bubbles = bubbles.filter(function (b) { return !bub.contains(b.el); });
        bubbles.push({el: bub});
      }
      var mid = dr.left + dr.width / 2;
      bubbles.forEach(function (b) {
        var who = null, p = b.el;
        for (var k = 0; k < 5 && p && p !== d; k++, p = p.parentElement) {
          var c = lc(cls(p)).replace(/chatbot_\S*/g, '');
          if (/user|self|outgoing|sender|sent/.test(c)) { who = 'user'; break; }
          if (/\bbot|recruiter|incoming|received/.test(c)) { who = 'bot'; break; }
        }
        if (!who) {
          var r = b.el.getBoundingClientRect();
          who = (r.left > mid - 20 && r.width < dr.width * 0.8) ? 'user' : 'bot';
        }
        b.who = who;
        b.text = ownText(b.el).slice(0, 400);
      });
      bubbles = bubbles.filter(function (b) { return b.text; });
    }
    dm.messages = bubbles.slice(-40).map(function (b) { return {who: b.who, text: b.text}; });
    dm.msgCount = bubbles.length;
    var lastMsg = bubbles.length ? bubbles[bubbles.length - 1] : null;
    dm.lastWho = lastMsg ? lastMsg.who : null;
    dm.activeQuestion = lastMsg && lastMsg.who === 'bot' ? lastMsg.text : null;
    dm.qsig = hash(dm.activeQuestion || '');

    // -- question for each widget: explicit label, else nearest preceding bot bubble
    dm.widgets.forEach(function (w) {
      var q = '';
      var anchor = w.el;
      if (anchor && anchor.id && w.kind !== 'radio' && w.kind !== 'checkbox') {
        try { var lab = anchor.parentElement && anchor.parentElement.querySelector('label[for="' + CSS.escape(anchor.id) + '"]'); if (lab) q = txt(lab); } catch (e) {}
      }
      if (!q && anchor) {
        for (var i = 0; i < bubbles.length; i++) {
          if (bubbles[i].who === 'bot' && precedes(bubbles[i].el, anchor)) q = bubbles[i].text;
        }
      }
      if (!q && lastMsg && lastMsg.who === 'bot') q = lastMsg.text;
      w.q = q.slice(0, 400);
      if (w.required === null && /\*|\b(mandatory|required)\b/i.test(q)) w.required = true;
      if (w.ref === undefined) w.ref = ref(w.el);
      delete w.el;
    });

    // -- Save (div.sendMsgbtn_container > div.sendMsg: it SENDS each answer)
    var saveCands = [];
    var sw2 = document.createTreeWalker(d, NodeFilter.SHOW_TEXT, null), sn;
    while ((sn = sw2.nextNode())) {
      var sv = norm(sn.nodeValue);
      if (!sv || !SAVE_RE.test(sv)) continue;
      var leaf = sn.parentElement;
      if (!leaf || !vis(leaf) || leaf.closest('label') || isClaimed(leaf)) continue;
      var clickEl = leaf, pp = leaf;
      for (var su = 0; su < 3 && pp && pp !== d; su++, pp = pp.parentElement) {
        if (pp.tagName === 'BUTTON' || pp.getAttribute('role') === 'button' || /send|save|submit|btn|button/i.test(cls(pp))) { clickEl = pp; break; }
      }
      saveCands.push({leaf: leaf, click: clickEl, text: sv});
    }
    function enabledInfo(el) {
      var s = getComputedStyle(el), c = lc(cls(el));
      return {disabledAttr: !!el.disabled || el.hasAttribute('disabled'), ariaDisabled: el.getAttribute('aria-disabled') === 'true',
        clsDisabled: /disabled|inactive/.test(c), pointerNone: s.pointerEvents === 'none', opacity: parseFloat(s.opacity || '1')};
    }
    if (saveCands.length) {
      var sc = saveCands[saveCands.length - 1];
      var a = enabledInfo(sc.leaf), b = enabledInfo(sc.click);
      var enabled = !(a.disabledAttr || b.disabledAttr || a.ariaDisabled || b.ariaDisabled || a.clsDisabled ||
        b.clsDisabled || a.pointerNone || b.pointerNone || Math.min(a.opacity, b.opacity) < 0.5);
      var cont = sc.leaf.closest('[class*="sendMsgbtn_container"]');
      dm.save = {text: sc.text, enabled: enabled, tag: lc(sc.click.tagName), cls: cls(sc.click).slice(0, 80),
        why: enabled ? '' : JSON.stringify({leaf: a, click: b}), ref: ref(sc.click), leafRef: ref(sc.leaf),
        containerRef: cont ? ref(cont) : -1};
    }
    if (footer) {
      var sends = footer.querySelectorAll('[class*="send" i], [aria-label*="send" i]');
      for (var sdi = 0; sdi < sends.length; sdi++) {
        var se = sends[sdi], last = saveCands[saveCands.length - 1];
        if (!vis(se) || (last && (se === last.click || se.contains(last.leaf) || last.leaf.contains(se)))) continue;
        if (/sendMsgbtn|sendmsg|SendMessageContainer/i.test(cls(se))) continue;
        dm.send = {tag: lc(se.tagName), cls: cls(se).slice(0, 80), ref: ref(se)};
        break;
      }
    }

    dm.sig = hash(dm.widgets.map(function (w) {
      return w.kind + (w.parts ? '[' + w.parts.map(function (p) { return p.role + ':' + p.tag; }).join(',') + ']' : '') +
        (w.options ? '#' + w.options.length : '');
    }).join('|'));
    if (opts.withHtml) dm.html = (d.outerHTML || '').slice(0, 120000);
    model.drawer = dm;
    scanBanners(d, false);
  }

  if (phase === 'result' || phase === 'login') scanBanners(document.body, true);
  else {
    var toasts = document.querySelectorAll('[role=alert], [class*="toast" i], [class*="snackbar" i], [class*="error" i]');
    for (var tsi = 0; tsi < toasts.length && tsi < 30; tsi++) if (vis(toasts[tsi])) scanBanners(toasts[tsi], !model.drawer);
  }

  var out = JSON.stringify(model);
  return opts.withElements ? {json: out, els: els} : out;
})
