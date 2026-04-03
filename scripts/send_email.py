#!/usr/bin/env python3
"""
send_email.py
=============
일일 모니터링 산출물을 이메일로 발송합니다.

첨부 파일:
    - daily_report_YYYYMMDD.xlsx
    - daily_report_llm_YYYYMMDD.docx
    - daily_report_llm_YYYYMMDD.md
    - gdelt_mon_classified_daily_YYYYMMDD.csv
    - naver_mon_classified_daily_YYYYMMDD.csv

환경변수:
    KMI_SMTP_ADDRESS     발송 이메일 주소 (kmi@kmi.re.kr)
    KMI_SMTP_PASSWORD    메일 계정 비밀번호

호출:
    python scripts/send_email.py [YYYY-MM-DD]                            # 기본 수신자, Full (첨부포함)
    python scripts/send_email.py 2026-04-01 --to a@b.com c@d.com        # 수신자 지정, Full
    python scripts/send_email.py 2026-04-01 --brief --to a@b.com        # 수신자 지정, Brief (본문만)
    python scripts/send_email.py 2026-04-01 --brief                     # 기본 수신자, Brief
    python scripts/send_email.py 2026-04-01 --summary --to a@b.com     # 수신자 지정, Summary (요약만)
"""

import json, os, sys, smtplib
from datetime import datetime, timedelta
from dateutil.parser import parse as dateparse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# ── 인자 파싱 (날짜 + --to 수신자) ──
DEFAULT_RECIPIENTS = [
    'hmjeon@kmi.re.kr',
    'h.kim@kmi.re.kr',
]

args = sys.argv[1:]
TARGET_DATE = None
custom_recipients = []
brief_mode = False          # --brief: 첨부파일 없이 브리핑 본문만 발송
summary_mode = False        # --summary: 주요기사 요약만 발송 (첨부 없음)

i = 0
while i < len(args):
    if args[i] == '--to':
        custom_recipients = args[i+1:]
        break
    elif args[i] == '--brief':
        brief_mode = True
    elif args[i] == '--summary':
        summary_mode = True
    elif TARGET_DATE is None:
        TARGET_DATE = dateparse(args[i]).date()
    i += 1

if TARGET_DATE is None:
    TARGET_DATE = (datetime.now() - timedelta(days=1)).date()

RECIPIENTS = custom_recipients if custom_recipients else DEFAULT_RECIPIENTS

DATE_TAG = TARGET_DATE.strftime('%Y%m%d')
MONITOR_DIR = 'monitoring'
DAILY_DIR = os.path.join(MONITOR_DIR, DATE_TAG)

# ── 환경변수 ──
KMI_SMTP_ADDRESS = os.environ.get('KMI_SMTP_ADDRESS', '')
KMI_SMTP_PASSWORD = os.environ.get('KMI_SMTP_PASSWORD', '')

if not KMI_SMTP_ADDRESS or not KMI_SMTP_PASSWORD:
    print("⚠ KMI_SMTP_ADDRESS 또는 KMI_SMTP_PASSWORD 미설정 — 이메일 발송 건너뜀")
    sys.exit(0)

# ── SMTP 서버 설정 ──
SMTP_HOST = 'zmx124.mailplug.com'
SMTP_PORT = 465

# ── 카테고리 한글명 ──
CAT_KR = {
    "1_Security":"안보·군사","2_Safety":"해양안전","3_Freight":"운임·물류비",
    "4_PortCargo":"항만·화물","5_EconFinance":"경제·금융","6_Seafood":"수산물",
    "7_Shipping":"해운","8_Logistics":"물류","9_PortCongestion":"항만혼잡",
    "10_OtherIndustry":"기타 산업",
}

# ── JSON에서 전체 브리핑 추출 ──
json_path = os.path.join(DAILY_DIR, f'daily_report_llm_{DATE_TAG}.json')
summary = '(요약 없음)'
n_total = n_high = n_med = 0
llm = {}

if os.path.exists(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    llm = data.get('llm_result', {})
    summary = llm.get('executive_summary', '(요약 없음)')
    n_total = data.get('n_total', 0)
    n_high = data.get('n_high', 0)
    n_med = data.get('n_med', 0)


# ── HTML 렌더링 헬퍼 ──
_S = 'style'
_SEC = f'{_S}="background:#fff; border-radius:8px; padding:18px 22px; margin-bottom:14px; border:1px solid #e8eaed;"'
_SEC_TITLE = f'{_S}="font-size:15px; font-weight:700; color:#1a252f; border-bottom:2px solid #eef0f3; padding-bottom:6px; margin-bottom:10px;"'
_FLOW_LABEL = f'{_S}="font-weight:700; font-size:13px; color:#2980b9; margin:10px 0 4px;"'
_TAG_INTL = f'{_S}="display:inline-block; font-size:11px; font-weight:700; padding:1px 7px; border-radius:3px; background:#ebf5fb; color:#2980b9; margin-right:4px;"'
_TAG_DOM = f'{_S}="display:inline-block; font-size:11px; font-weight:700; padding:1px 7px; border-radius:3px; background:#eafaf1; color:#27ae60; margin-right:4px;"'
_P = f'{_S}="font-size:13px; line-height:1.65; color:#444; margin:2px 0 8px;"'
_DOM_LABEL = f'{_S}="font-weight:700; font-size:13px; color:#8e44ad; margin:8px 0 2px;"'
_CHG_NEW = f'{_S}="display:inline-block; font-size:11px; font-weight:700; padding:1px 8px; border-radius:3px; background:#fdebd0; color:#e67e22;"'
_CHG_ESC = f'{_S}="display:inline-block; font-size:11px; font-weight:700; padding:1px 8px; border-radius:3px; background:#fadbd8; color:#c0392b;"'
_CHG_RES = f'{_S}="display:inline-block; font-size:11px; font-weight:700; padding:1px 8px; border-radius:3px; background:#d5f5e3; color:#27ae60;"'


def _flow_html(label, data):
    if not data: return ''
    ov = (data.get('overseas') or '').strip()
    ki = (data.get('korea_impact') or '').strip()
    if not ov and not ki: return ''
    h = f'<div {_FLOW_LABEL}>{label}</div>'
    if ov: h += f'<span {_TAG_INTL}>해외</span><p {_P}>{ov}</p>'
    if ki: h += f'<span {_TAG_DOM}>국내</span><p {_P}>{ki}</p>'
    return h


def _build_full_briefing():
    """LLM JSON → 전체 브리핑 HTML"""
    flow = llm.get('flow', {})
    triggers = flow.get('triggers', {})
    dom_impact = flow.get('domestic_impact', {})
    changes = llm.get('changes', {})
    cats = llm.get('categories', {})

    # 공급망 이슈
    trig = ''
    trig += _flow_html('경로(ROUTE) 이슈', triggers.get('route', {}))
    trig += _flow_html('공급원(SOURCE) 이슈', triggers.get('source', {}))
    trig += _flow_html('물류(LOGISTICS) 이슈', triggers.get('logistics', {}))
    if not trig: trig = f'<p {_P} style="color:#999;">주요 국외 트리거 없음</p>'

    # 국내 산업 영향
    dom = ''
    for sector, text in dom_impact.items():
        t = (text or '').strip()
        if t: dom += f'<div {_DOM_LABEL}>{sector}</div><p {_P}>{t}</p>'
    if not dom: dom = f'<p {_P} style="color:#999;">주요 국내 산업 영향 없음</p>'

    # 어제 대비 변화
    chg = ''
    for items, label, style in [(changes.get('new',[]),'신규 ↑',_CHG_NEW),
                                 (changes.get('escalated',[]),'심화 ▲',_CHG_ESC),
                                 (changes.get('resolved',[]),'완화 ↓',_CHG_RES)]:
        if items:
            chg += f'<span {style}>{label}</span><ul style="padding-left:18px; margin:4px 0 10px;">'
            for x in items: chg += f'<li style="font-size:13px; line-height:1.6;">{x}</li>'
            chg += '</ul>'
    if not chg: chg = f'<p {_P} style="color:#999;">전일 대비 주요 변화 없음</p>'

    # 카테고리별 분석
    cat_html = ''
    for cat_key, cat_data in cats.items():
        if not cat_data: continue
        ov = (cat_data.get('overseas') or '').strip()
        ki = (cat_data.get('korea_impact') or '').strip()
        if not ov and not ki: continue
        label = CAT_KR.get(cat_key, cat_key)
        cat_html += f'<div {_FLOW_LABEL}>{label}</div>'
        if ov: cat_html += f'<span {_TAG_INTL}>해외</span><p {_P}>{ov}</p>'
        if ki: cat_html += f'<span {_TAG_DOM}>국내</span><p {_P}>{ki}</p>'
    if not cat_html: cat_html = f'<p {_P} style="color:#999;">카테고리 분석 없음</p>'

    return f"""
<div {_SEC}>
  <div {_SEC_TITLE}>🌐 공급망 이슈</div>
  {trig}
</div>
<div {_SEC}>
  <div {_SEC_TITLE}>🇰🇷 국내 산업 영향</div>
  {dom}
</div>
<div {_SEC}>
  <div {_SEC_TITLE}>📊 어제 대비 변화</div>
  {chg}
</div>
<div {_SEC}>
  <div {_SEC_TITLE}>🗂 카테고리별 분석</div>
  {cat_html}
</div>"""


briefing_html = _build_full_briefing() if llm else ''

# ── 이메일 본문 구성 ──
PUB_DATE = TARGET_DATE + timedelta(days=1)
subject = f'[KMI 글로벌 공급망 AI 일일 브리핑] {PUB_DATE} AI 브리핑'

# summary 모드: 폭 제한 없음 / full·brief 모드: 960px
_content_width = 'margin:0 auto; padding:20px 16px;'

# summary 모드: 주요기사 요약 + 뷰어 링크만 / brief·full: 전체 브리핑 포함
_body_sections = f"""
<div {_SEC}>
  <div {_SEC_TITLE}>📌 주요기사 요약</div>
  <p style="font-size:14px; line-height:1.75; color:#34495e; background:#f8f9fa; padding:12px 16px; border-left:4px solid #3498db; border-radius:4px;">
    {summary}
  </p>
</div>
""" if summary_mode else f"""
<div {_SEC}>
  <div {_SEC_TITLE}>📌 주요기사 요약</div>
  <p style="font-size:14px; line-height:1.75; color:#34495e; background:#f8f9fa; padding:12px 16px; border-left:4px solid #3498db; border-radius:4px;">
    {summary}
  </p>
</div>

{briefing_html}
"""

html_body = f"""\
<html>
<body style="font-family:'Apple SD Gothic Neo','Noto Sans KR',sans-serif; color:#2c3e50; line-height:1.6; background:#f4f6f9; margin:0; padding:0;">
<div style="background:#2c3e50; color:white; padding:22px 28px 16px;">
  <h1 style="font-size:20px; font-weight:700; margin:0 0 6px;">🚢 글로벌 공급망 AI 일일 브리핑</h1>
  <div style="font-size:13px; color:#ecf0f1; margin-bottom:6px;">{TARGET_DATE}</div>
  <div style="font-size:12px; color:#bdc3c7; margin-bottom:10px;">한국해양수산개발원(KMI) 해양수산 AX 지원단 · hmjeon@kmi.re.kr</div>
  <div style="font-size:11px; color:#95a5a6; background:rgba(255,255,255,0.08); padding:8px 12px; border-radius:4px;">
    본 브리핑은 온톨로지 기반 전문가 지식 그래프와 국내외 기사를 기반으로 생성형 AI가 작성한 것으로 KMI의 공식 의견이 아님을 밝힙니다.
  </div>
</div>

<div style="{_content_width}">

{_body_sections}

<div style="text-align:center; margin:20px 0 10px;">
  <a href="https://hyongmo.github.io/Global-SCM-Monitoring/"
     style="display:inline-block; background:#3498db; color:white; padding:10px 24px; border-radius:6px; text-decoration:none; font-size:13px; font-weight:700;">
    📊 웹 뷰어에서 전체 보기
  </a>
</div>

<p style="font-size:11px; color:#999; text-align:center; margin-top:16px;">
  본 리포트는 KMI 해상공급망 AI 모니터링 시스템에서 자동 생성·발송되었습니다.
</p>

</div>
</body>
</html>
"""

# ── 메시지 구성 ──
msg = MIMEMultipart()
msg['From'] = KMI_SMTP_ADDRESS
msg['To'] = ', '.join(RECIPIENTS)
msg['Subject'] = subject
msg.attach(MIMEText(html_body, 'html', 'utf-8'))

# ── 첨부 파일 (brief 모드에서는 건너뜀) ──
ATTACHMENTS = [
    f'daily_report_{DATE_TAG}.xlsx',
    f'daily_report_llm_{DATE_TAG}.docx',
    f'daily_report_llm_{DATE_TAG}.md',
    f'gdelt_mon_classified_daily_{DATE_TAG}.csv',
    f'naver_mon_classified_daily_{DATE_TAG}.csv',
]

attached = 0
if brief_mode or summary_mode:
    print(f"  📋 {'Summary' if summary_mode else 'Brief'} 모드: 첨부파일 없이 발송")
else:
    for fname in ATTACHMENTS:
        fpath = os.path.join(DAILY_DIR, fname)
        if not os.path.exists(fpath):
            print(f"  ⚠ 첨부 파일 없음: {fname}")
            continue
        with open(fpath, 'rb') as f:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename="{fname}"')
        msg.attach(part)
        attached += 1
        print(f"  📎 첨부: {fname}")

# ── 발송 ──
mode_label = "Summary (요약만)" if summary_mode else ("Brief (본문만)" if brief_mode else "Full (첨부포함)")
print(f"\n[이메일 발송] {TARGET_DATE}  ({mode_label})")
print(f"  수신: {', '.join(RECIPIENTS)}")
if not brief_mode and not summary_mode:
    print(f"  첨부: {attached}/{len(ATTACHMENTS)}개")

try:
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(KMI_SMTP_ADDRESS, KMI_SMTP_PASSWORD)
        server.sendmail(KMI_SMTP_ADDRESS, RECIPIENTS, msg.as_string())
    print(f"✅ 이메일 발송 완료")
except Exception as e:
    print(f"❌ 이메일 발송 실패: {e}")
    sys.exit(1)
