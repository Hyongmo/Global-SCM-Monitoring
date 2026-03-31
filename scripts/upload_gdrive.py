#!/usr/bin/env python3
"""
upload_gdrive.py
================
일일 산출물을 Google Drive(Service Account)에 업로드합니다.

업로드 대상:
    monitoring/YYYYMMDD/
        - gdelt_mon_classified_daily_YYYYMMDD.csv
        - naver_mon_classified_daily_YYYYMMDD.csv
        - daily_report_llm_YYYYMMDD.json
        - daily_report_llm_YYYYMMDD.md
        - daily_report_llm_YYYYMMDD.docx
        - daily_report_YYYYMMDD.xlsx
    docs/daily_brief.html

폴더 구조 (Google Drive):
    Hormuz-monitoring/          ← 상위 공유 폴더 (GDRIVE_FOLDER_ID)
        YYYYMMDD/               ← 날짜별 하위 폴더
            *.csv, *.json, *.md, *.docx, *.xlsx
        daily_brief.html       ← 최신 HTML 뷰어 (루트에 덮어쓰기)

환경변수:
    GDRIVE_CREDENTIALS   Service Account JSON (문자열 전체)
    GDRIVE_FOLDER_ID     상위 공유 폴더 ID

호출:
    python scripts/upload_gdrive.py [YYYY-MM-DD]
    날짜 미지정 시 어제 날짜 사용
"""

import json, os, sys
from datetime import datetime, timedelta
from dateutil.parser import parse as dateparse

# ─── 날짜 결정 ────────────────────────────────────────────────
if len(sys.argv) > 1:
    TARGET_DATE = dateparse(sys.argv[1]).date()
else:
    TARGET_DATE = (datetime.now() - timedelta(days=1)).date()

DATE_TAG = TARGET_DATE.strftime('%Y%m%d')
MONITOR_DIR = 'monitoring'
DAILY_DIR   = os.path.join(MONITOR_DIR, DATE_TAG)
DOCS_DIR    = 'docs'

# ─── 환경변수 ─────────────────────────────────────────────────
GDRIVE_CREDENTIALS = os.environ.get('GDRIVE_CREDENTIALS', '')
GDRIVE_FOLDER_ID   = os.environ.get('GDRIVE_FOLDER_ID', '')

if not GDRIVE_CREDENTIALS or not GDRIVE_FOLDER_ID:
    print("⚠ GDRIVE_CREDENTIALS 또는 GDRIVE_FOLDER_ID 미설정 — 업로드 건너뜀")
    sys.exit(0)

# ─── Google Drive API 초기화 ──────────────────────────────────
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except ImportError:
    print("❌ google-api-python-client 미설치")
    print("   pip install google-api-python-client google-auth --break-system-packages")
    sys.exit(1)

SCOPES = ['https://www.googleapis.com/auth/drive']

try:
    cred_info = json.loads(GDRIVE_CREDENTIALS)
    credentials = service_account.Credentials.from_service_account_info(
        cred_info, scopes=SCOPES
    )
    drive = build('drive', 'v3', credentials=credentials)
    print("✅ Google Drive 인증 완료")
except Exception as e:
    print(f"❌ Google Drive 인증 실패: {e}")
    sys.exit(1)


# ─── 유틸리티 함수 ────────────────────────────────────────────

MIME_MAP = {
    '.csv':  'text/csv',
    '.json': 'application/json',
    '.md':   'text/markdown',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.html': 'text/html',
}


def _get_or_create_folder(name, parent_id):
    """Drive 내에 이름이 name인 폴더를 찾거나 생성, folder_id 반환"""
    query = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents and trashed=false"
    )
    results = drive.files().list(
        q=query, fields='files(id, name)', pageSize=5
    ).execute()
    files = results.get('files', [])
    if files:
        return files[0]['id']

    # 폴더 없음 → 생성
    meta = {
        'name': name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_id],
    }
    folder = drive.files().create(body=meta, fields='id').execute()
    print(f"  📁 폴더 생성: {name} ({folder['id']})")
    return folder['id']


def _upload_file(local_path, file_name, parent_folder_id, overwrite=True):
    """파일을 Drive 폴더에 업로드. overwrite=True 면 동일 이름 파일 삭제 후 업로드"""
    if not os.path.exists(local_path):
        print(f"  ⚠ 파일 없음 건너뜀: {local_path}")
        return None

    ext   = os.path.splitext(file_name)[1].lower()
    mime  = MIME_MAP.get(ext, 'application/octet-stream')
    size  = os.path.getsize(local_path)

    if overwrite:
        # 동일 이름 파일 삭제
        query = (
            f"name='{file_name}' and '{parent_folder_id}' in parents and trashed=false"
        )
        existing = drive.files().list(
            q=query, fields='files(id)', pageSize=5
        ).execute().get('files', [])
        for f in existing:
            drive.files().delete(fileId=f['id']).execute()

    media = MediaFileUpload(local_path, mimetype=mime, resumable=size > 5 * 1024 * 1024)
    meta  = {'name': file_name, 'parents': [parent_folder_id]}
    result = drive.files().create(
        body=meta, media_body=media, fields='id, name'
    ).execute()
    print(f"  ✅ 업로드: {file_name}  ({size/1024:.0f} KB)  id={result['id']}")
    return result['id']


# ─── 1. 날짜별 하위 폴더 생성 ────────────────────────────────
print(f"\n[Google Drive 업로드] 날짜: {TARGET_DATE}")
print("-" * 40)

daily_folder_id = _get_or_create_folder(DATE_TAG, GDRIVE_FOLDER_ID)
print(f"  📂 날짜 폴더: {DATE_TAG} ({daily_folder_id})")


# ─── 2. DAILY_DIR 내 파일 업로드 ────────────────────────────

DAILY_TARGETS = [
    f'gdelt_mon_classified_daily_{DATE_TAG}.csv',
    f'naver_mon_classified_daily_{DATE_TAG}.csv',
    f'daily_report_llm_{DATE_TAG}.json',
    f'daily_report_llm_{DATE_TAG}.md',
    f'daily_report_llm_{DATE_TAG}.docx',
    f'daily_report_{DATE_TAG}.xlsx',
]

uploaded = 0
for fname in DAILY_TARGETS:
    local_path = os.path.join(DAILY_DIR, fname)
    fid = _upload_file(local_path, fname, daily_folder_id)
    if fid:
        uploaded += 1

print(f"\n  일별 파일: {uploaded}/{len(DAILY_TARGETS)}개 업로드")


# ─── 3. weekly 파일 — 루트 폴더에 덮어쓰기 ──────────────────────
# 같은 이름으로 매일 덮어씌워짐 (주간 누적 완성본으로 자동 교체)

import glob as _glob

WEEKLY_DIR = os.path.join(MONITOR_DIR, 'weekly')
weekly_files = _glob.glob(os.path.join(WEEKLY_DIR, '*.csv'))

if weekly_files:
    weekly_folder_id = _get_or_create_folder('weekly', GDRIVE_FOLDER_ID)
    for wf in weekly_files:
        _upload_file(wf, os.path.basename(wf), weekly_folder_id, overwrite=True)
    print(f"  주간 파일: {len(weekly_files)}개 업로드")
else:
    print(f"  ⚠ 주간 파일 없음 — 건너뜀")


# ─── 4. daily_brief.html — 루트 폴더에 덮어쓰기 ─────────────

viewer_path = os.path.join(DOCS_DIR, 'daily_brief.html')
if os.path.exists(viewer_path):
    _upload_file(viewer_path, 'daily_brief.html', GDRIVE_FOLDER_ID, overwrite=True)
else:
    print(f"  ⚠ daily_brief.html 없음 ({viewer_path})")


# ─── 5. 완료 메시지 ──────────────────────────────────────────

print(f"\n✅ Google Drive 업로드 완료")
print(f"   폴더: https://drive.google.com/drive/folders/{GDRIVE_FOLDER_ID}")
