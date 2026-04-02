#!/usr/bin/env python3
"""
save_indicators.py
==================
수집된 해상공급망 지표를 CSV로 저장합니다.

사용법:
    python scripts/save_indicators.py '{"BDI":{"value":2030,"date":"2026-04-01"},...}'
    python scripts/save_indicators.py --file /path/to/indicators.json

JSON 입력 형식:
{
  "BDI":       {"value": 2030.00, "date": "2026-04-01", "source": "tradingeconomics.com"},
  "SCFI":      {"value": 1826.77, "date": "2026-03-28", "source": "en.sse.net.cn"},
  "Harpex":    {"value": 2213,    "date": "2026-03-27", "source": "harperpetersen.com"},
  "NAPMSDI":   {"value": 58.90,   "date": "2026-03-01", "source": "tradingeconomics.com"},
  "RWI_ISL_CTI":{"value": 144.8,  "date": "2026-02-01", "source": "isl.org"},
  "GSCSI":     {"value": null,    "date": null,          "source": "worldbank.org"}
}

출력:
    monitoring/indicators/indicators_YYYYMMDD.csv   (일일)
    monitoring/indicators/indicators_cumulative.csv  (누적)
"""

import csv, json, os, sys
from datetime import datetime

INDICATOR_DIR = os.path.join('monitoring', 'indicators')
os.makedirs(INDICATOR_DIR, exist_ok=True)

FIELDS = ['collect_date', 'indicator', 'value', 'unit', 'source_date', 'frequency', 'source']

INDICATOR_META = {
    'BDI':          {'unit': 'points', 'frequency': 'daily'},
    'SCFI':         {'unit': 'points', 'frequency': 'weekly'},
    'Harpex':       {'unit': 'points', 'frequency': 'weekly'},
    'NAPMSDI':      {'unit': 'index',  'frequency': 'monthly'},
    'RWI_ISL_CTI':  {'unit': 'index',  'frequency': 'monthly'},
    'GSCSI':        {'unit': 'index',  'frequency': 'monthly'},
}

def main():
    # 입력 파싱
    if len(sys.argv) < 2:
        print("Usage: python scripts/save_indicators.py '<json>' [--collect-date YYYY-MM-DD]")
        sys.exit(1)

    collect_date = datetime.now().strftime('%Y-%m-%d')

    if sys.argv[1] == '--file':
        with open(sys.argv[2], 'r') as f:
            data = json.load(f)
        if len(sys.argv) > 4 and sys.argv[3] == '--collect-date':
            collect_date = sys.argv[4]
    else:
        data = json.loads(sys.argv[1])
        for i, arg in enumerate(sys.argv):
            if arg == '--collect-date' and i + 1 < len(sys.argv):
                collect_date = sys.argv[i + 1]

    date_tag = collect_date.replace('-', '')

    # 일일 CSV 작성
    daily_path = os.path.join(INDICATOR_DIR, f'indicators_{date_tag}.csv')
    rows = []
    for indicator, info in data.items():
        meta = INDICATOR_META.get(indicator, {'unit': '', 'frequency': ''})
        rows.append({
            'collect_date': collect_date,
            'indicator': indicator,
            'value': info.get('value', ''),
            'unit': meta['unit'],
            'source_date': info.get('date', ''),
            'frequency': meta['frequency'],
            'source': info.get('source', ''),
        })

    with open(daily_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  저장: {daily_path} ({len(rows)}개 지표)")

    # 누적 CSV 업데이트 (중복 방지: collect_date + indicator 기준)
    cumulative_path = os.path.join(INDICATOR_DIR, 'indicators_cumulative.csv')
    existing = []
    if os.path.exists(cumulative_path):
        with open(cumulative_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            existing = list(reader)

    # 기존 데이터에서 오늘 수집분 제거 (덮어쓰기)
    existing = [r for r in existing
                if not (r['collect_date'] == collect_date and r['indicator'] in data)]
    existing.extend(rows)

    # 정렬: 날짜 → 지표명
    existing.sort(key=lambda r: (r['collect_date'], r['indicator']))

    with open(cumulative_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(existing)
    print(f"  누적: {cumulative_path} (총 {len(existing)}행)")

    # 결과 요약
    print(f"\n[지표 수집 결과] {collect_date}")
    print(f"{'지표':<15} {'값':>10}  {'기준일':<12} {'출처'}")
    print("-" * 60)
    for r in rows:
        v = r['value'] if r['value'] != '' else '(미수집)'
        d = r['source_date'] if r['source_date'] else '-'
        print(f"{r['indicator']:<15} {str(v):>10}  {d:<12} {r['source']}")


if __name__ == '__main__':
    main()
