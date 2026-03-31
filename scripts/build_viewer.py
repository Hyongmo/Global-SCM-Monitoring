#!/usr/bin/env python3
"""
build_viewer.py
===============
monitoring/*/daily_report_llm_*.json 을 읽어
docs/daily_brief.html 을 빌드 (GitHub Pages 퍼블리시용)

호출 방식:
    python scripts/build_viewer.py [--days N]   # 기본 최근 30일
"""

import os, json, glob, sys
from datetime import datetime

# ─── 경로 설정 ───────────────────────────────────────────────
MONITOR_DIR = 'monitoring'
DOCS_DIR    = 'docs'
VIEWER_PATH = os.path.join(DOCS_DIR, 'daily_brief.html')

# ─── 최대 표시 일수 ───────────────────────────────────────────
MAX_DAYS = 30
if '--days' in sys.argv:
    idx = sys.argv.index('--days')
    try:
        MAX_DAYS = int(sys.argv[idx + 1])
    except (IndexError, ValueError):
        pass

os.makedirs(DOCS_DIR, exist_ok=True)

# ─── 카테고리 한국어 레이블 ────────────────────────────────────
CAT_KR_MAP = {
    "1_Security":       "안보·군사",
    "2_Safety":         "해양안전",
    "3_Freight":        "운임·물류비",
    "4_PortCargo":      "항만·화물",
    "5_EconFinance":    "경제·금융",
    "6_Seafood":        "수산물",
    "7_Shipping":       "해운",
    "8_Logistics":      "물류",
    "9_PortCongestion": "항만혼잡",
    "10_OtherIndustry": "기타 산업",
}
CAT_ORDER = list(CAT_KR_MAP.keys())

# ─── JSON 로드 ─────────────────────────────────────────────────
pattern    = os.path.join(MONITOR_DIR, '????????', 'daily_report_llm_????????.json')
json_files = sorted(glob.glob(pattern), reverse=True)

if not json_files:
    print(f"❌ JSON 없음 — {pattern} 에 해당하는 파일이 없습니다.")
    sys.exit(0)

print(f"📂 발견된 JSON: {len(json_files)}개")

days = []
for jf in json_files[:MAX_DAYS]:
    try:
        with open(jf, encoding='utf-8') as f:
            raw = json.load(f)
        # stats 필드 정규화 (collect_daily.py 출력 구조 → 뷰어용 플랫 구조)
        stats = raw.get('stats', {})
        n_gdelt = stats.get('gdelt_total', 0)
        n_naver = stats.get('naver_total', 0)
        n_high  = stats.get('gdelt_high', 0) + stats.get('naver_high', 0)
        n_med   = stats.get('gdelt_med', 0)  + stats.get('naver_med',  0)
        # med 필드가 없을 경우 llm_result 에서 추정
        llm = raw.get('llm_result', {})
        days.append({
            'date':    raw.get('date', '?'),
            'n_total': n_gdelt + n_naver,
            'n_high':  n_high,
            'n_med':   n_med,
            'llm_result': llm,
        })
        print(f"   ✓ {raw.get('date','?')}  (HIGH {n_high}, MED {n_med})")
    except Exception as e:
        print(f"⚠ 로드 실패 ({jf}): {e}")

if not days:
    print("❌ 유효한 JSON 없음")
    sys.exit(0)


# ─── HTML 렌더링 헬퍼 ─────────────────────────────────────────

def _esc(s):
    """HTML 이스케이프 (XSS 방지)"""
    return (s or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')


def _flow_block(label, data):
    if not data:
        return ''
    ov = _esc((data.get('overseas') or '').strip())
    ki = _esc((data.get('korea_impact') or '').strip())
    if not ov and not ki:
        return ''
    html  = f'<div class="flow-item"><div class="flow-label">{label}</div>'
    if ov:
        html += f'<div class="flow-sub"><span class="tag tag-intl">🌐 해외</span><p>{ov}</p></div>'
    if ki:
        html += f'<div class="flow-sub"><span class="tag tag-dom">🇰🇷 국내</span><p>{ki}</p></div>'
    html += '</div>'
    return html


def _changes_html(changes):
    """new / escalated / resolved 변화 블록"""
    new_items = changes.get('new', []) or []
    esc_items = changes.get('escalated', []) or []
    res_items = changes.get('resolved', []) or []

    def _item_text(x):
        if isinstance(x, dict):
            issue  = _esc(x.get('issue', ''))
            detail = _esc(x.get('detail', ''))
            return f'{issue} — {detail}' if detail else issue
        return _esc(str(x))

    html = ''
    if new_items:
        html += '<div class="chg-group"><span class="chg-label new">신규 ↑</span><ul>'
        for x in new_items:
            html += f'<li>{_item_text(x)}</li>'
        html += '</ul></div>'
    if esc_items:
        html += '<div class="chg-group"><span class="chg-label esc">심화 ▲</span><ul>'
        for x in esc_items:
            html += f'<li>{_item_text(x)}</li>'
        html += '</ul></div>'
    if res_items:
        html += '<div class="chg-group"><span class="chg-label res">완화 ↓</span><ul>'
        for x in res_items:
            html += f'<li>{_item_text(x)}</li>'
        html += '</ul></div>'
    return html or '<p class="empty">전일 대비 주요 변화 없음</p>'


def _alert_badge(level):
    colors = {
        'CRISIS':  ('#c0392b', '#fadbd8'),
        'WARNING': ('#e67e22', '#fdebd0'),
        'CAUTION': ('#f39c12', '#fef9e7'),
        'NORMAL':  ('#27ae60', '#d5f5e3'),
    }
    fg, bg = colors.get(level, ('#7f8c8d', '#ecf0f1'))
    labels = {'CRISIS':'🚨 위기','WARNING':'⚠️ 경보','CAUTION':'🟡 주의','NORMAL':'🟢 정상'}
    label  = labels.get(level, level)
    return f'<span class="alert-badge" style="background:{bg};color:{fg};">{label}</span>'


def _render_day(d, idx):
    date_str   = d.get('date', '?')
    llm        = d.get('llm_result', {})
    exec_s     = _esc((llm.get('executive_summary') or '').strip())
    alert_lvl  = (llm.get('alert_level') or 'NORMAL').upper()
    outlook    = _esc((llm.get('outlook') or '').strip())
    flow       = llm.get('flow', {})
    changes    = llm.get('changes', {}) or {}
    cats       = llm.get('categories', {}) or {}

    # ── 공급망 이슈 (flow 구조가 있으면 사용, 없으면 categories 기반 fallback) ──
    triggers   = flow.get('triggers', {}) if flow else {}
    trig_html  = ''
    trig_html += _flow_block('경로(ROUTE) 이슈',     triggers.get('route', {}))
    trig_html += _flow_block('공급원(SOURCE) 이슈',  triggers.get('source', {}))
    trig_html += _flow_block('물류(LOGISTICS) 이슈', triggers.get('logistics', {}))
    if not trig_html:
        # categories fallback: Security + Freight + Shipping
        for ckey in ['1_Security', '3_Freight', '7_Shipping']:
            trig_html += _flow_block(CAT_KR_MAP.get(ckey, ckey), cats.get(ckey) or {})
    if not trig_html:
        trig_html = '<p class="empty">오늘 주요 공급망 이슈 없음</p>'

    # ── 국내 산업 영향 (flow.domestic_impact 또는 categories fallback) ──
    dom_impact = flow.get('domestic_impact', {}) if flow else {}
    dom_html   = ''
    if dom_impact:
        for sector, text in dom_impact.items():
            t = _esc((text or '').strip())
            if t:
                dom_html += (
                    f'<div class="dom-item">'
                    f'<div class="dom-label">{_esc(sector)}</div>'
                    f'<p>{t}</p></div>'
                )
    else:
        # fallback: EconFinance + PortCargo + OtherIndustry
        for ckey in ['5_EconFinance', '4_PortCargo', '10_OtherIndustry']:
            cdata = cats.get(ckey) or {}
            ki    = _esc((cdata.get('korea_impact') or '').strip())
            if ki:
                label = CAT_KR_MAP.get(ckey, ckey)
                dom_html += (
                    f'<div class="dom-item">'
                    f'<div class="dom-label">{label}</div>'
                    f'<p>{ki}</p></div>'
                )
    if not dom_html:
        dom_html = '<p class="empty">오늘 주요 국내 산업 영향 없음</p>'

    # ── 카테고리별 분석 ──
    cat_html = ''
    for cat_key in CAT_ORDER:
        cat_data = cats.get(cat_key) or {}
        ov = _esc((cat_data.get('overseas') or '').strip())
        ki = _esc((cat_data.get('korea_impact') or '').strip())
        if not ov and not ki:
            continue
        label     = CAT_KR_MAP.get(cat_key, cat_key)
        cat_html += f'<div class="flow-item"><div class="flow-label">{label}</div>'
        if ov:
            cat_html += f'<div class="flow-sub"><span class="tag tag-intl">🌐 해외</span><p>{ov}</p></div>'
        if ki:
            cat_html += f'<div class="flow-sub"><span class="tag tag-dom">🇰🇷 국내</span><p>{ki}</p></div>'
        cat_html += '</div>'
    if not cat_html:
        cat_html = '<p class="empty">카테고리 분석 없음</p>'

    # ── 전망 ──
    outlook_html = (
        f'<div class="section"><div class="section-title">🔭 전망</div>'
        f'<p class="exec-text">{outlook}</p></div>'
    ) if outlook else ''

    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M KST')

    return f"""
<div class="day-block" id="day_{idx}">
  <div class="day-header">
    <div class="header-row">
      <h1>🚢 글로벌 공급망 AI 데일리 브리핑 — {date_str}</h1>
      {_alert_badge(alert_lvl)}
    </div>
    <div class="meta">KMI 해양수산 AX 지원단 (hmjeon@kmi.re.kr) &nbsp;|&nbsp; 생성: {generated_at}</div>
  </div>
  <div class="day-content">
    <div class="section">
      <div class="section-title">📌 핵심 요약</div>
      <p class="exec-text">{exec_s if exec_s else '<span class="empty">요약 없음</span>'}</p>
    </div>
    <div class="section">
      <div class="section-title">🌐 공급망 이슈</div>
      {trig_html}
    </div>
    <div class="section">
      <div class="section-title">🇰🇷 국내 산업 영향</div>
      {dom_html}
    </div>
    <div class="section">
      <div class="section-title">📊 전일 대비 변화</div>
      {_changes_html(changes)}
    </div>
    <div class="section">
      <div class="section-title">🗂 카테고리별 분석</div>
      {cat_html}
    </div>
    {outlook_html}
  </div>
</div>"""


# ─── 탭 CSS + 메뉴 HTML 생성 ──────────────────────────────────

n = len(days)

tab_css = '\n'.join(
    f'#tab_{i}:checked ~ .sidebar label[for="tab_{i}"] {{ background:#3498db; color:white; }}\n'
    f'#tab_{i}:checked ~ .main #day_{i} {{ display:block; }}'
    for i in range(n)
)

radio_inputs = '\n'.join(
    f'<input type="radio" name="daytab" id="tab_{i}" hidden{" checked" if i == 0 else ""}>'
    for i in range(n)
)

alert_dot = {'CRISIS':'🔴','WARNING':'🟠','CAUTION':'🟡','NORMAL':'🟢'}
menu_items = ''
for i, d in enumerate(days):
    lvl = (d.get('llm_result', {}).get('alert_level') or 'NORMAL').upper()
    dot = alert_dot.get(lvl, '⚪')
    menu_items += (
        f'<label class="menu-item" for="tab_{i}">'
        f'{dot} {d.get("date","?")}'
        f'<br><small style="color:#95a5a6;">HIGH {d.get("n_high",0)} / MED {d.get("n_med",0)}</small>'
        f'</label>\n'
    )

day_blocks = '\n'.join(_render_day(d, i) for i, d in enumerate(days))

generated_ts = datetime.now().strftime('%Y-%m-%d %H:%M UTC')

# ─── 전체 HTML 조립 ───────────────────────────────────────────

html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>글로벌 공급망 AI 데일리 모니터링 | KMI</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: 'Noto Sans KR','Apple SD Gothic Neo','Malgun Gothic',sans-serif;
       background:#f4f6f9; color:#2c3e50; }}

/* ─ 탭 라디오 버튼 숨기기 ─ */
input[type=radio][name=daytab] {{ display:none; }}

/* ─ 레이아웃 ─ */
.layout {{ display:flex; min-height:100vh; }}

/* ─ 사이드바 ─ */
.sidebar {{ width:200px; min-width:200px; background:#2c3e50; color:#ecf0f1;
           overflow-y:auto; position:sticky; top:0; height:100vh;
           align-self:flex-start; flex-shrink:0; }}
.sidebar-title {{ padding:14px 14px 10px; font-size:12px; font-weight:700;
                 border-bottom:1px solid #34495e; color:#bdc3c7; line-height:1.5; }}
.nav-link {{ display:block; padding:8px 14px; font-size:12px; font-weight:700;
            color:#3498db; border-bottom:1px solid #34495e; text-decoration:none;
            transition:.15s; }}
.nav-link:hover {{ background:#34495e; color:#5dade2; }}
.menu-item {{ display:block; padding:10px 14px; cursor:pointer; font-size:12px;
             line-height:1.5; border-bottom:1px solid #34495e; color:#ecf0f1;
             user-select:none; -webkit-user-select:none; transition:.15s; }}
.menu-item:hover {{ background:#34495e; }}

/* ─ 탭 활성화 CSS ─ */
{tab_css}

/* ─ 본문 ─ */
.main {{ flex:1; min-width:0; }}
.day-block {{ display:none; }}

/* ─ 헤더 ─ */
.day-header {{ background:#2c3e50; color:white; padding:22px 32px; }}
.header-row {{ display:flex; align-items:center; gap:14px; flex-wrap:wrap; margin-bottom:6px; }}
.day-header h1 {{ font-size:1.3em; font-weight:700; }}
.day-header .meta {{ font-size:0.82em; color:#aab; }}
.alert-badge {{ font-size:0.82em; font-weight:700; padding:3px 12px;
               border-radius:20px; white-space:nowrap; }}

/* ─ 콘텐츠 ─ */
.day-content {{ max-width:960px; margin:0 auto; padding:24px 16px; }}
.section {{ background:white; border-radius:8px; padding:20px 24px;
           margin-bottom:16px; box-shadow:0 1px 4px rgba(0,0,0,.08); }}
.section-title {{ font-size:1.03em; font-weight:700; color:#2c3e50;
                 margin-bottom:14px; padding-bottom:8px;
                 border-bottom:2px solid #eef0f3; }}
.exec-text {{ line-height:1.8; font-size:0.95em; color:#34495e; }}

/* ─ 플로우 블록 ─ */
.flow-item {{ margin-bottom:18px; padding-bottom:14px; border-bottom:1px solid #f0f0f0; }}
.flow-item:last-child {{ border-bottom:none; margin-bottom:0; }}
.flow-label {{ font-weight:700; font-size:0.88em; color:#2980b9;
              margin-bottom:8px; letter-spacing:.3px; }}
.flow-sub {{ display:flex; gap:10px; margin-bottom:6px; align-items:flex-start; }}
.flow-sub p {{ font-size:0.9em; line-height:1.7; color:#444; flex:1; }}
.tag {{ font-size:0.78em; font-weight:700; padding:2px 8px; border-radius:4px;
       white-space:nowrap; margin-top:2px; }}
.tag-intl {{ background:#ebf5fb; color:#2980b9; }}
.tag-dom  {{ background:#eafaf1; color:#27ae60; }}

/* ─ 국내 영향 ─ */
.dom-item {{ margin-bottom:14px; padding-bottom:10px; border-bottom:1px solid #f0f0f0; }}
.dom-item:last-child {{ border-bottom:none; margin-bottom:0; }}
.dom-label {{ font-weight:700; font-size:0.88em; color:#8e44ad; margin-bottom:4px; }}
.dom-item p {{ font-size:0.9em; line-height:1.7; color:#444; }}

/* ─ 변화 블록 ─ */
.chg-group {{ margin-bottom:10px; }}
.chg-label {{ display:inline-block; font-size:0.8em; font-weight:700;
             padding:2px 10px; border-radius:4px; margin-bottom:4px; }}
.chg-label.new {{ background:#fdebd0; color:#e67e22; }}
.chg-label.esc {{ background:#fadbd8; color:#c0392b; }}
.chg-label.res {{ background:#d5f5e3; color:#27ae60; }}
.chg-group ul {{ padding-left:18px; }}
.chg-group li {{ font-size:0.9em; line-height:1.7; color:#444; margin-bottom:2px; }}

/* ─ 푸터 ─ */
.empty {{ color:#999; font-size:0.88em; font-style:italic; }}
.footer {{ text-align:center; padding:24px; font-size:0.78em; color:#aaa; }}

/* ─ 모바일 ─ */
@media (max-width:768px) {{
  .layout {{ flex-direction:column; }}
  .sidebar {{ width:100%; min-width:0; height:auto; position:sticky; top:0; z-index:100;
    display:flex; flex-wrap:nowrap; overflow-x:auto; padding:6px 8px; gap:4px; }}
  .sidebar-title {{ display:none; }}
  .menu-item {{ flex:0 0 auto; border-radius:4px; padding:4px 10px;
    border-bottom:none; white-space:nowrap; font-size:11px; }}
  .nav-link {{ flex:0 0 auto; border-radius:4px; padding:4px 10px;
    border-bottom:none; white-space:nowrap; font-size:11px; margin-bottom:0; }}
  .day-content {{ padding:12px; }}
  .header-row {{ flex-direction:column; align-items:flex-start; gap:6px; }}
}}
</style>
</head>
<body>
<div class="layout">
{radio_inputs}
<div class="sidebar">
  <div class="sidebar-title">📋 KMI<br>AI 데일리<br>모니터링</div>
  <a class="nav-link" href="https://hyongmo.github.io/Global-SCM-Monitoring/weekly_report.html">📊 주간 리포트 →</a>
{menu_items}
</div>
<div class="main">
{day_blocks}
<div class="footer">
  KMI 해양수산 AX 지원단 &nbsp;|&nbsp; 생성: {generated_ts}<br>
  본 브리핑은 온톨로지 기반 전문가 지식 그래프와 국내외 기사를 기반으로 생성형 AI가 작성한 것으로 KMI의 공식 의견이 아님을 밝힙니다.
</div>
</div>
</div>
</body>
</html>"""

with open(VIEWER_PATH, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"✅ 뷰어 저장: {VIEWER_PATH}")
print(f"   날짜 수: {n}개  ({days[-1].get('date','?')} ~ {days[0].get('date','?')})")
