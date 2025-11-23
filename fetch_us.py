# fetch_us.py
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

WIKI_SP500 = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

def get_sp500_tickers():
    """S&P500 티커를 위키 표에서 파싱"""
    tables = pd.read_html(WIKI_SP500)
    sp500 = tables[0]
    tickers = sp500["Symbol"].tolist()
    # BRK.B, BF.B 같은 표기 보정 (yfinance는 - 사용)
    tickers = [t.replace(".", "-") for t in tickers]
    return tickers

def safe_get_series(df, key):
    try:
        return df.loc[key]
    except Exception:
        return None

def calc_momentum_12m_ex1m(tk: yf.Ticker):
    """
    12개월 모멘텀(최근 1개월 제외) 근사:
    (1개월 전 가격 / 12개월 전 가격 - 1)
    trading day 기준으로 대략 252일과 21일 사용
    """
    try:
        hist = tk.history(period="370d")  # 대충 12m+1m
        if hist.empty or "Close" not in hist.columns:
            return None
        close = hist["Close"].dropna()
        if len(close) < 60:
            return None

        last = close.iloc[-1]
        one_month_ago = close.iloc[-22] if len(close) >= 22 else close.iloc[-1]
        twelve_month_ago = close.iloc[0]

        if twelve_month_ago == 0:
            return None
        mom = (one_month_ago / twelve_month_ago - 1.0) * 100.0
        return mom
    except Exception:
        return None

def calc_ev_ebitda(info):
    ev = info.get("enterpriseValue")
    ebitda = info.get("ebitda")
    try:
        if ev is None or ebitda is None or ebitda == 0:
            return None
        return ev / ebitda
    except Exception:
        return None

def calc_mini_fscore(tk: yf.Ticker, info):
    """
    미니 Piotroski F-score (0~5)
    데이터 없으면 0 처리.
    구성(5개):
      1) ROE > 0
      2) OCF > 0
      3) Long-term Debt 감소
      4) Gross Margin 증가
      5) Asset Turnover 증가
    """
    score = 0

    # 1) ROE > 0 (info returnOnEquity는 비율)
    roe = info.get("returnOnEquity")
    if roe is not None and roe > 0:
        score += 1

    # statements는 최신 2개 컬럼만 비교
    try:
        fin = tk.financials
        bs = tk.balance_sheet
        cf = tk.cashflow

        # 2) OCF > 0
        ocf_series = safe_get_series(cf, "Total Cash From Operating Activities")
        if ocf_series is not None and len(ocf_series) >= 1:
            if ocf_series.iloc[0] > 0:
                score += 1

        # 3) Long-term Debt 감소
        ltd_series = safe_get_series(bs, "Long Term Debt")
        if ltd_series is not None and len(ltd_series) >= 2:
            if ltd_series.iloc[0] < ltd_series.iloc[1]:
                score += 1

        # 4) Gross Margin 증가
        gp_series = safe_get_series(fin, "Gross Profit")
        rev_series = safe_get_series(fin, "Total Revenue")
        if gp_series is not None and rev_series is not None and len(gp_series) >= 2 and len(rev_series) >= 2:
            gm_now = gp_series.iloc[0] / rev_series.iloc[0] if rev_series.iloc[0] else None
            gm_prev = gp_series.iloc[1] / rev_series.iloc[1] if rev_series.iloc[1] else None
            if gm_now is not None and gm_prev is not None and gm_now > gm_prev:
                score += 1

        # 5) Asset Turnover 증가 (Revenue / Total Assets)
        assets_series = safe_get_series(bs, "Total Assets")
        if rev_series is not None and assets_series is not None and len(rev_series) >= 2 and len(assets_series) >= 2:
            at_now = rev_series.iloc[0] / assets_series.iloc[0] if assets_series.iloc[0] else None
            at_prev = rev_series.iloc[1] / assets_series.iloc[1] if assets_series.iloc[1] else None
            if at_now is not None and at_prev is not None and at_now > at_prev:
                score += 1

    except Exception:
        pass

    return score

def load_us_data(universe="SP500", limit=None):
    """
    universe:
      - "SP500" (default)
      - "AAPL,MSFT,NVDA" 처럼 콤마로 직접 지정도 가능
    """
    if universe.upper() == "SP500":
        tickers = get_sp500_tickers()
    else:
        tickers = [t.strip().upper() for t in universe.split(",") if t.strip()]

    if limit:
        tickers = tickers[:limit]

    rows = []
    for t in tickers:
        try:
            tk = yf.Ticker(t)
            info = tk.info or {}

            per = info.get("trailingPE")
            pbr = info.get("priceToBook")
            roe = info.get("returnOnEquity")
            dy = info.get("dividendYield")
            mcap = info.get("marketCap")

            ev_ebitda = calc_ev_ebitda(info)
            mom12 = calc_momentum_12m_ex1m(tk)
            fscore = calc_mini_fscore(tk, info)

            rows.append({
                "종목명": info.get("shortName") or info.get("longName") or t,
                "종목코드": t,
                "PER": per,
                "PBR": pbr,
                "ROE": roe * 100 if roe is not None else None,
                "배당수익률": dy * 100 if dy is not None else None,
                "시가총액": mcap,
                "시가총액(B$)": (mcap / 1e9) if mcap is not None else None,
                "EV/EBITDA": ev_ebitda,
                "모멘텀12M_ex1M(%)": mom12,
                "미니Fscore(0-5)": fscore
            })
        except Exception:
            continue

    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("미국주식 데이터를 가져오지 못했습니다(yfinance 응답 없음).")

    # 숫자형 변환
    num_cols = ["PER","PBR","ROE","배당수익률","시가총액","시가총액(B$)","EV/EBITDA","모멘텀12M_ex1M(%)","미니Fscore(0-5)"]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # 기본 이상치 제거
    df = df[df["PER"] > 0]
    df = df[df["PBR"] > 0]
    df = df[df["ROE"] > 0]
    df = df[df["시가총액"] > 0]

    base_date = datetime.now().strftime("%Y-%m-%d")
    return base_date, df


if __name__ == "__main__":
    d, df = load_us_data("SP500", limit=30)
    print(d)
    print(df.head())