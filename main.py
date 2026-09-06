"""
NEIS(나이스) 급식 정보 API를 활용한 급식 분석 스트림릿 앱

기능
1. 알레르기 유발 식품 분석 (요리별 알레르기 코드 파싱 및 통계)
2. 급식 메뉴 검색 -> 연관 검색어 형태로 해당 메뉴가 나온 날짜/학교 표시
3. 계절별 평균 칼로리 비교
4. 여러 학교 간 급식(칼로리/알레르기) 비교

실행 방법
    pip install -r requirements.txt
    streamlit run main.py

주의
- NEIS Open API 인증키가 필요합니다. (https://open.neis.go.kr 에서 발급)
  사이드바에 인증키를 입력하지 않으면 학교별 5건(샘플)만 조회됩니다.
"""

import re
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

# --------------------------------------------------------------------------------
# 기본 설정값
# --------------------------------------------------------------------------------

API_URL = "https://open.neis.go.kr/hub/mealServiceDietInfo"

# 문제에서 제시된 학교 (모두 서울특별시교육청 소속 -> 코드 B10)
ATPT_OFCDC_SC_CODE = "B10"

SCHOOLS = {
    "당곡고등학교": "7010073",
    "미림여자고등학교": "7010167",
    "성보고등학교": "7010197",
    "용산고등학교": "7010103",
}

MEAL_CODE_MAP = {"1": "조식", "2": "중식", "3": "석식"}

# 식품 알레르기 유발물질 표시 대상 (교육부 고시 기준, 1~19번)
ALLERGY_MAP = {
    "1": "난류", "2": "우유", "3": "메밀", "4": "땅콩", "5": "대두",
    "6": "밀", "7": "고등어", "8": "게", "9": "새우", "10": "돼지고기",
    "11": "복숭아", "12": "토마토", "13": "아황산류", "14": "호두",
    "15": "닭고기", "16": "쇠고기", "17": "오징어",
    "18": "조개류(굴,전복,홍합 포함)", "19": "잣",
}

SEASON_MONTHS = {
    "봄": (3, 4, 5),
    "여름": (6, 7, 8),
    "가을": (9, 10, 11),
    "겨울": (12, 1, 2),
}


def month_to_season(month: int) -> str:
    for season, months in SEASON_MONTHS.items():
        if month in months:
            return season
    return "알수없음"


# --------------------------------------------------------------------------------
# NEIS API 호출 & 파싱
# --------------------------------------------------------------------------------

@st.cache_data(show_spinner=False, ttl=3600)
def fetch_meal_rows(api_key: str, school_code: str, from_ymd: str, to_ymd: str):
    """지정 기간 동안의 급식 데이터를 모두 가져온다 (페이지네이션 처리)."""
    rows = []
    p_index = 1
    p_size = 100 if api_key else 5  # 인증키 없으면 샘플 5건 고정

    while True:
        params = {
            "KEY": api_key,
            "Type": "json",
            "pIndex": p_index,
            "pSize": p_size,
            "ATPT_OFCDC_SC_CODE": ATPT_OFCDC_SC_CODE,
            "SD_SCHUL_CODE": school_code,
            "MLSV_FROM_YMD": from_ymd,
            "MLSV_TO_YMD": to_ymd,
        }
        try:
            resp = requests.get(API_URL, params=params, timeout=15)
            data = resp.json()
        except Exception as e:  # 네트워크 오류 등
            st.warning(f"API 호출 중 오류가 발생했습니다: {e}")
            break

        root = data.get("mealServiceDietInfo")
        if not root:
            # 데이터 없음 / 인증키 오류 등
            break

        row_block = None
        for part in root:
            if isinstance(part, dict) and "row" in part:
                row_block = part["row"]
        if not row_block:
            break

        rows.extend(row_block)

        if not api_key or len(row_block) < p_size:
            # 샘플키는 1페이지 고정, 정식키는 마지막 페이지면 종료
            break
        p_index += 1

    return rows


def parse_dishes(ddish_nm: str):
    """DDISH_NM 문자열을 (요리명, [알레르기코드...]) 리스트로 변환"""
    if not ddish_nm:
        return []
    items = ddish_nm.split("<br/>")
    parsed = []
    for item in items:
        item = item.strip()
        if not item:
            continue
        m = re.match(r"^(.*?)\s*\(([\d.\s]+)\)\s*$", item)
        if m:
            name = m.group(1).strip()
            codes = [c for c in m.group(2).split(".") if c.strip().isdigit()]
        else:
            name = item
            codes = []
        parsed.append((name, codes))
    return parsed


def parse_calorie(cal_info: str):
    if not cal_info:
        return None
    m = re.search(r"([\d.]+)", cal_info)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def build_dataframe(rows, school_name: str) -> pd.DataFrame:
    records = []
    for r in rows:
        ymd = r.get("MLSV_YMD", "")
        meal_code = r.get("MMEAL_SC_CODE", "")
        meal_name = MEAL_CODE_MAP.get(meal_code, r.get("MMEAL_SC_NM", meal_code))
        dishes = parse_dishes(r.get("DDISH_NM", ""))
        cal = parse_calorie(r.get("CAL_INFO", ""))
        try:
            dt = pd.to_datetime(ymd, format="%Y%m%d")
        except Exception:
            dt = pd.NaT
        records.append(
            {
                "학교": school_name,
                "날짜": dt,
                "급식일자": ymd,
                "식사구분": meal_name,
                "요리목록": dishes,
                "요리원문": r.get("DDISH_NM", ""),
                "칼로리": cal,
                "칼로리원문": r.get("CAL_INFO", ""),
                "원산지정보": r.get("ORPLC_INFO", ""),
                "영양정보": r.get("NTR_INFO", ""),
            }
        )
    df = pd.DataFrame(records)
    if not df.empty:
        df["월"] = df["날짜"].dt.month
        df["계절"] = df["월"].apply(lambda m: month_to_season(int(m)) if pd.notna(m) else None)
        df["연도"] = df["날짜"].dt.year
    return df


def explode_allergy(df: pd.DataFrame) -> pd.DataFrame:
    """학교/날짜/요리명/알레르기코드 단위로 펼친 데이터프레임 생성"""
    recs = []
    for _, row in df.iterrows():
        for name, codes in row["요리목록"]:
            if not codes:
                continue
            for c in codes:
                recs.append(
                    {
                        "학교": row["학교"],
                        "날짜": row["날짜"],
                        "식사구분": row["식사구분"],
                        "요리명": name,
                        "알레르기코드": c,
                        "알레르기명": ALLERGY_MAP.get(c, f"코드{c}"),
                    }
                )
    return pd.DataFrame(recs)


# --------------------------------------------------------------------------------
# Streamlit UI
# --------------------------------------------------------------------------------

st.set_page_config(page_title="급식 알레르기 · 칼로리 분석", layout="wide")
st.title("🍱 급식 알레르기 · 칼로리 분석 대시보드")
st.caption("나이스(NEIS) 급식식단정보 Open API 기반")

with st.sidebar:
    st.header("조회 조건")
    api_key = st.text_input(
        "NEIS 인증키 (Open API KEY)",
        type="password",
        help="비워두면 샘플키로 동작하며, 학교당 5건만 조회됩니다.",
    )

    selected_schools = st.multiselect(
        "학교 선택",
        options=list(SCHOOLS.keys()),
        default=list(SCHOOLS.keys()),
    )

    today = date.today()
    default_from = today - timedelta(days=180)
    date_range = st.date_input(
        "조회 기간",
        value=(default_from, today),
        help="계절 비교를 위해서는 최소 1년 이상 기간을 권장합니다.",
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        from_date, to_date = date_range
    else:
        from_date, to_date = default_from, today

    fetch_btn = st.button("📥 급식 데이터 불러오기", type="primary", use_container_width=True)

if "meal_df" not in st.session_state:
    st.session_state["meal_df"] = pd.DataFrame()

if fetch_btn:
    if not selected_schools:
        st.warning("학교를 하나 이상 선택해주세요.")
    else:
        from_ymd = from_date.strftime("%Y%m%d")
        to_ymd = to_date.strftime("%Y%m%d")
        all_dfs = []
        progress = st.progress(0.0, text="급식 데이터를 불러오는 중...")
        for i, school in enumerate(selected_schools):
            code = SCHOOLS[school]
            rows = fetch_meal_rows(api_key, code, from_ymd, to_ymd)
            all_dfs.append(build_dataframe(rows, school))
            progress.progress((i + 1) / len(selected_schools), text=f"{school} 완료")
        progress.empty()
        combined = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
        st.session_state["meal_df"] = combined
        if combined.empty:
            st.error(
                "데이터를 가져오지 못했습니다. 인증키, 학교 선택, 조회 기간을 확인해주세요."
            )
        else:
            st.success(f"총 {len(combined):,}건의 급식 데이터를 불러왔습니다.")

df = st.session_state["meal_df"]

if df.empty:
    st.info("왼쪽 사이드바에서 조회 조건을 설정한 뒤 '급식 데이터 불러오기'를 눌러주세요.")
    st.stop()

tab_overview, tab_allergy, tab_search, tab_season, tab_compare = st.tabs(
    ["개요", "🥜 알레르기 분석", "🔍 메뉴 검색", "🍂 계절별 칼로리", "🏫 학교 간 비교"]
)

# --------------------------------------------------------------------------------
# 개요
# --------------------------------------------------------------------------------
with tab_overview:
    c1, c2, c3 = st.columns(3)
    c1.metric("총 급식 건수", f"{len(df):,}")
    c2.metric("학교 수", df["학교"].nunique())
    valid_dates = df["날짜"].dropna()
    if not valid_dates.empty:
        c3.metric("조회 기간", f"{valid_dates.min().date()} ~ {valid_dates.max().date()}")

    st.dataframe(
        df[["학교", "급식일자", "식사구분", "요리원문", "칼로리원문"]].sort_values(
            "급식일자", ascending=False
        ),
        use_container_width=True,
        height=400,
    )

# --------------------------------------------------------------------------------
# 알레르기 분석
# --------------------------------------------------------------------------------
with tab_allergy:
    st.subheader("알레르기 유발 식품 분석")
    allergy_df = explode_allergy(df)

    if allergy_df.empty:
        st.info("선택한 기간/학교 데이터에서 알레르기 표시 정보를 찾을 수 없습니다.")
    else:
        school_filter = st.multiselect(
            "학교 필터", options=sorted(allergy_df["학교"].unique()),
            default=sorted(allergy_df["학교"].unique()), key="allergy_school_filter",
        )
        f_allergy = allergy_df[allergy_df["학교"].isin(school_filter)]

        colA, colB = st.columns([1, 1])
        with colA:
            freq = (
                f_allergy.groupby("알레르기명")["요리명"]
                .count()
                .reset_index(name="빈도")
                .sort_values("빈도", ascending=False)
            )
            fig = px.bar(
                freq, x="알레르기명", y="빈도",
                title="알레르기 유발 식품 언급 빈도 (전체)",
                text="빈도",
            )
            st.plotly_chart(fig, use_container_width=True)

        with colB:
            freq_school = (
                f_allergy.groupby(["학교", "알레르기명"])["요리명"]
                .count()
                .reset_index(name="빈도")
            )
            fig2 = px.bar(
                freq_school, x="알레르기명", y="빈도", color="학교",
                barmode="group", title="학교별 알레르기 유발 식품 빈도",
            )
            st.plotly_chart(fig2, use_container_width=True)

        st.markdown("#### 특정 알레르기 성분이 포함된 메뉴 찾기")
        target = st.selectbox(
            "알레르기 성분 선택",
            options=sorted(ALLERGY_MAP.values()),
        )
        matched = f_allergy[f_allergy["알레르기명"] == target].sort_values(
            "날짜", ascending=False
        )
        st.write(f"'{target}' 관련 메뉴 {len(matched)}건")
        st.dataframe(
            matched[["학교", "날짜", "식사구분", "요리명"]],
            use_container_width=True,
            height=300,
        )

# --------------------------------------------------------------------------------
# 메뉴 검색 (연관 검색어처럼: 언제 나왔는지)
# --------------------------------------------------------------------------------
with tab_search:
    st.subheader("급식 메뉴 검색")
    keyword = st.text_input("메뉴 이름을 입력하세요 (예: 떡볶이, 돈까스, 김치찌개)")

    if keyword:
        rows_hit = []
        for _, row in df.iterrows():
            for name, codes in row["요리목록"]:
                if keyword.strip() in name:
                    rows_hit.append(
                        {
                            "학교": row["학교"],
                            "날짜": row["날짜"],
                            "요일": row["날짜"].strftime("%A") if pd.notna(row["날짜"]) else "",
                            "식사구분": row["식사구분"],
                            "요리명": name,
                            "알레르기": ", ".join(
                                ALLERGY_MAP.get(c, c) for c in codes
                            ) if codes else "-",
                        }
                    )
        hit_df = pd.DataFrame(rows_hit)

        if hit_df.empty:
            st.warning(f"'{keyword}'(이)가 포함된 메뉴를 찾을 수 없습니다.")
        else:
            hit_df = hit_df.sort_values("날짜", ascending=False)
            st.success(f"'{keyword}' 관련 메뉴가 총 {len(hit_df)}건 나왔습니다.")

            # 연관 검색어 형태: 실제로 등장한 요리명들과 등장 횟수
            related = (
                hit_df["요리명"].value_counts().reset_index()
            )
            related.columns = ["요리명", "등장횟수"]
            st.markdown("**연관 검색어 (실제 등장한 유사 메뉴명)**")
            st.dataframe(related, use_container_width=True, height=200)

            st.markdown("**언제 나왔는지 (급식 이력)**")
            st.dataframe(
                hit_df[["학교", "날짜", "요일", "식사구분", "요리명", "알레르기"]],
                use_container_width=True,
                height=350,
            )

            # 월별 등장 빈도로 "언제 자주 나오는지" 시각화
            hit_df["연월"] = hit_df["날짜"].dt.to_period("M").astype(str)
            monthly = hit_df.groupby("연월").size().reset_index(name="횟수")
            fig = px.bar(monthly, x="연월", y="횟수", title=f"'{keyword}' 월별 등장 빈도")
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("검색할 메뉴 이름을 입력해주세요.")

# --------------------------------------------------------------------------------
# 계절별 칼로리 비교
# --------------------------------------------------------------------------------
with tab_season:
    st.subheader("계절별 평균 칼로리 비교")
    cal_df = df.dropna(subset=["칼로리", "계절"])

    if cal_df.empty:
        st.info("칼로리 정보가 있는 데이터가 없습니다.")
    else:
        season_order = ["봄", "여름", "가을", "겨울"]
        season_avg = (
            cal_df.groupby("계절")["칼로리"].mean().reindex(season_order).reset_index()
        )
        fig = px.bar(
            season_avg, x="계절", y="칼로리",
            title="계절별 평균 칼로리 (전체 학교 평균)",
            text_auto=".1f",
        )
        st.plotly_chart(fig, use_container_width=True)

        season_school_avg = (
            cal_df.groupby(["계절", "학교"])["칼로리"].mean().reset_index()
        )
        fig2 = px.bar(
            season_school_avg, x="계절", y="칼로리", color="학교",
            barmode="group", title="학교별 · 계절별 평균 칼로리",
            category_orders={"계절": season_order},
        )
        st.plotly_chart(fig2, use_container_width=True)

        with st.expander("계절별 상세 통계 (건수/평균/최고/최저)"):
            stat = cal_df.groupby("계절")["칼로리"].agg(
                건수="count", 평균="mean", 최고="max", 최저="min"
            ).reindex(season_order)
            st.dataframe(stat, use_container_width=True)

# --------------------------------------------------------------------------------
# 학교 간 비교
# --------------------------------------------------------------------------------
with tab_compare:
    st.subheader("학교 간 급식 비교")

    cal_df = df.dropna(subset=["칼로리"])
    if not cal_df.empty:
        st.markdown("#### 시기별 평균 칼로리 추이")
        trend = (
            cal_df.groupby([cal_df["날짜"].dt.to_period("M").astype(str), "학교"])["칼로리"]
            .mean()
            .reset_index()
        )
        trend.columns = ["연월", "학교", "평균칼로리"]
        fig = px.line(
            trend, x="연월", y="평균칼로리", color="학교", markers=True,
            title="학교별 월간 평균 칼로리 추이",
        )
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### 학교별 전체 평균 칼로리")
        overall = cal_df.groupby("학교")["칼로리"].mean().reset_index()
        fig2 = px.bar(overall, x="학교", y="칼로리", text_auto=".1f", title="학교별 평균 칼로리")
        st.plotly_chart(fig2, use_container_width=True)

    st.markdown("#### 학교별 알레르기 유발 식품 비중")
    allergy_df = explode_allergy(df)
    if not allergy_df.empty:
        pie_src = allergy_df.groupby(["학교", "알레르기명"]).size().reset_index(name="빈도")
        fig3 = px.bar(
            pie_src, x="학교", y="빈도", color="알레르기명",
            title="학교별 알레르기 유발 식품 누적 빈도", barmode="stack",
        )
        st.plotly_chart(fig3, use_container_width=True)
    else:
        st.info("알레르기 정보가 있는 데이터가 없습니다.")

    st.markdown("#### 학교별 급식 건수")
    count_df = df.groupby("학교").size().reset_index(name="급식건수")
    st.dataframe(count_df, use_container_width=True)
