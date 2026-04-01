#!/usr/bin/env python3
"""
send_email.py
=============
일일 모니터링 산출물을 이메일로 발송합니다.

첨부 파일:
    - daily_report_YYYYMMDD.xlsx
    - daily_report_llm_YYYYMMDD.docx
    - daily_report_llm_YYYYMMDD.md

환경변수:
    GMAIL_ADDRESS        발송 Gmail 주소
    GMAIL_APP_PASSWORD   Gmail 앱 비밀번호 (2단계 인증 필요)

호출:
    python scripts/send_email.py [YYYY-MM-DD]
"""

import json, os, sys, smtplib
from datetime import datetime, timedelta
from dateutil.parser import parse as dateparse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# ── 날짜 결정 ──
if len(sys.argv) > 1:
    TARGET_DATE = dateparse(sys.argv[1]).date()
else:
    TARGET_DATE = (datetime.now() - timedelta(days=1)).date()

DATE_TAG = TARGET_DATE.strftime('%Y%m%d')
MONITOR_DIR = 'monitoring'
DAILY_DIR = os.path.join(MONITOR_DIR, DATE_TAG)

# ── 환경변수 ──
GMAIL_ADDRESS = os.environ.get('GMAIL_ADDRESS', '')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')

RECIPIENTS = [
    'hmjeon@kmi.re.kr',
    'h.kim@kmi.re.kr',
]

if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
    print("⚠ GMAIL_ADDRESS 또는 GMAIL_APP_PASSWORD 미설정 — 이메일 발송 건너뜀")
    sys.exit(0)

# ── JSON에서 요약 추출 ──
json_path = os.path.join(DAILY_DIR, f'daily_report_llm_{DATE_TAG}.json')
summary = '(요약 없음)'
n_total = n_high = n_med = 0

if os.path.exists(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    llm = data.get('llm_result', {})
    summary = llm.get('executive_summary', '(요약 없음)')
    n_total = data.get('n_total', 0)
    n_high = data.get('n_high', 0)
    n_med = data.get('n_med', 0)

# ── 이메일 본문 구성 ──
subject = f'[KMI 해상공급망 모니터링] {TARGET_DATE} 일일 리포트'

html_body = f"""\
<html>
<body style="font-family:'Apple SD Gothic Neo','Noto Sans KR',sans-serif; color:#2c3e50; line-height:1.6;">
<h2 style="color:#2c3e50; border-bottom:2px solid #3498db; padding-bottom:8px;">
  해상 공급망 모니터링 일일 리포트 — {TARGET_DATE}
</h2>

<table style="border-collapse:collapse; margin:12px 0;">
  <tr>
    <td style="padding:4px 16px 4px 0; font-weight:bold;">총 수집</td>
    <td style="padding:4px 0;">{n_total:,}건</td>
  </tr>
  <tr>
    <td style="padding:4px 16px 4px 0; font-weight:bold; color:#e74c3c;">HIGH</td>
    <td style="padding:4px 0;">{n_high}건</td>
  </tr>
  <tr>
    <td style="padding:4px 16px 4px 0; font-weight:bold; color:#f39c12;">MEDIUM</td>
    <td style="padding:4px 0;">{n_med}건</td>
  </tr>
</table>

<h3 style="color:#2980b9;">주요기사 요약</h3>
<p style="background:#f8f9fa; padding:14px 18px; border-left:4px solid #3498db; border-radius:4px;">
  {summary}
</p>

<hr style="border:none; border-top:1px solid #ddd; margin:20px 0;">
<p style="font-size:12px; color:#999;">
  본 리포트는 KMI 해상공급망 AI 모니터링 시스템에서 자동 생성·발송되었습니다.<br>
  <a href="https://hyongmo.github.io/Global-SCM-Monitoring/daily_brief.html">웹 뷰어 바로가기</a>
</p>
</body>
</html>
"""

# ── 메시지 구성 ──
msg = MIMEMultipart()
msg['From'] = GMAIL_ADDRESS
msg['To'] = ', '.join(RECIPIENTS)
msg['Subject'] = subject
msg.attach(MIMEText(html_body, 'html', 'utf-8'))

# ── 첨부 파일 ──
ATTACHMENTS = [
    f'daily_report_{DATE_TAG}.xlsx',
    f'daily_report_llm_{DATE_TAG}.docx',
    f'daily_report_llm_{DATE_TAG}.md',
]

attached = 0
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
print(f"\n[이메일 발송] {TARGET_DATE}")
print(f"  수신: {', '.join(RECIPIENTS)}")
print(f"  첨부: {attached}/{len(ATTACHMENTS)}개")

try:
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, RECIPIENTS, msg.as_string())
    print(f"✅ 이메일 발송 완료")
except Exception as e:
    print(f"❌ 이메일 발송 실패: {e}")
    sys.exit(1)
