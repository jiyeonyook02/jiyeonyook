# server.py
from flask import Flask, jsonify, send_file, request
from fetch_us import load_us_data
import pandas as pd
from io import BytesIO
from datetime import datetime, timedelta
import os

app = Flask(__name__)

# ===== 간단 캐시 (6시간) =====
CACHE_TTL = timedelta(hours=6)
_cache = {"ts": None, "universe": None, "base_date": None, "df": None}

def get_cached_us_data(universe="SP500"):
    global _cache
    now = datetime.now()

    if (
        _cache["ts"] is not None and
        _cache["universe"] == universe and
        now - _cache["ts"] < CACHE_TTL
    ):
        return _cache["base_date"], _cache["df"]

    base_date, df = load_us_data(universe)
    _cache = {"ts": now, "universe": universe, "base_date": base_date, "df": df}
    return base_date, df


def apply_base_filter(df: pd.DataFrame, choice: str):
    """너 원래 1/2/3 투자성향 필터"""
    dfw = df.copy()

    if choice == "1":
        PER_max, PBR_max, ROE_min, DY_min = 12, 1.2, 5, 3.0
        CAP_min = dfw["시가총액"].quantile(0.80)
        sort_key = "배당수익률"
        rule_label = "PER<12, PBR<1.2, ROE>5, DY>3, 시총 상위20%"
    elif choice == "3":
        PER_max, PBR_max, ROE_min, DY_min = 50, 5, 15, 0
        CAP_min = 0
        sort_key = "ROE"
        rule_label = "PER<50, PBR<5, ROE>15"
    else:
        PER_max, PBR_max, ROE_min, DY_min = 20, 2, 10, 1.5
        CAP_min = dfw["시가총액"].quantile(0.50)
        sort_key = "ROE"
        rule_label = "PER<20, PBR<2, ROE>10, DY>1.5, 시총 상위50%"

    cond = (
        (dfw["PER"] < PER_max) &
        (dfw["PBR"] < PBR_max) &
        (dfw["ROE"] > ROE_min) &
        (dfw["배당수익률"] > DY_min) &
        (dfw["시가총액"] > CAP_min)
    )

    out = dfw[cond].copy()
    if sort_key in out.columns:
        out = out.sort_values(by=sort_key, ascending=False)

    return out, rule_label


def apply_extra_filters(df: pd.DataFrame, use_mom: bool, use_fscore: bool, use_ev: bool):
    dfw = df.copy()

    extra_labels = []

    # 1) 모멘텀 하드컷: 상위 30%만
    if use_mom and "모멘텀12M_ex1M(%)" in dfw.columns:
        thr = dfw["모멘텀12M_ex1M(%)"].quantile(0.70)
        dfw = dfw[dfw["모멘텀12M_ex1M(%)"] >= thr]
        extra_labels.append("모멘텀 Top30%")

    # 2) 미니 F-score 컷: 3점 이상
    if use_fscore and "미니Fscore(0-5)" in dfw.columns:
        dfw = dfw[dfw["미니Fscore(0-5)"] >= 3]
        extra_labels.append("미니Fscore≥3")

    # 3) EV/EBITDA 밸류: 하위 40%만
    if use_ev and "EV/EBITDA" in dfw.columns:
        thr = dfw["EV/EBITDA"].quantile(0.40)
        dfw = dfw[dfw["EV/EBITDA"] <= thr]
        extra_labels.append("EV/EBITDA Low40%")

    return dfw, extra_labels


@app.route("/api/stocks")
def api_stocks():
    try:
        universe = request.args.get("universe", "SP500")
        base_date, df = get_cached_us_data(universe)
        return jsonify({
            "baseDate": base_date,
            "columns": df.columns.tolist(),
            "data": df.to_dict(orient="records")
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/download.xlsx")
def download_excel():
    try:
        choice = request.args.get("choice", "2")
        universe = request.args.get("universe", "SP500")

        use_mom = request.args.get("mom", "1") == "1"
        use_fscore = request.args.get("fscore", "1") == "1"
        use_ev = request.args.get("ev", "1") == "1"

        base_date, df = get_cached_us_data(universe)

        filtered, base_label = apply_base_filter(df, choice)
        filtered, extra_labels = apply_extra_filters(filtered, use_mom, use_fscore, use_ev)

        output = BytesIO()
        filtered.to_excel(output, index=False, engine="openpyxl")
        output.seek(0)

        filename = f"{universe}_{base_date}_choice{choice}.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/")
def index():
    here = os.path.dirname(os.path.abspath(__file__))
    return send_file(os.path.join(here, "index.html"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)