"""종합소득세 딥리서처 — 기획 → 배치 → 서브에이전트 → 점검 → 종합 → 평가.

교안(딥리서치 에이전트 만들기 1·2일차)의 노드 여섯 개를 바탕으로, 코퍼스를 소득세법 관련 법령으로 바꿨다.
교안과 달라진 곳은 ★ 표시.
    python graph.py --q Q3                 # 질문 하나 (실제 호출, .env 의 OPENAI_API_KEY)
    python graph.py --all                  # questions.json 전부
    python graph.py --q Q3 --fake          # 모델 없이 흐름만 확인 (비용 0)
"""
from __future__ import annotations
import argparse, json, operator, os, random, re, threading, time
from datetime import datetime
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

import metrics as M

HERE = Path(__file__).resolve().parent
CORPUS = json.loads((HERE / "data" / "corpus.json").read_text(encoding="utf-8"))
DOCS, LINKS, TITLES, KINDS = CORPUS["docs"], CORPUS["links"], CORPUS["titles"], CORPUS["kinds"]
QUESTIONS = {q["id"]: q for q in json.loads((HERE / "data" / "questions.json").read_text(encoding="utf-8"))}
BASE_CFG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
OUT = HERE / "output"


def load_key() -> str:
    for env in (HERE / ".env", HERE.parent / "deepresearch-agent" / ".env"):
        if env.exists():
            for line in env.read_text(encoding="utf-8-sig").splitlines():
                k, sep, v = line.strip().partition("=")
                if sep and k.strip() == "OPENAI_API_KEY":
                    return v.strip().strip('"').strip("'")
    return os.getenv("OPENAI_API_KEY", "")


# ── 모델 ────────────────────────────────────────────────────────────────
class OpenAIModel:
    def __init__(self, name: str):
        from langchain_openai import ChatOpenAI
        key = load_key()
        if not key.startswith("sk-"):
            raise SystemExit("OPENAI_API_KEY 를 찾지 못했습니다 (.env).")
        self.llm = ChatOpenAI(model=name, temperature=0, timeout=60, max_retries=0, api_key=key)

    def __call__(self, system: str, user: str) -> str:
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for i in range(3):
            try:
                return self.llm.invoke(msgs).content
            except Exception as e:
                if i == 2 or not any(k in type(e).__name__ for k in ("RateLimit", "APIConnection", "Timeout", "InternalServer")):
                    raise
                time.sleep(2 ** i)


class FakeModel:
    """비용 없이 그래프 흐름만 확인하는 가짜 모델. 결과물의 품질과는 상관없다."""
    def __call__(self, system: str, user: str) -> str:
        rnd = random.Random(len(user))
        if "코디네이터" in system:
            seeds = re.findall(r"^- (소득세법 제[^ ]+조(?:의\d+)?)", user, re.M)
            roles = list(BASE_CFG["역할명단"])
            return json.dumps({"제목": "가짜 보고서", "목차": [
                {"절": f"절{i}", "지시": "가짜 지시", "역할": roles[i % len(roles)], "시작문서": seeds[i * 7 % len(seeds)]}
                for i in range(4)]}, ensure_ascii=False)
        if "다음에 읽을 문서" in system:
            cand = re.search(r"\[후보\] (.*)", user).group(1).split(", ")
            return json.dumps({"문서": cand[0]}, ensure_ascii=False)
        if "간추려라" in system:
            return "가짜 요약."
        if "한 절을 써라" in system:
            docs = re.findall(r"^\[([^\]·]+?) ·", user, re.M)
            body = " ".join(f"가짜 문장 {i}입니다. «{d}»" for i, d in enumerate(docs))
            return json.dumps({"본문": body, "충분": rnd.random() > 0.3, "부족": "가짜 부족"}, ensure_ascii=False)
        if "편집자" in system:
            return json.dumps({"머리말": "가짜 머리말.", "맺음말": "가짜 맺음말."}, ensure_ascii=False)
        if "보고서를" in system:
            docs = re.findall(r"^\[([^\]·]+?) ·", user, re.M)
            return " ".join(f"가짜 문장입니다. «{d}»" for d in docs)
        return "{}"


# ── 계량기: 코디네이터와 서브에이전트가 본 글자를 따로 센다 (격리 증명) ──────
class Meter:
    def __init__(self, model):
        self.model, self.lock = model, threading.Lock()
        self.calls = self.coord_chars = self.sub_chars = 0

    def ask(self, system: str, user: str, coord: bool = False, cap: int = 60000) -> str:
        body = user[:cap]
        with self.lock:
            self.calls += 1
            if coord:
                self.coord_chars += len(body)
            else:
                self.sub_chars += len(body)
        return self.model(system, body)


def jload(raw: str, default):
    try:
        return json.loads(re.search(r"\[.*\]" if isinstance(default, list) else r"\{.*\}", raw, re.S).group(0))
    except Exception:
        return default


class Research(TypedDict):
    question: str
    plan: dict
    sections: Annotated[list, operator.add]
    visited: Annotated[list, operator.add]
    report: str
    metrics: dict
    log: Annotated[list, operator.add]
    task: dict
    prior: dict


def 절모음(sections: list, rule: str) -> dict:
    """★ 재위임으로 같은 절이 두 번 올라오면 무엇을 남길지.
    교안은 '마지막 것'(덮어쓰기)이었다. 소거 실험에서 두 번째 원고가 인용을 빠뜨리고 첫 원고를 지운 일이 있어서,
    기본값을 '실제로 읽은 문서를 인용한 수가 많은 쪽'으로 바꿨다. 같으면 나중 것(더 많이 읽은 쪽)."""
    out = {}
    for sec in sections:
        old = out.get(sec["절"])
        if old is None or rule == "마지막":
            out[sec["절"]] = sec
            continue
        score = lambda s: len(set(s["인용"]) - set(s["허위인용"]))
        if score(sec) >= score(old):
            out[sec["절"]] = sec
    return out


def make_graph(cfg: dict, meter: Meter):
    ROSTER = cfg["역할명단"]
    DEFAULT_ROLE = next(iter(ROSTER))
    LAW = [k for k, v in KINDS.items() if v == "소득세법"]

    def cards() -> str:
        """★ 코디네이터는 법률 조문 카드(제목 + 앞 N자)만 본다. 1,657건 전체 카드는 60,000자를 넘는다.
        시행령·통칙·집행기준은 리서처가 법률 조문의 links 를 따라가서 닿는다."""
        n = cfg["카드글자"]
        return "\n".join(f"- {k}({TITLES[k]}): " + re.sub(r"\s+", " ", DOCS[k][:n]) for k in LAW)

    def plan(s):
        roster = "\n".join(f"- {k}: {v}" for k, v in ROSTER.items())
        raw = meter.ask(
            "너는 종합소득세 리서치 팀의 코디네이터다. 아래 질문에 답하는 보고서의 목차를 짜고, 각 절을 리서처 "
            f"에이전트 하나에게 맡긴다. 절은 최대 {cfg['절수']}개.\n"
            "목차는 반드시 **내용 단위**로 나눠라 — 소득 종류·제도·요건·절차처럼 질문이 실제로 묻는 덩어리로 쪼갠다. "
            "'개요·요건·절차·결론' 같은 형식 단위로 나누지 마라. 그렇게 나누면 리서처들이 같은 조문을 여러 번 읽는다.\n"
            "절마다 그 절을 쓰는 데 가장 먼저 읽어야 할 조문을 카드 목록에서 하나 배정하라. 절끼리 다른 조문을 주어라. "
            "카드에 적힌 문서명(예: 소득세법 제70조)을 그대로 써라.\n"
            f"절의 성격에 맞는 리서처 에이전트를 명단에서 골라 붙여라.\n[리서처 에이전트 명단]\n{roster}\n"
            'JSON으로만: {"제목":"보고서 제목","목차":[{"절":"절 제목","지시":"이 절에서 밝혀야 할 것 한두 문장",'
            '"역할":"명단에 있는 이름 그대로","시작문서":"카드에 있는 문서명"}]}',
            f"[질문] {s['question']}\n[법률 조문 카드]\n{cards()}", coord=True)
        obj = jload(raw, {})
        toc, taken, fixes = [], set(), []
        for item in obj.get("목차", [])[:cfg["절수"]]:
            role, seed = item.get("역할", ""), str(item.get("시작문서") or "").strip()
            # ★ 카드가 "문서명(제목)" 꼴이라 모델이 제목까지 붙여 답한다. 괄호 제목을 떼고 비교한다.
            if seed not in DOCS:
                seed = re.sub(r"\s*\([^()]*\)\s*$", "", seed).strip()
            # ★ 배정이 실제 자료를 가리키는지 코드가 검사한다. 모델은 그럴듯한 제목을 지어낸다.
            if cfg["배정"] and (seed not in DOCS or seed in taken):
                fixes.append(seed)
                seed = next((t for t in LAW if t not in taken), "")
            if not cfg["배정"]:
                seed = ""
            if seed:
                taken.add(seed)
            toc.append({"절": item.get("절", "무제"), "지시": item.get("지시", s["question"]),
                        "역할": role if (cfg["역할"] and role in ROSTER) else DEFAULT_ROLE,
                        "시작문서": seed, "예산": cfg["절예산"]})
        if not toc:
            toc = [{"절": "개요", "지시": s["question"], "역할": DEFAULT_ROLE, "시작문서": "", "예산": cfg["절예산"]}]
        p = {"제목": obj.get("제목", s["question"]), "목차": toc, "배치": list(range(len(toc))), "바퀴": 1,
             "배정수정": fixes}
        seeds = " · ".join(f"{t['역할']}→«{t['시작문서'] or '자율'}»" for t in toc)
        return {"plan": p, "log": [f"① 기획   목차 {len(toc)}절 · {seeds}" + (f" · 배정 고침 {fixes}" if fixes else "")]}

    def dispatch(s):
        return {"log": [f"② 배치   {s['plan'].get('바퀴', 1)}바퀴 · 서브에이전트 {len(s['plan'].get('배치', []))}개 동시 파견"]}

    def fanout(s):
        idxs = s["plan"].get("배치", [])
        if not idxs:
            return "review"
        done = 절모음(s["sections"], cfg["재위임원고"])
        toc = s["plan"]["목차"]
        sends = []
        for i in idxs:
            mine = toc[i]["절"]
            others = {t["시작문서"] for j, t in enumerate(toc) if j != i and t["시작문서"]}
            others |= {d for name, sec in done.items() if name != mine for d in sec["읽은문서"]}
            sends.append(Send("researcher", {"question": s["question"],
                                             "task": {**toc[i], "번호": i, "바퀴": s["plan"].get("바퀴", 1),
                                                      "피하기": sorted(others) if cfg["구역"] else []},
                                             "prior": done.get(mine, {})}))
        return sends

    def role_line(t):
        return f"너는 {t['역할']}이다. {ROSTER.get(t['역할'], ROSTER[DEFAULT_ROLE])}."

    def read_one(title, brief):
        return meter.ask("너는 세법 자료 조사 담당이다. 아래 문서를 읽고 지시에 관련된 내용만 여섯 문장 이내로 간추려라. "
                         "조문의 요건·금액·기한은 원문 표현대로 옮겨라. 관련 없으면 '관련 없음'이라고만 답하라.\n"
                         f"[지시] {brief}", f"[문서: {title} · {TITLES.get(title, '')}]\n{DOCS[title]}")

    def candidates(read, avoid):
        used = set(read) | set(avoid)
        frontier = sorted({x for v in read for x in LINKS.get(v, []) if x in DOCS} - used)
        return frontier or [x for x in DOCS if x not in used] or [x for x in DOCS if x not in read]

    def cand_text(cand):
        return ", ".join(c + "(" + TITLES.get(c, "") + ")" for c in cand)

    def next_doc(t, read, avoid):
        cand = candidates(read, avoid)
        raw = meter.ask(f"{role_line(t)} 맡은 절을 쓰기 위해 다음에 읽을 문서를 후보 중에서 정확히 하나 고른다. "
                        "더 읽을 것이 없으면 그만둔다.\n"
                        'JSON으로만: {"문서":"후보에 있는 문서명"} 또는 {"문서":null}',
                        f"[맡은 절] {t['절']}\n[지시] {t['지시']}\n[이미 읽음] {', '.join(read) or '없음'}\n"
                        f"[후보] {cand_text(cand[:60])}")
        pick = str(jload(raw, {}).get("문서") or "").split("(")[0].strip()
        return pick if pick in DOCS and pick not in read else None

    def researcher(s):
        t, prior = s["task"], s.get("prior") or {}
        read, notes = list(prior.get("읽은문서", [])), list(prior.get("메모", []))
        new_reads = []
        nxt = t.get("시작문서") if t.get("시작문서") in DOCS else None
        for _ in range(t["예산"]):
            if nxt is None or nxt in read:
                nxt = next_doc(t, read, t.get("피하기") or [])
            if nxt is None:
                break
            notes.append([nxt, read_one(nxt, t["지시"])])
            read.append(nxt)
            new_reads.append(nxt)
            nxt = None
        자료 = "\n\n".join(f"[{d} · {TITLES.get(d, '')}]\n{n}" for d, n in notes) or "(읽은 자료 없음)"
        raw = meter.ask(f"{role_line(t)} 아래 자료만 근거로 보고서의 한 절을 써라. 여섯 문장 이상 열두 문장 이하, 600자 이상. "
                        "문장마다 근거가 된 문서명을 «소득세법 제70조» 처럼 겹화살괄호 « » 로 감싸 붙여라. "
                        "문서명은 자료 머리의 [ ] 안에서 ' · ' 앞부분만 쓰고, 대괄호 [ ] 는 쓰지 마라. "
                        "자료에 없는 내용은 쓰지 마라. 지시를 자료로 다 채우지 못했으면 충분을 false 로 하고 "
                        "어떤 근거가 비었는지 적어라(문장 수 같은 형식 문제는 적지 마라).\n"
                        'JSON으로만: {"본문":"...","충분":true,"부족":""}',
                        f"[맡은 절] {t['절']}\n[지시] {t['지시']}\n[모은 자료]\n{자료}")
        obj = jload(raw, {})
        text = str(obj.get("본문") or "")
        if len(text) < 100:
            text = re.sub(r'^\s*\{.*?"본문"\s*:\s*"?', "", raw, flags=re.S)[:2000].strip()
        # ★ 자료 머리가 [문서명 · 제목] 꼴이라 모델이 «» 대신 [문서명] 으로 붙이는 일이 많다. 코퍼스에 있는 이름이면 «» 로 바꾼다.
        def _fix(m):
            name = m.group(1).split("·")[0].strip()
            return f"«{name}»" if name in DOCS else m.group(0)
        text = re.sub(r"\[([^\[\]]+)\]", _fix, text)
        cites = [c.strip() for c in re.findall(r"«([^»]+)»", text)]
        sec = {"번호": t["번호"], "절": t["절"], "역할": t["역할"], "시작문서": t.get("시작문서", ""), "바퀴": t["바퀴"],
               "지시": t["지시"], "본문": text, "읽은문서": read, "새로읽음": new_reads, "메모": notes, "인용": cites,
               "허위인용": sorted({c for c in cites if c not in read}),
               "충분": bool(obj.get("충분")), "부족": str(obj.get("부족") or "")}
        mark = "충분" if sec["충분"] else f"부족({sec['부족'][:20]})"
        return {"sections": [sec], "visited": [[t["절"], d] for d in new_reads],
                "log": [f"   ③ {t['역할']} «{t['절'][:16]}» {t['바퀴']}바퀴 {len(new_reads)}건 새로 읽고 "
                        f"{len(text)}자 · 인용 {len(cites)}곳 · {mark}"]}

    def review(s):
        latest = 절모음(s["sections"], "마지막")
        toc, wheel = s["plan"]["목차"], s["plan"].get("바퀴", 1)
        gaps = [] if (not cfg["재위임"] or wheel >= cfg["최대바퀴"]) else \
            [i for i, t in enumerate(toc) if not latest.get(t["절"], {}).get("충분", False)]
        if not gaps:
            why = "루프 없음" if not cfg["재위임"] else ("바퀴 소진" if wheel >= cfg["최대바퀴"] else "빈 칸 없음")
            return {"plan": {**s["plan"], "배치": [], "종료": why},
                    "log": [f"④ 점검   {len(toc)}절 중 재위임 0개 — 종합으로 ({why})"]}
        newtoc = [dict(t) for t in toc]
        for i in gaps:
            prev = latest.get(toc[i]["절"], {})
            newtoc[i]["지시"] = (f"{toc[i]['지시']} (재위임: 지난번에 «{'», «'.join(prev.get('읽은문서', [])) or '없음'}» 를 "
                                 f"읽었지만 {prev.get('부족') or '근거가 모자랐다'}. 그 빈 칸을 채우도록 아직 안 본 문서를 찾아라)")
        return {"plan": {**s["plan"], "목차": newtoc, "배치": gaps, "바퀴": wheel + 1},
                "log": [f"④ 점검   {len(toc)}절 중 {len(gaps)}개 재위임 — «{'», «'.join(toc[i]['절'][:14] for i in gaps)}»"]}

    def route(s):
        return "more" if s["plan"].get("배치") else "done"

    def synthesize(s):
        order = sorted(절모음(s["sections"], cfg["재위임원고"]).values(), key=lambda x: x["번호"])
        outline = "\n".join(f"{i+1}. {x['절']}: {x['본문'][:90]}…" for i, x in enumerate(order))
        obj = jload(meter.ask("너는 보고서를 마무리하는 편집자다. 아래는 리서처 에이전트들이 각자 쓴 절의 제목과 첫머리다. "
                              "본문은 고치지 말고, 전체를 여는 머리말과 닫는 맺음말만 써라. 각각 세 문장 이내.\n"
                              'JSON으로만: {"머리말":"...","맺음말":"..."}',
                              f"[질문] {s['question']}\n[보고서 제목] {s['plan']['제목']}\n[절 목록]\n{outline}", coord=True), {})
        parts = [f"# {s['plan']['제목']}", str(obj.get("머리말") or "")]
        parts += [f"## {i+1}. {x['절']}  _({x['역할']})_\n\n{x['본문']}" for i, x in enumerate(order)]
        parts.append(f"## 맺음말\n\n{obj.get('맺음말') or ''}")
        report = "\n\n".join(p for p in parts if p.strip())
        return {"report": report, "log": [f"⑤ 종합   {len(order)}절 이어붙임 → 보고서 {len(report):,}자 (편집자는 절 첫머리만 봤다)"]}

    def evaluate(s):
        final = list(절모음(s["sections"], cfg["재위임원고"]).values())
        m = M.evaluate(s["report"], s["visited"], final, s["sections"], DOCS,
                       cost={"calls": meter.calls, "coord": meter.coord_chars, "sub": meter.sub_chars},
                       budget=cfg["절수"] * cfg["절예산"])
        return {"metrics": m, "log": [M.line(m)]}

    g = StateGraph(Research)
    for name, fn in [("plan", plan), ("dispatch", dispatch), ("researcher", researcher), ("review", review),
                     ("synthesize", synthesize), ("evaluate", evaluate)]:
        g.add_node(name, fn)
    g.add_edge(START, "plan")
    g.add_edge("plan", "dispatch")
    g.add_conditional_edges("dispatch", fanout, ["researcher", "review"])
    g.add_edge("researcher", "review")
    g.add_conditional_edges("review", route, {"more": "dispatch", "done": "synthesize"})
    g.add_edge("synthesize", "evaluate")
    g.add_edge("evaluate", END)
    return g.compile()


def run(question: str, overrides: dict | None = None, fake: bool = False, label: str = "기본",
        qid: str = "", save: bool = True) -> dict:
    cfg = {**BASE_CFG, **(overrides or {})}
    meter = Meter(FakeModel() if fake else OpenAIModel(cfg["모델"]))
    t0 = time.time()
    st = make_graph(cfg, meter).invoke({"question": question, "plan": {}, "sections": [], "visited": [], "report": "",
                                        "metrics": {}, "log": [], "task": {}, "prior": {}})
    rec = {"시각": datetime.now().isoformat(timespec="seconds"), "라벨": label, "qid": qid, "질문": question,
           "설정": {k: v for k, v in cfg.items() if k != "역할명단"}, "가짜모델": fake, "걸린초": round(time.time() - t0, 1),
           "plan": st["plan"], "sections": st["sections"],
           "최종절": sorted(절모음(st["sections"], cfg["재위임원고"]).values(), key=lambda x: x["번호"]),
           "report": st["report"], "metrics": st["metrics"], "log": st["log"]}
    if save and not fake:                 # 가짜 모델 실행은 기록하지 않는다
        save_record(rec)
    return rec


def save_record(rec: dict) -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / "reports").mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    stamp = rec["시각"].replace(":", "").replace("-", "")
    name = f"{rec['qid'] or 'Q'}_{rec['라벨']}_{stamp}{'_fake' if rec['가짜모델'] else ''}.md".replace(" ", "").replace("·", "-")
    (OUT / "reports" / name).write_text(rec["report"], encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", nargs="*", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--question")
    ap.add_argument("--fake", action="store_true")
    a = ap.parse_args()
    jobs = [(q, QUESTIONS[q]["질문"]) for q in (QUESTIONS if a.all else a.q)]
    if a.question:
        jobs.append(("", a.question))
    for qid, q in jobs or [("Q3", QUESTIONS["Q3"]["질문"])]:
        rec = run(q, fake=a.fake, qid=qid)
        print(f"\n■ {qid} {q}")
        print("\n".join(rec["log"]))


if __name__ == "__main__":
    main()
