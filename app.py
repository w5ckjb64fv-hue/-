import copy
import json
import mimetypes
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent


def load_env(path: Path):
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env(BASE_DIR / ".env")
with open(BASE_DIR / "case.json", "r", encoding="utf-8") as f:
    CASE = json.load(f)

AIPING_BASE_URL = os.getenv("AIPING_BASE_URL", "https://aiping.cn/api/v1").rstrip("/")
AIPING_API_KEY = os.getenv("AIPING_API_KEY", "").strip()
AIPING_MODEL = os.getenv("AIPING_MODEL", "DeepSeek-V3.2").strip()
MOCK_MODE = os.getenv("MOCK_MODE", "").lower() in {"1", "true", "yes", "on"}
AIPING_TIMEOUT = int(os.getenv("AIPING_TIMEOUT", "60"))
ROLE_MAP = {r["id"]: r for r in CASE["roles"]}
ROLE_ORDER = [r["id"] for r in CASE["roles"]]
RADAR_KEYS = list(CASE["metrics"].keys())
MEMORY_WALL_PATH = BASE_DIR / "memory_wall.json"
MEMORY_WALL_LOCK = threading.Lock()
MEMORY_WALL_LIMIT = 200


def clamp(v):
    return max(0, min(100, int(round(float(v)))))


def clean_json_text(text: str):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        left, right = text.find("{"), text.rfind("}")
        if left >= 0 and right > left:
            return json.loads(text[left:right + 1])
        raise


def aiping_chat(system_prompt: str, user_prompt: str, max_tokens=3600, temperature=0.4):
    if MOCK_MODE or not AIPING_API_KEY:
        raise RuntimeError("AI Ping API Key 未配置，当前使用本地演示模式。")
    payload = {
        "model": AIPING_MODEL,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    req = request.Request(
        f"{AIPING_BASE_URL}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {AIPING_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=AIPING_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"AI Ping HTTP {e.code}: {detail[:500]}")
    except Exception as e:
        raise RuntimeError(f"AI Ping 调用失败：{e}")
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("AI Ping 返回中没有 choices。")
    content = (choices[0].get("message") or {}).get("content") or ""
    parsed = clean_json_text(content)
    parsed["_meta"] = {"provider": data.get("provider"), "model": data.get("model", AIPING_MODEL), "usage": data.get("usage", {})}
    return parsed


def aiping_stream_text(system_prompt: str, user_prompt: str, max_tokens=5200, temperature=0.45):
    """Yield OpenAI-compatible streamed content chunks from AI Ping."""
    if MOCK_MODE or not AIPING_API_KEY:
        raise RuntimeError("AI Ping API Key 未配置，当前使用本地演示模式。")
    payload = {
        "model": AIPING_MODEL,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    req = request.Request(
        f"{AIPING_BASE_URL}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {AIPING_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=AIPING_TIMEOUT) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data:"):
                    continue
                data_text = line[5:].strip()
                if not data_text or data_text == "[DONE]":
                    continue
                try:
                    data = json.loads(data_text)
                except Exception:
                    continue
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content
    except error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"AI Ping HTTP {e.code}: {detail[:500]}")
    except Exception as e:
        raise RuntimeError(f"AI Ping 流式调用失败：{e}")


def event_by_id(event_id):
    return next((e for e in CASE["events"] if e["id"] == event_id), None)


def choice_for(event, choice_id):
    return next((o for o in event["options"] if o["id"] == choice_id), None)


def default_actor_memory():
    return {r["id"]: copy.deepcopy(r.get("memory_seed") or {}) for r in CASE["roles"]}


def sanitize_actor_memory(raw):
    base = default_actor_memory()
    if not isinstance(raw, dict):
        return base
    for rid in ROLE_ORDER:
        src = raw.get(rid) or {}
        mem = base[rid]
        mem["trust"] = clamp(src.get("trust", mem.get("trust", 55)))
        mem["last_support"] = max(1, min(5, int(src.get("last_support", mem.get("last_support", 3)) or 3)))
        mem["events_seen"] = max(0, int(src.get("events_seen", mem.get("events_seen", 0)) or 0))
        mem["last_position"] = str(src.get("last_position", mem.get("last_position", "")))[:500]
        for key in ("commitments", "conditions", "concerns"):
            vals = src.get(key, mem.get(key, []))
            if not isinstance(vals, list):
                vals = []
            mem[key] = [str(x)[:220] for x in vals if str(x).strip()][-5:]
        base[rid] = mem
    return base


def memory_prompt_view(actor_memory):
    out = {}
    for rid in ROLE_ORDER:
        mem = actor_memory[rid]
        out[rid] = {
            "name": ROLE_MAP[rid]["name"],
            "trust": mem.get("trust", 55),
            "last_support": mem.get("last_support", 3),
            "commitments": mem.get("commitments", [])[-3:],
            "conditions": mem.get("conditions", [])[-3:],
            "last_position": mem.get("last_position", "")[-240:],
        }
    return out


def role_support(role_id, chosen, is_case, memory=None):
    d = chosen.get("delta", {})
    cd = chosen.get("condition_delta", {})
    val = 3
    if role_id == "government":
        val += 1 if d.get("collaboration", 0) + d.get("public_value", 0) > 8 else 0
        val -= 1 if d.get("public_value", 0) < -5 else 0
    elif role_id == "collective":
        val += 1 if d.get("asset", 0) + cd.get("capital_resilience", 0) > 7 else 0
        val -= 1 if d.get("asset", 0) + cd.get("capital_resilience", 0) < -8 else 0
    elif role_id == "operator":
        val += 1 if cd.get("execution_capacity", 0) >= 3 else 0
        val -= 1 if cd.get("execution_capacity", 0) <= -6 else 0
    elif role_id == "startup":
        val += 1 if d.get("innovation", 0) >= 7 else 0
        val -= 1 if d.get("innovation", 0) <= -5 else 0
    elif role_id == "public":
        val += 1 if d.get("public_value", 0) + d.get("openness", 0) >= 12 else 0
        val -= 1 if d.get("public_value", 0) + d.get("openness", 0) <= -8 else 0
    if is_case and val < 5:
        val += 1
    if memory:
        prev = max(1, min(5, int(memory.get("last_support", 3) or 3)))
        val = round(val * 0.75 + prev * 0.25)
    return max(1, min(5, val))


def fallback_radar(role_id, chosen=None, memory=None):
    base = dict(ROLE_MAP[role_id].get("preference") or {k: 60 for k in RADAR_KEYS})
    chosen = chosen or {}
    delta = chosen.get("delta") or {}
    for k in RADAR_KEYS:
        if k in delta:
            base[k] = clamp(base.get(k, 60) + min(18, abs(delta[k]) * 1.5))
    if memory:
        trust = int(memory.get("trust", 55))
        if role_id == "public":
            base["public_value"] = clamp(base["public_value"] + (60 - trust) * 0.2)
        if role_id == "collective":
            base["asset"] = clamp(base["asset"] + (60 - trust) * 0.15)
    return {k: clamp(base.get(k, 50)) for k in RADAR_KEYS}


def normalize_radar(raw, role_id, chosen=None, memory=None):
    fb = fallback_radar(role_id, chosen, memory)
    if not isinstance(raw, dict):
        return fb
    return {k: clamp(raw.get(k, fb[k])) for k in RADAR_KEYS}


def update_actor_memory(actor_memory, event, rounds, agreement_level):
    updated = sanitize_actor_memory(actor_memory)
    final_messages = {}
    final_round = max([int(r.get("round", 0) or 0) for r in (rounds or [])] or [5])
    for rnd in rounds or []:
        if int(rnd.get("round", 0) or 0) == final_round:
            for msg in rnd.get("messages") or []:
                rid = msg.get("role")
                if rid in ROLE_MAP:
                    final_messages[rid] = msg
    for rid in ROLE_ORDER:
        mem = updated[rid]
        msg = final_messages.get(rid) or {}
        support = max(1, min(5, int(msg.get("support", mem.get("last_support", 3)) or 3)))
        concession = str(msg.get("concession", "")).strip()
        condition = str(msg.get("condition", "")).strip()
        text = str(msg.get("text", "")).strip()
        if concession and concession not in mem["commitments"]:
            mem["commitments"].append(concession[:220])
        if condition and condition not in mem["conditions"]:
            mem["conditions"].append(condition[:220])
        mem["commitments"] = mem["commitments"][-5:]
        mem["conditions"] = mem["conditions"][-5:]
        mem["last_support"] = support
        mem["last_position"] = text[:700]
        mem["events_seen"] = int(mem.get("events_seen", 0)) + 1
        trust_shift = (support - 3) * 3 + (int(agreement_level or 50) - 50) / 18
        mem["trust"] = clamp(mem.get("trust", 55) + trust_shift)
        updated[rid] = mem
    return updated


def make_custom_choice(text):
    text = str(text or "").strip()[:1600]
    return {
        "id": "D",
        "label": "自由回答",
        "desc": text,
        "delta": {},
        "condition_delta": {},
        "is_custom": True,
    }


def custom_impact_heuristic(text):
    """Fallback-only semantic impact for a free-form answer. Mechanism score, never empirical fact."""
    t = str(text or "")
    d = {k: 0 for k in RADAR_KEYS}
    c = {k: 0 for k in CASE["condition_metrics"].keys()}
    rules = [
        (("租金","收益","预算","成本","资金","分期","回收"), {"asset":4}, {"capital_resilience":5}),
        (("企业","创新","算力","融资","高校","科研","技术","创业"), {"innovation":7}, {"spillover":4}),
        (("协商","共治","分担","联席","反馈","共同","协调","责任"), {"collaboration":8}, {"execution_capacity":4,"public_trust":3}),
        (("开放","居民","公众","社区","夜校","家庭","儿童","老人"), {"openness":7,"public_value":9}, {"public_trust":7}),
        (("施工","安全","通行","停车","预约","分时","分区","容量"), {"public_value":4,"collaboration":3}, {"street_capacity":7,"execution_capacity":5}),
        (("试点","阶段","复盘","退出","小范围"), {"asset":2,"collaboration":4}, {"capital_resilience":3,"execution_capacity":5}),
    ]
    for words, dd, cc in rules:
        if any(w in t for w in words):
            for k,v in dd.items(): d[k]=max(-12,min(12,d[k]+v))
            for k,v in cc.items(): c[k]=max(-12,min(12,c[k]+v))
    if any(w in t for w in ("全部","强制","一刀切","立刻清退")):
        d["collaboration"] -= 5; d["public_value"] -= 3; c["public_trust"] -= 5
    if not any(d.values()) and not any(c.values()):
        d.update({"asset":1,"innovation":2,"collaboration":3,"openness":2,"public_value":3})
        c.update({"execution_capacity":1,"public_trust":2,"spillover":1})
    return ({k:max(-12,min(12,int(v))) for k,v in d.items()}, {k:max(-12,min(12,int(v))) for k,v in c.items()})


def normalize_signed_delta(raw, allowed, limit=12):
    if not isinstance(raw, dict): return {k:0 for k in allowed}
    out={}
    for k in allowed:
        try: v=int(round(float(raw.get(k,0) or 0)))
        except Exception: v=0
        out[k]=max(-limit,min(limit,v))
    return out


def mock_deliberation(event, choice_id, scores, conditions, scenario, actor_memory=None, custom_response=""):
    actor_memory = sanitize_actor_memory(actor_memory)
    chosen = make_custom_choice(custom_response) if choice_id == "D" else (choice_for(event, choice_id) or event["options"][0])
    is_case = bool(event.get("case_choice")) and choice_id == event.get("case_choice")
    label = chosen["label"] if choice_id != "D" else f"自由方案：{chosen['desc'][:80]}"
    supports = {rid: role_support(rid, chosen, is_case, actor_memory[rid]) for rid in ROLE_ORDER}
    concerns = {rid: ROLE_MAP[rid]["concern"] for rid in ROLE_ORDER}

    def past_hook(rid):
        mem = actor_memory[rid]
        if mem.get("conditions"):
            return f"我上一轮留下的条件是“{mem['conditions'][-1]}”，这轮仍然有效。"
        if mem.get("commitments"):
            return f"我此前承诺过“{mem['commitments'][-1]}”，因此这次不能无解释地反向。"
        return ""

    r1={
      "government":f"从政府视角，我不会先问这个方案‘新不新’，而会先看它是否同时解释产业目标、城市功能、公共利益和责任链条。对『{label}』，我尤其关心谁牵头、哪些部门要协同、什么节点复盘，以及如果效果不及预期如何调整。{past_hook('government')}",
      "collective":f"我把『{label}』先放进资产账里看：会占用多少高价值空间、产生多少新增投入、回收周期多长、最坏情况下由谁承担。长期价值可以讨论，但不能只用愿景替代现金流边界；如果要让渡短期收益，我需要看到预算上限、阶段目标和退出条件。{past_hook('collective')}",
      "operator":f"我先把『{label}』拆成现场任务：谁施工、谁招商、谁管活动、谁处理峰值客流、谁承担安保保洁、出现投诉谁响应。很多方案在会议室里成立，到现场会因为人手、动线和责任不清失效，所以我最关心执行复杂度和异常情况。{past_hook('operator')}",
      "startup":f"创业企业更在乎这个方案能不能缩短获得资源的时间。『{label}』如果能提高算力、融资、人才、科研合作、试用场景和客户连接效率，我愿意支持；如果只是增加活动数量、装修或流程，却让专业资源更分散，我会非常谨慎。{past_hook('startup')}",
      "public":f"我会把『{label}』翻译成普通人的一天：上下班是否更安全，周末能否进入，老人和孩子能否使用，活动有没有门槛，噪声和拥堵由谁承担，意见提交后有没有回应。公共价值不能只写在介绍牌上，必须能在具体生活场景里感受到。{past_hook('public')}"
    }
    round1=[{"role":rid,"name":ROLE_MAP[rid]["name"],"text":r1[rid],"support":supports[rid],"focus":concerns[rid]} for rid in ROLE_ORDER]

    # Round 2: every agent addresses multiple peers and posts multiple questions into the shared group.
    r2_text={
      "government":f"@集体经济组织 你强调现金流边界，我同意不能无限投入，但如果只把一层面积按即时租金计价，会遗漏创新网络和城市功能的长期收益；请你说明可以接受的试点损失上限与复盘周期。@运营方 你强调执行，我需要你把最容易失控的两个现场环节说清。@居民与公众 你要求可感知收益，请指出最优先的一项日常体验。对『{label}』，政府可以协调，但不能替其他主体永久兜底。",
      "collective":f"@政府 我接受长期价值进入决策，但请不要只给抽象公共目标：能否把公共目标转成可核查的阶段指标？@创业企业 你强调资源密度，我要追问企业愿意为高价值服务承担什么成本或贡献，不能所有资源都免费。@运营方 如果开放和活动继续增加，维护、人力和安全成本谁来测算？我的底线仍是风险可量化、投入可退出。",
      "operator":f"@政府 如果需要跨产权和跨部门协同，请明确谁有最终协调权，否则现场会不断等待。@集体经济组织 预算上限必须同时对应服务上限，不能要求压低成本又增加所有开放任务。@创业企业 你想要24小时、高频活动和快速场景，但高峰客流、安保和设备维护也会增加；你能接受哪些预约或分时规则？@公众 你最担心的使用冲突是什么？",
      "startup":f"@集体经济组织 我理解成本，但如果所有资源都按短期租金回报衡量，初创企业最需要的低成本试错空间会消失；我愿意讨论企业贡献机制。@运营方 我接受容量管理，但不要把流程做成层层审批，请说明怎样保证快速试点。@政府 如果强调公共价值，也请保证专业服务不被平均分散。@居民与公众 我愿意开放产品体验，但希望公众反馈能真正进入产品迭代，而不是只做展示。",
      "public":f"@政府 我最关心的不是活动数量，而是公共承诺有没有固定入口和反馈结果。@集体经济组织 我理解资产要保值，但公共空间长期带来的通行便利和生活服务是否也能进入你的‘收益’概念？@运营方 分时分区可以接受，但规则必须简单易懂。@创业企业 我支持创新，但请回答：当企业便利和居民安静、安全发生冲突时，你愿意让出什么？"
    }
    r2_suffix={
      "government":"另外，我不会接受‘大家都支持’这种模糊结论：如果集体经济组织认为预算不可承受，需要说清触发阈值；如果公众认为开放不足，也要把具体时段、空间和人群说清。只有把冲突量化成可复盘事项，政府协调才不会退化成反复开会。",
      "collective":"我还要把一个容易被忽略的问题摆上桌：公共价值和创新价值可以很重要，但它们不能天然等于资产增值。请政府和运营方分别说明，什么指标可以证明这些投入正在形成长期回报；如果连续两个阶段看不到改善，是否同意主动收缩。",
      "operator":"我还要提醒所有人，现场资源是有限的：同一批保安、保洁、招商主管和活动人员不可能同时承担无限新增任务。请政府说明跨部门事项的升级通道，请集体经济组织说明可追加多少运维预算，请企业和公众分别给出最不能妥协的一项使用需求。",
      "startup":"我还想追问政府和运营方：所谓开放和协同，最终有没有明确的企业获得感指标，例如资源匹配时间、试点审批时间、对接成功率？如果活动更多但连接效率下降，我们会把它视为负担。也请公众说明哪些专业活动其实可以通过预约、错峰而不是取消来解决。",
      "public":"我还要补充一个公平问题：谁获得便利、谁承担噪声和拥堵，不能只用平均数回答。请运营方说明高峰冲突怎么处理，请政府说明居民反馈多久能得到回应，请企业说明能否为社区提供可持续的体验、课程或问题解决，而不是偶尔做一次品牌活动。"
    }
    for rid in ROLE_ORDER: r2_text[rid]+=r2_suffix[rid]
    r2=[{"role":rid,"name":ROLE_MAP[rid]["name"],"text":r2_text[rid],"support":supports[rid],"responds_to":[ROLE_MAP[x]["name"] for x in ROLE_ORDER if x!=rid][:4],"questions":2} for rid in ROLE_ORDER]

    # Round 3: answer the group, explicitly differentiate evidence and risk lenses.
    r3_text={
      "government":f"回应群里的几个问题：对集体经济组织，我认为可采用‘小规模试点—阶段评估—满足条件再扩围’，让长期目标和资金边界同时存在；对运营方，跨部门事项必须指定单一牵头接口和超时升级机制；对公众，优先把安全通行与固定开放入口做成可检查承诺。与创业企业不同，我不会把资源密度当成唯一目标，因为如果周边承载和公众信任下降，创新网络自身也会失去稳定运行环境。",
      "collective":f"我回应政府：可以把创新连接、公共使用和公众反馈纳入绩效，但必须与资产收益分账核算，不能最后所有指标都好、现金流却没人负责。回应创业企业：企业若享受低成本空间和平台资源，应考虑导师、场景、活动或服务贡献，形成交换，而不是单向补贴。回应公众：我接受公共价值进入资产长期价值判断，但需要能看到使用量、维护成本和对整体资产吸引力的影响。我的风险偏好仍明显比其他主体谨慎。",
      "operator":f"回应政府和集体经济组织：我建议把『{label}』拆成最小可执行单元，每个单元都有责任人、容量、时段、预算和异常处理。回应创业企业：快速试点可以，但要设置无需多部门审批的低风险‘沙盒’，超过噪声、客流、安全阈值再升级审批。回应公众：分时分区规则必须在入口、预约页面和现场统一，不让使用者猜规则。我的关注点不在愿景，而在高峰时谁来处理真正发生的冲突。",
      "startup":f"回应集体经济组织：我接受资源交换，例如企业用导师、产品体验、数据洞察或场景服务换取平台支持，但不希望变成繁重行政任务。回应运营方：低风险试点沙盒很重要，最好规定清晰的额度和时限，避免每次创新都从零审批。回应公众：当夜间噪声、安全或通行冲突出现时，我可以接受高干扰活动错峰或迁移，但希望保留核心研发和专业交流。我的判断标准是连接效率和迭代速度，而不是空间看起来多热闹。",
      "public":f"回应创业企业：我接受部分专业空间和专业时段不完全开放，也接受低干扰研发持续进行；但高噪声、高客流活动应服从生活安全和基本休息。回应运营方：规则如果只写给专业用户就不算真正可及，需要老人、家长、骑手也能看懂。回应集体经济组织：公共空间带来的价值可以慢，但不能完全不可见，至少要跟踪通行改善、开放使用、投诉响应和家庭参与。我的侧重点与企业明显不同：我关心的是成本和收益有没有公平落到日常生活里。"
    }
    r3_suffix={
      "government":"我还会要求把争议事项放进同一张治理台账：哪些属于财政或资产问题，哪些属于空间规则，哪些属于公众反馈，分别由谁负责。这样下一轮再发生冲突时，不必重新争论原则，而是直接检查上一轮承诺是否履行。",
      "collective":"如果大家接受这种分账逻辑，我愿意把支持度提高，但我不会放弃最坏情景测试：招商不及预期、维护成本上升、公共空间使用率低时，谁先削减什么、谁承担沉没成本，都应在投入前讲清楚。",
      "operator":"此外，我希望所有人接受一个执行原则：每新增一个目标，就要对应新增资源或删减一项旧任务。否则‘协同’最后会变成运营团队用加班填补制度空白，这种模式短期能撑住，长期一定失效。",
      "startup":"我也愿意承担一部分可量化贡献，例如导师时数、产品体验、场景开放或企业间互助，但这些贡献最好与平台资源形成交换关系。这样企业参与公共价值不是额外负担，而能和自身成长形成正循环。",
      "public":"如果这些机制能被公开看见，我对专业优先的容忍度会更高。居民并不一定要求所有空间随时开放，真正敏感的是规则是否稳定、负担是否公平、反馈是否有效，以及出现冲突时有没有明确的人负责调整。"
    }
    for rid in ROLE_ORDER: r3_text[rid]+=r3_suffix[rid]
    r3=[{"role":rid,"name":ROLE_MAP[rid]["name"],"text":r3_text[rid],"support":min(5,supports[rid]+(1 if rid in ('government','operator') else 0)),"responds_to":[ROLE_MAP[x]["name"] for x in ROLE_ORDER if x!=rid][:4],"questions_answered":2} for rid in ROLE_ORDER]

    r4_base={
      "government":("接受先小规模试点，不要求一次性完成","设公共目标、责任清单、阶段评估与超时协调机制"),
      "collective":("接受可核算的短期机会成本","设投入上限、分账核算、追加投入阈值与退出条件"),
      "operator":("接受增加开放、协调与反馈任务","新增任务必须匹配人员预算，且每项跨主体事项只有一个牵头人"),
      "startup":("接受贡献部分导师、体验和场景资源","保留快速试点通道与高密度专业服务，避免资源平均化"),
      "public":("接受部分空间和时段优先服务专业创新","安全通行、固定公共入口、低门槛规则与投诉反馈必须稳定存在")
    }
    r4=[]
    for rid in ROLE_ORDER:
        con,cond=r4_base[rid]
        text=f"经过两轮群聊，我愿意把立场从‘只强调自己的优先项’推进到交换条件。我的让步是：{con}。但这不是无条件支持，我要求：{cond}。这条交换必须写进后续执行和复盘，而不是停留在口头共识；我还希望明确谁负责、何时检查、什么情形算未履行。如果这一条件在后续事件中被突破，我会降低支持度，并把它重新带回协商桌。"
        r4.append({"role":rid,"name":ROLE_MAP[rid]["name"],"text":text,"support":min(5,supports[rid]+1),"concession":con,"condition":cond})

    r5_text={
      "government":f"最终我对『{label}』持有条件支持。理由是它只有在产业目标、公共目标和责任机制同时可追踪时，才可能成为可持续治理方案。当前最值得保留的是分阶段试点和固定协商接口；仍未解决的风险是跨部门协调一旦失去牵头人，机制可能重新依赖临时推动。我会把‘阶段评估与责任清单’作为下一题继续检查的承诺。",
      "collective":f"最终我不是根据方案听起来是否先进来表态，而是看风险能否封顶。只要『{label}』设置投入上限、分账核算、复盘节点和退出阈值，我可以接受部分短期收益让渡；如果长期公共任务不断增加却没有成本分担，我会转为反对。我的核心侧重仍是资产可持续和资本韧性，这一点与政府、企业和公众不会完全一致。",
      "operator":f"最终我支持把『{label}』先做成可运行的最小版本，而不是一次铺满。我的判断取决于责任人、时段、容量、预算、现场SOP和异常升级路径是否齐全。群聊后我认可公众开放与企业效率可以通过分时分区协调，但真正的风险是执行任务不断叠加。下一轮如果又增加新目标，我会首先追问谁来做、用什么资源做。",
      "startup":f"最终我愿意支持『{label}』，前提是它提升真实连接效率，而不是只增加活动和行政流程。群聊后我接受用错峰、容量和贡献交换来降低外部成本，也愿意提供部分公众体验与导师资源；但如果专业网络被平均摊薄、试点速度明显下降，我会降低支持。我的雷达会明显更偏创新活力和协同，而非单纯公共开放。",
      "public":f"最终我可以接受『{label}』中有一部分资源优先服务企业，只要公众获得的不是象征性开放。群聊后我最看重三件事：安全通行有底线、公共时段和入口稳定、投诉和建议能看到处理结果。若这些都落实，我愿意接受合理的专业优先和试点不确定性；若生活成本由居民承担、收益却只在产业内部循环，我会重新反对。"
    }
    r5_suffix={rid:f" 我不会把本轮共识理解成永久授权：下一题若外部条件变化，我会依据自己的决策规则重新判断，但必须解释为什么改变。对我而言，真正可接受的治理不是所有人说同样的话，而是不同价值有稳定的谈判接口、清楚的责任边界和可追踪的履约记录。" for rid in ROLE_ORDER}
    for rid in ROLE_ORDER: r5_text[rid]+=r5_suffix[rid]
    r5=[]
    for rid in ROLE_ORDER:
        con,cond=r4_base[rid]
        r5.append({"role":rid,"name":ROLE_MAP[rid]["name"],"text":r5_text[rid],"support":min(5,supports[rid]+1),"concession":con,"condition":cond,"radar":fallback_radar(rid,chosen,actor_memory[rid])})

    agreement=int(sum(x['support'] for x in r5)/25*100)
    rounds=[
      {"round":1,"title":"独立立场","goal":"五个主体先按各自利益函数形成独立判断，不急于求同。","messages":round1},
      {"round":2,"title":"群聊质询 · 第一圈","goal":"每个主体同时点名多个其他主体，提出至少两个问题或反驳。","messages":r2},
      {"round":3,"title":"群聊交锋 · 第二圈","goal":"回答群内质询，并说明自己与其他主体判断标准为什么不同。","messages":r3},
      {"round":4,"title":"条件交换","goal":"明确自己愿意让什么、换取什么，以及哪条底线会带入下一题。","messages":r4},
      {"round":5,"title":"最终表态","goal":"在充分交锋后给出最终立场、未决风险与关注雷达。","messages":r5},
    ]
    state_delta,condition_delta=custom_impact_heuristic(custom_response) if choice_id=='D' else ({},{})
    result={
      "rounds":rounds,
      "turning_points":["五方从各自陈述转向同时质询多个主体，争议被拆成资金、执行、创新效率与公众可及四类问题","第三轮以后开始出现可交换条件，而不是简单赞成或反对"],
      "agreement_level":agreement,
      "unresolved":["机会成本与公共收益如何长期共同核算","当专业效率与生活体验再次冲突时，谁拥有最终调整权"],
      "negotiated_plan":["以可退出的小规模试点启动，并设置明确复盘节点","为资金、现场执行、专业资源和公众使用分别设置责任与阈值","保留固定五方协商接口，让本轮承诺和条件继续约束后续事件"],
      "facilitator":"本轮没有把五个主体‘统一成一种声音’，而是把不同利益函数保留下来，并通过群聊质询把冲突转化为可交换的条件。",
      "state_delta":state_delta,"condition_delta":condition_delta,
      "impact_explanation":"自由回答没有预设选项分值；这里的变化由本地语义规则估计，仅用于沙盘继续运行。" if choice_id=='D' else "",
      "mode":"mock"
    }
    result['actor_memory']=update_actor_memory(actor_memory,event,rounds,agreement)
    return result


def deliberation_stream_prompt(event, chosen, scores, conditions, scenario, actor_memory):
    roles_desc=[]
    for r in CASE['roles']:
        roles_desc.append({
          'id':r['id'],'name':r['name'],'concern':r['concern'],'base_preference':r.get('preference',{}),
          'decision_rule':r.get('decision_rule',''),'risk_attitude':r.get('risk_attitude',''),
          'evidence_focus':r.get('evidence_focus',[]),'red_lines':r.get('red_lines',[]),
          'preferred_tools':r.get('preferred_tools',[]),'speaking_style':r.get('speaking_style','')
        })
    is_custom=bool(chosen.get('is_custom'))
    system_prompt=(
      "你是公共管理案例教学沙盘中的五角色代理群聊协调器。严格区分‘案例事实’与‘情景推演’：只有输入中的案例事实可写成已发生事实，"
      "新增事件卡、自由回答效果、指数、雷达和协商结论都只是模拟。不得虚构新数据、新政策、新人物或新历史事件。"
      "五个角色必须表现得像五种不同利益函数，而不是五个换名字的同一个助手。必须持续使用各自decision_rule、risk_attitude、evidence_focus、red_lines和preferred_tools。"
      "禁止五个角色重复同一种论证结构、相同结论句或相同风险清单；至少三名角色应在关键问题上存在清晰分歧。若最终都支持，也必须说明支持理由、优先级和底线为何不同。"
      "五个角色有跨题记忆：不能无故忘记过去承诺和条件；若立场变化必须说明触发原因。"
      "第2、3轮是共享群聊，不是一对一轮流问答：每名角色必须同时回应至少2名其他角色，至少明确赞成/修正1个观点、反驳1个观点，并提出或回答至少2个具体问题。"
      "从第2轮开始发言必须充分展开，禁止只写一两句话。要出现具体权衡、风险、证据需求、责任主体或执行机制。"
      "你正在被实时流式解析，必须严格使用标签协议；除协议内容外不要输出Markdown、代码围栏或解释。"
      "第5轮每个角色的radar为本轮最终发言的议题关注强度，不是绩效或事实测量；雷达必须与正文高度一致，五个角色形状要有明显差异。"
    )
    protocol="""严格输出协议：
[[ROUND]]{"round":1,"title":"独立立场","goal":"..."}[[/ROUND]]
[[ROLE]]{"round":1,"role":"government","support":1-5,"focus":"..."}[[/ROLE]]
这里写120-180字自然发言正文
[[ENDROLE]]
依次 collective、operator、startup、public。

第2轮：ROUND标题“群聊质询 · 第一圈”。五个ROLE固定顺序，每个正文220-320字；ROLE元数据包含"responds_to":["至少2个主体名"],"questions":2。正文中使用@主体名，至少回应2人、反驳1点、提出2个具体问题。
第3轮：ROUND标题“群聊交锋 · 第二圈”。五个ROLE固定顺序，每个正文220-320字；元数据包含"responds_to":[...],"questions_answered":2。必须回应上一轮群聊中的多个问题，并明确自己与其他主体的判断标准差异。
第4轮：ROUND标题“条件交换”。每个正文160-240字；ROLE元数据包含"concession":"...","condition":"..."。让步和条件必须具体到责任、阈值、时段、预算、反馈或退出机制之一。
第5轮：ROUND标题“最终表态”。每个正文180-260字；ROLE元数据包含"concession":"...","condition":"...","radar":{"asset":0-100,"innovation":0-100,"collaboration":0-100,"openness":0-100,"public_value":0-100}。最终正文必须包含：最终支持度理由、最重要未决风险、下一轮仍会坚持的底线。

最后：
[[SUMMARY]]{"turning_points":["2-3条"],"agreement_level":0-100,"unresolved":["2-4条"],"negotiated_plan":["3-4条"],"facilitator":"...","state_delta":{"asset":-12到12,"innovation":-12到12,"collaboration":-12到12,"openness":-12到12,"public_value":-12到12},"condition_delta":{"capital_resilience":-12到12,"tenant_stability":-12到12,"execution_capacity":-12到12,"street_capacity":-12到12,"public_trust":-12到12,"spillover":-12到12},"impact_explanation":"..."}[[ENDSUMMARY]]
如果不是自由回答D，state_delta和condition_delta必须全部为0；如果是D，则根据自由回答的机制含义给出克制的模拟变化，绝不能声称为实证效果。
ROLE正文只能放在[[/ROLE]]之后、[[ENDROLE]]之前，正文中不要出现任何双中括号标签。"""
    user_prompt=f"""【事件类型】{event.get('event_type','案例节点')}
【事件】{event['title']}
【情境】{event['situation']}
【已知案例事实/情景说明】{event['fact']}
【材料标识】{event.get('source_section','')}
【参与者选择】{chosen['id']}. {chosen['label']} —— {chosen['desc']}
【是否自由回答】{'是' if is_custom else '否'}
【公共价值五维】{json.dumps(scores,ensure_ascii=False)}
【运行条件六维】{json.dumps(conditions,ensure_ascii=False)}
【外部情景参数】{json.dumps(scenario,ensure_ascii=False)}
【五角色差异画像】{json.dumps(roles_desc,ensure_ascii=False)}
【主体跨题记忆】{json.dumps(memory_prompt_view(actor_memory),ensure_ascii=False)}

强制差异化：政府从制度/公共目标/跨部门责任论证；集体经济组织从现金流/资产/机会成本论证；运营方从SOP/容量/人力/异常处理论证；创业企业从资源密度/速度/试错/合作转化论证；公众从安全/公平/可进入/生活干扰/反馈论证。不得互相替代视角。
{protocol}"""
    return system_prompt,user_prompt


def stream_tagged_protocol(system_prompt, user_prompt, max_tokens=5600, temperature=0.45):
    """Parse a tagged model stream and yield round/message_start/delta/message_end/summary events.

    Text inside each ROLE block is forwarded while tokens are still arriving, so the browser
    does not need to wait for the whole subject message or the whole deliberation.
    """
    buffer = ""
    state = "scan"
    role_meta = None
    markers = {"[[ROUND]]": "round_meta", "[[ROLE]]": "role_meta", "[[SUMMARY]]": "summary_meta"}
    end_role = "[[ENDROLE]]"
    while True:
        got_chunk = False
        for chunk in aiping_stream_text(system_prompt, user_prompt, max_tokens=max_tokens, temperature=temperature):
            got_chunk = True
            buffer += chunk
            while True:
                if state == "scan":
                    hits = [(buffer.find(m), m, st) for m, st in markers.items() if buffer.find(m) >= 0]
                    if not hits:
                        # Keep only a short suffix that might contain a split marker.
                        if len(buffer) > 32:
                            buffer = buffer[-32:]
                        break
                    idx, marker, new_state = min(hits, key=lambda x: x[0])
                    buffer = buffer[idx + len(marker):]
                    state = new_state
                    continue
                if state == "round_meta":
                    end = "[[/ROUND]]"
                    idx = buffer.find(end)
                    if idx < 0:
                        break
                    raw = buffer[:idx].strip()
                    buffer = buffer[idx + len(end):]
                    try:
                        meta = json.loads(raw)
                        yield {"type": "round", **meta}
                    except Exception:
                        pass
                    state = "scan"
                    continue
                if state == "role_meta":
                    end = "[[/ROLE]]"
                    idx = buffer.find(end)
                    if idx < 0:
                        break
                    raw = buffer[:idx].strip()
                    buffer = buffer[idx + len(end):]
                    try:
                        role_meta = json.loads(raw)
                    except Exception:
                        role_meta = None
                    if role_meta:
                        yield {"type": "message_start", **role_meta}
                        state = "role_text"
                    else:
                        state = "scan"
                    continue
                if state == "role_text":
                    idx = buffer.find(end_role)
                    if idx >= 0:
                        text = buffer[:idx]
                        buffer = buffer[idx + len(end_role):]
                        if text:
                            yield {"type": "delta", "round": role_meta.get("round"), "role": role_meta.get("role"), "text": text}
                        yield {"type": "message_end", "round": role_meta.get("round"), "role": role_meta.get("role")}
                        role_meta = None
                        state = "scan"
                        continue
                    # Protect against sending a partial ENDROLE marker as visible text.
                    safe_len = max(0, len(buffer) - (len(end_role) - 1))
                    if safe_len > 0:
                        text = buffer[:safe_len]
                        buffer = buffer[safe_len:]
                        if text:
                            yield {"type": "delta", "round": role_meta.get("round"), "role": role_meta.get("role"), "text": text}
                    break
                if state == "summary_meta":
                    end = "[[ENDSUMMARY]]"
                    idx = buffer.find(end)
                    if idx < 0:
                        break
                    raw = buffer[:idx].strip()
                    buffer = buffer[idx + len(end):]
                    try:
                        meta = json.loads(raw)
                        yield {"type": "summary", **meta}
                    except Exception:
                        pass
                    state = "scan"
                    continue
                break
            # Continue the upstream token loop.
        if not got_chunk:
            break
        break


def stream_records_from_aiping(system_prompt, user_prompt, max_tokens=5600, temperature=0.5):
    buffer = ""
    for chunk in aiping_stream_text(system_prompt, user_prompt, max_tokens=max_tokens, temperature=temperature):
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            line = re.sub(r"^```(?:json)?\s*", "", line, flags=re.I)
            line = re.sub(r"\s*```$", "", line)
            try:
                yield json.loads(line)
            except Exception:
                if not line.endswith("}"):
                    buffer = line + "\n" + buffer
                    break
        # continue token stream
    tail = buffer.strip()
    tail = re.sub(r"^```(?:json)?\s*", "", tail, flags=re.I)
    tail = re.sub(r"\s*```$", "", tail)
    if tail:
        try:
            yield json.loads(tail)
        except Exception:
            pass

def deliberation_stream(body):
    event_id=int(body.get("event_id",0) or 0)
    choice_id=str(body.get("choice","")).upper()
    custom_response=str(body.get("custom_response") or "").strip()
    scores=body.get("scores") or {}
    conditions=body.get("conditions") or {}
    scenario=body.get("scenario") or {}
    actor_memory=sanitize_actor_memory(body.get("actor_memory"))
    event=event_by_id(event_id)
    if not event:
        yield {"type":"error","message":"事件不存在"}; return
    if choice_id=='D':
        if len(custom_response)<4:
            yield {"type":"error","message":"自由回答至少输入4个字"}; return
        chosen=make_custom_choice(custom_response)
    else:
        chosen=choice_for(event,choice_id)
        if not chosen:
            yield {"type":"error","message":"选项不存在"}; return

    yield {"type":"status","stage":"connected","message":"五角色代理群聊已建立，正在实时返回25次发言…","mode":"aiping" if AIPING_API_KEY and not MOCK_MODE else "mock"}
    if MOCK_MODE or not AIPING_API_KEY:
        result=mock_deliberation(event,choice_id,scores,conditions,scenario,actor_memory,custom_response)
        for rnd in result['rounds']:
            yield {"type":"round","round":rnd['round'],"title":rnd['title'],"goal":rnd.get('goal','')}
            for msg in rnd['messages']:
                yield {"type":"message","round":rnd['round'],**msg}
        yield {"type":"summary","turning_points":result['turning_points'],"agreement_level":result['agreement_level'],"unresolved":result['unresolved'],"negotiated_plan":result['negotiated_plan'],"facilitator":result['facilitator'],"state_delta":result.get('state_delta',{}),"condition_delta":result.get('condition_delta',{}),"impact_explanation":result.get('impact_explanation','')}
        yield {"type":"memory","actor_memory":result['actor_memory']}
        yield {"type":"done","mode":"mock"}; return

    titles={1:'独立立场',2:'群聊质询 · 第一圈',3:'群聊交锋 · 第二圈',4:'条件交换',5:'最终表态'}
    rounds={i:{"round":i,"title":titles[i],"goal":"","messages":[]} for i in range(1,6)}
    summary=None
    try:
        system_prompt,user_prompt=deliberation_stream_prompt(event,chosen,scores,conditions,scenario,actor_memory)
        active={}
        for rec in stream_tagged_protocol(system_prompt,user_prompt,max_tokens=11500,temperature=0.58):
            typ=rec.get('type')
            if typ=='round':
                rn=int(rec.get('round',0) or 0)
                if rn in rounds:
                    rounds[rn]['title']=str(rec.get('title') or rounds[rn]['title'])[:100]
                    rounds[rn]['goal']=str(rec.get('goal') or '')[:300]
                    yield {"type":"round","round":rn,"title":rounds[rn]['title'],"goal":rounds[rn]['goal']}
            elif typ=='message_start':
                rn=int(rec.get('round',0) or 0); rid=rec.get('role')
                if rn not in rounds or rid not in ROLE_MAP: continue
                msg={"role":rid,"name":ROLE_MAP[rid]['name'],"text":"","support":max(1,min(5,int(rec.get('support',3) or 3)))}
                for key in ('focus','concession','condition'):
                    if rec.get(key): msg[key]=str(rec.get(key))[:320]
                for key in ('responds_to',):
                    val=rec.get(key)
                    if isinstance(val,list): msg[key]=[str(x)[:80] for x in val][:5]
                    elif val: msg[key]=[x.strip() for x in str(val).split('、') if x.strip()][:5]
                for key in ('questions','questions_answered'):
                    if rec.get(key) is not None:
                        try: msg[key]=int(rec.get(key))
                        except Exception: pass
                if rn==5: msg['radar']=normalize_radar(rec.get('radar'),rid,chosen,actor_memory[rid])
                active[(rn,rid)]=msg
                yield {"type":"message_start","round":rn,**{k:v for k,v in msg.items() if k!='text'}}
            elif typ=='delta':
                rn=int(rec.get('round',0) or 0); rid=rec.get('role'); key=(rn,rid)
                if key in active:
                    text=str(rec.get('text') or '')
                    active[key]['text']=(active[key]['text']+text)[:2800]
                    yield {"type":"delta","round":rn,"role":rid,"text":text}
            elif typ=='message_end':
                rn=int(rec.get('round',0) or 0); rid=rec.get('role')
                msg=active.pop((rn,rid),None)
                if msg and rn in rounds and rid in ROLE_MAP:
                    msg['text']=msg['text'].strip(); rounds[rn]['messages'].append(msg)
                    yield {"type":"message_end","round":rn,"role":rid}
            elif typ=='summary':
                state_delta=normalize_signed_delta(rec.get('state_delta'),RADAR_KEYS) if choice_id=='D' else {k:0 for k in RADAR_KEYS}
                condition_delta=normalize_signed_delta(rec.get('condition_delta'),CASE['condition_metrics'].keys()) if choice_id=='D' else {k:0 for k in CASE['condition_metrics'].keys()}
                summary={
                  'turning_points':[str(x)[:360] for x in (rec.get('turning_points') or [])][:4],
                  'agreement_level':clamp(rec.get('agreement_level',60)),
                  'unresolved':[str(x)[:360] for x in (rec.get('unresolved') or [])][:4],
                  'negotiated_plan':[str(x)[:420] for x in (rec.get('negotiated_plan') or [])][:4],
                  'facilitator':str(rec.get('facilitator') or '')[:900],
                  'state_delta':state_delta,'condition_delta':condition_delta,
                  'impact_explanation':str(rec.get('impact_explanation') or '')[:500]
                }
                yield {"type":"summary",**summary}
        complete=all(len(rounds[i]['messages'])>=5 for i in range(1,6)) and summary is not None
        if not complete: raise RuntimeError('流式协议未完整返回25条主体发言或协商总结')
        updated_memory=update_actor_memory(actor_memory,event,[rounds[i] for i in range(1,6)],summary['agreement_level'])
        yield {"type":"memory","actor_memory":updated_memory}
        yield {"type":"done","mode":"aiping"}
    except Exception as e:
        yield {"type":"status","stage":"fallback","message":f"实时模型中断，正在切换本地保障内容：{str(e)[:140]}","mode":"fallback"}
        result=mock_deliberation(event,choice_id,scores,conditions,scenario,actor_memory,custom_response)
        for rnd in result['rounds']:
            rn=rnd['round']; existing_roles={m.get('role') for m in rounds.get(rn,{}).get('messages',[])}
            yield {"type":"round","round":rn,"title":rnd['title'],"goal":rnd.get('goal','')}
            for msg in rnd['messages']:
                if msg.get('role') not in existing_roles: yield {"type":"message","round":rn,**msg}
        if summary is None:
            yield {"type":"summary","turning_points":result['turning_points'],"agreement_level":result['agreement_level'],"unresolved":result['unresolved'],"negotiated_plan":result['negotiated_plan'],"facilitator":result['facilitator'],"state_delta":result.get('state_delta',{}),"condition_delta":result.get('condition_delta',{}),"impact_explanation":result.get('impact_explanation','')}
        yield {"type":"memory","actor_memory":result['actor_memory']}
        yield {"type":"done","mode":"fallback"}


def mock_report(history, scores, conditions, scenario, actor_memory=None, memory_style="实际"):
    memory_style = memory_style if memory_style in {"文艺", "实际", "官方"} else "实际"
    case_hits = sum(1 for h in history if h.get("choice") == h.get("case_choice"))
    strongest = max(scores, key=scores.get)
    weakest_condition = min(conditions, key=conditions.get) if conditions else None
    metric_name = CASE["metrics"].get(strongest, strongest)
    cond_name = CASE["condition_metrics"].get(weakest_condition, {}).get("name", weakest_condition or "运行条件")
    if scores.get("collaboration", 0) >= 75 and scores.get("public_value", 0) >= 75:
        profile = "协同公共价值型治理者"
    elif scores.get("innovation", 0) >= 78:
        profile = "创新网络驱动型治理者"
    elif scores.get("asset", 0) >= 78:
        profile = "资产稳健型治理者"
    else:
        profile = "动态平衡型治理者"
    branch_titles = [h.get("title", "") for h in history if h.get("branch_note")]
    achievement = {"协同公共价值型治理者":"协同筑桥者","创新网络驱动型治理者":"创新点火者","资产稳健型治理者":"稳健守门人","动态平衡型治理者":"动态平衡者"}.get(profile, "治理探索者")
    if memory_style == "文艺":
        memory_quote = f"你在创新与日常之间反复搭桥，让这座街区的光亮既照见创业者，也照见每一个经过的人。"
    elif memory_style == "官方":
        memory_quote = f"本轮模拟形成了兼顾{metric_name}与运行约束的阶段性治理路径，并通过多主体协商增强了方案的可执行性。"
    else:
        memory_quote = f"这局你最明显的选择，是在推进{metric_name}的同时不断给资金、执行和公众需求设置可复盘的边界。"
    return {
        "profile": profile,
        "one_sentence": f"你完成了{len(history)}轮推演，其中{case_hits}轮与案例实际路径相近；最突出的公共价值维度是“{metric_name}”。",
        "route_summary": f"你的路线经历了{len(branch_titles)}个由状态变量触发的分支事件；五类主体的承诺与支持条件也被持续带入后续协商。",
        "strengths": ["能够在多主体冲突中继续推进决策", "注意创新空间、公共空间与组织机制之间的联动", "把主体承诺和支持条件保留到后续事件，而不是每题重新开始"],
        "risks": [f"当前运行条件中相对薄弱的是“{cond_name}”，后续选择容易在这里形成瓶颈", "情景指数、关注雷达与AI协商均属教学推演，不应替代真实调查或实证数据"],
        "public_value_judgement": "公共价值在本沙盘中体现为政府、集体经济组织、运营方、企业与公众围绕成本、收益、空间和机会持续协商，并在跨轮承诺约束下形成可共享结果。",
        "next_actions": ["优先修复最低的运行条件，而不是继续单纯抬高最高分", "把协商中的未决问题转化为责任主体、时间节点和反馈指标", "复盘因果网络中被多次影响的关键维度，识别路径依赖"],
        "memory_quote": memory_quote,
        "memory_style": memory_style,
        "memory_achievement": achievement,
        "mode": "mock",
    }


def normalize_memory_quote(text, fallback="完成了一次治理模拟。"):
    value = re.sub(r"\s+", "", str(text or "")).strip().strip('“”"')
    if not value:
        value = fallback
    # 强制只保留第一句，避免模型返回两三句话把便利贴撑长。
    m = re.search(r"[。！？!?]", value)
    if m:
        value = value[:m.start()+1]
    else:
        value = value[:82].rstrip("，,；;：:") + "。"
    return value[:90]


def normalize_achievement(text, fallback="治理探索者"):
    value = re.sub(r"\s+", "", str(text or "")).strip('“”"')
    return (value[:8] or fallback)


def report_payload(body):
    history = body.get("history") or []
    scores = {k: clamp(v) for k, v in (body.get("scores") or CASE["initial_scores"]).items()}
    conditions = {k: clamp(v) for k, v in (body.get("conditions") or CASE["initial_conditions"]).items()}
    scenario = body.get("scenario") or {}
    actor_memory = sanitize_actor_memory(body.get("actor_memory"))
    memory_style = str(body.get("memory_style") or "实际")
    if memory_style not in {"文艺", "实际", "官方"}: memory_style = "实际"
    simulation_mode = str(body.get("simulation_mode") or "normal")
    system_prompt = (
        "你是公共管理案例教学的评估助手。根据治理沙盘完整路线与五类主体跨题记忆生成诊断。"
        "严格区分案例事实与情景推演，不得把指数、雷达、分支、模型判断写成真实统计。你还要为本局生成一张回忆便利贴的一句话总结。只输出严格JSON。"
    )
    user_prompt = f"""用户完整决策记录：{json.dumps(history, ensure_ascii=False)}
最终公共价值五维：{json.dumps(scores, ensure_ascii=False)}
最终运行条件六维：{json.dumps(conditions, ensure_ascii=False)}
外部情景参数：{json.dumps(scenario, ensure_ascii=False)}
本局模式：{simulation_mode}
回忆句要求风格：{memory_style}
主体最终记忆：{json.dumps(memory_prompt_view(actor_memory), ensure_ascii=False)}
指标含义：{json.dumps(CASE['metrics'], ensure_ascii=False)}
案例最终关注：多元主体如何协同形成兼具创新承载与公共开放功能的治理场域，以及公共价值如何转化为可共享、可持续的城市生活成果。
回忆墙要求：memory_quote必须严格只有一句话，约30-70个汉字；memory_style必须原样返回“{memory_style}”；memory_achievement给一个4-8字的成就称号。文艺风格可有比喻但不得虚构事实；实际风格突出选择与权衡；官方风格使用正式、克制的治理表述。
返回：{{"profile":"8-16字治理类型","one_sentence":"一句话总结","route_summary":"一句话解释路径依赖和主体记忆","strengths":["3条"],"risks":["2条"],"public_value_judgement":"100字以内判断","next_actions":["3条"],"memory_quote":"严格一句话","memory_style":"{memory_style}","memory_achievement":"4-8字成就称号"}}"""
    try:
        result = aiping_chat(system_prompt, user_prompt, max_tokens=1800, temperature=0.35)
        result["memory_style"] = memory_style
        result["memory_quote"] = normalize_memory_quote(result.get("memory_quote"), result.get("one_sentence") or "完成了一次治理模拟。")
        result["memory_achievement"] = normalize_achievement(result.get("memory_achievement"), result.get("profile") or "治理探索者")
        result["mode"] = "aiping"
        return 200, result
    except Exception as e:
        result = mock_report(history, scores, conditions, scenario, actor_memory, memory_style)
        result["memory_quote"] = normalize_memory_quote(result.get("memory_quote"), result.get("one_sentence") or "完成了一次治理模拟。")
        result["memory_achievement"] = normalize_achievement(result.get("memory_achievement"), result.get("profile") or "治理探索者")
        result["fallback_reason"] = str(e)
        return 200, result


def mock_challenge(proposal, actor_memory=None):
    actor_memory=sanitize_actor_memory(actor_memory)
    base={
      'government':f"我先把你的方案“{proposal[:90]}”放进城市治理框架里看：需要同时回答产业目标、公共目标、责任主体和复盘机制。若只有愿景没有制度接口，我不会直接支持。",
      'collective':f"我先算“{proposal[:90]}”的资金账：新增投入、占用空间、维护成本、回收周期和最坏情况谁承担。可以试，但必须有预算上限和退出条件。",
      'operator':f"我会把“{proposal[:90]}”拆成现场任务和SOP：人员、时段、容量、安保、保洁、投诉、异常升级都要有人负责，否则方案越丰富，执行越容易失控。",
      'startup':f"我最关心“{proposal[:90]}”是否提高企业获得算力、融资、人才、科研合作和试用场景的速度。如果资源被活动和流程稀释，我不会因为口号漂亮就支持。",
      'public':f"我会问“{proposal[:90]}”具体改变普通人的什么：通行、开放时段、家庭使用、噪声、拥堵和反馈是否更好。若只是名义开放，我不会把它当成公共价值。"
    }
    rounds=[{'round':1,'title':'独立立场','goal':'五方先形成独立判断','messages':[{'role':rid,'name':ROLE_MAP[rid]['name'],'text':base[rid],'support':3 if rid in ('collective','public') else 4,'focus':ROLE_MAP[rid]['concern']} for rid in ROLE_ORDER]}]
    qtexts={
      'government':'@集体经济组织 请给出可接受的投入边界；@运营方 请指出两个最易失控的执行环节；@公众 请提出最优先的生活收益。政府可以协调，但不会替所有主体永久兜底。',
      'collective':'@政府 公共目标如何转成可核查指标？@创业企业 享受资源后企业愿意贡献什么？@运营方 新增开放任务的长期维护成本由谁算、谁付？',
      'operator':'@政府 跨部门卡点谁有最终牵头权？@创业企业 你能接受哪些容量和时段规则？@公众 哪类冲突最需要现场优先处理？请别同时要求“无限开放、零成本、零等待”。',
      'startup':'@集体经济组织 我可以讨论资源交换，但不接受所有创新都按即时租金回报；@运营方 请给快速试点留沙盒；@公众 我愿意开放体验，但高密度专业网络不能被平均化。',
      'public':'@政府 公共承诺如何看到结果？@集体经济组织 公共空间长期便利能否进入资产价值？@创业企业 当企业便利与居民安静、安全冲突时，你愿意让出什么？'
    }
    rounds.append({'round':2,'title':'群聊质询 · 第一圈','goal':'每人同时质询多个主体','messages':[{'role':rid,'name':ROLE_MAP[rid]['name'],'text':qtexts[rid],'support':4,'responds_to':[ROLE_MAP[x]['name'] for x in ROLE_ORDER if x!=rid][:4],'questions':2} for rid in ROLE_ORDER]})
    atexts={
      'government':'回应群聊：可以采用小规模试点、阶段评估和超时升级，把长期目标与资金边界并存；我不同意只看短期租金，也不同意把公共目标无限扩张。',
      'collective':'回应群聊：我接受创新和公共指标进入绩效，但要与现金流分账；企业若获得优惠应提供可量化贡献，公共任务若持续增加也必须有成本分担。',
      'operator':'回应群聊：建议建立低风险试点沙盒，并为每项任务指定唯一牵头人、容量、时段和异常处理；我的判断标准是现场是否跑得动，而不是方案写得多完整。',
      'startup':'回应群聊：我接受错峰、容量和资源贡献机制，但审批不能拖慢试错；我支持公众体验，只要它能形成真实反馈而不稀释专业网络。',
      'public':'回应群聊：我接受部分专业优先时段，但通行、安全和固定公共入口不能被临时活动挤掉；规则必须让老人、家长和骑手都能看懂。'
    }
    rounds.append({'round':3,'title':'群聊交锋 · 第二圈','goal':'回答多人质询并拉开判断标准','messages':[{'role':rid,'name':ROLE_MAP[rid]['name'],'text':atexts[rid],'support':4,'responds_to':[ROLE_MAP[x]['name'] for x in ROLE_ORDER if x!=rid][:4],'questions_answered':2} for rid in ROLE_ORDER]})
    terms={
      'government':('接受先试点再扩围','公共目标、责任清单和阶段复盘必须公开'),
      'collective':('接受可核算的短期收益让渡','投入上限、分账核算与退出阈值必须明确'),
      'operator':('接受增加开放和协调任务','新增任务要匹配人员预算且只有一个牵头接口'),
      'startup':('接受贡献导师、体验和场景','保留快速试点与高密度专业资源'),
      'public':('接受部分专业优先时段','安全通行、固定公众入口与投诉反馈必须稳定')
    }
    rounds.append({'round':4,'title':'条件交换','goal':'用具体让步交换具体条件','messages':[{'role':rid,'name':ROLE_MAP[rid]['name'],'text':f"我的让步是：{terms[rid][0]}。作为交换，我要求：{terms[rid][1]}。这条条件会继续带入后续判断。",'support':4,'concession':terms[rid][0],'condition':terms[rid][1]} for rid in ROLE_ORDER]})
    rounds.append({'round':5,'title':'最终协议','goal':'形成有差异的最终表态','messages':[{'role':rid,'name':ROLE_MAP[rid]['name'],'text':f"最终我对该方案有条件支持。我的核心理由仍来自{ROLE_MAP[rid]['concern']}。我同意进入试运行，但若“{terms[rid][1]}”无法落实，我会重新降低支持。未决风险需要下一轮继续复盘。",'support':5 if rid=='government' else 4,'concession':terms[rid][0],'condition':terms[rid][1],'radar':fallback_radar(rid,{},actor_memory[rid])} for rid in ROLE_ORDER]})
    return {'rounds':rounds,'main_tradeoff':'创新速度、资产边界、执行容量与公众可及性需要在同一套责任机制中交换条件。','agreement_level':84,'negotiated_plan':['先做可退出的小规模试点','明确投入上限、责任清单、容量规则和公开复盘节点','保留专业资源密度，同时设置稳定公众入口与反馈接口'],'warning':'这是情景推演，不是对真实政策效果的事实判断。','verdict':'有条件支持','mode':'mock'}


def challenge_stream_prompt(proposal,scores,conditions,scenario,actor_memory):
    role_profiles={r['id']:{k:r.get(k) for k in ('decision_rule','risk_attitude','evidence_focus','red_lines','preferred_tools','speaking_style')} for r in CASE['roles']}
    system_prompt=("你是AI市长挑战的五角色代理群聊协调器。方案只做情景推演。五角色必须按各自利益函数输出明显不同的论证。"
      "共5轮：独立立场、群聊质询第一圈、群聊交锋第二圈、条件交换、最终协议。第2和第3轮每个角色必须同时回应至少2名其他角色，内容充分展开。"
      "只输出NDJSON，每行一个JSON；不要Markdown。第5轮radar是发言关注强度，不是绩效评分。")
    protocol="""顺序：round1 + 5 message；round2 + 5 message；round3 + 5 message；round4 + 5 message；round5 + 5 message；最后summary。
round行字段：type=round,round,title,goal。
message字段：type=message,round,role,text,support。第1轮120-180字；第2、3轮220-320字，第2轮加responds_to数组和questions=2，第3轮加responds_to数组和questions_answered=2；第4轮160-240字，加concession和condition；第5轮180-260字，加concession、condition、radar五维。
summary字段：type=summary,main_tradeoff,agreement_level,negotiated_plan(3-4条),warning,verdict。role固定government,collective,operator,startup,public。每个JSON独占一行，字符串内部不要换行。"""
    user_prompt=f"""用户方案：{proposal}\n当前公共价值：{json.dumps(scores,ensure_ascii=False)}\n当前运行条件：{json.dumps(conditions,ensure_ascii=False)}\n外部情景：{json.dumps(scenario,ensure_ascii=False)}\n主体跨题记忆：{json.dumps(memory_prompt_view(actor_memory),ensure_ascii=False)}\n五角色差异画像：{json.dumps(role_profiles,ensure_ascii=False)}\n{protocol}"""
    return system_prompt,user_prompt


def challenge_stream(body):
    proposal=str(body.get('proposal') or '').strip(); scores=body.get('scores') or CASE['initial_scores']; conditions=body.get('conditions') or CASE['initial_conditions']; scenario=body.get('scenario') or {}; actor_memory=sanitize_actor_memory(body.get('actor_memory'))
    if not proposal: yield {'type':'error','message':'请输入你的治理方案'}; return
    yield {'type':'status','message':'五轮代理群聊已启动，正在逐主体返回…','mode':'aiping' if AIPING_API_KEY and not MOCK_MODE else 'mock'}
    if MOCK_MODE or not AIPING_API_KEY:
        r=mock_challenge(proposal,actor_memory)
        for rnd in r['rounds']:
            yield {'type':'round','round':rnd['round'],'title':rnd['title'],'goal':rnd.get('goal','')}
            for msg in rnd['messages']: yield {'type':'message','round':rnd['round'],**msg}
        yield {'type':'summary','main_tradeoff':r['main_tradeoff'],'agreement_level':r['agreement_level'],'negotiated_plan':r['negotiated_plan'],'warning':r['warning'],'verdict':r['verdict']}; yield {'type':'done','mode':'mock'}; return
    try:
        sys_p,usr_p=challenge_stream_prompt(proposal,scores,conditions,scenario,actor_memory); got_summary=False; count=0
        for rec in stream_records_from_aiping(sys_p,usr_p,max_tokens=11000,temperature=.6):
            typ=rec.get('type')
            if typ=='round': yield {'type':'round','round':int(rec.get('round',0) or 0),'title':str(rec.get('title') or '')[:100],'goal':str(rec.get('goal') or '')[:300]}
            elif typ=='message' and rec.get('role') in ROLE_MAP:
                rid=rec['role']; rn=int(rec.get('round',0) or 0); msg={'type':'message','round':rn,'role':rid,'name':ROLE_MAP[rid]['name'],'text':str(rec.get('text') or '')[:2600],'support':max(1,min(5,int(rec.get('support',3) or 3)))}
                val=rec.get('responds_to');
                if isinstance(val,list): msg['responds_to']=[str(x)[:80] for x in val][:5]
                elif val: msg['responds_to']=[str(val)[:80]]
                for k in ('questions','questions_answered'):
                    if rec.get(k) is not None:
                        try: msg[k]=int(rec.get(k))
                        except Exception: pass
                for k in ('concession','condition'):
                    if rec.get(k): msg[k]=str(rec.get(k))[:320]
                if rn==5: msg['radar']=normalize_radar(rec.get('radar'),rid,{},actor_memory[rid])
                count+=1; yield msg
            elif typ=='summary':
                got_summary=True; yield {'type':'summary','main_tradeoff':str(rec.get('main_tradeoff') or '')[:600],'agreement_level':clamp(rec.get('agreement_level',60)),'negotiated_plan':[str(x)[:400] for x in (rec.get('negotiated_plan') or [])][:4],'warning':str(rec.get('warning') or '')[:500],'verdict':str(rec.get('verdict') or '有条件支持')[:80]}
        if count<25 or not got_summary: raise RuntimeError('五轮群聊协议返回不完整')
        yield {'type':'done','mode':'aiping'}
    except Exception as e:
        yield {'type':'status','message':f"实时模型中断，切换本地保障内容：{str(e)[:120]}",'mode':'fallback'}; r=mock_challenge(proposal,actor_memory)
        for rnd in r['rounds']:
            yield {'type':'round','round':rnd['round'],'title':rnd['title'],'goal':rnd.get('goal','')}
            for msg in rnd['messages']: yield {'type':'message','round':rnd['round'],**msg}
        yield {'type':'summary','main_tradeoff':r['main_tradeoff'],'agreement_level':r['agreement_level'],'negotiated_plan':r['negotiated_plan'],'warning':r['warning'],'verdict':r['verdict']}; yield {'type':'done','mode':'fallback'}




def simple_terms(text: str):
    text = str(text or '').lower()
    out = []
    for w in re.findall(r"[a-z0-9_]{2,}", text):
        out.append(w)
    for seq in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        seq = seq.strip()
        if not seq:
            continue
        out.append(seq)
        for n in (2, 3):
            if len(seq) >= n:
                out.extend(seq[i:i+n] for i in range(len(seq)-n+1))
    stop = {'这个','那个','以及','因为','所以','我们','你们','他们','如何','什么','为什么','是否','一个','一种','案例','作品','方案','问题','详细','更加','可以','进行','就是','不是','如果','关于','对于','还有'}
    return [x for x in out if x not in stop]


def knowledge_chunks():
    chunks = []
    for item in CASE.get('report_sections', []):
        chunks.append({'title': item.get('title',''), 'text': item.get('text',''), 'kind': '团队报告正文', 'url': '', 'keywords': item.get('keywords', []), 'source': item.get('source','团队案例分析报告')})
    for item in CASE.get('case_briefs', []):
        chunks.append({'title': item.get('title',''), 'text': item.get('text',''), 'kind': '案例脉络', 'url': '', 'keywords': item.get('keywords', []), 'source': item.get('source','')})
    for item in CASE.get('references', []):
        txt = ' '.join([item.get('summary',''), item.get('citation','')] + list(item.get('key_points') or []))
        chunks.append({'title': item.get('title',''), 'text': txt, 'kind': item.get('kind','资料'), 'url': item.get('url',''), 'keywords': item.get('key_points', []), 'source': item.get('kind','')})
    for item in CASE.get('gallery', []):
        txt = ' '.join([item.get('caption',''), item.get('summary','')])
        chunks.append({'title': item.get('title',''), 'text': txt, 'kind': item.get('source_kind','图片'), 'url': item.get('source_url',''), 'keywords': [item.get('title','')], 'source': item.get('source_title','')})
    return chunks


KNOWLEDGE_CHUNKS = knowledge_chunks()


def retrieve_knowledge(question: str, topk=5):
    q = str(question or '').strip()
    q_terms = set(simple_terms(q))
    scored = []
    for doc in KNOWLEDGE_CHUNKS:
        hay = f"{doc.get('title','')} {doc.get('text','')} {' '.join(map(str, doc.get('keywords') or []))}".lower()
        score = 0
        if q and q in hay:
            score += 18
        for term in q_terms:
            if term and term in hay:
                score += 4 if len(term) >= 4 else 1
        for kw in doc.get('keywords') or []:
            if kw and str(kw) in q:
                score += 5
        if score > 0:
            scored.append((score, doc))
    if not scored:
        base = []
        for key in ('团队案例分析报告（内部材料）','AI原点社区入选北京首批AI创新街区','北京市城市更新条例','北京市城市更新行动计划（2021-2025年）'):
            for doc in KNOWLEDGE_CHUNKS:
                if doc['title'] == key:
                    base.append(doc)
        return base[:topk] or KNOWLEDGE_CHUNKS[:topk]
    scored.sort(key=lambda x: (-x[0], x[1].get('title','')))
    out = []
    seen = set()
    for _, doc in scored:
        key = doc.get('title','')
        if key in seen:
            continue
        out.append(doc)
        seen.add(key)
        if len(out) >= topk:
            break
    return out


def heuristic_case_answer(question: str, hits):
    q = str(question or '')
    answer = 'AI原点社区案例的重点，是把存量楼宇更新、人工智能产业集聚与公共生活场景治理放进同一个问题框架里理解。'
    bullets = []
    followups = CASE.get('qa_prompts', [])[:3]
    if any(x in q for x in ('城市更新','楼宇更新','不是产业园','招商')):
        answer = '它之所以是城市更新案例，是因为它依托的是既有楼宇和周边存量空间的功能重塑，而不是从零新建园区。案例同时涉及产业类更新、公共空间类更新和区域综合性更新，核心是“怎么把原来的空间重新组织起来”，而不只是“怎么招来更多企业”。'
        bullets = ['从载体看：核心空间是原东升大厦等既有楼宇更新后的再利用。','从任务看：不仅有企业入驻，还涉及公共空间、慢行体验、交流空间和社区服务。','从治理看：必须处理产权、运营、公众感受和政策协调，因此具有典型城市更新属性。']
    elif any(x in q for x in ('核心矛盾','主要矛盾','难点','冲突')):
        answer = '这个案例最核心的矛盾，是在高价值核心地段里，如何同时兼顾创新效率、资产收益、现场执行和公众可及性。换句话说，大家都支持创新，但各主体对“什么最重要”的排序并不一样。'
        bullets = ['集体经济组织会更关注收益边界、投入上限和长期资产可持续。','企业会更关注算力、人才、试错速度和创新资源密度。','公众会更关注安全、通行、开放时段、噪声与反馈机制。']
    elif any(x in q for x in ('公共价值','公众价值')):
        answer = '在这个案例里，公共价值不是一句抽象口号，而是看创新空间是否真正转化为更多人能感受到的共享收益。它既包括产业层面的创新生态，也包括生活层面的开放、通行、服务、交往和反馈。'
        bullets = ['如果只是企业多、融资多，但普通人几乎无法进入和感受，就很难说公共价值充分实现。','如果只追求全面开放，反过来削弱了创新资源的密度和效率，也会损害长期公共价值。','所以公共价值在本案例中更像“平衡后的共享成果”。']
    elif any(x in q for x in ('多主体','五个主体','共治','协同治理')):
        answer = '之所以要设置五个主体，是因为这个案例不是简单的政府—企业二元关系。政府、集体经济组织、运营方、创业企业、居民公众分别代表不同的利益函数和判断标准，只有把这些差异放进同一张桌子上，评委才能看到真实治理难题。'
        bullets = ['政府代表公共目标与制度协调。','集体经济组织代表资产、收益与风险边界。','运营方代表执行容量、动线和日常SOP。','创业企业代表创新资源效率。','居民公众代表安全、公平、开放和生活感受。']
    elif any(x in q for x in ('创新点','作品特色','作品创新')):
        answer = '你的作品最出彩的地方，是把案例分析从静态“讲故事”升级成动态“做推演”，同时又补了一个独立问答助手，方便评审在体验推演前后都能快速理解案例。'
        bullets = ['沙盘负责展示决策过程，包括随机事件、群聊质询、五方记忆与因果网络。','问答助手负责解释案例事实、政策依据和作品逻辑。','图文资料库降低评审理解门槛，让作品不只“能玩”，也“能讲清楚”。']
    else:
        bullets = [f"{doc.get('title','资料')}：{(doc.get('text','') or '')[:86]}…" for doc in hits[:3]]
    sources = [{'title': d.get('title',''), 'kind': d.get('kind','资料'), 'url': d.get('url','')} for d in hits[:5]]
    return {'answer': answer, 'bullets': bullets[:4], 'sources': sources, 'followups': followups, 'mode': 'mock'}


def case_qa_payload(body):
    question = str(body.get('question') or '').strip()
    if not question:
        return 400, {'error': '请输入问题'}
    hits = retrieve_knowledge(question, topk=6)
    context = []
    for d in hits:
        context.append({'title': d.get('title',''), 'kind': d.get('kind','资料'), 'url': d.get('url',''), 'content': d.get('text','')[:1200]})
    system_prompt = (
        '你是“原点·共治”中的独立案例问答助手。你的任务是帮助评审理解AI原点社区案例、政策背景和作品设计。'
        '必须优先依据提供的团队报告摘要、政策文件和公开资料回答，不得杜撰新事实。'
        '要明确区分：哪些是案例事实或公开资料，哪些是基于这些材料做出的分析解释。只输出严格JSON。'
    )
    user_prompt = f'''评审提问：{question}
可用知识：{json.dumps(context, ensure_ascii=False)}
请返回：{{"answer":"120-260字回答","bullets":["3-4条要点"],"sources":[{{"title":"来源标题","kind":"资料类型","url":"来源链接"}}],"followups":["3条可继续追问的问题"]}}'''
    try:
        result = aiping_chat(system_prompt, user_prompt, max_tokens=1800, temperature=0.25)
        result['mode'] = 'aiping'
        if not result.get('sources'):
            result['sources'] = [{'title': d.get('title',''), 'kind': d.get('kind','资料'), 'url': d.get('url','')} for d in hits[:4]]
        return 200, result
    except Exception as e:
        result = heuristic_case_answer(question, hits)
        result['fallback_reason'] = str(e)
        return 200, result


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sanitize_memory_record(raw):
    raw = raw if isinstance(raw, dict) else {}
    record = {
        "id": str(raw.get("id") or "").strip()[:120],
        "created_at": str(raw.get("created_at") or _utc_now_iso()).strip()[:64],
        "mode": "free" if str(raw.get("mode")) == "free" else "normal",
        "mode_name": str(raw.get("mode_name") or ("自由模式" if str(raw.get("mode")) == "free" else "普通模式"))[:40],
        "profile": str(raw.get("profile") or "治理诊断")[:120],
        "achievement": str(raw.get("achievement") or "治理探索者")[:80],
        "quote": str(raw.get("quote") or "完成了一次治理模拟。")[:500],
        "style": str(raw.get("style") or "实际")[:20],
        "total": max(0, min(100, int(raw.get("total") or 0))),
        "top_metric": str(raw.get("top_metric") or "")[:80],
        "top_value": max(0, min(100, int(raw.get("top_value") or 0))),
        "events": max(0, min(100, int(raw.get("events") or 0))),
        "seed": raw.get("seed") if raw.get("seed") is None else int(raw.get("seed") or 0),
    }
    if not record["id"]:
        record["id"] = f"memory-{int(datetime.now(timezone.utc).timestamp()*1000)}"
    return record


def default_memory_store():
    return {
        "schema": "origin-cogov-memory-wall",
        "schema_version": 1,
        "product_version": CASE.get("product", {}).get("version", "8.0"),
        "updated_at": _utc_now_iso(),
        "memories": [],
    }


def _load_memory_store_unlocked():
    if not MEMORY_WALL_PATH.exists():
        return default_memory_store()
    try:
        raw = json.loads(MEMORY_WALL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default_memory_store()
    if isinstance(raw, list):
        raw = {"schema": "origin-cogov-memory-wall", "schema_version": 1, "memories": raw}
    if not isinstance(raw, dict):
        return default_memory_store()
    items = raw.get("memories") or []
    if not isinstance(items, list):
        items = []
    store = default_memory_store()
    store.update({k: raw.get(k, store[k]) for k in ("schema", "schema_version", "product_version", "updated_at")})
    store["memories"] = [sanitize_memory_record(x) for x in items][:MEMORY_WALL_LIMIT]
    return store


def _write_memory_store_unlocked(store):
    MEMORY_WALL_PATH.parent.mkdir(parents=True, exist_ok=True)
    clean = default_memory_store()
    clean["memories"] = [sanitize_memory_record(x) for x in (store.get("memories") or [])][:MEMORY_WALL_LIMIT]
    clean["updated_at"] = _utc_now_iso()
    clean["product_version"] = CASE.get("product", {}).get("version", "8.0")
    tmp = MEMORY_WALL_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MEMORY_WALL_PATH)
    return clean


def load_memory_store():
    with MEMORY_WALL_LOCK:
        store = _load_memory_store_unlocked()
        if not MEMORY_WALL_PATH.exists():
            store = _write_memory_store_unlocked(store)
        return store


def add_memory_record(raw):
    with MEMORY_WALL_LOCK:
        store = _load_memory_store_unlocked()
        record = sanitize_memory_record(raw)
        items = [x for x in store.get("memories", []) if x.get("id") != record["id"]]
        items.insert(0, record)
        store["memories"] = items[:MEMORY_WALL_LIMIT]
        return _write_memory_store_unlocked(store)


def delete_memory_record(memory_id):
    with MEMORY_WALL_LOCK:
        store = _load_memory_store_unlocked()
        before = len(store.get("memories", []))
        store["memories"] = [x for x in store.get("memories", []) if x.get("id") != str(memory_id)]
        store = _write_memory_store_unlocked(store)
        return store, before - len(store["memories"])


def clear_memory_store():
    with MEMORY_WALL_LOCK:
        return _write_memory_store_unlocked(default_memory_store())


def import_memory_store(raw_data, mode="merge"):
    if isinstance(raw_data, list):
        incoming = raw_data
    elif isinstance(raw_data, dict):
        incoming = raw_data.get("memories") or []
    else:
        raise ValueError("导入JSON必须是回忆数组，或包含 memories 数组的对象。")
    if not isinstance(incoming, list):
        raise ValueError("导入JSON中的 memories 必须是数组。")
    incoming = [sanitize_memory_record(x) for x in incoming][:MEMORY_WALL_LIMIT]
    with MEMORY_WALL_LOCK:
        store = _load_memory_store_unlocked()
        if mode == "replace":
            merged = incoming
        else:
            by_id = {x.get("id"): x for x in store.get("memories", [])}
            for item in incoming:
                by_id[item.get("id")] = item
            merged = list(by_id.values())
            merged.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        store["memories"] = merged[:MEMORY_WALL_LIMIT]
        return _write_memory_store_unlocked(store)


# Ensure an editable local JSON file exists beside the project data.
load_memory_store()

class Handler(BaseHTTPRequestHandler):
    server_version = "OriginCoGov/8.0"
    protocol_version = "HTTP/1.1"

    def send_bytes(self, content, content_type="application/octet-stream", status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, obj, status=200):
        self.send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def send_json_download(self, obj, filename="origin-cogov-memory-wall.json"):
        content = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def send_sse_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

    def sse_write(self, obj):
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(("data: " + data + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self.send_bytes((BASE_DIR / "templates" / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/health":
            return self.send_json({"ok": True, "ai_enabled": bool(AIPING_API_KEY) and not MOCK_MODE, "model": AIPING_MODEL, "mode": "aiping" if bool(AIPING_API_KEY) and not MOCK_MODE else "mock", "version": CASE["product"]["version"], "streaming": True, "gallery_count": len(CASE.get("gallery", [])), "knowledge_docs": len(CASE.get("references", [])) + len(CASE.get("case_briefs", [])) + len(CASE.get("report_sections", []))})
        if path == "/api/case":
            return self.send_json(CASE)
        if path == "/api/memories":
            store = load_memory_store()
            return self.send_json({**store, "storage_file": "data/memory_wall.json", "count": len(store.get("memories", []))})
        if path == "/api/memories/export":
            return self.send_json_download(load_memory_store())
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            target = (BASE_DIR / "static" / rel).resolve()
            if not str(target).startswith(str((BASE_DIR / "static").resolve())) or not target.exists() or not target.is_file():
                return self.send_json({"error": "文件不存在"}, 404)
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in {"application/javascript", "application/json"}:
                ctype += "; charset=utf-8"
            return self.send_bytes(target.read_bytes(), ctype)
        return self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            return self.send_json({"error": "JSON格式错误"}, 400)
        path = urlparse(self.path).path
        if path == "/api/deliberate/stream":
            self.send_sse_headers()
            try:
                for item in deliberation_stream(body):
                    self.sse_write(item)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if path == "/api/challenge/stream":
            self.send_sse_headers()
            try:
                for item in challenge_stream(body):
                    self.sse_write(item)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if path == "/api/memories/add":
            try:
                store = add_memory_record(body.get("memory") or body)
                return self.send_json({"ok": True, "count": len(store.get("memories", [])), "memories": store.get("memories", [])})
            except Exception as e:
                return self.send_json({"error": f"写入回忆JSON失败：{e}"}, 400)
        if path == "/api/memories/delete":
            store, removed = delete_memory_record(body.get("id"))
            return self.send_json({"ok": True, "removed": removed, "count": len(store.get("memories", [])), "memories": store.get("memories", [])})
        if path == "/api/memories/clear":
            store = clear_memory_store()
            return self.send_json({"ok": True, "count": 0, "memories": store.get("memories", [])})
        if path == "/api/memories/import":
            try:
                mode = "replace" if str(body.get("mode")) == "replace" else "merge"
                store = import_memory_store(body.get("data"), mode=mode)
                return self.send_json({"ok": True, "mode": mode, "count": len(store.get("memories", [])), "memories": store.get("memories", [])})
            except Exception as e:
                return self.send_json({"error": f"导入回忆JSON失败：{e}"}, 400)
        if path == "/api/report":
            status, result = report_payload(body)
            return self.send_json(result, status)
        if path == "/api/caseqa":
            status, result = case_qa_payload(body)
            return self.send_json(result, status)
        return self.send_json({"error": "Not found"}, 404)

    def log_message(self, format, *args):
        print(f"[WEB] {self.address_string()} - {format % args}")


if __name__ == "__main__":
    port = 7860
    print("\n原点·共治 V8.0 已启动")
    print(f"打开浏览器访问：http://127.0.0.1:{port}")
    print("AI 模式：" + (f"AI Ping / {AIPING_MODEL} / 流式" if AIPING_API_KEY and not MOCK_MODE else "本地演示模式（在 .env 填 Key 后启用 AI Ping 流式生成）"))
    print("按 Ctrl+C 可关闭。\n")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
