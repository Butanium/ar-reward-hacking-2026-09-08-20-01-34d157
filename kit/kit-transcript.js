/* clab report kit — kit-transcript.js
   Agent-transcript renderer for multi-turn tool-use episodes (inspect-ai
   rollouts and the like): system/user/assistant/tool messages, optional
   reasoning (full thinking and/or OpenAI-style summary), tool calls with
   smart arg display, and tool results attached beneath their call.
   Pure DOM, no dependencies. Visual language borrowed from
   tim-hua-01/cc_transcript_viewer; styles in kit-transcript.css.

     KitTranscript.render(el, {
       messages: [{ role: "system"|"user"|"assistant"|"tool",
                    text, reasoning, reasoning_summary,
                    tool_calls: [{ id?, fn, args: {k: v} }],
                    tool_call_id? }],
       collapsed: true,     // false = start with reasoning + long blocks open
       id: "kt-ep3",        // stable anchor prefix (default: auto)
     }) -> root element     // el may be null: just build and return

   KitCards.transcript(messages) delegates here, so legacy [{role, text}]
   call sites get the new rendering for free. */
"use strict";

const KitTranscript = (() => {
  const ROLES = { system: "system", user: "user", assistant: "assistant", tool: "tool-turn" };

  const el = (tag, cls, text) => {
    const d = document.createElement(tag);
    if (cls) d.className = cls;
    if (text != null) d.textContent = text;
    return d;
  };
  const fmtBytes = n => n < 1024 ? `${n} B`
    : n < 1048576 ? `${(n / 1024).toFixed(1)} kB` : `${(n / 1048576).toFixed(2)} MB`;
  const firstLine = (s, max = 140) => {
    const line = String(s).replace(/\s+/g, " ").trim();
    return line.length > max ? line.slice(0, max - 1) + "…" : line;
  };
  const onActivate = (node, fn) => {
    node.addEventListener("click", fn);
    node.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(e); }
    });
  };

  /* ---- clamped mono block. Lazy: only the first `lines` lines enter the DOM
     until expanded (a 100+-message transcript carries megabytes of tool output;
     rendering it all up front janks the page). The affordance is its own row —
     clicking the text itself never toggles, so selecting a quote is safe. ---- */
  function clip(text, { lines = 15, expanded = false } = {}) {
    text = String(text ?? "");
    const wrap = el("div", "kt-clip");
    const pre = el("pre", "kt-pre");
    wrap.appendChild(pre);
    const all = text.split("\n");
    if (all.length <= lines + 3 && text.length <= 2000) {   // fits (with tolerance): no affordance
      pre.textContent = text;
      return wrap;
    }
    wrap.classList.add("clipped");
    const head = all.slice(0, lines).join("\n");
    const hint = `${all.length} lines` + (text.length > 2048 ? ` · ${fmtBytes(text.length)}` : "");
    const more = el("div", "kt-clip-more");
    more.tabIndex = 0;
    more.setAttribute("role", "button");
    wrap.appendChild(more);
    let open = false;
    const paint = () => {
      pre.textContent = open ? text : head;
      more.textContent = open ? "▴ collapse" : `▸ expand — ${hint}`;
      wrap.classList.toggle("open", open);
      more.setAttribute("aria-expanded", String(open));
    };
    onActivate(more, () => { open = !open; paint(); });
    if (expanded) open = true;
    paint();
    return wrap;
  }

  /* ---- reasoning panel: one-line digest at rest, full text (lazy) on click ---- */
  function think(label, text, expanded) {
    text = String(text ?? "");
    const box = el("div", "kt-think");
    const head = el("div", "kt-think-head");
    head.tabIndex = 0;
    head.setAttribute("role", "button");
    const chev = el("span", "kt-chev", "▸");
    head.append(chev, el("span", "kt-think-lab", label),
                el("span", "kt-think-digest", firstLine(text)));
    box.appendChild(head);
    let body = null, open = false;
    const toggle = force => {
      open = force ?? !open;
      if (open && !body) { body = el("div", "kt-think-body", text); box.appendChild(body); }
      if (body) body.style.display = open ? "" : "none";
      box.classList.toggle("open", open);
      chev.textContent = open ? "▾" : "▸";
      head.setAttribute("aria-expanded", String(open));
    };
    onActivate(head, () => {
      const sel = getSelection();
      if (sel && String(sel).length && head.contains(sel.anchorNode)) return;
      toggle();
    });
    toggle(!!expanded);
    return box;
  }

  /* ---- tool call card. Args split by shape: multi-line / long string args
     (bash scripts, file bodies) each get a clamped code block; scalar args a
     compact `key: value` list. Header = fn name + one-line summary; clicking
     it folds the whole card (args + result) for skimming. ---- */
  function toolCall(call, expandAll) {
    const box = el("div", "kt-call");
    const small = [], big = [];
    for (const [k, v] of Object.entries(call?.args || {})) {
      const s = typeof v === "string" ? v : JSON.stringify(v, null, 1) ?? String(v);
      if (s.includes("\n") || s.length > 100) big.push([k, s]);
      else small.push([k, s]);
    }
    const head = el("div", "kt-call-head");
    head.tabIndex = 0;
    head.setAttribute("role", "button");
    head.setAttribute("aria-expanded", "true");
    const chev = el("span", "kt-chev", "▾");
    const summary = big.length ? firstLine(big[0][1], 120)
      : small.map(([k, s]) => `${k}: ${s}`).join("  ");
    head.append(chev, el("span", "kt-fn", String(call?.fn ?? "tool")),
                el("span", "kt-call-sum", summary));
    const body = el("div", "kt-call-body");
    if (small.length) {
      const kv = el("div", "kt-kv");
      for (const [k, s] of small) {
        const row = el("div", "kt-kv-row");
        row.append(el("b", null, k + ":"), " " + s);
        kv.appendChild(row);
      }
      body.appendChild(kv);
    }
    for (const [k, s] of big) {
      /* a lone multi-line arg (the common single-`cmd` case) needs no label —
         the fn name in the header already says what the block is */
      if (big.length > 1 || small.length) body.appendChild(el("div", "kt-arg-lab", k));
      body.appendChild(clip(s, { expanded: expandAll }));
    }
    if (!small.length && !big.length) body.appendChild(el("div", "kt-empty", "(no arguments)"));
    box.append(head, body);
    onActivate(head, () => {
      const closed = box.classList.toggle("collapsed");
      chev.textContent = closed ? "▸" : "▾";
      head.setAttribute("aria-expanded", String(!closed));
    });
    return box;
  }

  /* tool result block: labelled, clamped, with a size hint when large */
  function resultPart(msg, mi, expandAll) {
    const box = el("div", "kt-result");
    const text = String(msg?.text ?? "");
    const lab = el("div", "kt-result-lab",
      "output" + (mi != null ? ` · msg ${mi}` : "")
      + (text.length > 2048 ? ` · ${fmtBytes(text.length)}` : ""));
    box.appendChild(lab);
    box.appendChild(text.trim() ? clip(text, { expanded: expandAll })
                                : el("div", "kt-empty", "(empty output)"));
    return box;
  }

  /* message prose through the kit's expandable text block when available
     (6-line clamp + overflow detection), else a plain pre-wrap div */
  function textPart(text) {
    const d = el("div", "kt-text");
    const KC = typeof KitCards !== "undefined" ? KitCards : null;
    if (KC?.ptext && KC?.esc) d.appendChild(KC.ptext(KC.esc(text)));
    else { const p = el("div"); p.style.whiteSpace = "pre-wrap"; p.textContent = text; d.appendChild(p); }
    return d;
  }

  /* ---- sticky-bar position tracking. One capture-phase scroll listener
     serves every transcript on the page (capture catches inner scrollers like
     the explorer's .sample-list, where window scroll never fires); work is
     rAF-coalesced and skips transcripts that are detached or folded away. ---- */
  const live = [];
  let armed = false, raf = 0;
  function update() {
    for (let k = live.length - 1; k >= 0; k--) {
      const r = live[k];
      if (!r.root.isConnected) { live.splice(k, 1); continue; }
      const rect = r.root.getBoundingClientRect();
      if (!rect.height) continue;                       // inside a closed fold
      const yRef = r.bar.getBoundingClientRect().bottom + 8 - rect.top;
      let lo = 0, hi = r.turns.length - 1, cur = 0;     // last turn starting above the bar
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (r.turns[mid].offsetTop <= yRef) { cur = mid; lo = mid + 1; } else hi = mid - 1;
      }
      const label = r.turns.length
        ? `message ${r.turns[cur].dataset.mi}/${r.n}` : "empty transcript";
      if (label !== r.last) { r.last = label; r.pos.textContent = label; }
    }
  }
  function armScroll() {
    if (armed) return;
    armed = true;
    const kick = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; update(); });
    };
    addEventListener("scroll", kick, { passive: true, capture: true });
    addEventListener("resize", kick, { passive: true });
  }

  let uid = 0;
  function build(opts = {}) {
    const msgs = opts.messages || [];
    const expandAll = opts.collapsed === false;
    const baseId = opts.id || `kt${++uid}`;
    const N = msgs.length;
    const root = el("div", "kt-transcript");

    const bar = el("div", "kt-bar");
    const pos = el("span", "kt-bar-pos num", N ? `message 1/${N}` : "empty transcript");
    const top = el("button", "kt-bar-top", "↑ top");
    top.type = "button";
    top.title = "scroll to the top of this transcript";
    top.addEventListener("click", () =>
      root.scrollIntoView({ behavior: "smooth", block: "start" }));
    bar.append(pos, top);
    root.appendChild(bar);

    const turns = [];
    const mkTurn = (role, mi) => {
      const t = el("section", `kt-turn kt-${ROLES[role] || "user"}`);
      t.id = `${baseId}-m${mi}`;
      t.dataset.mi = mi;
      const head = el("div", "kt-head");
      const a = el("a", "kt-anchor", "#");
      a.href = `#${t.id}`;
      a.title = "link to this message";
      head.append(el("span", "kt-badge", ROLES[role] ? role : String(role)),
                  el("span", "kt-mi num", `#${mi}`), a);
      const parts = el("div", "kt-parts");
      t.append(head, parts);
      root.appendChild(t);
      turns.push(t);
      return parts;
    };

    for (let i = 0; i < N; i++) {
      const m = msgs[i] || {};
      const mi = i + 1;
      if (m.role === "assistant") {
        const parts = mkTurn("assistant", mi);
        if (m.reasoning) parts.appendChild(think("thinking", m.reasoning, expandAll));
        if (m.reasoning_summary)
          parts.appendChild(think("reasoning summary", m.reasoning_summary, expandAll));
        if (String(m.text ?? "").trim()) parts.appendChild(textPart(m.text));
        const open = [];
        for (const c of m.tool_calls || []) {
          const cb = toolCall(c, expandAll);
          parts.appendChild(cb);
          open.push({ id: c?.id, box: cb, used: false });
        }
        /* the tool-role messages that follow are this turn's results: match by
           tool_call_id when present, else in call order */
        while (i + 1 < N && msgs[i + 1]?.role === "tool") {
          const tm = msgs[++i];
          const tmi = i + 1;
          const slot = (tm.tool_call_id != null
              && open.find(o => !o.used && o.id === tm.tool_call_id))
            || open.find(o => !o.used);
          if (slot) {
            slot.used = true;
            slot.box.querySelector(".kt-call-body")
              .appendChild(resultPart(tm, tmi, expandAll));
          } else {
            mkTurn("tool", tmi).appendChild(resultPart(tm, null, expandAll));
          }
        }
        if (!parts.childNodes.length) parts.appendChild(el("div", "kt-empty", "(empty message)"));
      } else if (m.role === "tool") {
        /* orphan tool message (no assistant turn ahead of it) */
        mkTurn("tool", mi).appendChild(resultPart(m, null, expandAll));
      } else {
        const parts = mkTurn(m.role || "user", mi);
        if (String(m.text ?? "").trim()) parts.appendChild(textPart(m.text));
        else parts.appendChild(el("div", "kt-empty", "(empty message)"));
      }
    }

    live.push({ root, bar, pos, turns, n: N, last: "" });
    armScroll();
    requestAnimationFrame(update);
    return root;
  }

  /* render(el, opts) mounts into el (replacing its content); render(opts) or
     render(null, opts) just builds. Returns the root element either way. */
  function render(target, opts) {
    if (target && !(target instanceof Element)) { opts = target; target = null; }
    const root = build(opts || {});
    if (target) { target.textContent = ""; target.appendChild(root); }
    return root;
  }

  return { render };
})();
