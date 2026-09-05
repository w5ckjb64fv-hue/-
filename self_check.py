import os, json
os.environ['MOCK_MODE']='true'
import app

case=app.CASE
assert case['product']['version'].startswith('8.')
assert len(case['events']) == 50
assert len(case['stages']) == 10
assert all(len(s['candidates'])==5 for s in case['stages'])
assert len(case['roles']) == 5
assert len(case['condition_metrics']) == 6
assert len(case['scenario_controls']) == 4
assert case.get('random_engine')
assert all(set(r.get('preference',{})) == set(case['metrics']) for r in case['roles'])
assert all(r.get('decision_rule') and r.get('red_lines') and r.get('evidence_focus') for r in case['roles'])

ids={e['id'] for e in case['events']}
for stage in case['stages']:
    assert stage['candidates'] and all(i in ids for i in stage['candidates'])
for e in case['events']:
    assert len(e['options']) == 3
    if e.get('case_choice') is not None:
        assert e['case_choice'] in {o['id'] for o in e['options']}
    for o in e['options']:
        assert set(o['delta']) == set(case['initial_scores'])
        assert set(o['condition_delta']) == set(case['initial_conditions'])
assert sum(1 for e in case['events'] if e.get('special')) == 35
assert all(any(not next(e for e in case['events'] if e['id']==eid).get('special') for eid in s['candidates']) for s in case['stages'])
html=(app.BASE_DIR/'templates'/'index.html').read_text(encoding='utf-8')
js=(app.BASE_DIR/'static'/'app.js').read_text(encoding='utf-8')
css=(app.BASE_DIR/'static'/'styles.css').read_text(encoding='utf-8')
assert 'data-mode="normal"' in html and 'data-mode="free"' in html
assert "if(runMode==='normal')" in js and "candidates.filter(e=>!e.special)" in js
assert '.free-impact-summary' in css and 'grid-column:1/-1!important' in css

base_scores=case['initial_scores']; base_conditions=case['initial_conditions']
scenario={c['id']:c['default'] for c in case['scenario_controls']}; memory=app.default_actor_memory()

# 1) Offline fallback: 5 rounds x 5 actors, group questioning, final radars, memory.
items=list(app.deliberation_stream({'event_id':1,'choice':'C','scores':base_scores,'conditions':base_conditions,'scenario':scenario,'actor_memory':memory}))
assert items[0]['type']=='status'
assert sum(1 for x in items if x.get('type')=='round') == 5
msgs=[x for x in items if x.get('type')=='message']; assert len(msgs)==25
r2=[x for x in msgs if x.get('round')==2]; assert len(r2)==5 and all(isinstance(x.get('responds_to'),list) and len(x['responds_to'])>=2 for x in r2)
r3=[x for x in msgs if x.get('round')==3]; assert len(r3)==5 and all(x.get('questions_answered',0)>=2 for x in r3)
final=[x for x in msgs if x.get('round')==5]; assert len(final)==5 and all(set(x.get('radar',{}))==set(case['metrics']) for x in final)
assert min(len(x.get('text','')) for x in r2) > 80
mem_item=next(x for x in items if x.get('type')=='memory'); newmem=mem_item['actor_memory']
assert all(newmem[r]['events_seen']==1 for r in newmem)
assert all(newmem[r]['conditions'] for r in newmem)

# 2) D free answer gets semantic impact and 25-message debate.
free='先保留核心公共空间，同时建立分阶段预算上限，让居民、企业和运营方每月共同复盘；高峰期采用分时预约，并设置企业资源贡献机制。'
items_d=list(app.deliberation_stream({'event_id':28,'choice':'D','custom_response':free,'scores':base_scores,'conditions':base_conditions,'scenario':scenario,'actor_memory':newmem}))
assert len([x for x in items_d if x.get('type')=='message'])==25
summary_d=next(x for x in items_d if x.get('type')=='summary')
assert set(summary_d['state_delta'])==set(case['initial_scores'])
assert set(summary_d['condition_delta'])==set(case['initial_conditions'])
assert any(summary_d['state_delta'].values()) or any(summary_d['condition_delta'].values())
mem2=next(x for x in items_d if x.get('type')=='memory')['actor_memory']; assert all(mem2[r]['events_seen']==2 for r in mem2)

# 3) Synthetic TRUE token stream: markers deliberately cross chunk boundaries; all 25 messages must stream.
role_ids=[r['id'] for r in case['roles']]; parts=[]
for rn,title in [(1,'独立立场'),(2,'群聊质询 · 第一圈'),(3,'群聊交锋 · 第二圈'),(4,'条件交换'),(5,'最终表态')]:
    parts.append('[[ROUND]]'+json.dumps({'round':rn,'title':title,'goal':'测试目标'},ensure_ascii=False)+'[[/ROUND]]')
    for rid in role_ids:
        meta={'round':rn,'role':rid,'support':4}
        if rn==1: meta['focus']='测试关注'
        if rn in (2,3): meta['responds_to']=['政府','运营方']; meta['questions' if rn==2 else 'questions_answered']=2
        if rn in (4,5): meta['concession']='测试让步'; meta['condition']='测试条件'
        if rn==5: meta['radar']={'asset':80,'innovation':60,'collaboration':70,'openness':50,'public_value':65}
        parts.append('[[ROLE]]'+json.dumps(meta,ensure_ascii=False)+'[[/ROLE]]')
        parts.append(f'{rid}第{rn}轮正在真正流式生成的较长正文，用于测试标签和中文分片边界，并确保浏览器不需要等待整轮结束。')
        parts.append('[[ENDROLE]]')
parts.append('[[SUMMARY]]'+json.dumps({'turning_points':['A','B'],'agreement_level':82,'unresolved':['C','D'],'negotiated_plan':['E','F','G'],'facilitator':'H','state_delta':{k:0 for k in case['initial_scores']},'condition_delta':{k:0 for k in case['initial_conditions']},'impact_explanation':''},ensure_ascii=False)+'[[ENDSUMMARY]]')
protocol=''.join(parts); orig_stream=app.aiping_stream_text; orig_mock=app.MOCK_MODE; orig_key=app.AIPING_API_KEY

def fake_stream(*args,**kwargs):
    cuts=[7,13,5,19,11]; i=j=0
    while i<len(protocol):
        n=cuts[j%len(cuts)]; j+=1; yield protocol[i:i+n]; i+=n
app.aiping_stream_text=fake_stream; app.MOCK_MODE=False; app.AIPING_API_KEY='synthetic-test-key'
try:
    live=list(app.deliberation_stream({'event_id':1,'choice':'C','scores':base_scores,'conditions':base_conditions,'scenario':scenario,'actor_memory':memory}))
finally:
    app.aiping_stream_text=orig_stream; app.MOCK_MODE=orig_mock; app.AIPING_API_KEY=orig_key
assert len([x for x in live if x.get('type')=='message_start'])==25
assert len([x for x in live if x.get('type')=='message_end'])==25
assert len([x for x in live if x.get('type')=='delta'])>=25
assert live[-1].get('type')=='done' and live[-1].get('mode')=='aiping'

# 4) Report + 5-round mayor challenge fallback.
history=[{'choice':'C','case_choice':'C','title':'测试事件','branch_note':'','delta':case['events'][0]['options'][2]['delta'],'condition_delta':case['events'][0]['options'][2]['condition_delta']}]
code,r=app.report_payload({'history':history,'scores':base_scores,'conditions':base_conditions,'scenario':scenario,'actor_memory':newmem})
assert code==200 and r.get('profile') and r.get('route_summary')
ch=list(app.challenge_stream({'proposal':'开放更多公共课程并设置分时空间','scores':base_scores,'conditions':base_conditions,'scenario':scenario,'actor_memory':newmem}))
assert sum(1 for x in ch if x.get('type')=='round')==5
assert len([x for x in ch if x.get('type')=='message'])==25
assert all(x.get('radar') for x in ch if x.get('type')=='message' and x.get('round')==5)


assert case.get('modes',{}).get('normal',{}).get('random_special') is False
assert case.get('modes',{}).get('free',{}).get('random_special') is True
assert sum(1 for e in case['events'] if not e.get('special')) == 15
assert sum(1 for e in case['events'] if e.get('special')) == 35
assert all(any(not next(e for e in case['events'] if e['id']==eid).get('special') for eid in s['candidates']) for s in case['stages'])
html=(app.BASE_DIR/'templates'/'index.html').read_text(encoding='utf-8')
js=(app.BASE_DIR/'static'/'app.js').read_text(encoding='utf-8')
css=(app.BASE_DIR/'static'/'styles.css').read_text(encoding='utf-8')
assert 'data-mode="normal"' in html and 'data-mode="free"' in html
assert "if(runMode==='normal')" in js and "candidates.filter(e=>!e.special)" in js
assert '.free-impact-summary' in css and 'grid-column:1/-1!important' in css
print('SELF_CHECK_OK')


# 5) Knowledge base + case QA.
assert len(case.get('gallery', [])) >= 14
assert len(case.get('references', [])) >= 17
assert len(case.get('case_briefs', [])) >= 5
status, qa = app.case_qa_payload({'question':'AI原点社区为什么说是城市更新案例，而不只是产业招商？'})
assert status == 200 and qa.get('answer') and qa.get('sources')
print('CASE_QA_OK')


# 7) V7 report memory note fields.
code, mem_report = app.report_payload({'history':history,'scores':base_scores,'conditions':base_conditions,'scenario':scenario,'actor_memory':newmem,'simulation_mode':'normal','memory_style':'文艺'})
assert code == 200
assert mem_report.get('memory_quote') and mem_report.get('memory_style') == '文艺' and mem_report.get('memory_achievement')
assert len(mem_report['memory_quote']) < 120
print('MEMORY_WALL_REPORT_OK')


# 8) V8 local JSON memory archive: add / merge import / replace import / delete.
orig_memory_path = app.MEMORY_WALL_PATH
test_memory_path = app.BASE_DIR / 'data' / '_memory_wall_test.json'
app.MEMORY_WALL_PATH = test_memory_path
try:
    if test_memory_path.exists(): test_memory_path.unlink()
    store = app.load_memory_store()
    assert store.get('schema') == 'origin-cogov-memory-wall' and store.get('memories') == []
    m1 = {'id':'check-1','created_at':'2026-08-28T10:00:00+00:00','mode':'normal','achievement':'协同筑桥者','quote':'测试回忆一。','style':'实际','total':80,'top_metric':'多元协同','top_value':88,'events':10,'seed':None}
    store = app.add_memory_record(m1)
    assert len(store['memories']) == 1 and store['memories'][0]['id'] == 'check-1'
    store = app.import_memory_store({'memories':[{'id':'check-2','mode':'free','achievement':'创新点火者','quote':'测试回忆二。','style':'文艺','total':85}]}, mode='merge')
    assert {x['id'] for x in store['memories']} == {'check-1','check-2'}
    store = app.import_memory_store([{'id':'check-3','mode':'normal','achievement':'稳健守门人','quote':'测试回忆三。','style':'官方','total':78}], mode='replace')
    assert [x['id'] for x in store['memories']] == ['check-3']
    store, removed = app.delete_memory_record('check-3')
    assert removed == 1 and store['memories'] == []
finally:
    app.MEMORY_WALL_PATH = orig_memory_path
    if test_memory_path.exists(): test_memory_path.unlink()
print('MEMORY_JSON_OK')
