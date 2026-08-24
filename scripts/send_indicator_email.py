#!/usr/bin/env python3
"""지표 감수용 메일 발송 — indicator_weekly.csv / indicator_weekly_dates.csv 첨부.

주간 초안 검토 메일(send_weekly_email.py --review) 직후에 별도로 발송한다.
지표 감수자가 값과 기준일을 원본 CSV로 직접 대조할 수 있도록 하는 것이 목적이다.

본문에는 이번 주 수집 상태를 세 갈래로 정리해 넣는다.
  · 신규 수집 : 기준일 있음 + 값이 전주와 다름
  · 값 유지   : 기준일 있음 + 값이 전주와 동일 (월간 지표 등 정상)
  · 수집 실패 : 기준일 없음 → 이전 값이 그대로 남아 있음 (CLAUDE.md 0-a)

사용법
  python scripts/send_indicator_email.py --week-tag 20260824
  python scripts/send_indicator_email.py --week-tag 20260824 --to a@b.kr c@d.kr
"""
import os, sys, ssl, smtplib, traceback
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import pandas as pd

RECIPIENTS_DEFAULT = ['hmjeon@kmi.re.kr', 'h.kim@kmi.re.kr']

_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VALUES_CSV = os.path.join(_ROOT, 'indicator_weekly.csv')
DATES_CSV  = os.path.join(_ROOT, 'indicator_weekly_dates.csv')

# ── 인자 ──────────────────────────────────────────────────────
args, week_tag, recipients = sys.argv[1:], None, list(RECIPIENTS_DEFAULT)
dry_run = False   # 발송 없이 본문만 생성 (검증용)
i = 0
while i < len(args):
    if args[i] == '--week-tag':
        week_tag = args[i + 1]; i += 2; continue
    if args[i] == '--to':
        recipients = args[i + 1:]; break
    if args[i] == '--dry-run':
        dry_run = True; i += 1; continue
    i += 1

# ── 첨부 파일 확인 (이 메일의 존재 이유이므로 없으면 실패시킨다) ──
missing = [p for p in (VALUES_CSV, DATES_CSV) if not os.path.exists(p)]
if missing:
    print(f"❌ 지표 파일 없음: {[os.path.basename(p) for p in missing]}")
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print("::error title=지표 메일 발송 실패::첨부할 지표 CSV가 없습니다")
    sys.exit(1)

# ── SMTP (send_weekly_email.py 와 동일 규칙) ──────────────────
SMTP_ADDRESS  = os.environ.get('GMAIL_ADDRESS', '') or os.environ.get('KMI_SMTP_ADDRESS', '')
SMTP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '') or os.environ.get('KMI_SMTP_PASSWORD', '')
if not dry_run and (not SMTP_ADDRESS or not SMTP_PASSWORD):
    print("⚠ SMTP_ADDRESS/SMTP_PASSWORD 미설정 — 이메일 발송 건너뜀")
    sys.exit(0)
SMTP_ADDRESS = SMTP_ADDRESS or '(dry-run)'
if 'gmail' in SMTP_ADDRESS:
    SMTP_HOST, SMTP_PORT, SMTP_MODE = 'smtp.gmail.com', 587, 'STARTTLS'
else:
    SMTP_HOST = os.environ.get('SMTP_HOST', 'gov-smtp.mailplug.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', '465'))
    SMTP_MODE = 'SSL'

# ── 이번 주 수집 상태 집계 ────────────────────────────────────
v = pd.read_csv(VALUES_CSV, index_col=0); v.index = pd.to_datetime(v.index)
d = pd.read_csv(DATES_CSV,  index_col=0); d.index = pd.to_datetime(d.index)
row  = v.index[-1]
prev = v.index[-2] if len(v) > 1 else None

fresh, held, failed = [], [], []
for c in v.columns:
    dt = d.at[row, c] if c in d.columns else None
    dt = '' if pd.isna(dt) or not str(dt).strip() else str(dt)[:10]
    if not dt:
        failed.append((c, v.at[row, c]))
        continue
    vn = v.at[row, c]
    vp = v.at[prev, c] if prev is not None else None
    same = pd.notna(vn) and vp is not None and pd.notna(vp) and float(vn) == float(vp)
    (held if same else fresh).append((c, dt))

week_label = week_tag or str(row.date()).replace('-', '')
period_txt = f"{row.date()} 주간"

def rows(items, fmt):
    return ''.join(f'<tr><td style="padding:3px 10px 3px 0;">{fmt(a, b)}</td></tr>' for a, b in items)

fail_block = ''
if failed:
    fail_block = f'''
    <div style="background:#fdecea; border-left:4px solid #c0392b; padding:12px 14px; margin:14px 0;">
      <div style="font-weight:600; color:#c0392b; margin-bottom:6px;">
        ⚠ 기준일 없음 {len(failed)}개 — 이번 주 수집 실패로 이전 값이 남아 있습니다
      </div>
      <table style="font-size:13px; border-collapse:collapse;">
        {rows(failed, lambda a, b: f"<b>{a}</b> — 표시값 {b} (출처일 불명, 검수 필요)")}
      </table>
    </div>'''

html = f'''<html><body style="font-family:-apple-system,'Malgun Gothic',sans-serif; color:#2c3e50; max-width:720px;">
<div style="background:#2c3e50; color:#fff; padding:14px 18px;">
  <div style="font-size:17px; font-weight:700;">글로벌 공급망 AI 주간 모니터링 — 지표 감수 자료</div>
  <div style="font-size:12px; opacity:.85; margin-top:3px;">{period_txt} · 총 {len(v.columns)}개 지표 · 누적 {len(v)}주</div>
</div>
<div style="padding:16px 18px; border:1px solid #e3e6e8; border-top:none;">
  <p style="font-size:13px; line-height:1.6;">
    주간 리포트 초안에 사용된 지표 원본 2개를 첨부합니다. 값과 기준일을 직접 대조하실 수 있습니다.
  </p>
  <table style="font-size:13px; border-collapse:collapse; margin:10px 0;">
    <tr><td style="padding:3px 12px 3px 0;">📎 <b>indicator_weekly.csv</b></td><td>지표 값 (주간)</td></tr>
    <tr><td style="padding:3px 12px 3px 0;">📎 <b>indicator_weekly_dates.csv</b></td><td>지표별 기준일(source_date)</td></tr>
  </table>
  {fail_block}
  <div style="font-size:13px; margin-top:14px;">
    <b>신규 수집 {len(fresh)}개</b> <span style="color:#7f8c8d;">(기준일 갱신됨)</span><br>
    <span style="font-size:12px; color:#555;">{', '.join(f'{a}({b})' for a, b in fresh) or '—'}</span>
  </div>
  <div style="font-size:13px; margin-top:12px;">
    <b>값 유지 {len(held)}개</b> <span style="color:#7f8c8d;">(기준일 있음 — 월간 지표 등 정상)</span><br>
    <span style="font-size:12px; color:#555;">{', '.join(f'{a}({b})' for a, b in held) or '—'}</span>
  </div>
  <p style="font-size:11px; color:#999; margin-top:18px;">
    본 메일은 주간 초안 생성 직후 자동 발송됩니다.
  </p>
</div>
</body></html>'''

# ── 메시지 ────────────────────────────────────────────────────
msg = MIMEMultipart()
msg['From'] = SMTP_ADDRESS
msg['To'] = ', '.join(recipients)
msg['Subject'] = f"[지표 감수] 글로벌 공급망 AI 주간 모니터링 — {period_txt} ({week_label})"
msg.attach(MIMEText(html, 'html', 'utf-8'))
for path in (VALUES_CSV, DATES_CSV):
    name = os.path.basename(path)
    with open(path, 'rb') as f:
        part = MIMEApplication(f.read(), Name=name)
    part['Content-Disposition'] = f'attachment; filename="{name}"'
    msg.attach(part)

print(f"[지표 감수 메일] {period_txt}")
print(f"  수신: {', '.join(recipients)}")
print(f"  첨부: indicator_weekly.csv, indicator_weekly_dates.csv")
print(f"  집계: 신규 {len(fresh)} / 값 유지 {len(held)} / 기준일 없음 {len(failed)}")

if dry_run:
    out = os.path.join('/tmp', f'indicator_mail_{week_label}.html')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"🔍 dry-run — 발송하지 않음. 본문 저장: {out}")
    sys.exit(0)

try:
    ctx = ssl.create_default_context()
    if SMTP_MODE == 'STARTTLS':
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.ehlo(); s.starttls(context=ctx); s.ehlo()
            s.login(SMTP_ADDRESS, SMTP_PASSWORD)
            s.sendmail(SMTP_ADDRESS, recipients, msg.as_string())
    else:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=30) as s:
            s.login(SMTP_ADDRESS, SMTP_PASSWORD)
            s.sendmail(SMTP_ADDRESS, recipients, msg.as_string())
    print("✅ 지표 감수 메일 발송 완료")
except Exception as e:
    print(f"❌ 지표 감수 메일 발송 실패: {e}")
    traceback.print_exc()
    sys.exit(1)
