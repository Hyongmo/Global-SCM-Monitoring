#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
regen_weekly.py
================
scenario_results.json 을 LLM 재호출 없이 그대로 읽어
docs/weekly_report.html 과 docs/pdf/KMI_Global_SC_AI_Weekly_Report(YYYY.MM.DD).pdf 를 재생성한다.

용도
----
리뷰어 피드백을 받아 scenario_results.json 을 사용자가 직접 수정한 뒤,
LLM(Claude API)을 다시 호출하지 않고 산출물(HTML/PDF)만 결정론적으로
재생성할 때 사용한다. (regen_reports.py 가 일일 리포트에 대해 하는 것과
동일한 역할을 주간 리포트에 대해 수행)

원본 로직 출처 (그대로 이식, 최소 수정)
--------------------------------------
scenario_generator_v11.ipynb
  - HTML: Part 5 (Cell 14) — weekly_report.html 생성 로직
          출력 경로만 그대로 두고(프로젝트 루트 weekly_report.html, .gitignore 대상 —
          git log 상 "chore: 루트 weekly_report.html 제거 + .gitignore 추가" 커밋으로 확인),
          생성 후 docs/weekly_report.html 로 복사한다(발행 대상은 docs/ 하위 파일).
  - PDF : Part 6 (Cell 16) — fpdf2 기반 PDF 생성 로직 (WeeklyReportPDF 클래스,
          generate_pdf() 등)을 그대로 이식. 파일명 규칙만 요구사항에 맞게
          'KMI_Global_SC_AI_Weekly_Report(YYYY.MM.DD).pdf' 대신
          'weekly_report_W{{nn}}_{{YYYYMMDD}}.pdf' 로 변경했고, 원본의
          GENERATE_LAST_N/GENERATE_FROM/SKIP_EXISTING 다중 주차 순회 필터는
          사용하지 않는다 — 이 스크립트는 WEEK_TAG로 지정된 단 하나의 주만
          무조건 재생성(덮어쓰기)한다.

확인 사항
---------
scripts/weekly_pipeline.py 에는 HTML/PDF 생성 코드가 없음(Part 1, Steps 1-5까지만
포함되어 있고, 파일 끝에 "# === Part 2: Steps 6-9 will be appended below ==="
placeholder만 존재) — 코드를 직접 읽어 확인함. 따라서 weekly_pipeline.py 로부터
import할 대상이 없어, 노트북 Cell 14/16의 코드를 이 스크립트로 직접 이식했다.

사용법
------
    python scripts/regen_weekly.py <WEEK_TAG>

    WEEK_TAG: YYYYMMDD 8자리. scenario_results.json 각 항목의 "_phaseA_week_tag"
              필드와 우선 매칭하고, 없으면 "period"/"week_label"/"week" 안의
              YYYY-MM-DD 날짜를 YYYYMMDD로 변환해 매칭한다.

    예)
        python scripts/regen_weekly.py 20260816

주의
----
- scenario_results.json 은 읽기만 하고 수정하지 않는다.
- LLM(Claude API) 호출 없음 — anthropic 패키지에 의존하지 않는다.
- 프로젝트 루트에 weekly_report.html 이 생성되지만 이는 .gitignore 대상 빌드
  산출물이다. 실제 배포/추적 대상은 docs/weekly_report.html 이다.
"""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"
PDF_DIR = DOCS_DIR / "pdf"
RESULT_FILE = BASE_DIR / "scenario_results.json"


def parse_args():
    p = argparse.ArgumentParser(
        description="scenario_results.json -> weekly_report.html / PDF 재생성 (LLM 재호출 없음)"
    )
    p.add_argument(
        "week_tag",
        metavar="WEEK_TAG",
        help="대상 주차 (YYYYMMDD). scenario_results.json의 _phaseA_week_tag 또는 "
             "period/week_label 안의 날짜와 매칭한다.",
    )
    return p.parse_args()


def find_scenario(scenarios, week_tag):
    """WEEK_TAG(YYYYMMDD)에 해당하는 scenario dict를 scenario_results.json에서 찾는다."""
    for s in scenarios:
        if str(s.get("_phaseA_week_tag", "")) == week_tag:
            return s
    # fallback: period/week_label/week 안의 YYYY-MM-DD 날짜로 매칭
    for s in scenarios:
        period = s.get("period") or s.get("week_label") or s.get("week") or ""
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(period))
        if m and f"{m.group(1)}{m.group(2)}{m.group(3)}" == week_tag:
            return s
    return None


def _run_html_generation():
    """scenario_generator_v11.ipynb Part 5 (Cell 14) 이식 — weekly_report.html 생성.

    scenario_results.json 전체(모든 주)를 읽어 다주차 뷰어 HTML을 생성한다
    (원본 노트북과 동일하게, 특정 WEEK_TAG로 필터링하지 않음).
    출력 파일: BASE_DIR/weekly_report.html (원본과 동일한 상대경로 'weekly_report.html').
    """
    # ============================================================

    # Cell 11: HTML 뷰어 생성 (weekly_report.html) — v7: Part C 제거, 헤더 통일

    # ============================================================



    import json, os, re

    from pathlib import Path





    # ── indicator_sources.csv → verify_url 매핑 ──

    import csv as _csv

    _IND_URL_MAP = {}

    if os.path.exists('indicator_sources.csv'):

        with open('indicator_sources.csv', encoding='utf-8-sig') as _f:

            for _row in _csv.DictReader(_f):

                _ind_raw = _row.get('indicator', '')

                _kid = _ind_raw.split('(')[0].strip().replace(' ', '_').replace('/', '_')

                # CP_ 계열은 원래 키 유지: 'CP_Hormuz (호르무즈해협)' → 'CP_Hormuz'

                if _ind_raw.startswith('CP_'):

                    _kid = _ind_raw.split(' ')[0].strip()

                _url = _row.get('verify_url', '').strip()

                if _kid and _url:

                    _IND_URL_MAP[_kid] = _url

        print(f'지표 URL 매핑: {len(_IND_URL_MAP)}개 로드')

    else:

        print('⚠ indicator_sources.csv 없음 — 지표 링크 없이 진행')



    # ── 일일 보고서 기사 로딩 (주간 기사 인용 링크용) ──

    from datetime import datetime as _dt, timedelta as _td

    _DAILY_SOURCES = {}   # { 'YYYYMMDD': { cat: [articles] } }

    _MON_DIR = 'monitoring'

    if os.path.isdir(_MON_DIR):

        for _dname in sorted(os.listdir(_MON_DIR)):

            _jpath = os.path.join(_MON_DIR, _dname, f'daily_report_llm_{_dname}.json')

            if os.path.exists(_jpath):

                try:

                    with open(_jpath, encoding='utf-8') as _f:

                        _dj = json.load(_f)

                    _dsrc = _dj.get('sources', {})

                    if _dsrc:

                        _DAILY_SOURCES[_dname] = _dsrc

                except Exception:

                    pass

        print(f'일일 기사 로드: {len(_DAILY_SOURCES)}일분')

    else:

        print('⚠ monitoring/ 디렉토리 없음 — 주간 기사 링크 생략')





    # ── 로드 파일 (v7: 통합 JSON) ──

    RESULT_FILES = [

        ('scenario_results.json', '전체'),

    ]

    OUTPUT_HTML = 'weekly_report.html'



    _all = {}

    for _rfile, _label in RESULT_FILES:

        if os.path.exists(_rfile):

            with open(_rfile, encoding='utf-8') as f:

                _loaded = json.load(f)

            for s in _loaded:

                _key = s.get('week', s.get('week_label', s.get('period', '?')))

                _all[_key] = s

            print(f"로드: {_rfile} ({_label}) — {len(_loaded)}주")

        else:

            print(f"⚠ {_rfile} 없음 (건너뜀)")



    if not _all:

        print("⚠ 로드할 JSON 없음 — Cell 9 먼저 실행하세요.")

    else:

        scenarios = sorted(_all.values(), key=lambda x: x.get('week', x.get('week_label', x.get('period', ''))))

        print(f"→ 합산: {len(scenarios)}주 ({scenarios[0].get('week','?')} ~ {scenarios[-1].get('week','?')})")



        SEV_COLOR = {

            '심각': '#c0392b', '중요': '#e67e22',

            '보통': '#2980b9', '미약': '#7f8c8d', '?': '#bdc3c7', '-': '#bdc3c7'

        }

        DIR_ICON = {'네거티브': '▼', '포지티브': '▲', '혼합': '◆', '안정': '●', '?': '?'}

        TIER_BG  = {1: '#27ae60', 2: '#2980b9', 3: '#e67e22', 4: '#c0392b'}

        CHG_SYM  = {'↑': '🔺', '↓': '🔻', '☆': '⭐', '−': '—',

                    '↑↑': '🔺🔺', '↓↓': '🔻🔻'}

        GROUP_ORDER = [

            '글로벌 해운', '초크포인트', '공급망 스트레스', '에너지',

            '거시경제', '한국 해운', '한국 에너지', '한국 철강/소재',

            '한국 자동차', '한국 화학', '한국 반도체', '한국 에너지/식품',

            '한국 정유/화학', '한국 산업 ETF'

        ]



        def sev_badge(s):

            c = SEV_COLOR.get(s, '#bdc3c7')

            return f'<span style="background:{c};color:#fff;padding:2px 7px;border-radius:4px;font-size:.85em">{s}</span>'



        def chg_sym(s):

            return CHG_SYM.get(s, s or '—')



        def esc(s):

            return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')



        def clean_kg(text):

            """KG 내부 표기 → 사람이 읽기 좋은 형태로 변환"""

            if not text: return ''

            t = str(text)

            KG_NAME_MAP = {

                'CF_CrudeOil':'원유', 'CF_LNG':'LNG', 'CF_Naphtha':'나프타',

                'CF_Corn':'옥수수', 'CF_Wheat':'밀', 'CF_Coal':'석탄',

                'CF_EuroContainer':'유럽 컨테이너', 'CF_RareEarth':'희토류',

                'CF_Meat':'육류', 'CF_Petrochemicals':'석유화학',

                'KS_Energy':'에너지', 'KS_Material':'소재/화학',

                'KS_Manufacture':'제조업', 'KS_Shipping':'해운/물류',

                'KS_FoodAgri':'식품/농산물', 'KS_Construction':'건설/인프라',

                'KS_Finance':'금융', 'KS_Macro':'거시경제', 'KS_Consumer':'소비자',

                'CP_Hormuz':'호르무즈 해협', 'CP_Suez':'수에즈 운하',

                'CP_BabElMandeb':'바브엘만데브 해협', 'CP_Malacca':'말라카 해협',

                'CP_Panama':'파나마 운하', 'CP_Taiwan':'대만 해협',

                'CP_BlackSea':'흑해', 'CP_Korea':'한국 항구',

                'CP_Lombok':'롬복 해협', 'CP_Kaohsiung':'가오슝',

                'CP_RussiaFuelExport':'러시아 에너지 수출',

            }

            for kg_id, kor in KG_NAME_MAP.items():

                t = t.replace(kg_id, kor)

            # 한국어로 번역된 KG ID가 괄호 안에 남아 있으면 제거

            for _kor in KG_NAME_MAP.values():

                t = t.replace(f'({_kor})', '')

            t = re.sub(r'--\[.*?\]-->', '→', t)

            t = re.sub(r'^KG:\s*', '', t)

            t = re.sub(r'\bDOMESTIC_IMPACT\s*기사', '국내 기사', t)

            t = re.sub(r'\b(THREAT|DISRUPTION)[/_A-Z]*\s*기사', '기사', t)

            t = re.sub(r'\bDOMESTIC_IMPACT\b', '', t)

            t = re.sub(r'\bTHREAT/SOURCE\b', '', t)

            t = re.sub(r'\bDISRUPTION/LOGISTICS\b', '', t)

            t = re.sub(r'\b(SOURCE|ROUTE|CP|CHOKEPOINT|LOGISTICS)형?\s*교란\s*[:：]?', '', t)

            t = re.sub(r'\bSOURCE/ROUTE\s*교란\s*[:：]?', '', t)

            t = re.sub(r'기사\s*\([^)]*(?:THREAT|DISRUPTION|ROUTE|SOURCE|LOGISTICS)[^)]*\)', '기사', t)

            t = re.sub(r'\([^)]*\b(?:THREAT|DISRUPTION|SOURCE|ROUTE|LOGISTICS|CP|CHOKEPOINT)\b[^)]*\)', '', t)

            t = re.sub(r'^(?:SOURCE|ROUTE|THREAT|DISRUPTION|LOGISTICS)[/_\s]*', '', t)

            t = re.sub(r'@[A-Za-z_\s]+(?=;|$|\b)', '', t)

            t = re.sub(r'\(\s*\)', '', t)

            t = re.sub(r'\s*cascade\b[^;,]*', '', t)

            t = t.replace('CP별경유율', '초크포인트별 경유율')

            t = re.sub(r'\bKG\s+(?:등록|비구조|등록\s*초크포인트)[^;,\.]*', '', t)

            t = re.sub(r'\blagMin(?:Days)?=\d+[~\-]?\d*', '', t)

            t = re.sub(r'\blagMax(?:Days)?=\d+[~\-]?\d*', '', t)

            t = re.sub(r'\bev=[\d.]+', '', t)

            t = re.sub(r'\bKG\s*노드\s*:[^;\n]+', '', t)

            t = re.sub(r'\bKG\s*초크포인트[^;\n]*', '', t)

            t = re.sub(r'\bKG\s*엣지[^;\n]*', '', t)

            t = re.sub(r'\bCF_([A-Za-z가-힣0-9]+)', r'\1', t)

            t = re.sub(r'\bKS_([A-Za-z가-힣0-9/]+)', r'\1', t)

            t = re.sub(r'\bCP_([A-Za-z가-힣0-9]+)', r'\1', t)

            t = re.sub(r'[\s;]*\(?w=[\d.]+\)?', '', t)

            t = re.sub(r'[\s;]*\(?crisis=\d+%\)?', '', t)

            t = t.replace('CP경유율', '초크포인트 경유율')

            t = re.sub(r';\s*;', ';', t)

            t = re.sub(r',\s*,+', ',', t)

            t = re.sub(r'^[;\s]+|[;\s]+$', '', t)

            t = re.sub(r',\s*$', '', t)

            # 독립 영문 초크포인트명 → 한국어

            for _e, _k in [('Hormuz','호르무즈 해협'),('Malacca','말라카 해협'),('BabElMandeb','바브엘만데브 해협'),('Suez','수에즈 운하'),('Panama','파나마 운하')]:

                t = re.sub(r'\b' + _e + r'\b', _k, t)

            # 영문 Tier 레벨 → 한국어 (상황요약 본문에서 노출 방지)

            for _e, _k in [('Crisis','위기'),('Warning','경고'),('Caution','주의'),('Normal','정상')]:

                t = re.sub(r'\b' + _e + r'\b', _k, t)

            t = re.sub(r'\s+', ' ', t).strip()

            return esc(t)





        def _linkify_refs(text, ref_map):

            if not text or not ref_map:

                return text

            import re as _re_link

            def _repl(m):

                num = m.group(1)

                ref = ref_map.get(num, {})

                url = ref.get("url", "")

                title = ref.get("title", "").replace('"', '&quot;')[:120] if ref.get("title") else f"기사 {num}"

                if url:

                    return f'<a href="{url}" target="_blank" rel="noopener" class="ref-link" title="{title}"><sup>[{num}]</sup></a>'

                else:

                    return f'<span class="ref-link" title="{title}"><sup>[{num}]</sup></span>'

            return _re_link.sub(r'\[(\d+)\]', _repl, text)



        def clean_path(text):

            """전파경로 path/pathway 필드에서 내부 KG 표기 제거"""

            if not text: return ''

            t = str(text)

            KG_NAME_MAP = {

                'CF_CrudeOil':'원유', 'CF_LNG':'LNG', 'CF_Naphtha':'나프타',

                'CF_Corn':'옥수수', 'CF_Wheat':'밀', 'CF_Coal':'석탄',

                'CF_EuroContainer':'유럽 컨테이너', 'CF_RareEarth':'희토류',

                'CF_Meat':'육류', 'CF_Petrochemicals':'석유화학',

                'KS_Energy':'에너지', 'KS_Material':'소재/화학',

                'KS_Manufacture':'제조업', 'KS_Shipping':'해운/물류',

                'KS_FoodAgri':'식품/농산물', 'KS_Construction':'건설/인프라',

                'KS_Finance':'금융', 'KS_Macro':'거시경제', 'KS_Consumer':'소비자',

            }

            for kg_id, kor in KG_NAME_MAP.items():

                t = t.replace(kg_id, kor)

            t = re.sub(r'\(\d{2}\.\d{2}\s+SOURCE\s+교란\)', '', t)

            t = re.sub(r'\bSOURCE\s+교란', '', t)

            t = re.sub(r'\((?:SOURCE|ROUTE|THREAT|DISRUPTION|LOGISTICS|CHOKEPOINT|CP)\)', '', t)

            t = re.sub(r'\bCF_([A-Za-z가-힣0-9]+)', r'\1', t)

            t = re.sub(r'\bKS_([A-Za-z가-힣0-9/]+)', r'\1', t)

            t = re.sub(r'\bCP_([A-Za-z가-힣0-9]+)', r'\1', t)

            t = re.sub(r'[\s;]*\(?w=[\d.]+\)?', '', t)

            t = re.sub(r'[\s;]*\(?crisis=\d+%\)?', '', t)

            t = re.sub(r';\s*;', ';', t)

            t = re.sub(r'\s+', ' ', t).strip()

            return esc(t)



        def clean_node(text):

            """에너지(KS_Energy) → 에너지"""

            if not text: return ''

            return esc(re.sub(r'\([A-Za-z_][A-Za-z0-9_]*\)', '', str(text)).strip())

        def clean_sector(text):

            """Part B from/to 필드: KS_/CF_/CP_ 접두사와 괄호 처리 — 세 가지 패턴 모두 대응

            KS_Energy(에너지) → 에너지

            에너지(KS_Energy) → 에너지

            KS_Energy (bare) → KS_MAP 또는 접두사 제거

            """

            if not text: return ''

            t = str(text).strip()

            KS_MAP = {

                'KS_Energy': '에너지', 'KS_Material': '소재/화학',

                'KS_Manufacture': '제조업', 'KS_Shipping': '해운/물류',

                'KS_FoodAgri': '식품/농산물', 'KS_Construction': '건설/인프라',

                'KS_Finance': '금융', 'KS_Macro': '거시경제',

            }

            # Pattern 1: KOREAN(KG_CODE) → KOREAN  e.g. "에너지(KS_Energy)"

            m = re.match(r'^([^()]+)\([A-Za-z_][A-Za-z0-9_]*\)$', t)

            if m: return esc(m.group(1).strip())

            # Pattern 2: KG_CODE(KOREAN_DESC) → KOREAN_DESC  e.g. "KS_Energy(에너지 섹터)"

            m = re.match(r'^(?:KS_|CF_|CP_|KI_)[A-Za-z가-힣0-9]+\(([^)]+)\)$', t)

            if m: return esc(m.group(1).strip())

            # Pattern 3: Bare KG_CODE → KS_MAP 또는 접두사 제거

            if t in KS_MAP: return esc(KS_MAP[t])

            t = re.sub(r'\bKS_([A-Za-z가-힣0-9/]+)', r'\1', t)

            t = re.sub(r'\bCF_([A-Za-z가-힣0-9]+)', r'\1', t)

            t = re.sub(r'\bCP_([A-Za-z가-힣0-9]+)', r'\1', t)

            t = re.sub(r'\bKI_([A-Za-z가-힣0-9]+)', r'\1', t)

            return esc(t.strip())





        def fmt_val(v, unit):

            if v is None:

                return 'N/A'

            try:

                fv = float(v)

                if fv != fv:

                    return 'N/A'

                if unit in ('pt', 'USD/bbl', 'KRW/USD', 'USD'):

                    return f'{fv:,.1f}'

                elif unit == 'KRW':

                    return f'{fv:,.0f}'

                elif unit == '%':

                    return f'{fv:.2f}'

                elif unit == 'σ':

                    return f'{fv:.2f}'

                elif unit == '척':

                    return f'{int(fv):,}'

                else:

                    return f'{fv:,.2f}'

            except Exception:

                return str(v)



        def chg_color_for(cdir, ddir):

            """방향 반영 색상 반환"""

            if cdir == 'up':

                return '#c0392b' if ddir == 'up_bad' else ('#27ae60' if ddir == 'down_bad' else '#e67e22')

            elif cdir == 'down':

                return '#c0392b' if ddir == 'down_bad' else ('#27ae60' if ddir == 'up_bad' else '#2980b9')

            return '#7f8c8d'



        def chg_arrow(cdir):

            return {'up': '▲', 'down': '▼'}.get(cdir, '—')





        _CAT_KR = {

            '1_Security': '안보/지정학', '2_Energy': '에너지',

            '3_Freight': '해상운임', '4_PortCargo': '항만/물류',

            '5_EconFinance': '경제/금융', '6_Seafood': '수산',

            '7_Shipping': '해운', '8_Logistics': '물류',

            '9_PortCongestion': '항만혼잡', '10_OtherIndustry': '기타산업',

        }

        _WEEKDAY_KR = ['월','화','수','목','금','토','일']

        _MAX_PER_CAT = 10   # 카테고리당 최대 표시 기사 수



        def _get_week_sources(period_str):

            """주간 period 문자열에서 해당 주 일일 기사 수집"""

            m = re.search(r'(\d{4}-\d{2}-\d{2})', period_str)

            if not m:

                return {}

            mon_date = _dt.strptime(m.group(1), '%Y-%m-%d')

            agg = {}

            for i in range(7, 0, -1):

                d = (mon_date - _td(days=i)).strftime('%Y%m%d')

                if d in _DAILY_SOURCES:

                    agg[d] = _DAILY_SOURCES[d]

            return agg



        def _render_weekly_sources(agg):

            """주간 기사 접이식 HTML 생성"""

            if not agg:

                return ''

            total = sum(len(a) for ds in agg.values() for a in ds.values() if isinstance(a, list))

            html = f'<details class="weekly-sources"><summary class="ws-summary">📰 주간 참조 기사 ({total:,}건 · {len(agg)}일)</summary>'

            for dstr in sorted(agg.keys()):

                day_src = agg[dstr]

                day_n = sum(len(v) for v in day_src.values() if isinstance(v, list))

                try:

                    dd = _dt.strptime(dstr, '%Y%m%d')

                    dl = f"{dd.month}/{dd.day}({_WEEKDAY_KR[dd.weekday()]})"

                except Exception:

                    dl = dstr

                html += f'<details class="ws-day"><summary class="ws-day-sum">{dl} — {day_n:,}건</summary><div class="ws-day-body">'

                for ck in sorted(day_src.keys()):

                    arts = day_src[ck]

                    if not isinstance(arts, list) or not arts:

                        continue

                    cn = _CAT_KR.get(ck, ck)

                    intl = [a for a in arts if a.get('type') == 'intl']

                    dom  = [a for a in arts if a.get('type') == 'dom']

                    for tag, items in [('해외', intl), ('국내', dom)]:

                        if not items:

                            continue

                        html += f'<div class="ws-cat">{cn} · {tag} ({len(items)}건)</div><ul class="ws-arts">'

                        for a in items[:_MAX_PER_CAT]:

                            t = a.get('title','').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

                            u = a.get('url','')

                            html += f'<li><a href="{u}" target="_blank" rel="noopener">{t}</a></li>' if u else f'<li>{t}</li>'

                        if len(items) > _MAX_PER_CAT:

                            html += f'<li class="ws-more">… 외 {len(items)-_MAX_PER_CAT}건</li>'

                        html += '</ul>'

                html += '</div></details>'

            html += '</details>'

            return html





        # ── 지표 패널 (전체 그룹별) ──────────────────────────────

        def render_indicators(indicators):

            if not indicators:

                return ''

            groups = {}

            for kid, meta in indicators.items():

                g = meta.get('group', '기타')

                groups.setdefault(g, []).append((kid, meta))

            ordered = [g for g in GROUP_ORDER if g in groups]

            ordered += [g for g in groups if g not in ordered]

            html = ['<div class="ind-panel"><div class="ind-title">📈 주간 공급망 모니터링 지표 <span style="font-size:0.7em;color:#999;font-weight:normal">(지표명 클릭 시 최신 정보 확인)</span></div><div class="ind-groups">']

            for g in ordered:

                html.append(f'<div class="ind-group"><div class="ind-group-title">{esc(g)}</div><div class="ind-items">')

                for kid, meta in groups[g]:

                    name  = meta.get('name', kid)

                    full  = meta.get('full', name)

                    value = meta.get('value')

                    unit  = meta.get('unit', '')

                    chg   = meta.get('chg_pct')

                    cdir  = meta.get('chg_dir', 'flat')

                    ddir  = meta.get('dir', 'neutral')

                    cc    = chg_color_for(cdir, ddir)

                    arrow = chg_arrow(cdir)

                    val_str = fmt_val(value, unit)

                    chg_str = f'{chg:+.1f}%' if (chg is not None and chg == chg) else ''

                    unit_span = f'<span class="ind-unit">{esc(unit)}</span>' if unit else ''

                    # 날짜 라벨 생성

                    _freq = meta.get('freq', '')

                    _data_date = meta.get('data_date', '')

                    _date_label = ''

                    if _data_date and str(_data_date) not in ('', 'nan', 'None'):

                        try:

                            _parts = str(_data_date)[:10].split('-')  # YYYY-MM-DD

                            if _freq == 'monthly':

                                _date_label = f"{int(_parts[1])}월"

                            else:  # daily or weekly

                                _date_label = f"{int(_parts[1])}/{int(_parts[2])}"

                        except Exception:

                            pass

                    date_span = f'<span class="ind-date">{_date_label}</span>' if _date_label else ''

                    html.append(

                        f'<div class="ind-item" title="{esc(full)}">'

                        f'<div class="ind-name">{f"""<a href="{_IND_URL_MAP[kid]}" target="_blank" rel="noopener">{esc(name)}</a>""" if kid in _IND_URL_MAP else esc(name)}{date_span}</div>'

                        f'<div class="ind-val">{esc(val_str)}{unit_span}</div>'

                        f'<div class="ind-chg" style="color:{cc}">{arrow} {esc(chg_str)}</div>'

                        f'</div>'

                    )

                html.append('</div></div>')

            html.append('</div></div>')

            return '\n'.join(html)



        # ── 시나리오별 HTML 블록 ───────────────────────────────────



        # ── 지도 렌더링 함수 ──

        # 좌표 테이블 (지리 정보 — KG 노드 ID → [lat, lng])

        _GEO = {

            # 초크포인트

            'CP_Hormuz': [26.56,56.25], 'CP_Suez': [30.46,32.34],

            'CP_BabElMandeb': [12.58,43.33], 'CP_Malacca': [2.50,101.20],

            'CP_Panama': [9.08,-79.68], 'CP_Taiwan': [24.50,120.50],

            'CP_BlackSea': [41.20,29.10], 'CP_Lombok': [-8.40,115.70],

            # 해외 항구

            'FP_RasTanura': [26.64,50.15], 'FP_MinaAhmadi': [29.05,48.15],

            'FP_Fujairah': [25.12,56.33], 'FP_RasLaffan': [25.93,51.53],

            'FP_PortHedland': [-20.31,118.58], 'FP_Gladstone': [-23.85,151.26],

            'FP_Newcastle': [-32.93,151.78], 'FP_USGulf': [29.30,-94.80],

            'FP_Vancouver': [49.29,-123.11], 'FP_Bintulu': [3.17,113.04],

            'FP_Indonesian': [-6.10,106.88], 'FP_Kaohsiung': [22.61,120.27],

            'FP_Yokohama': [35.44,139.64], 'FP_Rotterdam': [51.91,4.48],

            'FP_Tubarao': [-20.28,-40.24], 'FP_Lianyungang': [34.74,119.45],

            'FP_Qingdao': [36.07,120.38], 'FP_Primorsk': [60.35,28.63],

            'FP_Novorossiysk': [44.72,37.79], 'FP_Vladivostok': [43.11,131.87],

            'FP_Sabetta': [71.28,72.05],

            # 한국 항구

            'KP_Ulsan': [35.50,129.38], 'KP_Yeosu': [34.74,127.76],

            'KP_Daesan': [36.99,126.35], 'KP_Pyeongtaek': [36.97,126.83],

            'KP_Incheon_LNG': [37.45,126.60], 'KP_Tongyeong': [34.83,128.43],

            'KP_Gwangyang': [34.93,127.70], 'KP_Pohang': [36.03,129.37],

            'KP_Incheon': [37.45,126.60], 'KP_Gunsan': [35.99,126.71],

            'KP_Busan': [35.10,129.03], 'KP_Boryeong': [36.33,126.59],

            'KP_Dangjin': [36.89,126.63],

        }



        # KG 로드 (항로·초크포인트·우회 정보)

        import os as _os

        _kg_path = _os.path.join(_os.path.dirname(OUTPUT_HTML) or '.', 'seed_kg_v4.json')

        if not _os.path.exists(_kg_path):

            _kg_path = 'seed_kg_v4.json'

        with open(_kg_path, encoding='utf-8') as _f:

            _KG = json.load(_f)

        print(f'KG loaded: {len(_KG["nodes"])} nodes, {len(_KG["edges"])} edges')



        _KG_NODES = _KG['nodes']

        _KG_EDGES = _KG['edges']

        _SHIPS_TO = [e for e in _KG_EDGES if e.get('relation') == 'shipsTo']

        _BYPASSES = [e for e in _KG_EDGES if e.get('relation') == 'bypasses']

        _BP_NODES = {nid: n for nid, n in _KG_NODES.items() if nid.startswith('BP_')}

        _CP_NAMES = {nid: n.get('name','') for nid, n in _KG_NODES.items() if nid.startswith('CP_')}

        # v11: CP 키워드 (한국어 이름 첫 단어) — path 텍스트 매칭용
        _CP_KEYWORDS = {}
        for _cpid, _cpnm in _CP_NAMES.items():
            _parts = _cpnm.split()
            if _parts:
                _CP_KEYWORDS[_cpid] = _parts[0]  # e.g. "호르무즈", "수에즈", "바브엘만데브"

        # v11: KG linkedTo 양방향 전파 — 같은 수로의 두 관문은 어느 쪽이 막혀도 전체 항로 차단
        # e.g. 수에즈↔바브엘만데브: 유럽-아시아 항로에서 둘 다 통과해야 하므로 양방향
        _CP_LINKED = {}
        for _cpid, _cpnode in _KG_NODES.items():
            if _cpid.startswith('CP_') and 'linkedTo' in _cpnode:
                _linked = _cpnode['linkedTo']
                _CP_LINKED.setdefault(_cpid, set()).add(_linked)
                _CP_LINKED.setdefault(_linked, set()).add(_cpid)

        # ── 주요 해상 구간별 중간 경유점 (실제 항로대 반영) ──

        # ── searoute 라이브러리 기반 실제 해상 항로 웨이포인트 ──

        _MALACCA_STRAIT_EXIT = [[2, 102], [1.1, 103.6], [1.34, 104.48]]

        _SCS_TO_KR = [[7.61, 107.32], [12.2, 110.1], [16.38, 111.81], [16.95, 112.05], [16.96, 112.05], [21.7, 114.1], [23, 117], [23.77, 118.1], [24.54, 118.94], [25.09, 119.76], [25.7, 120], [27.8, 121.3], [28.73, 122.21], [30.18, 122.88], [30.83, 122.68], [31.3, 122.9], [33.55, 126.54], [34.2, 127.6]]

        _MALACCA_TO_KR = _MALACCA_STRAIT_EXIT + _SCS_TO_KR

        _INDIAN_OCEAN = [[6.2, 85.95], [6.47, 90], [6.7, 94], [7, 97]]  # (레거시 호환)

        _SEA_SEGMENTS = {

            ('CP_Hormuz', 'CP_Malacca'): [[26.51, 56.55], [26.42, 56.76], [25.96, 56.93], [25.5, 57.1], [24, 59], [22.7, 60.4], [21.44, 62.38], [20.08, 64.5], [19.43, 64.95], [17.53, 66.26], [16.9, 66.69], [15.62, 67.57], [14.68, 68.8], [13.77, 70.0], [12.65, 71.46], [9.7, 75.3], [8, 77], [5.8, 80.1], [5.9, 81.9], [6.2, 85.95], [6.47, 90], [6.7, 94], [7, 97]],

            'Malacca_to_KP': _MALACCA_TO_KR,

            ('CP_Suez', 'CP_BabElMandeb'): [[29.7, 32.6], [27.9, 33.75], [27, 34.5], [23.6, 37], [20.75, 38.9], [16.3, 41.2], [15, 42]],

            ('CP_BabElMandeb', 'CP_Malacca'): [[12, 45], [12.58, 53.06], [11.43, 58.4], [10.87, 60.83], [10.03, 64.3], [9.86, 64.99], [8.88, 68.86], [8.67, 69.67], [8.37, 70.82], [7.8, 72.94], [6.81, 76.53], [5.9, 81.9], [6.47, 90], [7, 97]],

            ('FP_Rotterdam', 'CP_Suez'): [[52, 3.9], [50.99, 1.81], [50.5, 0.39], [49.95, -1.3], [49.62, -2.39], [48.8, -5.06], [43.92, -9.02], [40.78, -9.98], [36.32, -7.27], [36.16, -3.68], [36.47, -1.62], [37.49, 10.37], [37.21, 12.1], [35.85, 17.9], [33.75, 26.31], [32.32, 30.41]],

            ('FP_Tubarao', 'CP_Malacca'): [[-24.89, -34.61], [-27.34, -22.82], [-30, -10], [-32.14, 1.97], [-35, 18], [-34.37, 30.52], [-33.61, 39.47], [-32.99, 43.87], [-31.84, 50.0], [-30.26, 56.63], [-29.33, 59.85], [-25.78, 64.47], [-17.54, 72.59], [-12.54, 77.58], [-7.52, 82.53], [-2.51, 87.52], [-0.0, 90.0], [7, 97]],

            ('FP_PortHedland', 'CP_Lombok'): [[-17.85, 118.1], [-16.49, 117.71], [-16.44, 117.7]],

            ('CP_Lombok', 'KP'): [[-7.25, 116.2], [-5.4, 116.95], [-3.19, 118.45], [-2.15, 118.23], [1.1, 119.5], [1.1, 119.5], [3.81, 119.7], [4.08, 119.72], [4.75, 119.77], [8.4, 120.05], [14.4, 119.9], [17, 119.4], [19, 120], [22.1, 120], [23.01, 120], [23.91, 120], [24.36, 120], [24.81, 120], [25.7, 120], [27.8, 121.3], [28.73, 122.21], [30.18, 122.88], [30.83, 122.68], [31.3, 122.9], [33.55, 126.54], [34.2, 127.6]],

            ('FP_Gladstone', 'KP'): [[-18.49, 153.02], [-16.23, 152.88], [-13.62, 152.72], [-11.5, 152.59], [-10, 152.5], [-7, 149], [-4.8, 147], [-0.0, 144.73], [3.24, 143.2], [10, 140], [15.0, 140.0], [17.66, 140.0], [17.71, 140.0], [20, 140], [21.63, 138.64], [22.61, 137.82], [25.45, 135.44], [25.59, 135.33], [26.91, 134.25], [27.95, 133.4], [31, 130.9], [32.4, 132.4], [33.75, 131.9], [33.8, 131.21], [33.94, 131.07], [33.99, 130.69], [34.3, 130.1]],

            ('FP_Newcastle', 'KP'): [[-27.15, 153.8], [-24.8, 153.4], [-16.23, 152.88], [-11.5, 152.59], [-7, 149], [-0.0, 144.73], [10, 140], [17.66, 140.0], [20, 140], [22.61, 137.82], [25.59, 135.33], [27.95, 133.4], [32.4, 132.4], [33.8, 131.21], [33.99, 130.69]],

            ('FP_Indonesian', 'KP'): [[-5.2, 106.8], [-1.5, 107.3], [3.7, 109.8], [7.55, 109.93], [9.8, 111.91], [13.36, 115.04], [16.91, 118.16], [19, 120], [22.1, 120], [23.01, 120], [23.91, 120], [24.36, 120], [24.81, 120], [25.7, 120], [27.8, 121.3], [28.73, 122.21], [30.18, 122.88], [30.83, 122.68], [31.3, 122.9], [33.55, 126.54], [34.2, 127.6]],

            ('FP_Bintulu', 'KP'): [[3.7, 109.8], [7.55, 109.93], [9.8, 111.91], [13.36, 115.04], [16.91, 118.16], [19, 120], [22.1, 120], [23.01, 120], [23.91, 120], [24.36, 120], [24.81, 120], [25.7, 120], [27.8, 121.3], [28.73, 122.21], [30.18, 122.88], [30.83, 122.68], [31.3, 122.9], [33.55, 126.54], [34.2, 127.6]],

            ('CP_Taiwan', 'KP'): [[22.88, 120.06], [23.01, 120], [23.91, 120], [24.36, 120], [24.81, 120], [25.7, 120], [27.8, 121.3], [28.73, 122.21], [30.18, 122.88], [30.83, 122.68], [31.3, 122.9], [33.55, 126.54], [34.2, 127.6]],

            ('FP_Lianyungang', 'KP'): [[35.9, 121], [35.17, 123.25], [34.4, 125.6], [34.2, 127.6]],

            ('FP_Qingdao', 'KP'): [[35.17, 123.25], [34.4, 125.6], [34.2, 127.6]],

            ('FP_Yokohama', 'KP'): [[34.66, 139.19], [34.41, 138.79], [34.45, 138.6], [34.49, 138.42], [34.44, 138.14], [34.39, 137.86], [34.33, 137.2], [33.85, 136.6], [33.2, 135.1], [34.1, 134.86], [34.39, 134.39], [34.42, 134.01], [34.33, 133.63], [34.07, 133.09], [34.15, 132.96], [34.1, 132.66], [33.82, 132.46], [33.75, 131.9], [33.8, 131.21], [33.94, 131.07], [33.99, 130.69], [34.3, 130.1]],

            ('FP_Vancouver', 'KP'): [[48.66, -122.73], [48.41, -122.79], [48.21, -123.11], [48.16, -123.43], [48.49, -124.73], [50, -135.06], [50.7, -144.05], [51.5, -152.84], [51.83, -160.09], [51.59, -165.17], [51.1, -168.21], [50, -180], [50.48, -187.65], [48.48, -198.56], [47.44, -202.07], [43.2, -214], [41.36, -219.65]],

            ('FP_USGulf', 'KP'): [[25.38, -87.73], [21.79, -86.07], [9.21, -79.9], [8.99, -79.59], [10.04, -88.0], [15.63, -99.91], [24.93, -113.45], [32.73, -124.58], [35.61, -130.0], [40.3, -140.28], [44.96, -154.65], [47.06, -167.34], [50.69, -185.86], [48.11, -199.81], [41.68, -218.72]],

            ('FP_Fujairah', 'CP_Malacca'): [[25.5, 57.1], [24, 59], [22.7, 60.4], [21.44, 62.38], [20.08, 64.5], [19.43, 64.95], [17.53, 66.26], [16.9, 66.69], [15.62, 67.57], [14.68, 68.8], [13.77, 70.0], [12.65, 71.46], [9.7, 75.3], [8, 77], [5.8, 80.1], [5.9, 81.9], [6.2, 85.95], [6.47, 90], [6.7, 94], [7, 97]],

        }



        # ── searoute 사전 캐시 로드 (searoute_cache.json) ──
        _SR_FILE_CACHE = {}
        for _try_dir in ['.', _os.path.dirname(_os.path.abspath(__file__)) if '__file__' in dir() else '.', _os.getcwd()]:
            _sr_path = _os.path.join(_try_dir, 'searoute_cache.json')
            if _os.path.exists(_sr_path):
                try:
                    with open(_sr_path, 'r') as _f:
                        _SR_FILE_CACHE = json.load(_f)
                    print(f'  searoute cache: {len(_SR_FILE_CACHE)} routes from {_sr_path}')
                except Exception:
                    pass
                break

        # ── 지도 항로 폴백 경고 (재발 조기 발견용) ──
        #    경유점을 못 찾으면 지도는 '조용히' 직선을 그린다. 그 순간을 로그에 남긴다.
        _MAP_WARNS = set()
        def _map_warn(msg):
            if msg in _MAP_WARNS:
                return
            _MAP_WARNS.add(msg)
            print(f"  \u26a0 [MAP] {msg}")
            if os.environ.get('GITHUB_ACTIONS') == 'true':
                print(f"::warning title=\uc9c0\ub3c4 \ud56d\ub85c \ud3f4\ubc31::{msg}")

        if not _SR_FILE_CACHE:
            _map_warn("searoute_cache.json \uc744 \ucc3e\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4 \u2014 "
                      "\uc6b0\ud68c \ud56d\ub85c\uac00 \uc9c1\uc120\uc73c\ub85c \uadf8\ub824\uc9c8 \uc218 \uc788\uc2b5\ub2c8\ub2e4")

        _SR_CACHE = {}  # 동적 호출 캐시

        def _searoute_waypoints_by_id(from_id, to_id):
            """사전 캐시 → searoute 동적 호출 → 빈 리스트 순으로 해상 항로 반환"""
            # 1) 파일 캐시에서 조회
            cache_key = f"{from_id}|{to_id}"
            if cache_key in _SR_FILE_CACHE:
                return _SR_FILE_CACHE[cache_key]
            # 2) searoute 동적 호출
            if from_id not in _GEO or to_id not in _GEO:
                return []
            coord_from, coord_to = _GEO[from_id], _GEO[to_id]
            rt_key = (round(coord_from[0],1), round(coord_from[1],1),
                      round(coord_to[0],1), round(coord_to[1],1))
            if rt_key in _SR_CACHE:
                return _SR_CACHE[rt_key]
            try:
                import searoute as sr
                origin = [coord_from[1], coord_from[0]]
                dest = [coord_to[1], coord_to[0]]
                route = sr.searoute(origin, dest)
                coords = route['geometry']['coordinates']
                mid = coords[1:-1]
                if len(mid) > 20:
                    step = max(1, len(mid) // 20)
                    mid = mid[::step]
                    if coords[-2] not in mid:
                        mid.append(coords[-2])
                leaflet = [[round(c[1], 2), round(c[0], 2)] for c in mid]
                _SR_CACHE[rt_key] = leaflet
                return leaflet
            except Exception as _e:
                _map_warn(f"{from_id}\u2192{to_id}: \uce90\uc2dc\uc5d0 \uc5c6\uace0 searoute \ud638\ucd9c\ub3c4 "
                          f"\uc2e4\ud328 ({type(_e).__name__}) \u2014 \uacbd\uc720\uc810 \uc5c6\uc74c")
                _SR_CACHE[rt_key] = []
                return []

        def _sea_waypoints(from_id, to_id):
            """두 노드 사이의 해상 경유점 — 정확매칭 → 캐시/searoute → generic 순"""
            # 1) 정확한 (from, to) 매칭
            key = (from_id, to_id)
            if key in _SEA_SEGMENTS:
                return _SEA_SEGMENTS[key]
            # 2) searoute 캐시 / 동적 호출 (항구별 정밀 경로)
            sr = _searoute_waypoints_by_id(from_id, to_id)
            if sr:
                return sr
            # 3) generic KP 폴백 (캐시에도 없을 때만)
            if to_id.startswith('KP_'):
                generic = (from_id, 'KP')
                if generic in _SEA_SEGMENTS:
                    return _SEA_SEGMENTS[generic]
                if from_id == 'CP_Malacca':
                    return _SEA_SEGMENTS.get('Malacca_to_KP', [])
                if from_id == 'CP_Lombok':
                    return _SEA_SEGMENTS.get(('CP_Lombok', 'KP'), [])
            _map_warn(f"{from_id}\u2192{to_id}: \uacbd\uc720\uc810 \uc5c6\uc74c \u2014 "
                      f"\uc9c1\uc120\uc73c\ub85c \uadf8\ub824\uc9d1\ub2c8\ub2e4(\uc721\uc9c0 \uad00\ud1b5 \uac00\ub2a5)")
            return []



        def _render_map(s, map_id):

            """KG 기반 Leaflet 지도: 초크포인트·항로·우회 경로를 KG에서 읽어 렌더링"""

            part_a = s.get('part_a', {})

            routes = part_a.get('routes', []) if isinstance(part_a, dict) else []

            tier = s.get('tier', 1)

            sig = s.get('signal', {})



            # part_a routes → 교란 중인 CP 추출
            # v11: 2-layer 접근 (v10 kg_basis 단독 → 말라카 등 거짓양성 발생 해결)
            #   Layer 1: path 텍스트에서 CP 키워드(이름 첫 단어) 매칭
            #            — LLM 자유 텍스트이지만, CP명 첫 단어는 거의 항상 포함
            #   Layer 2: KG linkedTo 단방향 전파
            #            — 바브엘만데브 교란 → 수에즈도 영향 (지리적 연쇄)
            #   안전장치: dominant_cluster는 항상 포함

            _active_cps = set()

            # Layer 1: path 키워드 매칭 (CP명 첫 단어 vs 전체 route path 텍스트)
            _all_paths = ' '.join(r.get('path', '') for r in routes)
            for cp_id, kw in _CP_KEYWORDS.items():
                if kw in _all_paths:
                    _active_cps.add(cp_id)

            # Layer 2: KG linkedTo 양방향 전파 (같은 수로 관문 — 어느 쪽 교란이든 전체 항로 차단)
            _propagated = set()
            for cp_id in list(_active_cps):
                for linked in _CP_LINKED.get(cp_id, set()):
                    if linked not in _active_cps:
                        _propagated.add(linked)
            _active_cps |= _propagated

            _dominant = sig.get('dominant_cluster', '')

            # dominant CP는 항상 active에 포함 (안전장치)
            if _dominant and _dominant.startswith('CP_') and _dominant in _CP_NAMES:
                _active_cps.add(_dominant)

            _secondary_cps = _active_cps - {_dominant}

            if _active_cps:
                print(f'  MAP {s.get("week","?")}: active={sorted(_active_cps)}, dominant={_dominant}, secondary={sorted(_secondary_cps)}, propagated={sorted(_propagated)}')



            if tier <= 1 and not _active_cps:

                print(f'  MAP skip: {s.get("week","?")}')

                return ''



            # ── 교란 품목 목록 ──

            active_commodities = set()

            for r in routes:

                active_commodities.add(r.get('commodity',''))



            # ── KG shipsTo 중 교란 CP를 경유하는 항로 추출 ──

            affected_routes = []

            for e in _SHIPS_TO:

                edge_cps = set(e.get('chokepoints', []))

                if edge_cps & _active_cps:

                    affected_routes.append(e)



            # ── 마커 JS 생성 ──

            markers_js = []



            # 1) 초크포인트 마커

            for cp_id in _GEO:

                if not cp_id.startswith('CP_'):

                    continue

                coord = _GEO[cp_id]

                name = _CP_NAMES.get(cp_id, cp_id)

                is_dominant = (cp_id == _dominant)

                is_secondary = (cp_id in _secondary_cps)

                if is_dominant:

                    color, radius, opacity, status = '#c0392b', 10, 1.0, '주요 교란'

                elif is_secondary:

                    color, radius, opacity, status = '#e67e22', 8, 0.9, '교란'

                else:

                    color, radius, opacity, status = '#2980b9', 5, 0.4, '정상'

                popup = f"<b>{name}</b><br>상태: {status}" if (is_dominant or is_secondary) else f"<b>{name}</b>"

                # CP_Panama는 한국 오른쪽(+360°)에 기본 표시
                lng = coord[1] + 360 if cp_id == 'CP_Panama' else coord[1]

                markers_js.append(

                    f'L.circleMarker([{coord[0]},{lng}],{{radius:{radius},fillColor:"{color}",'

                    f'color:"#fff",weight:2,fillOpacity:{opacity}}}'

                    f').addTo(m).bindPopup("{popup}");'

                )

            # 2) 한국 마커

            markers_js.append(

                'L.marker([36.5,127.0],{icon:L.divIcon({html:"\U0001f1f0\U0001f1f7",className:"kr-icon",iconSize:[24,24],iconAnchor:[12,12]})}).addTo(m);'

            )



            routes_js = []



            # 3) KG shipsTo 항로 — 교란 CP 경유 항로만 표시

            _drawn = set()  # 중복 방지 (동일 FP→KP 그룹)

            for e in affected_routes:

                fp = e.get('from','')

                kp = e.get('to','')

                cf = e.get('commodity','')

                edge_cps = e.get('chokepoints', [])

                key = f"{fp}_{kp}"

                if key in _drawn or fp not in _GEO or kp not in _GEO:

                    continue

                _drawn.add(key)



                # 경유지 좌표 연결: 해상 경유점 삽입으로 실제 항로 반영

                node_seq = [fp] + [cp for cp in edge_cps if cp in _GEO] + [kp]

                is_panama_route = 'CP_Panama' in edge_cps

                waypoints = []

                for idx_n in range(len(node_seq)):

                    nid = node_seq[idx_n]

                    coord = list(_GEO[nid])

                    # 파나마 경유: 미주 측 좌표를 +360° 이동 (지도 오른쪽에서 한국으로 연속 표시)

                    if is_panama_route and not nid.startswith('KP_') and coord[1] < 0:

                        coord = [coord[0], coord[1] + 360]

                    if idx_n == 0:

                        waypoints.append(coord)

                    else:

                        prev_id = node_seq[idx_n - 1]

                        # 파나마 경유 태평양 횡단 전용 경유점 (양수 경도: 오른쪽→한국)

                        if prev_id == 'CP_Panama' and nid.startswith('KP_'):

                            waypoints.extend([

                                [12, 240], [22, 200], [28, 165],

                                [26, 140], [30, 128], [33, 126],

                            ])

                        else:

                            intermediates = _sea_waypoints(prev_id, nid)

                            if is_panama_route:

                                intermediates = [[p[0], p[1] + 360] if p[1] < 0 else p for p in intermediates]

                            waypoints.extend(intermediates)

                        waypoints.append(coord)



                fp_name = _KG_NODES.get(fp, {}).get('name', fp)

                kp_name = _KG_NODES.get(kp, {}).get('name', kp)

                cf_name = _KG_NODES.get(cf, {}).get('name', cf)

                popup = f"{fp_name} → {kp_name} ({cf_name})"



                routes_js.append(

                    f'L.polyline({json.dumps(waypoints)},{{color:"#2980b9",weight:2,opacity:0.6}}).addTo(m).bindPopup("{popup}");'

                )



            # 4) 우회 경로 — 교란 CP에 대한 bypass

            for bp in _BYPASSES:

                bp_id = bp.get('from','')

                bypassed_cp = bp.get('to','')

                if bypassed_cp not in _active_cps:

                    continue

                bp_node = _BP_NODES.get(bp_id, {})

                bp_name = bp_node.get('name', bp_id)

                add_days = bp_node.get('additionalDays', '?')

                cap_pct = bp.get('bypassCapacityPct', '?')

                popup = f"우회: {bp_name}<br>추가 일수: {add_days}일 | 용량: {cap_pct}%"



                # 우회 경로 시각화: BP_CapeRoute는 희망봉 루트
                if bp_id == 'BP_CapeRoute':
                    # 사전 캐시에서 희망봉 경유 항로 로드
                    if 'CAPE_ROUTE' in _SR_FILE_CACHE:
                        cape_route = _SR_FILE_CACHE['CAPE_ROUTE']
                    else:
                        # searoute 동적 생성 (Rotterdam→Cape→Busan)
                        _rot = _GEO.get('FP_Rotterdam', [51.9, 4.5])
                        _bus = _GEO.get('KP_Busan', [35.1, 129.0])
                        try:
                            import searoute as sr
                            _r1 = sr.searoute([_rot[1], _rot[0]], [18.5, -34.5])
                            _r2 = sr.searoute([18.5, -34.5], [_bus[1], _bus[0]])
                            _combined = _r1['geometry']['coordinates'] + _r2['geometry']['coordinates'][1:]
                            _step = max(1, len(_combined) // 30)
                            _simp = _combined[::_step]
                            if _combined[-1] not in _simp:
                                _simp.append(_combined[-1])
                            cape_route = [[round(c[1],2), round(c[0],2)] for c in _simp]
                        except Exception:
                            _map_warn("CAPE_ROUTE \uce90\uc2dc \uc5c6\uc74c + searoute \ubbf8\uc124\uce58 \u2014 "
                                      "\ud76c\ub9dd\ubd09 \uc6b0\ud68c\ub85c\uac00 3\uc810 \uc9c1\uc120\uc73c\ub85c \uadf8\ub824\uc9d1\ub2c8\ub2e4")
                            cape_route = [_rot, [-34.5, 18.5], _bus]

                    routes_js.append(
                        f'L.polyline({json.dumps(cape_route)},{{color:"#c0392b",weight:2,opacity:0.7,dashArray:"8,5"}}).addTo(m).bindPopup("{popup}");'
                    )

                elif bp_id == 'BP_SaudiEastWest':

                    # 사우디 동서 파이프라인: 페르시아만→홍해(얀부)

                    pipe_route = [_GEO.get('FP_RasTanura',[26.6,50.2]),[24.5,44.0],[24.0,38.2]]

                    routes_js.append(

                        f'L.polyline({json.dumps(pipe_route)},{{color:"#e67e22",weight:3,opacity:0.7,dashArray:"4,4"}}).addTo(m).bindPopup("{popup}");'

                    )

                elif bp_id == 'BP_FujairahPipeline':

                    # Habshan→Fujairah 파이프라인

                    pipe_route = [[24.2,54.6],_GEO.get('FP_Fujairah',[25.1,56.3])]

                    routes_js.append(

                        f'L.polyline({json.dumps(pipe_route)},{{color:"#e67e22",weight:3,opacity:0.7,dashArray:"4,4"}}).addTo(m).bindPopup("{popup}");'

                    )

                elif bp_id == 'BP_MagellanRoute':

                    # 마젤란 해협/케이프혼 우회: USGulf → 남대서양 → 케이프혼 → 남태평양 → 한국
                    # +360° 좌표계 사용 (파나마 경유 항로와 동일하게 지도 오른쪽에서 표시)
                    _usg = _GEO.get('FP_USGulf', [29.3, -94.8])
                    _bus = _GEO.get('KP_Busan', [35.1, 129.0])
                    magellan_route = [
                        [_usg[0], _usg[1] + 360],  # USGulf (+360)
                        [25, 295],    # 카리브해 남부
                        [10, 310],    # 베네수엘라 앞바다
                        [0, 320],     # 적도 브라질 앞바다
                        [-15, 330],   # 브라질 남부
                        [-35, 310],   # 아르헨티나 앞바다
                        [-52, 295],   # 마젤란 해협 입구
                        [-56, 290],   # 케이프혼
                        [-55, 270],   # 드레이크 해협 서쪽
                        [-50, 250],   # 남태평양 진입
                        [-40, 220],   # 남태평양
                        [-25, 195],   # 태평양 중부
                        [-10, 175],   # 적도 부근
                        [10, 155],    # 서태평양
                        [25, 135],    # 필리핀해
                        [_bus[0], _bus[1]],  # 부산
                    ]

                    routes_js.append(
                        f'L.polyline({json.dumps(magellan_route)},{{color:"#c0392b",weight:2,opacity:0.7,dashArray:"8,5"}}).addTo(m).bindPopup("{popup}");'
                    )



            all_js = ' '.join(markers_js + routes_js)



            # ── 범례 ──

            dominant_names = [_CP_NAMES.get(c,'') for c in [_dominant] if c in _CP_NAMES and c in _active_cps]

            secondary_names = [_CP_NAMES.get(c,'') for c in _secondary_cps if c in _CP_NAMES]

            legend_parts = []

            if dominant_names:

                legend_parts.append(f'<span style="color:#c0392b">\u25cf 주요:</span> {", ".join(dominant_names)}')

            if secondary_names:

                legend_parts.append(f'<span style="color:#e67e22">\u25cf 부차:</span> {", ".join(secondary_names)}')

            legend_html = ' | '.join(legend_parts) if legend_parts else '현재 교란 없음'

            commodity_list = ', '.join(active_commodities) if active_commodities else '-'



            # 우회 경로 범례

            active_bp_names = []

            for bp in _BYPASSES:

                if bp.get('to','') in _active_cps:

                    bp_node = _BP_NODES.get(bp.get('from',''), {})

                    active_bp_names.append(bp_node.get('name', bp.get('from','')))

            bp_legend = f' | 우회: {", ".join(set(active_bp_names))}' if active_bp_names else ''



            return (

                f'<div class="map-section">'

                f'<div class="map-title">\U0001f5fa 해상 공급망 위기 지도</div>'

                f'<div id="{map_id}" style="width:100%;height:340px;border-radius:8px;border:1px solid #ddd;"></div>'

                f'<div class="map-legend">{legend_html}</div>'

                f'<div class="map-legend" style="margin-top:2px;">'

                f'<span style="color:#2980b9">\u2500\u2500 정상 항로</span> | '

                f'<span style="color:#c0392b">- - - 우회 항로</span> | '

                f'<span style="color:#e67e22">- - 파이프라인</span> | '

                f'교란 품목: {commodity_list}{bp_legend}</div>'

                f'<script>'

                f'window.__mapInit=window.__mapInit||{{}};'

                f'window.__mapInit["{map_id}"]=function(){{'

                f'if(window.__maps&&window.__maps["{map_id}"])return;'

                f'var m=L.map("{map_id}",{{scrollWheelZoom:false,zoomControl:true}}).setView([15,60],2);'

                f'L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}",{{maxZoom:16}}).addTo(m);'

                f'L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{{z}}/{{y}}/{{x}}",{{attribution:"Esri, HERE, Garmin, &copy; OSM",maxZoom:16}}).addTo(m);'


                f'{all_js}'

                f'window.__maps=window.__maps||{{}};window.__maps["{map_id}"]=m;'

                f'}};'

                f'</script>'

                f'</div>'

            )





        def render_scenario(s):

            if s.get('skipped'):

                return (f'<div class="skipped">⏭ 스킵 — Tier {s.get("tier","?")} {s.get("tier_label","")}</div>')

            tier   = s.get('tier', 1)

            label  = s.get('tier_label', '정상')

            period = s.get('period', '?')

            header = s.get('header', {})

            sig    = s.get('signal', {})

            indicators = s.get('indicators', {})

            html   = []





            # 헤더 블록

            # crisis_level은 Tier에서 자동 결정 (LLM 자유 판단 제거 — Tier가 이미 신호 정규화 완료)

            _TIER_CRISIS = {1: 'Normal', 2: 'Caution', 3: 'Warning', 4: 'Crisis'}

            crisis_level = _TIER_CRISIS.get(tier, '?')

            cl_color = {'Crisis':'#c0392b','Warning':'#e67e22','Caution':'#2980b9','Normal':'#27ae60'}.get(crisis_level,'#7f8c8d')

            tier_bg  = TIER_BG.get(tier, '#7f8c8d')


            html.append(

                f'<div class="header-block">'

                f'<div class="header-top">'

                f'<span class="tier-badge" style="background:{tier_bg}">Tier {tier} {esc(label)}</span>'

                f'<span class="period">{format_period_display(s)}</span>'

                f'<span class="crisis-level" style="color:{cl_color}">● {esc(crisis_level)}</span>'

                f'</div>'
                f'<div class="signal-bar">'

                f'신호: Crisis <b>{sig.get("crisis_pct","?")}%</b> | '

                f'Warning <b>{sig.get("warning_pct","?")}%</b> | '

                f'합산 <b>{sig.get("warning_crisis_pct","?")}%</b> | '

                f'총 <b>{sig.get("n_articles","?")}건</b> | '

                f'추세: <b>{sig.get("trend","-")}</b>'

                f'</div>'

            )



            # ── 지도 삽입 ──

            _map_id = f"map_{s.get('week','x').replace('-','_')}"

            html.append(_render_map(s, _map_id))



            # ── 상황 요약: 개조식 (위기 클러스터별 텍스트 재구조화) ──

            summary = header.get('situation_summary', '')

            _ref_map = s.get('ref_map', {})

            if summary:

                html.append('<div class="situation structured">')

                html.append('<b>■ 상황 요약</b>')



                # 클러스터 키워드 매핑 (우선순위 순)

                _CL_KW = [

                    ('홍해/바브엘만데브', ['후티', '예멘', '바브엘만데브', '홍해']),

                    ('호르무즈 해협', ['호르무즈', '이란', '걸프', '통행료', '봉쇄']),

                    ('수에즈 운하', ['수에즈']),

                    ('파나마 운하', ['파나마']),

                    ('대만 해협', ['대만해협', '대만 해협']),

                    ('흑해', ['흑해', '우크라이나', '러시아']),

                    ('에너지/유가', ['브렌트유', 'WTI', '유가', 'IEA', 'OPEC', 'LNG', '카타르', '에너지 안보', '에너지 대란']),

                    ('한국 영향', ['국내', '한국', '소비자', '가스요금', '나프타', '수출 금지', '수출금지', '원/달러', '환율']),

                    ('해운/물류', ['SCFI', 'BDI', '운임', '컨테이너', '선사']),

                ]



                # 텍스트를 문장 단위로 분할 (한국어 문장종결어미 기준)

                _marked = re.sub(r'([다음임함짐됨](?:\[\d+\])*\.)(\s+)(?=[가-힣0-9"\'\'])', r'\1§SPLIT§', summary)

                # re.split with capturing group produces interleaved whitespace — filter

                _segs = [s.strip() for s in _marked.split('§SPLIT§') if s.strip() and len(s.strip()) > 5]



                # 각 세그먼트를 클러스터에 배정

                _cl_map = {}  # {cluster: [segments]}

                _unmatched = []

                for _seg in _segs:

                    _assigned = False

                    for _cname, _kws in _CL_KW:

                        if any(_kw in _seg for _kw in _kws):

                            _cl_map.setdefault(_cname, []).append(_seg)

                            _assigned = True

                            break

                    if not _assigned:

                        _unmatched.append(_seg)



                # 클러스터별 렌더링

                if _cl_map:

                    for _cname, _items in _cl_map.items():

                        html.append(f'<div class="cluster-group">')

                        html.append(f'<div class="cluster-title">{esc(_cname)}</div>')

                        html.append('<ul class="cluster-bullets">')

                        for _item in _items:

                            html.append(f'<li>{_linkify_refs(clean_kg(_item), _ref_map)}</li>')

                        html.append('</ul></div>')

                    if _unmatched:

                        html.append(f'<div class="cluster-group">')

                        html.append(f'<div class="cluster-title">종합</div>')

                        html.append('<ul class="cluster-bullets">')

                        for _item in _unmatched:

                            html.append(f'<li>{_linkify_refs(clean_kg(_item), _ref_map)}</li>')

                        html.append('</ul></div>')

                else:

                    # 클러스터 매핑 실패: 단순 bullet 리스트

                    html.append('<ul class="cluster-bullets">')

                    for _seg in _segs:

                        html.append(f'<li>{_linkify_refs(clean_kg(_seg), _ref_map)}</li>')

                    html.append('</ul>')



                # 접이식 원문

                html.append(f'<details class="original-narrative"><summary>원문 보기</summary><p>{_linkify_refs(clean_kg(summary), _ref_map)}</p></details>')

                html.append('</div>')

                # ── 주간 참조 기사 목록 ──

                _wsrc = _get_week_sources(period)

                if _wsrc:

                    html.append(_render_weekly_sources(_wsrc))

            # 기존 저장 JSON에 개별기업주가 포함된 경우를 위해 렌더링 단계에서도 필터

            _IND_STOCK_UNITS = {'KRW'}  # 개별기업주는 KRW + 집계지표가 아닌 경우

            _IND_STOCK_NAMES = {

                'SK이노', 'S-Oil', '롯데케미칼', 'LG화학', '한화솔루션',

                'HMM', '팬오션', '가스공사', '대한항공', 'CJ제일제당', '농심',

                'SK이노베이션',

            }

            changes = [

                c for c in header.get('changes_from_prev', [])

                if c.get('item', '') not in _IND_STOCK_NAMES

            ]

            if changes:

                html.append('<div class="changes"><b>■ 전주 대비 주요 변화</b><ul>')

                for c in changes:

                    sym  = chg_sym(c.get('change',''))

                    item = esc(c.get('item',''))

                    frm  = esc(c.get('from',''))

                    to_  = esc(c.get('to',''))

                    det  = clean_kg(c.get('detail',''))

                    html.append(f'<li>{sym} <b>{item}</b>  {frm} → {to_}  <span class="detail">{det}</span></li>')

                html.append('</ul></div>')

            wps = header.get('watchpoints', [])

            if wps:

                html.append('<div class="watchpoints"><b>■ 향후 주시 포인트</b><ul>')

                for w in wps:

                    html.append(f'<li><span class="horizon">{esc(w.get("horizon",""))}</span> {clean_kg(w.get("point",""))}</li>')

                html.append('</ul></div>')

            html.append('</div>')  # header-block









            # 지표 패널

            html.append(render_indicators(indicators))



            # Part A

            routes = s.get('part_a', {}).get('routes', [])

            if routes:

                html.append('<div class="part"><div class="part-title">■ 국제 → 한국 전파경로</div>')

                for r in routes:

                    status   = r.get('status', '')

                    is_new   = r.get('is_new', False)

                    d_type   = r.get('disruption_type', '')

                    _dt_map  = {'ROUTE':'⚓ 경로위기','SOURCE':'🏭 공급원위기','LOGISTICS':'📦 물류위기'}

                    dt_tag   = f'<span class="dt-tag dt-{d_type.lower()}">{_dt_map.get(d_type, d_type)}</span>' if d_type else ''

                    new_tag  = '<span class="new-tag">☆ 신규</span>' if (is_new or status == '신규활성') else ''

                    sta_col  = {'활성':'#27ae60','신규활성':'#e67e22','비활성':'#7f8c8d'}.get(status,'#bdc3c7')

                    html.append(

                        f'<div class="route-item">'

                        f'<span class="route-status" style="border-color:{sta_col};color:{sta_col}">{esc(status)}</span>'

                        f'<b>{clean_kg(r.get("commodity","?"))}</b> {dt_tag}{new_tag}'

                        f'<div class="route-path">⟶ {clean_path(r.get("path",""))}</div>'



                        f'</div>'

                    )

                html.append('</div>')



            # Part B

            cascades = s.get('part_b', {}).get('cascades', [])

            if cascades:

                html.append('<div class="part"><div class="part-title">■ 국내 산업간 전파경로</div>')

                for cas in cascades:

                    new_tag = '<span class="new-tag">☆ 신규</span>' if cas.get('is_new') else ''

                    html.append(f'<div class="cascade-item"><b>{clean_kg(cas.get("name",""))} {new_tag}</b><div class="cascade-steps">')

                    for step in cas.get('steps', []):

                        html.append(

                            f'<span class="step">{clean_sector(step.get("from",""))} → {clean_sector(step.get("to",""))}</span>'

                            f'<span class="mech"> ({clean_kg(step.get("mechanism",""))}, {esc(step.get("lag",""))})</span> '

                        )

                    html.append('</div></div>')

                html.append('</div>')





            # Part D

            matrix = s.get('part_d', {}).get('matrix', [])

            if matrix:

                html.append('<div class="part"><div class="part-title">■ 산업별 영향 및 전파경로</div>')

                html.append(

                    '<table class="matrix-table">'

                    '<thead><tr><th>산업</th><th>방향</th>'

                    '<th>초기(0-4주)</th><th>중기(4-12주)</th><th>장기(12주+)</th>'

                    '<th>영향 및 전파경로</th><th>변화</th></tr></thead><tbody>'

                )

                for row in matrix:

                    dir_ = row.get('direction', '?')

                    _pw = row.get('pathway', '')

                    _pw_sents = [p.strip() for p in re.split(r'(?<=[\.])\s+|;\s*', _pw) if p.strip()] if _pw else []

                    _path_sents = [p for p in _pw_sents if '→' in p]

                    _impact_sents = [p for p in _pw_sents if '→' not in p]

                    _pw_html = ''

                    if _path_sents and _impact_sents:

                        _pw_html = '<span class="pw-label">▸ 영향:</span> ' + clean_path('. '.join(_impact_sents))

                        _pw_html += '<br><span class="pw-label">▸ 전파경로:</span> ' + clean_path('. '.join(_path_sents))

                    elif _path_sents:

                        _pw_html = '<span class="pw-label">▸ 전파경로:</span> ' + clean_path('. '.join(_path_sents))

                    elif _impact_sents:

                        _pw_html = '<span class="pw-label">▸ 영향:</span> ' + clean_path('. '.join(_impact_sents))

                    else:

                        _pw_html = clean_path(_pw) if _pw else ''

                    html.append(

                        '<tr>'

                        f'<td><b>{clean_sector(row.get("sector","?"))}</b></td>'

                        f'<td>{DIR_ICON.get(dir_,"?")} {esc(dir_)}</td>'

                        f'<td>{sev_badge(row.get("initial","?"))}</td>'

                        f'<td>{sev_badge(row.get("mid","?"))}</td>'

                        f'<td>{sev_badge(row.get("long","?"))}</td>'

                        f'<td class="pathway">{_pw_html}</td>'

                        f'<td class="change-col">{chg_sym(row.get("change",""))}</td>'

                        '</tr>'

                    )

                html.append('</tbody></table></div>')



            # Part E

            part_e = s.get('part_e', {})

            vulns  = part_e.get('vulnerabilities', [])

            recs   = part_e.get('monitoring_recommendations', [])

            if vulns or recs:

                html.append('<div class="part part-e"><div class="part-title">■ 공급망 취약점 진단 및 모니터링 권고</div>')

                if vulns:

                    html.append('<b>공급망 취약점 진단</b><ul>')

                    for v in vulns:

                        html.append(f'<li>{esc(v)}</li>')

                    html.append('</ul>')



                if recs:

                    html.append('<b>모니터링 권고</b><ul>')

                    for r in recs:

                        html.append(f'<li>{esc(r)}</li>')

                    html.append('</ul>')

                html.append('</div>')

            return '\n'.join(html)



        def tier_dot(t):

            colors = {1:'#27ae60',2:'#2980b9',3:'#e67e22',4:'#c0392b'}

            return f'<span style="color:{colors.get(t,"#bdc3c7")}">●</span>'





        def format_period_plain(s):

            """사이드바 탭용 — period 문자열의 ISO week 번호 표시 (ex: 2026.03.30 (Week 13))"""

            import re as _re

            period_str = s.get('period', '') or s.get('week_label', '') or ''

            m_date = _re.search(r'(\d{4})-(\d{2})-(\d{2})', period_str)

            m_week = _re.search(r'W(\d{1,2})', period_str)

            if m_date and m_week:

                y, mo, d = m_date.groups()

                w = int(m_week.group(1))

                return f"{y}.{mo}.{d} (Week {w:02d})"

            return period_str



        def format_period_display(s):

            """

            period="2026-W01 (2026-01-05)" → "2026.01.05 (Week 01)"

            + 기사수집기간 서브타이틀 (월요일 기준 직전 7일)

            week_label="2026-W01"가 fallback

            """

            import re as _re

            from datetime import date as _date, timedelta as _td

            period_str = s.get('period', '') or s.get('week_label', '') or ''

            # 날짜 추출: YYYY-MM-DD 또는 마감 YYYY-MM-DD 패턴

            m_date = _re.search(r'(\d{4})-(\d{2})-(\d{2})', period_str)

            # 주차 추출: W\d{1,2}

            m_week = _re.search(r'W(\d{1,2})', period_str)

            if m_date and m_week:

                y, mo, d = m_date.groups()

                w = int(m_week.group(1))

                main = f"{y}.{mo}.{d} (Week {w:02d})"

                # 기사수집기간: 해당 월요일 기준 직전 7일 (월~일)

                try:

                    ref_mon  = _date(int(y), int(mo), int(d))

                    wk_end   = ref_mon - _td(days=1)   # 직전 일요일

                    wk_start = ref_mon - _td(days=7)   # 직전 월요일

                    sub = (f'<br><small style="font-size:.9em;font-weight:normal;color:#888">' 

                           f'기사수집기간: {wk_start.strftime("%Y.%m.%d")}~{wk_end.strftime("%Y.%m.%d")}' 

                           f'</small>')

                except Exception:

                    sub = ''

                return main + sub

            elif m_date:

                y, mo, d = m_date.groups()

                return f"{y}.{mo}.{d}"

            elif m_week:

                w = int(m_week.group(1))

                year = _re.match(r'(\d{4})', period_str)

                yr = year.group(1) if year else ''

                return f"{yr} Week {w:02d}"

            return period_str



        display_scenarios = list(reversed(scenarios))  # 최신 주가 위에 오도록 내림차순

        sidebar_items  = []

        content_blocks = []

        for i, s in enumerate(display_scenarios):

            period  = s.get('period','?')

            tier    = s.get('tier', 1)

            label   = s.get('tier_label','?')

            skipped = s.get('skipped', False)

            sid     = f"sc_{i}"

            # PDF 파일 링크용 날짜
            import re as _re_pdf
            _m_pdf = _re_pdf.search(r'(\d{4})-(\d{2})-(\d{2})', s.get('period',''))
            _pdf_date = f'{_m_pdf.group(1)}.{_m_pdf.group(2)}.{_m_pdf.group(3)}' if _m_pdf else ''
            _pdf_url = f'pdf/KMI_Global_SC_AI_Weekly_Report({_pdf_date}).pdf' if _pdf_date >= '2026.04.20' else ''

            dot     = tier_dot(tier)

            skip_cls = ' skipped-menu' if skipped else ''

            active_cls = " active" if i == 0 else ""

            sidebar_items.append(

                f'<label class="menu-item{skip_cls}" for="tab_{sid}">'

                f'{dot} {esc(format_period_plain(s))}</label>'

            )

            content_blocks.append(

                f'<div class="scenario-block" id="{sid}" data-pdf="{_pdf_url}">{render_scenario(s)}</div>'

            )



        # ── radio input 생성 (CSS-only 탭 전환용) ──

        radio_inputs = []

        tab_css_rules = []

        for i, s in enumerate(display_scenarios):

            sid = f'sc_{i}'

            checked = ' checked' if i == 0 else ''

            radio_inputs.append(f'<input type="radio" name="sc" id="tab_{sid}" hidden{checked}>')

            tab_css_rules.append(f'#tab_{sid}:checked ~ .sidebar label[for="tab_{sid}"] {{ background:#3498db; }}')

            tab_css_rules.append(f'#tab_{sid}:checked ~ .main #{sid} {{ display:block; }}')

        TAB_CSS = '\n'.join(tab_css_rules)



        CSS = """

    * { box-sizing:border-box; margin:0; padding:0; }

    body { font-family:'Noto Sans KR',sans-serif; font-size:16px; background:#f5f6fa; color:#2c3e50; -webkit-user-select:none; user-select:none; }

    .container { display:flex; min-height:100vh; }

    .sidebar { width:180px; min-width:180px; background:#2c3e50; color:#ecf0f1; overflow-y:auto; padding:10px 0; position:sticky; top:0; height:100vh; align-self:flex-start; }

    .sidebar-title { padding:12px 14px; font-size:13px; font-weight:bold; border-bottom:1px solid #34495e; color:#bdc3c7; }

    .nav-link { display:flex; align-items:center; justify-content:space-between; padding:9px 12px; font-size:12px; font-weight:600; color:#ecf0f1; text-decoration:none; border-radius:6px; background:rgba(52,152,219,0.15); border:1px solid rgba(52,152,219,0.3); margin-bottom:8px; } .nav-link:hover { background:#3498db; border-color:#3498db; color:#fff; }

    .menu-item { padding:8px 10px; cursor:pointer; font-size:12px; line-height:1.5; border-bottom:1px solid #34495e; transition:.15s; }

    .menu-item:hover { background:#34495e; }

    .menu-item.active { background:#3498db; }

    .menu-item.skipped-menu { opacity:.5; }

    .main { flex:1; overflow-y:visible; padding:20px 28px; min-width:0; }

    .scenario-block { display:none; }

    #sc_welcome { display:none; }

    /* radio+label 탭 전환: 동적 CSS(TAB_CSS)가 checked 상태에 따라 표시 제어 */

    label.menu-item { display:block; -webkit-user-select:none; user-select:none; }

    .header-block { background:#fff; border-radius:8px; padding:18px 22px; margin-bottom:14px; box-shadow:0 1px 4px rgba(0,0,0,.08); }

    .header-top { display:flex; align-items:center; gap:12px; margin-bottom:10px; }

    .tier-badge { color:#fff; padding:4px 12px; border-radius:5px; font-weight:bold; font-size:15px; }

    .period { font-size:16px; font-weight:bold; flex:1; }

    .crisis-level { font-weight:bold; font-size:17px; }


    .signal-bar { font-size:14px; color:#7f8c8d; padding:6px 0 10px; border-bottom:1px solid #ecf0f1; margin-bottom:10px; }

    .situation p { font-size:16px; line-height:1.75; margin-top:8px; }

    .situation.structured { margin-top:10px; }

    .cluster-group { margin:10px 0 6px; padding-left:4px; border-left:3px solid #3498db; }

    .cluster-title { font-size:15px; font-weight:bold; color:#2c3e50; padding:4px 8px; background:#eaf2ff; margin-bottom:2px; }

    .cluster-bullets { margin:4px 0 8px 18px; padding:0; }

    .cluster-bullets li { font-size:15px; line-height:1.7; margin-bottom:5px; }

    .cluster-bullets li b { color:#2c3e50; }

    .original-narrative { margin-top:10px; font-size:14px; color:#7f8c8d; }

    .original-narrative summary { cursor:pointer; font-size:13px; color:#95a5a6; }

    .original-narrative p { font-size:14px; line-height:1.65; color:#555; margin-top:5px; }

    .changes, .watchpoints { margin-top:10px; }

    .changes ul, .watchpoints ul { margin-top:5px; padding-left:18px; }

    .changes li, .watchpoints li { font-size:16px; line-height:1.75; margin-bottom:6px; }

    .horizon { background:#eaf2ff; color:#2980b9; padding:1px 6px; border-radius:3px; font-size:11px; margin-right:6px; }

    .detail { color:#7f8c8d; font-size:13px; }

    .new-tag { background:#f39c12; color:#fff; padding:1px 6px; border-radius:3px; font-size:11px; }

    /* 전체 지표 패널 */

    .ind-panel { background:#fff; border-radius:8px; padding:14px 18px; margin-bottom:14px; box-shadow:0 1px 4px rgba(0,0,0,.08); border-left:4px solid #1abc9c; }

    .ind-title { font-weight:bold; font-size:13px; color:#1abc9c; margin-bottom:10px; }

    .ind-groups { display:flex; flex-wrap:wrap; gap:14px; }

    .ind-group { min-width:160px; flex:1; }

    .ind-group-title { font-size:11px; font-weight:bold; color:#7f8c8d; letter-spacing:.04em; margin-bottom:6px; padding-bottom:3px; border-bottom:1px solid #ecf0f1; }

    .ind-items { display:flex; flex-wrap:wrap; gap:6px; }

    .ind-item { background:#f8f9fa; border-radius:6px; padding:6px 10px; min-width:88px; cursor:default; transition:.15s; }

    .ind-item:hover { background:#eaf6f3; }

    .ind-name { font-size:11px; font-weight:bold; color:#555; margin-bottom:2px; }

    .ind-name a { color:#555; text-decoration:none; }

    .ind-name a:hover { color:#2980b9; text-decoration:underline; }

    .ind-val { font-size:13px; font-weight:bold; color:#2c3e50; }

    .ind-unit { font-size:10px; color:#95a5a6; margin-left:2px; font-weight:normal; }

    .ind-chg { font-size:11px; font-weight:bold; margin-top:2px; }

    .ind-date { font-size:10px; color:#aaa; margin-left:4px; font-weight:normal; vertical-align:middle; }



    /* Part 공통 */

    .part { background:#fff; border-radius:8px; padding:20px 24px; margin-bottom:14px; box-shadow:0 1px 4px rgba(0,0,0,.08); }

    .part-title { font-weight:bold; font-size:16px; border-bottom:2px solid #3498db; padding-bottom:6px; margin-bottom:12px; }

    .part-e { border-left:4px solid #9b59b6; }

    .part-e .part-title { border-bottom-color:#9b59b6; }

    .part-e ul { padding-left:18px; margin-top:6px; }

    .part-e li { font-size:15px; line-height:1.7; margin-bottom:5px; }

    .part-e b { font-size:15px; }

    .route-item { border-left:3px solid #3498db; padding:8px 12px; margin-bottom:8px; background:#f8f9fa; border-radius:0 4px 4px 0; }

    .route-status { border:1px solid; padding:1px 7px; border-radius:4px; font-size:11px; margin-right:8px; }

    .dt-tag { display:inline-block; font-size:11px; font-weight:600; padding:1px 6px; border-radius:3px; margin-left:6px; vertical-align:middle; }

    .dt-route { background:#e8f0fe; color:#1a56db; }

    .dt-source { background:#f3e8ff; color:#7c3aed; }

    .dt-logistics { background:#e0f7f4; color:#0d9488; }

    .route-path { margin-top:4px; color:#555; font-size:16px; line-height:1.75; }




    .cascade-item { border-left:3px solid #e67e22; padding:8px 12px; margin-bottom:8px; background:#fef9f0; border-radius:0 4px 4px 0; }

    .cascade-steps { margin-top:5px; font-size:16px; line-height:1.75; }

    .step { font-weight:bold; }

    .mech { color:#7f8c8d; }

    .sector-block { margin-bottom:16px; padding-bottom:12px; border-bottom:1px solid #f0f0f0; }

    .sector-block:last-child { border-bottom:none; margin-bottom:0; padding-bottom:0; }

    .sector-name { font-weight:bold; font-size:14px; margin-bottom:6px; }

    .sector-id { font-weight:normal; color:#7f8c8d; font-size:13px; }

    table { width:100%; border-collapse:collapse; font-size:15px; }

    th { background:#f0f3f7; padding:7px 10px; text-align:center; border:1px solid #e0e3e8; font-size:13px; }

    td { padding:8px 10px; border:1px solid #e0e3e8; vertical-align:middle; font-size:15px; line-height:1.6; }

    .impact-table td { text-align:center; }

    .impact-table td:first-child { text-align:left; }

    .matrix-table td { text-align:center; }

    .matrix-table td:first-child, .matrix-table td:nth-child(6) { text-align:left; }

    .change-col { font-weight:bold; color:#c0392b; }

    .pathway { color:#555; font-size:16px; line-height:1.75; }

    .pw-label { font-weight:600; color:#2980b9; font-size:13px; }

    .policy-table th, .policy-table td { text-align:left; }

    .basis { color:#7f8c8d; font-size:13px; }

    .skipped { padding:20px; color:#7f8c8d; font-style:italic; }

    /* ── 리포트 헤더 ── */

    .report-header { background:#2c3e50; color:#ecf0f1; padding:20px 28px; display:flex; align-items:center; gap:12px; flex-wrap:nowrap; }
    .kmi-logo { height:34px; margin-right:0; flex-shrink:0; }
    .rh-text-group { display:flex; flex-direction:column; gap:2px; }

    .report-header .rh-title { font-size:20px; font-weight:700; white-space:nowrap; }

    .report-header .rh-sub { font-size:13px; color:#bdc3c7; }

    @media (max-width: 768px) {

      .report-header { padding:8px 12px; flex-wrap:nowrap; }

      .report-header .rh-title { font-size:16px; }
      .kmi-logo { height:27px; margin-right:0; }

      .report-header .rh-sub { font-size:12px; }

    }

    /* ── 모바일 반응형 ── */

    @media (max-width: 768px) {

      body { font-size:15px; }

      .container { flex-direction:column; height:auto; min-height:auto; overflow:visible; }

      /* 탭바: 상단 고정, 가로 스크롤 */

      .sidebar { width:100%; min-width:0; height:auto;

        position:sticky; top:0; z-index:100; background:#2c3e50;

        display:flex; flex-wrap:nowrap; overflow-x:auto; overflow-y:hidden;

        padding:6px 8px; gap:4px; -webkit-overflow-scrolling:touch; }

      .sidebar-title { display:none; }

      .menu-item { flex:0 0 auto; border-radius:4px; padding:4px 10px;

        border-bottom:none; border-right:1px solid #34495e; font-size:11px; white-space:nowrap; }

      .nav-link { flex:0 0 auto; border-radius:4px; padding:4px 10px; border-bottom:none; white-space:nowrap; font-size:11px; margin-bottom:0; }

      label.menu-item { display:block; }

      .main { overflow:visible; padding:12px 14px; }

      .scenario-block { max-width:100%; }

      .header-top { flex-wrap:wrap; gap:8px; }

      .ind-groups { flex-direction:column; gap:8px; }

      .ind-group { min-width:0; }

      .ind-items { gap:4px; }

      table { font-size:11px; }

      th, td { padding:3px 5px; }

      .part { padding:12px 14px; }

      .header-block { padding:14px 16px; }

    }

    /* 주간 참조 기사 */

    /* 기사 인용 링크 */

    .ref-link { font-size:0.75em; vertical-align:super; }

    .ref-link a, .ref-link { color:#2980b9; text-decoration:none; cursor:pointer; }

    .ref-link a:hover { color:#e74c3c; text-decoration:underline; }

    .weekly-sources { margin-top:12px; }

    .ws-summary { cursor:pointer; font-size:14px; font-weight:600; color:#2980b9; padding:6px 10px; background:#eaf2ff; border-radius:5px; }

    .ws-summary:hover { background:#d5e6fa; }

    .ws-day { margin:4px 0 2px 8px; }

    .ws-day-sum { cursor:pointer; font-size:13px; color:#555; padding:3px 6px; }

    .ws-day-sum:hover { color:#2980b9; }

    .ws-day-body { padding-left:10px; }

    .ws-cat { font-size:12px; font-weight:600; color:#7f8c8d; margin:6px 0 2px; }

    .ws-arts { margin:0 0 4px 16px; padding:0; }

    .ws-arts li { font-size:12px; line-height:1.6; list-style:disc; }

    .ws-arts a { color:#555; text-decoration:none; }

    .ws-arts a:hover { color:#2980b9; text-decoration:underline; }

    .ws-more { color:#999; font-style:italic; list-style:none; }

    /* PDF 다운로드 링크 */
    .pdf-link-btn { display:inline-block; background:#fff; color:#2c3e50; text-decoration:none; padding:5px 12px; border-radius:4px; font-size:11px; margin-left:auto; transition:.2s; font-weight:600; white-space:nowrap; }
    .pdf-link-btn:hover { background:#ecf0f1; color:#2c3e50; }





    """



        # radio+label CSS가 탭 전환 담당 — JS 최소화

        JS = ""



        MAP_CSS = """

    .map-section { margin: 10px 14px 16px; }

    .map-title { font-size: 15px; font-weight: 700; margin-bottom: 6px; color: #2c3e50; }

    .map-legend { font-size: 11px; color: #555; margin-top: 4px; line-height: 1.6; }

    .kr-icon { font-size: 20px !important; background: none !important; border: none !important; }

    .leaflet-container { font-family: inherit; touch-action: none; }

    .map-section { touch-action: none; }

    @media (max-width: 768px) {

      .map-section { margin: 8px 0 12px; }

      .map-section > div[id^=map_] { height: 260px !important; }

      .map-title { font-size: 13px; }

      .map-legend { font-size: 10px; }

    }

    """



        MAP_JS = (

            "function _tryInitMap(md){"

            "if(!md||!md.id)return;"

            "if(typeof L==='undefined'){md.innerHTML='<div style=\"padding:20px;color:#888\">지도를 불러오는 중…</div>';return;}"

            "if(window.__mapInit&&window.__mapInit[md.id]){"

            "try{"

            "md.style.touchAction='none';"

            "md.parentElement.style.touchAction='none';"

            "window.__mapInit[md.id]();"

            "[300,800,1500].forEach(function(d){"

            "setTimeout(function(){if(window.__maps&&window.__maps[md.id])window.__maps[md.id].invalidateSize();},d);"

            "});"

            "}catch(e){"

            "md.innerHTML='<div style=\"padding:20px;color:red\">Map error: '+e.message+'</div>';"

            "}"

            "}else if(window.__maps&&window.__maps[md.id]){"

            "window.__maps[md.id].invalidateSize();"

            "}"

            "}"

            "function _initVisibleMap(){"

            "var ch=document.querySelector('input[name=sc]:checked');"

            "if(!ch)return;"

            "var bl=document.getElementById(ch.id.replace('tab_',''));"

            "if(!bl)return;"

            "var md=bl.querySelector('[id^=map_]');"

            "_tryInitMap(md);"

            "}"

            "(function(){"

            "if('IntersectionObserver' in window){"

            "var obs=new IntersectionObserver(function(entries){"

            "entries.forEach(function(e){"

            "if(e.isIntersecting&&e.intersectionRatio>0){"

            "_tryInitMap(e.target);"

            "}"

            "});"

            "},{threshold:0.1});"

            "document.querySelectorAll('[id^=map_]').forEach(function(el){obs.observe(el);});"

            "}else{"

            "window.addEventListener('load',function(){_initVisibleMap();});"

            "}"

            "})();"

            "window.addEventListener('resize',function(){var k;if(window.__maps){for(k in window.__maps){window.__maps[k].invalidateSize();}}});"

            "document.querySelectorAll('input[type=radio][hidden]').forEach(function(r){"

            "r.addEventListener('change',function(){setTimeout(_initVisibleMap,300);});"

            "});"

            "window.addEventListener('load',function(){"

            "setTimeout(_initVisibleMap,500);"

            "setTimeout(_initVisibleMap,2000);"

            "});"

        )

        # ── Leaflet CSS/JS 인라인 임베드 (모바일 웹뷰 CDN 차단 대응) ──
        # KMI 로고 (base64 임베딩)
        _logo_path = os.path.join(os.path.dirname(os.path.abspath('__file__')), 'assets', 'kmi_logo_white.png')
        import base64 as _b64
        _KMI_LOGO_B64 = ''
        if os.path.exists(_logo_path):
            with open(_logo_path, 'rb') as _f:
                _KMI_LOGO_B64 = _b64.b64encode(_f.read()).decode()

        LEAFLET_CSS = ''
        LEAFLET_JS = ''
        for _try_dir in ['.', os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else '.', os.getcwd()]:
            _leaf_css_path = os.path.join(_try_dir, 'leaflet_inline.css')
            _leaf_js_path  = os.path.join(_try_dir, 'leaflet_inline.js')
            if os.path.exists(_leaf_css_path) and os.path.exists(_leaf_js_path):
                with open(_leaf_css_path, 'r', encoding='utf-8') as _f:
                    LEAFLET_CSS = _f.read()
                with open(_leaf_js_path, 'r', encoding='utf-8') as _f:
                    LEAFLET_JS = _f.read()
                print(f'Leaflet 인라인 임베드: CSS {len(LEAFLET_CSS)//1024}KB + JS {len(LEAFLET_JS)//1024}KB (from {_try_dir})')
                break
        if not LEAFLET_JS:
            print('⚠ leaflet_inline.css/js 없음 — CDN fallback 사용')

        n = len(scenarios)

        _PDF_JS = """(function(){var rs=document.querySelectorAll('input[name="sc"]');var slot=document.getElementById('pdf-header-slot');function up(){var c=document.querySelector('input[name="sc"]:checked');if(!c||!slot)return;var d=document.getElementById(c.id.replace('tab_',''));var u=d?d.getAttribute('data-pdf'):'';slot.innerHTML=u?'<a class="pdf-link-btn" href="'+u+'" download>PDF</a>':'';}rs.forEach(function(r){r.addEventListener('change',up);});up();})();"""

        overall_html = (

            '<!DOCTYPE html>\n<html lang="ko">\n<head>\n'

            '<meta charset="UTF-8">\n'

            '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'

            '<!-- Google Analytics (GA4) -->\n'

            '<script async src="https://www.googletagmanager.com/gtag/js?id=G-JEDV505PLS"></script>\n'

            '<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag("js",new Date());gtag("config","G-JEDV505PLS");</script>\n'

            '<title>글로벌 공급망 AI 주간 모니터링 | KMI</title>\n'

            f'<style>{CSS}\n{TAB_CSS}\n{MAP_CSS}\n{LEAFLET_CSS}</style>\n'

            + (f'<script>{LEAFLET_JS}</script>\n' if LEAFLET_JS else
               '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>\n'
               '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>\n')
            + '</head>\n<body>\n'

            '<div class="container">\n'

            + '\n'.join(radio_inputs) + '\n'

            '  <div class="sidebar">\n'

            + '<a class="nav-link" href="https://scm-briefing.kmi.re.kr/">Go To Daily <span style="font-size:16px;line-height:1">›</span></a>\n'

            + '\n'.join(sidebar_items) +

            '\n  </div>\n'

            '  <div class="main">\n'

            '  <div class="report-header">' + (f'<img class="kmi-logo" src="data:image/png;base64,{_KMI_LOGO_B64}" alt="KMI">' if _KMI_LOGO_B64 else '') + '<div class="rh-text-group"><span class="rh-title"> 글로벌 공급망 AI 주간 모니터링</span><span class="rh-sub">한국해양수산개발원(KMI) 해양수산 AX 지원단 · hmjeon@kmi.re.kr</span></div><span id="pdf-header-slot"></span></div>\n'

            '  <div style="background:#f0f4f8;border-left:4px solid #2980b9;padding:8px 14px;font-size:11px;color:#555;margin:10px 14px 0 14px">\n'

            '  본 리포트는 온톨로지 기반 전문가 지식 그래프와 주간 국내외 기사를 기반으로 생성형 AI가 작성한 것으로 KMI의 공식 의견이 아님을 밝힙니다.</div>\n'

            + '\n'.join(content_blocks) +

            '\n    <div id="sc_welcome" style="text-align:center;padding:60px;color:#7f8c8d">← 왼쪽에서 주를 선택하세요</div>\n'

            '  </div>\n'

            '</div>\n'

            f'<script>{JS}\n{MAP_JS}</script>\n'

            "<script>(function(){var a=document.querySelector('.main');if(!a)return;a.style.touchAction='pan-y';var startX=0,startValid=false,multiTouch=false;a.addEventListener('touchstart',function(e){startValid=false;var t=e.target;if(t.closest&&(t.closest('.map-section')||t.closest('.leaflet-control')))return;if(e.touches.length>1){multiTouch=true;return;}multiTouch=false;startX=e.changedTouches[0].clientX;startValid=true;},{passive:true});a.addEventListener('touchend',function(e){if(!startValid||multiTouch){multiTouch=false;return;}var t=e.target;if(t.closest&&(t.closest('.map-section')||t.closest('.leaflet-control'))){startValid=false;return;}if(e.touches.length>0)return;var dx=startX-e.changedTouches[0].clientX;if(Math.abs(dx)<50)return;startValid=false;var cur=document.querySelector('input[type=radio][hidden]:checked');if(!cur)return;var all=Array.from(document.querySelectorAll('input[type=radio][hidden][name=\"'+cur.name+'\"]'));var idx=all.indexOf(cur);var nxt=dx>0?idx+1:idx-1;if(nxt>=0&&nxt<all.length){var lbl=document.querySelector('label[for=\"'+all[nxt].id+'\"]');if(lbl){lbl.click();window.scrollTo(0,0);}}},{passive:true});}());</script>\n"

            "<script>document.addEventListener('contextmenu',function(e){e.preventDefault();});document.addEventListener('keydown',function(e){if((e.ctrlKey||e.metaKey)&&(e.key==='s'||e.key==='u')){e.preventDefault();}});</script>\n"


            f'<script>{_PDF_JS}</script>\n'

            '</body>\n</html>'

        )



        with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:

            f.write(overall_html)



        size_kb = os.path.getsize(OUTPUT_HTML) // 1024

        print(f"✅ {OUTPUT_HTML} 생성 완료 ({size_kb} KB)")

        print(f"   → open weekly_report.html  (macOS)")



def _run_pdf_generation(target_scenario):
    """scenario_generator_v11.ipynb Part 6 (Cell 16) 이식 — 단일 주 PDF 생성.

    target_scenario 하나에 대해서만 PDF를 생성하고 경로를 반환한다.
    파일명 규칙: docs/pdf/weekly_report_W{nn}_{YYYYMMDD}.pdf
    (원본 노트북의 'KMI_Global_SC_AI_Weekly_Report(YYYY.MM.DD).pdf' 규칙을
     요구사항에 맞게 변경 — 자세한 내용은 파일 상단 docstring 참조)
    """
    """
    Cell 12: 주간 보고서 PDF 생성 (v10)
    scenario_results.json > docs/pdf/KMI_Global_SC_AI_Weekly_Report(YYYY.MM.DD).pdf

    HormuzTracker SitRep 스타일의 깔끔한 보고서 문서.
    html2canvas 스크린샷이 아니라 Python으로 직접 레이아웃을 구성합니다.
    """

    import json, os, re, sys
    from pathlib import Path
    from datetime import datetime

    # ── fpdf2 설치 확인 ──
    try:
        from fpdf import FPDF
    except ImportError:
        # requirements.txt 에 fpdf2 가 선언되어 있으므로 여기 도달하면 환경 구성이 잘못된 것.
        # 마지막 수단으로 설치를 시도하되, 반환값을 확인하여 실패를 조용히 넘기지 않는다.
        print("\u26a0 fpdf2 \ubbf8\uc124\uce58 \u2014 \ub9c8\uc9c0\ub9c9 \uc218\ub2e8\uc73c\ub85c \uc124\uce58\ub97c \uc2dc\ub3c4\ud569\ub2c8\ub2e4 (requirements.txt \ud655\uc778 \ud544\uc694)")
        _rc = os.system(f"{sys.executable} -m pip install fpdf2 -q")
        if _rc != 0:
            raise RuntimeError(
                f"fpdf2 \uc124\uce58 \uc2e4\ud328 (exit={_rc}) \u2014 PDF\ub97c \uc0dd\uc131\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4. "
                "requirements.txt \uc758 fpdf2 \uc124\uce58\ub97c \ud655\uc778\ud558\uc138\uc694."
            )
        from fpdf import FPDF

    # ══════════════════════════════════════════════════════════════
    # 0. 설정
    # ══════════════════════════════════════════════════════════════
    RESULT_FILE = 'scenario_results.json'
    OUTPUT_DIR  = 'docs/pdf'
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # (원본 노트북의 GENERATE_LAST_N/GENERATE_FROM/SKIP_EXISTING 필터는 사용하지 않음
    #  — 이 스크립트는 WEEK_TAG로 지정된 단 하나의 주만, 무조건 재생성(덮어쓰기)한다)

    # ══════════════════════════════════════════════════════════════
    # 1. 한국어 폰트 자동 탐색
    # ══════════════════════════════════════════════════════════════
    def find_korean_font():
        """시스템에서 한국어 TTF 폰트를 찾아 (regular, bold) 경로 반환.

        주의: .ttc (TrueType Collection) 파일은 Adobe에서는 정상이지만
        브라우저 PDF 뷰어에서 글자가 깨지므로, .ttf 파일을 우선 사용합니다.
        프로젝트 assets/fonts/ 에 번들된 NotoSansKR을 최우선으로 사용합니다.
        """
        import platform

        # ── 0. 프로젝트 번들 폰트 (최우선 — .ttf, 브라우저 호환 보장) ──
        script_dir = os.path.dirname(os.path.abspath('__file__'))
        bundled_reg = os.path.join(script_dir, 'assets', 'fonts', 'NotoSansKR-Regular.ttf')
        bundled_bold = os.path.join(script_dir, 'assets', 'fonts', 'NotoSansKR-Bold.ttf')
        if os.path.exists(bundled_reg):
            bold_path = bundled_bold if os.path.exists(bundled_bold) else bundled_reg
            return bundled_reg, bold_path

        # ── 1. 시스템 TTF 폰트 (TTC 제외 — 브라우저 호환성) ──
        candidates_ttf = []
        sys_name = platform.system()

        if sys_name == 'Darwin':  # macOS
            candidates_ttf = [
                ('/Library/Fonts/NotoSansKR-Regular.ttf', '/Library/Fonts/NotoSansKR-Bold.ttf'),
                (os.path.expanduser('~/Library/Fonts/NotoSansKR-Regular.ttf'),
                 os.path.expanduser('~/Library/Fonts/NotoSansKR-Bold.ttf')),
                ('/Library/Fonts/NanumGothic.ttf', '/Library/Fonts/NanumGothicBold.ttf'),
                (os.path.expanduser('~/Library/Fonts/NanumGothic.ttf'),
                 os.path.expanduser('~/Library/Fonts/NanumGothicBold.ttf')),
                ('/Library/Fonts/malgun.ttf', '/Library/Fonts/malgunbd.ttf'),
            ]
        elif sys_name == 'Windows':
            windir = os.environ.get('WINDIR', 'C:\\Windows')
            candidates_ttf = [
                (f'{windir}\\Fonts\\malgun.ttf', f'{windir}\\Fonts\\malgunbd.ttf'),
                (f'{windir}\\Fonts\\NotoSansKR-Regular.ttf', f'{windir}\\Fonts\\NotoSansKR-Bold.ttf'),
                (f'{windir}\\Fonts\\NanumGothic.ttf', f'{windir}\\Fonts\\NanumGothicBold.ttf'),
            ]
        else:  # Linux
            candidates_ttf = [
                ('/usr/share/fonts/truetype/nanum/NanumGothic.ttf', '/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf'),
            ]

        # matplotlib 폰트 경로도 시도 (TTF만)
        try:
            import matplotlib.font_manager as fm
            for f in fm.findSystemFonts():
                if not f.lower().endswith('.ttf'):
                    continue  # .ttc 제외
                fname = os.path.basename(f).lower()
                if any(k in fname for k in ['notosanskr', 'nanum', 'malgun']):
                    if 'bold' not in fname:
                        bold = f.replace('Regular', 'Bold').replace('regular', 'bold')
                        candidates_ttf.append((f, bold if os.path.exists(bold) else None))
        except Exception:
            pass

        for reg, bold in candidates_ttf:
            if os.path.exists(reg):
                bold_path = bold if (bold and os.path.exists(bold)) else reg
                return reg, bold_path

        # ── 2. Fallback: TTC 허용 (Adobe에서만 정상, 브라우저 깨짐 경고) ──
        candidates_ttc = []
        if sys_name == 'Darwin':
            candidates_ttc = [
                ('/System/Library/Fonts/Supplemental/AppleSDGothicNeo.ttc', None),
                ('/System/Library/Fonts/AppleSDGothicNeo.ttc', None),
            ]
        elif sys_name == 'Linux':
            candidates_ttc = [
                ('/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc'),
                ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'),
            ]

        for reg, bold in candidates_ttc:
            if os.path.exists(reg):
                print("⚠ TTC 폰트 사용 중 — 브라우저에서 글자가 깨질 수 있습니다.")
                print("  assets/fonts/에 NotoSansKR-Regular.ttf를 추가하세요.")
                bold_path = bold if (bold and os.path.exists(bold)) else reg
                return reg, bold_path

        return None, None


    FONT_REG, FONT_BOLD = find_korean_font()
    if FONT_REG:
        print(f"✅ 한국어 폰트: {os.path.basename(FONT_REG)}")
    else:
        print("⚠ 한국어 폰트를 찾을 수 없습니다. NotoSansKR을 설치해 주세요.")
        print("  macOS: brew install font-noto-sans-cjk-kr")
        print("  또는: https://fonts.google.com/noto/specimen/Noto+Sans+KR 에서 다운로드")

    # ══════════════════════════════════════════════════════════════
    # 2. KG 표기 정리 함수 (Cell 11과 동일)
    # ══════════════════════════════════════════════════════════════
    _KG_NAME_MAP = {
        'CF_CrudeOil':'원유', 'CF_LNG':'LNG', 'CF_Naphtha':'나프타',
        'CF_Corn':'옥수수', 'CF_Wheat':'밀', 'CF_Coal':'석탄',
        'CF_EuroContainer':'유럽 컨테이너', 'CF_RareEarth':'희토류',
        'CF_Meat':'육류', 'CF_Petrochemicals':'석유화학',
        'KS_Energy':'에너지', 'KS_Material':'소재/화학',
        'KS_Manufacture':'제조업', 'KS_Shipping':'해운/물류',
        'KS_FoodAgri':'식품/농산물', 'KS_Construction':'건설/인프라',
        'KS_Finance':'금융', 'KS_Macro':'거시경제', 'KS_Consumer':'소비자',
    }

    def clean_text(text):
        if not text:
            return ''
        t = str(text)
        for kg_id, kor in _KG_NAME_MAP.items():
            t = t.replace(kg_id, kor)
        t = re.sub(r'--\[.*?\]-->', ' → ', t)
        t = re.sub(r'\([A-Za-z_][A-Za-z0-9_]*\)', '', t)
        t = re.sub(r'\bCF_([A-Za-z가-힣0-9]+)', r'\1', t)
        t = re.sub(r'\bKS_([A-Za-z가-힣0-9/]+)', r'\1', t)
        t = re.sub(r'\bCP_([A-Za-z가-힣0-9]+)', r'\1', t)
        t = re.sub(r'\bKI_([A-Za-z가-힣0-9]+)', r'\1', t)
        t = re.sub(r'[\s;]*\(?w=[\d.]+\)?', '', t)
        t = re.sub(r'\[(\d+)\]', '', t)  # 인용 번호 제거
        t = re.sub(r'\s+', ' ', t).strip()
        return t

    def clean_sector(text):
        if not text:
            return ''
        t = str(text).strip()
        m = re.match(r'^([^()]+)\([A-Za-z_][A-Za-z0-9_]*\)$', t)
        if m:
            return m.group(1).strip()
        m = re.match(r'^(?:KS_|CF_|CP_|KI_)[A-Za-z가-힣0-9]+\(([^)]+)\)$', t)
        if m:
            return m.group(1).strip()
        if t in _KG_NAME_MAP:
            return _KG_NAME_MAP[t]
        for prefix in ['KS_', 'CF_', 'CP_', 'KI_']:
            t = re.sub(r'\b' + prefix + r'([A-Za-z가-힣0-9/]+)', r'\1', t)
        return t.strip()

    def extract_date(period_str):
        """period 문자열에서 YYYY.MM.DD 추출"""
        m = re.search(r'(\d{4})-(\d{2})-(\d{2})', period_str or '')
        if m:
            return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
        return period_str or '?'

    def extract_week(period_str):
        m = re.search(r'W(\d{1,2})', period_str or '')
        return int(m.group(1)) if m else 0

    # ══════════════════════════════════════════════════════════════
    # 3. PDF 보고서 클래스
    # ══════════════════════════════════════════════════════════════
    _TIER_CRISIS = {1: 'Normal', 2: 'Caution', 3: 'Warning', 4: 'Crisis'}
    _TIER_KR = {1: '정상', 2: '주의', 3: '경고', 4: '위기'}
    _SEV_MAP = {'심각': 4, '중요': 3, '보통': 2, '미약': 1}
    _DIR_MAP = {'네거티브': '▼', '포지티브': '▲', '혼합': '◆', '안정': '●'}
    _CHG_MAP = {'↑': '▲', '↓': '▼', '↑↑': '▲▲', '↓↓': '▼▼', '☆': '★', '−': '—'}

    # 지표 그룹 순서 및 표시할 지표 필터
    _DISPLAY_GROUPS = {
        '글로벌 해운': ['SCFI', 'BDI', 'Harpex'],
        '초크포인트': [],  # CP_ 로 시작하는 것 자동
        '공급망 스트레스': ['GSCSI', 'RWI_ISL_CTI', 'GSCPI', 'NAPMSDI', 'GPR'],
        '에너지': ['Brent', 'WTI', 'NatGas', 'Gold'],
        '거시경제': ['KOSPI', 'KRWUSD', 'USD_Index', 'VIX', 'KR_ExportVol'],
        '한국 산업 ETF': [],  # ETF로 끝나는 것 자동
    }

    # 주식종목 제외 목록
    _STOCK_NAMES = {
        'SK이노베이션', 'S_Oil', '롯데케미칼', 'LG화학', '한화솔루션',
        'HMM', '팬오션', '한국가스공사', '대한항공', 'CJ제일제당', '농심',
    }


    class WeeklyReportPDF(FPDF):
        """주간 보고서 PDF — 전문 보고서 스타일"""

        # ── 디자인 컬러 팔레트 ──
        C_NAVY      = (26, 42, 58)       # 타이틀, 주요 강조
        C_DARK_BLUE = (44, 62, 80)       # 섹션 제목 텍스트
        C_ACCENT    = (41, 128, 185)     # 액센트 바, 링크 컬러
        C_LIGHT_BG  = (236, 240, 241)    # 섹션 헤더 배경
        C_TABLE_HDR = (44, 62, 80)       # 테이블 헤더 배경 (네이비)
        C_TABLE_ALT = (245, 247, 250)    # 테이블 교대행 배경
        C_BODY_TEXT = (51, 51, 51)       # 본문 텍스트
        C_SUB_TEXT  = (100, 100, 100)    # 부제, 날짜 등
        C_BORDER    = (189, 195, 199)    # 테이블 보더
        C_WHITE     = (255, 255, 255)

        def __init__(self, font_reg, font_bold):
            super().__init__(orientation='P', unit='mm', format='A4')
            self.set_auto_page_break(auto=True, margin=22)

            # 폰트 등록
            self.add_font('KR', '', font_reg)
            self.add_font('KR', 'B', font_bold)
            self._font_reg = font_reg
            self._font_bold = font_bold
            self._is_first_page = True

        def header(self):
            if not self._is_first_page:
                # 2페이지 이후: 상단에 얇은 네이비 바 + 보고서명
                self.set_fill_color(*self.C_NAVY)
                self.rect(0, 0, self.w, 10, 'F')
                self.set_font('KR', 'B', 8)
                self.set_text_color(*self.C_WHITE)
                self.set_y(2.5)
                self.cell(0, 5, '글로벌 공급망 AI 주간 모니터링', align='C')
                self.set_y(12)

        def footer(self):
            self.set_y(-18)
            # 구분선
            self.set_draw_color(*self.C_BORDER)
            self.set_line_width(0.3)
            self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
            self.ln(2)
            self.set_font('KR', '', 6.5)
            self.set_text_color(140, 140, 140)
            self.cell(0, 4,
                'Source: KMI (scm-briefing.kmi.re.kr) | 본 리포트는 온톨로지 기반 전문가 지식 그래프와 AI가 작성한 것으로 KMI의 공식 의견이 아닙니다.',
                align='L')
            self.set_font('KR', '', 7)
            self.set_text_color(160, 160, 160)
            self.cell(0, 4, f'Page {self.page_no()}/{{nb}}', align='R', new_x='LMARGIN', new_y='NEXT')

        def _draw_title_block(self, date_str, week_num):
            """문서 헤더: 진한 네이비 배경 타이틀 바 + KMI 로고"""
            # 네이비 배경 바
            bar_h = 28
            self.set_fill_color(*self.C_NAVY)
            self.rect(0, 0, self.w, bar_h, 'F')

            # KMI 로고 (타이틀 왼쪽, 작게)
            logo_path = os.path.join(os.path.dirname(os.path.abspath('__file__')), 'assets', 'kmi_logo_white.png')
            logo_w_mm = 0
            if os.path.exists(logo_path):
                logo_h = 9  # mm (타이틀 글자 높이와 맞춤)
                logo_w_mm = logo_h * 938 / 709  # 원본 비율 유지
                logo_x = self.l_margin + 2
                logo_y = 7.5  # 타이틀 글자 상단과 맞춤
                self.image(logo_path, x=logo_x, y=logo_y, w=logo_w_mm, h=logo_h)

            # 타이틀 텍스트 (흰색, 로고 오른쪽)
            title_indent = logo_w_mm + 5 if logo_w_mm else 2
            self.set_y(6)
            self.set_font('KR', 'B', 20)
            self.set_text_color(*self.C_WHITE)
            self.set_x(self.l_margin + title_indent)
            self.cell(0, 9, '글로벌 공급망 AI 주간 모니터링', new_x='LMARGIN', new_y='NEXT')

            self.set_font('KR', '', 9)
            self.set_text_color(180, 200, 220)
            self.set_x(self.l_margin + title_indent)
            self.cell(0, 5, f'{date_str}  (Week {week_num:02d})', new_x='LMARGIN', new_y='NEXT')

            # 네이비 바 아래 액센트 라인
            self.set_fill_color(*self.C_ACCENT)
            self.rect(0, bar_h, self.w, 1.2, 'F')
            self.set_y(bar_h + 4)

            # 기관 정보 (네이비 바 아래)
            self.set_font('KR', '', 8)
            self.set_text_color(*self.C_SUB_TEXT)
            self.cell(0, 4, 'https://scm-briefing.kmi.re.kr  |  한국해양수산개발원(KMI) 해양수산 AX 지원단  |  hmjeon@kmi.re.kr',
                      new_x='LMARGIN', new_y='NEXT')
            self.ln(3)
            self._is_first_page = False

        def _section_title(self, num, title):
            """번호 붙은 섹션 제목 — 좌측 액센트 바 + 배경"""
            # 페이지 넘김 체크
            if self.get_y() + 12 > self.h - 25:
                self.add_page()

            y_top = self.get_y()
            h = 8

            # 밝은 배경
            self.set_fill_color(*self.C_LIGHT_BG)
            self.rect(self.l_margin, y_top, self.w - self.l_margin - self.r_margin, h, 'F')

            # 좌측 액센트 바 (파란색, 두꺼운 세로줄)
            self.set_fill_color(*self.C_ACCENT)
            self.rect(self.l_margin, y_top, 1.5, h, 'F')

            # 텍스트
            self.set_font('KR', 'B', 12)
            self.set_text_color(*self.C_DARK_BLUE)
            self.set_xy(self.l_margin + 4, y_top + 1)
            self.cell(0, 6, f'{num}. {title}')
            self.set_y(y_top + h + 3)

        def _body_text(self, text, size=9):
            self.set_font('KR', '', size)
            self.set_text_color(*self.C_BODY_TEXT)
            self.multi_cell(0, 4.8, text)
            self.ln(1.5)

        def _bullet(self, text, size=9, indent=5):
            self.set_font('KR', '', size)
            self.set_text_color(*self.C_BODY_TEXT)
            x = self.get_x()
            self.set_x(x + indent)
            self.set_text_color(*self.C_ACCENT)
            self.cell(4, 4.8, '•')
            self.set_text_color(*self.C_BODY_TEXT)
            self.multi_cell(0, 4.8, text)
            self.ln(0.8)

        def _sub_heading(self, text):
            """서브 헤딩 (그룹명 등)"""
            self.set_font('KR', 'B', 9)
            self.set_text_color(*self.C_ACCENT)
            self.cell(3, 5, '')
            self.set_fill_color(*self.C_ACCENT)
            y_mid = self.get_y() + 2.5
            # 작은 사각형 불릿
            self.rect(self.l_margin + 1, y_mid - 1, 2, 2, 'F')
            self.set_x(self.l_margin + 5)
            self.set_text_color(*self.C_DARK_BLUE)
            self.cell(0, 5, text, new_x='LMARGIN', new_y='NEXT')
            self.ln(0.5)

        def _mini_table(self, headers, rows, col_widths=None, header_bg=None):
            """디자인 테이블 — 네이비 헤더 + zebra 행"""
            if header_bg is None:
                header_bg = self.C_TABLE_HDR

            if not col_widths:
                usable = self.w - self.l_margin - self.r_margin
                col_widths = [usable / len(headers)] * len(headers)

            row_h = 5.5
            hdr_h = 6.5

            # 페이지 넘김 체크 — 헤더 + 최소 2행
            needed = hdr_h + row_h * min(3, len(rows))
            if self.get_y() + needed > self.h - 25:
                self.add_page()

            def _draw_header():
                self.set_font('KR', 'B', 8)
                self.set_fill_color(*header_bg)
                self.set_text_color(*self.C_WHITE)
                self.set_draw_color(*header_bg)
                for i, h in enumerate(headers):
                    self.cell(col_widths[i], hdr_h, h, border=0, fill=True, align='C')
                self.ln()

            _draw_header()

            # Rows — zebra striping
            self.set_font('KR', '', 8)
            for row_idx, row in enumerate(rows):
                # 페이지 넘김 체크
                if self.get_y() + row_h > self.h - 25:
                    self.add_page()
                    _draw_header()
                    self.set_font('KR', '', 8)

                # 교대 배경
                if row_idx % 2 == 1:
                    self.set_fill_color(*self.C_TABLE_ALT)
                    fill = True
                else:
                    self.set_fill_color(*self.C_WHITE)
                    fill = True

                self.set_text_color(*self.C_BODY_TEXT)
                self.set_draw_color(*self.C_BORDER)

                for i, val in enumerate(row):
                    align = 'L' if i == 0 else 'C'
                    txt = str(val)[:60] if len(str(val)) > 60 else str(val)
                    # 하단 보더만
                    x_before = self.get_x()
                    self.cell(col_widths[i], row_h, txt, border='B', align=align, fill=fill)
                self.ln()
            self.ln(3)


    def generate_pdf(scenario, font_reg, font_bold):
        """단일 주의 PDF 보고서 생성"""

        period = scenario.get('period', '?')
        date_str = extract_date(period)
        week_num = extract_week(period)
        tier = scenario.get('tier', 1)
        tier_label = scenario.get('tier_label', '?')
        header = scenario.get('header', {})
        sig = scenario.get('signal', {})
        indicators = scenario.get('indicators', {})
        skipped = scenario.get('skipped', False)

        if skipped:
            return None

        pdf = WeeklyReportPDF(font_reg, font_bold)
        pdf.alias_nb_pages()
        pdf.add_page()

        # ── 문서 헤더 (네이비 타이틀 바) ──
        pdf._draw_title_block(date_str, week_num)

        # ══════════════════════════════════════════════════
        # 1. 상황 요약
        # ══════════════════════════════════════════════════
        pdf._section_title(1, '상황 요약')

        # Tier 배지 (컬러 박스)
        crisis = _TIER_CRISIS.get(tier, '?')
        crisis_kr = _TIER_KR.get(tier, '?')
        tier_colors = {1: (39,174,96), 2: (41,128,185), 3: (230,126,34), 4: (192,57,43)}
        tc = tier_colors.get(tier, (100,100,100))

        # 배지 배경 박스
        badge_text = f' Tier {tier} — {crisis} ({crisis_kr}) '
        pdf.set_font('KR', 'B', 11)
        badge_w = pdf.get_string_width(badge_text) + 8
        badge_h = 7.5
        y_badge = pdf.get_y()
        pdf.set_fill_color(*tc)
        pdf.set_draw_color(*tc)
        # 둥근 느낌의 배지 (rect로 대체)
        pdf.rect(pdf.l_margin, y_badge, badge_w, badge_h, 'F')
        pdf.set_text_color(255, 255, 255)
        pdf.set_xy(pdf.l_margin + 1, y_badge + 0.5)
        pdf.cell(badge_w - 2, badge_h - 1, badge_text, align='C')

        # Tier 라벨 (배지 옆)
        pdf.set_xy(pdf.l_margin + badge_w + 3, y_badge + 1)
        pdf.set_font('KR', 'B', 10)
        pdf.set_text_color(*tc)
        pdf.cell(0, 5, tier_label)
        pdf.set_y(y_badge + badge_h + 3)

        # Signal 바 (컬러 프로그레스 스타일)
        crisis_pct = sig.get('crisis_pct', 0)
        warning_pct = sig.get('warning_pct', 0)
        try:
            crisis_pct_val = float(crisis_pct)
            warning_pct_val = float(warning_pct)
        except (ValueError, TypeError):
            crisis_pct_val = 0
            warning_pct_val = 0

        bar_total_w = 80
        bar_h = 4
        y_bar = pdf.get_y()
        x_bar = pdf.l_margin

        # 배경 (회색)
        pdf.set_fill_color(230, 230, 230)
        pdf.rect(x_bar, y_bar, bar_total_w, bar_h, 'F')
        # Warning (주황)
        if warning_pct_val > 0:
            pdf.set_fill_color(230, 126, 34)
            pdf.rect(x_bar, y_bar, bar_total_w * min((warning_pct_val + crisis_pct_val) / 100, 1), bar_h, 'F')
        # Crisis (빨강)
        if crisis_pct_val > 0:
            pdf.set_fill_color(192, 57, 43)
            pdf.rect(x_bar, y_bar, bar_total_w * min(crisis_pct_val / 100, 1), bar_h, 'F')

        # 시그널 텍스트 (바 옆)
        pdf.set_xy(x_bar + bar_total_w + 3, y_bar - 0.5)
        pdf.set_font('KR', '', 8)
        pdf.set_text_color(80, 80, 80)
        sig_text = (
            f"Crisis {crisis_pct}% | Warning {warning_pct}% | "
            f"합산 {sig.get('warning_crisis_pct','?')}% | "
            f"기사 {sig.get('n_articles','?')}건 | {sig.get('trend','-')}"
        )
        pdf.cell(0, 5, sig_text)
        pdf.set_y(y_bar + bar_h + 3)

        # Summary text
        summary = header.get('situation_summary', '')
        if summary:
            summary_clean = clean_text(summary)
            # 문장 분리
            sentences = re.split(r'(?<=[다음임함짐됨]\.)(\s+)', summary_clean)
            sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 5]
            for sent in sentences:
                pdf._bullet(sent, size=9)
        pdf.ln(2)

        # ══════════════════════════════════════════════════
        # 2. 전주 대비 주요 변화
        # ══════════════════════════════════════════════════
        changes = [c for c in header.get('changes_from_prev', [])
                   if c.get('item', '') not in _STOCK_NAMES]
        if changes:
            pdf._section_title(2, '전주 대비 주요 변화')
            # 테이블: 항목/변화/이전/현재만 (상세는 아래 본문으로)
            headers_chg = ['항목', '변화', '이전', '현재']
            widths_chg = [35, 15, 45, 45]
            rows_chg = []
            for c in changes:
                chg_sym = _CHG_MAP.get(c.get('change', ''), c.get('change', ''))
                rows_chg.append([
                    c.get('item', ''),
                    chg_sym,
                    c.get('from', ''),
                    c.get('to', ''),
                ])
            pdf._mini_table(headers_chg, rows_chg, widths_chg)

            # 상세 내역은 본문 형태로
            for c in changes:
                detail = clean_text(c.get('detail', ''))
                if detail:
                    pdf.set_font('KR', 'B', 8)
                    pdf.set_text_color(44, 62, 80)
                    item_name = c.get('item', '')
                    chg_sym = _CHG_MAP.get(c.get('change', ''), '')
                    pdf.cell(30, 4, f'{chg_sym} {item_name}:', new_x='END')
                    pdf.set_font('KR', '', 8)
                    pdf.set_text_color(80, 80, 80)
                    pdf.multi_cell(0, 4, detail)
                    pdf.ln(0.5)

        # ══════════════════════════════════════════════════
        # 3. 주요 지표
        # ══════════════════════════════════════════════════
        pdf._section_title(3, '주요 지표')

        # 지표를 그룹별로 분류
        ind_by_group = {}
        for ind_key, ind_data in indicators.items():
            if isinstance(ind_data, dict):
                grp = ind_data.get('group', '기타')
                name = ind_data.get('name', ind_key)
                if name in _STOCK_NAMES:
                    continue
                ind_by_group.setdefault(grp, []).append(ind_data)

        group_order = ['글로벌 해운', '초크포인트', '공급망 스트레스', '에너지',
                       '거시경제', '한국 산업 ETF']

        for grp in group_order:
            items = ind_by_group.get(grp, [])
            if not items:
                # 다른 이름으로 매칭 시도
                for k, v in ind_by_group.items():
                    if grp in k:
                        items = v
                        break
            if not items:
                continue

            pdf._sub_heading(grp)

            headers_ind = ['지표', '값', '전주비(%)', '방향', '기준일']
            widths_ind = [35, 25, 20, 15, 25]
            rows_ind = []
            for it in items:
                val = it.get('value', 'N/A')
                if isinstance(val, (int, float)):
                    if val != val:  # NaN
                        val = 'N/A'
                    elif abs(val) >= 1000:
                        val = f'{val:,.1f}'
                    else:
                        val = f'{val:.2f}'
                chg = it.get('chg_pct', '')
                if isinstance(chg, (int, float)) and chg == chg:
                    chg_str = f'{chg:+.1f}'
                else:
                    chg_str = str(chg) if chg else '—'
                direction = '▲' if it.get('chg_dir') == 'up' else ('▼' if it.get('chg_dir') == 'down' else '—')
                data_date = it.get('data_date', '')
                rows_ind.append([
                    it.get('full', it.get('name', '?'))[:25],
                    str(val),
                    chg_str,
                    direction,
                    str(data_date)[:10]
                ])
            pdf._mini_table(headers_ind, rows_ind, widths_ind)

        # ══════════════════════════════════════════════════
        # 4. 국제>한국 전파경로
        # ══════════════════════════════════════════════════
        routes = scenario.get('part_a', {}).get('routes', [])
        if routes:
            pdf._section_title(4, '국제 > 한국 전파경로')
            for r in routes:
                status = r.get('status', '')
                commodity = clean_text(r.get('commodity', ''))
                path = clean_text(r.get('path', ''))
                is_new = r.get('is_new', False)
                d_type = r.get('disruption_type', '')
                dt_map = {'ROUTE': '경로위기', 'SOURCE': '공급원위기', 'LOGISTICS': '물류위기'}
                dt_label = dt_map.get(d_type, d_type)

                new_mark = ' [신규]' if (is_new or status == '신규활성') else ''

                # 상태 배지 (작은 컬러 박스)
                sc = {'활성': (39,174,96), '신규활성': (230,126,34), '비활성': (127,140,141)}
                status_color = sc.get(status, (100,100,100))
                badge_y = pdf.get_y()
                badge_x = pdf.l_margin

                # 상태 배지 배경
                pdf.set_font('KR', 'B', 7)
                status_w = pdf.get_string_width(status) + 4
                pdf.set_fill_color(*status_color)
                pdf.rect(badge_x, badge_y + 0.5, status_w, 4.5, 'F')
                pdf.set_text_color(255, 255, 255)
                pdf.set_xy(badge_x, badge_y + 0.3)
                pdf.cell(status_w, 5, f' {status} ', align='C')

                # commodity + type 텍스트
                pdf.set_xy(badge_x + status_w + 2, badge_y)
                pdf.set_font('KR', 'B', 9)
                pdf.set_text_color(*pdf.C_DARK_BLUE)
                pdf.cell(0, 5, f'{commodity} — {dt_label}{new_mark}', new_x='LMARGIN', new_y='NEXT')

                pdf.set_font('KR', '', 8)
                pdf.set_text_color(100, 100, 100)
                if path:
                    pdf.set_x(pdf.l_margin + 3)
                    pdf.multi_cell(0, 4, f'  {path}')
                pdf.ln(1.5)

        # ══════════════════════════════════════════════════
        # 5. 국내 산업간 전파경로 (part_b cascades)
        # ══════════════════════════════════════════════════
        cascades = scenario.get('part_b', {}).get('cascades', [])
        if cascades:
            pdf._section_title(5, '국내 산업간 전파경로')
            for cas in cascades:
                name = clean_text(cas.get('name', ''))
                is_new = cas.get('is_new', False)
                new_mark = ' [신규]' if is_new else ''

                # cascade 이름
                pdf.set_font('KR', 'B', 9)
                pdf.set_text_color(*pdf.C_DARK_BLUE)
                pdf.cell(0, 5, f'{name}{new_mark}', new_x='LMARGIN', new_y='NEXT')

                # steps
                steps = cas.get('steps', [])
                for step in steps:
                    frm = clean_text(step.get('from', ''))
                    to = clean_text(step.get('to', ''))
                    mech = clean_text(step.get('mechanism', ''))
                    lag = step.get('lag', '')

                    pdf.set_font('KR', 'B', 8)
                    pdf.set_text_color(100, 100, 100)
                    pdf.set_x(pdf.l_margin + 3)
                    pdf.cell(0, 4, f'{frm} > {to}  (시차: {lag})', new_x='LMARGIN', new_y='NEXT')

                    if mech:
                        pdf.set_font('KR', '', 8)
                        pdf.set_text_color(80, 80, 80)
                        pdf.set_x(pdf.l_margin + 5)
                        pdf.multi_cell(0, 3.8, mech)
                pdf.ln(2)

        # ══════════════════════════════════════════════════
        # 6. 산업별 영향 매트릭스
        # ══════════════════════════════════════════════════
        matrix = scenario.get('part_d', {}).get('matrix', [])
        if matrix:
            pdf._section_title(6, '산업별 영향 매트릭스')
            headers_m = ['산업', '방향', '초기', '중기', '장기', '변화']
            widths_m = [30, 18, 18, 18, 18, 12]
            rows_m = []
            for row in matrix:
                dir_sym = _DIR_MAP.get(row.get('direction', ''), '?')
                chg_sym = _CHG_MAP.get(row.get('change', ''), row.get('change', '—'))
                rows_m.append([
                    clean_sector(row.get('sector', '?'))[:20],
                    f"{dir_sym} {row.get('direction', '')}",
                    row.get('initial', '?'),
                    row.get('mid', '?'),
                    row.get('long', '?'),
                    chg_sym
                ])
            pdf._mini_table(headers_m, rows_m, widths_m)

            # 전파경로 상세 (매트릭스 아래)
            pdf._sub_heading('산업별 전파경로 상세')
            for row in matrix:
                pathway = clean_text(row.get('pathway', ''))
                if pathway:
                    sector = clean_sector(row.get('sector', '?'))
                    pdf.set_font('KR', 'B', 8)
                    pdf.set_text_color(*pdf.C_ACCENT)
                    pdf.cell(0, 4, f'{sector}:', new_x='LMARGIN', new_y='NEXT')
                    pdf.set_font('KR', '', 8)
                    pdf.set_text_color(80, 80, 80)
                    pdf.set_x(pdf.l_margin + 3)
                    pdf.multi_cell(0, 3.8, pathway)
                    pdf.ln(1)

        # ══════════════════════════════════════════════════
        # 7. 취약점 진단 및 모니터링 권고
        # ══════════════════════════════════════════════════
        part_e = scenario.get('part_e', {})
        vulns = part_e.get('vulnerabilities', [])
        recs = part_e.get('monitoring_recommendations', [])
        wps = header.get('watchpoints', [])

        if vulns or recs or wps:
            pdf._section_title(7, '취약점 진단 및 모니터링 권고')

            if wps:
                pdf._sub_heading('향후 주시 포인트')
                for w in wps:
                    horizon = w.get('horizon', '')
                    point = clean_text(w.get('point', ''))
                    pdf._bullet(f'[{horizon}] {point}', size=8, indent=5)
                pdf.ln(2)

            if vulns:
                pdf._sub_heading('공급망 취약점')
                for v in vulns:
                    pdf._bullet(clean_text(v), size=8, indent=5)
                pdf.ln(2)

            if recs:
                pdf._sub_heading('모니터링 권고')
                for r in recs:
                    pdf._bullet(clean_text(r), size=8, indent=5)

        # ── 파일 저장 ──
        # ⚠ 파일명은 HTML 이 거는 링크와 반드시 일치해야 한다.
        #    HTML(_pdf_url): f'pdf/KMI_Global_SC_AI_Weekly_Report({_pdf_date}).pdf'
        #    포팅 과정에서 PDF 파일명만 weekly_report_W{nn}_{YYYYMMDD}.pdf 로 바꾸고
        #    링크는 그대로 두어 W34 PDF 가 404 가 되었다(2026-08-24).
        #    과거 발행분이 모두 옛 규칙으로 올라가 있으므로 링크가 아닌 파일명을 되돌린다.
        fname = f'KMI_Global_SC_AI_Weekly_Report({date_str}).pdf'
        fpath = os.path.join(OUTPUT_DIR, fname)
        pdf.output(fpath)

        # ── 보호 설정 (인쇄만 허용, 수정·복사 금지) ──
        #    대외 배포물이므로 보호 적용은 선택이 아니다.
        #    보호되지 않은 PDF가 배포되는 것을 막기 위해 실패 시 조용히 넘기지 않고 중단한다.
        try:
            import pikepdf
        except ImportError:
            raise RuntimeError(
                "pikepdf \ubbf8\uc124\uce58 \u2014 PDF \ubcf4\ud638(\uc218\uc815\u00b7\ubcf5\uc0ac \uae08\uc9c0)\ub97c \uc801\uc6a9\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4. "
                "\ubcf4\ud638\ub418\uc9c0 \uc54a\uc740 PDF\uc758 \ubc30\ud3ec\ub97c \ub9c9\uae30 \uc704\ud574 \uc911\ub2e8\ud569\ub2c8\ub2e4. "
                "requirements.txt \uc758 pikepdf \uc124\uce58\ub97c \ud655\uc778\ud558\uc138\uc694."
            )
        try:
            with pikepdf.open(fpath, allow_overwriting_input=True) as src:
                src.save(fpath,
                         encryption=pikepdf.Encryption(
                             owner='kmi_admin_2026',
                             user='',                    # 열람 비밀번호 없음 (누구나 열 수 있음)
                             allow=pikepdf.Permissions(
                                 print_lowres=True,
                                 print_highres=True,
                                 modify_annotation=False,
                                 modify_assembly=False,
                                 modify_form=False,
                                 modify_other=False,
                                 extract=False,
                             ),
                         ))
            print("    \u2713 PDF \ubcf4\ud638 \uc801\uc6a9 \uc644\ub8cc (\uc778\uc1c4\ub9cc \ud5c8\uc6a9)")
        except Exception as e:
            raise RuntimeError(
                f"PDF \ubcf4\ud638 \uc124\uc815 \uc2e4\ud328: {e} \u2014 "
                "\ubcf4\ud638\ub418\uc9c0 \uc54a\uc740 PDF\uc758 \ubc30\ud3ec\ub97c \ub9c9\uae30 \uc704\ud574 \uc911\ub2e8\ud569\ub2c8\ub2e4"
            )

        return fpath


    # ══════════════════════════════════════════════════════════════

    # ── 드라이버: 단일 대상 주(target_scenario)에 대해서만 PDF 생성 ──
    if not FONT_REG:
        print("❌ 한국어 폰트를 찾을 수 없어 PDF를 생성할 수 없습니다.")
        return None

    fpath = generate_pdf(target_scenario, FONT_REG, FONT_BOLD)
    return fpath

def main():
    args = parse_args()
    week_tag = args.week_tag

    if not re.fullmatch(r"\d{8}", week_tag):
        print(f"❌ WEEK_TAG 형식 오류 (YYYYMMDD 8자리 필요): {week_tag}")
        sys.exit(1)

    os.chdir(BASE_DIR)  # 노트북 원본 코드가 상대경로 기준이므로 작업 디렉토리 고정
    DOCS_DIR.mkdir(exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"  주간 리포트 재생성 (LLM 재호출 없음) — WEEK_TAG={week_tag}")
    print("=" * 60)

    if not RESULT_FILE.exists():
        print(f"❌ {RESULT_FILE} 없음")
        sys.exit(1)

    with open(RESULT_FILE, encoding="utf-8") as f:
        all_scenarios = json.load(f)
    print(f"📂 scenario_results.json 로드: {len(all_scenarios)}주")

    target_scenario = find_scenario(all_scenarios, week_tag)
    if target_scenario is None:
        print(f"❌ scenario_results.json 에서 WEEK_TAG={week_tag} 에 해당하는 주를 찾을 수 없습니다.")
        sys.exit(1)
    print(f"✅ 대상 주 확인: {target_scenario.get('period', '?')}")

    # ──────────────────────────────────────────────────────────
    # 1) HTML 재생성 (scenario_generator_v11.ipynb Part 5 / Cell 14)
    # ──────────────────────────────────────────────────────────
    print("\n📄 [1/2] weekly_report.html 재생성 중...")
    _run_html_generation()

    _root_html = BASE_DIR / "weekly_report.html"
    if not _root_html.exists():
        print("❌ [1/2] weekly_report.html 생성 실패 (파일이 생성되지 않음)")
        sys.exit(1)
    shutil.copy2(_root_html, DOCS_DIR / "weekly_report.html")
    print(f"✅ [1/2] {DOCS_DIR / 'weekly_report.html'} 재생성 완료")

    # ──────────────────────────────────────────────────────────
    # 2) PDF 재생성 (scenario_generator_v11.ipynb Part 6 / Cell 16)
    # ──────────────────────────────────────────────────────────
    print(f"\n📑 [2/2] PDF 재생성 중 (WEEK_TAG={week_tag})...")
    pdf_path = _run_pdf_generation(target_scenario)
    if not pdf_path:
        print("❌ [2/2] PDF 재생성 실패")
        sys.exit(1)
    print(f"✅ [2/2] {pdf_path} 재생성 완료")

    print("\n" + "=" * 60)
    print("  🎉 재생성 완료")
    print("=" * 60)


if __name__ == "__main__":
    main()
