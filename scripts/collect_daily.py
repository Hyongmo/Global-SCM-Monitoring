#!/usr/bin/env python3
"""
collect_daily.py
================
해상 공급망 위기 일일 모니터링 자동화 스크립트
(gdelt_news_monitoring_v3.ipynb 를 GitHub Actions 용 스크립트로 변환)

실행 방식:
    python scripts/collect_daily.py [YYYY-MM-DD]
    날짜 미지정 시 어제(yesterday) 자동 사용

환경변수:
    ANTHROPIC_API_KEY    Claude API 키
    NAVER_CLIENT_ID      네이버 검색 API ID
    NAVER_CLIENT_SECRET  네이버 검색 API 시크릿
"""

import json, os, sys, hashlib, time, re, html, glob, requests
import pandas as pd
from datetime import datetime, timedelta, date
from dateutil.parser import parse as dateparse
from gdeltdoc import GdeltDoc, Filters
import anthropic

# ══════════════════════════════════════════════════════════════
# 0. 기본 설정
# ══════════════════════════════════════════════════════════════

# ── 수집 날짜 결정 (CLI 인수 또는 어제) ──
if len(sys.argv) > 1:
    TARGET_DATE = dateparse(sys.argv[1]).date()
else:
    TARGET_DATE = (datetime.now() - timedelta(days=1)).date()

d_start = TARGET_DATE
d_end   = TARGET_DATE
collect_dates   = [TARGET_DATE]
dates_to_collect = [TARGET_DATE]

DATE_TAG = TARGET_DATE.strftime('%Y%m%d')

print(f"{'='*60}")
print(f"  KMI 해상 공급망 일일 모니터링")
print(f"  수집 날짜: {TARGET_DATE}")
print(f"{'='*60}\n")

# ── 경로 설정 ──
MONITOR_DIR = 'monitoring'
CUMUL_DIR   = os.path.join(MONITOR_DIR, 'cumulative')
WEEKLY_DIR  = os.path.join(MONITOR_DIR, 'weekly')
DAILY_DIR   = os.path.join(MONITOR_DIR, DATE_TAG)

for d in [MONITOR_DIR, CUMUL_DIR, WEEKLY_DIR, DAILY_DIR]:
    os.makedirs(d, exist_ok=True)

# ── 쿼리 파일 ──
MON_QUERY_FILE = 'news_queries_monitoring_v2.json'

# ── API 클라이언트 ──
gd = GdeltDoc()
client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 자동 사용

# ── GDELT 파라미터 ──
MAX_RECORDS    = 250
SLEEP_SEC      = 0.5
LANGUAGES      = 'English'
MIN_KEYWORD_LEN = 5
MAX_RETRIES    = 2

# ── 네이버 파라미터 ──
NAVER_CLIENT_ID     = os.environ.get('NAVER_CLIENT_ID', '')
NAVER_CLIENT_SECRET = os.environ.get('NAVER_CLIENT_SECRET', '')
NAVER_API_URL = 'https://openapi.naver.com/v1/search/news.json'
NAVER_DISPLAY = 100
NAVER_SLEEP   = 0.3

# ── LLM 파라미터 ──
MODEL      = "claude-haiku-4-5-20251001"
BATCH_SIZE = 20
MAX_LLM_SAMPLE_GDELT = 1500
MAX_LLM_SAMPLE_NAVER = 1500
MIN_PER_KEYWORD      = 3

# ── 카테고리 정의 ──
CATEGORIES = {
    "1_Security":       "Security — military threats, geopolitical tensions, chokepoint control",
    "2_Safety":         "Safety — vessel accidents, maritime safety incidents",
    "3_Freight":        "Freight Rates — freight index changes (BDI, SCFI), traffic volume",
    "4_PortCargo":      "Port Cargo Volume — import cargo, port throughput",
    "5_EconFinance":    "Economy / Finance — stock prices, exchange rates, economic indicators",
    "6_Seafood":        "Seafood Trade — export/import of seafood, fishing industry",
    "7_Shipping":       "Shipping Industry — carrier trends, alternative routes",
    "8_Logistics":      "Logistics Companies — impacts on logistics/forwarding",
    "9_PortCongestion": "Port Congestion — delays, berth waiting, container dwell time",
    "10_OtherIndustry": "Other Industries — petroleum, LNG, fertilizers, petrochemical",
}
CATEGORY_KEYS = list(CATEGORIES.keys())

CAT_KR = {
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
CAT_ORDER = list(CAT_KR.keys())

CAT_BLOCK_MON = """  - 1_Security: Military threats, naval deployments, chokepoint control, Iran tensions, Houthi attacks
  - 2_Safety: Vessel accidents, IMO emergency meetings, maritime safety incidents
  - 3_Freight: BDI/SCFI/CCFI changes, tanker rate spikes, freight surcharges
  - 4_PortCargo: Import cargo volume changes (crude oil, LNG, petroleum), port throughput
  - 5_EconFinance: KOSPI, KRW/USD, Brent/WTI, VIX, financial market reactions
  - 6_Seafood: Seafood export/import values, fishing industry difficulties
  - 7_Shipping: HMM/Pan Ocean trends, global carrier updates, route strategies
  - 8_Logistics: CJ Logistics/Hyundai Glovis impacts, logistics cost changes
  - 9_PortCongestion: Port congestion, cargo delays, berth waiting times
  - 10_OtherIndustry: Petroleum/naphtha prices, LNG spot prices, fertilizer, petrochemical"""

# ── 추적 키워드 ──
TRACKED_KEYWORDS = [
    ('호르무즈|Hormuz',               '호르무즈'),
    ('이란|Iran|IRGC',                '이란'),
    ('트럼프|Trump',                  '트럼프'),
    ('나프타|naphtha|납사',            '나프타'),
    ('LNG',                           'LNG'),
    ('원유|crude oil|petroleum',      '원유'),
    ('유가|oil price',                '유가'),
    ('코스피|KOSPI',                  '코스피'),
    ('환율|exchange rate|won-dollar', '환율'),
    ('수에즈|Suez',                   '수에즈'),
    ('말라카|Malacca',                '말라카'),
    ('운임|freight rate|tanker rate', '운임'),
]


# ══════════════════════════════════════════════════════════════
# 1. 유틸리티 함수
# ══════════════════════════════════════════════════════════════

def _make_hash(url):
    return hashlib.md5(url.encode()).hexdigest()


def load_keywords(query_file, lang='en'):
    with open(query_file, 'r', encoding='utf-8') as f:
        config = json.load(f)
    keywords, kw_source = [], {}
    for qname, qs in config['query_sets'].items():
        for kw in qs.get(lang, []):
            if kw not in kw_source:
                keywords.append(kw)
                kw_source[kw] = qname
    return keywords, kw_source


def dedup_by_title(df, group_label):
    before = len(df)
    df = df.copy()
    df['_title_norm'] = df['title'].str.lower().str.strip()
    df = df.drop_duplicates(subset='_title_norm', keep='first').drop(columns=['_title_norm'])
    after = len(df)
    print(f"  [{group_label}] dedup: {before} → {after}건 (-{before-after}건)")
    return df.reset_index(drop=True)


def proportional_sample(df, max_n, min_per_kw=3, kw_col='query_keyword'):
    if len(df) <= max_n:
        return df
    kw_counts = df[kw_col].value_counts()
    kw_counts = kw_counts[kw_counts > 0]
    total     = kw_counts.sum()
    alloc = (kw_counts / total * max_n).clip(lower=min_per_kw).round().astype(int)
    diff = max_n - alloc.sum()
    if diff > 0:
        for kw in kw_counts.index:
            if diff == 0: break
            spare = int(kw_counts[kw]) - alloc[kw]
            add = min(spare, diff)
            if add > 0:
                alloc[kw] += add
                diff -= add
    elif diff < 0:
        excess = -diff
        for kw in alloc.index:
            if excess == 0: break
            reducible = alloc[kw] - min_per_kw
            reduce_by = min(reducible, excess)
            if reduce_by > 0:
                alloc[kw] -= reduce_by
                excess    -= reduce_by
    sampled = []
    for kw, n in alloc.items():
        sampled.append(df[df[kw_col] == kw].head(int(n)))
    result = pd.concat(sampled, ignore_index=True)
    print(f"  비례 샘플링: {len(df)} → {len(result)}건 (max={max_n})")
    return result


def call_llm_json(prompt, system="Return ONLY valid JSON.", max_tokens=16384, retries=3):
    text = ''
    for attempt in range(retries):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}]
            )
            text = resp.content[0].text.strip()
            if text.startswith("```"):
                text = re.sub(r'^```\w*\n?', '', text)
                text = re.sub(r'\n?```$', '', text)
            try:
                return json.loads(text)
            except json.JSONDecodeError as je:
                if 'Extra data' in str(je):
                    decoder = json.JSONDecoder()
                    obj, _ = decoder.raw_decode(text)
                    return obj
                raise
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            print(f"  ⚠ LLM 오류 ({attempt+1}회): {e}")
            return None


SYSTEM_MSG = (
    "You are a Korean supply chain risk analyst. "
    "Classify news relevance to KOREAN maritime supply chain disruption. "
    "Return ONLY valid JSON array."
)


def build_classify_prompt(batch_articles, mode='mon'):
    items = []
    for j, art in enumerate(batch_articles):
        items.append(f"{j+1}. [{art.get('language','EN')}] {art['title']}")
    news_block = '\n'.join(items)

    return f"""You are classifying news for KMI (Korea Maritime Institute) daily maritime supply chain monitoring.

Korea's key dependencies: crude oil 95% by sea (70% via Hormuz), LNG 99%, naphtha 100%, iron ore 100%, grain 77%.

RELEVANCE:
- HIGH: Direct Korea supply chain impact (chokepoint blockage, commodity shortage, shipping disruption)
- MEDIUM: Indirect impact (freight rate changes, energy price spikes, regional tension)
- LOW: Related but no immediate Korea impact
- NONE: Not related to Korean maritime supply chain

CATEGORY (HIGH/MEDIUM only, leave "" for LOW/NONE):
{CAT_BLOCK_MON}

Headlines:
{news_block}

Return ONLY valid JSON array:
[{{"index": 1, "relevance": "HIGH/MEDIUM/LOW/NONE", "topic": "2-3 word topic", "category": "1_Security or empty"}}]"""


def classify_articles(classify_df, group_label, ckpt_file):
    if len(classify_df) == 0:
        print(f"  [{group_label}] 분류할 기사 없음")
        return classify_df

    print(f"\n── [{group_label}] LLM 분류 시작 ({len(classify_df)}건, batch={BATCH_SIZE}) ──")

    classify_df = classify_df.copy()
    classify_df['relevance'] = 'NONE'
    classify_df['topic']     = ''
    classify_df['category']  = ''

    # 체크포인트 복원
    resume_batch = 0
    if os.path.exists(ckpt_file):
        ckpt = pd.read_csv(ckpt_file, encoding='utf-8-sig')
        restored = 0
        for _, crow in ckpt.iterrows():
            mask = classify_df['url_hash'] == crow['url_hash']
            if mask.any():
                idx = classify_df[mask].index[0]
                classify_df.loc[idx, 'relevance'] = crow['relevance']
                classify_df.loc[idx, 'topic']     = crow['topic']
                classify_df.loc[idx, 'category']  = crow.get('category', '')
                restored += 1
        resume_batch = restored // BATCH_SIZE
        print(f"  ⚡ 체크포인트: {restored}건 복원 → 배치 {resume_batch}부터")
        del ckpt

    target_idx    = classify_df.index.tolist()
    total         = len(target_idx)
    total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    llm_calls = errors = 0
    t0 = time.time()

    for batch_num in range(total_batches):
        if batch_num < resume_batch:
            continue

        start     = batch_num * BATCH_SIZE
        end       = min(start + BATCH_SIZE, total)
        batch_idx = target_idx[start:end]
        batch_rows = classify_df.loc[batch_idx].to_dict('records')

        prompt = build_classify_prompt(batch_rows)
        result = call_llm_json(prompt, system=SYSTEM_MSG)
        llm_calls += 1

        if result and isinstance(result, list):
            for item in result:
                j = item.get('index', 0) - 1
                if 0 <= j < len(batch_idx):
                    rel = item.get('relevance', 'LOW').upper()
                    if rel not in ('HIGH', 'MEDIUM', 'LOW', 'NONE'):
                        rel = 'LOW'
                    classify_df.loc[batch_idx[j], 'relevance'] = rel
                    classify_df.loc[batch_idx[j], 'topic']     = item.get('topic', '')
                    cat = item.get('category', '')
                    if rel in ('HIGH', 'MEDIUM') and cat in CATEGORY_KEYS:
                        classify_df.loc[batch_idx[j], 'category'] = cat
                    elif rel in ('HIGH', 'MEDIUM') and cat:
                        matched = [k for k in CATEGORY_KEYS if cat in k or k.startswith(cat)]
                        classify_df.loc[batch_idx[j], 'category'] = matched[0] if matched else cat
        else:
            errors += 1
            for idx in batch_idx:
                classify_df.loc[idx, 'relevance'] = 'LOW'
                classify_df.loc[idx, 'topic']     = 'classification_error'

        # 체크포인트 (50배치마다)
        if llm_calls % 50 == 0:
            classify_df[['url_hash','title','relevance','topic','category']].to_csv(
                ckpt_file, index=False, encoding='utf-8-sig')
            print(f"  💾 체크포인트 저장 ({llm_calls}배치)")

        if (batch_num + 1) % 10 == 0 or batch_num == total_batches - 1:
            elapsed = time.time() - t0
            print(f"  [{batch_num+1}/{total_batches}] LLM {llm_calls}회, 오류 {errors}건 ({elapsed:.0f}초)")

        time.sleep(0.3)

    # 체크포인트 정리
    if os.path.exists(ckpt_file):
        os.remove(ckpt_file)

    elapsed = time.time() - t0
    rel_counts = classify_df['relevance'].value_counts()
    print(f"\n  [{group_label}] 완료 ({elapsed:.0f}초, LLM {llm_calls}회)")
    for rel in ['HIGH', 'MEDIUM', 'LOW', 'NONE']:
        n = rel_counts.get(rel, 0)
        print(f"    {rel:8s}: {n:5d}건 ({n/len(classify_df)*100:.1f}%)")

    if '_kg_context' in classify_df.columns:
        classify_df.drop(columns=['_kg_context'], inplace=True)
    return classify_df


def save_classified(classify_df, daily_prefix):
    if len(classify_df) == 0:
        return
    save_cols = ['url_hash','title','url','seendate','domain','language',
                 'sourcecountry','query_keyword','query_group','collect_date',
                 'relevance','topic','category']
    save_cols = [c for c in save_cols if c in classify_df.columns]
    df_save = classify_df[save_cols].copy()
    rel_order = {'HIGH':0,'MEDIUM':1,'LOW':2,'NONE':3}
    df_save['_rel'] = df_save['relevance'].map(rel_order)
    df_save = df_save.sort_values(['_rel','seendate'], ascending=[True,False])
    df_save = df_save.drop(columns=['_rel']).reset_index(drop=True)

    for cd, grp in df_save.groupby('collect_date'):
        dt = cd.replace('-', '')
        ddir = os.path.join(MONITOR_DIR, dt)
        os.makedirs(ddir, exist_ok=True)
        csv_path = os.path.join(ddir, f'{daily_prefix}_daily_{dt}.csv')
        grp.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f"  저장: {csv_path} ({len(grp)}건)")

    # 누적 CSV 업데이트
    cumul_csv = os.path.join(CUMUL_DIR, f'{daily_prefix}.csv')
    if os.path.exists(cumul_csv):
        existing = pd.read_csv(cumul_csv, encoding='utf-8-sig')
        for cd in df_save['collect_date'].unique():
            existing = existing[existing['collect_date'] != cd]
        updated = pd.concat([existing, df_save], ignore_index=True)
    else:
        updated = df_save
    updated.to_csv(cumul_csv, index=False, encoding='utf-8-sig')


# ══════════════════════════════════════════════════════════════
# 2. GDELT 수집
# ══════════════════════════════════════════════════════════════

print("\n[Step 1/7] GDELT 영문 수집")
print("-" * 40)

mon_keywords, mon_kw_source = load_keywords(MON_QUERY_FILE, lang='en')
valid_keywords = [kw for kw in mon_keywords if len(kw) >= MIN_KEYWORD_LEN]
print(f"키워드: {len(valid_keywords)}개")

s_date = TARGET_DATE.strftime('%Y-%m-%d')
e_date = (TARGET_DATE + timedelta(days=1)).strftime('%Y-%m-%d')

gdelt_articles = []
seen_urls = set()
gdelt_stats, gdelt_errors = [], []
t0 = time.time()

for idx, keyword in enumerate(valid_keywords):
    qgroup = mon_kw_source[keyword]
    n_raw = n_new = 0
    err = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            f = Filters(keyword=keyword, start_date=s_date, end_date=e_date,
                        num_records=MAX_RECORDS, language=LANGUAGES)
            results = gd.article_search(f)
            if results is not None and len(results) > 0:
                n_raw = len(results)
                for _, row in results.iterrows():
                    url = row.get('url', '')
                    url_hash = _make_hash(url)
                    if url_hash not in seen_urls:
                        seen_urls.add(url_hash)
                        gdelt_articles.append({
                            'url': url, 'url_hash': url_hash,
                            'title': row.get('title', ''),
                            'seendate': row.get('seendate', ''),
                            'domain': row.get('domain', ''),
                            'language': row.get('language', ''),
                            'sourcecountry': row.get('sourcecountry', ''),
                            'query_keyword': keyword,
                            'query_group': qgroup,
                            'collect_date': str(TARGET_DATE),
                        })
                        n_new += 1
            time.sleep(SLEEP_SEC)
            break
        except Exception as e:
            err = str(e)
            is_network = any(x in err for x in ['ConnectionReset','ConnectionAborted',
                                                  'RemoteDisconnected','timeout','Timeout'])
            if is_network and attempt < MAX_RETRIES:
                time.sleep(3 * (attempt + 1))
                err = None
                continue
            gdelt_errors.append({'keyword': keyword, 'error': err})
            break

    gdelt_stats.append({'keyword': keyword, 'query_group': qgroup,
                        'raw': n_raw, 'new': n_new, 'error': err or ''})

    if (idx + 1) % 20 == 0:
        print(f"  [{idx+1}/{len(valid_keywords)}] {len(gdelt_articles)}건 수집 중...")

elapsed = time.time() - t0
print(f"✅ GDELT 수집 완료: {len(gdelt_articles)}건 ({elapsed:.0f}초)")

# Raw CSV 저장
gdelt_raw_df = pd.DataFrame(gdelt_articles)
if len(gdelt_raw_df) > 0:
    raw_csv = os.path.join(DAILY_DIR, f'gdelt_mon_daily_{DATE_TAG}.csv')
    gdelt_raw_df.to_csv(raw_csv, index=False, encoding='utf-8-sig')
    print(f"  원본 저장: {raw_csv} ({len(gdelt_raw_df)}건)")


# ══════════════════════════════════════════════════════════════
# 3. 네이버 수집
# ══════════════════════════════════════════════════════════════

print("\n[Step 2/7] 네이버 한국어 수집")
print("-" * 40)

naver_articles = []

if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
    print("⚠ NAVER 환경변수 미설정 — 네이버 수집 건너뜀")
    naver_raw_df = pd.DataFrame()
else:
    naver_keywords, naver_kw_source = load_keywords(MON_QUERY_FILE, lang='ko')
    print(f"키워드: {len(naver_keywords)}개")

    headers = {
        'X-Naver-Client-Id': NAVER_CLIENT_ID,
        'X-Naver-Client-Secret': NAVER_CLIENT_SECRET,
    }

    def _clean(text):
        text = re.sub(r'<[^>]+>', '', text)
        return html.unescape(text).strip()

    def _parse_naver_date(pub_date_str):
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(pub_date_str).date()
        except Exception:
            return None

    seen_naver = set()
    naver_stats = []
    t0 = time.time()

    for idx, keyword in enumerate(naver_keywords):
        qgroup = naver_kw_source[keyword]
        n_new = 0
        start_idx = 1

        while True:
            try:
                params = {'query': keyword, 'display': NAVER_DISPLAY,
                          'start': start_idx, 'sort': 'date'}
                resp = requests.get(NAVER_API_URL, headers=headers, params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                items = data.get('items', [])
                if not items:
                    break

                for item in items:
                    pub_d = _parse_naver_date(item.get('pubDate', ''))
                    if pub_d is None or pub_d < TARGET_DATE:
                        break
                    if pub_d > TARGET_DATE:
                        continue

                    url = item.get('link', item.get('originallink', ''))
                    url_hash = _make_hash(url)
                    if url_hash not in seen_naver:
                        seen_naver.add(url_hash)
                        naver_articles.append({
                            'url': url, 'url_hash': url_hash,
                            'title': _clean(item.get('title', '')),
                            'seendate': str(TARGET_DATE),
                            'domain': url.split('/')[2] if url else '',
                            'language': 'Korean',
                            'sourcecountry': 'South Korea',
                            'query_keyword': keyword,
                            'query_group': qgroup,
                            'collect_date': str(TARGET_DATE),
                        })
                        n_new += 1
                else:
                    if len(items) == NAVER_DISPLAY:
                        start_idx += NAVER_DISPLAY
                        time.sleep(NAVER_SLEEP)
                        continue
                break

                time.sleep(NAVER_SLEEP)
                break
            except Exception as e:
                print(f"  ⚠ 네이버 에러 ({keyword}): {e}")
                break

        naver_stats.append({'keyword': keyword, 'query_group': qgroup, 'new': n_new})

        if (idx + 1) % 20 == 0:
            print(f"  [{idx+1}/{len(naver_keywords)}] {len(naver_articles)}건...")

    elapsed = time.time() - t0
    print(f"✅ 네이버 수집 완료: {len(naver_articles)}건 ({elapsed:.0f}초)")

    naver_raw_df = pd.DataFrame(naver_articles)
    if len(naver_raw_df) > 0:
        naver_raw_csv = os.path.join(DAILY_DIR, f'naver_mon_daily_{DATE_TAG}.csv')
        naver_raw_df.to_csv(naver_raw_csv, index=False, encoding='utf-8-sig')
        print(f"  원본 저장: {naver_raw_csv} ({len(naver_raw_df)}건)")


# ══════════════════════════════════════════════════════════════
# 4. GDELT dedup + 비례 샘플링 + LLM 분류
# ══════════════════════════════════════════════════════════════

print("\n[Step 3/7] GDELT dedup + 샘플링 + LLM 분류")
print("-" * 40)

gdelt_classify_df = pd.DataFrame()
if len(gdelt_raw_df) > 0:
    _dedup = dedup_by_title(gdelt_raw_df, 'GDELT')
    _dedup['kg_entities']    = ''
    _dedup['kg_match_count'] = 0
    _dedup['_kg_context']    = ''
    gdelt_classify_df = proportional_sample(_dedup, MAX_LLM_SAMPLE_GDELT, MIN_PER_KEYWORD)

    GDELT_CKPT = os.path.join(CUMUL_DIR, 'gdelt_mon_classify_checkpoint.csv')
    gdelt_classify_df = classify_articles(gdelt_classify_df, 'GDELT', GDELT_CKPT)
    save_classified(gdelt_classify_df, 'gdelt_mon_classified')


# ══════════════════════════════════════════════════════════════
# 5. 네이버 dedup + 비례 샘플링 + LLM 분류
# ══════════════════════════════════════════════════════════════

print("\n[Step 4/7] 네이버 dedup + 샘플링 + LLM 분류")
print("-" * 40)

naver_classify_df = pd.DataFrame()
if len(naver_raw_df) > 0:
    _naver_dedup = dedup_by_title(naver_raw_df, '네이버')
    _naver_dedup['kg_entities']    = ''
    _naver_dedup['kg_match_count'] = 0
    _naver_dedup['_kg_context']    = ''
    naver_classify_df = proportional_sample(_naver_dedup, MAX_LLM_SAMPLE_NAVER, MIN_PER_KEYWORD)

    NAVER_CKPT = os.path.join(CUMUL_DIR, 'naver_mon_classify_checkpoint.csv')
    naver_classify_df = classify_articles(naver_classify_df, '네이버', NAVER_CKPT)
    save_classified(naver_classify_df, 'naver_mon_classified')


# ══════════════════════════════════════════════════════════════
# 6. 주간 롤링 파일 재구성 (Cell 10)
# ══════════════════════════════════════════════════════════════

print("\n[Step 5/7] 주간 롤링 재구성")
print("-" * 40)

for daily_prefix in ['gdelt_mon_classified', 'naver_mon_classified']:
    pattern = os.path.join(MONITOR_DIR, '*', f'{daily_prefix}_daily_*.csv')
    daily_files = sorted(glob.glob(pattern))
    if not daily_files:
        print(f"  {daily_prefix}: daily CSV 없음")
        continue

    week_map = {}
    for f in daily_files:
        df = pd.read_csv(f, encoding='utf-8-sig')
        if df.empty or 'collect_date' not in df.columns:
            continue
        for cd, grp in df.groupby('collect_date'):
            cd_date = pd.to_datetime(cd).date()
            sunday  = cd_date + timedelta(days=(6 - cd_date.weekday()))
            stag    = sunday.strftime('%Y%m%d')
            if stag not in week_map:
                week_map[stag] = {}
            week_map[stag][cd] = grp

    for stag in sorted(week_map.keys()):
        wk_df = pd.concat(list(week_map[stag].values()), ignore_index=True)
        weekly_csv = os.path.join(WEEKLY_DIR, f'{daily_prefix}_week_{stag}.csv')
        wk_df.to_csv(weekly_csv, index=False, encoding='utf-8-sig')
        print(f"  → {weekly_csv} ({len(wk_df)}건)")


# ══════════════════════════════════════════════════════════════
# 7. Excel 통계 리포트 (Cell 11)
# ══════════════════════════════════════════════════════════════

print("\n[Step 6/7] Excel 통계 리포트 생성")
print("-" * 40)

gdelt_daily_csv = os.path.join(DAILY_DIR, f'gdelt_mon_classified_daily_{DATE_TAG}.csv')
naver_daily_csv = os.path.join(DAILY_DIR, f'naver_mon_classified_daily_{DATE_TAG}.csv')

dfs_for_report = []
for csv_path, src_label in [(gdelt_daily_csv, 'GDELT'), (naver_daily_csv, '네이버')]:
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path, encoding='utf-8-sig')
        df['source'] = src_label
        dfs_for_report.append(df)

if dfs_for_report:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    combined = pd.concat(dfs_for_report, ignore_index=True)
    xlsx_path = os.path.join(DAILY_DIR, f'daily_report_{DATE_TAG}.xlsx')
    wb = openpyxl.Workbook()

    # ── 시트1: 요약 ──
    ws_sum = wb.active
    ws_sum.title = '요약'
    ws_sum['A1'] = f'KMI 해상공급망 일일 모니터링 — {TARGET_DATE}'
    ws_sum['A1'].font = Font(bold=True, size=14)

    header_fill = PatternFill('solid', fgColor='1F4E79')
    header_font = Font(bold=True, color='FFFFFF')
    thin = Side(style='thin', color='CCCCCC')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    row = 3
    ws_sum.cell(row, 1, '구분').font = header_font
    ws_sum.cell(row, 1).fill = header_fill
    for col, lbl in enumerate(['HIGH','MEDIUM','LOW','NONE','합계'], 2):
        ws_sum.cell(row, col, lbl).font = header_font
        ws_sum.cell(row, col).fill = header_fill

    row += 1
    for src in ['GDELT', '네이버', '합계']:
        sub = combined if src == '합계' else combined[combined['source'] == src]
        ws_sum.cell(row, 1, src)
        total = len(sub)
        for col, rel in enumerate(['HIGH','MEDIUM','LOW','NONE'], 2):
            n = len(sub[sub['relevance'] == rel])
            ws_sum.cell(row, col, n)
        ws_sum.cell(row, 6, total)
        row += 1

    # ── 시트2: 카테고리별 ──
    ws_cat = wb.create_sheet('카테고리별')
    ws_cat['A1'] = '카테고리별 HIGH+MEDIUM 기사 수'
    ws_cat['A1'].font = Font(bold=True, size=12)
    hm = combined[combined['relevance'].isin(['HIGH','MEDIUM'])]
    row = 3
    for cat in CAT_ORDER:
        kr = CAT_KR.get(cat, cat)
        n = len(hm[hm['category'] == cat])
        ws_cat.cell(row, 1, kr)
        ws_cat.cell(row, 2, n)
        row += 1

    # ── 시트3: 추적 키워드 ──
    ws_kw = wb.create_sheet('추적키워드')
    ws_kw['A1'] = '주요 키워드 언급 현황'
    ws_kw['A1'].font = Font(bold=True, size=12)
    row = 3
    for pattern_str, label in TRACKED_KEYWORDS:
        mask = combined['title'].str.contains(pattern_str, case=False, na=False, regex=True)
        n = mask.sum()
        ws_kw.cell(row, 1, label)
        ws_kw.cell(row, 2, n)
        row += 1

    # ── 시트4: HIGH 기사 목록 ──
    ws_high = wb.create_sheet('HIGH기사')
    high_df = combined[combined['relevance'] == 'HIGH'][
        ['source','title','category','query_keyword','url']
    ].head(100)
    for col_idx, col_name in enumerate(high_df.columns, 1):
        ws_high.cell(1, col_idx, col_name).font = header_font
        ws_high.cell(1, col_idx).fill = header_fill
    for r_idx, row_data in enumerate(high_df.itertuples(index=False), 2):
        for c_idx, val in enumerate(row_data, 1):
            ws_high.cell(r_idx, c_idx, str(val) if val else '')

    wb.save(xlsx_path)
    print(f"✅ Excel 저장: {xlsx_path}")
else:
    print("  분류 CSV 없음 — Excel 생성 건너뜀")


# ══════════════════════════════════════════════════════════════
# 8. LLM 일별 리포트 생성 (Cell 12)
# ══════════════════════════════════════════════════════════════

print("\n[Step 7/7] LLM 일별 리포트 + HTML 뷰어 생성")
print("-" * 40)

LLM_REPORT_MODEL = "claude-sonnet-4-6"
REPORT_SYSTEM = """당신은 KMI(한국해양수산개발원) 해상 공급망 위기 모니터링 시스템의 전문 분석가입니다.
현재 핵심 사태: 2026년 호르무즈 해협 봉쇄 위기 (역사상 최초 실제 봉쇄)
분석 관점: 글로벌 해상 공급망 교란 → 한국 경제·산업·소비자에 미치는 영향
원칙: 기사 제목 사실 기반으로만 서술. 과장·추론 금지. 불확실하면 '~로 보임', '~가능성' 명시.
출력: 반드시 유효한 JSON만 출력. 설명 텍스트나 마크다운 코드블록 없이 JSON 객체만."""

def _build_report_prompt(gdelt_df, naver_df, date_str):
    # ── 합치기 + source_type: sourcecountry 기준 (v3 notebook 동일) ──
    df_all = pd.concat([gdelt_df.copy(), naver_df.copy()], ignore_index=True) \
             if (len(gdelt_df) + len(naver_df)) > 0 else pd.DataFrame()
    if len(df_all) == 0:
        return None, None

    df_all['source_type'] = df_all['sourcecountry'].apply(
        lambda x: 'domestic' if x == 'South Korea' else 'international'
    )

    # ── 통계 ──
    total      = len(df_all)
    rel_counts = df_all['relevance'].value_counts()
    n_high = int(rel_counts.get('HIGH',   0))
    n_med  = int(rel_counts.get('MEDIUM', 0))
    n_low  = int(rel_counts.get('LOW',    0))
    n_none = int(rel_counts.get('NONE',   0))

    hm          = df_all[df_all['relevance'].isin(['HIGH','MEDIUM'])].copy()
    hm_intl     = hm[hm['source_type'] == 'international']
    hm_domestic = hm[hm['source_type'] == 'domestic']

    # ── 카테고리별 통계 ──
    cat_stats = {}
    for cat in CAT_ORDER:
        cat_hm = hm[hm['category'] == cat]
        h = len(cat_hm[cat_hm['relevance'] == 'HIGH'])
        m = len(cat_hm[cat_hm['relevance'] == 'MEDIUM'])
        cat_stats[cat] = {'high': h, 'med': m, 'total': h + m}

    # ── 카테고리별 기사 블록 ──
    active_cats    = []
    articles_block = ""
    for cat in CAT_ORDER:
        cat_hm = hm[hm['category'] == cat]
        if len(cat_hm) == 0:
            continue
        active_cats.append(cat)
        intl_rows = cat_hm[cat_hm['source_type'] == 'international'].head(10)
        dom_rows  = cat_hm[cat_hm['source_type'] == 'domestic'].head(10)
        h = len(cat_hm[cat_hm['relevance'] == 'HIGH'])
        m = len(cat_hm[cat_hm['relevance'] == 'MEDIUM'])
        articles_block += f"\n[{CAT_KR[cat]}] HIGH:{h} MEDIUM:{m}\n"
        if len(intl_rows) > 0:
            articles_block += f"  해외({len(intl_rows)}건):\n"
            for _, r in intl_rows.iterrows():
                articles_block += f"    - {r['title']}\n"
        if len(dom_rows) > 0:
            articles_block += f"  국내({len(dom_rows)}건):\n"
            for _, r in dom_rows.iterrows():
                articles_block += f"    - {r['title']}\n"

    cat_keys = ', '.join(f'"{c}"' for c in active_cats)

    data = {
        'total': total, 'n_high': n_high, 'n_med': n_med,
        'n_low': n_low, 'n_none': n_none,
        'hm': hm, 'hm_intl': hm_intl, 'hm_domestic': hm_domestic,
        'cat_stats': cat_stats,
    }

    prompt = f"""분석 대상일: {date_str}
HIGH+MEDIUM 기사: 총 {len(hm)}건 (해외 {len(hm_intl)}건 / 국내 {len(hm_dom)}건)

{articles_block}

아래 JSON 형식으로 분석하세요. 활성 카테고리 키: {cat_keys}

{{
  "executive_summary": "오늘의 핵심 동향 3~4문장. 가장 중요한 변화·위험 신호 중심. 구체적 수치·고유명사 활용.",

  "categories": {{
    "<카테고리코드>": {{
      "overseas": "해외 상황 2~4문장. 국제 기사 기반, 지금 무슨 일이 벌어지고 있는지. 없으면 빈 문자열.",
      "korea_impact": "국내 영향 2~4문장. 국내 기사 기반 한국 산업·경제 파급. 국내 기사 없으면 해외 상황의 한국 파급 가능성 서술."
    }}
  }},

  "flow": {{
    "triggers": {{
      "route":     {{ "overseas": "항로·초크포인트 차단·위협 해외 상황 2~4문장. 없으면 빈 문자열.", "korea_impact": "한국 수입 항로 위협 파급 2~4문장." }},
      "source":    {{ "overseas": "공급원 교란(수출금지·제재·감산) 해외 상황 2~4문장. 없으면 빈 문자열.", "korea_impact": "한국 원자재·에너지 공급원 파급 2~4문장." }},
      "logistics": {{ "overseas": "운임·항만·선박 교란 해외 상황 2~4문장. 없으면 빈 문자열.", "korea_impact": "한국 해운·수출입 물류 파급 2~4문장." }}
    }},
    "domestic_impact": {{
      "경제·금융":  "국내 거시경제·금융시장 영향 2~3문장. 관련 기사 없으면 빈 문자열.",
      "정유·화학":  "정유·석유화학 산업 영향 2~3문장. 없으면 빈 문자열.",
      "해운·물류":  "국내 해운사·물류 업계 영향 2~3문장. 없으면 빈 문자열.",
      "식품·농업":  "식품·농업·수산 영향 2~3문장. 없으면 빈 문자열.",
      "기타산업":   "그 외 주목할 산업 영향 1~2문장. 없으면 빈 문자열."
    }}
  }},

  "changes": {{
    "new":       ["어제 없었다가 오늘 새로 등장한 이슈 (없으면 빈 배열)"],
    "escalated": ["어제보다 심화·확대된 이슈 (없으면 빈 배열)"],
    "resolved":  ["어제 있었으나 오늘 소멸·완화된 이슈 (없으면 빈 배열)"]
  }}
}}

분석 원칙:
- 기사 제목 사실 기반, 과장 없이 현황 중심
- 불확실한 내용은 '~로 보임', '~가능성' 명시
- 한국 경제·산업 시사점 반드시 포함
- JSON 외 다른 텍스트 출력 금지"""

    return prompt, data

# 분류 CSV 로드
gdelt_cls = pd.read_csv(gdelt_daily_csv, encoding='utf-8-sig') if os.path.exists(gdelt_daily_csv) else pd.DataFrame()
naver_cls = pd.read_csv(naver_daily_csv, encoding='utf-8-sig') if os.path.exists(naver_daily_csv) else pd.DataFrame()

if len(gdelt_cls) > 0 or len(naver_cls) > 0:
    report_prompt, report_data = _build_report_prompt(gdelt_cls, naver_cls, str(TARGET_DATE))

    if report_prompt is None:
        print("  분류 결과 없음 — LLM 리포트 건너뜀")
    else:
        print(f"  프롬프트 길이: {len(report_prompt)}자")

        report_client = anthropic.Anthropic()
        resp = report_client.messages.create(
            model=LLM_REPORT_MODEL,
            max_tokens=16384,
            system=REPORT_SYSTEM,
            messages=[{"role": "user", "content": report_prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = re.sub(r'^```\w*\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)

        try:
            report_json = json.loads(raw)
        except Exception as e1:
            print(f"⚠ JSON 1차 파싱 실패: {e1}")
            print(f"  raw 앞 300자: {raw[:300]}")
            try:
                from json_repair import repair_json
                report_json = json.loads(repair_json(raw))
                print("✅ json-repair로 복구 성공")
            except Exception as e2:
                print(f"⚠ json-repair 실패: {e2}")
                decoder = json.JSONDecoder()
                report_json, _ = decoder.raw_decode(raw)

        # ── JSON 저장 (v3 notebook 동일 flat 구조) ──
        json_path = os.path.join(DAILY_DIR, f'daily_report_llm_{DATE_TAG}.json')
        json_data = {
            'date':     str(TARGET_DATE),
            'date_key': DATE_TAG,
            'n_total':  report_data['total'],
            'n_high':   report_data['n_high'],
            'n_med':    report_data['n_med'],
            'n_low':    report_data['n_low'],
            'n_none':   report_data['n_none'],
            'n_intl':   int(len(report_data['hm_intl'])),
            'n_dom':    int(len(report_data['hm_domestic'])),
            'llm_result': report_json,
        }
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        print(f"  JSON 저장: {json_path}")

        # ── Markdown (v3 notebook 동일 구조) ──
        md_path = os.path.join(DAILY_DIR, f'daily_report_llm_{DATE_TAG}.md')
        lines = []
        L = lines.append
        L(f"# 해상 공급망 모니터링 일일 분석 — {TARGET_DATE}")
        L(f"")
        L(f"> 생성 모델: {LLM_REPORT_MODEL}  |  분석 기준: HIGH+MEDIUM {len(report_data['hm'])}건 "
          f"(해외 {len(report_data['hm_intl'])}건 / 국내 {len(report_data['hm_domestic'])}건)")
        L(f"")
        L("---")
        L("")
        L("## 📌 오늘의 핵심")
        L("")
        L(report_json.get("executive_summary", ""))
        L("")
        L("---")
        L("")
        L("## 카테고리별 분석")
        L("")
        cats_out = report_json.get("categories", {})
        for cat in CAT_ORDER:
            if cat not in cats_out: continue
            cat_data = cats_out[cat]
            cat_kr   = CAT_KR.get(cat, cat)
            s        = report_data['cat_stats'].get(cat, {})
            h, m     = s.get('high', 0), s.get('med', 0)
            if h + m == 0: continue
            L(f"### {cat_kr}  _(HIGH {h} / MEDIUM {m})_")
            L("")
            overseas     = cat_data.get("overseas", "").strip()
            korea_impact = cat_data.get("korea_impact", "").strip()
            if overseas:
                L("**🌐 해외 상황**"); L(""); L(overseas); L("")
            if korea_impact:
                L("**🇰🇷 국내 영향**"); L(""); L(korea_impact); L("")
        L("---"); L("")
        L("## 통계 요약"); L("")
        L("| 구분 | 건수 | 비중 |"); L("|------|-----:|-----:|")
        for label, cnt in [('총 수집', report_data['total']), ('HIGH', report_data['n_high']),
                           ('MEDIUM', report_data['n_med']), ('LOW', report_data['n_low']),
                           ('NONE', report_data['n_none'])]:
            L(f"| {label} | {cnt:,} | {cnt/report_data['total']*100:.1f}% |")
        L("")
        L("| 카테고리 | HIGH | MEDIUM | 합계 |"); L("|----------|-----:|-------:|-----:|")
        for cat in CAT_ORDER:
            s = report_data['cat_stats'].get(cat, {})
            if s.get('total', 0) > 0:
                L(f"| {CAT_KR[cat]} | {s['high']} | {s['med']} | {s['total']} |")
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print(f"  MD 저장: {md_path}")

        # ── DOCX (v3 notebook 동일 구조) ──
        try:
            from docx import Document
            from docx.shared import Pt, RGBColor, Cm
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            doc = Document()
            for sec in doc.sections:
                sec.top_margin    = Cm(2.5); sec.bottom_margin = Cm(2.5)
                sec.left_margin   = Cm(3.0); sec.right_margin  = Cm(3.0)
            p = doc.add_heading(f'해상 공급망 모니터링 일일 분석 — {TARGET_DATE}', level=1)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            meta = doc.add_paragraph()
            meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = meta.add_run(
                f'생성 모델: {LLM_REPORT_MODEL}  |  HIGH+MEDIUM {len(report_data["hm"])}건 '
                f'(해외 {len(report_data["hm_intl"])}건 / 국내 {len(report_data["hm_domestic"])}건)'
            )
            run.font.size = Pt(9); run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
            doc.add_paragraph()
            doc.add_heading('오늘의 핵심', level=2)
            doc.add_paragraph(report_json.get('executive_summary', ''))
            doc.add_paragraph()
            doc.add_heading('카테고리별 분석', level=2)
            for cat in CAT_ORDER:
                if cat not in cats_out: continue
                cat_data = cats_out[cat]
                cat_kr   = CAT_KR.get(cat, cat)
                s        = report_data['cat_stats'].get(cat, {})
                h, m_cnt = s.get('high', 0), s.get('med', 0)
                if h + m_cnt == 0: continue
                doc.add_heading(f'{cat_kr}  (HIGH {h} / MEDIUM {m_cnt})', level=3)
                overseas     = cat_data.get('overseas', '').strip()
                korea_impact = cat_data.get('korea_impact', '').strip()
                if overseas:
                    p = doc.add_paragraph(); p.add_run('🌐 해외 상황').bold = True
                    doc.add_paragraph(overseas)
                if korea_impact:
                    p = doc.add_paragraph(); p.add_run('🇰🇷 국내 영향').bold = True
                    doc.add_paragraph(korea_impact)
            doc.add_page_break()
            doc.add_heading('통계 요약', level=2)
            tbl = doc.add_table(rows=6, cols=3); tbl.style = 'Table Grid'
            for j, h_txt in enumerate(['구분', '건수', '비중']):
                tbl.rows[0].cells[j].text = h_txt
                tbl.rows[0].cells[j].paragraphs[0].runs[0].bold = True
            for i_r, (label, cnt) in enumerate([
                ('총 수집', report_data['total']), ('HIGH',   report_data['n_high']),
                ('MEDIUM',  report_data['n_med']),  ('LOW',    report_data['n_low']),
                ('NONE',    report_data['n_none'])
            ]):
                tbl.rows[i_r+1].cells[0].text = label
                tbl.rows[i_r+1].cells[1].text = str(cnt)
                tbl.rows[i_r+1].cells[2].text = f"{cnt/report_data['total']*100:.1f}%"
            docx_path = os.path.join(DAILY_DIR, f'daily_report_llm_{DATE_TAG}.docx')
            doc.save(docx_path)
            print(f"  DOCX 저장: {docx_path}")
        except Exception as e:
            print(f"  ⚠ DOCX 생성 실패: {e}")

        print(f"✅ LLM 리포트 생성 완료")
else:
    print("  분류 결과 없음 — LLM 리포트 건너뜀")


# ══════════════════════════════════════════════════════════════
# 9. daily_brief.html 생성 (Cell 13)
# ══════════════════════════════════════════════════════════════

print("\n[HTML 뷰어 생성]")
print("-" * 40)

pattern = os.path.join(MONITOR_DIR, '????????', 'daily_report_llm_????????.json')
json_files = sorted(glob.glob(pattern), reverse=True)

if json_files:
    days = []
    for jf in json_files[:30]:  # 최근 30일
        try:
            with open(jf, encoding='utf-8') as f:
                days.append(json.load(f))
        except Exception:
            pass

    if days:
        # HTML 뷰어는 별도 스크립트로 분리 (노트북 Cell 13 로직)
        import subprocess
        result = subprocess.run(
            ['python3', 'scripts/build_viewer.py'],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print("✅ daily_brief.html 생성 완료")
        else:
            print(f"  ⚠ 뷰어 생성 실패: {result.stderr[:200]}")
else:
    print("  JSON 없음 — 뷰어 생성 건너뜀")

print(f"\n{'='*60}")
print(f"  ✅ 일일 파이프라인 완료: {TARGET_DATE}")
print(f"  산출물 위치: monitoring/{DATE_TAG}/")
print(f"{'='*60}")
