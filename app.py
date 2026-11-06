"""데모 — 질문을 넣으면 보고서와 함께 '절마다 누가 무엇을 읽고 무엇을 썼는지'를 보여 준다.
    streamlit run app.py
새 질문 실행은 OpenAI 호출(비용)이 든다. 저장된 실행 보기는 output/runs.jsonl 만 읽는다.
"""
from __future__ import annotations
import json, re
import streamlit as st

import graph as G
import metrics as M

st.set_page_config(page_title="종합소득세 딥리서처", layout="wide")
st.title("종합소득세 딥리서처")
st.caption(f"코퍼스: 소득세법·시행령·시행규칙(현행) + 기본통칙 + 집행기준 · {len(G.DOCS):,}건 · "
           f"{sum(map(len, G.DOCS.values())):,}자 — 코디네이터 1 + 리서처 N")


def load_runs():
    p = G.OUT / "runs.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def show_doc(key: str):
    with st.popover(f"«{key}» 원문"):
        st.markdown(f"**{key} · {G.TITLES.get(key, '')}** ({G.KINDS.get(key, '')})")
        st.text(G.DOCS.get(key, "(코퍼스에 없음)")[:6000])


def show_record(rec: dict):
    m = rec["metrics"]
    st.subheader(rec["질문"])
    st.caption(f"{rec['라벨']} · {rec['시각']} · {rec['걸린초']}초")
    al = M.alarms(m)
    (st.error if al else st.success)("경보: " + (", ".join(al) if al else "없음 (허위인용 0 · 인용 0곳인 절 0 · 예산 소진)"))
    c = st.columns(6)
    for col, (k, unit) in zip(c, [("근거율", "%"), ("인용편중", "%"), ("중복률", "%"), ("해석자료비율", "%"),
                                   ("격리율", "%"), ("LLM호출", "회")]):
        col.metric(k, f"{m[k]}{unit}")
    st.caption(f"코디네이터가 본 글자 {m['코디네이터글자']:,} · 서브에이전트가 본 글자 {m['서브에이전트글자']:,} · "
               f"읽은 문서 {m['읽은문서']}건 · 읽고 안 쓴 문서 {len(m['읽고안쓴'])}건 · 재위임된 절 {m['재위임절']}개")

    tab1, tab2, tab3 = st.tabs(["절마다 누가 무엇을", "보고서", "로그"])
    with tab1:
        finals = {(s["절"], s["바퀴"]) for s in rec["최종절"]}
        for s in sorted(rec["sections"], key=lambda x: (x["번호"], x["바퀴"])):
            used = (s["절"], s["바퀴"]) in finals
            head = (f"{s['번호']+1}. {s['절']} — {s['역할']} · {s['바퀴']}바퀴 · 시작 «{s.get('시작문서') or '자율'}» · "
                    f"인용 {len(s['인용'])}곳 · {'충분' if s['충분'] else '부족'}" + ("" if used else " · ⚠ 최종본에서 빠짐"))
            with st.expander(head, expanded=False):
                st.markdown(f"**지시**: {s['지시']}")
                if not s["충분"]:
                    st.markdown(f"**자기신고(부족)**: {s['부족']}")
                left, right = st.columns(2)
                with left:
                    st.markdown("**읽은 문서 → 리서처가 위로 올린 요약** (원문은 여기 아래에만 남는다)")
                    for d, note in s["메모"]:
                        new = "" if d in s.get("새로읽음", []) else " (지난 바퀴)"
                        st.markdown(f"- **{d}** · {G.TITLES.get(d, '')}{new}  \n  {note}")
                with right:
                    st.markdown("**올린 원고**")
                    st.write(s["본문"])
                    if s["허위인용"]:
                        st.error("읽지 않은 문서 인용: " + ", ".join(s["허위인용"]))
                st.markdown("**인용 대조** — 인용된 문서의 원문을 열어 문장과 맞는지 직접 확인")
                for d in dict.fromkeys(c for c in s["인용"] if c in G.DOCS):
                    show_doc(d)
    with tab2:
        st.markdown(rec["report"])
    with tab3:
        st.code("\n".join(rec["log"]))


mode = st.sidebar.radio("보기", ["저장된 실행", "새로 질문하기", "두 실행 나란히"])
runs = load_runs()

if mode == "저장된 실행":
    if not runs:
        st.info("저장된 실행이 없습니다. python graph.py --q Q3 로 먼저 돌리세요.")
    else:
        labels = [f"{r['qid'] or '-'} · {r['라벨']} · {r['시각']} · {r['질문'][:30]}" for r in runs]
        i = st.sidebar.selectbox("실행", range(len(runs)), index=len(runs) - 1, format_func=lambda i: labels[i])
        show_record(runs[i])
elif mode == "새로 질문하기":
    ex = st.selectbox("예시 질문", [""] + [f"{q['id']} {q['질문']}" for q in G.QUESTIONS.values()])
    q = st.text_area("질문", value=ex.split(" ", 1)[1] if ex else "")
    fake = st.checkbox("가짜 모델(비용 0, 흐름만 확인)")
    if st.button("실행", disabled=not q.strip()):
        with st.spinner("코디네이터가 목차를 짜고 리서처들이 읽는 중…"):
            rec = G.run(q.strip(), fake=fake, label="데모")
        show_record(rec)
else:
    if len(runs) < 2:
        st.info("실행이 두 개 이상 필요합니다.")
    else:
        labels = [f"{r['qid'] or '-'} · {r['라벨']} · {r['시각']}" for r in runs]
        a, b = st.columns(2)
        ia = a.selectbox("왼쪽", range(len(runs)), format_func=lambda i: labels[i])
        ib = b.selectbox("오른쪽", range(len(runs)), index=len(runs) - 1, format_func=lambda i: labels[i])
        with a:
            st.markdown(runs[ia]["report"])
        with b:
            st.markdown(runs[ib]["report"])
