#!/usr/bin/env python3
"""
build_viewer.py
===============
monitoring/*/daily_report_llm_*.json 을 읽어
docs/index.html 을 빌드 (GitHub Pages 퍼블리시용)

호출 방식:
    python scripts/build_viewer.py [--days N]   # 기본 최근 30일
"""

import os, json, glob, sys
from datetime import datetime, timedelta

# ─── 경로 설정 ───────────────────────────────────────────────
MONITOR_DIR = 'monitoring'
DOCS_DIR    = 'docs'
VIEWER_PATH = os.path.join(DOCS_DIR, 'index.html')

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
        # v3 notebook flat 구조
        llm = raw.get('llm_result', {})
        days.append({
            'date':    raw.get('date', '?'),
            'n_total': raw.get('n_total', 0),
            'n_high':  raw.get('n_high',  0),
            'n_med':   raw.get('n_med',   0),
            'llm_result': llm,
            'sources':    raw.get('sources', {}),
        })
        print(f"   ✓ {raw.get('date','?')}  (HIGH {raw.get('n_high',0)}, MED {raw.get('n_med',0)})")
    except Exception as e:
        print(f"⚠ 로드 실패 ({jf}): {e}")

if not days:
    print("❌ 유효한 JSON 없음")
    sys.exit(0)


# ─── HTML 렌더링 헬퍼 ─────────────────────────────────────────

def _flow_block(label, data):
    if not data: return ''
    ov = (data.get('overseas') or '').strip()
    ki = (data.get('korea_impact') or '').strip()
    if not ov and not ki: return ''
    html  = f'<div class="flow-item"><div class="flow-label">{label}</div>'
    if ov: html += f'<div class="flow-sub"><span class="tag tag-intl">🌐 해외</span><p>{ov}</p></div>'
    if ki: html += f'<div class="flow-sub"><span class="tag tag-dom">🇰🇷 국내</span><p>{ki}</p></div>'
    html += '</div>'
    return html


def _changes_html(changes):
    new_items = changes.get('new', []) or []
    esc_items = changes.get('escalated', []) or []
    res_items = changes.get('resolved', []) or []
    html = ''
    if new_items:
        html += '<div class="chg-group"><span class="chg-label new">신규 ↑</span><ul>'
        for x in new_items: html += f'<li>{x}</li>'
        html += '</ul></div>'
    if esc_items:
        html += '<div class="chg-group"><span class="chg-label esc">심화 ▲</span><ul>'
        for x in esc_items: html += f'<li>{x}</li>'
        html += '</ul></div>'
    if res_items:
        html += '<div class="chg-group"><span class="chg-label res">완화 ↓</span><ul>'
        for x in res_items: html += f'<li>{x}</li>'
        html += '</ul></div>'
    return html or '<p class="empty">전일 대비 주요 변화 없음</p>'


def _sources_html(articles):
    """카테고리별 참조 기사 접이식 목록 렌더링"""
    if not articles:
        return ''
    intl = [a for a in articles if a.get('type') == 'intl']
    dom  = [a for a in articles if a.get('type') == 'dom']
    html = '<details class="src-details"><summary class="src-summary">📰 참조 기사 ('
    html += f'{len(articles)}건)</summary><div class="src-list">'
    if intl:
        html += '<div class="src-group-label">해외</div><ul>'
        for a in intl:
            title = a.get('title', '')
            url   = a.get('url', '')
            if url:
                html += f'<li><a href="{url}" target="_blank" rel="noopener">{title}</a></li>'
            else:
                html += f'<li>{title}</li>'
        html += '</ul>'
    if dom:
        html += '<div class="src-group-label">국내</div><ul>'
        for a in dom:
            title = a.get('title', '')
            url   = a.get('url', '')
            if url:
                html += f'<li><a href="{url}" target="_blank" rel="noopener">{title}</a></li>'
            else:
                html += f'<li>{title}</li>'
        html += '</ul>'
    html += '</div></details>'
    return html


def _render_day(d, idx):
    date_str   = d.get('date', '?')
    llm        = d.get('llm_result', {})
    exec_s     = llm.get('executive_summary', '').strip()
    flow       = llm.get('flow', {})
    changes    = llm.get('changes', {}) or {}
    cats       = llm.get('categories', {}) or {}
    sources    = d.get('sources', {}) or {}

    # ── 공급망 이슈 ──
    triggers   = flow.get('triggers', {}) if flow else {}
    trig_html  = ''
    trig_html += _flow_block('경로(ROUTE) 이슈',     triggers.get('route', {}))
    trig_html += _flow_block('공급원(SOURCE) 이슈',  triggers.get('source', {}))
    trig_html += _flow_block('물류(LOGISTICS) 이슈', triggers.get('logistics', {}))
    if not trig_html:
        trig_html = '<p class="empty">오늘 주요 국외 트리거 없음</p>'

    # ── 국내 산업 영향 ──
    dom_impact = flow.get('domestic_impact', {}) if flow else {}
    dom_html   = ''
    for sector, text in dom_impact.items():
        t = (text or '').strip()
        if t:
            dom_html += (
                f'<div class="dom-item">'
                f'<div class="dom-label">{sector}</div>'
                f'<p>{t}</p></div>'
            )
    if not dom_html:
        dom_html = '<p class="empty">오늘 주요 국내 산업 영향 없음</p>'

    # ── 카테고리별 분석 ──
    cat_html = ''
    for cat_key, cat_data in cats.items():
        if not cat_data: continue
        ov = (cat_data.get('overseas') or '').strip()
        ki = (cat_data.get('korea_impact') or '').strip()
        if not ov and not ki: continue
        label     = CAT_KR_MAP.get(cat_key, cat_key)
        cat_html += f'<div class="flow-item"><div class="flow-label">{label}</div>'
        if ov: cat_html += f'<div class="flow-sub"><span class="tag tag-intl">🌐 해외</span><p>{ov}</p></div>'
        if ki: cat_html += f'<div class="flow-sub"><span class="tag tag-dom">🇰🇷 국내</span><p>{ki}</p></div>'
        cat_html += _sources_html(sources.get(cat_key, []))
        cat_html += '</div>'
    if not cat_html:
        cat_html = '<p class="empty">카테고리 분석 없음</p>'

    WEEKDAYS = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일']
    try:
        _d = datetime.strptime(date_str, '%Y-%m-%d')
        # 기사 수집일 + 1일 = 게시일
        from datetime import timedelta
        _d_pub = _d + timedelta(days=1)
        pub_date_label = f"{_d_pub.year}.{_d_pub.month:02d}.{_d_pub.day:02d} ({WEEKDAYS[_d_pub.weekday()]})"
        collect_date_label = f"{_d.year}.{_d.month:02d}.{_d.day:02d}"
    except Exception:
        pub_date_label = date_str
        collect_date_label = date_str

    return f"""
<div class="day-block" id="day_{idx}">
  <div class="day-date-header">🗓️ {pub_date_label}<span style="font-size:12px; color:#666; margin-left:12px;">기사수집일: {collect_date_label}</span></div>
  <div class="day-content">
    <div class="section">
      <div class="section-title">📌 주요기사 요약</div>
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
      <div class="section-title">📊 어제 대비 변화</div>
      {_changes_html(changes)}
    </div>
    <div class="section">
      <div class="section-title">🗂 카테고리별 분석</div>
      {cat_html}
    </div>
  </div>
</div>"""


# ─── 탭 CSS + 메뉴 HTML 생성 ──────────────────────────────────

n = len(days)

tab_css = '\n'.join(
    f'#tab_{i}:checked ~ .sidebar label[for="tab_{i}"] {{ background:#3498db; }}\n'
    f'#tab_{i}:checked ~ .main #day_{i} {{ display:block; }}'
    for i in range(n)
)

radio_inputs = '\n'.join(
    f'<input type="radio" name="daytab" id="tab_{i}" hidden{" checked" if i == 0 else ""}>'
    for i in range(n)
)

_WDAYS = ['월', '화', '수', '목', '금', '토', '일']
def _fmt_menu_date(date_str):
    try:
        _d = datetime.strptime(date_str, '%Y-%m-%d')
        _d_pub = _d + timedelta(days=1)
        return f"{_d_pub.year}.{_d_pub.month:02d}.{_d_pub.day:02d} ({_WDAYS[_d_pub.weekday()]})"
    except Exception:
        return date_str

menu_items = ''
for i, d in enumerate(days):
    menu_items += (
        f'<label class="menu-item" for="tab_{i}">'
        f'{_fmt_menu_date(d.get("date","?"))}'
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
<title>글로벌 공급망 AI 일일 브리핑 | KMI</title>
<!-- Google Analytics (GA4) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-JEDV505PLS"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-JEDV505PLS');</script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family:'Noto Sans KR','Apple SD Gothic Neo',sans-serif;
       font-size:16px; background:#f5f6fa; color:#2c3e50; }}

/* ─ 레이아웃 ─ */
.container {{ display:flex; min-height:100vh; }}

/* ─ 사이드바 ─ */
.sidebar {{ width:180px; min-width:180px; background:#2c3e50; color:#ecf0f1;
           overflow-y:auto; padding:10px 0; position:sticky; top:0; height:100vh;
           align-self:flex-start; }}
.sidebar-title {{ padding:14px 14px 10px; font-size:12px; font-weight:bold; border-bottom:1px solid #2c3e50; color:#bdc3c7; line-height:1.4; }}
.nav-link {{ display:flex; align-items:center; justify-content:space-between;
            padding:9px 12px; font-size:12px; font-weight:600; color:#ecf0f1;
            text-decoration:none; border-radius:6px;
            background:rgba(52,152,219,0.15); border:1px solid rgba(52,152,219,0.3);
            margin-bottom:8px; }}
.nav-link:hover {{ background:#3498db; border-color:#3498db; color:#fff; }}
.menu-item {{ display:block; padding:8px 10px; cursor:pointer; font-size:12px;
             line-height:1.5; border-bottom:1px solid #34495e; color:#ecf0f1;
             user-select:none; -webkit-user-select:none; transition:.15s; }}
.menu-item:hover {{ background:#34495e; }}

/* ─ 탭 활성화 CSS ─ */
{tab_css}

/* ─ 리포트 헤더 ─ */
.report-header {{ background:#2c3e50; color:#ecf0f1; padding:20px 28px;
                display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }}
.report-header .rh-title {{ font-size:20px; font-weight:700; white-space:nowrap; }}
.report-header .rh-sub {{ font-size:13px; color:#bdc3c7; }}

/* ─ 본문 ─ */
.main {{ flex:1; overflow-y:visible; padding:20px 28px; min-width:0; }}
.day-block {{ display:none; }}
.day-date-header {{ font-size:22px; font-weight:700; color:#2c3e50;
                   padding:14px 0 10px; border-bottom:2px solid #e0e4ea; margin-bottom:16px; }}

/* ─ 콘텐츠 ─ */
.day-content {{ }}
.section {{ background:white; border-radius:8px; padding:20px 24px;
           margin-bottom:16px; box-shadow:0 1px 4px rgba(0,0,0,.08); }}
.section-title {{ font-size:1.05em; font-weight:700; color:#2c3e50;
                 margin-bottom:14px; padding-bottom:8px;
                 border-bottom:2px solid #eef0f3; }}
.exec-text {{ line-height:1.75; font-size:0.97em; color:#34495e; }}

/* ─ 플로우 블록 ─ */
.flow-item {{ margin-bottom:18px; padding-bottom:14px; border-bottom:1px solid #f0f0f0; }}
.flow-item:last-child {{ border-bottom:none; margin-bottom:0; }}
.flow-label {{ font-weight:700; font-size:0.9em; color:#2980b9;
              margin-bottom:8px; letter-spacing:0.3px; }}
.flow-sub {{ display:flex; gap:10px; margin-bottom:6px; align-items:flex-start; }}
.flow-sub p {{ font-size:0.9em; line-height:1.65; color:#444; flex:1; }}
.tag {{ font-size:0.78em; font-weight:700; padding:2px 8px; border-radius:4px;
       white-space:nowrap; margin-top:2px; }}
.tag-intl {{ background:#ebf5fb; color:#2980b9; }}
.tag-dom  {{ background:#eafaf1; color:#27ae60; }}

/* ─ 참조 기사 ─ */
.src-details {{ margin-top:8px; }}
.src-summary {{ font-size:0.82em; color:#7f8c8d; cursor:pointer; user-select:none; padding:4px 0; }}
.src-summary:hover {{ color:#2980b9; }}
.src-list {{ padding:6px 0 0 8px; }}
.src-list ul {{ margin:2px 0 8px 16px; padding:0; }}
.src-list li {{ font-size:0.82em; line-height:1.55; color:#555; margin-bottom:3px; }}
.src-list a {{ color:#2980b9; text-decoration:none; }}
.src-list a:hover {{ text-decoration:underline; }}
.src-group-label {{ font-size:0.78em; font-weight:700; color:#95a5a6; margin-top:6px; margin-bottom:2px; }}

/* ─ 국내 영향 ─ */
.dom-item {{ margin-bottom:14px; padding-bottom:10px; border-bottom:1px solid #f0f0f0; }}
.dom-item:last-child {{ border-bottom:none; margin-bottom:0; }}
.dom-label {{ font-weight:700; font-size:0.88em; color:#8e44ad; margin-bottom:4px; }}
.dom-item p {{ font-size:0.9em; line-height:1.65; color:#444; }}

/* ─ 변화 블록 ─ */
.chg-group {{ margin-bottom:10px; }}
.chg-label {{ display:inline-block; font-size:0.8em; font-weight:700;
             padding:2px 10px; border-radius:4px; margin-bottom:4px; }}
.chg-label.new {{ background:#fdebd0; color:#e67e22; }}
.chg-label.esc {{ background:#fadbd8; color:#c0392b; }}
.chg-label.res {{ background:#d5f5e3; color:#27ae60; }}
.chg-group ul {{ padding-left:18px; }}
.chg-group li {{ font-size:0.9em; line-height:1.65; color:#444; margin-bottom:2px; }}

.empty {{ color:#999; font-size:0.88em; font-style:italic; }}

/* ─ 모바일 ─ */
@media (max-width:768px) {{
  .report-header {{ padding:8px 12px; }}
  .report-header .rh-title {{ font-size:16px; }}
  .report-header .rh-sub {{ font-size:12px; }}
}}
@media (max-width:768px) {{
  body {{ font-size:15px; }}
  .container {{ flex-direction:column; }}
  .sidebar {{ width:100%; min-width:0; height:auto; position:sticky; top:0; z-index:100;
    display:flex; flex-wrap:nowrap; overflow-x:auto; overflow-y:hidden;
    padding:6px 8px; gap:4px; -webkit-overflow-scrolling:touch; }}
  .menu-item {{ flex:0 0 auto; border-radius:4px; padding:4px 10px;
    border-bottom:none; border-right:1px solid #34495e; font-size:11px; white-space:nowrap; }}
  .nav-link {{ flex:0 0 auto; border-radius:4px; padding:4px 10px;
    border-bottom:none; white-space:nowrap; font-size:11px; margin-bottom:0; }}
  .main {{ overflow:visible; padding:12px 14px; }}
  .day-content {{ max-width:100%; }}
  .section {{ padding:14px 16px; }}
}}
body{{-webkit-user-select:none;-ms-user-select:none;user-select:none;}}input,textarea{{-webkit-user-select:text;user-select:text;}}
</style>
</head>
<body>
<div class="container">
{radio_inputs}
<div class="sidebar">
  <a class="nav-link" href="https://hyongmo.github.io/Global-SCM-Monitoring/weekly_report.html">Go To Weekly <span style="font-size:16px;line-height:1">›</span></a>
{menu_items}
</div>
<div class="main">
<div class="report-header">
  <span class="rh-title">🚢 글로벌 공급망 AI 일일 브리핑</span>
  <span class="rh-sub">한국해양수산개발원(KMI) 해양수산 AX 지원단 · hmjeon@kmi.re.kr</span>
</div>
<div style="background:#f0f4f8;border-left:4px solid #2980b9;padding:8px 14px;font-size:11px;color:#555;margin:10px 14px 0 14px">
  본 브리핑은 온톨로지 기반 전문가 지식 그래프와 국내외 기사를 기반으로 생성형 AI가 작성한 것으로 KMI의 공식 의견이 아님을 밝힙니다.</div>
{day_blocks}
</div>
</div>
<script>
(function(){{var a=document['querySelector']('.main');if(!a)return;a['style']['touchAction']='pan-y';var b=0x0;a['addEventListener']('touchstart',function(c){{b=c['changedTouches'][0x0]['clientX'];}},{{'passive':!![]}}),a['addEventListener']('touchend',function(c){{var d=b-c['changedTouches'][0x0]['clientX'];if(Math['abs'](d)<0x32)return;var f=document['querySelector']('input[type=radio][hidden]:checked');if(!f)return;var g=Array['from'](document['querySelectorAll']('input[type=radio][hidden][name=\\x27'+f['name']+'\\x27]')),h=g['indexOf'](f),i=d>0x0?h+0x1:h-0x1;if(i>=0x0&&i<g['length']){{var j=document['querySelector']('label[for=\\x22'+g[i]['id']+'\\x22]');j&&(j['click'](),window['scrollTo'](0x0,0x0));}}}},{{'passive':!![]}});}}());
</script>
<script>document.addEventListener('contextmenu',function(e){{e.preventDefault();}});document.addEventListener('keydown',function(e){{if((e.ctrlKey||e.metaKey)&&(e.key==='s'||e.key==='u')){{e.preventDefault();}}}});</script>
</body>
</html>"""

with open(VIEWER_PATH, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"✅ 뷰어 저장: {VIEWER_PATH}")
print(f"   날짜 수: {n}개  ({days[-1].get('date','?')} ~ {days[0].get('date','?')})")
