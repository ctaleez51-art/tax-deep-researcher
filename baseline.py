"""단일 에이전트 대조군 — 나누지 않는다. 에이전트 하나가 순서대로 고르고 읽고 전부 쓴다.

공정하게 맞춘 것: 같은 코퍼스 · 같은 모델(config.json) · 같은 도구(읽기 요약 프롬프트, links 후보) ·
같은 읽기 예산(절수 × 절예산). 다른 것은 '나누느냐' 하나.
예산을 끝까지 쓰는지 확인한다: 모델이 후보에 없는 제목을 답하면 후보 첫 번째를 읽는다(교안의 고름 = cand[0]).
읽기 예산을 다 쓰지 못하면 metrics 의 '예산미소진' 경보가 0 이 아니게 된다.
    python baseline.py --q Q3
"""
from __future__ import annotations
import argparse, json, re, time
from datetime import datetime

import graph as G
import metrics as M


def single_agent(question: str, fake: bool = False, label: str = "단일", qid: str = "", save: bool = True) -> dict:
    cfg = G.BASE_CFG
    budget = cfg["절수"] * cfg["절예산"]
    meter = G.Meter(G.FakeModel() if fake else G.OpenAIModel(cfg["모델"]))
    t0 = time.time()
    read, notes = [], []
    # 시작 문서도 스스로 고른다: 처음 후보는 팀 코디네이터와 같은 법률 조문 카드 목록
    law = [k for k, v in G.KINDS.items() if v == "소득세법"]
    while len(read) < budget:
        frontier = sorted({x for v in read for x in G.LINKS.get(v, []) if x in G.DOCS} - set(read))
        cand = frontier or [x for x in (law if not read else G.DOCS) if x not in read]
        raw = meter.ask("너는 종합소득세 자료 조사 오케스트레이터다. 질문에 답하려면 다음에 어느 문서를 읽어야 하는지 "
                        "후보 중에서 정확히 하나 고른다.\n"
                        'JSON으로만: {"문서":"후보에 있는 문서명"}',
                        f"[질문] {question}\n[지금까지 모은 요약]\n" +
                        "\n".join(f"- {t}: {n[:120]}" for t, n in notes) +
                        "\n[후보] " + ", ".join(c + "(" + G.TITLES.get(c, "") + ")" for c in cand[:60]), coord=True)
        pick = str(G.jload(raw, {}).get("문서") or "").split("(")[0].strip()
        if pick not in G.DOCS or pick in read:
            pick = cand[0]                       # 엉뚱한 답이면 후보 첫 번째 — 예산은 끝까지 쓴다
        notes.append([pick, meter.ask("너는 세법 자료 조사 담당이다. 아래 문서를 읽고 지시에 관련된 내용만 여섯 문장 이내로 "
                                      "간추려라. 조문의 요건·금액·기한은 원문 표현대로 옮겨라. 관련 없으면 '관련 없음'이라고만 답하라.\n"
                                      f"[지시] {question}",
                                      f"[문서: {pick} · {G.TITLES.get(pick, '')}]\n{G.DOCS[pick]}")])
        read.append(pick)
    report = meter.ask("너는 조사 결과를 정리하는 필자다. 아래 요약들만 근거로 질문에 답하는 보고서를 써라. "
                       f"제목(#)을 달고 절(##)을 {cfg['절수']}개 두고 각 절은 여섯 문장 이상 쓴다. "
                       "문장마다 근거가 된 문서명을 «소득세법 제70조» 처럼 겹화살괄호 « » 로 감싸 붙여라. "
                       "문서명은 요약 머리의 [ ] 안에서 ' · ' 앞부분만 쓰고, 대괄호 [ ] 는 쓰지 마라. 요약에 없는 내용은 쓰지 마라.",
                       f"[질문] {question}\n[모은 요약]\n" +
                       "\n\n".join(f"[{t} · {G.TITLES.get(t, '')}]\n{n}" for t, n in notes), coord=True)
    # 팀 쪽(graph.py)과 같은 처리: [문서명] 으로 붙인 인용을 «문서명» 으로 바꾼다.
    report = re.sub(r"\[([^\[\]]+)\]", lambda m: f"«{m.group(1).split('·')[0].strip()}»"
                    if m.group(1).split("·")[0].strip() in G.DOCS else m.group(0), report)
    cites = [c.strip() for c in re.findall(r"«([^»]+)»", report)]
    sec = {"번호": 0, "절": "(단일 에이전트 전체)", "역할": "단일", "시작문서": read[0] if read else "", "바퀴": 1,
           "지시": question, "본문": report, "읽은문서": read, "새로읽음": read, "메모": notes, "인용": cites,
           "허위인용": sorted({c for c in cites if c not in read}), "충분": True, "부족": ""}
    m = M.evaluate(report, [["단일", d] for d in read], [sec], [sec], G.DOCS,
                   cost={"calls": meter.calls, "coord": meter.coord_chars, "sub": meter.sub_chars}, budget=budget)
    rec = {"시각": datetime.now().isoformat(timespec="seconds"), "라벨": label, "qid": qid, "질문": question,
           "설정": {k: v for k, v in cfg.items() if k != "역할명단"}, "가짜모델": fake, "걸린초": round(time.time() - t0, 1),
           "plan": {"제목": "단일 에이전트", "목차": []}, "sections": [sec], "최종절": [sec], "report": report,
           "metrics": m, "log": [f"단일 에이전트 {len(read)}건 읽음 (예산 {budget})", M.line(m)]}
    if save and not fake:
        G.save_record(rec)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", nargs="*", default=["Q3"])
    ap.add_argument("--fake", action="store_true")
    a = ap.parse_args()
    for qid in a.q:
        rec = single_agent(G.QUESTIONS[qid]["질문"], fake=a.fake, qid=qid)
        print(f"■ {qid}", *rec["log"], sep="\n")


if __name__ == "__main__":
    main()
