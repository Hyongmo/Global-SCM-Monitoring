#!/usr/bin/env python3
"""
send_weekly_email.py
====================
주간 리포트(docs/weekly_report.html)에서 최신 주차 섹션을 파싱하여
상황요약 / 전주 대비 주요 변화 / 향후 주시 포인트를 요약 이메일로 발송합니다.

데이터 소스:
    docs/weekly_report.html 의 첫 번째 <div class="scenario-block" id="sc_0">
    (scenario_generator 출력은 최신주를 맨 앞에 배치)

환경변수:
    GMAIL_ADDRESS        Gmail 발송 주소 (우선)
    GMAIL_APP_PASSWORD   Gmail 앱 비밀번호 (우선)
    KMI_SMTP_ADDRESS     폴백: KMI 발송 주소
    KMI_SMTP_PASSWORD    폴백: KMI 비밀번호

호출:
    python scripts/send_weekly_email.py                                 # 기본 수신자
    python scripts/send_weekly_email.py --to a@b.com c@d.com            # 수신자 지정
    python scripts/send_weekly_email.py --html docs/weekly_report.html  # 소스 지정
"""

import os, sys, smtplib, ssl, traceback, base64 as _b64
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from bs4 import BeautifulSoup


# ── 기본값 ──
DEFAULT_RECIPIENTS = [
    'hmjeon@kmi.re.kr',
    'h.kim@kmi.re.kr',
    'lde@kmi.re.kr',
    'rheesw@kmi.re.kr',
    'pr@kmi.re.kr',
]
DEFAULT_HTML_PATH = os.path.join('docs', 'weekly_report.html')
VIEWER_URL = 'https://hyongmo.github.io/Global-SCM-Monitoring/weekly_report.html'


# ── 인자 파싱 ──
args = sys.argv[1:]
custom_recipients = []
html_path = DEFAULT_HTML_PATH

i = 0
while i < len(args):
    if args[i] == '--to':
        custom_recipients = args[i+1:]
        break
    elif args[i] == '--html':
        html_path = args[i+1]
        i += 2
        continue
    i += 1

RECIPIENTS = custom_recipients if custom_recipients else DEFAULT_RECIPIENTS


# ── SMTP 설정 (Gmail 우선, 없으면 KMI 폴백) ──
SMTP_ADDRESS  = os.environ.get('GMAIL_ADDRESS', '') or os.environ.get('KMI_SMTP_ADDRESS', '')
SMTP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '') or os.environ.get('KMI_SMTP_PASSWORD', '')

if not SMTP_ADDRESS or not SMTP_PASSWORD:
    print("⚠ SMTP_ADDRESS/SMTP_PASSWORD 미설정 — 이메일 발송 건너뜀")
    sys.exit(0)

if 'gmail' in SMTP_ADDRESS:
    SMTP_HOST = 'smtp.gmail.com'
    SMTP_PORT = 587
    SMTP_MODE = 'STARTTLS'
else:
    SMTP_HOST = os.environ.get('SMTP_HOST', 'gov-smtp.mailplug.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', '465'))
    SMTP_MODE = 'SSL'


# ── HTML 파싱 ──
if not os.path.exists(html_path):
    print(f"❌ {html_path} 없음 — 발송 중단")
    sys.exit(1)

with open(html_path, 'r', encoding='utf-8') as f:
    soup = BeautifulSoup(f.read(), 'html.parser')

# 최신 주차는 항상 sc_0 (scenario_generator 가 reverse 순으로 저장)
latest = soup.find('div', id='sc_0')
if latest is None:
    print("❌ <div id='sc_0'> 없음 — 발송 중단")
    sys.exit(1)


def _text(node):
    return node.get_text(strip=True) if node else ''


# 헤더: period / tier / crisis_level
header_block = latest.find('div', class_='header-block')
period_raw = _text(header_block.find('span', class_='period')) if header_block else ''
# period 예: "2026.04.13 (Week 15)기사수집기간: 2026.04.06~2026.04.12"
# <br><small>...</small> 로 분리되어 있으므로 다시 뜯어냄
period_main = ''
period_sub = ''
if header_block:
    period_span = header_block.find('span', class_='period')
    if period_span:
        small = period_span.find('small')
        if small:
            period_sub = small.get_text(strip=True)
            small.extract()
        period_main = period_span.get_text(strip=True)

tier_badge = _text(header_block.find('span', class_='tier-badge')) if header_block else ''
crisis_level_text = _text(header_block.find('span', class_='crisis-level')) if header_block else ''

# 위기 단계 색상 (tier-badge 의 background-color 재사용)
tier_color = '#7f8c8d'
if header_block:
    tb = header_block.find('span', class_='tier-badge')
    if tb and tb.has_attr('style'):
        import re as _re
        m = _re.search(r'background:\s*([#\w()\s,.]+)', tb['style'])
        if m:
            tier_color = m.group(1).strip().rstrip(';')


# 상황 요약: cluster-group 단위로 추출
situation_div = latest.find('div', class_='situation')
situation_clusters = []  # [{title, bullets:[...]}]
if situation_div:
    for cg in situation_div.find_all('div', class_='cluster-group'):
        title = _text(cg.find('div', class_='cluster-title'))
        bullets = []
        ul = cg.find('ul', class_='cluster-bullets')
        if ul:
            for li in ul.find_all('li', recursive=False):
                txt = li.get_text(' ', strip=True)
                if txt:
                    bullets.append(txt)
        if title or bullets:
            situation_clusters.append({'title': title, 'bullets': bullets})

# fallback: cluster 가 없으면 원문(original-narrative) 사용
situation_fallback = ''
if not situation_clusters and situation_div:
    details = situation_div.find('details', class_='original-narrative')
    if details:
        p = details.find('p')
        if p:
            situation_fallback = p.get_text(' ', strip=True)


# 전주 대비 주요 변화
changes_div = latest.find('div', class_='changes')
changes_items = []  # [raw_html_of_li]
if changes_div:
    ul = changes_div.find('ul')
    if ul:
        for li in ul.find_all('li', recursive=False):
            # li 내부 HTML 그대로 보존 (b, span.detail, 화살표 등)
            changes_items.append(li.decode_contents().strip())


# 향후 주시 포인트
wp_div = latest.find('div', class_='watchpoints')
wp_items = []  # [{horizon, text}]
if wp_div:
    ul = wp_div.find('ul')
    if ul:
        for li in ul.find_all('li', recursive=False):
            horizon = _text(li.find('span', class_='horizon'))
            # horizon span 을 제거한 나머지 텍스트
            li_copy = BeautifulSoup(str(li), 'html.parser').li
            h = li_copy.find('span', class_='horizon') if li_copy else None
            if h:
                h.extract()
            text = li_copy.get_text(' ', strip=True) if li_copy else ''
            wp_items.append({'horizon': horizon, 'text': text})


# ── KMI 로고 (base64) ──
_logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'assets', 'kmi_logo_white.png')
_KMI_LOGO_B64 = ''
if os.path.exists(_logo_path):
    with open(_logo_path, 'rb') as _f:
        _KMI_LOGO_B64 = _b64.b64encode(_f.read()).decode()
_logo_img = f'<img src="data:image/png;base64,{_KMI_LOGO_B64}" alt="KMI" style="height:30px; margin-right:12px; vertical-align:middle;">' if _KMI_LOGO_B64 else ''

# ── 이메일 HTML 렌더링 ──
_S = 'style'
_SEC       = f'{_S}="background:#fff; border-radius:8px; padding:18px 22px; margin-bottom:14px; border:1px solid #e8eaed;"'
_SEC_TITLE = f'{_S}="font-size:15px; font-weight:700; color:#1a252f; border-bottom:2px solid #eef0f3; padding-bottom:6px; margin-bottom:10px;"'
_CLUSTER_TITLE = f'{_S}="font-size:14px; font-weight:700; color:#2980b9; background:#eaf2ff; padding:3px 10px; border-radius:4px; margin:10px 0 6px; display:inline-block;"'
_BULLETS   = f'{_S}="padding-left:18px; margin:4px 0 10px;"'
_BULLET    = f'{_S}="font-size:13px; line-height:1.7; color:#34495e; margin-bottom:5px;"'
_HORIZON   = f'{_S}="display:inline-block; background:#eaf2ff; color:#2980b9; padding:1px 7px; border-radius:3px; font-size:11px; font-weight:700; margin-right:6px;"'


# 상황 요약 HTML
if situation_clusters:
    sit_html_parts = []
    for c in situation_clusters:
        title_html = f'<div {_CLUSTER_TITLE}>{c["title"]}</div>' if c['title'] else ''
        if c['bullets']:
            bullets_html = f'<ul {_BULLETS}>' + ''.join(
                f'<li {_BULLET}>{b}</li>' for b in c['bullets']
            ) + '</ul>'
        else:
            bullets_html = ''
        sit_html_parts.append(title_html + bullets_html)
    situation_html = ''.join(sit_html_parts)
elif situation_fallback:
    situation_html = f'<p {_BULLET}>{situation_fallback}</p>'
else:
    situation_html = f'<p {_BULLET} style="color:#999;">상황 요약 없음</p>'


# 전주 대비 주요 변화 HTML
if changes_items:
    changes_html = f'<ul {_BULLETS}>' + ''.join(
        f'<li {_BULLET}>{item}</li>' for item in changes_items
    ) + '</ul>'
else:
    changes_html = f'<p {_BULLET} style="color:#999;">전주 대비 주요 변화 없음</p>'


# 향후 주시 포인트 HTML
if wp_items:
    wp_html_parts = []
    for w in wp_items:
        horizon_tag = f'<span {_HORIZON}>{w["horizon"]}</span>' if w['horizon'] else ''
        wp_html_parts.append(f'<li {_BULLET}>{horizon_tag}{w["text"]}</li>')
    wp_html = f'<ul {_BULLETS}>' + ''.join(wp_html_parts) + '</ul>'
else:
    wp_html = f'<p {_BULLET} style="color:#999;">향후 주시 포인트 없음</p>'


# 제목: "[KMI 글로벌 공급망 AI 주간 브리핑] 2026.04.13 (Week 15)"
period_for_subject = period_main or '최신 주간'
subject = f'[KMI 글로벌 공급망 AI 주간 브리핑] {period_for_subject}'

# 헤더 블록
tier_tag = f'<span style="background:{tier_color}; color:#fff; padding:3px 10px; border-radius:4px; font-size:12px; font-weight:700; margin-right:8px;">{tier_badge}</span>' if tier_badge else ''
crisis_tag = f'<span style="color:{tier_color}; font-weight:700; font-size:13px;">{crisis_level_text}</span>' if crisis_level_text else ''
period_sub_html = f'<div style="font-size:11px; color:#95a5a6; margin-top:4px;">{period_sub}</div>' if period_sub else ''


html_body = f"""\
<html>
<body style="font-family:'Apple SD Gothic Neo','Noto Sans KR',sans-serif; color:#2c3e50; line-height:1.6; background:#f4f6f9; margin:0; padding:0;">
<div style="background:#2c3e50; color:white; padding:22px 28px 16px;">
  <div style="display:flex; align-items:center; margin-bottom:8px;">
    {_logo_img}
    <h1 style="font-size:20px; font-weight:700; margin:0;">글로벌 공급망 AI 주간 브리핑</h1>
  </div>
  <div style="font-size:13px; color:#ecf0f1; margin-bottom:6px;">{period_main}</div>
  {period_sub_html.replace('color:#95a5a6', 'color:#bdc3c7')}
  <div style="font-size:12px; color:#bdc3c7; margin:8px 0 10px;">한국해양수산개발원(KMI) 해양수산 AX 지원단 · hmjeon@kmi.re.kr</div>
  <div style="font-size:11px; color:#95a5a6; background:rgba(255,255,255,0.08); padding:8px 12px; border-radius:4px;">
    본 브리핑은 온톨로지 기반 전문가 지식 그래프와 주간 국내외 기사를 기반으로 생성형 AI가 작성한 것으로 KMI의 공식 의견이 아님을 밝힙니다.
  </div>
</div>

<div style="margin:0 auto; padding:20px 16px;">

<div {_SEC}>
  <div style="margin-bottom:10px;">{tier_tag}{crisis_tag}</div>
  <div {_SEC_TITLE}>📌 상황 요약</div>
  {situation_html}
</div>

<div {_SEC}>
  <div {_SEC_TITLE}>📈 전주 대비 주요 변화</div>
  {changes_html}
</div>

<div {_SEC}>
  <div {_SEC_TITLE}>🔍 향후 주시 포인트</div>
  {wp_html}
</div>

<div style="text-align:center; margin:20px 0 10px;">
  <a href="{VIEWER_URL}"
     style="display:inline-block; background:#3498db; color:white; padding:10px 24px; border-radius:6px; text-decoration:none; font-size:13px; font-weight:700;">
    📊 주간 리포트 전체 보기
  </a>
</div>

<p style="font-size:11px; color:#999; text-align:center; margin-top:16px;">
  본 리포트는 KMI 해상공급망 AI 모니터링 시스템에서 주간 리포트 업데이트 시 자동 발송됩니다.
</p>

</div>
</body>
</html>
"""


# ── 메시지 구성 ──
msg = MIMEMultipart()
msg['From'] = SMTP_ADDRESS
msg['To'] = ', '.join(RECIPIENTS)
msg['Subject'] = subject
msg.attach(MIMEText(html_body, 'html', 'utf-8'))


# ── 발송 ──
print(f"\n[주간 이메일 발송] {period_for_subject}")
print(f"  수신: {', '.join(RECIPIENTS)}")
print(f"  SMTP: {SMTP_HOST}:{SMTP_PORT} ({SMTP_MODE})")
print(f"  발신: {SMTP_ADDRESS}")
print(f"  상황요약 클러스터: {len(situation_clusters)}")
print(f"  전주 대비 변화 항목: {len(changes_items)}")
print(f"  주시 포인트 항목: {len(wp_items)}")

try:
    ctx = ssl.create_default_context()
    if SMTP_MODE == 'STARTTLS':
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.ehlo()
            server.login(SMTP_ADDRESS, SMTP_PASSWORD)
            server.sendmail(SMTP_ADDRESS, RECIPIENTS, msg.as_string())
    else:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=30) as server:
            server.login(SMTP_ADDRESS, SMTP_PASSWORD)
            server.sendmail(SMTP_ADDRESS, RECIPIENTS, msg.as_string())
    print(f"✅ 주간 이메일 발송 완료")
except Exception as e:
    print(f"❌ 주간 이메일 발송 실패: {e}")
    traceback.print_exc()
    sys.exit(1)
