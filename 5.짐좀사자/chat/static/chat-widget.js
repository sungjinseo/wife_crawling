/*
 * 브리핑 화면 내장 챗봇 위젯
 * briefing_template.html 이 /chat-api/widget.js 를 불러와 아래처럼 붙입니다.
 *   DashboardChat.mount({ endpoint: "/chat-api/api/chat", getContext: window.briefingContext });
 * getContext() 는 질문할 때마다 호출되어 지금 화면 상태(지역·보기·필터·접근성 설정·열어 둔 단지·상단 요약)를 보냅니다.
 * 대화는 서버에 저장되고, 위젯은 대화 번호(conversation_id)만 기억합니다 → 새로고침해도 이어서 대화.
 */
(function () {
  const CSS = `
  .dc-btn{position:fixed;right:20px;bottom:20px;z-index:9999;border:0;border-radius:24px;padding:12px 18px;
    background:#1f3fbf;color:#fff;font:600 14px/1 system-ui,sans-serif;cursor:pointer;z-index:10000;box-shadow:0 4px 14px rgba(0,0,0,.18)}
  .dc-panel{position:fixed;right:20px;bottom:76px;z-index:9999;width:min(460px,calc(100vw - 40px));height:min(640px,calc(100vh - 110px));
    display:none;flex-direction:column;background:#fff;border:1px solid #dde1ea;border-radius:14px;overflow:hidden;
    box-shadow:0 10px 30px rgba(0,0,0,.18);font:14px/1.6 system-ui,sans-serif;color:#1d2433}
  .dc-panel.open{display:flex}
  .dc-panel.wide{width:min(760px,calc(100vw - 40px));height:calc(100vh - 110px)}
  .dc-head-btns{display:flex;gap:2px}.dc-head-btns button{border:0;background:none;font-size:17px;line-height:1;cursor:pointer;color:#5b6475;padding:4px 6px;border-radius:6px}
  .dc-head-btns button:hover{background:#f0f2f7}
  .dc-btn-t{margin-left:4px}
  @media (max-width:600px){
    .dc-panel,.dc-panel.wide{left:0;right:0;top:0;bottom:auto;width:auto;height:var(--dc-vh,100dvh);border:0;border-radius:0}
    .dc-wide{display:none}
    .dc-head{position:sticky;top:0;z-index:1;background:#fff;padding-top:max(10px,env(safe-area-inset-top))}
    .dc-head-btns button{font-size:22px;padding:8px 10px}
    .dc-form{padding-bottom:max(10px,env(safe-area-inset-bottom))}
    .dc-form input{font-size:16px}
    .dc-btn{right:14px;bottom:calc(14px + env(safe-area-inset-bottom));width:52px;height:52px;padding:0;border-radius:50%;font-size:22px;line-height:52px}
    .dc-btn .dc-btn-t{display:none}
    body.dc-open .dc-btn{display:none}
    body.dc-open{overflow:hidden}
  }
  .dc-head{padding:10px 14px;border-bottom:1px solid #eef0f5;display:flex;justify-content:space-between;align-items:center}
  .dc-head b{font-size:14px}.dc-ctx{font-size:12px;color:#6b7385}
  .dc-log{flex:1;overflow-y:auto;overflow-x:hidden;padding:12px 14px;display:flex;flex-direction:column;gap:10px}
  .dc-msg{max-width:92%;min-width:0;padding:8px 12px;border-radius:12px;word-break:keep-all;overflow-wrap:anywhere}
  .dc-user{align-self:flex-end;background:#1f3fbf;color:#fff}
  .dc-bot{align-self:stretch;max-width:100%;background:#f4f6fb}
  .dc-bot h1,.dc-bot h2,.dc-bot h3,.dc-bot h4{font-size:14px;margin:10px 0 4px}
  .dc-bot p{margin:4px 0}.dc-bot ul,.dc-bot ol{margin:4px 0;padding-left:18px}
  .dc-bot code{background:#e8ebf3;border-radius:4px;padding:0 4px;font-size:12.5px}
  .dc-bot pre{overflow-x:auto;background:#e8ebf3;padding:6px 8px;border-radius:6px}
  .dc-tw{overflow-x:auto;margin:6px 0;border:1px solid #d9deea;border-radius:8px;background:#fff}
  .dc-bot table{border-collapse:collapse;font-size:12.5px;width:100%}
  .dc-bot th,.dc-bot td{border-bottom:1px solid #e6e9f0;padding:4px 8px;text-align:left;vertical-align:top;
    word-break:keep-all;overflow-wrap:anywhere;min-width:3.5em}
  .dc-bot th{background:#f0f2f8;font-weight:600;white-space:nowrap}
  .dc-bot tr:last-child td{border-bottom:0}
  .dc-bot td:not(:first-child){font-variant-numeric:tabular-nums}
  .dc-steps{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:4px}
  .dc-step{font-size:11.5px;color:#41508a;background:#e7ebfa;border-radius:10px;padding:1px 8px}
  .dc-src{margin-top:6px;font-size:12px;border-top:1px dashed #d4d9e6;padding-top:6px}
  .dc-src a{color:#1f3fbf;text-decoration:none}.dc-src div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .dc-form{display:flex;gap:6px;padding:10px;border-top:1px solid #eef0f5}
  .dc-form input{flex:1;border:1px solid #d4d9e6;border-radius:8px;padding:8px 10px;font:inherit}
  .dc-form button{border:0;border-radius:8px;background:#1f3fbf;color:#fff;padding:0 14px;font-weight:600;cursor:pointer}
  .dc-form button:disabled{opacity:.5}
  .dc-sugg{display:flex;flex-wrap:wrap;gap:6px;padding:0 14px 8px}
  .dc-sugg button{border:1px solid #d4d9e6;background:#fff;border-radius:14px;padding:3px 10px;font-size:12px;cursor:pointer}
  .dc-sep{align-self:center;font-size:11.5px;color:#8a93a5;display:flex;align-items:center;gap:8px;width:100%}
  .dc-sep::before,.dc-sep::after{content:"";flex:1;border-top:1px solid #e3e7ef}`;

  const libs = { marked: null, purify: null };
  async function loadLibs() {
    const load = (src) => new Promise((ok) => {
      const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = ok; document.head.appendChild(s);
    });
    if (!window.marked) await load("https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js");
    if (!window.DOMPurify) await load("https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.1.6/purify.min.js");
    libs.marked = window.marked; libs.purify = window.DOMPurify;
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  // "24~40평", "0~15억" 처럼 범위에 쓰는 ~ 가 마크다운 취소선으로 바뀌지 않게 코드 밖의 ~ 를 이스케이프
  const fixTilde = (md) => md.replace(/(```[\s\S]*?```|`[^`\n]*`)|~/g, (m, code) => code || "\\~");
  function render(md) {
    if (libs.marked && libs.purify) {
      const html = libs.purify.sanitize(libs.marked.parse(fixTilde(md)));
      return html.replace(/<table>/g, '<div class="dc-tw"><table>').replace(/<\/table>/g, "</table></div>");
    }
    return "<p>" + esc(md).replace(/\n/g, "<br>") + "</p>";
  }

  // 짧은 셀(숫자·단위)은 줄바꿈하지 않음: "+34.9%" 가 "+34.9 / %" 로 쪼개지지 않게
  function tidy(el) {
    el.querySelectorAll("td, th").forEach((c) => { if (c.textContent.trim().length <= 12) c.style.whiteSpace = "nowrap"; });
  }

  function mount(opts) {
    const o = Object.assign({ endpoint: "/chat-api/api/chat", getContext: () => ({}), token: "", title: "브리핑 비서",
      suggestions: ["지금 화면 요약해줘", "작년보다 얼마나 올랐어?", "왜 올랐어?"] }, opts || {});
    loadLibs();
    const style = document.createElement("style"); style.textContent = CSS; document.head.appendChild(style);
    const btn = Object.assign(document.createElement("button"), { className: "dc-btn" });
    btn.innerHTML = '<span aria-hidden="true">💬</span><span class="dc-btn-t">물어보기</span>';
    btn.setAttribute("aria-label", "물어보기");
    const panel = document.createElement("div"); panel.className = "dc-panel";
    panel.innerHTML = `<div class="dc-head"><div><b>${esc(o.title)}</b><div class="dc-ctx"></div></div>
      <div class="dc-head-btns"><button class="dc-new" title="새 대화">↺</button><button class="dc-wide" title="크게 보기">⤢</button><button class="dc-x" title="닫기">×</button></div></div>
      <div class="dc-log"></div><div class="dc-sugg"></div>
      <form class="dc-form"><input placeholder="예) 야탑은 왜 이렇게 올랐어?" /><button>보내기</button></form>`;
    document.body.append(btn, panel);
    const log = panel.querySelector(".dc-log"), form = panel.querySelector("form"), input = form.querySelector("input"),
      send = form.querySelector("button"), ctxEl = panel.querySelector(".dc-ctx"), sugg = panel.querySelector(".dc-sugg");
    const history = [];                                   // 화면 표시용 (서버에는 이번 질문만 보냄)
    const base = o.endpoint.replace(/\/api\/chat\/?$/, "");     // 예: /chat-api
    const KEY = "bundang:chatConv";
    let convId = null;
    try { convId = localStorage.getItem(KEY); } catch (e) {}
    const saveConv = (id) => { convId = id; try { id ? localStorage.setItem(KEY, id) : localStorage.removeItem(KEY); } catch (e) {} };
    let restored = false;
    async function restore() {                             // 처음 열 때 한 번: 저장된 대화 불러오기
      if (restored || !convId) return; restored = true;
      try {
        const r = await fetch(`${base}/api/conversations/${convId}`, { headers: o.token ? { "X-Chat-Token": o.token } : {} });
        if (r.status === 404) { saveConv(null); return; }
        if (!r.ok) return;
        const d = await r.json();
        if (!d.messages || !d.messages.length) return;
        await loadLibs();
        sugg.style.display = "none";
        const sep = document.createElement("div"); sep.className = "dc-sep"; sep.textContent = "이전 대화";
        log.appendChild(sep);
        d.messages.forEach((m) => {
          history.push(m);
          const b = bubble(m.role === "user" ? "dc-user" : "dc-bot", m.role === "user" ? esc(m.content) : render(m.content));
          if (m.role !== "user") tidy(b);
        });
        log.scrollTop = log.scrollHeight;
      } catch (e) {}
    }

    const showCtx = () => {
      try {
        const c = o.getContext() || {}, f = c.filter || {};
        const scope = {filt: "필터 조건", area: "평형·방만", all: "전체"}[c.scope] || "";
        const parts = [c.dong && c.dong !== "전체" ? c.dong : "5개 동", scope];
        if (c.scope !== "all" && f.areaMin != null) parts.push(`${f.areaMin}~${f.areaMax}평`);
        if (c.scope === "filt" && f.priceMax != null) parts.push(`${f.priceMin || 0}~${f.priceMax}억`);
        if (c.unit) parts.push(c.unit);
        ctxEl.textContent = "지금 화면: " + parts.filter(Boolean).join(" · ");
      } catch (e) { ctxEl.textContent = ""; }
    };
    // 모바일: 키보드가 올라오면 브라우저의 '보이는 영역'이 줄어드므로 창을 그 영역에 맞춰 닫기 버튼이 가려지지 않게 함
    const mobile = () => window.matchMedia("(max-width:600px)").matches;
    const fit = () => {
      const vv = window.visualViewport;
      if (!mobile() || !vv) { panel.style.top = ""; panel.style.removeProperty("--dc-vh"); return; }
      panel.style.top = vv.offsetTop + "px";
      panel.style.setProperty("--dc-vh", vv.height + "px");
    };
    if (window.visualViewport) { visualViewport.addEventListener("resize", fit); visualViewport.addEventListener("scroll", fit); }
    window.addEventListener("resize", fit);
    const setOpen = (on) => {
      panel.classList.toggle("open", on); document.body.classList.toggle("dc-open", on && mobile());
      if (on) { showCtx(); fit(); restore(); if (!mobile()) input.focus(); }   // 모바일은 바로 키보드를 띄우지 않음
    };
    btn.onclick = () => setOpen(!panel.classList.contains("open"));
    panel.querySelector(".dc-x").onclick = () => setOpen(false);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && panel.classList.contains("open")) setOpen(false); });
    const wideBtn = panel.querySelector(".dc-wide");
    const setWide = (on) => { panel.classList.toggle("wide", on); wideBtn.textContent = on ? "⤡" : "⤢";
      wideBtn.title = on ? "작게 보기" : "크게 보기"; try { localStorage.setItem("bundang:chatWide", on ? "1" : "0"); } catch (e) {} };
    wideBtn.onclick = () => setWide(!panel.classList.contains("wide"));
    try { setWide(localStorage.getItem("bundang:chatWide") === "1"); } catch (e) {}
    panel.querySelector(".dc-new").onclick = () => {
      if (send.disabled) return;
      if (convId) fetch(`${base}/api/conversations/${convId}`, { method: "DELETE", headers: o.token ? { "X-Chat-Token": o.token } : {} }).catch(() => {});
      saveConv(null); restored = true;
      history.length = 0; log.innerHTML = ""; sugg.style.display = "flex"; input.focus();
    };
    o.suggestions.forEach((q) => {
      const b = document.createElement("button"); b.type = "button"; b.textContent = q;
      b.onclick = () => ask(q); sugg.appendChild(b);
    });

    function bubble(cls, html) {
      const d = document.createElement("div"); d.className = "dc-msg " + cls; d.innerHTML = html;
      log.appendChild(d); log.scrollTop = log.scrollHeight; return d;
    }

    async function ask(q) {
      if (!q.trim() || send.disabled) return;
      showCtx(); sugg.style.display = "none";
      history.push({ role: "user", content: q });
      bubble("dc-user", esc(q));
      const bot = bubble("dc-bot", `<div class="dc-steps"></div><div class="dc-body">…</div><div class="dc-src" style="display:none"></div>`);
      const steps = bot.querySelector(".dc-steps"), body = bot.querySelector(".dc-body"), src = bot.querySelector(".dc-src");
      let text = "", sources = [];
      send.disabled = true; input.value = "";
      try {
        const res = await fetch(o.endpoint, {
          method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, o.token ? { "X-Chat-Token": o.token } : {}),
          body: JSON.stringify({ conversation_id: convId, message: q, context: o.getContext() || {} }),
        });
        if (!res.ok || !res.body) throw new Error("HTTP " + res.status);
        const reader = res.body.getReader(), dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read(); if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            const raw = buf.slice(0, i); buf = buf.slice(i + 2);
            const ev = (raw.match(/^event: (.*)$/m) || [])[1], dl = (raw.match(/^data: (.*)$/m) || [])[1];
            if (!ev || !dl) continue;
            const data = JSON.parse(dl);
            if (ev === "meta") { if (data.conversation_id) saveConv(data.conversation_id); }
            else if (ev === "tool") steps.insertAdjacentHTML("beforeend", `<span class="dc-step">${esc(data.label)}</span>`);
            else if (ev === "delta") { text += data.text; body.innerHTML = render(text); tidy(body); }
            else if (ev === "source") sources.push(data);
            else if (ev === "error") body.innerHTML = `<p style="color:#b42318">오류: ${esc(data.message)}</p>`;
          }
          log.scrollTop = log.scrollHeight;
        }
        if (sources.length) {
          src.style.display = "block";
          src.innerHTML = "<b>참고 기사</b>" + sources.slice(0, 6).map((s) =>
            `<div>· <a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a> <span style="color:#6b7385">${esc(s.source || "")} ${esc(s.date || "")}</span></div>`).join("");
        }
        history.push({ role: "assistant", content: text });
      } catch (e) {
        body.innerHTML = `<p style="color:#b42318">연결 오류: ${esc(e.message)}</p>`;
        history.pop();
      } finally {
        send.disabled = false; log.scrollTop = log.scrollHeight;
      }
    }
    form.onsubmit = (e) => { e.preventDefault(); ask(input.value); };
    return { ask, open: () => setOpen(true), close: () => setOpen(false) };
  }
  window.DashboardChat = { mount };
})();
