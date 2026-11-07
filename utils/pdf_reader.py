"""PDF 텍스트 추출. 업로드 파일은 디스크에 저장하지 않고 메모리(bytes)에서만 처리한다."""

from dataclasses import dataclass, field
from io import BytesIO

import streamlit as st
from pypdf import PdfReader
from pypdf.errors import PdfReadError


@dataclass
class PdfDocument:
    name: str
    pages: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def char_count(self) -> int:
        return sum(len(p) for p in self.pages)

    @property
    def has_text(self) -> bool:
        return self.char_count > 0


# 캐시는 서버 프로세스 전체(모든 사용자)가 공유하므로, 메모리 한도(Streamlit Cloud 약 1GB)와
# 개인정보를 고려해 개수와 보관 시간을 제한한다.
@st.cache_data(show_spinner=False, max_entries=20, ttl=3600)
def extract_pages(file_bytes: bytes) -> tuple[list[str], str | None]:
    """페이지별 텍스트 리스트와 오류 메시지(없으면 None)를 반환한다.

    bytes를 입력으로 받아 st.cache_data가 내용 기준으로 캐싱한다.
    AES로 암호화된 PDF를 열려면 cryptography 패키지가 필요하다 (requirements.txt).
    """
    try:
        reader = PdfReader(BytesIO(file_bytes))
    except PdfReadError as e:
        return [], f"PDF를 읽을 수 없습니다: {e}"
    except Exception as e:
        return [], f"PDF를 여는 중 오류가 발생했습니다: {e}"

    if reader.is_encrypted:
        try:
            # 빈 암호로 잠긴 PDF(열람은 자유, 편집만 제한)는 열 수 있다.
            if not reader.decrypt(""):
                return [], "암호가 걸린 PDF는 지원하지 않습니다."
        except Exception:
            return [], "암호가 걸린 PDF는 지원하지 않습니다."

    pages = []
    for page in reader.pages:
        try:
            pages.append((page.extract_text() or "").strip())
        except Exception:
            pages.append("")
    return pages, None


def read_pdf(name: str, file_bytes: bytes) -> PdfDocument:
    pages, error = extract_pages(file_bytes)
    return PdfDocument(name=name, pages=pages, error=error)


def to_tagged_text(doc: PdfDocument) -> str:
    """LLM이 근거 페이지를 인용할 수 있도록 '[파일명 p.N]' 태그를 붙인 텍스트."""
    return "\n\n".join(
        f"[{doc.name} p.{i}]\n{text}" for i, text in enumerate(doc.pages, start=1) if text
    )
