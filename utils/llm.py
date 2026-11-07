"""OpenAI 연동: 분석(Map-Reduce 포함), 질의응답, 오류 메시지 변환."""

import re
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed

import openai
from openai import OpenAI

from utils.config import get_api_key, get_model
from utils.pdf_reader import PdfDocument

# 한 번의 요청에 넣을 원문 최대 글자 수. 이보다 길면 청크로 나눠 Map-Reduce 처리한다.
CHUNK_SIZE = 30_000
# Map 단계 동시 요청 수 (너무 크면 429 Rate Limit 위험)
MAP_WORKERS = 4
# 부분 결과가 CHUNK_SIZE를 넘을 때 묶어서 다시 압축하는 최대 단계 수
MAX_CONDENSE_ROUNDS = 3

LANGUAGES = {"한국어": "Korean", "English": "English"}

COMPARE = "문서 비교"
CUSTOM = "사용자 정의"

ANALYSIS_PROMPTS = {
    "전체 요약": (
        "문서 전체 내용을 3~5개 문단으로 요약하세요. 문서의 목적, 핵심 주장/내용, 결론을 포함하세요."
    ),
    "핵심 포인트": (
        "문서의 핵심 내용을 중요도 순으로 불릿 포인트(5~10개)로 정리하세요. "
        "각 항목 끝에 근거 페이지를 [파일명 p.N] 형식으로 표시하세요."
    ),
    "키워드 추출": (
        "문서의 핵심 키워드 10개 내외를 뽑아 Markdown 표(키워드 | 설명 | 등장 페이지)로 정리하세요."
    ),
    "수치/데이터 추출": (
        "문서에 나오는 숫자, 통계, 금액, 비율, 날짜를 빠짐없이 찾아 "
        "Markdown 표(항목 | 값 | 의미 | 페이지)로 정리하세요. 문서에 없는 값은 만들지 마세요."
    ),
    COMPARE: (
        "업로드된 여러 문서를 비교 분석하세요. 각 문서의 주제를 한 줄씩 소개한 뒤, "
        "공통점, 차이점, 각 문서에만 있는 고유한 내용을 정리하고, 마지막에 종합 의견을 쓰세요."
    ),
    CUSTOM: "",
}

_BASE_SYSTEM = (
    "당신은 PDF 문서를 분석하는 전문 분석가입니다. 제공된 문서 내용에만 근거해서 답하고, "
    "문서에 없는 내용은 추측하지 말고 '문서에서 확인할 수 없습니다'라고 말하세요. "
    "문서 텍스트에는 [파일명 p.N] 형식의 페이지 표시가 있습니다. "
    "결과는 Markdown으로 작성하세요."
)


def _lang_rule(language: str) -> str:
    return f"Write the entire answer in {LANGUAGES.get(language, 'Korean')}."


def get_client() -> OpenAI | None:
    api_key = get_api_key()
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


# ---------------------------------------------------------------------------
# 텍스트 준비
# ---------------------------------------------------------------------------

def page_blocks(doc: PdfDocument) -> list[str]:
    return [f"[{doc.name} p.{i}]\n{text}" for i, text in enumerate(doc.pages, 1) if text]


def chunk_document(doc: PdfDocument, size: int = CHUNK_SIZE) -> list[str]:
    """페이지 단위로 묶어서 size 이하의 청크를 만든다. 한 페이지가 size보다 크면 잘라서 나눈다."""
    chunks, current = [], ""
    for block in page_blocks(doc):
        pieces = [block[i : i + size] for i in range(0, len(block), size)]
        for piece in pieces:
            if current and len(current) + len(piece) + 2 > size:
                chunks.append(current)
                current = ""
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def full_text(docs: list[PdfDocument]) -> str:
    return "\n\n".join(b for d in docs for b in page_blocks(d))


# ---------------------------------------------------------------------------
# OpenAI 호출
# ---------------------------------------------------------------------------

def _stream(client: OpenAI, instructions: str, input_) -> Iterator[str]:
    stream = client.responses.create(
        model=get_model(), instructions=instructions, input=input_, stream=True
    )
    for event in stream:
        if event.type == "response.output_text.delta":
            yield event.delta
        elif event.type in ("response.failed", "error"):
            raise RuntimeError(getattr(event, "message", None) or "응답 생성에 실패했습니다.")


def _complete(client: OpenAI, instructions: str, input_) -> str:
    return client.responses.create(
        model=get_model(), instructions=instructions, input=input_
    ).output_text


def stream_analysis(
    client: OpenAI,
    docs: list[PdfDocument],
    analysis_type: str,
    language: str,
    custom_prompt: str = "",
    on_progress: Callable[[float, str], None] | None = None,
) -> Iterator[str]:
    """분석 결과를 스트리밍한다. 원문이 CHUNK_SIZE를 넘으면 Map-Reduce로 처리한다."""
    task = custom_prompt.strip() if analysis_type == CUSTOM else ANALYSIS_PROMPTS[analysis_type]
    system = f"{_BASE_SYSTEM}\n\n[분석 지시]\n{task}\n\n{_lang_rule(language)}"
    names = ", ".join(d.name for d in docs)
    text = full_text(docs)

    if len(text) <= CHUNK_SIZE:
        if on_progress:
            on_progress(1.0, "분석 결과를 생성하는 중...")
        yield from _stream(client, system, f"[분석할 문서: {names}]\n\n{text}")
        return

    # Map: 문서별로 청크를 나눠 부분 요약 (문서끼리 섞이지 않게 문서 단위로 나눈다)
    jobs = [(d.name, c) for d in docs for c in chunk_document(d)]
    map_system = (
        f"{_BASE_SYSTEM}\n\n아래는 긴 문서의 일부입니다. 다음 최종 분석 지시에 필요한 정보를 "
        f"빠짐없이 추출해 정리하세요. 숫자, 고유명사, 페이지 표시([파일명 p.N])는 그대로 유지하세요.\n"
        f"[최종 분석 지시]\n{task}"
    )
    def report(p: float, msg: str) -> None:
        if on_progress:
            on_progress(p, msg)

    report(0.0, f"긴 문서를 {len(jobs)}개 부분으로 나눠 읽는 중... (0/{len(jobs)})")
    outputs = _parallel_complete(
        client, map_system, [chunk for _, chunk in jobs],
        lambda done: report(
            done / (len(jobs) + 1),
            f"긴 문서를 {len(jobs)}개 부분으로 나눠 읽는 중... ({done}/{len(jobs)})",
        ),
    )
    notes = [f"### {jobs[i][0]} (부분 {i + 1})\n{out}" for i, out in enumerate(outputs)]

    # 부분 결과가 너무 많아 한 번에 넣을 수 없으면, 묶어서 한 단계 더 압축한다 (계층형 Reduce).
    condense_system = (
        f"{_BASE_SYSTEM}\n\n아래는 긴 문서를 나눠 정리한 부분 결과 여러 개입니다. "
        f"중복을 합치고 최종 분석 지시에 필요한 정보만 남겨 하나로 압축하세요. "
        f"숫자, 고유명사, 페이지 표시는 그대로 유지하세요.\n[최종 분석 지시]\n{task}"
    )
    for round_no in range(1, MAX_CONDENSE_ROUNDS + 1):
        if sum(len(n) + 2 for n in notes) <= CHUNK_SIZE:
            break
        groups = _pack(notes, CHUNK_SIZE)
        report(len(jobs) / (len(jobs) + 1), f"부분 결과 {len(notes)}개를 {len(groups)}개로 압축하는 중... ({round_no}단계)")
        notes = _parallel_complete(client, condense_system, groups)
    reduce_body = "\n\n".join(notes)[:CHUNK_SIZE]  # 그래도 넘치면 잘라서 컨텍스트 초과를 막는다

    # Reduce: 부분 결과를 합쳐 최종 분석
    report(len(jobs) / (len(jobs) + 1), "부분 결과를 종합하는 중...")
    reduce_input = (
        f"[분석할 문서: {names}]\n아래는 문서를 나눠 읽고 정리한 부분 결과입니다. "
        f"이를 종합해 최종 결과를 작성하세요.\n\n" + reduce_body
    )
    yield from _stream(client, system, reduce_input)
    report(1.0, "완료")


def _pack(items: list[str], size: int) -> list[str]:
    """items를 순서대로 이어 붙여 size 이하의 묶음으로 만든다 (한 항목이 size보다 크면 잘라 넣는다)."""
    groups, current = [], ""
    for item in items:
        item = item[:size]
        if current and len(current) + len(item) + 2 > size:
            groups.append(current)
            current = ""
        current = f"{current}\n\n{item}" if current else item
    if current:
        groups.append(current)
    return groups


def _parallel_complete(
    client: OpenAI, instructions: str, inputs: list[str], on_done: Callable[[int], None] | None = None
) -> list[str]:
    """inputs를 동시에 요청하고 입력 순서대로 결과를 돌려준다. on_done은 호출한 스레드에서 실행된다."""
    outputs = [""] * len(inputs)
    with ThreadPoolExecutor(max_workers=MAP_WORKERS) as pool:
        futures = {pool.submit(_complete, client, instructions, x): i for i, x in enumerate(inputs)}
        for done, future in enumerate(as_completed(futures), 1):
            outputs[futures[future]] = future.result()
            if on_done:
                on_done(done)
    return outputs


# ---------------------------------------------------------------------------
# 질의응답
# ---------------------------------------------------------------------------

def _terms(text: str) -> set[str]:
    """간단한 검색용 토큰: 영문/숫자 단어 + 한글 2글자 조각."""
    text = text.lower()
    words = set(re.findall(r"[a-z0-9]+", text))
    for w in re.findall(r"[가-힣]+", text):
        words.update(w[i : i + 2] for i in range(max(1, len(w) - 1)))
    return words


def select_context(docs: list[PdfDocument], question: str, size: int = CHUNK_SIZE) -> str:
    """문서가 길면 질문과 겹치는 단어가 많은 페이지 위주로 size 이하만 골라 넣는다."""
    text = full_text(docs)
    if len(text) <= size:
        return text
    q = _terms(question)
    blocks = [b for d in docs for b in page_blocks(d)]
    ranked = sorted(range(len(blocks)), key=lambda i: -len(q & _terms(blocks[i])))
    picked, total = [], 0
    for i in ranked:
        if total + len(blocks[i]) > size:
            continue
        picked.append(i)
        total += len(blocks[i])
    return "\n\n".join(blocks[i] for i in sorted(picked))


def stream_answer(
    client: OpenAI,
    docs: list[PdfDocument],
    history: list[dict],
    question: str,
    language: str,
) -> Iterator[str]:
    context = select_context(docs, question)
    system = (
        f"{_BASE_SYSTEM}\n\n사용자의 질문에 아래 문서 내용을 근거로 답하세요. "
        f"답변 마지막 줄에 '📌 근거: [파일명 p.N], ...' 형식으로 근거 페이지를 모두 적으세요. "
        f"근거를 찾을 수 없으면 그렇게 말하세요.\n{_lang_rule(language)}\n\n"
        f"[문서 내용]\n{context}"
    )
    messages = [{"role": m["role"], "content": m["content"]} for m in history[-10:]]
    messages.append({"role": "user", "content": question})
    yield from _stream(client, system, messages)


# ---------------------------------------------------------------------------
# 오류 메시지
# ---------------------------------------------------------------------------

def friendly_error(e: Exception) -> str:
    if isinstance(e, openai.AuthenticationError):
        return "API 키가 올바르지 않습니다. `.env` 또는 Streamlit Secrets의 `OPENAI_API_KEY`를 확인하세요."
    if isinstance(e, openai.PermissionDeniedError):
        return f"모델 `{get_model()}`에 접근할 권한이 없습니다. `OPENAI_MODEL` 설정과 계정 권한을 확인하세요."
    if isinstance(e, openai.NotFoundError):
        return f"모델 `{get_model()}`을(를) 찾을 수 없습니다. `OPENAI_MODEL` 값을 확인하세요."
    if isinstance(e, openai.RateLimitError):
        return "API 사용량 한도를 초과했습니다(429). 잠시 후 다시 시도하세요."
    if isinstance(e, (openai.APIConnectionError, openai.APITimeoutError)):
        return "OpenAI 서버에 연결할 수 없습니다. 인터넷 연결을 확인하고 다시 시도하세요."
    if isinstance(e, openai.BadRequestError):
        return f"요청이 거부되었습니다: {getattr(e, 'message', e)}"
    if isinstance(e, openai.APIError):
        return f"OpenAI API 오류가 발생했습니다: {getattr(e, 'message', e)}"
    return f"알 수 없는 오류가 발생했습니다: {e}"
