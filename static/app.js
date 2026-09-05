let DATA=null, stageIdx=0, scores={}, conditions={}, history=[], chosen=null, currentEvent=null, fromView='landing';
let scenario={}, actorMemory={}, currentDeliberation=null, runSeed=0, stageEventCache={}, customResponse='', customImpactApplied=false;
let runMode='normal';
let currentRunId='', currentMemoryStyle='实际', currentReportData=null, currentRunSaved=false;
const $=id=>document.getElementById(id);
const clamp=v=>Math.max(0,Math.min(100,Math.round(Number(v)||0)));
const signed=v=>`${v>0?'+':''}${v}`;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clone=o=>JSON.parse(JSON.stringify(o));
const roleById=id=>DATA?.roles?.find(r=>r.id===id);
const roleName=id=>roleById(id)?.name||id;
const roleIcon=id=>roleById(id)?.icon||'◉';
const radarLabels={asset:'资产',innovation:'创新',collaboration:'协同',openness:'开放',public_value:'公众'};
let aiHealthMode='mock';

async function init(){
  DATA=await fetch('/api/case').then(r=>r.json());
  const health=await fetch('/api/health').then(r=>r.json());
  aiHealthMode=health.mode||'mock';
  $('aiStatus').textContent=health.ai_enabled?`AI Ping 流式已连接 · ${health.model}`:'本地演示模式 · 添加Key即可启用AI流式生成';
  $('aiStatus').style.color=health.ai_enabled?'#71f6b4':'#ffcf6b';
  if($('qaModeTag')) $('qaModeTag').textContent=health.ai_enabled?`AI Ping + 本地资料库 · ${health.model}`:'本地资料库讲解模式';
  DATA.scenario_controls.forEach(c=>scenario[c.id]=c.default);
  resetActorMemory();
  resetStateFromScenario();
  renderScenarioControls(); bindModeSelector();
  renderSteps(); renderScores(); renderConditions(); renderMemoryPanel(); renderKnowledge(); renderMemoryWall();
}

function resetActorMemory(){
  actorMemory={};
  DATA.roles.forEach(r=>actorMemory[r.id]=clone(r.memory_seed||{trust:55,commitments:[],conditions:[],concerns:[r.concern],last_support:3,last_position:'',events_seen:0}));
}
function scenarioLabel(control){
  const v=scenario[control.id]||control.default||2;
  return control.labels[Math.max(0,Math.min(control.labels.length-1,v-1))];
}
function setRunMode(mode){
  runMode=mode==='free'?'free':'normal';
  document.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('active',b.dataset.mode===runMode));
  if($('modeChip')) $('modeChip').textContent=runMode==='normal'?'普通模式 · 15张案例卡':'自由模式 · 50张事件卡';
  if($('startBtn')) $('startBtn').textContent=runMode==='normal'?'以普通模式开始推演 →':'以自由模式开始推演 →';
  if($('randomChip')) $('randomChip').textContent=runMode==='normal'?'普通模式关闭随机特殊事件':'自由模式启用时间随机种子';
  stageEventCache={};
}
function bindModeSelector(){
  document.querySelectorAll('[data-mode]').forEach(btn=>btn.addEventListener('click',()=>setRunMode(btn.dataset.mode)));
  setRunMode(runMode);
}
function renderScenarioControls(){
  $('scenarioControls').innerHTML=DATA.scenario_controls.map(c=>`
    <div class="scenario-control">
      <div class="scenario-control-head"><span>${esc(c.name)}</span><b id="scenarioLabel-${esc(c.id)}">${esc(scenarioLabel(c))}</b></div>
      <input type="range" min="1" max="3" step="1" value="${scenario[c.id]}" data-scenario="${esc(c.id)}" />
      <div class="range-labels">${c.labels.map(x=>`<span>${esc(x)}</span>`).join('')}</div>
      <p>${esc(c.desc)}</p>
    </div>`).join('');
  document.querySelectorAll('[data-scenario]').forEach(input=>input.addEventListener('input',()=>{
    scenario[input.dataset.scenario]=Number(input.value);
    const c=DATA.scenario_controls.find(x=>x.id===input.dataset.scenario);
    $(`scenarioLabel-${c.id}`).textContent=scenarioLabel(c);
    resetStateFromScenario();
  }));
}
function resetStateFromScenario(){
  scores={...DATA.initial_scores}; conditions={...DATA.initial_conditions};
  const growth=scenario.growth||2, capital=scenario.capital_pressure||2, openness=scenario.openness_demand||2, coord=scenario.coordination_difficulty||2;
  conditions.street_capacity=clamp(conditions.street_capacity-(growth-2)*10);
  conditions.execution_capacity=clamp(conditions.execution_capacity-(growth-2)*3-(coord-2)*10);
  conditions.capital_resilience=clamp(conditions.capital_resilience-(capital-2)*12);
  conditions.public_trust=clamp(conditions.public_trust-(openness-2)*6);
  conditions.tenant_stability=clamp(conditions.tenant_stability-(coord-2)*4);
  if($('scoreList')) renderScores(); if($('conditionList')) renderConditions();
}
function renderSteps(){
  if(!DATA) return;
  $('stepList').innerHTML=DATA.stages.map((s,i)=>`<div class="step ${i===stageIdx?'active':''} ${i<stageIdx?'done':''}">${String(i+1).padStart(2,'0')} · ${esc(s.label)}</div>`).join('');
}
function renderScores(){
  $('scoreList').innerHTML=Object.entries(DATA.metrics).map(([k,name])=>`<div class="metric"><div class="metric-head"><span>${esc(name)}</span><b>${scores[k]}</b></div><div class="metric-bar"><i style="width:${scores[k]}%"></i></div></div>`).join('');
}
function renderConditions(){
  $('conditionList').innerHTML=Object.entries(DATA.condition_metrics).map(([k,m])=>`<div class="condition-metric" title="${esc(m.hint)}"><div class="metric-head"><span>${esc(m.name)}</span><b>${conditions[k]}</b></div><div class="condition-bar"><i style="width:${conditions[k]}%"></i></div></div>`).join('');
}
function renderMemoryPanel(){
  if(!$('memoryList')) return;
  $('memoryList').innerHTML=DATA.roles.map(r=>{
    const m=actorMemory[r.id]||{};
    const commit=(m.commitments||[]).slice(-1)[0]||'尚无跨题承诺';
    const cond=(m.conditions||[]).slice(-1)[0]||'尚无支持条件';
    return `<details class="memory-item"><summary><span>${r.icon} ${esc(r.name)}</span><b>信任 ${clamp(m.trust??55)}</b></summary><div class="memory-body"><p><em>最近承诺</em>${esc(commit)}</p><p><em>支持条件</em>${esc(cond)}</p><small>上轮支持度 ${Math.max(1,Math.min(5,Number(m.last_support)||3))}/5 · 已参与 ${Number(m.events_seen)||0} 轮</small></div></details>`;
  }).join('');
}
function renderMemoryLedger(){
  $('memoryLedger').innerHTML=DATA.roles.map(r=>{
    const m=actorMemory[r.id]||{};
    const commitments=(m.commitments||[]).slice(-3);
    const conds=(m.conditions||[]).slice(-3);
    return `<article class="memory-ledger-item"><div class="ledger-head"><span>${r.icon}</span><div><b>${esc(r.name)}</b><small>信任 ${clamp(m.trust??55)} · 支持 ${Math.max(1,Math.min(5,Number(m.last_support)||3))}/5</small></div></div><div class="ledger-section"><em>仍保留的承诺</em>${commitments.length?`<ul>${commitments.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:'<p>暂无</p>'}</div><div class="ledger-section"><em>仍保留的支持条件</em>${conds.length?`<ul>${conds.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:'<p>暂无</p>'}</div></article>`;
  }).join('');
}
function getStateValue(path){
  const [root,key]=path.split('.');
  if(root==='scores') return scores[key];
  if(root==='conditions') return conditions[key];
  if(root==='scenario') return scenario[key];
  return undefined;
}
function testRule(rule){
  const v=getStateValue(rule.path), x=rule.value;
  if(rule.op==='lt') return v<x; if(rule.op==='lte') return v<=x; if(rule.op==='gt') return v>x; if(rule.op==='gte') return v>=x; if(rule.op==='eq') return v===x; return false;
}
function eventMatches(e){
  if(!e.when || !e.when.length) return true;
  return e.when_match==='all'?e.when.every(testRule):e.when.some(testRule);
}
function seededRandom(seed){
  let x=(Number(seed)||1)>>>0; x^=x<<13; x^=x>>>17; x^=x<<5; return ((x>>>0)%1000000)/1000000;
}
function stageRandom(offset=0){ return seededRandom((runSeed + (stageIdx+1)*2654435761 + offset*1013904223)>>>0); }
function weightedPick(items, offset=0){
  if(!items.length) return null;
  const total=items.reduce((a,e)=>a+Math.max(.1,Number(e.random_weight)||1),0); let r=stageRandom(offset)*total;
  for(const e of items){r-=Math.max(.1,Number(e.random_weight)||1); if(r<=0) return e;} return items[items.length-1];
}
function selectEventForStage(){
  if(stageEventCache[stageIdx]) return stageEventCache[stageIdx];
  const stage=DATA.stages[stageIdx];
  let candidates=stage.candidates.map(id=>DATA.events.find(e=>e.id===id)).filter(Boolean);

  // 普通模式：严格排除35张随机情景卡，只在15张原有案例卡中按状态进入对应分支。
  if(runMode==='normal'){
    candidates=candidates.filter(e=>!e.special);
    const eligible=candidates.filter(eventMatches);
    const triggered=eligible.filter(e=>e.when?.length && eventMatches(e));
    let picked=triggered[0] || eligible.find(e=>!e.when?.length) || eligible[0] || candidates[0];
    picked={...picked,_draw_reason:triggered.length?'案例状态分支':'真实案例主线',_stage_random:null};
    stageEventCache[stageIdx]=picked;
    return picked;
  }

  // 自由模式：沿用时间种子 + 状态触发 + 随机特殊事件。
  const eligible=candidates.filter(eventMatches);
  const triggered=eligible.filter(e=>e.when?.length && eventMatches(e));
  const specials=eligible.filter(e=>e.special);
  const normals=eligible.filter(e=>!e.special);
  const baseChance=Number(DATA.random_engine?.special_base_chance)||.62;
  const difficulty=((scenario.coordination_difficulty||2)-2)*.05 + ((scenario.capital_pressure||2)-2)*.04;
  const specialChance=Math.max(.35,Math.min(.82,baseChance+difficulty));
  let picked=null, reason='';
  if(triggered.length && stageRandom(11)<.58){picked=weightedPick(triggered,12); reason='状态条件触发';}
  else if(specials.length && stageRandom(21)<specialChance){picked=weightedPick(specials,22); reason='时间随机特殊事件';}
  else {picked=weightedPick(normals.length?normals:eligible,31)||candidates[0]; reason=picked?.branch_note?'状态分支':'案例/常规节点';}
  picked={...picked,_draw_reason:reason,_stage_random:stageRandom(99)};
  stageEventCache[stageIdx]=picked; return picked;
}
function showGame(){
  stageIdx=0; history=[]; chosen=null; currentDeliberation=null; customResponse=''; customImpactApplied=false; currentReportData=null; currentRunSaved=false;
  currentRunId=`run-${Date.now()}-${Math.random().toString(36).slice(2,7)}`;
  const styles=['文艺','实际','官方']; currentMemoryStyle=styles[Math.floor(Math.random()*styles.length)];
  const seedParam=Number(new URLSearchParams(location.search).get('seed'));
  runSeed=runMode==='free'?((Number.isFinite(seedParam)&&seedParam>0)?Math.floor(seedParam):Number(String(Date.now()).slice(-9))):0; stageEventCache={};
  resetActorMemory(); resetStateFromScenario(); currentEvent=selectEventForStage();
  $('knowledge').classList.add('hidden'); $('landing').classList.add('hidden'); $('game').classList.remove('hidden'); $('reportView').classList.add('hidden'); $('challengeView').classList.add('hidden'); $('eventView').classList.remove('hidden');
  renderEvent(); renderMemoryPanel();
}
function renderEvent(){
  chosen=null; customResponse=''; customImpactApplied=false; currentDeliberation=null; currentEvent=selectEventForStage(); const e=currentEvent;
  $('progressText').textContent=`${stageIdx+1} / ${DATA.stages.length}`; $('progressBar').style.width=`${((stageIdx+1)/DATA.stages.length)*100}%`;
  if($('seedInfo')) $('seedInfo').textContent=runMode==='normal'?`普通模式 · 仅案例卡 · ${e._draw_reason||'真实案例'} · 卡 #${e.id}`:`自由模式 · 本局种子 ${runSeed} · ${e._draw_reason||'常规节点'} · 卡 #${e.id}/50`;
  renderSteps(); renderScores(); renderConditions(); renderMemoryPanel();
  $('eventChapter').textContent=e.chapter; $('eventTitle').textContent=e.title; $('eventSituation').textContent=e.situation; $('eventFact').textContent=e.fact; $('eventQuestion').textContent=e.question; $('sourceSection').textContent=runMode==='normal'?`${e.source_section||'团队案例材料'} · 普通模式：仅案例卡`:`${e.source_section||'团队案例材料'} · 抽取方式：${e._draw_reason||'常规节点'}`;
  const isBranch=!!e.branch_note, isSpecial=!!e.special;
  if(runMode==='normal'){
    $('routeTag').textContent=isBranch?'真实案例分支':'真实案例主线';
    $('routeTag').className=`route-tag ${isBranch?'branch':''}`;
    if(isBranch){$('branchBanner').textContent=`▣ 案例路径：${e.branch_note||'根据前序治理状态进入该案例分支。'}`; $('branchBanner').classList.remove('hidden');}else{$('branchBanner').classList.add('hidden');}
  }else{
    $('routeTag').textContent=isSpecial?'随机特殊事件':isBranch?'状态触发支线':(e.event_type||'案例主线节点');
    $('routeTag').className=`route-tag ${isSpecial?'special':isBranch?'branch':''}`;
    if(isBranch||isSpecial){$('branchBanner').textContent=`${isSpecial?'🎲':'⚡'} ${isSpecial?'时间随机卡：':'情景触发：'}${e.branch_note||'本局时间种子从当前阶段候选卡中抽中了这张模拟事件。'}`; $('branchBanner').classList.remove('hidden');}else{$('branchBanner').classList.add('hidden');}
  }
  const fixed=e.options.map(o=>`<button class="option" data-choice="${esc(o.id)}"><b>${esc(o.id)}</b><strong>${esc(o.label)}</strong><p>${esc(o.desc)}</p></button>`).join('');
  const free=`<button class="option free-option" data-choice="D"><b>D</b><strong>自由回答</strong><p>不受预设方案限制，输入你自己的治理策略，再让五角色代理围绕你的方案进行完整群聊。</p></button>`;
  $('options').innerHTML=fixed+free+`<div id="freeAnswerBox" class="free-answer-box hidden"><label>输入你的自定义治理方案</label><textarea id="freeAnswerInput" maxlength="1200" placeholder="例如：我会先保留核心公共空间，同时成立一个由集体、运营方、企业和居民代表组成的小组，用三个月试点检验……"></textarea><div><small>自由回答没有预设分值；协商完成后，系统会根据文本语义生成克制的模拟状态变化。</small><button id="freeAnswerSubmit" class="primary">提交自由方案并召集五方</button></div></div>`;
  document.querySelectorAll('.option').forEach(b=>b.addEventListener('click',()=>{
    if(b.dataset.choice==='D'){$('freeAnswerBox').classList.toggle('hidden'); setTimeout(()=>$('freeAnswerInput')?.focus(),40);} else selectChoice(b.dataset.choice);
  }));
  $('freeAnswerSubmit').addEventListener('click',()=>{const text=$('freeAnswerInput').value.trim(); if(text.length<4){$('freeAnswerInput').focus(); $('freeAnswerInput').classList.add('input-error'); return;} $('freeAnswerInput').classList.remove('input-error'); selectChoice('D',text);});
  $('afterChoice').classList.add('hidden'); $('deliberationTimeline').innerHTML=''; $('consensusBox').classList.add('hidden'); $('impactBox').innerHTML=''; $('aiLoading').classList.add('hidden');
}
function applyDelta(target,delta){Object.entries(delta||{}).forEach(([k,v])=>target[k]=clamp((target[k]||0)+v));}
function renderImpact(o){
  $('impactBox').classList.remove('free-impact');
  const values=Object.entries(o.delta||{}).filter(([,v])=>v!==0).map(([k,v])=>`<span class="impact-chip ${v>0?'plus':'minus'}">${esc(DATA.metrics[k])} ${signed(v)}</span>`).join('');
  const conds=Object.entries(o.condition_delta||{}).filter(([,v])=>v!==0).map(([k,v])=>`<span class="impact-chip condition ${v>0?'plus':'minus'}">${esc(DATA.condition_metrics[k]?.name||k)} ${signed(v)}</span>`).join('');
  $('impactBox').innerHTML=`<div><b>本轮状态变化</b><small>情景指数仅用于推演机制</small></div><div class="impact-chips">${values}${conds}</div>`;
}

async function selectChoice(choice, freeText=''){
  if(chosen) return;
  chosen=choice; customResponse=choice==='D'?String(freeText||'').trim():''; const e=currentEvent;
  const o=choice==='D'?{id:'D',label:'自由回答',desc:customResponse,delta:{},condition_delta:{}}:e.options.find(x=>x.id===choice);
  const memoryBefore=clone(actorMemory);
  if(choice!=='D'){applyDelta(scores,o.delta); applyDelta(conditions,o.condition_delta); renderScores(); renderConditions(); renderImpact(o);} else {
    $('impactBox').classList.add('free-impact');
    $('impactBox').innerHTML=`<div class="impact-label"><b>自由策略</b><small>等待五方协商后的语义评估</small></div><div class="free-impact-summary"><span>已提交自定义治理方案</span><details><summary>查看原文</summary><p>“${esc(customResponse)}”</p></details></div>`;
  }
  document.querySelectorAll('.option').forEach(b=>{b.classList.add('disabled'); if(b.dataset.choice===choice)b.classList.add('selected')});
  if($('freeAnswerBox')) $('freeAnswerBox').classList.add('hidden');
  if(choice==='D'){$('caseBadge').textContent='✎ 自由策略 · 情景推演'; $('caseResult').textContent='你的方案没有预设“正确答案”。系统会让五类主体充分质询，并在协商后给出仅用于沙盘推进的语义状态变化。';}
  else if(e.case_choice){$('caseBadge').textContent=choice===e.case_choice?'✓ 与案例实际路径一致':'案例实际路径 / 对照'; $('caseResult').textContent=e.case_result;}
  else {$('caseBadge').textContent='🎲 随机情景卡 · 无唯一答案'; $('caseResult').textContent=e.case_result;}
  $('afterChoice').classList.remove('hidden');
  history.push({event_id:e.id,event_type:e.event_type||'',title:e.title,stage:stageIdx+1,mode:runMode,seed:runSeed,draw_reason:e._draw_reason||'',choice,choice_label:o.label,custom_response:customResponse,case_choice:e.case_choice,delta:{...(o.delta||{})},condition_delta:{...(o.condition_delta||{})},branch_note:e.branch_note||'',scores_after:{...scores},conditions_after:{...conditions},actor_memory_before:memoryBefore});
  $('nextBtn').disabled=true; $('aiLoading').classList.remove('hidden'); $('streamStatusText').textContent='五角色代理群聊已建立，首个主体会立即开始输出…';
  $('deliberationTimeline').innerHTML=''; $('consensusBox').classList.add('hidden');
  currentDeliberation={rounds:{},summary:null};
  try{
    await streamPost('/api/deliberate/stream',{event_id:e.id,choice,custom_response:customResponse,scores,conditions,scenario,actor_memory:actorMemory},handleDeliberationItem);
  }catch(err){
    $('streamStatusText').textContent='协商流中断';
    $('deliberationTimeline').insertAdjacentHTML('beforeend',`<div class="round-section error-card"><p>${esc(String(err))}</p></div>`);
  } finally {
    $('aiLoading').classList.add('hidden'); $('nextBtn').disabled=false;
  }
}

async function streamPost(url,payload,onItem){
  const res=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','Accept':'text/event-stream'},body:JSON.stringify(payload)});
  if(!res.ok) throw new Error(`HTTP ${res.status}`);
  if(!res.body) throw new Error('当前浏览器不支持流式读取');
  const reader=res.body.getReader(), decoder=new TextDecoder('utf-8');
  let buffer='';
  while(true){
    const {value,done}=await reader.read();
    if(value) buffer+=decoder.decode(value,{stream:true});
    let boundary;
    while((boundary=buffer.indexOf('\n\n'))>=0){
      const block=buffer.slice(0,boundary); buffer=buffer.slice(boundary+2);
      const dataLines=block.split('\n').filter(x=>x.startsWith('data:')).map(x=>x.slice(5).trim());
      if(!dataLines.length) continue;
      try{ await onItem(JSON.parse(dataLines.join('\n'))); }catch(e){ console.warn('stream item parse/render error',e); }
    }
    if(done) break;
  }
}

function ensureRound(round,title='',goal='',target='deliberationTimeline',finalRound=5){
  const prefix=target==='challengeResult'?'ch':'ev';
  let section=$(`${prefix}-round-${round}`);
  if(!section){
    section=document.createElement('section'); section.className='round-section streaming-round'; section.id=`${prefix}-round-${round}`;
    const head=document.createElement('div'); head.className='round-head';
    const left=document.createElement('div'); const roundTag=document.createElement('span'); roundTag.textContent=`ROUND ${round}`; const b=document.createElement('b'); b.textContent=title||`第${round}轮`; left.append(roundTag,b);
    const p=document.createElement('p'); p.textContent=goal||''; head.append(left,p);
    const list=document.createElement('div'); list.className='dialogue-list'; list.id=`${prefix}-round-list-${round}`;
    DATA.roles.forEach(r=>list.appendChild(createPlaceholderRow(prefix,round,r,round===finalRound)));
    section.append(head,list);
    $(target).appendChild(section);
  } else {
    if(title) section.querySelector('.round-head b').textContent=title;
    if(goal) section.querySelector('.round-head p').textContent=goal;
  }
  return section;
}
function createPlaceholderRow(prefix,round,role,isFinal){
  const row=document.createElement('div'); row.className=`dialogue-row pending ${isFinal?'final-answer':''}`; row.id=`${prefix}-msg-${round}-${role.id}`;
  row.innerHTML=`<div class="dialogue-who"><div class="avatar">${role.icon}</div><div><b>${esc(role.name)}</b><small class="support-line">等待发言 ···</small></div></div><div class="dialogue-bubble"><p class="message-text"><span class="skeleton-line"></span></p><div class="dialogue-meta"></div></div>`;
  return row;
}
function typeText(el,text){
  const value=String(text||''); el.textContent='';
  const total=value.length; if(total===0) return;
  const step=Math.max(1,Math.ceil(total/70)); let i=0;
  const tick=()=>{ i=Math.min(total,i+step); el.textContent=value.slice(0,i); if(i<total) requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
}
function radarSVG(values){
  const keys=Object.keys(radarLabels), cx=88, cy=83, r=54, levels=[25,50,75,100];
  const point=(i,pct)=>{const a=-Math.PI/2+i*2*Math.PI/keys.length; const rr=r*(pct/100); return [cx+Math.cos(a)*rr,cy+Math.sin(a)*rr]};
  const grid=levels.map(l=>`<polygon points="${keys.map((_,i)=>point(i,l).map(n=>n.toFixed(1)).join(',')).join(' ')}" class="radar-grid"/>`).join('');
  const axes=keys.map((k,i)=>{const p=point(i,100), lp=point(i,128); const val=clamp(values?.[k]??50); return `<line x1="${cx}" y1="${cy}" x2="${p[0]}" y2="${p[1]}" class="radar-axis"/><text x="${lp[0]}" y="${lp[1]}" class="radar-label" text-anchor="middle">${radarLabels[k]} ${val}</text>`}).join('');
  const dataPts=keys.map((k,i)=>point(i,clamp(values?.[k]??50)).map(n=>n.toFixed(1)).join(',')).join(' ');
  return `<div class="radar-wrap"><div class="radar-caption">本轮发言关注强度 <span>AI语义解析</span></div><svg class="radar-svg" viewBox="0 0 176 176" aria-label="主体关注雷达图">${grid}${axes}<polygon points="${dataPts}" class="radar-data"/>${keys.map((k,i)=>{const p=point(i,clamp(values?.[k]??50));return `<circle cx="${p[0]}" cy="${p[1]}" r="2.4" class="radar-dot"/>`}).join('')}</svg><small>表示发言侧重点，不是绩效得分</small></div>`;
}
function renderStreamMessage(item,target='deliberationTimeline',finalRound=5){
  ensureRound(item.round,'','',target,finalRound);
  const prefix=target==='challengeResult'?'ch':'ev';
  const row=$(`${prefix}-msg-${item.round}-${item.role}`); if(!row) return;
  // Ignore duplicate fallback records for an already completed role/round.
  if(row.dataset.complete==='1') return;
  row.dataset.received='1'; row.dataset.complete='1'; row.classList.remove('pending'); row.classList.add('received','speaking');
  const support=Math.max(1,Math.min(5,Number(item.support)||3));
  row.querySelector('.support-line').textContent=`支持度 ${'●'.repeat(support)}${'○'.repeat(5-support)}`;
  const p=row.querySelector('.message-text'); typeText(p,item.text||'');
  const meta=[];
  if(item.focus) meta.push(`<span>关注：${esc(item.focus)}</span>`);
  if(item.responds_to){const rs=Array.isArray(item.responds_to)?item.responds_to:[item.responds_to]; meta.push(`<span class="reply">群聊回应 → ${rs.map(esc).join(' · ')}</span>`);} if(item.questions) meta.push(`<span class="question-tag">提出 ${Number(item.questions)||0} 个问题</span>`); if(item.questions_answered) meta.push(`<span class="question-tag">回应 ${Number(item.questions_answered)||0} 个问题</span>`);
  if(item.concession) meta.push(`<span class="concession">让步：${esc(item.concession)}</span>`);
  if(item.condition) meta.push(`<span class="condition-tag">条件：${esc(item.condition)}</span>`);
  row.querySelector('.dialogue-meta').innerHTML=meta.join('');
  if(item.round===finalRound && item.radar){
    row.classList.add('has-radar');
    const radar=document.createElement('div'); radar.className='final-radar'; radar.innerHTML=radarSVG(item.radar); row.appendChild(radar);
  }
  setTimeout(()=>row.classList.remove('speaking'),700);
}
function startLiveMessage(item,target='deliberationTimeline',finalRound=5){
  ensureRound(item.round,'','',target,finalRound);
  const prefix=target==='challengeResult'?'ch':'ev';
  const row=$(`${prefix}-msg-${item.round}-${item.role}`); if(!row || row.dataset.complete==='1') return;
  row.dataset.received='1'; row.classList.remove('pending'); row.classList.add('received','speaking');
  const support=Math.max(1,Math.min(5,Number(item.support)||3));
  row.querySelector('.support-line').textContent=`支持度 ${'●'.repeat(support)}${'○'.repeat(5-support)}`;
  row.querySelector('.message-text').textContent='';
  const meta=[];
  if(item.focus) meta.push(`<span>关注：${esc(item.focus)}</span>`);
  if(item.responds_to){const rs=Array.isArray(item.responds_to)?item.responds_to:[item.responds_to]; meta.push(`<span class="reply">群聊回应 → ${rs.map(esc).join(' · ')}</span>`);} if(item.questions) meta.push(`<span class="question-tag">提出 ${Number(item.questions)||0} 个问题</span>`); if(item.questions_answered) meta.push(`<span class="question-tag">回应 ${Number(item.questions_answered)||0} 个问题</span>`);
  if(item.concession) meta.push(`<span class="concession">让步：${esc(item.concession)}</span>`);
  if(item.condition) meta.push(`<span class="condition-tag">条件：${esc(item.condition)}</span>`);
  row.querySelector('.dialogue-meta').innerHTML=meta.join('');
  if(item.round===finalRound && item.radar && !row.querySelector('.final-radar')){
    row.classList.add('has-radar');
    const radar=document.createElement('div'); radar.className='final-radar'; radar.innerHTML=radarSVG(item.radar); row.appendChild(radar);
  }
}
function appendLiveDelta(item,target='deliberationTimeline'){
  const prefix=target==='challengeResult'?'ch':'ev';
  const row=$(`${prefix}-msg-${item.round}-${item.role}`); if(!row || row.dataset.complete==='1') return;
  const p=row.querySelector('.message-text'); p.textContent+=(item.text||'');
}
function endLiveMessage(item,target='deliberationTimeline'){
  const prefix=target==='challengeResult'?'ch':'ev';
  const row=$(`${prefix}-msg-${item.round}-${item.role}`); if(!row) return;
  row.dataset.complete='1'; row.classList.remove('speaking');
}
function handleDeliberationItem(item){
  if(item.type==='status'){
    $('aiLoading').classList.remove('hidden'); $('streamStatusText').textContent=item.message||'正在生成…';
    $('aiModeTag').textContent=item.mode==='aiping'?'AI Ping · 实时流':'本地保障流';
    return;
  }
  if(item.type==='round'){
    ensureRound(item.round,item.title,item.goal,'deliberationTimeline',5);
    currentDeliberation.rounds[item.round]=currentDeliberation.rounds[item.round]||{round:item.round,title:item.title,goal:item.goal,messages:[]};
    return;
  }
  if(item.type==='message_start'){
    startLiveMessage(item,'deliberationTimeline',5);
    $('streamStatusText').textContent=`第 ${item.round} 轮 · ${item.name||roleName(item.role)} 正在实时发言…`;
    return;
  }
  if(item.type==='delta'){appendLiveDelta(item,'deliberationTimeline');return;}
  if(item.type==='message_end'){endLiveMessage(item,'deliberationTimeline');$('streamStatusText').textContent=`第 ${item.round} 轮 · ${roleName(item.role)} 发言完成，下一主体继续…`;return;}
  if(item.type==='message'){
    ensureRound(item.round,'','','deliberationTimeline',5); renderStreamMessage(item,'deliberationTimeline',5);
    const r=currentDeliberation.rounds[item.round]||(currentDeliberation.rounds[item.round]={round:item.round,messages:[]});
    if(!r.messages.some(x=>x.role===item.role)) r.messages.push(item);
    $('streamStatusText').textContent=`第 ${item.round} 轮 · ${item.name||roleName(item.role)} 已返回，其他主体继续生成…`;
    return;
  }
  if(item.type==='summary'){
    currentDeliberation.summary=item;
    if(chosen==='D' && !customImpactApplied){
      customImpactApplied=true; const sd=item.state_delta||{}, cd=item.condition_delta||{}; applyDelta(scores,sd); applyDelta(conditions,cd); renderScores(); renderConditions();
      renderImpact({delta:sd,condition_delta:cd});
      const h=history[history.length-1]; if(h){h.delta={...sd};h.condition_delta={...cd};h.scores_after={...scores};h.conditions_after={...conditions};h.impact_explanation=item.impact_explanation||'';}
      if(item.impact_explanation) $('impactBox').insertAdjacentHTML('beforeend',`<p class="impact-explain">${esc(item.impact_explanation)}</p>`);
    }
    renderConsensus(item);
    const h=history[history.length-1]; if(h){h.agreement_level=item.agreement_level; h.negotiated_plan=item.negotiated_plan;}
    return;
  }
  if(item.type==='memory'){
    actorMemory=clone(item.actor_memory||actorMemory); renderMemoryPanel();
    const h=history[history.length-1]; if(h) h.actor_memory_after=clone(actorMemory);
    return;
  }
  if(item.type==='done'){
    $('aiModeTag').textContent=item.mode==='aiping'?'AI Ping · 流式完成':item.mode==='fallback'?'AI Ping中断 · 本地保障完成':'本地演示流完成';
    $('streamStatusText').textContent='五角色代理的5轮群聊已完成';
    return;
  }
  if(item.type==='error') throw new Error(item.message||'协商错误');
}
function renderConsensus(r){
  const agreement=Number(r.agreement_level)||0;
  $('consensusBox').innerHTML=`
    <div class="agreement"><div><span>协商共识度</span><b>${agreement}%</b></div><div class="agreement-bar"><i style="width:${agreement}%"></i></div></div>
    <div class="synthesis-grid">
      <div><strong>协商转折点</strong><ul>${(r.turning_points||[]).map(x=>`<li>${esc(x)}</li>`).join('')||'<li>—</li>'}</ul></div>
      <div><strong>仍未解决</strong><ul>${(r.unresolved||[]).map(x=>`<li>${esc(x)}</li>`).join('')||'<li>—</li>'}</ul></div>
    </div>
    <div class="negotiated"><strong>协商后的行动方案</strong><ol>${(r.negotiated_plan||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ol></div>
    <div class="facilitator"><strong>主持人观察：</strong>${esc(r.facilitator||'—')}</div>`;
  $('consensusBox').classList.remove('hidden');
}
async function next(){
  if(stageIdx<DATA.stages.length-1){stageIdx++; currentEvent=selectEventForStage(); renderEvent(); window.scrollTo({top:70,behavior:'smooth'});}else{await showReport();}
}

function topEffects(h,limit=2){
  const all=[];
  Object.entries(h.delta||{}).forEach(([k,v])=>v&&all.push({key:k,label:DATA.metrics[k]||k,value:v,kind:'score'}));
  Object.entries(h.condition_delta||{}).forEach(([k,v])=>v&&all.push({key:k,label:DATA.condition_metrics[k]?.name||k,value:v,kind:'condition'}));
  return all.sort((a,b)=>Math.abs(b.value)-Math.abs(a.value)).slice(0,limit);
}
function renderCausalGraph(){
  const dims=[...Object.entries(DATA.metrics).map(([key,label])=>({key,label,value:scores[key],kind:'score'})),...Object.entries(DATA.condition_metrics).map(([key,m])=>({key,label:m.name,value:conditions[key],kind:'condition'}))];
  const h=Math.max(790,history.length*76+90), w=1160, eventX=25,eventW=330,dimX=900,dimW=220;
  const eventY=i=>55+i*((h-110)/Math.max(1,history.length-1));
  const dimY=i=>45+i*((h-90)/Math.max(1,dims.length-1));
  const dimMap={}; dims.forEach((d,i)=>dimMap[d.key]={...d,y:dimY(i)});
  let edges='',seq='',eventNodes='',dimNodes='';
  history.forEach((item,i)=>{
    const y=eventY(i); if(i<history.length-1){const ny=eventY(i+1); seq+=`<path d="M 48 ${y+24} L 48 ${ny-24}" class="route-edge"/>`;}
    topEffects(item,2).forEach(e=>{const d=dimMap[e.key]; if(!d)return; const sx=eventX+eventW,sy=y,tx=dimX,ty=d.y; const c1=sx+180,c2=tx-150; edges+=`<path d="M ${sx} ${sy} C ${c1} ${sy}, ${c2} ${ty}, ${tx} ${ty}" class="network-edge ${e.value>0?'positive':'negative'}"><title>${esc(item.title)} → ${esc(e.label)} ${signed(e.value)}</title></path>`;});
    const branch=!!item.branch_note; const title=(item.title||'').length>21?(item.title||'').slice(0,21)+'…':item.title||''; const choice=(item.choice_label||'').length>23?(item.choice_label||'').slice(0,23)+'…':item.choice_label||'';
    eventNodes+=`<g class="network-event ${branch?'branch-node':''}" transform="translate(${eventX},${y-25})"><rect width="${eventW}" height="50" rx="10"/><circle cx="23" cy="25" r="7"/><text x="42" y="20" class="node-title">${String(i+1).padStart(2,'0')} · ${esc(title)}</text><text x="42" y="38" class="node-sub">${esc(item.choice)} · ${esc(choice)}</text><title>${esc(item.title)}｜选择：${esc(item.choice_label)}${branch?'｜'+esc(item.branch_note):''}</title></g>`;
  });
  dims.forEach((d,i)=>{const y=dimY(i); dimNodes+=`<g class="network-dim ${d.kind}" transform="translate(${dimX},${y-22})"><rect width="${dimW}" height="44" rx="10"/><text x="13" y="18" class="node-title">${esc(d.label)}</text><text x="13" y="34" class="node-sub">最终状态 ${d.value}</text><text x="194" y="28" class="dim-value" text-anchor="end">${d.value}</text></g>`;});
  $('causalGraph').innerHTML=`<svg viewBox="0 0 ${w} ${h}" class="causal-svg" role="img" aria-label="治理因果网络"><g>${seq}${edges}</g><g>${eventNodes}</g><g>${dimNodes}</g></svg>`;
}

async function showReport(){
  $('knowledge').classList.add('hidden'); $('memoryWall').classList.add('hidden'); $('eventView').classList.add('hidden'); $('challengeView').classList.add('hidden'); $('reportView').classList.remove('hidden');
  const total=Math.round(Object.values(scores).reduce((a,b)=>a+b,0)/Object.keys(scores).length);
  $('reportTotal').textContent=total;
  $('reportConditions').innerHTML=Object.entries(DATA.condition_metrics).map(([k,m])=>`<div><span>${esc(m.name)}</span><b>${conditions[k]}</b></div>`).join('');
  renderCausalGraph(); renderMemoryLedger();
  if(!currentReportData){
    currentReportData=await fetch('/api/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({history,scores,conditions,scenario,actor_memory:actorMemory,simulation_mode:runMode,memory_style:currentMemoryStyle})}).then(r=>r.json());
  }
  const r=currentReportData;
  $('reportProfile').textContent=r.profile||'治理诊断'; $('reportOne').textContent=r.one_sentence||''; $('routeSummary').textContent=r.route_summary||''; $('strengths').innerHTML=(r.strengths||[]).map(x=>`<li>${esc(x)}</li>`).join(''); $('risks').innerHTML=(r.risks||[]).map(x=>`<li>${esc(x)}</li>`).join(''); $('publicJudgement').textContent=r.public_value_judgement||''; $('nextActions').innerHTML=(r.next_actions||[]).map(x=>`<li>${esc(x)}</li>`).join('');
  renderCurrentRunMemory(r,total);
  if(!currentRunSaved){ currentRunSaved=await saveRunMemory(r,total); await renderMemoryWall(); }
  fromView='report'; window.scrollTo({top:70,behavior:'smooth'});
}
function enterChallenge(origin='landing'){
  fromView=origin;
  if(origin==='landing'){resetActorMemory(); resetStateFromScenario();}
  $('knowledge').classList.add('hidden'); $('landing').classList.add('hidden'); $('game').classList.remove('hidden'); $('eventView').classList.add('hidden'); $('reportView').classList.add('hidden'); $('challengeView').classList.remove('hidden'); renderMemoryPanel(); window.scrollTo({top:70,behavior:'smooth'});
}
async function runChallenge(){
  const proposal=$('proposal').value.trim(); if(!proposal){alert('先写下你的治理方案。');return;}
  $('challengeLoading').classList.remove('hidden'); $('challengeStatusText').textContent='五轮群聊已启动，首个主体发言会立即显示…'; $('challengeBtn').disabled=true;
  $('challengeResult').className='challenge-result'; $('challengeResult').innerHTML='';
  try{
    await streamPost('/api/challenge/stream',{proposal,scores,conditions,scenario,actor_memory:actorMemory},handleChallengeItem);
  }catch(err){$('challengeResult').insertAdjacentHTML('beforeend',`<div class="round-section error-card"><p>${esc(String(err))}</p></div>`);} finally {$('challengeLoading').classList.add('hidden'); $('challengeBtn').disabled=false;}
}
function handleChallengeItem(item){
  if(item.type==='status'){$('challengeStatusText').textContent=item.message||'正在协商…';return;}
  if(item.type==='round'){ensureRound(item.round,item.title,item.goal,'challengeResult',5);return;}
  if(item.type==='message'){ensureRound(item.round,'','','challengeResult',5);renderStreamMessage(item,'challengeResult',5);$('challengeStatusText').textContent=`第 ${item.round} 轮 · ${item.name||roleName(item.role)} 已返回…`;return;}
  if(item.type==='summary'){renderChallengeSummary(item);return;}
  if(item.type==='done'){$('challengeStatusText').textContent='五轮群聊已完成';return;}
  if(item.type==='error') throw new Error(item.message||'挑战协商错误');
}
function renderChallengeSummary(r){
  let box=$('challengeSummary'); if(!box){box=document.createElement('div');box.id='challengeSummary';box.className='challenge-summary';$('challengeResult').appendChild(box);}
  box.innerHTML=`<div class="challenge-verdict"><span class="event-kicker">最终协商结论</span><h3>${esc(r.verdict||'—')}</h3><p>${esc(r.main_tradeoff||'')}</p><div class="agreement"><div><span>共识度</span><b>${Number(r.agreement_level)||0}%</b></div><div class="agreement-bar"><i style="width:${Number(r.agreement_level)||0}%"></i></div></div></div><div class="negotiated"><b>协商后的调整方案</b><ol>${(r.negotiated_plan||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ol><div class="warning">提醒：${esc(r.warning||'')}</div></div>`;
}



let wallMemories=[];
async function loadWallMemories(){
  try{
    const r=await fetch('/api/memories',{cache:'no-store'}).then(r=>r.json());
    wallMemories=Array.isArray(r.memories)?r.memories:[];
    if($('wallStorageState')) $('wallStorageState').textContent=`本地JSON · ${r.storage_file||'data/memory_wall.json'} · ${wallMemories.length}条`;
    return wallMemories;
  }catch(e){
    console.warn('memory JSON load failed',e); wallMemories=[]; return wallMemories;
  }
}
function formatWallTime(iso){
  try{return new Date(iso).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});}catch(e){return iso||'';}
}
function renderCurrentRunMemory(r,total){
  $('runMemoryStyle').textContent=`${r.memory_style||currentMemoryStyle}风格`;
  $('runMemoryQuote').textContent=r.memory_quote||r.one_sentence||'这一局的治理选择已经被记录。';
  $('runMemoryAchievement').textContent=`✦ ${r.memory_achievement||r.profile||'治理探索者'}`;
  $('runMemoryMode').textContent=runMode==='normal'?'普通模式':'自由模式';
  $('runMemorySaved').textContent=currentRunSaved?'已写入本地JSON':'正在写入本地JSON';
  $('runMemoryCard').dataset.style=r.memory_style||currentMemoryStyle;
}
async function saveRunMemory(r,total){
  const topMetric=Object.entries(scores).sort((a,b)=>b[1]-a[1])[0]||['',0];
  const memory={id:currentRunId,created_at:new Date().toISOString(),mode:runMode,mode_name:runMode==='normal'?'普通模式':'自由模式',profile:r.profile||'治理诊断',achievement:r.memory_achievement||r.profile||'治理探索者',quote:r.memory_quote||r.one_sentence||'完成了一次治理模拟。',style:r.memory_style||currentMemoryStyle,total,top_metric:DATA.metrics[topMetric[0]]||'',top_value:topMetric[1],events:history.length,seed:runMode==='free'?runSeed:null};
  try{
    const res=await fetch('/api/memories/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({memory})});
    const data=await res.json();
    if(!res.ok) throw new Error(data.error||`HTTP ${res.status}`);
    wallMemories=Array.isArray(data.memories)?data.memories:wallMemories;
    $('runMemorySaved').textContent='已写入 data/memory_wall.json';
    return true;
  }catch(e){
    $('runMemorySaved').textContent='JSON写入失败';
    console.warn('memory JSON save failed',e); return false;
  }
}
function stickyClass(style,index){const base=style==='文艺'?'literary':style==='官方'?'official':'practical'; return `${base} tilt-${index%5}`;}
async function renderMemoryWall(){
  if(!$('memoryBoard')) return;
  const items=await loadWallMemories();
  const normal=items.filter(x=>x.mode==='normal').length, free=items.filter(x=>x.mode==='free').length;
  $('wallStats').innerHTML=`<div><span>累计模拟</span><b>${items.length}</b></div><div><span>普通模式</span><b>${normal}</b></div><div><span>自由模式</span><b>${free}</b></div><div><span>最新成就</span><b>${esc(items[0]?.achievement||'等待第一局')}</b></div>`;
  $('wallEmpty').classList.toggle('hidden',items.length>0);
  $('memoryBoard').innerHTML=items.map((m,i)=>`<article class="sticky-note ${stickyClass(m.style,i)}"><div class="sticky-pin"></div><div class="sticky-top"><span>${esc(m.style||'实际')} · ${esc(m.mode_name||'模拟')}</span><time>${esc(formatWallTime(m.created_at))}</time></div><h3>${esc(m.achievement||m.profile||'治理探索者')}</h3><blockquote>${esc(m.quote||'')}</blockquote><div class="sticky-meta"><span>综合 ${Number(m.total)||0}</span><span>${esc(m.top_metric||'')} ${Number(m.top_value)||0}</span><span>${Number(m.events)||10}轮</span></div><button class="sticky-delete" data-memory-delete="${esc(m.id)}" title="移除这张便利贴">×</button></article>`).join('');
  document.querySelectorAll('[data-memory-delete]').forEach(btn=>btn.addEventListener('click',async()=>{
    const id=btn.dataset.memoryDelete;
    const res=await fetch('/api/memories/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
    if(!res.ok){alert('删除失败，请检查本地服务是否仍在运行。');return;}
    await renderMemoryWall();
  }));
}
async function openMemoryWall(origin='landing'){
  fromView=origin; $('landing').classList.add('hidden'); $('knowledge').classList.add('hidden'); $('game').classList.add('hidden'); $('memoryWall').classList.remove('hidden'); await renderMemoryWall(); window.scrollTo({top:50,behavior:'smooth'});
}
function backFromMemoryWall(){
  $('memoryWall').classList.add('hidden');
  if(fromView==='report'){$('game').classList.remove('hidden'); $('reportView').classList.remove('hidden'); $('eventView').classList.add('hidden'); $('challengeView').classList.add('hidden'); return;}
  if(fromView==='challenge'){$('game').classList.remove('hidden'); $('challengeView').classList.remove('hidden'); $('eventView').classList.add('hidden'); $('reportView').classList.add('hidden'); return;}
  if(fromView==='game'){$('game').classList.remove('hidden'); $('eventView').classList.remove('hidden'); $('reportView').classList.add('hidden'); $('challengeView').classList.add('hidden'); return;}
  $('landing').classList.remove('hidden');
}
async function clearMemoryWall(){
  if(!confirm('确定清空 data/memory_wall.json 中的全部模拟回忆吗？此操作无法撤销。')) return;
  const res=await fetch('/api/memories/clear',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  if(!res.ok){alert('清空失败。');return;}
  await renderMemoryWall();
}
function exportMemoryWall(){
  const a=document.createElement('a'); a.href='/api/memories/export'; a.download='origin-cogov-memory-wall.json'; document.body.appendChild(a); a.click(); a.remove();
}
function importMemoryWall(){ $('importWallFile').click(); }
async function handleMemoryImportFile(file){
  if(!file) return;
  try{
    const raw=JSON.parse(await file.text());
    const mode=$('wallImportMode')?.value==='replace'?'replace':'merge';
    const res=await fetch('/api/memories/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode,data:raw})});
    const data=await res.json();
    if(!res.ok) throw new Error(data.error||`HTTP ${res.status}`);
    alert(`导入完成：当前共有 ${data.count||0} 条回忆。`);
    await renderMemoryWall();
  }catch(e){ alert(`导入失败：${String(e.message||e)}`); }
  finally { $('importWallFile').value=''; }
}

function renderKnowledge(){
  if(!$('galleryGrid')) return;
  $('galleryGrid').innerHTML=(DATA.gallery||[]).map(g=>`<article class="gallery-card"><a class="gallery-media" href="${esc(g.source_url||'#')}" target="_blank" rel="noreferrer"><img src="${esc(g.image)}" alt="${esc(g.title)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none';this.nextElementSibling.classList.remove('hidden')" /><div class="gallery-offline hidden"><span>图片需联网加载</span><small>点击打开来源页面</small></div></a><div class="gallery-body"><b>${esc(g.title)}</b><p>${esc(g.caption||'')}</p><small>${esc(g.source_kind||'图片来源')}｜<a href="${esc(g.source_url||'#')}" target="_blank" rel="noreferrer">${esc(g.source_title||'查看来源')}</a></small></div></article>`).join('');
  $('briefGrid').innerHTML=(DATA.case_briefs||[]).map(b=>`<article class="brief-card"><div class="brief-head"><b>${esc(b.title)}</b><span>${esc(b.source||'')}</span></div><p>${esc(b.text||'')}</p><div class="brief-tags">${(b.keywords||[]).slice(0,4).map(k=>`<span>${esc(k)}</span>`).join('')}</div></article>`).join('');
  $('referenceList').innerHTML=(DATA.references||[]).map(r=>`<article class="ref-card"><div class="ref-top"><b>${esc(r.title)}</b><span>${esc(r.kind||'资料')}</span></div><p>${esc(r.summary||'')}</p>${r.citation?`<div class="ref-citation"><em>参考文献格式</em>${esc(r.citation)}</div>`:''}${(r.key_points||[]).length?`<ul>${r.key_points.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}${r.url?`<a class="ref-link" href="${esc(r.url)}" target="_blank" rel="noreferrer">打开来源 ↗</a>`:'<span class="ref-link muted">内部材料</span>'}</article>`).join('');
  $('qaChips').innerHTML=(DATA.qa_prompts||[]).map(q=>`<button class="qa-chip" data-q="${esc(q)}">${esc(q)}</button>`).join('');
  document.querySelectorAll('.qa-chip').forEach(btn=>btn.addEventListener('click',()=>{$('qaInput').value=btn.dataset.q||'';}));
}

function openKnowledge(origin='landing'){
  fromView=origin;
  $('memoryWall').classList.add('hidden'); $('landing').classList.add('hidden'); $('game').classList.add('hidden'); $('knowledge').classList.remove('hidden');
  renderKnowledge(); window.scrollTo({top:70,behavior:'smooth'});
}
function backFromKnowledge(){
  $('knowledge').classList.add('hidden');
  if(fromView==='report'){$('game').classList.remove('hidden'); $('reportView').classList.remove('hidden'); $('eventView').classList.add('hidden'); $('challengeView').classList.add('hidden'); window.scrollTo({top:70,behavior:'smooth'}); return;}
  if(fromView==='challenge'){$('game').classList.remove('hidden'); $('challengeView').classList.remove('hidden'); $('eventView').classList.add('hidden'); $('reportView').classList.add('hidden'); window.scrollTo({top:70,behavior:'smooth'}); return;}
  if(fromView==='game'){$('game').classList.remove('hidden'); $('eventView').classList.remove('hidden'); $('reportView').classList.add('hidden'); $('challengeView').classList.add('hidden'); window.scrollTo({top:70,behavior:'smooth'}); return;}
  $('landing').classList.remove('hidden'); window.scrollTo({top:0,behavior:'smooth'});
}
async function askCaseQA(){
  const question=$('qaInput').value.trim();
  if(!question){alert('先输入评审团想问的问题。'); return;}
  $('qaStatus').classList.remove('hidden'); $('qaAnswer').classList.add('hidden'); $('askQaBtn').disabled=true;
  try{
    const r=await fetch('/api/caseqa',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,history,scores,conditions,scenario})}).then(r=>r.json());
    renderQaAnswer(r,question);
  }catch(err){
    $('qaAnswer').classList.remove('hidden');
    $('qaAnswer').innerHTML=`<div class="qa-error">问答助手暂时不可用：${esc(String(err))}</div>`;
  } finally {
    $('qaStatus').classList.add('hidden'); $('askQaBtn').disabled=false;
  }
}
function renderQaAnswer(r,question=''){
  $('qaAnswer').classList.remove('hidden');
  const srcs=(r.sources||[]).map(s=>`<li><b>${esc(s.title||'资料')}</b>${s.kind?`<span>${esc(s.kind)}</span>`:''}${s.url?`<a href="${esc(s.url)}" target="_blank" rel="noreferrer">打开来源 ↗</a>`:''}</li>`).join('')||'<li>未返回来源</li>';
  const follow=(r.followups||[]).map(q=>`<button class="qa-chip followup" data-q="${esc(q)}">${esc(q)}</button>`).join('');
  $('qaAnswer').innerHTML=`<div class="qa-answer-head"><span>问题</span><b>${esc(question)}</b><small>${esc(r.mode==='aiping'?'AI Ping + 本地知识库':'本地知识库回答')}</small></div><div class="qa-main"><p>${esc(r.answer||'')}</p>${(r.bullets||[]).length?`<ol>${r.bullets.map(x=>`<li>${esc(x)}</li>`).join('')}</ol>`:''}</div><div class="qa-sources"><strong>本次主要依据</strong><ul>${srcs}</ul></div>${follow?`<div class="qa-followups"><strong>你还可以继续追问</strong><div class="qa-chips">${follow}</div></div>`:''}`;
  $('qaAnswer').querySelectorAll('.followup').forEach(btn=>btn.addEventListener('click',()=>{$('qaInput').value=btn.dataset.q||'';}));
}

function reset(){
  stageIdx=0; history=[]; chosen=null; currentEvent=null; currentDeliberation=null; currentReportData=null; currentRunSaved=false; DATA.scenario_controls.forEach(c=>scenario[c.id]=c.default); resetActorMemory(); resetStateFromScenario(); renderScenarioControls(); renderMemoryPanel();
  $('memoryWall').classList.add('hidden'); $('knowledge').classList.add('hidden'); $('game').classList.add('hidden'); $('landing').classList.remove('hidden'); renderSteps(); renderScores(); renderConditions(); window.scrollTo({top:0,behavior:'smooth'});
}

$('startBtn').addEventListener('click',showGame);
$('nextBtn').addEventListener('click',next);
$('resetBtn').addEventListener('click',reset);
$('scenarioReset').addEventListener('click',()=>{DATA.scenario_controls.forEach(c=>scenario[c.id]=c.default);resetStateFromScenario();renderScenarioControls();});
$('printBtn').addEventListener('click',()=>window.print());
$('challengeEntry').addEventListener('click',()=>enterChallenge('landing'));
$('knowledgeEntry').addEventListener('click',()=>openKnowledge('landing'));
$('memoryWallEntry').addEventListener('click',()=>openMemoryWall('landing'));
$('memoryWallTopBtn').addEventListener('click',()=>{const origin=!$('reportView').classList.contains('hidden')?'report':!$('challengeView').classList.contains('hidden')?'challenge':!$('eventView').classList.contains('hidden')?'game':'landing';openMemoryWall(origin);});
$('toChallengeBtn').addEventListener('click',()=>enterChallenge('report'));
$('toKnowledgeBtn').addEventListener('click',()=>openKnowledge('report'));
$('toMemoryWallBtn').addEventListener('click',()=>openMemoryWall('report'));
$('openWallFromReportBtn').addEventListener('click',()=>openMemoryWall('report'));
$('challengeBtn').addEventListener('click',runChallenge);
$('backBtn').addEventListener('click',()=>{if(fromView==='report'){showReport()}else{reset()}});
$('backFromKnowledgeBtn').addEventListener('click',backFromKnowledge);
$('backFromWallBtn').addEventListener('click',backFromMemoryWall);
$('clearWallBtn').addEventListener('click',clearMemoryWall);
$('exportWallBtn').addEventListener('click',exportMemoryWall);
$('importWallBtn').addEventListener('click',importMemoryWall);
$('importWallFile').addEventListener('change',e=>handleMemoryImportFile(e.target.files?.[0]));
$('askQaBtn').addEventListener('click',askCaseQA);
$('clearQaBtn').addEventListener('click',()=>{$('qaInput').value=''; $('qaAnswer').classList.add('hidden'); $('qaAnswer').innerHTML='';});
init();
