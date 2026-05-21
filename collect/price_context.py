"""
중립적 가격 맥락 fetcher.
방향·판정을 완전히 제거하고 변동 크기·거래량·위치만 반환한다.
fetch_close_direction()은 오직 divergence 후처리 전용 — 프롬프트 빌더로 절대 흘리지 말 것.
"""

import os
import re
import sys
from typing import Optional

import requests

SNIPERBOARD_API_BASE = os.environ.get("SNIPERBOARD_API_BASE", "http://localhost:5001")
API_TIMEOUT = 10

# 오염 방지선: 방향 단어 패턴 (word-boundary 적용)
_DIRECTION_PATTERN = re.compile(
    r"\b(up|down|bullish|bearish|buy|sell|strong|weak|rally|drop|surge|crash|rose|fell)\b"
    r"|올랐|떨어|급등|급락|상승|하락",
    re.IGNORECASE,
)

_UNAVAILABLE: dict = {
    "volatility": None,
    "volume_ratio": None,
    "near_key_level": None,
    "abnormal_move": None,
    "available": False,
}


def _assert_no_direction(d: dict, label: str = "") -> None:
    """반환 dict에 방향 단어가 없는지 기계적으로 보증."""
    text = str(d)
    m = _DIRECTION_PATTERN.search(text)
    if m:
        raise AssertionError(
            f"오염 방지선 위반 [{label}]: 방향 단어 '{m.group()}' 감지 — {d}"
        )


def fetch_price_context(symbol: str) -> dict:
    """SniperBoard에서 중립적 가격 맥락만 추출해 반환.
    API 실패 시 available:False 반환 — 수집은 맨눈 모드로 계속되어야 한다.
    """
    try:
        daily_resp = requests.get(
            f"{SNIPERBOARD_API_BASE}/api/daily",
            params={"symbol": symbol},
            timeout=API_TIMEOUT,
        )
        daily_resp.raise_for_status()
        daily = daily_resp.json()
    except Exception as e:
        print(f"[WARN] {symbol}: SniperBoard API 실패 ({e})", file=sys.stderr)
        return dict(_UNAVAILABLE)

    try:
        candles = daily.get("candles") or []
        if not candles:
            raise ValueError("candles 비어 있음")
        last = candles[-1]

        indicators = daily.get("indicators") or {}
        atr14_series = indicators.get("atr14") or []
        atr14 = float(atr14_series[-1]) if atr14_series else 0.0

        close = float(last.get("close") or 0)
        open_price = float(last.get("open") or 0)

        # 52주 고/저점: candles 최대 252봉에서 직접 계산. 돌파/이탈 판정 없음.
        year_candles = candles[-252:]
        high_52w = max(float(c.get("high") or 0) for c in year_candles)
        low_52w = min(float(c.get("low") or float("inf")) for c in year_candles)

        # 변동폭: 절대값만. 부호(방향) 절대 사용 안 함.
        daily_range = abs(close - open_price)
        range_ratio = (daily_range / atr14) if atr14 > 0 else 0.0

        if range_ratio < 0.5:
            volatility = "calm"
        elif range_ratio < 1.0:
            volatility = "normal"
        elif range_ratio < 1.5:
            volatility = "elevated"
        else:
            volatility = "extreme"

        abnormal_move: bool = range_ratio > 1.5

        # 거래량 비율 (배수, 소수 1자리) — daily 데이터에서 직접 추출
        recent_vol = float(last.get("volume") or 0)
        vol_avg20_series = daily.get("vol_avg20") or []
        vol_avg20 = float(vol_avg20_series[-1]) if vol_avg20_series else 0.0
        volume_ratio = round(recent_vol / vol_avg20, 1) if vol_avg20 > 0 else None

        # 52주 위치: ±3% 이내면 레이블, 그 외 none. 돌파/이탈 판정 없음.
        near_key_level = "none"
        if high_52w > 0 and close > 0 and abs(close - high_52w) / high_52w <= 0.03:
            near_key_level = "near_52w_high"
        elif low_52w > 0 and close > 0 and abs(close - low_52w) / low_52w <= 0.03:
            near_key_level = "near_52w_low"

        result = {
            "volatility": volatility,
            "volume_ratio": volume_ratio,
            "near_key_level": near_key_level,
            "abnormal_move": abnormal_move,
            "available": True,
        }

        # 오염 방지선 기계 검증 (available 필드 제외 후 검사)
        _assert_no_direction(
            {k: v for k, v in result.items() if k != "available"}, symbol
        )

        return result

    except Exception as e:
        print(f"[WARN] {symbol}: price_context 계산 실패 ({e})", file=sys.stderr)
        return dict(_UNAVAILABLE)


def fetch_market_context() -> dict:
    """^VIX 수준만 반환. 방향성 정보는 모두 무시한다."""
    try:
        resp = requests.get(
            f"{SNIPERBOARD_API_BASE}/api/macro",
            timeout=API_TIMEOUT,
        )
        resp.raise_for_status()
        macro = resp.json()

        # macro 배열에서 ^VIX 항목 찾기
        macro_list = macro.get("macro") or []
        vix_item = next((m for m in macro_list if m.get("symbol") == "^VIX"), None)
        vix = float(vix_item["price"]) if vix_item and vix_item.get("price") else 0.0

        if vix < 16:
            vix_level = "low"
        elif vix < 22:
            vix_level = "normal"
        else:
            vix_level = "high"

        return {"vix_level": vix_level, "available": True}

    except Exception as e:
        print(f"[WARN] MARKET: SniperBoard macro API 실패 ({e})", file=sys.stderr)
        return {"vix_level": None, "available": False}


def fetch_close_direction(symbol: str) -> str:
    """종가 방향 반환 (up / down / flat).
    ⛔ 오직 divergence 후처리 계산 전용. 이 값은 프롬프트 빌더로 절대 흘리지 말 것.
    """
    try:
        resp = requests.get(
            f"{SNIPERBOARD_API_BASE}/api/daily",
            params={"symbol": symbol},
            timeout=API_TIMEOUT,
        )
        resp.raise_for_status()
        daily = resp.json()

        candles = daily.get("candles") or []
        if not candles:
            return "flat"
        last = candles[-1]
        close = float(last.get("close") or 0)
        open_price = float(last.get("open") or 0)

        if open_price == 0:
            return "flat"

        change_pct = (close - open_price) / open_price
        if change_pct > 0.001:
            return "up"
        elif change_pct < -0.001:
            return "down"
        else:
            return "flat"

    except Exception:
        return "flat"
