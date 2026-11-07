"""커스텀 CSS와 HTML 조각을 한 곳에 모아 둔다.

색은 고정값 대신 currentColor/반투명 값을 써서 라이트·다크 모드 모두에서 읽히게 한다.
"""

import streamlit as st

_CSS = """
<style>
/* 상단 사용 방법 3단계: 좁은 화면에서는 자동 줄바꿈 */
.pa-steps { display: flex; flex-wrap: wrap; gap: .5rem; margin: .25rem 0 1rem; }
.pa-step {
  flex: 1 1 160px; padding: .6rem .8rem; border-radius: .6rem;
  border: 1px solid rgba(128,128,128,.25); background: rgba(128,128,128,.06);
  font-size: .9rem; line-height: 1.4;
}
.pa-step b {
  display: inline-block; width: 1.4rem; height: 1.4rem; line-height: 1.3rem;
  text-align: center; border-radius: 50%; border: 1.5px solid currentColor;
  font-size: .8rem; margin-right: .45rem; opacity: .75;
}

/* PDF 업로드 전 빈 화면 */
.pa-empty {
  text-align: center; padding: 2.5rem 1rem; margin: 1rem 0;
  border: 2px dashed rgba(128,128,128,.35); border-radius: .8rem;
}
.pa-empty .icon { font-size: 2.6rem; line-height: 1; }
.pa-empty h3 { margin: .6rem 0 .3rem; font-size: 1.15rem; }
.pa-empty p { opacity: .7; margin: 0; font-size: .92rem; }

/* 지표 카드 */
[data-testid="stMetric"] {
  border: 1px solid rgba(128,128,128,.25); border-radius: .6rem;
  padding: .6rem .9rem; background: rgba(128,128,128,.05);
}

/* 텍스트 없는 페이지 안내 */
.pa-muted { opacity: .55; font-style: italic; font-size: .9rem; }

/* 모바일: 제목 크기 축소 */
@media (max-width: 640px) {
  h1 { font-size: 1.7rem !important; }
}
</style>
"""

STEPS = [
    ("1", "📁 사이드바에서 PDF 업로드"),
    ("2", "⚙️ 분석 유형과 언어 선택"),
    ("3", "🚀 [분석 시작] 클릭 또는 질문하기"),
]


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def steps_html() -> str:
    items = "".join(f'<div class="pa-step"><b>{n}</b>{t}</div>' for n, t in STEPS)
    return f'<div class="pa-steps">{items}</div>'


def empty_state_html() -> str:
    return (
        '<div class="pa-empty"><div class="icon">📄</div>'
        "<h3>아직 업로드한 PDF가 없습니다</h3>"
        "<p>왼쪽 사이드바에서 PDF 파일을 올리면 요약·분석·질의응답을 시작할 수 있어요.<br>"
        "(모바일에서는 왼쪽 위 <b>»</b> 버튼으로 사이드바를 여세요.)<br>"
        "여러 파일을 한 번에 올리면 문서 비교도 할 수 있습니다.</p></div>"
    )


def muted(text: str) -> str:
    return f'<div class="pa-muted">{text}</div>'
