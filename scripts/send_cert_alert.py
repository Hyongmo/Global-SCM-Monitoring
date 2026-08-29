#!/usr/bin/env python3
"""GDELT TLS 인증서 문제 경고 메일.

collect_daily.py 의 사전 점검이 인증서 검증 실패를 감지하면
daily.yml 이 이 스크립트를 실행한다(CERT_PROBLEM=1 일 때만).

인증서 상태는 이 스크립트가 직접 다시 진단한다.
(collect_daily.py 에서 메시지를 넘겨받지 않는다 — GITHUB_ENV 이스케이프 문제 회피)

환경변수
    GMAIL_ADDRESS / GMAIL_APP_PASSWORD   Gmail 발송 (우선)
    KMI_SMTP_ADDRESS / KMI_SMTP_PASSWORD 폴백
    TARGET_DATE                          기사 수집 대상일 (YYYY-MM-DD)
"""
import os, ssl, socket, smtplib, subprocess, sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

HOST       = 'api.gdeltproject.org'
RECIPIENTS = ['hmjeon@kmi.re.kr']

TARGET_DATE = os.environ.get('TARGET_DATE', '') or '(미상)'

SMTP_ADDRESS  = os.environ.get('GMAIL_ADDRESS', '') or os.environ.get('KMI_SMTP_ADDRESS', '')
SMTP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '') or os.environ.get('KMI_SMTP_PASSWORD', '')
if not SMTP_ADDRESS or not SMTP_PASSWORD:
    print('⚠ SMTP 설정 없음 — 경고 메일 발송 건너뜀')
    sys.exit(0)

if 'gmail' in SMTP_ADDRESS:
    SMTP_HOST, SMTP_PORT, SMTP_MODE = 'smtp.gmail.com', 587, 'STARTTLS'
else:
    SMTP_HOST = os.environ.get('SMTP_HOST', 'gov-smtp.mailplug.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', '465'))
    SMTP_MODE = 'SSL'


def diagnose():
    """(오류문구, 인증서 유효기간) 반환."""
    err = ''
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((HOST, 443), timeout=10) as s:
            ctx.wrap_socket(s, server_hostname=HOST).close()
        err = '(재점검 시점에는 검증 성공 — 일시적 문제였을 수 있음)'
    except Exception as e:
        err = f'{type(e).__name__}: {e}'

    dates = ''
    try:  # 검증을 끄고 인증서 자체를 읽어 유효기간 확인
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((HOST, 443), timeout=10) as s:
            with ctx.wrap_socket(s, server_hostname=HOST) as ss:
                der = ss.getpeercert(binary_form=True)
        p = subprocess.run(['openssl', 'x509', '-inform', 'DER', '-noout', '-dates'],
                           input=der, capture_output=True)
        dates = p.stdout.decode('utf-8', 'replace').strip()
    except Exception as e:
        dates = f'(유효기간 확인 실패: {e})'
    return err, dates


err, dates = diagnose()
now = datetime.now(timezone.utc).strftime('%b %d %H:%M:%S %Y') + ' GMT'

subject = f'[KMI 일일 브리핑] GDELT 인증서 문제로 해외 기사 수집 불가 — {TARGET_DATE}'

html = f"""<html><body style="font-family:-apple-system,'Malgun Gothic',sans-serif;
 font-size:14px; line-height:1.7; color:#222;">
<div style="background:#fdecea; border-left:4px solid #c0392b; color:#7b241c;
 padding:12px 16px; margin:0 0 18px 0; border-radius:3px;">
<b>{HOST}</b> 의 TLS 인증서 검증에 실패해 영문 해외 기사를 수집하지 못했습니다.
</div>

<pre style="background:#f4f4f4; padding:12px 14px; border-radius:3px; font-size:12px;
 white-space:pre-wrap; word-break:break-all;">오류: {err}
{dates}
현재: {now}</pre>

<p>오늘 브리핑은 <b>국내 기사만으로 생성</b>되었으며,
<b style="color:#c0392b;">외부 수신자에게는 발송하지 않았습니다.</b></p>

<h3 style="margin:22px 0 8px; font-size:15px;">조치 방법</h3>
<ol style="margin:0; padding-left:20px;">
  <li style="margin-bottom:6px;">GDELT 측 인증서 갱신을 기다린 뒤 재실행</li>
  <li>급하면 Actions 수동 실행에서 <code>gdelt_insecure_tls=1</code><br>
      <span style="color:#888;">— TLS 검증을 끄므로 상시 사용 금지</span></li>
</ol>

<h3 style="margin:22px 0 8px; font-size:15px;">재실행 설정</h3>
<pre style="background:#f4f4f4; padding:12px 14px; border-radius:3px; font-size:12px;
">target_date = {TARGET_DATE}
gdelt_only  = 1          (네이버·리포트 보존, GDELT만 보충)
gdelt_insecure_tls = 1   (인증서 갱신 전일 때만)</pre>

<h3 style="margin:22px 0 8px; font-size:15px;">갱신 확인</h3>
<pre style="background:#f4f4f4; padding:12px 14px; border-radius:3px; font-size:12px;
 white-space:pre-wrap; word-break:break-all;">echo | openssl s_client -connect {HOST}:443 -servername {HOST} 2>/dev/null | openssl x509 -noout -dates</pre>
</body></html>"""

msg = MIMEMultipart()
msg['From'] = SMTP_ADDRESS
msg['To'] = ', '.join(RECIPIENTS)
msg['Subject'] = subject
msg.attach(MIMEText(html, 'html', 'utf-8'))

try:
    ctx = ssl.create_default_context()
    if SMTP_MODE == 'STARTTLS':
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo(); server.starttls(context=ctx); server.ehlo()
            server.login(SMTP_ADDRESS, SMTP_PASSWORD)
            server.sendmail(SMTP_ADDRESS, RECIPIENTS, msg.as_string())
    else:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=30) as server:
            server.login(SMTP_ADDRESS, SMTP_PASSWORD)
            server.sendmail(SMTP_ADDRESS, RECIPIENTS, msg.as_string())
    print(f'✅ 인증서 경고 메일 발송: {", ".join(RECIPIENTS)}')
except Exception as e:
    print(f'❌ 경고 메일 발송 실패: {e}')
    import traceback; traceback.print_exc()
