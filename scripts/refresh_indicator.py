#!/usr/bin/env python3
"""지표 1개를 재수집하여 이미 생성된 주차의 지표만 교체한다 (LLM 재호출 없음).

배경
----
weekly_pipeline.py 를 같은 주차로 재실행하면 `_has_fresher_indicators()` 가 True 가 되어
generate_weekly_scenario() 가 다시 호출되고, **감수 반영분이 통째로 덮어써진다.**
(weekly_pipeline.py step7 참조)

따라서 감수 진행 중에 지표 수집 실패를 바로잡아야 할 때는 이 스크립트를 쓴다.
시나리오 본문(header/part_a~e/watchpoints 등)은 일절 건드리지 않고
`indicators[<지표명>]` 항목과 지표 CSV 2개만 갱신한다.

사용법
------
  # 미리보기 (기본값 — 아무것도 쓰지 않음)
  python scripts/refresh_indicator.py --week 2026-W34 --refetch Brent

  # 실제 적용
  python scripts/refresh_indicator.py --week 2026-W34 --refetch Brent --apply

  # ffill 된 지표의 누락된 기준일을 직전 주 기준일로 복원 (CLAUDE.md 0-a)
  python scripts/refresh_indicator.py --week 2026-W34 --restore-date GPR GSCPI --apply

주의
----
- 야후 파이낸스 접근이 필요하므로 네트워크가 되는 환경(로컬 터미널/Jupyter)에서 실행할 것.
- 티커 매핑은 weekly_pipeline.py 의 YF_GLOBAL_MAP / YF_KR_STOCK_MAP 을 ast 로 읽어 쓴다
  (하드코딩 금지 — CLAUDE.md 17).
- --apply 시 대상 파일 3개를 .bak_refresh_<타임스탬프> 로 백업한다.
"""
import argparse, ast, json, os, shutil, sys
from datetime import datetime
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE   = os.path.join(BASE, 'scripts', 'weekly_pipeline.py')
VALUES_CSV = os.path.join(BASE, 'indicator_weekly.csv')
DATES_CSV  = os.path.join(BASE, 'indicator_weekly_dates.csv')
RESULT_JSON= os.path.join(BASE, 'scenario_results.json')


def load_ticker_map():
    """weekly_pipeline.py 에서 티커 매핑을 ast 로 추출 (지표명 → 티커)."""
    tree = ast.parse(open(PIPELINE, encoding='utf-8').read())
    name2ticker = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
           and node.targets[0].id in ('YF_GLOBAL_MAP', 'YF_KR_STOCK_MAP'):
            for k, v in zip(node.value.keys, node.value.values):
                name2ticker[ast.literal_eval(v)] = ast.literal_eval(k)
    if not name2ticker:
        sys.exit('❌ weekly_pipeline.py 에서 티커 매핑을 찾지 못했습니다.')
    return name2ticker


def fetch_weekly(ticker, idx):
    """yf_weekly() 와 동일한 방식으로 주간 종가 시리즈와 실제 종가일을 반환."""
    try:
        import yfinance as yf
    except ImportError:
        sys.exit('❌ yfinance 가 설치되어 있지 않습니다.\n'
                 '   네트워크가 되는 환경(로컬 터미널 / Jupyter)에서 실행하세요.\n'
                 '   설치: pip install yfinance')
    start = str((idx[0] - pd.Timedelta(days=14)).date())
    end   = str((idx[-1] + pd.Timedelta(days=1)).date())
    try:
        raw = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    except Exception as e:
        print(f'    ⚠ {ticker} 다운로드 실패: {type(e).__name__}: {e}')
        return None, None
    if raw is None or len(raw) == 0:
        return None, None
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    if 'Close' not in raw.columns:
        return None, None
    close = raw['Close'].dropna()
    vals  = close.resample('W-MON', closed='left', label='right').last().reindex(idx)
    dates = close.index.to_series().resample('W-MON', closed='left', label='right').last().reindex(idx)
    return vals, dates


def snap_fields(value, prev_value):
    """get_indicator_snapshot() 과 동일한 변동률/방향 계산."""
    out = {'value': round(float(value), 4)}
    if prev_value is not None and pd.notna(prev_value) and float(prev_value) != 0:
        chg = (float(value) - float(prev_value)) / abs(float(prev_value)) * 100
        out['chg_pct']    = round(chg, 1)
        out['chg_dir']    = 'up' if chg > 0.5 else ('down' if chg < -0.5 else 'flat')
        out['prev_value'] = round(float(prev_value), 4)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--week', required=True, help='대상 주차 라벨 (예: 2026-W34)')
    ap.add_argument('--refetch', nargs='*', default=[], help='야후에서 재수집할 지표명')
    ap.add_argument('--restore-date', nargs='*', default=[], help='기준일만 직전 주 값으로 복원할 지표명')
    ap.add_argument('--apply', action='store_true', help='실제 파일에 반영 (미지정 시 미리보기)')
    a = ap.parse_args()

    if not a.refetch and not a.restore_date:
        sys.exit('❌ --refetch 또는 --restore-date 중 하나는 지정해야 합니다.')

    vdf = pd.read_csv(VALUES_CSV, index_col=0)
    ddf = pd.read_csv(DATES_CSV,  index_col=0)
    vdf.index = pd.to_datetime(vdf.index)
    ddf.index = pd.to_datetime(ddf.index)

    scen = json.load(open(RESULT_JSON, encoding='utf-8'))
    targets = [s for s in scen if s.get('week') == a.week]
    if not targets:
        sys.exit(f'❌ scenario_results.json 에 week={a.week} 항목이 없습니다.')
    entry = targets[-1]

    # 주차 라벨 → 주간 인덱스 행 (period 의 (YYYY-MM-DD) = 월요일 = 주간 인덱스)
    import re
    m = re.search(r'\((\d{4})-(\d{2})-(\d{2})\)', entry.get('period', ''))
    if not m:
        sys.exit(f"❌ period 에서 날짜를 찾지 못했습니다: {entry.get('period')!r}")
    row = pd.Timestamp('-'.join(m.groups()))
    if row not in vdf.index:
        sys.exit(f'❌ indicator_weekly.csv 에 {row.date()} 행이 없습니다.')
    prev_row = vdf.index[vdf.index.get_loc(row) - 1]

    name2ticker = load_ticker_map()
    changes = []

    for name in a.refetch:
        if name not in name2ticker:
            print(f'  ⚠ {name}: 야후 티커 매핑 없음 — 건너뜀'); continue
        tk = name2ticker[name]
        print(f'  · {name} ({tk}) 재수집 중...')
        vals, dates = fetch_weekly(tk, vdf.index)
        if vals is None or pd.isna(vals.get(row)):
            print(f'    ❌ {name}: 해당 주차 데이터를 받지 못했습니다 (야후 미제공/장애)')
            continue
        new_v = float(vals[row]); new_d = dates[row]
        old_v = vdf.at[row, name] if name in vdf.columns else None
        old_d = ddf.at[row, name] if name in ddf.columns else None
        prev_v = vdf.at[prev_row, name] if name in vdf.columns else None
        changes.append({'name': name, 'value': new_v,
                        'date': str(pd.Timestamp(new_d).date()) if pd.notna(new_d) else '',
                        'old_value': old_v, 'old_date': old_d, 'prev_value': prev_v})

    for name in a.restore_date:
        old_d = ddf.at[row, name] if name in ddf.columns else None
        if pd.notna(old_d) and str(old_d).strip():
            print(f'  · {name}: 기준일이 이미 있음({old_d}) — 건너뜀'); continue
        src_d = ddf.at[prev_row, name] if name in ddf.columns else None
        if pd.isna(src_d) or not str(src_d).strip():
            print(f'  ⚠ {name}: 직전 주 기준일도 없음 — 건너뜀'); continue
        cur_v = vdf.at[row, name]; prv_v = vdf.at[prev_row, name]
        if pd.isna(cur_v) or pd.isna(prv_v) or abs(float(cur_v) - float(prv_v)) > 1e-9:
            print(f'  ⚠ {name}: 값이 직전 주와 달라 ffill 이 아님 — 기준일 복원 부적절, 건너뜀'); continue
        changes.append({'name': name, 'value': None, 'date': str(src_d)[:10],
                        'old_value': cur_v, 'old_date': old_d, 'prev_value': None})

    if not changes:
        print('\n변경할 내용이 없습니다.'); return

    print(f'\n=== 변경 예정 ({a.week}, 주간 인덱스 {row.date()}) ===')
    for c in changes:
        ind = entry['indicators'].get(c['name'], {})
        if c['value'] is None:
            print(f"  {c['name']}: 기준일만 복원  {ind.get('data_date','(없음)')!r} → {c['date']!r}  (값 {c['old_value']} 유지, ffill 출처일)")
        else:
            f = snap_fields(c['value'], c['prev_value'])
            print(f"  {c['name']}:")
            print(f"      값       {ind.get('value')} → {f['value']}")
            print(f"      변동률   {ind.get('chg_pct')}% → {f.get('chg_pct')}%  ({ind.get('chg_dir')} → {f.get('chg_dir')})")
            print(f"      기준일   {ind.get('data_date')!r} → {c['date']!r}")

    if not a.apply:
        print('\n(미리보기입니다. 실제로 반영하려면 --apply 를 붙이세요.)'); return

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    for p in (VALUES_CSV, DATES_CSV, RESULT_JSON):
        shutil.copy2(p, f'{p}.bak_refresh_{stamp}')
    print(f'\n백업 생성: *.bak_refresh_{stamp}')

    for c in changes:
        n = c['name']
        if c['value'] is not None:
            vdf.at[row, n] = c['value']
            entry['indicators'].setdefault(n, {}).update(snap_fields(c['value'], c['prev_value']))
        ddf.at[row, n] = c['date']
        entry['indicators'].setdefault(n, {})['data_date'] = c['date']

    vdf.to_csv(VALUES_CSV); ddf.to_csv(DATES_CSV)
    with open(RESULT_JSON, 'w', encoding='utf-8') as f:
        json.dump(scen, f, ensure_ascii=False, indent=2, default=str)  # 파이프라인과 동일 포맷

    print('✅ 반영 완료 — 시나리오 본문은 변경하지 않았습니다.')
    print('   다음: python scripts/regen_weekly.py <월요일 태그>  로 HTML/PDF 재생성')


if __name__ == '__main__':
    main()
