"""SafeAgent-KR 전체 워크플로 브라우저 데모.

실행:
    ./.venv/bin/python -m uvicorn app.ui:app --host 127.0.0.1 --port 8501
    open http://127.0.0.1:8501

FastAPI + 순수 HTML/JS만 사용한다. Streamlit/Node 설치가 없어도 동작하며,
scripted(키 불요)와 Gemini/OpenAI live 를 같은 화면에서 명확히 구분한다.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from src.agent.cli import SCENARIOS
from src.agent.demo_runtime import DEFENSE_PRESETS, run_workflow_demo

app = FastAPI(title="SafeAgent-KR Workflow Console", version="1.0")


class WorkflowRequest(BaseModel):
    scenario: str = Field(default="poisoned_kb")
    provider_mode: str = Field(default="scripted")
    defense_preset: str = Field(default="tier_a+dlp_vault")
    user_input: str = Field(default="", max_length=8000)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _HTML


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "app": "workflow-console"}


@app.get("/api/meta")
async def meta() -> dict:
    return {
        "scenarios": {
            key: {"input": value[0], "description": value[1]}
            for key, value in SCENARIOS.items()
        },
        "providers": ["scripted", "gemini", "openai"],
        "defense_presets": list(DEFENSE_PRESETS),
    }


@app.post("/api/run")
async def run(req: WorkflowRequest) -> dict:
    try:
        return await run_workflow_demo(
            scenario=req.scenario,
            provider_mode=req.provider_mode,
            defense_preset=req.defense_preset,
            user_input=req.user_input,
        )
    except (ValueError, RuntimeError, NotImplementedError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # provider 429/네트워크 오류를 UI에 읽을 수 있게 전달
        raise HTTPException(
            status_code=502,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc


@app.post("/api/compare")
async def compare(req: WorkflowRequest) -> dict:
    """발표용 3-arm 대비는 API 비용·비결정성을 피하려 scripted 로만 실행한다."""
    rows = []
    try:
        for preset in ("none", "tier_a", "tier_a+dlp_vault"):
            rows.append(await run_workflow_demo(
                scenario=req.scenario,
                provider_mode="scripted",
                defense_preset=preset,
                user_input=req.user_input,
            ))
    except (ValueError, RuntimeError, NotImplementedError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "notice": "동일한 고정 도구 호출을 3개 방어 구성에 재생한 런타임 비교입니다.",
        "runs": rows,
    }


_HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SafeAgent-KR Workflow Console</title>
<style>
:root{
  --bg:#08111f;--panel:#101c2d;--panel2:#13243a;--line:#2a3d55;--text:#e8eef6;
  --muted:#9bb0c7;--green:#40d685;--red:#ff6b6b;--yellow:#ffc857;--blue:#65a9ff;
  --purple:#b89cff;
}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(135deg,#07101d,#0c1728 55%,#0b1322);
  color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Noto Sans KR","Malgun Gothic",sans-serif}
button,select,textarea{font:inherit}
.shell{display:grid;grid-template-columns:330px 1fr;min-height:100vh}
aside{border-right:1px solid var(--line);padding:24px;background:rgba(8,17,31,.94);
  position:sticky;top:0;height:100vh;overflow:auto}
main{padding:28px;max-width:1500px;width:100%;margin:0 auto}
h1{font-size:25px;margin:0 0 8px}.sub{color:var(--muted);font-size:14px;line-height:1.55}
label{display:block;margin:20px 0 7px;color:#c8d7e7;font-size:13px;font-weight:700}
select,textarea{width:100%;border:1px solid var(--line);border-radius:9px;background:#0c1727;
  color:var(--text);padding:10px} textarea{min-height:128px;resize:vertical;line-height:1.5}
.btns{display:grid;gap:9px;margin-top:18px}
button{border:0;border-radius:9px;padding:11px 14px;font-weight:800;cursor:pointer}
.primary{background:var(--green);color:#06160d}.secondary{background:#223a57;color:#dcecff}
button:disabled{opacity:.5;cursor:wait}
.hint{border:1px solid #594b20;background:#2a2411;color:#ffe39a;border-radius:9px;
  padding:10px;margin-top:18px;font-size:12px;line-height:1.5}
.top{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;margin-bottom:20px}
.status{color:var(--muted);font-family:ui-monospace,monospace;font-size:13px}
.notice{border-left:4px solid var(--yellow);background:#2c2513;padding:12px 15px;border-radius:8px;
  margin:12px 0;color:#ffe9ae}.warn{border-left-color:var(--red);background:#2e151c;color:#ffc4c4}
.grid{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:10px;margin:14px 0 22px}
.metric,.panel,.turn,.compare-card{border:1px solid var(--line);background:rgba(16,28,45,.93);
  border-radius:12px}.metric{padding:13px}.metric b{display:block;font-size:22px;margin-top:4px}
.metric span{color:var(--muted);font-size:12px}
.panel{padding:16px;margin:13px 0}.panel h2,.turn h2{font-size:16px;margin:0 0 12px}
.tag{display:inline-block;border:1px solid var(--line);background:#152a43;border-radius:999px;
  padding:3px 9px;font-size:12px;color:#cfe5ff;margin:2px}
.tag.good{border-color:#226843;color:#8aefb8;background:#102b20}
.tag.bad{border-color:#71323b;color:#ffb0b0;background:#32171c}
.tag.yellow{border-color:#675522;color:#ffe08a;background:#2a2514}
.turn{margin:14px 0;overflow:hidden}.turn-head{display:flex;justify-content:space-between;
  align-items:center;padding:13px 16px;background:var(--panel2);border-bottom:1px solid var(--line)}
.turn-body{padding:15px}.stage{border-left:3px solid var(--blue);padding:8px 12px;margin:9px 0}
.stage.model{border-left-color:var(--purple)}.stage.gate{border-left-color:var(--yellow)}
.stage.tool{border-left-color:var(--green)}.stage.block{border-left-color:var(--red)}
.stage-title{font-size:11px;font-weight:900;letter-spacing:.06em;color:var(--muted);margin-bottom:5px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:5px 0 0;background:#08111d;border-radius:8px;
  padding:10px;color:#dce9f7;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
.action{border:1px solid var(--line);border-radius:10px;padding:12px;margin:10px 0}
.action.allow{border-color:#2b704c}.action.blocked{border-color:#7b3540;background:#25151c}
.action-title{display:flex;justify-content:space-between;gap:12px;align-items:center;font-weight:800}
.flowline{color:var(--muted);font-size:12px;margin:7px 0}
.compare{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.compare-card{padding:15px}.compare-card h3{margin:0 0 10px}.mini-hop{padding:7px 0;border-top:1px solid var(--line);
  font-size:12px}.final{color:#bce7d0;line-height:1.6}
.empty{text-align:center;color:var(--muted);padding:100px 20px}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid #47617d;border-top-color:var(--green);
  border-radius:50%;animation:spin .8s linear infinite;vertical-align:-2px}@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:900px){.shell{display:block}aside{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line)}
  .grid{grid-template-columns:repeat(2,1fr)}.compare{grid-template-columns:1fr}main{padding:18px}}
</style>
</head>
<body>
<div class="shell">
<aside>
  <h1>SafeAgent-KR</h1>
  <div class="sub">Agent Workflow Console<br>입력부터 모델·도구·DLP·게이트·결과까지</div>
  <label for="scenario">시나리오</label><select id="scenario"></select>
  <label for="provider">Provider</label>
  <select id="provider">
    <option value="scripted">Scripted — 키 불요 고정 재생</option>
    <option value="gemini">Gemini — Live API</option>
    <option value="openai">OpenAI — Live API</option>
  </select>
  <label for="defense">방어 구성</label><select id="defense"></select>
  <label for="input">사용자 입력</label><textarea id="input"></textarea>
  <div class="btns">
    <button id="run" class="primary">▶ 전체 워크플로 실행</button>
    <button id="compare" class="secondary">▦ 3개 Arm 비교</button>
  </div>
  <div class="hint"><b>정직성 표시</b><br>Scripted는 모델이 판단한 결과가 아니라 고정 호출을 실제
  런타임에 재생합니다. Gemini/OpenAI를 선택해야 실제 모델의 도구 선택을 관측할 수 있습니다.</div>
</aside>
<main>
  <div class="top"><div><h1>실행 타임라인</h1><div class="sub">before_model → model → after_model →
  before_tool → tool → after_tool → model</div></div><div id="status" class="status">준비</div></div>
  <div id="out" class="empty">왼쪽에서 설정을 선택하고 전체 워크플로를 실행하세요.</div>
</main>
</div>
<script>
const $=id=>document.getElementById(id);
let META={};
const j=v=>JSON.stringify(v,null,2);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
const pre=v=>`<pre>${esc(typeof v==="string"?v:j(v))}</pre>`;

async function init(){
  META=await (await fetch("/api/meta")).json();
  $("scenario").innerHTML=Object.entries(META.scenarios).map(([k,v])=>
    `<option value="${esc(k)}">${esc(k)} — ${esc(v.description.slice(0,34))}</option>`).join("");
  $("defense").innerHTML=META.defense_presets.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join("");
  $("defense").value="tier_a+dlp_vault";
  setInput();
}
function setInput(){$("input").value=META.scenarios[$("scenario").value]?.input||""}
$("scenario").addEventListener("change",setInput);

function payload(){return {scenario:$("scenario").value,provider_mode:$("provider").value,
  defense_preset:$("defense").value,user_input:$("input").value}}
function busy(on,label="실행 중"){
  $("run").disabled=on;$("compare").disabled=on;
  $("status").innerHTML=on?`<span class="spinner"></span> ${esc(label)}`:"완료";
}
async function call(path,body){
  const r=await fetch(path,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body)});
  const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==="string"?data.detail:j(data.detail));return data;
}
$("run").onclick=async()=>{busy(true,"에이전트 실행 중");try{renderRun(await call("/api/run",payload()))}
  catch(e){renderError(e)}finally{busy(false)}};
$("compare").onclick=async()=>{busy(true,"3개 arm 실행 중");try{renderCompare(await call("/api/compare",payload()))}
  catch(e){renderError(e)}finally{busy(false)}};

function renderRun(d){
  const m=d.meta,s=d.summary,inp=d.input;
  let h=`${m.scripted?`<div class="notice"><b>Scripted 고정 재생</b> — ${esc(m.scripted_notice)}</div>`:""}
  ${m.rail_fallback?`<div class="notice warn"><b>Rail fallback</b> — 학습 아티팩트를 불러오지 못해 heuristic이 사용됐습니다. .venv 환경을 확인하세요.</div>`:""}
  <div class="panel"><span class="tag">${esc(m.provider)}</span><span class="tag">${esc(m.model)}</span>
  <span class="tag">${esc(m.rail)}</span>${m.defenses.map(x=>`<span class="tag good">${esc(x)}</span>`).join("")}
  <div class="sub" style="margin-top:8px">${esc(m.scenario_description)}</div></div>
  <div class="grid">${metric("Finish",s.finish_reason)}${metric("Hops",s.hops)}${metric("Attempted",s.attempted)}
  ${metric("Executed",s.executed)}${metric("Blocked",s.blocked)}</div>
  <div class="panel"><h2>① 입력과 before_model</h2><div class="stage"><div class="stage-title">ORIGINAL INPUT</div>${pre(inp.original)}</div>
  <div class="stage model"><div class="stage-title">PROVIDER-VISIBLE INPUT ${inp.changed_by_before_model?"· DLP/방어로 변경됨":""}</div>
  ${pre(inp.provider_visible)}</div></div>`;
  d.turns.forEach(t=>h+=turnHtml(t,m));
  h+=`<div class="panel"><h2>최종 결과</h2><div class="final">${esc(s.final_text||"(최종 텍스트 없음)")}</div>
  <div class="flowline">finish=${esc(s.finish_reason)} · latency=${esc(s.latency_ms)}ms
  ${s.pending_approval?` · pending=${esc(s.pending_approval)}`:""}</div></div>`;
  if(d.audit&&Object.keys(d.audit).length)h+=`<div class="panel"><h2>서비스 백엔드 감사 상태</h2>${pre(d.audit)}</div>`;
  if(d.hitl?.pending?.length)h+=`<div class="panel"><h2>HITL 승인 대기</h2>${pre(d.hitl.pending)}</div>`;
  $("out").className="";$("out").innerHTML=h;
}
function metric(k,v){return `<div class="metric"><span>${esc(k)}</span><b>${esc(v)}</b></div>`}
function turnHtml(t,m){
  const latest=t.latest_input||{};let h=`<div class="turn"><div class="turn-head"><b>TURN ${t.turn}</b>
  <span class="tag">${m.scripted?"SCRIPTED OUTPUT":"LIVE MODEL"}</span></div><div class="turn-body">`;
  if(latest.role==="tool"){
    h+=`<div class="stage"><div class="stage-title">MODEL INPUT · 이전 도구 결과</div>${pre(latest.tool_results)}</div>`;
  }else h+=`<div class="stage"><div class="stage-title">MODEL INPUT · ${esc(latest.role)}</div>${pre(latest.text)}</div>`;
  const o=t.model_output||{};
  if(o.tool_calls?.length){
    h+=`<div class="stage model"><div class="stage-title">${m.scripted?"SCRIPTED ACTION · 모델 판단 아님":"MODEL TOOL_CALL"}</div>${pre(o.tool_calls)}</div>`;
    t.actions.forEach(a=>h+=actionHtml(a));
  }else h+=`<div class="stage model"><div class="stage-title">MODEL FINAL RESPONSE</div>${pre(o.text||"")}</div>`;
  if(t.provider_error)h+=`<div class="stage block"><div class="stage-title">PROVIDER ERROR</div>${pre(t.provider_error)}</div>`;
  return h+`</div></div>`;
}
function actionHtml(a){
  const blocked=!a.executed;let h=`<div class="action ${blocked?"blocked":"allow"}"><div class="action-title">
  <span>${esc(a.tool)}</span><span class="tag ${blocked?"bad":"good"}">${blocked?"🛑 "+esc(a.decision):"✅ ALLOW · 실행"}</span></div>
  <div class="flowline">MODEL TOOL_CALL → before_tool → ${blocked?"BLOCK":"TOOL EXECUTE"} → after_tool → MODEL</div>
  <div class="stage gate ${blocked?"block":""}"><div class="stage-title">BEFORE_TOOL · 결정적 정책 게이트</div>
  ${pre({decision:a.decision,rule:a.rule||null,reason:a.reason||null,args:a.args})}</div>`;
  if(a.raw_tool_result!==null)h+=`<div class="stage tool"><div class="stage-title">TOOL RAW RESULT · 합성 로컬 데이터</div>${pre(a.raw_tool_result)}</div>`;
  if(a.model_visible_result!==null)h+=`<div class="stage model"><div class="stage-title">AFTER_TOOL → MODEL · DLP/TAINT 처리 후</div>${pre(a.model_visible_result)}</div>`;
  if(a.dlp_tokens?.length)h+=`<span class="tag yellow">DLP ${esc(a.dlp_tokens.join(", "))}</span>`;
  Object.entries(a.rail_scores||{}).forEach(([k,v])=>h+=`<span class="tag">LOG-ONLY ${esc(k)}=${Number(v).toFixed(4)}</span>`);
  return h+"</div>";
}
function renderCompare(data){
  let h=`<div class="notice">${esc(data.notice)}</div><div class="compare">`;
  data.runs.forEach(d=>{const s=d.summary,m=d.meta;h+=`<div class="compare-card"><h3>${esc(m.defense_preset)}</h3>
    <div><span class="tag">${esc(m.rail)}</span><span class="tag ${s.blocked?"bad":"good"}">blocked=${s.blocked}</span></div>
    <p class="sub">attempted=${s.attempted} · executed=${s.executed} · finish=${esc(s.finish_reason)}</p>`;
    d.turns.flatMap(t=>t.actions).forEach(a=>h+=`<div class="mini-hop"><b>${esc(a.tool)}</b><br>
      ${a.executed?"✅ 실행":"🛑 "+esc(a.rule||a.decision)}</div>`);
    h+=`<p class="final">${esc(s.final_text)}</p></div>`});h+="</div>";
  $("out").className="";$("out").innerHTML=h;
}
function renderError(e){$("out").className="";$("out").innerHTML=`<div class="notice warn"><b>실행 실패</b><br>${esc(e.message)}</div>`}
init().then(()=>{
  if(new URLSearchParams(location.search).get("autorun")==="1")$("run").click();
}).catch(renderError);
</script>
</body></html>"""
