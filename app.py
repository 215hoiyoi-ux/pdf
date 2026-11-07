from datetime import datetime

import streamlit as st

from utils.config import get_api_key, get_model
from utils.llm import (
    ANALYSIS_PROMPTS,
    COMPARE,
    CUSTOM,
    LANGUAGES,
    friendly_error,
    get_client,
    stream_analysis,
    stream_answer,
)
from utils.pdf_reader import read_pdf
from utils.style import empty_state_html, inject_css, muted, steps_html

st.set_page_config(page_title="PDF 분석기", page_icon="📄", layout="wide")
inject_css()

USER_AVATAR = "🧑"
AI_AVATAR = "🤖"
EXAMPLE_QUESTIONS = [
    "이 문서의 핵심 내용을 세 줄로 알려줘",
    "문서에 나온 중요한 숫자를 정리해줘",
    "이 문서에서 주의해야 할 점은?",
]

# --- 헤더 ---
st.title("📄 PDF 분석기")
st.caption("PDF를 업로드하면 AI가 내용을 요약·분석하고 질문에 답해 드립니다.")
st.markdown(steps_html(), unsafe_allow_html=True)

api_key = get_api_key()
if not api_key:
    st.error(
        "OpenAI API 키가 설정되지 않았습니다.\n\n"
        "- **로컬**: 프로젝트 폴더의 `.env` 파일에 `OPENAI_API_KEY=sk-...` 를 추가하세요.\n"
        "- **Streamlit Cloud**: 앱 Settings → Secrets 에 `OPENAI_API_KEY = \"sk-...\"` 를 추가하세요.",
        icon="🔑",
    )

# --- 사이드바: 파일 업로드 ---
with st.sidebar:
    st.subheader("📁 파일 업로드")
    uploaded_files = st.file_uploader(
        "PDF 파일을 선택하세요 (여러 개 가능)", type="pdf", accept_multiple_files=True
    )

# --- PDF 텍스트 추출 ---
documents, warnings = [], []
for f in uploaded_files or []:
    doc = read_pdf(f.name, f.getvalue())
    if doc.error:
        warnings.append(f"**{doc.name}**: {doc.error}")
        continue
    if not doc.has_text:
        warnings.append(
            f"**{doc.name}**: 텍스트를 찾을 수 없습니다. 스캔(이미지) PDF는 지원하지 않습니다."
        )
    documents.append(doc)
text_docs = [d for d in documents if d.has_text]  # AI에 보낼 문서

# 업로드 파일 구성이 바뀌면 이전 분석 결과와 대화 기록을 초기화한다.
file_key = tuple(f.file_id for f in uploaded_files or [])
if st.session_state.get("file_key") != file_key:
    st.session_state.file_key = file_key
    st.session_state.result = None
    st.session_state.messages = []

# --- 사이드바: 분석 설정 / 정보 ---
with st.sidebar:
    st.divider()
    st.subheader("⚙️ 분석 설정")
    options = [k for k in ANALYSIS_PROMPTS if k != COMPARE or len(text_docs) >= 2]
    analysis_type = st.selectbox(
        "분석 유형",
        options,
        help="'문서 비교'는 텍스트가 있는 PDF를 2개 이상 올렸을 때 선택할 수 있습니다.",
    )
    custom_prompt = ""
    if analysis_type == CUSTOM:
        custom_prompt = st.text_area(
            "분석 지시문", placeholder="예: 이 문서의 위험 요소와 대응 방안을 정리해줘"
        )
    language = st.radio("출력 언어", list(LANGUAGES), horizontal=True)

    st.divider()
    st.subheader("ℹ️ 정보")
    st.markdown(f"**모델** `{get_model()}`")
    st.markdown(f"**API 키** {'✅ 연결됨' if api_key else '❌ 설정 필요'}")
    st.caption(
        "업로드한 파일은 서버에 저장되지 않고 이번 세션에서만 사용됩니다. "
        "분석·질의응답 시 PDF 텍스트가 OpenAI API로 전송되니, 민감한 개인정보가 담긴 문서는 주의하세요."
    )

# --- 업로드 전 빈 화면 ---
if not uploaded_files:
    st.markdown(empty_state_html(), unsafe_allow_html=True)
    st.stop()

for w in warnings:
    st.warning(w, icon="⚠️")

# --- 업로드 파일 요약 ---
c1, c2, c3 = st.columns(3)
c1.metric("📁 파일 수", f"{len(documents)}개")
c2.metric("📄 총 페이지", f"{sum(d.page_count for d in documents):,}쪽")
c3.metric("🔤 총 글자 수", f"{sum(d.char_count for d in documents):,}자")
with st.expander("파일별 상세 보기"):
    st.dataframe(
        [
            {"파일명": d.name, "페이지 수": d.page_count, "글자 수": d.char_count}
            for d in documents
        ],
        hide_index=True,
        width="stretch",
    )

tab_analysis, tab_chat, tab_raw = st.tabs(["🔍 분석", "💬 질의응답", "📃 원문"])

# --- 분석 탭 ---
with tab_analysis:
    disabled_reason = None
    if not api_key:
        disabled_reason = "API 키가 없어 분석할 수 없습니다."
    elif not text_docs:
        disabled_reason = "텍스트가 있는 PDF가 없어 분석할 수 없습니다."

    st.markdown(f"**선택한 분석:** {analysis_type} · {language}")
    # 사용자 정의 지시문이 비었다고 버튼을 비활성화하면, 지시문을 입력하고 바로 누른 첫 클릭이
    # (입력 확정 rerun 때문에) 무시된다. 그래서 버튼은 살려 두고 누른 시점에 검사한다.
    clicked = st.button(
        "🚀 분석 시작", type="primary", disabled=disabled_reason is not None
    )
    if disabled_reason:
        st.caption(disabled_reason)

    run_analysis = clicked or st.session_state.pop("rerun_analysis", False)
    if run_analysis and disabled_reason:
        run_analysis = False
    if run_analysis and analysis_type == CUSTOM and not custom_prompt.strip():
        st.warning("사이드바의 **분석 지시문**을 입력한 뒤 다시 눌러 주세요.", icon="✍️")
        run_analysis = False

    if run_analysis:
        st.session_state.result = None
        progress = st.progress(0.0, text="분석을 준비하는 중...")
        with st.container(border=True):
            try:
                with st.spinner(f"`{get_model()}` 모델로 분석하는 중..."):
                    text = st.write_stream(
                        stream_analysis(
                            get_client(),
                            text_docs,
                            analysis_type,
                            language,
                            custom_prompt,
                            on_progress=lambda p, msg: progress.progress(p, text=msg),
                        )
                    )
                st.session_state.result = {
                    "type": analysis_type,
                    "language": language,
                    "files": [d.name for d in text_docs],
                    "text": text if isinstance(text, str) else "".join(map(str, text)),
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
            except Exception as e:
                st.error(friendly_error(e), icon="🚨")
        progress.empty()
    elif st.session_state.result:
        result = st.session_state.result
        st.caption(f"{result['time']} · {result['type']} · {result['language']}")
        with st.container(border=True):
            st.markdown(result["text"])

    result = st.session_state.result
    if result:
        md = (
            f"# PDF 분석 결과 - {result['type']}\n\n"
            f"- 분석 일시: {result['time']}\n"
            f"- 모델: {get_model()}\n"
            f"- 파일: {', '.join(result['files'])}\n\n---\n\n{result['text']}\n"
        )
        with st.container(horizontal=True):
            st.download_button(
                "📥 결과 다운로드 (.md)",
                md,
                file_name=f"pdf_analysis_{datetime.now():%Y%m%d_%H%M}.md",
                mime="text/markdown",
            )
            if st.button("🔄 다시 분석", disabled=disabled_reason is not None):
                st.session_state.rerun_analysis = True
                st.rerun()
    elif not run_analysis:
        st.info("분석 유형을 고른 뒤 **🚀 분석 시작**을 누르면 결과가 여기에 표시됩니다.")

# --- 질의응답 탭 ---
with tab_chat:
    if not text_docs:
        st.info("텍스트가 있는 PDF를 먼저 업로드하세요.")
    elif not api_key:
        st.info("API 키가 없어 질의응답을 사용할 수 없습니다.")
    else:
        if st.session_state.messages:
            if st.button("🗑️ 대화 초기화"):
                st.session_state.messages = []
                st.rerun()
        else:
            st.markdown("**💡 이런 질문을 해 보세요**")
            with st.container(horizontal=True):
                for i, q in enumerate(EXAMPLE_QUESTIONS):
                    if st.button(q, key=f"example_{i}"):
                        st.session_state.pending_question = q

        # 탭 안의 chat_input은 호출 위치에 그려지므로, 대화는 입력창 위 컨테이너에 표시한다.
        chat_box = st.container()
        typed = st.chat_input("PDF 내용에 대해 질문하세요")
        question = typed or st.session_state.pop("pending_question", None)

        with chat_box:
            for m in st.session_state.messages:
                avatar = USER_AVATAR if m["role"] == "user" else AI_AVATAR
                with st.chat_message(m["role"], avatar=avatar):
                    st.markdown(m["content"])

        if question:
            with chat_box.chat_message("user", avatar=USER_AVATAR):
                st.markdown(question)
            with chat_box.chat_message("assistant", avatar=AI_AVATAR):
                try:
                    answer = st.write_stream(
                        stream_answer(
                            get_client(),
                            text_docs,
                            st.session_state.messages,
                            question,
                            language,
                        )
                    )
                    st.session_state.messages += [
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": answer},
                    ]
                    st.rerun()  # 대화 기록 기준으로 다시 그려 [대화 초기화] 버튼을 표시
                except Exception as e:
                    st.error(friendly_error(e), icon="🚨")

# --- 원문 탭 ---
with tab_raw:
    if not documents:
        st.caption("텍스트를 읽을 수 있는 PDF가 없습니다.")
    else:
        idx = 0
        if len(documents) > 1:
            idx = st.selectbox(
                "파일 선택", range(len(documents)), format_func=lambda i: documents[i].name
            )
        doc = documents[idx]
        st.caption(f"{doc.page_count}쪽 · {doc.char_count:,}자")
        for i, text in enumerate(doc.pages, start=1):
            label = f"p.{i} · {len(text):,}자" if text else f"p.{i} · 텍스트 없음"
            with st.expander(label):
                if text:
                    st.text(text)
                else:
                    st.markdown(
                        muted("이 페이지에서 추출된 텍스트가 없습니다. (이미지/빈 페이지)"),
                        unsafe_allow_html=True,
                    )
