"""소거 실험 — 장치를 하나씩 끄고 같은 지표로 잰다. 늘 기준에서 출발해 하나만 끈다.

같은 설정도 시행마다 십몇 %p씩 움직이므로 --repeat 로 여러 번 돌리고 평균과 각 값을 함께 남긴다.
스위치를 껐다고 로그만 바뀌고 코드 경로가 그대로인 사고를 막기 위해, 실행마다 '꺼진 것이 실제로 꺼졌는지'를
기록에서 다시 확인한다(확인 칸). 결과: output/ablation.json
    python ablation.py --q Q3 Q6 --repeat 2            # 기준·장치 끔 3가지·단일 대조군
"""
from __future__ import annotations
import argparse, json, statistics

import graph as G
import baseline as B
import metrics as M

SWITCHES = [("기준", {}), ("배정·구역 끔", {"배정": False, "구역": False}),
            ("역할 끔", {"역할": False}), ("재위임 끔", {"재위임": False})]
KEYS = ["근거율", "인용편중", "중복률", "해석자료비율", "읽은문서", "보고서자수", "LLM호출", "격리율"]


def check(name: str, rec: dict) -> str:
    """스위치가 실제 코드 경로에서 꺼졌는지 기록으로 확인한다."""
    toc = rec["plan"].get("목차", [])
    secs = rec["sections"]
    if name == "배정·구역 끔":
        ok = all(not t["시작문서"] for t in toc)
    elif name == "역할 끔":
        ok = len({t["역할"] for t in toc}) <= 1
    elif name == "재위임 끔":
        ok = all(s["바퀴"] == 1 for s in secs)
    else:
        ok = True
    return "확인됨" if ok else "⚠ 꺼지지 않음"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", nargs="*", default=["Q3", "Q6"])
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--out", default="ablation.json")   # 질문별로 동시에 돌릴 때 파일이 겹치지 않게
    a = ap.parse_args()
    result = {}
    for qid in a.q:
        q = G.QUESTIONS[qid]["질문"]
        result[qid] = {"질문": q, "설정": {}}
        for name, off in SWITCHES + [("단일 대조군", None)]:
            runs = []
            for r in range(a.repeat):
                rec = B.single_agent(q, fake=a.fake, qid=qid) if off is None else \
                    G.run(q, off, fake=a.fake, label=name, qid=qid)
                m = rec["metrics"]
                runs.append({**{k: m[k] for k in KEYS}, "경보": M.alarms(m),
                             "확인": "대조군" if off is None else check(name, rec), "시각": rec["시각"]})
                print(f"{qid} {name:<10} #{r+1} 근거율 {m['근거율']:>5}% · 편중 {m['인용편중']:>5}% · 중복 {m['중복률']:>5}% · "
                      f"읽은 {m['읽은문서']:>2} · {m['보고서자수']:>5,}자 · LLM {m['LLM호출']} · 경보 {M.alarms(m) or '없음'} · {runs[-1]['확인']}")
            mean = {k: round(statistics.mean(x[k] for x in runs), 1) for k in KEYS}
            spread = {k: round(max(x[k] for x in runs) - min(x[k] for x in runs), 1) for k in KEYS}
            result[qid]["설정"][name] = {"평균": mean, "최대-최소": spread, "실행": runs}
    if not a.fake:
        (G.OUT / a.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"저장: output/{a.out}")


if __name__ == "__main__":
    main()
