"""지표 — 정답표도 판정 모델도 쓰지 않는다. 만들어진 글과 실제로 읽은 자료만 본다.

지표마다 어느 장치를 보는 신호인지 정해 두었다.
  [신호: 높낮이를 견준다]
    근거율      : «문서명» 이 붙은 문장 / 전체 문장          → 절예산(깊이)·재위임
    인용편중    : 가장 많이 인용된 문서의 인용 / 전체 인용    → 배정·구역
    중복률      : 1 − 서로 다른 읽은 문서 / 전체 읽기 횟수     → 배정·구역(피하기 목록)
    읽고안쓴    : 읽었지만 보고서에 한 번도 인용 안 된 문서 수 → 배정 품질(카드)
    해석자료비율: 인용 중 기본통칙·집행기준 비율               → 역할(해석 담당)·링크 탐색
    격리율      : 코디네이터가 본 글자 / 팀 전체가 본 글자     → 컨텍스트 격리
  [경보: 0 이어야 한다]
    허위인용    : 아무도 읽지 않은 문서를 인용
    인용0절     : 인용이 하나도 없는 절 (자료 없이 모델 상식으로 썼다는 신호)
    예산미소진  : 읽기 예산을 다 쓰지 않은 횟수 (대조군 공정성 확인용)
★ 교안의 문장 나누기는 «» 가 마침표 뒤에 붙으면 인용이 다음 문장으로 넘어가 근거율이 낮게 나왔다.
  여기서는 제목 줄(#)을 빼고, 마침표 뒤 «…» 를 앞 문장에 붙여 센다. 비교는 이 규칙으로만 한다.
"""
from __future__ import annotations
import re
from collections import Counter

CITE = re.compile(r"«([^»]+)»")


def sentences(text: str) -> list[str]:
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    out = []
    for l in lines:
        out += [x.strip() for x in re.split(r"(?<=[.!?])\s+(?!«)", l) if len(x.strip()) > 15]
    return out


def evaluate(report: str, visited: list, final_sections: list, all_sections: list, docs: dict,
             cost: dict | None = None, budget: int | None = None) -> dict:
    tries = [d for _, d in visited]
    read = set(tries)
    sents = sentences(report)
    grounded = [s for s in sents if any(c.strip() in docs for c in CITE.findall(s))]
    cites = [c.strip() for c in CITE.findall(report) if c.strip() in docs]
    used = Counter(cites)
    interp = sum(n for c, n in used.items() if c.startswith(("기본통칙", "집행기준")))
    cost = cost or {}
    m = {
        "문장수": len(sents), "근거율": round(100 * len(grounded) / max(len(sents), 1), 1),
        "인용수": len(cites), "읽은문서": len(read), "읽기횟수": len(tries),
        "인용편중": round(100 * max(used.values()) / sum(used.values()), 1) if used else 0.0,
        "중복률": round(100 * (1 - len(read) / len(tries)), 1) if tries else 0.0,
        "읽고안쓴": sorted(read - set(used)),
        "해석자료비율": round(100 * interp / max(len(cites), 1), 1),
        "절수": len(final_sections), "보고서자수": len(report),
        "절당자수": round(sum(len(s["본문"]) for s in final_sections) / max(len(final_sections), 1)),
        "재위임절": len([s for s in all_sections if s.get("바퀴", 1) > 1]),
        "LLM호출": cost.get("calls", 0),
        "코디네이터글자": cost.get("coord", 0), "서브에이전트글자": cost.get("sub", 0),
        "격리율": round(100 * cost.get("coord", 0) / max(cost.get("coord", 0) + cost.get("sub", 0), 1), 1),
        # 경보
        "허위인용": sorted(set(c.strip() for c in CITE.findall(report)) - read),
        "인용0절": [s["절"] for s in final_sections if not s["인용"]],
        "예산미소진": (budget - len(tries)) if budget else 0,
    }
    return m


def alarms(m: dict) -> list[str]:
    out = []
    if m["허위인용"]:
        out.append(f"허위인용 {len(m['허위인용'])}")
    if m["인용0절"]:
        out.append(f"인용0절 {len(m['인용0절'])}")
    if m["예산미소진"] > 0:
        out.append(f"예산미소진 {m['예산미소진']}")
    return out


def line(m: dict) -> str:
    a = alarms(m)
    return (f"⑥ 평가   문장 {m['문장수']} · 근거율 {m['근거율']}% · 인용편중 {m['인용편중']}% · 중복률 {m['중복률']}% · "
            f"읽고안쓴 {len(m['읽고안쓴'])}건 · 해석자료 {m['해석자료비율']}% · 격리율 {m['격리율']}% · "
            f"LLM {m['LLM호출']}회 · 경보 {'없음' if not a else ', '.join(a)}")
