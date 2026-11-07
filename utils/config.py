"""환경변수 로딩: st.secrets(Streamlit Cloud) → .env / os.environ(로컬) 순서로 찾는다."""

import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "gpt-6.1-terra"

# 키 이름을 잘못 적은 경우(OPEN_API_KEY)도 허용한다.
_ALIASES = {"OPENAI_API_KEY": ["OPENAI_API_KEY", "OPEN_API_KEY"]}

# 메모장 등에서 붙여 넣을 때 섞이기 쉬운 따옴표/공백 문자
_STRIP_CHARS = " \t\r\n\"'“”‘’﻿"


def _clean(value):
    if value is None:
        return None
    value = str(value).strip(_STRIP_CHARS)
    return value or None


def _from_secrets(name: str):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        # 로컬에 secrets.toml이 없으면 예외가 발생한다 → .env로 넘어간다.
        pass
    return None


def get_config(key: str, default: str | None = None) -> str | None:
    for name in _ALIASES.get(key, [key]):
        value = _clean(_from_secrets(name))
        if value:
            return value
    for name in _ALIASES.get(key, [key]):
        value = _clean(os.getenv(name))
        if value:
            return value
    return default


def get_api_key() -> str | None:
    return get_config("OPENAI_API_KEY")


def get_model() -> str:
    return get_config("OPENAI_MODEL", DEFAULT_MODEL)
