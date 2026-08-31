#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weekly_pipeline.py
===================
해상 공급망 위기 주간 모니터링 자동화 스크립트
(news_kg_mapping_v7.ipynb + scenario_generator_v11.ipynb 를 GitHub Actions 용
 스크립트로 변환)

Part 1 (Steps 1-5): KG 로드 → 주간 뉴스 로드/KG매칭 → 비례 층화 샘플링
                     → Phase A 2축 분류(Haiku) → 자동 지표 수집(indicator_weekly.csv)
Part 2 (Steps 6-9): 아래 파일 하단 placeholder 참조 (별도 작업)

실행 방식:
    python scripts/weekly_pipeline.py [YYYYMMDD]
    WEEK_TAG 미지정 시 monitoring/weekly/ 의 최신 주간 CSV에서 자동 탐색

환경변수:
    ANTHROPIC_API_KEY    Claude API 키

전제:
    - collect_daily.py 가 매일 실행되어 monitoring/weekly/gdelt_mon_classified_week_*.csv
      / naver_mon_classified_week_*.csv 가 생성되어 있어야 함
    - GitHub Actions에서 daily 파이프라인 완료 후 매주 월요일 실행
"""

import warnings
warnings.filterwarnings('ignore')

import glob
import io
import json
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import networkx as nx
import requests
import yfinance as yf

import anthropic

# ══════════════════════════════════════════════════════════════
# 0. 기본 설정
# ══════════════════════════════════════════════════════════════

# ── API 클라이언트 ──
client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수 자동 사용
HAIKU_MODEL  = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"

# ── 경로 설정 (스크립트 위치 기준) ──
BASE_DIR       = Path(__file__).resolve().parent.parent
MONITORING_DIR = BASE_DIR / "monitoring"
WEEKLY_DIR     = MONITORING_DIR / "weekly"
INDICATORS_DIR = MONITORING_DIR / "indicators"

KG_FILE = BASE_DIR / "seed_kg_v4.json"

MKG_DIR       = BASE_DIR.parent / "maritime-kg"
INDICATOR_CSV = BASE_DIR / "indicator_weekly.csv"
DATES_CSV     = BASE_DIR / "indicator_weekly_dates.csv"
WEEK_START    = "2019-01-01"

for _d in [MONITORING_DIR, WEEKLY_DIR, INDICATORS_DIR]:
    os.makedirs(_d, exist_ok=True)

# ── WEEK_TAG 결정 (CLI 인수 또는 monitoring/weekly/ 최신 주간 파일 자동 탐색) ──
def resolve_week_tag(cli_arg=None):
    """WEEK_TAG(YYYYMMDD) 결정: CLI 인수 우선, 없으면 monitoring/weekly/ 최신 주간 파일에서 자동 탐색."""
    if cli_arg:
        if not re.fullmatch(r"\d{8}", cli_arg):
            raise ValueError(f"WEEK_TAG 형식이 잘못됨 (YYYYMMDD 필요): {cli_arg}")
        return cli_arg

    week_files = sorted(glob.glob(str(WEEKLY_DIR / "gdelt_mon_classified_week_*.csv")))
    if not week_files:
        raise FileNotFoundError(
            f"{WEEKLY_DIR} 에 gdelt_mon_classified_week_*.csv 파일이 없습니다."
        )
    week_tag = re.search(r"week_(\d{8})\.csv$", week_files[-1]).group(1)
    print(f"★ WEEK_TAG 자동 탐색: {week_tag} (최신 주간 파일 기준)")
    return week_tag


# ══════════════════════════════════════════════════════════════
# 1. 공통 유틸리티
# ══════════════════════════════════════════════════════════════

def call_llm_json(prompt, system="Return ONLY valid JSON.", max_tokens=2048, retries=3):
    """Anthropic API 호출(Haiku) → JSON 파싱 (재시도 포함). news_kg_mapping_v7 Cell 1 기반."""
    text = ""
    for attempt in range(retries):
        try:
            resp = client.messages.create(
                model=HAIKU_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}]
            )
            text = resp.content[0].text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```\w*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            return json.loads(text)
        except json.JSONDecodeError as e:
            # Extra data 오류: JSON 뒤 추가 텍스트 제거 후 재시도
            s_idx = text.find('{')
            e_idx = text.rfind('}') + 1
            if s_idx != -1 and e_idx > s_idx:
                try:
                    return json.loads(text[s_idx:e_idx])
                except json.JSONDecodeError:
                    pass
            if attempt < retries - 1:
                time.sleep(1 * (attempt + 1))
                continue
            print(f"  ⚠ LLM JSON 파싱 실패 ({attempt+1}회): {e}")
            print(f"  Raw: {text[:200]}")
            return None
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1 * (attempt + 1))
                continue
            print(f"  ⚠ LLM 호출 실패 ({attempt+1}회): {e}")
            return None


def call_llm_sonnet(prompt, system="Return ONLY valid JSON.", max_tokens=4096, retries=3):
    """Anthropic API 호출(Sonnet) → JSON 파싱 (재시도 포함). scenario_generator_v11 Cell 1 기반.
    temperature=0.1 은 anthropic<1.0.0 (requirements.txt 고정) 에서만 유효 — 노트북 원본 값 유지."""
    text = ''
    for attempt in range(retries):
        try:
            resp = client.messages.create(
                model=SONNET_MODEL, max_tokens=max_tokens,
                temperature=0.1,
                system=system,
                messages=[{"role": "user", "content": prompt}]
            )
            # ── stop_reason 체크: max_tokens 잘림 즉시 감지 ──
            if resp.stop_reason == 'max_tokens':
                print(f"  ⚠ max_tokens 도달 ({max_tokens} tokens, {len(resp.content[0].text)} chars) — 재시도 불필요")
                return None   # 같은 프롬프트를 재시도해도 동일하게 잘림
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
                time.sleep(1 * (attempt + 1))
                continue
            print(f"  ⚠ LLM 실패 ({attempt+1}회): {e}")
            print(f"  Raw: {text[:150]}...")
            return None


# ══════════════════════════════════════════════════════════════
# STEP 1: KG 로드 + entity_patterns 구축
# (news_kg_mapping_v7.ipynb Cell 1)
# ══════════════════════════════════════════════════════════════

def build_entity_patterns(nodes):
    """KG 노드 → 뉴스 매칭 키워드 사전 (aliases 전체 활용)"""
    patterns = {}

    def add(kw, nid, name, ntype):
        kw = kw.strip()
        if len(kw) >= 2:
            patterns[kw.lower()] = (nid, name, ntype)

    for nid, n in nodes.items():
        ntype = n.get("node_type", "")
        name  = n.get("name", "")
        name_en = n.get("nameEn", "")

        # 1) aliases (가장 우선: 명시적으로 정의된 키워드)
        for alias in n.get("aliases", []):
            add(alias, nid, name, ntype)

        # 2) 영문 이름 전체 + 개별 토큰
        #    ⚠ 제네릭 지리 용어(strait, canal 등)는 단독 패턴 금지
        #      "Lombok Strait" → "strait" 단독 등록 시 모든 해협 기사에 오매칭
        _SKIP_TOKENS = {
            # 지리 용어
            'strait', 'canal', 'channel', 'waterway', 'sea', 'ocean', 'gulf',
            'bay', 'port', 'passage', 'route', 'waters', 'lane', 'basin',
            # 제네릭 동사/명사 (단독 패턴 시 오매핑 위험)
            'export', 'import', 'control', 'ban', 'restriction', 'crisis',
            'trade', 'supply', 'demand', 'price', 'market', 'risk', 'impact',
            'flow', 'policy', 'security', 'global', 'international',
        }
        if name_en:
            add(name_en, nid, name, ntype)
            for tok in name_en.split():
                if len(tok) >= 3 and tok.lower() not in _SKIP_TOKENS:
                    add(tok, nid, name, ntype)

        # 3) ID 접두사 제거 (CP_Hormuz → hormuz)
        short_id = nid.split("_", 1)[-1] if "_" in nid else nid
        if len(short_id) >= 3:
            add(short_id, nid, name, ntype)

        # 4) 한글 이름 (2자 이상)
        if name and any("가" <= c <= "힣" for c in name):
            add(name, nid, name, ntype)

        # 5) 초크포인트 변형
        if ntype == "chokepoint":
            for suffix in ["strait", "canal", "channel", "waterway"]:
                add(f"{short_id.lower()} {suffix}", nid, name, ntype)
                add(f"strait of {short_id.lower()}", nid, name, ntype)

        # 6) 품목 카테고리별 추가 키워드 (동적 추출)
        if ntype == "commodity_flow":
            cat = n.get("category", "")
            extra = {
                "EnergyFlow":     ["crude oil", "petroleum", "brent", "wti", "crude"],
                "GasFlow":        ["natural gas", "lng", "liquefied natural gas", "lpg"],
                "ChemicalFlow":   ["naphtha", "petrochemical", "ethylene", "propylene"],
                "MetalFlow":      ["iron ore", "steel", "coking coal", "copper"],
                "GrainFlow":      ["wheat", "corn", "soybean", "grain", "maize"],
                "FertilizerFlow": ["urea", "fertilizer", "ammonia", "adblue"],
                "SemiMaterial":   ["hydrogen fluoride", "photoresist", "semiconductor material"],
            }.get(cat, [])
            for kw in extra:
                add(kw, nid, name, ntype)

    return patterns


def step1_load_kg():
    """KG 로드 + NetworkX 그래프 구축 + entity_patterns 구축.

    Returns:
        dict: {nodes, edges, G, SEED_EVT_IDS, entity_patterns, match_entities}
    """
    print(f"\n{'='*60}")
    print("  STEP 1: KG 로드 + entity_patterns 구축")
    print(f"{'='*60}")

    with open(KG_FILE, encoding="utf-8") as f:
        kg_raw = json.load(f)

    nodes = kg_raw["nodes"]   # dict {nid: {...}}
    edges = kg_raw["edges"]   # list [{from, to, relation, ...}]

    print(f"=== KG 로드 완료 ===")
    print(f"  노드: {len(nodes)}개  |  엣지: {len(edges)}개")
    type_counts = Counter(n.get("node_type", "?") for n in nodes.values())
    for t, c in sorted(type_counts.items()):
        print(f"    {t}: {c}")

    # NetworkX MultiDiGraph
    G = nx.MultiDiGraph()
    for nid, ndata in nodes.items():
        G.add_node(nid, **ndata)
    for e in edges:
        G.add_edge(e["from"], e["to"],
                   **{k: v for k, v in e.items() if k not in ("from", "to")})
    print(f"  NetworkX: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    # seed KG의 EVT_ 노드 ID 목록 (동적 감지 시 중복 방지용)
    SEED_EVT_IDS = {nid for nid, n in nodes.items() if n.get("node_type") == "crisis_event"}
    print(f"  seed EVT_ 노드: {sorted(SEED_EVT_IDS)}")

    entity_patterns = build_entity_patterns(nodes)
    print(f"\n=== entity_patterns ===")
    print(f"  총 패턴: {len(entity_patterns)}개")
    type_dist = Counter(v[2] for v in entity_patterns.values())
    for t, c in sorted(type_dist.items()):
        print(f"    {t}: {c}패턴")

    def match_entities(title, patterns=entity_patterns, graph=G, nodes_ref=nodes):
        """기사 제목 → KG 엔티티 매칭 (긴 패턴 우선)"""
        title_lower = title.lower()
        matched = {}  # {eid: (name, type)}
        for pattern, (eid, ename, etype) in sorted(patterns.items(), key=lambda x: -len(x[0])):
            if re.search(r"\b" + re.escape(pattern) + r"\b", title_lower):
                if eid not in matched:
                    matched[eid] = (ename, etype)
        kg_ctx_lines = []
        for eid, (ename, etype) in matched.items():
            neighbors = []
            if eid in graph:
                for _, tgt, _, ed in graph.out_edges(eid, data=True, keys=True):
                    neighbors.append(f"{ed.get('relation','')}→{nodes_ref.get(tgt,{}).get('name',tgt)}")
            kg_ctx_lines.append(f"[{etype}] {ename}({eid}): {'; '.join(neighbors[:4])}")
        return {
            "matched_entities": list(matched.keys()),
            "match_count": len(matched),
            "kg_context": "\n".join(kg_ctx_lines),
        }

    return {
        "kg_raw": kg_raw,
        "nodes": nodes,
        "edges": edges,
        "G": G,
        "SEED_EVT_IDS": SEED_EVT_IDS,
        "entity_patterns": entity_patterns,
        "match_entities": match_entities,
    }


# ══════════════════════════════════════════════════════════════
# STEP 2: 주간 분류 CSV 로드 + KG 엔티티 매칭
# (news_kg_mapping_v7.ipynb Cell 2)
# ══════════════════════════════════════════════════════════════

def step2_load_and_match(week_tag, entity_patterns, match_entities):
    """monitoring/weekly/{gdelt,naver}_mon_classified_week_{week_tag}.csv 로드 + KG 엔티티 매칭.

    Returns:
        pd.DataFrame: news_df (kg_entities, kg_match_count, _kg_context 컬럼 추가)
    """
    print(f"\n{'='*60}")
    print("  STEP 2: 주간 분류 CSV 로드 + KG 엔티티 매칭")
    print(f"{'='*60}")

    GDELT_WEEKLY = WEEKLY_DIR / f"gdelt_mon_classified_week_{week_tag}.csv"
    NAVER_WEEKLY = WEEKLY_DIR / f"naver_mon_classified_week_{week_tag}.csv"

    # ── 파일 로드 ───────────────────────────────────────────────
    dfs = []
    for fpath, label in [(GDELT_WEEKLY, "GDELT(영문)"), (NAVER_WEEKLY, "네이버(한글)")]:
        if os.path.exists(fpath):
            _df = pd.read_csv(fpath, encoding="utf-8-sig")
            _df["_source"] = label
            dfs.append(_df)
            print(f"  {label}: {len(_df)}건 로드 ({fpath})")
        else:
            print(f"  ⚠ {label} 파일 없음: {fpath}")

    if not dfs:
        raise FileNotFoundError("주간 파일이 없습니다. WEEK_TAG와 WEEKLY_DIR를 확인하세요.")

    news_df = pd.concat(dfs, ignore_index=True)
    news_df["pub_date"] = pd.to_datetime(
        news_df.get("seendate", news_df.get("pub_date", pd.NaT)),
        format="mixed", utc=True, errors="coerce"
    ).dt.tz_localize(None)
    news_df["collect_date"] = news_df["collect_date"].astype(str)

    print(f"\n=== 주간 파일 로드 완료 ===")
    print(f"  총: {len(news_df)}건 | 기간: {week_tag[:4]}-{week_tag[4:6]}-{week_tag[6:]}(일요일) 주")
    print(f"  언어별: {dict(news_df['language'].value_counts())}")
    print(f"  날짜별 건수:")
    for cd, cnt in news_df.groupby("collect_date").size().items():
        hm = len(news_df[(news_df["collect_date"]==cd) & news_df["relevance"].isin(["HIGH","MEDIUM"])])
        print(f"    {cd}: 전체 {cnt}건 | HIGH+MED {hm}건")
    print(f"  relevance: {dict(news_df['relevance'].value_counts())}")
    if "query_group" in news_df.columns:
        print(f"  차원별: {dict(news_df['query_group'].value_counts())}")

    # ── KG 엔티티 매칭 ─────────────────────────────────────────
    print("\n=== KG 엔티티 매칭 실행 ===")
    match_results = [match_entities(str(row["title"]), entity_patterns) for _, row in news_df.iterrows()]
    news_df["kg_entities"]    = [json.dumps(m["matched_entities"]) for m in match_results]
    news_df["kg_match_count"] = [m["match_count"] for m in match_results]
    news_df["_kg_context"]    = [m["kg_context"] for m in match_results]
    print(f"  KG 매칭 1+건: {(news_df['kg_match_count']>0).sum()}건")
    print(f"  KG 매칭 0건:  {(news_df['kg_match_count']==0).sum()}건")

    return news_df


# ══════════════════════════════════════════════════════════════
# STEP 3: 비례 층화 샘플링
# (news_kg_mapping_v7.ipynb Cell 3)
# ══════════════════════════════════════════════════════════════
# 샘플링 전략:
#   1. LOW/NONE 제외 → HIGH+MEDIUM만 대상
#   2. 일별 × 차원(query_group)별 HIGH+MEDIUM 기사 건수 → 비중 계산
#      (HIGH=0이더라도 MEDIUM이 있으면 배분에 포함)
#   3. 비중에 따라 영문 200건 + 한글 200건 배분 (일별)
#   4. 각 차원 할당량 내에서 HIGH 70% + MEDIUM 30%
# ─────────────────────────────────────────────────────────────

MAX_EN_PER_DAY  = 200   # 영문(GDELT) 일별 최대
MAX_KO_PER_DAY  = 200   # 한글(네이버) 일별 최대
HIGH_RATIO      = 0.7   # 차원 할당량 중 HIGH 비율
MED_RATIO       = 0.3   # 차원 할당량 중 MEDIUM 비율


def stratified_sample(day_df, max_n, high_ratio=0.7):
    """
    하루치 기사를 차원별 HIGH+MEDIUM 비중으로 max_n건 샘플링.
    - HIGH=0이어도 MEDIUM이 있으면 배분에 포함 (최소 1건 보장)
    - 차원 없는 기사(query_group 없음) → 별도 풀로 처리
    - HIGH 부족 시 MEDIUM으로 보충, MEDIUM 부족 시 HIGH로 보충
    """
    hm_df = day_df[day_df["relevance"].isin(["HIGH", "MEDIUM"])].copy()
    if len(hm_df) == 0:
        return pd.DataFrame()
    if len(hm_df) <= max_n:
        return hm_df  # 이미 max_n 이하이면 전부 사용

    has_dim = "query_group" in hm_df.columns and hm_df["query_group"].notna().any()

    if not has_dim:
        # 차원 정보 없으면 HIGH 우선 단순 샘플링
        high_df = hm_df[hm_df["relevance"] == "HIGH"]
        med_df  = hm_df[hm_df["relevance"] == "MEDIUM"]
        n_h = min(len(high_df), round(max_n * high_ratio))
        n_m = min(len(med_df),  max_n - n_h)
        n_h = min(len(high_df), max_n - n_m)
        return pd.concat([high_df.head(n_h), med_df.head(n_m)], ignore_index=True)

    # ── 차원별 HIGH+MEDIUM 건수 → 비중 계산 ──
    # HIGH=0이어도 MEDIUM이 있으면 배분에 포함 (최소 1건 보장)
    dim_hm_cnt = hm_df.groupby("query_group").size()
    total_hm = dim_hm_cnt.sum()

    alloc = {
        dim: max(1, round(cnt / total_hm * max_n))
        for dim, cnt in dim_hm_cnt.items()
    }

    # ── 배분 합 조정 (반올림 오차) ──
    total_alloc = sum(alloc.values())
    dims_sorted = sorted(alloc, key=lambda x: -alloc[x])
    while total_alloc > max_n:
        for d in dims_sorted:
            if alloc[d] > 1:
                alloc[d] -= 1
                total_alloc -= 1
            if total_alloc <= max_n:
                break
    while total_alloc < max_n:
        for d in dims_sorted:
            alloc[d] += 1
            total_alloc += 1
            if total_alloc >= max_n:
                break

    # ── 차원별 HIGH 70% + MEDIUM 30% 채우기 ──
    parts = []
    for dim, n_alloc in alloc.items():
        dim_df  = hm_df[hm_df["query_group"] == dim]
        high_df = dim_df[dim_df["relevance"] == "HIGH"]
        med_df  = dim_df[dim_df["relevance"] == "MEDIUM"]

        n_h = min(len(high_df), round(n_alloc * high_ratio))
        n_m = min(len(med_df),  n_alloc - n_h)
        # 부족분 보충
        if n_h + n_m < n_alloc:
            n_h = min(len(high_df), n_alloc - n_m)
        if n_h + n_m < n_alloc:
            n_m = min(len(med_df),  n_alloc - n_h)

        parts.append(high_df.head(n_h))
        parts.append(med_df.head(n_m))

    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def step3_stratified_sampling(news_df):
    """언어별(GDELT/네이버) × 일별 비례 층화 샘플링 실행.

    Returns:
        pd.DataFrame: sampled_df (Phase A 대상)
    """
    print(f"\n{'='*60}")
    print("  STEP 3: 비례 층화 샘플링")
    print(f"{'='*60}")

    print("=== 비례 층화 샘플링 ===")
    sampled_parts = []

    # 영문(GDELT)과 한글(네이버) 분리
    gdelt_df = news_df[news_df["language"].str.lower().isin(["english", "en"])].copy()
    naver_df = news_df[~news_df["language"].str.lower().isin(["english", "en"])].copy()

    for lang_label, lang_df, max_per_day in [
        ("영문(GDELT)", gdelt_df, MAX_EN_PER_DAY),
        ("한글(네이버)", naver_df, MAX_KO_PER_DAY),
    ]:
        print(f"\n  [{lang_label}] HIGH+MED 전체: {len(lang_df[lang_df['relevance'].isin(['HIGH','MEDIUM'])])}건")
        for cd, day_df in lang_df.groupby("collect_date"):
            sampled = stratified_sample(day_df, max_per_day)
            if len(sampled) > 0:
                sampled_parts.append(sampled)
                dim_alloc = sampled["query_group"].value_counts().to_dict() if "query_group" in sampled.columns else {}
                print(f"    {cd}: {len(day_df[day_df['relevance'].isin(['HIGH','MEDIUM'])])}건 → 샘플 {len(sampled)}건")
                if dim_alloc:
                    for dim, cnt in sorted(dim_alloc.items()):
                        print(f"      {dim}: {cnt}건")

    if not sampled_parts:
        raise ValueError("샘플링 결과가 없습니다. 입력 파일과 relevance 값을 확인하세요.")

    sampled_df = pd.concat(sampled_parts, ignore_index=True)
    print(f"\n✅ 샘플링 완료: 총 {len(sampled_df)}건 → Phase A 대상")
    print(f"  relevance: {dict(sampled_df['relevance'].value_counts())}")
    print(f"  언어별: {dict(sampled_df['language'].value_counts())}")

    return sampled_df


# ══════════════════════════════════════════════════════════════
# STEP 4: Phase A v7 — 2축 분류 (event_status × disruption_type)
# (news_kg_mapping_v7.ipynb Cell 4)
# ══════════════════════════════════════════════════════════════
# 병렬처리: ThreadPoolExecutor(max_workers=10)
#   - 결과를 dict에 수집 후 DataFrame 일괄 반영 (race condition 방지)
#   - 50건마다 체크포인트 저장
# ══════════════════════════════════════════════════════════════

PHASE_A_V5_PROMPT = """Classify this news headline for Korea's maritime supply chain disruption monitoring.
Assess severity from KOREA's perspective using the KG context below.

## Headline (language: {language}):
{title}

## Korea Supply Chain KG Context (이 기사와 매칭된 KG 노드):
{kg_context}


## Classification Axes

### Axis 1 — Event Status (기사가 보도하는 것)
- THREAT: Reports tension/threat that COULD lead to supply disruption
  (military conflict, diplomatic crisis, sanctions threat, geopolitical escalation)
  Example: "US strikes Iranian military targets", "China warns Taiwan"
- DISRUPTION: Reports an ACTUAL supply flow being cut or blocked
  (chokepoint blockage, export ban enacted, port closure, shipping halt)
  Example: "Iran closes Strait of Hormuz", "China bans urea exports"
- DOMESTIC_IMPACT: Reports a Korean company/industry being affected
  (earnings decline, production cut, supply shortage at Korean entity)
  Example: "S-Oil 실적 악화", "국내 요소수 수급 대란"
- IRRELEVANT: No connection to supply chain disruption
  Example: "LG상사 물류 효율 개선", "삼성전자 신제품 출시"

### Axis 2 — Disruption Type (교란 유형, THREAT 또는 DISRUPTION일 때만)
- ROUTE: Maritime route/chokepoint physically blocked or threatened
  (strait blockage, canal closure, naval confrontation in shipping lane)
- SOURCE: Supply source cut by policy/sanctions/production halt
  (export ban, sanctions, OPEC production cut, factory shutdown)
- LOGISTICS: Shipping/port/freight system disrupted
  (port congestion, container shortage, freight rate spike, vessel grounding)
- null: For DOMESTIC_IMPACT or IRRELEVANT

Return ONLY compact valid JSON:
{{"event_status": "THREAT/DISRUPTION/DOMESTIC_IMPACT/IRRELEVANT", "disruption_type": "ROUTE/SOURCE/LOGISTICS/null", "trigger_location": "country or region (from headline only) / null", "event_summary": "one-line factual summary of what the headline reports", "severity": 0, "recommended_alert_level": "Normal/Caution/Warning/Crisis", "validated_chokepoints": ["CP_id1"]}}

Rules:
- severity: integer 0~10, assessed from KOREA's supply chain perspective.
  Use the KG context to gauge Korea's dependency on the affected route/commodity.
  (0=no relevance, 1-3=low, 4-5=moderate, 6-7=high, 8-10=critical for Korea)
- recommended_alert_level: Based on Korea impact severity.
  Normal(0-3), Caution(4-5), Warning(6-7), Crisis(8-10)
- trigger_location: Extract ONLY from headline. If headline doesn't mention a location, set null.
- event_summary: Summarize what the headline SAYS, not what you think it implies.
- Do NOT output impact_chain or propagation paths. Classification and severity ONLY.
- For DOMESTIC_IMPACT: identify it as Korean impact evidence. Do NOT guess the external cause.
- For IRRELEVANT: set severity=0, alert_level="Normal".
- When uncertain between THREAT and DISRUPTION, choose THREAT (conservative).
- When uncertain between DOMESTIC_IMPACT and IRRELEVANT, choose DOMESTIC_IMPACT (conservative).
- validated_chokepoints: Include a CP_ node ONLY if the chokepoint name is EXPLICITLY mentioned in the headline
  (e.g. headline contains "Hormuz", "Panama Canal", "Suez", "Lombok Strait").
  Do NOT add chokepoints based on inference, association, or general reasoning. If not explicitly mentioned, return empty list []."""

PHASE_A_V5_SYSTEM = (
    "You are a KMI (Korea Maritime Institute) supply chain risk classifier. "
    "You monitor global maritime disruptions and assess their severity for KOREA's supply chain. "
    "Classify headlines using a 2-axis system. "
    "Return ONLY valid JSON. No markdown, no explanation."
)

# ── 설정 ─────────────────────────────────────────────────────
MAX_WORKERS     = 10    # 동시 LLM 호출 수
CKPT_INTERVAL   = 50    # 몇 건마다 체크포인트 저장

_CP_NORMALIZE = {
    "CP_Panama_Canal":    "CP_Panama",
    "CP_Hormuz_Strait":   "CP_Hormuz",
    "CP_Strait_of_Hormuz":"CP_Hormuz",
    "CP_Suez_Canal":      "CP_Suez",
    "CP_Malacca_Strait":  "CP_Malacca",
    "CP_Taiwan_Strait":   "CP_Taiwan",
    "CP_Lombok_Strait":   "CP_Lombok",
    "CP_Bab_el_Mandeb":   "CP_BabElMandeb",
}


def step4_phaseA_classification(sampled_df, week_tag, nodes, entity_patterns, match_entities):
    """Phase A 2축 분류 (Haiku, 병렬처리 + 체크포인트).

    출력: news_scored_phaseA_v7_{week_tag}.csv (BASE_DIR 기준)

    Returns:
        pd.DataFrame: out_df (저장된 최종 결과)
    """
    print(f"\n{'='*60}")
    print("  STEP 4: Phase A v7 — 2축 분류 (event_status × disruption_type)")
    print(f"{'='*60}")

    OUTPUT_CSV  = BASE_DIR / f"news_scored_phaseA_v7_{week_tag}.csv"
    OUTPUT_CKPT = BASE_DIR / f"news_scored_phaseA_v7_{week_tag}_ckpt.csv"

    _valid_cp_ids = {nid for nid, nd in nodes.items() if nd.get("node_type") == "chokepoint"}

    # ── 입력 준비 ────────────────────────────────────────────────
    input_df = sampled_df.copy()
    input_df["pub_date"] = pd.to_datetime(
        input_df.get("seendate", input_df.get("pub_date", pd.NaT)),
        format="mixed", utc=True, errors="coerce"
    ).dt.tz_localize(None)
    input_df["_kg_context"] = input_df["title"].apply(
        lambda t: ", ".join(match_entities(str(t), entity_patterns).get("matched_entities", [])[:5]) or ""
    )

    scored_df = input_df[input_df["relevance"].isin(["HIGH","MEDIUM"])].copy().reset_index(drop=True)
    total = len(scored_df)
    print(f"=== Phase A 입력: {total}건 (Step 3 샘플링 결과) ===")
    print(f"  언어별: {dict(scored_df['language'].value_counts())}")
    print(f"  병렬처리: max_workers={MAX_WORKERS}")

    # ── 체크포인트 복원 ──────────────────────────────────────────
    # 이미 처리된 title → 결과 dict
    results_map = {}   # {title: result_dict}  ← thread-safe (GIL 보호, 다른 key)
    cp_lock = threading.Lock()

    if os.path.exists(OUTPUT_CKPT):
        ckpt = pd.read_csv(OUTPUT_CKPT, encoding="utf-8-sig")
        for _, r in ckpt.iterrows():
            if str(r.get("event_status","")) not in ("","nan"):
                results_map[str(r["title"])] = r.to_dict()
        print(f"  체크포인트 복원: {len(results_map)}건")
        del ckpt

    # ── 워커 함수 (1건 처리) ─────────────────────────────────────
    def process_one(row):
        title  = str(row["title"])
        lang   = str(row.get("language","English"))
        kg_ctx = str(row.get("_kg_context","")) or "(no KG match)"

        result = call_llm_json(
            PHASE_A_V5_PROMPT.format(title=title, language=lang, kg_context=kg_ctx),
            system=PHASE_A_V5_SYSTEM, max_tokens=512
        )

        # CP 정규화·검증
        _vcp = result.get("validated_chokepoints", []) if result else []
        _vcp = [c for c in _vcp if isinstance(c, str) and c.startswith("CP_")]
        _vcp = [_CP_NORMALIZE.get(c, c) for c in _vcp]
        _vcp = [c for c in _vcp if c in _valid_cp_ids]

        if result and isinstance(result, dict):
            es = str(result.get("event_status","IRRELEVANT")).upper()
            if es not in ("THREAT","DISRUPTION","DOMESTIC_IMPACT","IRRELEVANT"):
                es = "IRRELEVANT"
            dt = result.get("disruption_type")
            if es in ("DOMESTIC_IMPACT","IRRELEVANT"):
                dt = None
            elif dt and str(dt).upper() not in ("ROUTE","SOURCE","LOGISTICS"):
                dt = None
            try:
                sev = max(0, min(10, int(result.get("severity", 0))))
            except:
                sev = 0
            if es == "IRRELEVANT":
                sev = 0
            alert = str(result.get("recommended_alert_level","Normal"))
            if alert not in ("Normal","Caution","Warning","Crisis"):
                alert = "Normal"
            if es == "IRRELEVANT":
                alert = "Normal"

            return {
                "title": title,
                "event_status": es,
                "disruption_type": str(dt).upper() if dt else "",
                "trigger_location": str(result.get("trigger_location") or ""),
                "event_summary": str(result.get("event_summary","")),
                "severity": sev,
                "recommended_alert_level": alert,
                "validated_chokepoints": _vcp,
                "ok": True,
            }
        else:
            return {
                "title": title,
                "event_status": "IRRELEVANT",
                "disruption_type": "",
                "trigger_location": "",
                "event_summary": "",
                "severity": 0,
                "recommended_alert_level": "Normal",
                "validated_chokepoints": [],
                "ok": False,
            }

    # ── 병렬 실행 ────────────────────────────────────────────────
    # 체크포인트에 없는 것만 제출
    pending_rows = [
        row for _, row in scored_df.iterrows()
        if str(row["title"]) not in results_map
    ]
    print(f"  미처리: {len(pending_rows)}건 제출")

    done_count = 0
    error_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_title = {executor.submit(process_one, row): str(row["title"])
                           for row in pending_rows}

        for future in as_completed(future_to_title):
            res = future.result()
            title = res["title"]
            results_map[title] = res
            if not res["ok"]:
                error_count += 1

            done_count += 1
            if done_count % 20 == 0 or done_count == len(pending_rows):
                pct = (len(results_map)) / total * 100
                print(f"  [{len(results_map):4d}/{total}] {pct:.0f}% — 오류 {error_count}건")

            # 체크포인트: 50건마다
            if done_count % CKPT_INTERVAL == 0:
                with cp_lock:
                    ckpt_rows = list(results_map.values())
                    pd.DataFrame(ckpt_rows).to_csv(OUTPUT_CKPT, index=False, encoding="utf-8-sig")

    # ── 결과를 scored_df에 일괄 반영 ────────────────────────────
    for col in ["event_status","disruption_type","trigger_location",
                "event_summary","severity","recommended_alert_level"]:
        scored_df[col] = ""
    scored_df["severity"] = 0

    for idx, row in scored_df.iterrows():
        title = str(row["title"])
        r = results_map.get(title, {})
        scored_df.loc[idx, "event_status"]           = r.get("event_status", "IRRELEVANT")
        scored_df.loc[idx, "disruption_type"]         = r.get("disruption_type", "")
        scored_df.loc[idx, "trigger_location"]        = r.get("trigger_location", "")
        scored_df.loc[idx, "event_summary"]           = r.get("event_summary", "")
        scored_df.loc[idx, "severity"]                = r.get("severity", 0)
        scored_df.loc[idx, "recommended_alert_level"] = r.get("recommended_alert_level", "Normal")

    # ── matched_entities 조립 ────────────────────────────────────
    def _assemble_matched_entities(title, results_map):
        code_ents = match_entities(str(title), entity_patterns).get("matched_entities", [])
        validated_cps = set(results_map.get(str(title), {}).get("validated_chokepoints", []))
        non_cp = [e for e in code_ents if not e.startswith("CP_")]
        return json.dumps(list(validated_cps) + non_cp, ensure_ascii=False)

    scored_df["matched_entities"] = scored_df["title"].apply(
        lambda t: _assemble_matched_entities(t, results_map)
    )

    # ── severity 기준 alert_level 재계산 ─────────────────────────
    def _sev_to_alert(sev):
        sev = int(sev)
        if sev >= 8:   return "Crisis"
        elif sev >= 6: return "Warning"
        elif sev >= 4: return "Caution"
        else:          return "Normal"

    scored_df["recommended_alert_level"] = scored_df["severity"].apply(_sev_to_alert)
    scored_df.loc[scored_df["event_status"] == "IRRELEVANT", "recommended_alert_level"] = "Normal"
    scored_df.loc[scored_df["event_status"] == "IRRELEVANT", "severity"] = 0

    # ── 저장 ─────────────────────────────────────────────────────
    BASE_COLS = ["title","pub_date","language","source",
                 "event_status","disruption_type","trigger_location",
                 "event_summary","severity","recommended_alert_level",
                 "matched_entities"]
    OPTIONAL_COLS = ["query_group","relevance","topic","url_hash","period_type"]
    out_cols = BASE_COLS + [c for c in OPTIONAL_COLS if c in scored_df.columns]
    out_df = scored_df[[c for c in out_cols if c in scored_df.columns]].copy()
    out_df["severity"] = pd.to_numeric(out_df["severity"], errors="coerce").fillna(0).astype(int)
    out_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    if os.path.exists(OUTPUT_CKPT):
        os.remove(OUTPUT_CKPT)

    print(f"\n=== Phase A v7 완료: {len(out_df)}건 → {OUTPUT_CSV} ===")
    print(f"오류: {error_count}건")
    print(f"\n[event_status]")
    for k, v in out_df["event_status"].value_counts().items():
        print(f"  {k}: {v}건 ({v/len(out_df)*100:.1f}%)")
    print(f"\n[disruption_type]")
    for k, v in out_df["disruption_type"].replace("","(없음)").value_counts().items():
        print(f"  {k}: {v}건")
    print(f"\n[severity 분포]")
    print(out_df["severity"].value_counts().sort_index().to_string())
    print(f"\n[recommended_alert_level]")
    for k, v in out_df["recommended_alert_level"].value_counts().items():
        print(f"  {k}: {v}건 ({v/len(out_df)*100:.1f}%)")
    print(f"\n[event_status × disruption_type]")
    print(pd.crosstab(out_df["event_status"], out_df["disruption_type"].replace("","null")).to_string())

    return out_df


# ══════════════════════════════════════════════════════════════
# STEP 5: 자동 지표 수집 (indicator_weekly.csv 구축/연장)
# (scenario_generator_v11.ipynb Part 0-B / Cell 3)
# ══════════════════════════════════════════════════════════════
# REBUILD  : CSV 없을 때 전체 재수집 (2019~) — 최초 1회는 노트북으로 수동 부트스트랩 필요
# EXTEND   : CSV 있지만 WEEK_END_TARGET보다 오래됐을 때 자동 연장 (주간 자동화 대상)
# LOAD     : 이미 최신이면 그냥 로드
#
# 과거 행 소급 수정 금지 원칙(CLAUDE.md 0-b) 준수: EXTEND는 새 주차만 추가하고
# 기존 행은 절대 덮어쓰지 않는다.
# ══════════════════════════════════════════════════════════════

# ── IMF PortWatch API: 초크포인트 통과량 자동 수집 ───────────
PORTWATCH_API = (
    'https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services'
    '/Daily_Chokepoints_Data/FeatureServer/0/query'
)
CP_MAP = {
    'Strait of Hormuz': 'CP_Hormuz',
    'Suez Canal': 'CP_Suez',
    'Bab el-Mandeb Strait': 'CP_BabElMandeb',
    'Malacca Strait': 'CP_Malacca',
    'Taiwan Strait': 'CP_Taiwan',
    'Korea Strait': 'CP_Korea',
}
CP_VESSEL_TYPES = ['n_container', 'n_dry_bulk', 'n_general_cargo', 'n_roro', 'n_tanker']


def monthly_to_weekly(series, idx, dates_dict=None, col_name=None):
    s = series.copy()
    s.index = pd.to_datetime(s.index)
    resampled = s.resample('W-MON', closed='left', label='right').last()
    # EXTEND 시 idx가 resampled 범위 밖이면 ffill 실패 방지:
    # resampled의 기존 인덱스를 포함시켜 ffill 후 idx만 추출
    _full = resampled.index.union(idx).sort_values()
    vals = resampled.reindex(_full).ffill().reindex(idx)
    if dates_dict is not None and col_name:
        # 각 데이터 포인트의 원래 날짜를 값으로 가지는 시리즈 → .last()로 resample
        # (.apply(lambda) 방식은 빈 bin에 NaN/NaT 혼합 → ffill 실패 원인)
        _date_as_val = pd.Series(s.index, index=s.index)
        date_resampled = _date_as_val.resample('W-MON', closed='left', label='right').last()
        dates_dict[col_name] = date_resampled.reindex(_full).ffill().reindex(idx)
    return vals


def _get_last_dates(daily_series, weekly_idx):
    """resample 시 각 주간 bin의 마지막 유효 데이터의 원본 날짜를 반환."""
    s = daily_series.dropna()
    if len(s) == 0:
        return pd.Series(index=weekly_idx, dtype='datetime64[ns]')
    s.index = pd.to_datetime(s.index)
    # .last() 방식으로 datetime 순수 타입 유지 (apply+lambda NaN/NaT 혼합 방지)
    _date_as_val = pd.Series(s.index, index=s.index)
    date_series = _date_as_val.resample('W-MON', closed='left', label='right').last()
    # EXTEND 시 weekly_idx가 date_series 범위 밖이면 ffill 실패 방지
    _full = date_series.index.union(weekly_idx).sort_values()
    return date_series.reindex(_full).ffill().reindex(weekly_idx)


def fetch_portwatch_cp(start_date, end_date, weekly_idx):
    """IMF PortWatch API에서 초크포인트 일별 통과량을 가져와 주간 합산 반환. 페이지네이션 지원."""
    result = {}
    start_str = pd.Timestamp(start_date).strftime('%Y-%m-%d')
    end_str   = pd.Timestamp(end_date).strftime('%Y-%m-%d')
    _PAGE_SIZE = 1000  # ArcGIS 서버 최대 반환 한도
    for portname, colname in CP_MAP.items():
        try:
            all_rows = []
            offset = 0
            while True:
                params = {
                    'where': f"portname='{portname}' AND date>='{start_str}' AND date<='{end_str}'",
                    'outFields': ','.join(['date'] + CP_VESSEL_TYPES),
                    'f': 'json',
                    'resultRecordCount': _PAGE_SIZE,
                    'resultOffset': offset,
                    'orderByFields': 'date ASC',
                }
                r = requests.get(PORTWATCH_API, params=params, timeout=30)
                data = r.json()
                feats = data.get('features', [])
                if not feats:
                    break
                for feat in feats:
                    a = feat['attributes']
                    dt = pd.Timestamp(a['date'], unit='ms')
                    total = sum(a.get(v, 0) or 0 for v in CP_VESSEL_TYPES)
                    all_rows.append({'date': dt, 'total': total})
                if len(feats) < _PAGE_SIZE:
                    break  # 마지막 페이지
                offset += _PAGE_SIZE
            if not all_rows:
                print(f"    ⚠ {colname}: API 응답 없음")
                continue
            df_cp = pd.DataFrame(all_rows).set_index('date')
            weekly = df_cp['total'].resample('W-MON', closed='left', label='right').sum()
            result[colname] = weekly.reindex(weekly_idx)
            actual = weekly.reindex(weekly_idx).dropna()
            if len(actual):
                print(f"    ✓ {colname}: {actual.index[0].date()} ~ {actual.index[-1].date()} ({len(actual)}주, API {len(all_rows)}일)")
        except Exception as e:
            print(f"    ⚠ {colname}: API 실패 ({e})")
    return result


def fetch_gpr_auto(start, end):
    """Caldara-Iacoviello GPR 자동 다운로드"""
    for url in [
        "https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls",
        "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls",
    ]:
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            df = pd.read_excel(io.BytesIO(resp.content))
            date_col = next((c for c in df.columns
                             if str(c).lower() in ['month','date','observation_date']), df.columns[0])
            gpr_col  = next((c for c in df.columns if 'GPR' in str(c) and 'B' not in str(c)), None)
            if gpr_col is None:
                continue
            df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
            df = df.dropna(subset=[date_col, gpr_col])
            mask = (df[date_col] >= pd.Timestamp(start)) & (df[date_col] <= pd.Timestamp(end))
            s = df[mask].set_index(date_col)[gpr_col]
            s.index = pd.to_datetime(s.index)
            print(f"    ✓ GPR 자동: {url.split('/')[-1]} ({len(s)}개월)")
            return s
        except Exception as e:
            print(f"    ⚠ GPR {url.split('/')[-1]}: {e}")
    return None


def fetch_gscpi_auto(start, end):
    """NY Fed GSCPI 자동 다운로드"""
    url = "https://www.newyorkfed.org/medialibrary/research/interactives/gscpi/downloads/gscpi_data.xlsx"
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        df = pd.read_excel(io.BytesIO(resp.content), sheet_name='GSCPI Monthly Data')
        df = df[['Date','GSCPI']].dropna()
        df['Date'] = pd.to_datetime(df['Date'])
        mask = (df['Date'] >= pd.Timestamp(start)) & (df['Date'] <= pd.Timestamp(end))
        s = df[mask].set_index('Date')['GSCPI']
        s.index = pd.to_datetime(s.index)
        print(f"    ✓ GSCPI 자동: NY Fed ({len(s)}개월)")
        # 로컬 파일도 갱신 (다음 실행 시 fallback용, 실패해도 데이터는 반환)
        try:
            with open(os.path.join(MKG_DIR, 'gscpi_data.xlsx'), 'wb') as f:
                f.write(resp.content)
        except OSError:
            pass  # MKG_DIR 없는 환경(GitHub Actions 등)에서는 캐시 저장 스킵
        return s
    except Exception as e:
        print(f"    ⚠ GSCPI 자동 다운로드 실패: {e}")
        return None


def fetch_kr_export_customs(start, end):
    """관세청 수출입총괄(GW) API: 한국 월별 수출액 (USD)

    주의: API 조회 기간이 1년 이내로 제한됨 → 자동 분할 호출.
    당월(미완료) 잠정치는 제외.
    """
    try:
        import xml.etree.ElementTree as ET
        from urllib.request import urlopen

        # 공공데이터포털 인증키 — 반드시 환경변수/GitHub Secrets 로 주입할 것.
        # 소스에 직접 적으면 공개 저장소에 그대로 노출된다(2026-08-23 유출 사례).
        API_KEY = os.environ.get('CUSTOMS_API_KEY', '').strip()
        if not API_KEY:
            msg = ('CUSTOMS_API_KEY \ubbf8\uc124\uc815 \u2014 \uad00\uc138\uccad \uc218\ucd9c\uc561'
                   '(KR_ExportVol) \uc218\uc9d1\uc744 \uac74\ub108\ub701\ub2c8\ub2e4')
            print(f'    \u26a0 {msg}')
            if os.environ.get('GITHUB_ACTIONS') == 'true':
                print(f'::warning title=\uc9c0\ud45c \uc218\uc9d1 \ub204\ub77d::{msg}')
            return None
        base_url = 'https://apis.data.go.kr/1220000/Newtrade/getNewtradeList'

        s_ts = pd.Timestamp(start)
        e_ts = pd.Timestamp(end)

        # 당월 잠정치 제외: end를 전월말로 조정
        today = pd.Timestamp.now()
        last_complete_month = (today.replace(day=1) - pd.Timedelta(days=1))
        if e_ts > last_complete_month:
            e_ts = last_complete_month

        # 1년(12개월) 단위로 분할 호출
        records = {}
        chunk_start = s_ts
        while chunk_start <= e_ts:
            chunk_end = min(chunk_start + pd.DateOffset(months=11), e_ts)
            s_ym = chunk_start.strftime('%Y%m')
            e_ym = chunk_end.strftime('%Y%m')

            url = f'{base_url}?serviceKey={API_KEY}&strtYymm={s_ym}&endYymm={e_ym}'

            with urlopen(url, timeout=30) as resp:
                xml_data = resp.read()

            root = ET.fromstring(xml_data)
            rc = root.findtext('.//resultCode')
            if rc != '00':
                print(f'    ⚠ 관세청 API ({s_ym}~{e_ym}): {root.findtext(".//resultMsg")}')
                chunk_start = chunk_end + pd.DateOffset(months=1)
                continue

            for item in root.findall('.//item'):
                year_str = item.findtext('year', '').strip()
                if '총계' in year_str or not year_str:
                    continue
                exp_dlr = item.findtext('expDlr', '0').strip()
                ym = year_str.replace('.', '-')
                dt = pd.Timestamp(f'{ym}-01')
                # 당월 잠정치 한 번 더 제외
                if dt <= last_complete_month:
                    records[dt] = float(exp_dlr)

            chunk_start = chunk_end + pd.DateOffset(months=1)
            time.sleep(0.3)  # API 부하 방지

        if not records:
            print('    ⚠ KR_ExportVol 관세청: 데이터 없음')
            return None

        s = pd.Series(records).sort_index()
        print(f'    ✓ KR_ExportVol 관세청 ({len(s)}개월, {s.index[0].strftime("%Y.%m")}~{s.index[-1].strftime("%Y.%m")}, USD)')
        return s
    except Exception as e:
        print(f'    ⚠ KR_ExportVol 관세청: {e}')
        return None


def fetch_bdi_auto():
    """tradingeconomics.com에서 BDI 최신값 스크래핑"""
    try:
        from bs4 import BeautifulSoup
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
        resp = requests.get('https://tradingeconomics.com/commodity/baltic', headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tr in soup.find_all('tr'):
            cells = tr.find_all('td')
            if cells and 'Baltic' in cells[0].get_text():
                row = [c.get_text(strip=True) for c in cells]
                value = float(row[1].replace(',', ''))
                date_str = row[-1]  # 'Apr/24'
                import calendar
                mon_str, day_str = date_str.split('/')
                month_num = list(calendar.month_abbr).index(mon_str)
                year = pd.Timestamp.now().year
                date = pd.Timestamp(year, month_num, int(day_str))
                print(f'    ✓ BDI 자동(tradingeconomics): {value:.0f} ({date.date()})')
                return pd.Series({date: value})
    except Exception as e:
        print(f'    ⚠ BDI 자동 수집 실패: {e}')
    return None


def fetch_napmsdi_auto():
    """tradingeconomics.com에서 NAPMSDI 최신값 스크래핑"""
    try:
        from bs4 import BeautifulSoup
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
        resp = requests.get(
            'https://tradingeconomics.com/united-states/ism-manufacturing-supplier-deliveries',
            headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        text = soup.get_text()
        m = re.search(
            r'(increased|decreased) to ([\d.]+) points in (\w+) from [\d.]+ points in (\w+) of (\d{4})',
            text)
        if m:
            value = float(m.group(2))
            month_name = m.group(3)
            year = int(m.group(5))
            month_num = pd.Timestamp(f'{month_name} 1, {year}').month
            date = pd.Timestamp(year, month_num, 1)
            print(f'    ✓ NAPMSDI 자동(tradingeconomics): {value} ({date.date()})')
            return pd.Series({date: value})
    except Exception as e:
        print(f'    ⚠ NAPMSDI 자동 수집 실패: {e}')
    return None


def fetch_harpex_auto():
    """harperpetersen.com에서 Harpex 최신값 스크래핑"""
    try:
        from bs4 import BeautifulSoup
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
        resp = requests.get('https://www.harperpetersen.com/container', headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        table = soup.find_all('table')[0]
        rows = table.find_all('tr')
        data_row = rows[2]  # first data row
        cells = [c.get_text(strip=True) for c in data_row.find_all('td')]
        date_str = cells[0]   # '24-Apr-2026'
        value_str = cells[1]  # '2,257'
        date = pd.Timestamp(date_str)
        value = float(value_str.replace(',', ''))
        print(f'    ✓ Harpex 자동(harperpetersen): {value:.0f} ({date.date()})')
        return pd.Series({date: value})
    except Exception as e:
        print(f'    ⚠ Harpex 자동 수집 실패: {e}')
    return None


def fetch_scfi_auto():
    """en.sse.net.cn JSON API에서 SCFI 최신값 수집"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
        resp = requests.get('https://en.sse.net.cn/currentIndex',
                            params={'indexName': 'scfi'},
                            headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()['data']
        date = pd.Timestamp(data['currentDate'])
        value = data['lineDataList'][0]['currentContent']  # Comprehensive Index
        print(f'    ✓ SCFI 자동(en.sse.net.cn): {value:.2f} ({date.date()})')
        return pd.Series({date: value})
    except Exception as e:
        print(f'    ⚠ SCFI 자동 수집 실패: {e}')
    return None


def fetch_rwi_isl_cti_auto():
    """isl.org 보도자료에서 RWI/ISL CTI 최신값 스크래핑"""
    try:
        from bs4 import BeautifulSoup
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
        now = pd.Timestamp.now()
        # 최신 보도자료부터 역순 시도 (URL: index-{M}{YY})
        for offset in range(0, 4):
            target = now - pd.DateOffset(months=offset)
            slug = f'{target.month}{target.strftime("%y")}'
            url = f'https://www.isl.org/en/services/rwiisl-container-throughput-index-{slug}'
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                continue
            text = BeautifulSoup(resp.text, 'html.parser').get_text()
            m = re.search(r'(?:to|at)\s+([\d.]+)\s+points\s*\(seasonally adjusted\)', text)
            if m:
                value = float(m.group(1))
                # 보도자료 제목에서 월 추출 (URL의 month가 데이터 기준월)
                data_month = target.month
                data_year = target.year
                date = pd.Timestamp(data_year, data_month, 1)
                print(f'    ✓ RWI_ISL_CTI 자동(isl.org): {value} ({date.date()})')
                return pd.Series({date: value})
    except Exception as e:
        print(f'    ⚠ RWI_ISL_CTI 자동 수집 실패: {e}')
    return None


def yf_weekly(ticker, start, end, idx, dates_dict=None, col_name=None):
    try:
        raw = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        if 'Close' in raw.columns and len(raw) > 0:
            vals = raw['Close'].resample('W-MON', closed='left', label='right').last().reindex(idx).ffill()
            if dates_dict is not None and col_name:
                dates_dict[col_name] = _get_last_dates(raw['Close'], idx)
            return vals
        # 예외 없이 빈 응답이 오는 경우(야후에서 실제로 발생) — 이전에는 흔적이 전혀 남지 않았다
        print(f"    \u26a0 {ticker} ({col_name}): 응답이 비어 있음 — 수집 실패")
    except Exception as e:
        print(f"    ⚠ {ticker}: {e}")
    return None


# YF 티커 매핑 (글로벌 + 한국 산업주). 주의: DX-Y.NYB 는 KeyError 발생 → UUP 사용 (CLAUDE.md 13).
YF_GLOBAL_MAP = {
    'BZ=F': 'Brent', 'CL=F': 'WTI', 'NG=F': 'NatGas',
    '^VIX': 'VIX', 'GLD': 'Gold', 'UUP': 'USD_Index',
    '^KS11': 'KOSPI', 'KRW=X': 'KRWUSD',
}
YF_KR_STOCK_MAP = {
    '096770.KS': 'SK이노베이션', '010950.KS': 'S_Oil',
    '011170.KS': '롯데케미칼', '051910.KS': 'LG화학',
    '009830.KS': '한화솔루션', '011200.KS': 'HMM',
    '028670.KS': '팬오션', '036460.KS': '한국가스공사',
    '003490.KS': '대한항공', '097950.KS': 'CJ제일제당',
    '004370.KS': '농심',
}


def step5_collect_indicators(kg_raw):
    """자동/반자동 지표 수집 → indicator_weekly.csv 구축/연장 (EXTEND 모드).

    - REBUILD(파일 없음)는 2019년부터의 전체 이력 수집이 필요한 1회성 부트스트랩
      작업이므로 이 주간 자동화 스크립트의 범위 밖. 파일이 없으면 에러를 내고
      scenario_generator_v11.ipynb 를 먼저 1회 수동 실행하도록 안내한다.
    - CSV가 이미 최신이면 그대로 로드만 하고 종료(과거 행 불변 원칙, CLAUDE.md 0-b).
    - CSV가 오래됐으면 마지막 주 다음부터 WEEK_END_TARGET까지 새 행만 추가한다(EXTEND).

    Returns:
        pd.DataFrame: indicator_df (indicator_weekly.csv 저장 결과)
    """
    print(f"\n{'='*60}")
    print("  STEP 5: 자동 지표 수집 (indicator_weekly.csv EXTEND)")
    print(f"{'='*60}")

    _today = datetime.now()
    _days_to_next_mon = (7 - _today.weekday()) % 7  # 월요일이면 0
    WEEK_END_TARGET = (_today + timedelta(days=_days_to_next_mon)).strftime('%Y-%m-%d')

    if not os.path.exists(INDICATOR_CSV):
        raise FileNotFoundError(
            f"{INDICATOR_CSV} 가 없습니다. 최초 전체 수집(REBUILD, 2019~현재)은 이 스크립트의 "
            f"범위 밖입니다. scenario_generator_v11.ipynb Part 0-B 를 1회 수동 실행하여 "
            f"indicator_weekly.csv 를 먼저 생성하세요."
        )

    indicator_df = pd.read_csv(INDICATOR_CSV, index_col='week_date', parse_dates=True)
    current_max  = indicator_df.index.max()
    EXTEND = current_max < pd.Timestamp(WEEK_END_TARGET)

    # ── 온라인 자동 수집 지표 목록 (cumulative.csv 병합 시 덮어쓰지 않을 지표) ──
    # RWI_ISL_CTI, GSCSI는 반자동 → 이 집합에 포함하면 안 됨 (CLAUDE.md 0-c)
    _online_auto_indicators = (
        {'Brent', 'WTI', 'NatGas', 'VIX', 'Gold', 'USD_Index', 'KOSPI', 'KRWUSD',
         'SK이노베이션', 'S_Oil', '롯데케미칼', 'LG화학', '한화솔루션', 'HMM', '팬오션',
         '한국가스공사', '대한항공', 'CJ제일제당', '농심'}
        | {'SCFI', 'BDI', 'Harpex'}
        | {c for c in indicator_df.columns if c.startswith('CP_')}
        | {'GPR', 'GSCPI', 'NAPMSDI', 'KR_ExportVol'}
    )
    # KG 기반 ETF도 추가
    for _nid, _nd in kg_raw['nodes'].items():
        if _nd.get('node_type') == 'korea_sector':
            for _etf_name in _nd.get('etfTickers', {}).keys():
                _online_auto_indicators.add(_etf_name)

    if not EXTEND:
        print(f"✅ indicator_weekly.csv 이미 최신 ({len(indicator_df)}주, {len(indicator_df.columns)}개 지표)")
        print(f"   기간: {indicator_df.index.min().date()} ~ {indicator_df.index.max().date()}")
        return indicator_df

    print(f"⚡ EXTEND 모드: {current_max.date()} → {WEEK_END_TARGET}")

    ext_start = current_max + pd.Timedelta(weeks=1)
    ext_idx   = pd.date_range(ext_start, WEEK_END_TARGET, freq="W-MON")
    _data_start = current_max  # 실제 커버 시작일 (ext_start 라벨의 커버 구간은 [current_max, ext_start))

    # 기존 dates CSV 로드
    if os.path.exists(DATES_CSV):
        df_dates_existing = pd.read_csv(DATES_CSV, index_col='week_date', parse_dates=True)
    else:
        df_dates_existing = pd.DataFrame(index=indicator_df.index)
        df_dates_existing.index.name = 'week_date'
    _dates_ext = {}  # 신규 주차 날짜

    if len(ext_idx) == 0:
        print(f"  다음 월요일({ext_start.date()})이 아직 안 왔음 → 재실행 불필요")
        print(f"\n✅ indicator_weekly.csv ({len(indicator_df)}주, {len(indicator_df.columns)}개 지표)")
        print(f"   기간: {indicator_df.index.min().date()} ~ {indicator_df.index.max().date()}")
        return indicator_df

    print(f"  연장 범위: {ext_start.date()} ~ {ext_idx[-1].date()} ({len(ext_idx)}주)")

    df_ext = pd.DataFrame(index=ext_idx)
    df_ext.index.name = "week_date"

    # 기본값: 마지막 알려진 값으로 ffill 초기화
    for col in indicator_df.columns:
        last_val = indicator_df[col].dropna()
        df_ext[col] = last_val.iloc[-1] if len(last_val) > 0 else np.nan

    # ── SCFI: cfi_weekly.csv (로컬 fallback, 자동 스크래핑으로 아래서 덮어씀) ──
    _scfi_local_path = os.path.join(MKG_DIR, "cfi_weekly.csv")
    if Path(_scfi_local_path).exists():
        cfi = pd.read_csv(_scfi_local_path)
        cfi["Date"] = pd.to_datetime(cfi["Date"])
        _scfi_raw = cfi.set_index("Date")["CFI"]
        cfi_s = _scfi_raw.resample('W-MON', closed='left', label='right').last()
        new_cfi = cfi_s[cfi_s.index >= _data_start].reindex(ext_idx).ffill()
        actual = new_cfi.dropna()
        if len(actual):
            df_ext["SCFI"] = new_cfi
            _dates_ext['SCFI'] = _get_last_dates(_scfi_raw, ext_idx)
            print(f"  ✓ SCFI 실제값: {actual.index[0].date()} ~ {actual.index[-1].date()} ({len(actual)}주)")

    # ── CP_*: PortWatch API (신규 주차) ──────────────
    # CP 지연 발표 대응: 4주 전부터 쿼리, 최신 가용 주간 데이터를 신규 행에 반영
    print("  CP_* PortWatch API 수집 중 (신규 주차)...")
    _cp_query_start = str((_data_start - pd.Timedelta(weeks=4)).date())
    _cp_wide_idx = pd.date_range(_data_start - pd.Timedelta(weeks=4), ext_idx[-1], freq='W-MON')
    try:
        _cp_data = fetch_portwatch_cp(_cp_query_start, WEEK_END_TARGET, _cp_wide_idx)
        for colname, series in _cp_data.items():
            if colname not in df_ext.columns:
                continue
            _valid = series.dropna()
            if len(_valid) > 0:
                _latest_val = float(_valid.iloc[-1])
                for _ext_dt in ext_idx:
                    df_ext.loc[_ext_dt, colname] = _latest_val
                # source_date: API 일별 데이터의 실제 마지막 날짜
                _pname = [k for k, v in CP_MAP.items() if v == colname][0]
                _last_q = {'where': f"portname='{_pname}' AND date>='{_cp_query_start}'",
                           'outFields': 'date', 'f': 'json',
                           'resultRecordCount': 1, 'orderByFields': 'date DESC'}
                try:
                    _lr = requests.get(PORTWATCH_API, params=_last_q, timeout=15)
                    _lf = _lr.json().get('features', [])
                    _cp_src_date = str(pd.Timestamp(_lf[0]['attributes']['date']).date()) if _lf else str((_valid.index[-1] - pd.Timedelta(days=1)).date())
                except Exception:
                    _cp_src_date = str((_valid.index[-1] - pd.Timedelta(days=1)).date())
                _dates_ext[colname] = pd.Series(_cp_src_date, index=ext_idx)
                print(f"    ✓ {colname}: {_latest_val:.0f} (기준:{_cp_src_date})")
            else:
                # 전체 빈 응답 → 기존 dates에서 ffill
                if colname in df_dates_existing.columns:
                    _existing_dates = pd.to_datetime(df_dates_existing[colname], errors='coerce').dropna()
                    if len(_existing_dates):
                        _dates_ext[colname] = pd.Series(_existing_dates.iloc[-1], index=ext_idx)
                        print(f"    ℹ {colname}: API 빈 응답 → 기존 dates의 {_existing_dates.iloc[-1].date()} 사용 (stale)")
    except Exception as _e:
        print(f"  ⚠ CP_* 갱신 실패: {_e}")
    # CP dates fallback: API가 빈 dict 반환 시 for 루프 미실행
    for _cp_col in [c for c in df_ext.columns if c.startswith('CP_')]:
        if _cp_col not in _dates_ext:
            if _cp_col in df_dates_existing.columns:
                _existing_dates = pd.to_datetime(df_dates_existing[_cp_col], errors='coerce').dropna()
                if len(_existing_dates):
                    _dates_ext[_cp_col] = pd.Series(_existing_dates.iloc[-1], index=ext_idx)
                    print(f"    ℹ {_cp_col}: API 미반환 → 기존 dates의 {_existing_dates.iloc[-1].date()} 사용 (stale)")
    print(f"  ✓ CP_* 신규 주차 완료")

    # ── BDI: 로컬 파일 (BDI_2026(1~3).xlsx 또는 기존 CSV) → 자동 스크래핑 fallback ───
    bdi_2026_files = [
        os.path.join(BASE_DIR, 'BDI_2026(1~3).xlsx'),
        os.path.join(BASE_DIR, 'BDI_2026.xlsx'),
    ]
    bdi_loaded = False
    for bdi_fname in bdi_2026_files:
        if Path(bdi_fname).exists():
            try:
                raw = pd.read_excel(bdi_fname, header=None)
                # Investing.com 형식: 헤더 4행 스킵, col0=Date, col1=Price
                data = raw.iloc[4:].copy()
                data.columns = range(len(data.columns))
                data['Date']  = pd.to_datetime(data[0], errors='coerce')
                data['Price'] = pd.to_numeric(
                    data[1].astype(str).str.replace(',',''), errors='coerce')
                data = data.dropna(subset=['Date','Price']).set_index('Date').sort_index()
                bdi_s = data['Price']
                new_bdi = bdi_s[bdi_s.index >= _data_start].resample('W-MON', closed='left', label='right').last().reindex(ext_idx).ffill()
                actual = new_bdi.dropna()
                if len(actual):
                    df_ext['BDI'] = new_bdi
                    _dates_ext['BDI'] = _get_last_dates(bdi_s[bdi_s.index >= _data_start], ext_idx)
                    print(f"  ✓ BDI 실제값 ({bdi_fname}): {actual.index[0].date()} ~ {actual.index[-1].date()} ({len(actual)}주)")
                    bdi_loaded = True
                    break
            except Exception as e:
                print(f"  ⚠ BDI {bdi_fname}: {e}")
    if not bdi_loaded:
        # tradingeconomics 자동 수집 시도
        _bdi_auto = fetch_bdi_auto()
        if _bdi_auto is not None:
            bdi_s = _bdi_auto
            new_bdi = bdi_s.resample("W-MON", closed="left", label="right").last().reindex(ext_idx).ffill()
            actual = new_bdi.dropna()
            if len(actual):
                df_ext["BDI"] = new_bdi
                _dates_ext["BDI"] = _get_last_dates(bdi_s, ext_idx)
                bdi_loaded = True
        if not bdi_loaded:
            print("  ⚠ BDI 2026 파일 없음 + 자동 수집 실패 → ffill 유지")

    # ── GSCSI: 로컬 파일 (World Bank, 반자동 — CLAUDE.md 0-c) ─────────────────
    _gscsi_local_path = os.path.join(MKG_DIR, 'GSCSI_data.xlsx')
    if Path(_gscsi_local_path).exists():
        try:
            _tmp = pd.read_excel(_gscsi_local_path)
            _tmp.columns = ['date', 'GSCSI']
            _tmp['date'] = pd.to_datetime(_tmp['date'])
            _s = _tmp.dropna().set_index('date')['GSCSI']
            _new = monthly_to_weekly(_s, ext_idx, _dates_ext, 'GSCSI')
            _actual = _new.dropna()
            if len(_actual):
                df_ext['GSCSI'] = _new
                print(f"  ✓ GSCSI 실제값: {_actual.index[0].date()} ~ {_actual.index[-1].date()} ({len(_actual)}주)")
        except Exception as _e:
            print(f"  ⚠ GSCSI_data.xlsx: {_e}")

    # ── NAPMSDI: tradingeconomics 자동 스크래핑 ───────────────
    _napmsdi_auto = fetch_napmsdi_auto()
    if _napmsdi_auto is not None:
        _new = monthly_to_weekly(_napmsdi_auto, ext_idx, _dates_ext, 'NAPMSDI')
        _actual = _new.dropna()
        if len(_actual):
            df_ext['NAPMSDI'] = _new
            print(f"  ✓ NAPMSDI 자동(tradingeconomics): {_actual.index[0].date()} ~ {_actual.index[-1].date()} ({len(_actual)}주)")

    # ── Harpex: harperpetersen.com 자동 스크래핑 ──────────────
    _harpex_auto = fetch_harpex_auto()
    if _harpex_auto is not None:
        _new = _harpex_auto.resample('W-MON', closed='left', label='right').last().reindex(ext_idx).ffill()
        _actual = _new.dropna()
        if len(_actual):
            df_ext['Harpex'] = _new
            _dates_ext['Harpex'] = _get_last_dates(_harpex_auto, ext_idx)
            print(f"  ✓ Harpex 자동(harperpetersen): {_actual.index[0].date()} ~ {_actual.index[-1].date()} ({len(_actual)}주)")

    # ── SCFI: en.sse.net.cn JSON API ────────────────────────
    _scfi_auto = fetch_scfi_auto()
    if _scfi_auto is not None:
        _new = _scfi_auto.resample('W-MON', closed='left', label='right').last().reindex(ext_idx).ffill()
        _actual = _new.dropna()
        if len(_actual):
            df_ext['SCFI'] = _new
            _dates_ext['SCFI'] = _get_last_dates(_scfi_auto, ext_idx)
            print(f"  ✓ SCFI 자동(en.sse.net.cn): {_actual.index[0].date()} ~ {_actual.index[-1].date()} ({len(_actual)}주)")

    # ── RWI_ISL_CTI: isl.org 보도자료 스크래핑 (v9에서 자동화됨, CLAUDE.md 0-c) ──────
    _cti_auto = fetch_rwi_isl_cti_auto()
    if _cti_auto is not None:
        _new = monthly_to_weekly(_cti_auto, ext_idx, _dates_ext, 'RWI_ISL_CTI')
        _actual = _new.dropna()
        if len(_actual):
            df_ext['RWI_ISL_CTI'] = _new
            print(f"  ✓ RWI_ISL_CTI 자동(isl.org): {_actual.index[0].date()} ~ {_actual.index[-1].date()} ({len(_actual)}주)")

    # 월간 지표는 _data_start보다 3개월 전부터 조회 (마지막 발표월이 _data_start 이전일 수 있음)
    _monthly_start = (_data_start - pd.DateOffset(months=3)).strftime('%Y-%m-%d')

    # ── GSCPI: NY Fed 자동 다운로드 ───────────────────────
    gscpi_s = fetch_gscpi_auto(_monthly_start, WEEK_END_TARGET)
    if gscpi_s is not None:
        _new = monthly_to_weekly(gscpi_s, ext_idx, _dates_ext, 'GSCPI')
        _actual = _new.dropna()
        if len(_actual):
            df_ext['GSCPI'] = _new
            print(f"  ✓ GSCPI 실제값: {_actual.index[0].date()} ~ {_actual.index[-1].date()} ({len(_actual)}주)")
    else:
        # fallback: 로컬 파일
        _gscpi_local_path = os.path.join(MKG_DIR, 'gscpi_data.xlsx')
        if Path(_gscpi_local_path).exists():
            try:
                _tmp = pd.read_excel(_gscpi_local_path, sheet_name='GSCPI Monthly Data')
                _tmp = _tmp[['Date','GSCPI']].dropna()
                _tmp.columns = ['date', 'GSCPI']
                _tmp['date'] = pd.to_datetime(_tmp['date'])
                _s = _tmp.dropna().set_index('date')['GSCPI']
                _new = monthly_to_weekly(_s, ext_idx, _dates_ext, 'GSCPI')
                _actual = _new.dropna()
                if len(_actual):
                    df_ext['GSCPI'] = _new
                    print(f"  ✓ GSCPI 로컬 fallback: {_actual.index[0].date()} ~ {_actual.index[-1].date()} ({len(_actual)}주)")
            except Exception as _e:
                print(f"  ⚠ gscpi_data.xlsx: {_e}")

    # ── Harpex: harpex_weekly.csv (로컬 fallback) ─────────────────────────
    for _hf in ['harpex_weekly.csv', 'harpex_weekly_with_source.csv']:
        _hf_path = os.path.join(MKG_DIR, _hf)
        if Path(_hf_path).exists():
            try:
                _hx = pd.read_csv(_hf_path)
                _hx.columns = _hx.columns.str.strip()
                _dc = [c for c in _hx.columns if 'date' in c.lower()][0]
                _vc = [c for c in _hx.columns if c != _dc][0]
                _hx[_dc] = pd.to_datetime(_hx[_dc])
                _hx_s = _hx.set_index(_dc)[_vc]
                _new_hx = _hx_s[_hx_s.index >= _data_start].resample('W-MON', closed='left', label='right').last().reindex(ext_idx).ffill()
                _actual_hx = _new_hx.dropna()
                if len(_actual_hx):
                    df_ext['Harpex'] = _new_hx
                    _dates_ext['Harpex'] = _get_last_dates(_hx_s[_hx_s.index >= _data_start], ext_idx)
                    print(f"  ✓ Harpex 실제값: {_actual_hx.index[0].date()} ~ {_actual_hx.index[-1].date()} ({len(_actual_hx)}주)")
                break
            except Exception as _e:
                print(f"  ⚠ {_hf}: {_e}")

    # ── GPR: Caldara 자동 다운로드 ────────────────────────────
    gpr_s = fetch_gpr_auto(_monthly_start, WEEK_END_TARGET)
    if gpr_s is not None:
        gpr_w = monthly_to_weekly(gpr_s, ext_idx, _dates_ext, 'GPR')
        actual = gpr_w.dropna()
        if len(actual):
            df_ext['GPR'] = gpr_w
            print(f"  ✓ GPR 실제값: {actual.index[0].date()} ~ {actual.index[-1].date()}")

    # ── KR_ExportVol: 관세청 API ────────────────────────────────
    kr_exp = fetch_kr_export_customs(_monthly_start, WEEK_END_TARGET)
    if kr_exp is not None:
        kr_w = monthly_to_weekly(kr_exp, ext_idx, _dates_ext, 'KR_ExportVol')
        actual = kr_w.dropna()
        if len(actual):
            df_ext['KR_ExportVol'] = kr_w
            print(f"  ✓ KR_ExportVol 실제값: {actual.index[0].date()} ~ {actual.index[-1].date()}")

    # ── Yahoo Finance 글로벌 ──────────────────────────────────
    print("  Yahoo Finance 글로벌 연장 중...")
    for ticker, col in YF_GLOBAL_MAP.items():
        s = yf_weekly(ticker, str(_data_start.date()), WEEK_END_TARGET, ext_idx, _dates_ext, col)
        if s is not None and s.dropna().any():
            df_ext[col] = s
            print(f"    ✓ {ticker} → {col}")

    # ── Yahoo Finance 한국 개별주 ─────────────────────────────
    print("  Yahoo Finance 한국 산업주 연장 중...")
    for ticker, col in YF_KR_STOCK_MAP.items():
        s = yf_weekly(ticker, str(_data_start.date()), WEEK_END_TARGET, ext_idx, _dates_ext, col)
        if s is not None and s.dropna().any():
            df_ext[col] = s
            print(f"    ✓ {ticker}")

    # ── Yahoo Finance ETF (KG 기반) ───────────────────────────
    # 주의: df_ext가 위에서 모든 컬럼을 ffill 초기화하므로
    #       `_etf_name in df_ext.columns` 체크는 항상 True → 수집 누락 버그.
    #       대신 이번 실행에서 실제 수집된 ETF를 추적하는 세트 사용.
    _collected_tickers = {t: c for t, c in YF_KR_STOCK_MAP.items() if c in df_ext.columns}
    _etf_collected_this_run = set()  # 이번 실행에서 실제로 수집된 ETF 이름
    for _nid, _nd in kg_raw['nodes'].items():
        if _nd.get('node_type') == 'korea_sector':
            for _etf_name, _ticker in _nd.get('etfTickers', {}).items():
                if _ticker in _collected_tickers:
                    print(f"    ⏭ {_ticker} ({_etf_name}) — 이미 {_collected_tickers[_ticker]}로 수집됨, 스킵")
                    continue
                if _etf_name in _etf_collected_this_run:
                    print(f"    ⏭ {_ticker} ({_etf_name}) — 이미 수집됨, 스킵")
                    continue
                s = yf_weekly(_ticker, str(_data_start.date()), WEEK_END_TARGET, ext_idx, _dates_ext, _etf_name)
                if s is not None and s.dropna().any():
                    df_ext[_etf_name] = s
                    _etf_collected_this_run.add(_etf_name)
                    print(f"    ✓ {_ticker} ({_etf_name})")

    # ── 반자동 수집 지표 병합 (monitoring/indicators/indicators_cumulative.csv) ──
    # RWI_ISL_CTI, GSCSI: CLAUDE.md 0-c 절차로 수동 수집되어 이 CSV에 누적됨.
    _cum_path = INDICATORS_DIR / "indicators_cumulative.csv"
    if os.path.exists(_cum_path):
        _cum = pd.read_csv(_cum_path)
        _cum['source_date'] = pd.to_datetime(_cum['source_date'], errors='coerce')
        _cum['value'] = pd.to_numeric(_cum['value'], errors='coerce')
        _cum = _cum.dropna(subset=['source_date', 'value'])
        _monthly_indicators = {'NAPMSDI', 'RWI_ISL_CTI', 'GSCSI'}
        _updated = []
        for ind_name in _cum['indicator'].unique():
            if ind_name not in df_ext.columns:
                continue
            if ind_name in _online_auto_indicators:
                continue
            _sub = _cum[_cum['indicator'] == ind_name].set_index('source_date')['value'].sort_index()
            if ind_name in _monthly_indicators:
                _weekly_vals = monthly_to_weekly(_sub, ext_idx)
            else:
                _weekly_vals = _sub.resample('W-MON', closed='left', label='right').last().reindex(ext_idx)
            _actual = _weekly_vals.dropna()
            # 누적 CSV의 실제 source_date 주간 매핑
            _cum_dates_ext = _get_last_dates(_sub, ext_idx)
            if len(_actual):
                for _dt in _actual.index:
                    df_ext.loc[_dt, ind_name] = _actual[_dt]
                _updated.append(f"{ind_name}({len(_actual)}주)")
            # 날짜 기록: _dates_ext에 cumulative source_date 반영
            if ind_name not in _dates_ext:
                _dates_ext[ind_name] = pd.Series(index=ext_idx, dtype='datetime64[ns]')
            _mask_ext = _cum_dates_ext.notna()
            if _mask_ext.any():
                _existing_ext = _dates_ext[ind_name].copy()
                _existing_ext.loc[_mask_ext] = _cum_dates_ext.loc[_mask_ext]
                _dates_ext[ind_name] = _existing_ext
        # 과거 행 소급 수정 금지(CLAUDE.md 0-b): 반자동 지표는 신규 주차(df_ext)에만 반영
        if _updated:
            print(f"  ✓ 반자동 수집 지표 병합: {', '.join(_updated)}")
        else:
            print(f"  ℹ 반자동 수집 지표: 신규 주차 매칭 없음")
    else:
        print(f"  ℹ {_cum_path} 없음 → 반자동 지표 병합 스킵")

    # ── 병합 & 저장 ───────────────────────────────────────────
    indicator_df = pd.concat([indicator_df, df_ext]).sort_index()
    indicator_df = indicator_df[~indicator_df.index.duplicated(keep='last')]
    indicator_df.to_csv(INDICATOR_CSV, encoding='utf-8-sig')

    # 날짜 기록 병합 & 저장 (과거 행 불변 원칙, CLAUDE.md 0-a/0-b)
    df_dates_new = pd.DataFrame(_dates_ext, index=ext_idx)
    df_dates_new.index.name = 'week_date'
    # dates 포맷 통일: Timestamp → YYYY-MM-DD 문자열
    for _dc in df_dates_new.columns:
        df_dates_new[_dc] = df_dates_new[_dc].apply(
            lambda x: str(pd.Timestamp(x).date()) if pd.notna(x) else ''
        )
    df_dates = pd.concat([df_dates_existing, df_dates_new]).sort_index()
    df_dates = df_dates[~df_dates.index.duplicated(keep='last')]
    df_dates.to_csv(DATES_CSV, encoding='utf-8-sig')
    print(f"   날짜 기록: {DATES_CSV} ({len(df_dates.columns)}개 지표)")

    # 결과 요약
    print(f"\n✅ EXTEND 완료: {len(indicator_df)}주 × {len(indicator_df.columns)}개 지표")
    print(f"   전체 기간: {indicator_df.index.min().date()} ~ {indicator_df.index.max().date()}")
    print()
    # ── 수집 결과 요약 ────────────────────────────────────────
    #  ⚠ 하드코딩 후보 목록을 컬럼 존재 여부로만 거르던 이전 방식은
    #     실제로 수집에 실패한 지표(Brent·GPR 등)까지 "실제값(신규)"로 보고했다.
    #     이제는 이번 실행에서 기록된 기준일(_dates_ext)을 근거로 판정한다.
    #     기준일이 없다 = 이번 실행에서 수집되지 않았다 (값은 이전 값이 남아 있음).
    _new_row  = indicator_df.index[-1]
    _prev_row = indicator_df.index[-2] if len(indicator_df) > 1 else None

    _fresh, _held, _failed = [], [], []
    for _c in indicator_df.columns:
        _ser = _dates_ext.get(_c)
        _d   = _ser.get(_new_row) if _ser is not None else None
        _d   = '' if _d is None or pd.isna(_d) else str(pd.Timestamp(_d).date())
        if not _d:
            _failed.append(_c)
            continue
        _vn = indicator_df.at[_new_row, _c]
        _vp = indicator_df.at[_prev_row, _c] if _prev_row is not None else None
        _same = (pd.notna(_vn) and _vp is not None and pd.notna(_vp)
                 and float(_vn) == float(_vp))
        (_held if _same else _fresh).append(f"{_c}({_d})")

    print(f"  ✓ 신규 수집 {len(_fresh)}개: {_fresh}")
    print(f"  = 값 유지 {len(_held)}개: {_held}   (기준일 있음 — 월간 지표 등 정상)")
    if _failed:
        _msg = (f"지표 {len(_failed)}개가 이번 실행에서 수집되지 않았습니다(기준일 없음) — "
                f"이전 값이 그대로 남아 있습니다: {_failed}")
        print(f"  \u26a0 {_msg}")
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            print(f"::warning title=\uc9c0\ud45c \uc218\uc9d1 \uc2e4\ud328::{_msg}")
    else:
        print("  ⚠ 수집 실패: 없음")

    return indicator_df


# ══════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════

def main(week_tag=None):
    print(f"{'='*60}")
    print(f"  KMI 해상 공급망 주간 모니터링 파이프라인")
    print(f"{'='*60}\n")

    os.chdir(BASE_DIR)  # 노트북 원본 코드가 상대경로 기준이므로 작업 디렉토리 고정

    week_tag = resolve_week_tag(week_tag)
    print(f"  WEEK_TAG = {week_tag}")

    # STEP 1
    kg_ctx = step1_load_kg()

    # STEP 2
    news_df = step2_load_and_match(week_tag, kg_ctx["entity_patterns"], kg_ctx["match_entities"])

    # STEP 3
    sampled_df = step3_stratified_sampling(news_df)

    # STEP 4
    phaseA_df = step4_phaseA_classification(
        sampled_df, week_tag, kg_ctx["nodes"], kg_ctx["entity_patterns"], kg_ctx["match_entities"]
    )

    # STEP 5
    indicator_df = step5_collect_indicators(kg_ctx["kg_raw"])

    print(f"\n{'='*60}")
    print("  Part 1 (Steps 1-5) 완료")
    print(f"{'='*60}")

    # STEP 6
    tier_info = step6_signal_aggregation(phaseA_df, indicator_df, kg_ctx, week_tag)

    # STEP 7
    scenario_json = step7_generate_scenario(phaseA_df, indicator_df, tier_info, kg_ctx, week_tag)

    # STEP 8
    html_path = step8_generate_html(scenario_json, week_tag, kg_ctx)

    # STEP 9
    email_paths = step9_prepare_email(html_path, str(BASE_DIR / 'scenario_results.json'), week_tag)

    print(f"\n{'='*60}")
    print("  Part 2 (Steps 6-9) 완료")
    print(f"{'='*60}")

    return {
        "week_tag": week_tag,
        "kg_ctx": kg_ctx,
        "news_df": news_df,
        "sampled_df": sampled_df,
        "phaseA_df": phaseA_df,
        "indicator_df": indicator_df,
        "tier_info": tier_info,
        "scenario_json": scenario_json,
        "html_path": html_path,
        "email_paths": email_paths,
    }



import copy
import math

# ══════════════════════════════════════════════════════════════
# STEP 6: 신호 집계 + Tier 판정 (V1/V2)
# (scenario_generator_v11.ipynb Part 1 / Cell 3, Cell 4 기반)
# ══════════════════════════════════════════════════════════════

WINDOW_WEEKS = 4   # 롤링 윈도우 (주)
MIN_ARTICLES = 3   # Tier 판정에 필요한 최소 기사 수
TIER_LABELS  = {1: '정상', 2: '관심', 3: '경계', 4: '위기'}
TIER_THRESHOLDS = {
    4: (85, 10),   # W+C > 85% AND Crisis > 10%
    3: (72, 15),   # W+C > 72% OR Crisis > 15%
    2: (55, 8),    # W+C > 55% OR Crisis > 8%
}

# Temporal decay 계수: exp(-λ * max(0, days_old - 7))
DECAY_LAMBDA = 0.1

# 클러스터 lifecycle 파라미터
CLUSTER_CONFIRM_WEEKS  = 2   # candidate → confirmed 에 필요한 연속 Warning+ 주수
CLUSTER_DISSOLVE_WEEKS = 4   # confirmed → dissolved 에 필요한 신호 없는 연속 주수
CLUSTER_CAND_DISSOLVE  = 2   # candidate → dissolved 에 필요한 신호 없는 연속 주수
MIN_CLUSTER_ARTICLES   = 5   # dominant 후보 최소 기사 수 (소수 기사 wc=100% 노이즈 방지)

# 클러스터 생성 대상 엔터티 타입
CLUSTER_ENTITY_TYPES = {
    'chokepoint', 'crisis_event', 'risk_event',
    'geopolitical_event', 'external_event', 'external_shock', 'maritime_event'
}
_DOMINANT_ELIGIBLE_TYPES = {
    'chokepoint', 'bypass_infrastructure',
    'crisis_event', 'geopolitical_event', 'external_shock',
}
_DOMINANT_ELIGIBLE_PREFIXES = ('CP_', 'EVT_', 'CE_', 'EV_SCENARIO')

_ALERT_ORDER = {'Normal': 0, 'Caution': 1, 'Warning': 2, 'Crisis': 3}

# 지리적 초크포인트 (해협·운하·지역) — KG 경로 검증 대상
GEOGRAPHIC_CPS = {
    'CP_Hormuz', 'CP_Suez', 'CP_Panama', 'CP_Malacca',
    'CP_Taiwan', 'CP_BabElMandeb', 'CP_RedSea', 'CP_BlackSea',
    'CP_Kaohsiung', 'CP_Shanghai', 'CP_Lombok',
}

# 클러스터 이벤트 → 원산지 국가 메타데이터
KG_EVENT_METADATA = {
    'CP_RussiaFuelExport': {'originCountry': '러시아'},
    'CP_BlackSea':         {'originCountry': '러시아'},
}

# 지정학적 trigger_location → CP 매핑
TRIGGER_ROUTE_CP_MAP = {
    # 호르무즈권
    'iran': 'CP_Hormuz', '이란': 'CP_Hormuz',
    'persian gulf': 'CP_Hormuz', '페르시아만': 'CP_Hormuz', '걸프': 'CP_Hormuz',
    'qatar': 'CP_Hormuz', '카타르': 'CP_Hormuz',
    'uae': 'CP_Hormuz', '아랍에미리트': 'CP_Hormuz',
    'saudi arabia': 'CP_Hormuz', '사우디': 'CP_Hormuz',
    'bahrain': 'CP_Hormuz', '바레인': 'CP_Hormuz',
    'kuwait': 'CP_Hormuz', '쿠웨이트': 'CP_Hormuz',
    'oman': 'CP_Hormuz', '오만': 'CP_Hormuz',
    'iraq': 'CP_Hormuz', '이라크': 'CP_Hormuz',
    # 수에즈/바브엘만데브/홍해권
    'yemen': 'CP_BabElMandeb', '예멘': 'CP_BabElMandeb',
    'houthi': 'CP_BabElMandeb', '후티': 'CP_BabElMandeb',
    'red sea': 'CP_BabElMandeb', '홍해': 'CP_BabElMandeb',
    'suez': 'CP_Suez', '수에즈': 'CP_Suez',
    'egypt': 'CP_Suez', '이집트': 'CP_Suez',
    # 말라카/남중국해권
    'south china sea': 'CP_Malacca', '남중국해': 'CP_Malacca',
    'malacca': 'CP_Malacca', '말라카': 'CP_Malacca',
    'singapore': 'CP_Malacca', '싱가포르': 'CP_Malacca',
    # 대만해협권
    'taiwan': 'CP_Taiwan', '대만': 'CP_Taiwan', '타이완': 'CP_Taiwan',
    'taiwan strait': 'CP_Taiwan', '대만해협': 'CP_Taiwan',
    # 파나마권
    'panama': 'CP_Panama', '파나마': 'CP_Panama',
    # 흑해권
    'black sea': 'CP_BlackSea', '흑해': 'CP_BlackSea',
    'ukraine': 'CP_BlackSea', '우크라이나': 'CP_BlackSea',
}

# 동적 빌드로 커버 안 되는 항목 (실데이터 기반; KG aliases 보강 시 삭제 예정)
_CANONICAL_SUPPLEMENT = {
    'chokepoint_hormutz':        'CP_Hormuz',
    'CHOKEPOINT_MEG':            'CP_Hormuz',
    'CP_PersianGulf':            'CP_Hormuz',
    'bab_elmandeb':              'CP_BabElMandeb',
    'chokepoint_bab_elmandeb':   'CP_BabElMandeb',
    'chokepoint_baab_mandeb':    'CP_BabElMandeb',
    'chokepoint_대만 해협':      'CP_Taiwan',
    'chokepoint_malaka':         'CP_Malacca',
    'CP_MalacaStrait':           'CP_Malacca',
    'CP_MalackaStrait':          'CP_Malacca',
    'CP_MalaccaSingapore':       'CP_Malacca',
    'CP_MalaccaStraits':         'CP_Malacca',
    'CP_MalaccastraitSouthChinaSea': 'CP_Malacca',
    'Chokepoint_MalackaStrait':  'CP_Malacca',
    'chokepoint_말라카_롬복':    'CP_Malacca',
    'CP_TSMC_Taiwan':            'CP_Taiwan',
    'CP_TaiwanStraits':          'CP_Taiwan',
    'chokepoint_taipei_strait':  'CP_Taiwan',
    'chokepoint_tw_strait':      'CP_Taiwan',
    'RiskEvent_TaiwanStraitTensions': 'CP_Taiwan',
    'RiskEvent_Hormuz_Critical':   'CP_Hormuz',
    'RiskEvent_Hormuz_Volatility':  'CP_Hormuz',
    'Hormuz_Strait_Chokepoint':    'CP_Hormuz',
    'TAIWAN_STRAIT_CHOKEPOINT':    'CP_Taiwan',
    'CP_SUEZ_STRAIT':              'CP_Suez',
    'panama_canal_capacity':       'CP_Panama',
    'panama_canal_chokepoint':     'CP_Panama',
    'panama_canal_transit':        'CP_Panama',
    'RussiaFuelExportBan':                       'CP_RussiaFuelExport',
    'RiskEvent_RussiaFuelExportBan':             'CP_RussiaFuelExport',
    'RiskEvent_RussiaDieselBan':                 'CP_RussiaFuelExport',
    'RiskEvent_Russia_Fuel_Export':              'CP_RussiaFuelExport',
    'RiskEvent_Russia_Fuel_Export_Ban':          'CP_RussiaFuelExport',
    'RiskEvent_Russia_Fuel_Export_Ban_Easing':   'CP_RussiaFuelExport',
    'RiskEvent_Russia_fuel_export_ban_easing':   'CP_RussiaFuelExport',
    'RiskEvent_Russia_fuel_ban_lift':            'CP_RussiaFuelExport',
    'russia_fuel_export':                        'CP_RussiaFuelExport',
    'russia_fuel_export_ban':                    'CP_RussiaFuelExport',
    'russia_fuel_export_ban_easing':             'CP_RussiaFuelExport',
    'russia_fuel_export_ban_partial':            'CP_RussiaFuelExport',
    'CP_RussiaEnergyExport':                     'CP_RussiaFuelExport',
    'CP_RussianPorts':                           'CP_RussiaFuelExport',
    'CP_RussiaCrudeExport':                      'CP_RussiaFuelExport',
    '2023_RedSea_Houthi':          'CP_RedSea',
    '2023_Red_Sea_Houthi':         'CP_RedSea',
    'CP_RedSea_Suez':              'CP_RedSea',
    'CP_RedSea_Hormuz':            'CP_RedSea',
    'CP_RedSea_BabElMandeb':       'CP_RedSea',
    'CP_RedSea_Bab_el_Mandeb':     'CP_RedSea',
    'CP_RedSea_Bab_El_Mandeb':     'CP_RedSea',
    'CP_RedSea_Bab_Elmandeb':      'CP_RedSea',
    'CP_RedSea_Aden':              'CP_RedSea',
    'CP_RedSea_Houthis':           'CP_RedSea',
    'CP_RedSeaRoute':              'CP_RedSea',
    'CE_RedSea2023':               'CP_RedSea',
    'CE_2024_IranRedSeaTension':   'CP_RedSea',
    'KI_RedSeaHouthi2023':         'CP_RedSea',
    'chokepoint_blacksea':         'CP_BlackSea',
    'chokepoint_ukraine_blacksea':  'CP_BlackSea',
    'CP_BlackSeaGrain':            'CP_BlackSea',
    'CP_BlackSeaGrains':           'CP_BlackSea',
    'CP_UkraineGrainCorridor':     'CP_BlackSea',
    'chokepoint_shanghai':         'CP_Shanghai',
    'CP_ShanghaiPort':             'CP_Shanghai',
    'CP_Shanghai_Port':            'CP_Shanghai',
    'CP_StraitsOfHormuz':          'CP_Hormuz',
    'CP_StaitOfHormuz':            'CP_Hormuz',
    'CP_HormuzStraits':            'CP_Hormuz',
    'CP_Strait_Hormuz':            'CP_Hormuz',
    'CE_Hormuz_Blockade_2026':     'CP_Hormuz',
    'CE_2024_Hormuz_TankerSeizure':'CP_Hormuz',
    'CE_2024_Hormuz_Closure':      'CP_Hormuz',
    'CE_2024_Hormuz_Tension':      'CP_Hormuz',
    'CP_PersianGulfChokepoint':    'CP_Hormuz',
    'CP_PersianGulf_Hormuz':       'CP_Hormuz',
    'CP_PanelMandeb':              'CP_BabElMandeb',
    'CP_Aden_Strait':              'CP_BabElMandeb',
    'CP_RussisFuelExport':         'CP_RussiaFuelExport',
    'CE_Panama_Canal_Toll_Dispute':'CP_Panama',
    'CE_Taiwan_Strait_Blockade_2024':'CP_Taiwan',
    'KI_TaiwanStraitGeopolitical': 'CP_Taiwan',
}

_CANONICAL_ENTITY_NAMES_SUPPLEMENT = {
    'CP_Kaohsiung':        '가오슝항',
    'CP_Shanghai':         '상하이항',
    'CP_RedSea':           '홍해',
    'CP_BlackSea':         '흑해',
    'CP_RussiaFuelExport': '러시아 연료 수출금지',
}

REGISTRY_FILE = BASE_DIR / 'cluster_registry_v2.json'


def _new_cluster(canonical_id, display_name, created_week):
    return {
        'canonical_id':         canonical_id,
        'display_name':         display_name,
        'status':               'candidate',
        'weeks_with_signal':    1,
        'weeks_without_signal': 0,
        'peak_alert':           'Normal',
        'created_week':         created_week,
        'last_signal_week':     created_week,
        'rolling_crisis_pct':   0.0,
        'rolling_wc_pct':       0.0,
        'n_articles_rolling':   0,
    }


def update_cluster_registry(registry, week_label, entity_signals):
    """
    entity_signals: {canonical_id: {crisis_pct, wc_pct, n_articles,
                                     display_name, peak_alert}}
    Lifecycle 규칙:
      Warning+ 발생 → weeks_with_signal 누적
        candidate + weeks_with_signal >= CONFIRM_WEEKS → confirmed
      Warning+ 없음 → weeks_without_signal 누적
        confirmed + weeks_without_signal >= DISSOLVE_WEEKS → dissolved
        candidate + weeks_without_signal >= CAND_DISSOLVE → dissolved
    """
    for cid, sig in entity_signals.items():
        has_signal = sig['wc_pct'] > 0

        if cid not in registry:
            if has_signal:
                registry[cid] = _new_cluster(cid, sig['display_name'], week_label)
                registry[cid]['rolling_crisis_pct'] = sig['crisis_pct']
                registry[cid]['rolling_wc_pct']     = sig['wc_pct']
                registry[cid]['n_articles_rolling']  = sig['n_articles']
                registry[cid]['n_crisis_rolling']    = sig.get('n_crisis', 0)
                registry[cid]['n_warning_rolling']   = sig.get('n_warning', 0)
                registry[cid]['peak_alert']          = sig['peak_alert']
            continue

        c = registry[cid]

        if has_signal:
            c['weeks_with_signal']    += 1
            c['weeks_without_signal']  = 0
            c['last_signal_week']      = week_label
            c['rolling_crisis_pct']    = sig['crisis_pct']
            c['rolling_wc_pct']        = sig['wc_pct']
            c['n_articles_rolling']    = sig['n_articles']
            c['n_crisis_rolling']     = sig.get('n_crisis', 0)
            c['n_warning_rolling']    = sig.get('n_warning', 0)
            if _ALERT_ORDER.get(sig['peak_alert'], 0) > _ALERT_ORDER.get(c['peak_alert'], 0):
                c['peak_alert'] = sig['peak_alert']
            if c['status'] in ('candidate', 'dissolved') and \
               c['weeks_with_signal'] >= CLUSTER_CONFIRM_WEEKS:
                c['status'] = 'confirmed'
        else:
            c['weeks_without_signal'] += 1
            c['weeks_with_signal']     = 0
            c['rolling_crisis_pct']    = 0.0
            c['rolling_wc_pct']        = 0.0
            c['n_articles_rolling']    = 0
            c['n_crisis_rolling']     = 0
            c['n_warning_rolling']    = 0
            if c['status'] == 'confirmed' and \
               c['weeks_without_signal'] >= CLUSTER_DISSOLVE_WEEKS:
                c['status'] = 'dissolved'
            elif c['status'] == 'candidate' and \
                 c['weeks_without_signal'] >= CLUSTER_CAND_DISSOLVE:
                c['status'] = 'dissolved'

    for cid, c in registry.items():
        if cid in entity_signals or c['status'] == 'dissolved':
            continue
        c['weeks_without_signal'] += 1
        c['weeks_with_signal']     = 0
        c['rolling_crisis_pct']    = 0.0
        c['rolling_wc_pct']        = 0.0
        c['n_articles_rolling']    = 0
        c['n_crisis_rolling']     = 0
        c['n_warning_rolling']    = 0
        if c['status'] == 'confirmed' and \
           c['weeks_without_signal'] >= CLUSTER_DISSOLVE_WEEKS:
            c['status'] = 'dissolved'
        elif c['status'] == 'candidate' and \
             c['weeks_without_signal'] >= CLUSTER_CAND_DISSOLVE:
            c['status'] = 'dissolved'


def aggregate_signals(df, ref_date, window_weeks=WINDOW_WEEKS):
    """ref_date 기준 직전 window_weeks 주 기사 집계 (V1 — 전체 합산 방식)."""
    win_end   = pd.Timestamp(ref_date)
    win_start = win_end - pd.Timedelta(weeks=window_weeks)
    sub = df[(df['date'] >= win_start) & (df['date'] < win_end)].copy()
    n = len(sub)
    if n == 0:
        return None

    vc = sub['alert_level_1st'].value_counts()
    crisis_pct  = vc.get('Crisis', 0) / n * 100
    warning_pct = vc.get('Warning', 0) / n * 100
    caution_pct = vc.get('Caution', 0) / n * 100
    normal_pct  = vc.get('Normal', 0) / n * 100
    wc_pct      = crisis_pct + warning_pct

    commodities = Counter()
    sectors     = Counter()
    for val in sub['affected_commodities'].dropna():
        try:
            items = json.loads(val) if isinstance(val, str) else []
            commodities.update(items)
        except Exception:
            pass
    for val in sub['affected_korea_sectors'].dropna():
        try:
            items = json.loads(val) if isinstance(val, str) else []
            sectors.update(items)
        except Exception:
            pass

    return {
        'n_articles':         n,
        'crisis_pct':         round(crisis_pct, 1),
        'warning_pct':        round(warning_pct, 1),
        'caution_pct':        round(caution_pct, 1),
        'normal_pct':         round(normal_pct, 1),
        'warning_crisis_pct': round(wc_pct, 1),
        'top_commodities':    [k for k, _ in commodities.most_common(5)],
        'top_sectors':        [k for k, _ in sectors.most_common(5)],
        'window_start':       win_start.strftime('%Y-%m-%d'),
        'window_end':         win_end.strftime('%Y-%m-%d'),
    }


def determine_tier(signal, prev_signal=None, prev_tier=None):
    """신호 dict → (tier, tier_label, trend, reason)  [V1]
    하강 제한: 한 주에 최대 1단계 하락 / 상승 추세: +1 단계
    """
    if signal is None or signal['n_articles'] < MIN_ARTICLES:
        raw_tier = 1
        if prev_tier is not None and prev_tier > raw_tier + 1:
            raw_tier = prev_tier - 1
        return raw_tier, TIER_LABELS[raw_tier], 'stable', \
               f"기사 수 부족 ({signal['n_articles'] if signal else 0}건)"

    wc = signal['warning_crisis_pct']
    cr = signal['crisis_pct']

    trend = 'stable'
    if prev_signal and prev_signal['n_articles'] >= MIN_ARTICLES:
        delta = cr - prev_signal['crisis_pct']
        if delta > 4:    trend = 'escalating'
        elif delta < -4: trend = 'de-esc'
    signal['trend'] = trend

    raw_tier = 1
    reason = '정상'
    for t in [4, 3, 2]:
        thr_wc, thr_cr = TIER_THRESHOLDS[t]
        if t == 4:
            hit = (wc > thr_wc and cr > thr_cr)  # T4: AND
        else:
            hit = (wc > thr_wc or cr > thr_cr)   # T3/T2: OR
        if hit:
            raw_tier = t
            op = 'AND' if t == 4 else 'OR'
            reason = f"W+C={wc:.0f}%(임계={thr_wc}), Crisis={cr:.0f}%(임계={thr_cr}), {op}"
            if trend == 'escalating' and raw_tier < 4:
                raw_tier += 1
                reason += ' +1(escalating)'
            break

    tier = raw_tier
    if prev_tier is not None and tier < prev_tier - 1:
        tier = prev_tier - 1
        reason += f' → 하강제한(raw={raw_tier}→{tier})'

    return tier, TIER_LABELS[tier], trend, reason


def determine_tier_v2(signal, prev_signal=None, prev_tier=None):
    """
    클러스터 기반 Tier 판정 (V2).
    - confirmed 클러스터의 최고 crisis%/wc% 기준
    - confirmed 없으면 → Tier 1 (candidate는 watchpoint만)
    - 하강 제한: 한 주에 최대 1단계 하락
    """
    if signal is None or signal['n_articles'] < MIN_ARTICLES:
        raw_tier = 1
        if prev_tier is not None and prev_tier > 2:
            raw_tier = prev_tier - 1
        return raw_tier, TIER_LABELS[raw_tier], 'stable', '기사 수 부족'

    dom_crisis = signal.get('dominant_crisis_pct', 0.0)
    dom_wc     = signal.get('dominant_wc_pct', 0.0)
    dom_cid    = signal.get('dominant_cluster')

    if not signal.get('confirmed_clusters'):
        reason   = '확정 클러스터 없음 (candidate 단계 — watchpoint만)'
        raw_tier = 1
        tier = raw_tier
        if prev_tier is not None and tier < prev_tier - 1:
            tier = prev_tier - 1
            reason += f' → 하강제한(→T{tier})'
        return tier, TIER_LABELS[tier], 'stable', reason

    trend = 'stable'
    if prev_signal and prev_signal.get('dominant_cluster') == dom_cid:
        delta = dom_crisis - prev_signal.get('dominant_crisis_pct', 0)
        if delta > 4:    trend = 'escalating'
        elif delta < -4: trend = 'de-esc'

    dom_name = signal.get('dominant_display', dom_cid)
    reason   = f"dominant={dom_name} crisis={dom_crisis:.0f}% wc={dom_wc:.0f}%"

    raw_tier = 1
    for t in [4, 3, 2]:
        thr_wc, thr_cr = TIER_THRESHOLDS[t]
        if t == 4:
            hit = (dom_wc > thr_wc and dom_crisis > thr_cr)  # T4: AND
        else:
            hit = (dom_wc > thr_wc or dom_crisis > thr_cr)   # T3/T2: OR
        if hit:
            raw_tier = t
            op = 'AND' if t == 4 else 'OR'
            reason  += f' → T{t}(임계wc={thr_wc},cr={thr_cr},{op})'
            if trend == 'escalating' and raw_tier < 4:
                raw_tier += 1
                reason  += ' +1(escalating)'
            break

    tier = raw_tier
    if prev_tier is not None and tier > prev_tier + 2:
        tier = prev_tier + 2
        reason += f' → 상승제한(raw={raw_tier}→{tier})'
    if prev_tier is not None and tier < prev_tier - 1:
        tier = prev_tier - 1
        reason += f' → 하강제한(raw={raw_tier}→{tier})'

    return tier, TIER_LABELS[tier], trend, reason


def _build_canonical_entity_map(nodes):
    """seed_kg_v4.json aliases/nameEn/name에서 동적 빌드 — 하드코딩 없음
    CLAUDE.md 규칙 17: 항상 원천 데이터에서 동적 도출
    """
    cmap = {}
    _PREFIXES = [
        'chokepoint_', 'Chokepoint_', 'CP_', 'CHOKE_', 'CHOKEPOINT_',
        'maritime_chokepoint_', 'RiskEvent_', 'maritime_route_',
        'foreign_port_', 'port_', 'crisis_event_', 'GEO_', 'MP_',
    ]
    _STOPWORDS = {'of', 'the', 'de', 'a', 'an', 'le'}

    def _wv(text):
        all_w = [w for w in re.split(r'[\s_\-]+', text) if w]
        filt_w = [w for w in all_w if w.lower() not in _STOPWORDS]
        vs = set()
        for words in [all_w, filt_w]:
            if len(words) >= 2:
                fwd, rev = list(words), list(reversed(words))
                for combo in [fwd, rev]:
                    vs.add(''.join(combo))
                    vs.add(''.join(w.capitalize() for w in combo))
                vs.add('_'.join(fwd));  vs.add('_'.join(fwd).lower());  vs.add('_'.join(fwd).upper())
                vs.add('_'.join(rev));  vs.add('_'.join(rev).lower());  vs.add('_'.join(rev).upper())
        if all_w:
            vs.add(all_w[0]); vs.add(all_w[0].lower())
        return vs

    def _av(key, nid):
        bvs = {
            key, key.lower(), key.upper(),
            key.replace(' ', '_'), key.replace(' ', '_').lower(), key.replace(' ', '_').upper(),
            key.replace('_', ' '), key.replace('_', ' ').lower(),
            key.replace(' ', '').replace('_', ''), key.replace(' ', '').replace('_', '').lower(),
        }
        bvs |= _wv(key)
        for v in bvs:
            if v: cmap[v] = nid
        for pfx in _PREFIXES:
            for v in bvs:
                if v: cmap[pfx + v] = nid
            if ' ' in key:
                cmap[pfx + key] = nid

    for nid, attrs in nodes.items():
        cmap[nid] = nid
        cmap[nid.lower()] = nid
        cmap[nid.upper()] = nid
        if nid.startswith('EV_GDELT_'):
            continue
        for a in attrs.get('aliases', []):
            _av(a, nid)
        if attrs.get('nameEn'): _av(attrs['nameEn'], nid)
        if attrs.get('name'):   _av(attrs['name'],   nid)
    return cmap


def _build_cluster_context(kg_data):
    """KG(nodes/G) 의존적인 클러스터 집계 함수·상수 묶음을 빌드.
    (scenario_generator_v11 Cell 3의 CANONICAL_ENTITY_MAP/extract_cluster_entities/
     aggregate_signals_v2/build_tier_plan_v2 등을 kg_data에 바인딩한 클로저로 구성)

    Returns:
        dict: {CANONICAL_ENTITY_MAP, CANONICAL_ENTITY_NAMES, extract_cluster_entities,
               aggregate_signals_v2, build_tier_plan_v2, normalize_entity_id}
    """
    nodes   = kg_data['nodes']
    G       = kg_data['G']

    canonical_map = _build_canonical_entity_map(nodes)
    canonical_map.update(_CANONICAL_SUPPLEMENT)

    canonical_names = {nid: attrs.get('name', nid) for nid, attrs in nodes.items()}
    canonical_names.update(_CANONICAL_ENTITY_NAMES_SUPPLEMENT)

    _dominant_kg_set = {
        nid for nid, nd in nodes.items()
        if nd.get('node_type') in _DOMINANT_ELIGIBLE_TYPES
        and not nid.startswith('EV_GDELT_')
    }

    def _is_dominant_eligible(cid):
        return (cid in _dominant_kg_set or
                any(cid.startswith(p) for p in _DOMINANT_ELIGIBLE_PREFIXES))

    port_transit_cps = {}
    for _nid, _ndata in G.nodes(data=True):
        if _ndata.get('node_type') == 'foreign_port':
            port_transit_cps[_nid] = set(_ndata.get('transitCPs', []))

    country_transit_cps = {}
    for _nid, _ndata in G.nodes(data=True):
        if _ndata.get('node_type') == 'foreign_port':
            _c = _ndata.get('country')
            if _c:
                country_transit_cps.setdefault(_c, set()).update(_ndata.get('transitCPs', []))

    def normalize_entity_id(raw_id):
        """raw entity_id → canonical ID.
        V4 Phase A LLM이 CE_/CRISIS_/EVENT_ 등 비표준 접두사를 사용하므로
        직접 매칭 실패 시 접두사 제거 후 재검색.
        """
        result = canonical_map.get(raw_id)
        if result:
            return result
        _LLM_PREFIXES = ('CE_', 'CRISIS_', 'EVENT_', 'EV_', 'POLICY_', 'EXT_', 'GEO_')
        for pfx in _LLM_PREFIXES:
            if raw_id.startswith(pfx):
                stripped = raw_id[len(pfx):]
                result = canonical_map.get(stripped)
                if result:
                    return result
                result = canonical_map.get('RiskEvent_' + stripped)
                if result:
                    return result
                break
        return raw_id

    def extract_cluster_entities(row):
        """기사 한 건에서 클러스터 대상 엔티티 추출 (matched_entities + Phase A 분류 기반).
        Returns: list of (canonical_id, display_name) — 중복 제거됨
        """
        entities = []
        seen = set()
        try:
            raw_entities = row.get('matched_entities', '[]')
            ent_ids = json.loads(raw_entities) if isinstance(raw_entities, str) else []

            trigger_loc = str(row.get('trigger_location', '') or '').strip()
            disruption_type = str(row.get('disruption_type', '') or '').strip()

            if disruption_type in ('ROUTE', 'SOURCE') and trigger_loc:
                tl_lower = trigger_loc.lower()
                inferred_cp = None
                if tl_lower in TRIGGER_ROUTE_CP_MAP:
                    inferred_cp = TRIGGER_ROUTE_CP_MAP[tl_lower]
                else:
                    for key, cp_id in TRIGGER_ROUTE_CP_MAP.items():
                        if key in tl_lower or tl_lower in key:
                            inferred_cp = cp_id
                            break
                if inferred_cp and inferred_cp not in [str(e) for e in ent_ids]:
                    ent_ids.append(inferred_cp)

            valid_cps = None
            if trigger_loc:
                for country, cps in country_transit_cps.items():
                    if country in trigger_loc or trigger_loc in country:
                        valid_cps = cps
                        break
                if trigger_loc.lower() in TRIGGER_ROUTE_CP_MAP:
                    inferred = TRIGGER_ROUTE_CP_MAP[trigger_loc.lower()]
                    if valid_cps is None:
                        valid_cps = {inferred}
                    else:
                        valid_cps = set(valid_cps) | {inferred}

            for eid in ent_ids:
                eid = str(eid).strip()
                if not eid:
                    continue
                canonical = canonical_map.get(eid, eid)

                ntype = None
                if canonical in nodes:
                    ntype = nodes[canonical].get('node_type', '')
                else:
                    prefix = canonical.split('_')[0] + '_' if '_' in canonical else ''
                    _PREFIX_TYPE = {
                        'CP_': 'chokepoint', 'CF_': 'commodity_flow',
                        'KS_': 'korea_sector', 'KC_': 'korea_company',
                        'RiskEvent_': 'crisis_event', 'EVT_': 'crisis_event',
                    }
                    for pfx, t in _PREFIX_TYPE.items():
                        if canonical.startswith(pfx):
                            ntype = t
                            break

                if ntype not in CLUSTER_ENTITY_TYPES:
                    continue
                if canonical in seen:
                    continue

                if valid_cps is not None and canonical in GEOGRAPHIC_CPS:
                    if canonical not in valid_cps:
                        continue

                seen.add(canonical)
                name = canonical_names.get(canonical,
                       nodes.get(canonical, {}).get('name', canonical))
                entities.append((canonical, name))
        except Exception:
            pass
        return entities

    def aggregate_signals_v2(df, ref_date, cluster_registry, window_weeks=WINDOW_WEEKS):
        """클러스터 기반 집계 (V2). V1 호환 필드 모두 포함."""
        win_end   = pd.Timestamp(ref_date)
        win_start = win_end - pd.Timedelta(weeks=window_weeks)
        sub = df[(df['date'] >= win_start) & (df['date'] < win_end)].copy()
        n = len(sub)
        if n == 0:
            return None

        vc = sub['alert_level_1st'].value_counts()
        crisis_pct  = vc.get('Crisis', 0) / n * 100
        warning_pct = vc.get('Warning', 0) / n * 100
        caution_pct = vc.get('Caution', 0) / n * 100
        normal_pct  = vc.get('Normal', 0) / n * 100
        wc_pct      = crisis_pct + warning_pct

        commodities = Counter()
        sectors     = Counter()
        for val in sub['affected_commodities'].dropna():
            try:
                commodities.update(json.loads(val) if isinstance(val, str) else [])
            except Exception:
                pass
        for val in sub['affected_korea_sectors'].dropna():
            try:
                sectors.update(json.loads(val) if isinstance(val, str) else [])
            except Exception:
                pass

        ent_articles = defaultdict(list)

        ref_ts = pd.Timestamp(ref_date)
        seven_days_ago = ref_ts - pd.Timedelta(days=7)
        n_new_articles_this_week = int((sub['date'] >= seven_days_ago).sum())

        _has_rel = 'relevance' in sub.columns
        sub_high = sub[sub['relevance'] == 'HIGH'] if _has_rel else sub

        for idx, (_, row) in enumerate(sub_high.iterrows()):
            ents = extract_cluster_entities(row)
            alert = row.get('alert_level_1st', 'Normal')
            article_date = pd.Timestamp(row['date'])
            days_old = max(0, (ref_ts - article_date).days)
            decay_w = math.exp(-DECAY_LAMBDA * max(0, days_old - 7))
            if not ents:
                ent_articles['__misc__'].append((alert, '기타', idx, decay_w))
            for canonical, display_name in ents:
                ent_articles[canonical].append((alert, display_name, idx, decay_w))

        entity_signals = {}
        for cid, articles in ent_articles.items():
            if cid == '__misc__':
                continue
            ent_n = len(articles)
            total_w = sum(a[3] for a in articles)
            if total_w == 0:
                continue
            crisis_w  = sum(a[3] for a in articles if a[0] == 'Crisis')
            warning_w = sum(a[3] for a in articles if a[0] == 'Warning')
            ent_cr = crisis_w / total_w * 100
            ent_wc = (crisis_w + warning_w) / total_w * 100
            peak   = max(articles, key=lambda x: _ALERT_ORDER.get(x[0], 0))[0]
            n_crisis = sum(1 for a in articles if a[0] == 'Crisis')
            n_warning = sum(1 for a in articles if a[0] == 'Warning')
            entity_signals[cid] = {
                'crisis_pct':   round(ent_cr, 1),
                'wc_pct':       round(ent_wc, 1),
                'n_articles':   ent_n,
                'n_crisis':     n_crisis,
                'n_warning':    n_warning,
                'display_name': articles[0][1],
                'peak_alert':   peak,
            }

        week_label = pd.Timestamp(ref_date).strftime('%G-W%V')
        update_cluster_registry(cluster_registry, week_label, entity_signals)

        confirmed = {
            cid: c for cid, c in cluster_registry.items()
            if c['status'] == 'confirmed'
        }
        dominant_cid       = None
        dominant_n_crisis  = 0
        dominant_n_warning = 0
        for cid, c in confirmed.items():
            if not _is_dominant_eligible(cid):
                continue
            if c['n_articles_rolling'] < MIN_CLUSTER_ARTICLES:
                continue
            nc = c.get('n_crisis_rolling', 0)
            nw = c.get('n_warning_rolling', 0)
            if nc > dominant_n_crisis:
                dominant_n_crisis  = nc
                dominant_n_warning = nw
                dominant_cid       = cid
            elif nc == dominant_n_crisis and nw > dominant_n_warning:
                dominant_n_warning = nw
                dominant_cid       = cid

        if dominant_cid:
            dom_idx = [a[2] for a in ent_articles.get(dominant_cid, [])]
            dom_sub = sub.iloc[dom_idx]
            dom_comm = Counter()
            dom_sect = Counter()
            for val in dom_sub['affected_commodities'].dropna():
                try: dom_comm.update(json.loads(val) if isinstance(val, str) else [])
                except Exception: pass
            for val in dom_sub['affected_korea_sectors'].dropna():
                try: dom_sect.update(json.loads(val) if isinstance(val, str) else [])
                except Exception: pass
            top_commodities = [k for k, _ in dom_comm.most_common(5)] or \
                              [k for k, _ in commodities.most_common(5)]
            top_sectors     = [k for k, _ in dom_sect.most_common(5)] or \
                              [k for k, _ in sectors.most_common(5)]
            n_new_dominant = int((dom_sub['date'] >= seven_days_ago).sum())
        else:
            top_commodities = [k for k, _ in commodities.most_common(5)]
            top_sectors     = [k for k, _ in sectors.most_common(5)]
            n_new_dominant  = 0

        return {
            'n_articles':         n,
            'crisis_pct':         round(crisis_pct, 1),
            'warning_pct':        round(warning_pct, 1),
            'caution_pct':        round(caution_pct, 1),
            'normal_pct':         round(normal_pct, 1),
            'warning_crisis_pct': round(wc_pct, 1),
            'top_commodities':    top_commodities,
            'top_sectors':        top_sectors,
            'window_start':       win_start.strftime('%Y-%m-%d'),
            'window_end':         win_end.strftime('%Y-%m-%d'),
            'entity_signals':     entity_signals,
            'confirmed_clusters': {cid: c['display_name'] for cid, c in confirmed.items()},
            'candidate_clusters': {
                cid: c['display_name'] for cid, c in cluster_registry.items()
                if c['status'] == 'candidate'
            },
            'dominant_cluster':   dominant_cid,
            'dominant_crisis_pct': confirmed[dominant_cid]['rolling_crisis_pct'] if dominant_cid else 0.0,
            'dominant_wc_pct':     confirmed[dominant_cid]['rolling_wc_pct'] if dominant_cid else 0.0,
            'dominant_n_crisis':   dominant_n_crisis,
            'dominant_display':   cluster_registry[dominant_cid]['display_name']
                                  if dominant_cid else None,
            'n_new_articles_this_week': n_new_articles_this_week,
            'n_new_dominant':           n_new_dominant,
            'event_status_dist':  dict(sub['event_status'].value_counts()) if 'event_status' in sub.columns else {},
            'disruption_type_dist': dict(sub['disruption_type'].replace('', 'none').value_counts()) if 'disruption_type' in sub.columns else {},
            'top_triggers': list(sub['trigger_location'].dropna().replace('', pd.NA).dropna().value_counts().head(5).index) if 'trigger_location' in sub.columns else [],
        }

    def build_tier_plan_v2(dates, df, cluster_registry_v2):
        """
        dates: pd.DatetimeIndex (W-MON)
        df   : phase_a 기사 데이터프레임
        반환 : ([(ref, tier, label, sig, dom, reason), ...], updated_registry)
        """
        _registry = copy.deepcopy(cluster_registry_v2)
        plan = []
        prev_sig  = None
        prev_tier = None
        for ref in dates:
            sig = aggregate_signals_v2(df, ref, _registry)
            tier, label, trend, reason = determine_tier_v2(sig, prev_sig, prev_tier)
            dom = (sig.get('dominant_display') or '-') if sig else '-'
            plan.append((ref, tier, label, sig, dom, reason))
            prev_sig  = sig
            prev_tier = tier
        return plan, _registry

    return {
        'CANONICAL_ENTITY_MAP':   canonical_map,
        'CANONICAL_ENTITY_NAMES': canonical_names,
        'normalize_entity_id':    normalize_entity_id,
        'extract_cluster_entities': extract_cluster_entities,
        'aggregate_signals_v2':   aggregate_signals_v2,
        'build_tier_plan_v2':     build_tier_plan_v2,
    }


def _load_all_phase_a():
    """모든 news_scored_phaseA_v7_*.csv(과거 누적 + 이번 주 신규) 로드/합산.
    (scenario_generator_v11.ipynb Cell 1의 v5→v2 호환 레이어 기반)
    롤링 4주 윈도우 집계에는 여러 주차의 과거 기사가 필요하므로,
    step4가 저장한 이번 주 CSV뿐 아니라 BASE_DIR의 모든 주차 파일을 합산한다.
    """
    files = sorted(glob.glob(str(BASE_DIR / "news_scored_phaseA_v7_*.csv")))
    if not files:
        raise FileNotFoundError(
            f"{BASE_DIR} 에 news_scored_phaseA_v7_*.csv 파일이 없습니다. STEP 4를 먼저 실행하세요."
        )
    dfs = []
    for f in files:
        _df = pd.read_csv(f)
        _tag = re.search(r"v7_(\d{8})\.csv$", f).group(1)
        _df['week_tag'] = _tag
        dfs.append(_df)
    phase_a = pd.concat(dfs, ignore_index=True)
    phase_a['pub_date'] = pd.to_datetime(phase_a['pub_date'], format='mixed', utc=True).dt.tz_convert(None)
    phase_a['date'] = phase_a['pub_date']
    phase_a = phase_a.drop_duplicates(subset=['title', 'pub_date']).sort_values('pub_date').reset_index(drop=True)

    # v5→v2 호환 컬럼 생성 (alert_level_1st, affected_commodities, affected_korea_sectors)
    phase_a['alert_level_1st'] = phase_a['recommended_alert_level'].fillna('Normal')

    def _extract_by_prefix(entities_json, prefix):
        try:
            ents = json.loads(entities_json) if isinstance(entities_json, str) else []
            return json.dumps([e for e in ents if e.startswith(prefix)])
        except Exception:
            return '[]'

    phase_a['affected_commodities'] = phase_a['matched_entities'].apply(
        lambda x: _extract_by_prefix(x, 'CF_'))
    phase_a['affected_korea_sectors'] = phase_a['matched_entities'].apply(
        lambda x: _extract_by_prefix(x, 'KS_'))
    return phase_a


def _bootstrap_registry_from_history(phase_a, cluster_ctx):
    """cluster_registry_v2.json이 없는 최초 실행 시, 과거 전체 이력을 순회하며
    클러스터 레지스트리를 빌드업. (scenario_generator_v11 Cell 3 하단 로직)"""
    start_date = phase_a['date'].min() + pd.Timedelta(weeks=WINDOW_WEEKS)
    end_date   = phase_a['date'].max()
    weekly_dates = pd.date_range(start=start_date, end=end_date, freq='W-MON')
    registry = {}
    if len(weekly_dates) == 0:
        return registry
    print(f"  [V2] 클러스터 레지스트리 빌드업(최초 1회): {len(weekly_dates)}주 이력 순회 중...")
    for ref in weekly_dates:
        cluster_ctx['aggregate_signals_v2'](phase_a, ref, registry)
    active = {cid: c for cid, c in registry.items() if c['status'] == 'confirmed'}
    print(f"  → 빌드업 완료: 전체 {len(registry)}개 클러스터, confirmed {len(active)}개")
    return registry


def step6_signal_aggregation(phaseA_df, indicator_weekly_df, kg_data, week_tag):
    """4주 롤링 윈도우 신호 집계 + Tier 판정(V1/V2).

    phaseA_df: step4가 이번 주에 저장한 뉴스 분류 결과(참고용 — 실제 집계는
               BASE_DIR의 모든 news_scored_phaseA_v7_*.csv 누적본을 다시 로드하여 사용).
    kg_data: step1_load_kg()의 반환값 (nodes/G/kg_raw 등)

    Returns:
        dict: {ref_date, week_label, period, tier, tier_label, trend, reason,
               signal, phase_a(전체 이력 df), cluster_ctx, v1_tier}
    """
    print(f"\n{'='*60}")
    print("  STEP 6: 신호 집계 + Tier 판정 (V1/V2)")
    print(f"{'='*60}")

    phase_a = _load_all_phase_a()
    print(f"  Phase A 누적: {len(phase_a)}건 "
          f"({phase_a['pub_date'].min().date()} ~ {phase_a['pub_date'].max().date()}, "
          f"이번 주 {len(phaseA_df)}건 포함)")

    cluster_ctx = _build_cluster_context(kg_data)

    # ── 클러스터 레지스트리 로드 또는 최초 빌드업 ──
    if REGISTRY_FILE.exists():
        with open(REGISTRY_FILE, encoding='utf-8') as f:
            registry = json.load(f)
        print(f"  ✓ 레지스트리 로드: {REGISTRY_FILE.name} ({len(registry)}개 클러스터)")
    else:
        registry = _bootstrap_registry_from_history(phase_a, cluster_ctx)

    # ── 이번 주 ref_date(월요일) 계산: week_tag(일요일)의 다음 날 ──
    ref_date = pd.Timestamp(week_tag) + timedelta(days=1)
    prev_sunday = ref_date - pd.Timedelta(days=1)
    week_label  = prev_sunday.strftime('%G-W%V')
    period      = f"{week_label} ({ref_date.date()})"

    tier_plan, updated_registry = cluster_ctx['build_tier_plan_v2']([ref_date], phase_a, registry)
    ref, tier, label, sig, dom, reason = tier_plan[0]

    with open(REGISTRY_FILE, 'w', encoding='utf-8') as f:
        json.dump(updated_registry, f, ensure_ascii=False, indent=2, default=str)
    print(f"  ✓ 레지스트리 갱신 저장: {len(updated_registry)}개 클러스터")

    # ── V1(참고용) — 직전 주 상태 없이 이번 주 신호만으로 계산 (정보 제공용) ──
    v1_sig = aggregate_signals(phase_a, ref_date)
    v1_tier, v1_label, v1_trend, v1_reason = determine_tier(v1_sig, None, None) if v1_sig else (1, TIER_LABELS[1], 'stable', '기사 없음')

    print(f"  {week_label} (ref={ref_date.date()})")
    print(f"    V2 Tier {tier} ({label}) — dominant={dom} | {reason}")
    print(f"    V1 Tier {v1_tier} ({v1_label}) [참고, 직전주 상태 미반영] | {v1_reason}")
    if sig:
        print(f"    신호: Crisis {sig['crisis_pct']}% | Warning {sig['warning_pct']}% | "
              f"합산 {sig['warning_crisis_pct']}% | 총 {sig['n_articles']}건")

    return {
        'ref_date':    ref_date,
        'week_label':  week_label,
        'period':      period,
        'tier':        tier,
        'tier_label':  label,
        'trend':       sig.get('trend', 'stable') if sig else 'stable',
        'reason':      reason,
        'signal':      sig,
        'phase_a':     phase_a,
        'cluster_ctx': cluster_ctx,
        'v1_tier':     v1_tier,
        'v1_tier_label': v1_label,
    }


# ══════════════════════════════════════════════════════════════
# STEP 7: LLM 시나리오 생성 (Claude Sonnet)
# (scenario_generator_v11.ipynb Part 2-3 / Cell 5, Cell 9 기반)
# ══════════════════════════════════════════════════════════════

SCENARIO_SYSTEM = (
    "You are a KMI (Korea Maritime Institute) maritime supply chain analyst. "
    "Generate weekly risk monitoring scenario reports about maritime supply chain "
    "disruptions and their impact on Korean economy. "
    "Output language: Korean. Return ONLY valid compact JSON, no explanation."
)

PROMPT_TEMPLATE = """해운 공급망 주간 모니터링 시나리오 (Tier {tier}: {tier_label})

[현재 주 신호]
기간: {period} (직전 4주 집계: {window_start} ~ {window_end})
Signal(전체): Crisis {crisis_pct}% | Warning {warning_pct}% | 합산 {wc_pct}% | 총 {n}건
이번 주(직전 7일) 신규 기사: {n_new_articles_this_week}건
⚠ 기사 날짜 주의: [주요 기사 목록]에는 최대 4주분 기사가 포함됩니다. 7일 초과 기사는 배경 맥락으로만 참고하고, situation_summary에서 이번 주 사건처럼 서술하지 마세요.
추세: {trend}
주요 품목: {commodities}
주요 섹터: {sectors}

[기사 분류 분포 (Phase A)]
사건유형: {event_status_summary}
교란유형: {disruption_type_summary}
주요 발생지역: {top_triggers_summary}

{cluster_section}

[전주 시나리오]
{prev_summary}

[이번 주 일별 모니터링 요약]
{daily_context_section}

{articles_section}

⚠ 기사 인용 규칙: 위 [주요 기사 목록]의 [N] 번호를 situation_summary 본문에서 해당 사실 뒤에 [N] 형태로 삽입하라. 모든 문장에 인용이 필요하지는 않으나, 핵심 사건·수치·정책 변화에는 반드시 출처 기사 번호를 달 것. 인용은 situation_summary에만 적용하고 다른 필드(part_a, part_d 등)에는 [N]을 넣지 말 것.
⚠ 문장 분리 규칙: situation_summary에서 같은 날짜에 발생한 서로 다른 사건은 반드시 별도 문장으로 분리할 것. 하나의 문장에 무관한 사건 두 개를 합치지 말 것.
⚠ 날짜 귀속 규칙: [주요 기사 목록]에 표시된 날짜는 '보도일'이며 사건이 실제로 발생한 날이 아니다. 보도일을 발생일로 단정하지 말 것. 날짜를 언급할 때는 "8월 20일 보도에 따르면", "8월 20일 보도된 바에 따르면"처럼 보도 시점임이 드러나게 쓰거나, 기사 제목·요약에 발생일이 명시된 경우에만 발생일로 서술할 것.
⚠ 예고·위협 구분 규칙: 기사 태그의 THREAT 는 위협·예고·경고로서 아직 발생하지 않은 사안이다. THREAT 기사를 이미 일어난 사실로 서술하지 말 것("~했다" 금지 → "~하겠다고 예고했다/위협했다/경고했다"). 실제 발생한 교란으로 취급할 수 있는 것은 DISRUPTION 뿐이다.
{indicator_section}

{ind_changes_section}

⚠ 지표 수치 인용 규칙: 본문(situation_summary, part_d pathway 등)에서 지표 변동률(%)을 언급할 때 반드시 위 [주요 지표 변화]에 제공된 chg_pct 값을 그대로 사용할 것. 직접 계산하거나 반올림하지 말 것. 예: 위에 +168.1%로 제공되면 본문에서도 +168.1%로 표기.

[KG 컨텍스트]
{kg_ctx}

[교란유형별 전파 경로 추적 지침]
기사 목록에서 트리거(THREAT/DISRUPTION)와 국내영향(DOMESTIC_IMPACT)을 식별하고,
KG 구조적 전파 경로를 따라 연결하라. 기사가 evidence, KG가 backbone이다.

1) 경로교란 (ROUTE): 트리거 → CP(초크포인트 차단) → CF(해당 경로 물자) → KS(의존 산업)
   - KG 엣지: CP --[restrictsFlowOf]--> CF --[feedsInto]--> KS
   - 예: 후티 공격(트리거) → CP_BabElMandeb → CF_CrudeOil → KS_Energy

2) 공급원교란 (SOURCE): 트리거 → 수출국/정책(공급 차단) → CF(해당국 물자) → KS
   - CP는 관련 없거나 부차적. 정책/제재가 원인.
   - 예: 중국 수출금지(트리거) → CF_Urea(중국산 97%) → KS_FoodAgri

3) 해운교란 (LOGISTICS): 트리거 → 항만/운임(물류 마비) → CF(해당 경로 물자) → KS
   - 예: COVID(트리거) → 항만 폐쇄 → CF_EuroContainer → KS_Manufacture

part_a(routes)에는 위 패턴을 따라 실제 기사 증거가 있는 경로만 작성.
각 route의 disruption_type 필드에 해당 경로의 교란 유형(ROUTE/SOURCE/LOGISTICS)을 반드시 기입.
판단 기준: CP(초크포인트) 차단이 원인이면 ROUTE, 정책/제재/생산차질이 원인이면 SOURCE, 항만/운임/선복 문제이면 LOGISTICS.
복합 교란(예: 호르무즈 봉쇄 + 원유 수출 중단)은 주된 트리거 1개를 선택.
기사에 없는 경로를 추론하지 말 것.
part_b(cascades)의 산업간 전파도 반드시 KG의 suppliesTo 엣지가 존재하는 산업 쌍만 작성. KG에 없는 산업간 연결을 상상하여 만들지 말 것.
DOMESTIC_IMPACT 기사는 전파 경로의 끝단(한국 영향)에 해당하며, 해당 KS 섹터의 영향 evidence로 사용.

[심각도 기준]
shock>=15%=심각 | 5~15%=중요 | 1~5%=보통 | <1%=미약

[시간대 기준]
초기: 0-4주 | 중기: 4-12주 | 장기: 12주+

[생성 지침 - Tier {tier} ({tier_label})]
{tier_guidance}

아래 JSON 구조로만 응답. 없는 항목은 빈 배열([]) 또는 빈 문자열로:
{{
  "period": "{period}",
  "tier": {tier},
  "tier_label": "{tier_label}",
  "header": {{
    "period": "{period}",
    "crisis_level": "Normal|Caution|Warning|Crisis 중 하나",
    "situation_summary": "날짜 기반 서술형 요약 (Tier에 따라 1-4문단). [주요 기사 목록]의 [N] 번호를 활용하여 주요 사실에 출처를 [N] 형태로 인용할 것.",
    "changes_from_prev": [
      {{"key": "지표키(아래 [주요 지표 변화] 목록에서 선택)", "detail": "변화 원인 한 문장 설명"}}
    ],
    "watchpoints": [
      {{"horizon": "초기(0-4주)|중기(4-12주)|장기(12주+)", "point": "주시 포인트"}}
    ]
  }},
  "part_a": {{
    "routes": [
      {{
        "commodity": "품목명",
        "status": "활성|신규활성|비활성",  // 판단 기준: [전주 시나리오] 활성경로 목록만 참조. 전주 목록에 이미 있으면 반드시 "활성". 전주 목록에 없던 품목이 이번 주 처음 등장하면 "신규활성". 새 이벤트/에스컬레이션이 발생해도 전주에 이미 있었으면 "활성" 유지. 전주에 있었으나 이번 주 기사 근거 없으면 "비활성".
        "path": "초크포인트 → 공급차단(즉시) → 가격급등(1-2주) → 한국타격(2-4주)",
        "kg_basis": "KG 엣지 또는 설명",
        "disruption_type": "ROUTE|SOURCE|LOGISTICS",  // 아래 [교란유형별 전파 경로 추적 지침] 참조. 복합이면 주된 유형 1개.
        "is_new": false
      }}
    ]
  }},
  "part_b": {{
    "cascades": [
      {{
        "name": "에너지 → 소재 → 제조",
        "steps": [
          {{"from": "섹터A", "to": "섹터B", "mechanism": "전파 메커니즘", "lag": "2-4주"}}
        ],
        "is_new": false
      }}
    ]  // cascade의 from→to는 반드시 KG suppliesTo 엣지가 존재하는 쌍만 허용. 예: KS_Energy→KS_Material(O), KS_FoodAgri→KS_Construction(X, KG에 없음)
  }},
  "part_d": {{
    "matrix": [
      {{
        "sector": "산업명",
        "direction": "네거티브|포지티브|혼합",
        "initial": "심각|중요|보통|미약",
        "mid": "심각|중요|보통|미약",
        "long": "심각|중요|보통|미약",
        "pathway": "주요 전파경로 요약",
        "change": "↑↑|↑|↓|↓↓|☆(신규)|−"
      }}
    ]
  }},
  "part_e": {{
    "vulnerabilities": ["공급망 취약점 설명"],
    "monitoring_recommendations": ["모니터링 권고 항목"]
  }},
  "overall_severity": "심각|중요|보통|미약",
  "overall_direction": "네거티브|포지티브|혼합|안정",
  "alert_level": "Normal|Caution|Warning|Crisis"
}}
규칙: JSON만 출력. 설명 없음."""


TIER_GUIDANCE = {
    1: (
        "Tier 1(정상): 공급망 정상 상태. 기사 요약 위주로 간결하게 작성.\n"
        "⚠ 전주 Tier 확인 필수: [전주 시나리오]에 표기된 전주 Tier가 1(정상)이면, \n"
        "'이번 주에도' '지속되고 있으며' '계속' 등 연속성 표현 금지. \n"
        "전주도 정상이었으므로 연속 상황이 아님.\n"
        "- situation_summary: 2-3문장. '주요 해상 초크포인트에서 한국 공급망에 직접적 영향을 미치는 위기 징후는 감지되지 않음'으로 시작. \n"
        "이어서 이번 주 주요 공급망 관련 기사 동향을 간략히 요약. \n"
        "기사 목록에 Warning/Crisis 기사가 있으면 해당 내용을 언급하되, 한국 직접 영향이 제한적임을 명시.\n"
        "- part_a: 빈 배열\n"
        "- part_b: 빈 배열\n"
                "- part_d: 빈 배열\n"
        "- part_e: vulnerabilities 빈 배열, monitoring_recommendations 1개 ('정기 모니터링 지속')\n"
        "- watchpoints: 1개\n"
        "- overall_severity: '미약'\n"
        "- overall_direction: '안정'\n"
        "- alert_level: 'Normal'"
    ),
    2: (
        "Tier 2(관심): 간결하게 작성.\n"
        "⚠ 전주 Tier 확인: [전주 시나리오]의 전주 Tier가 현재보다 낮으면(예: T1→T2), "
        "'이번 주에도' '지속' 등 연속 표현 금지. 신규 발생으로 서술.\n"
        "⚠ Part A status 연속성 규칙: [전주 시나리오]의 활성 경로 목록과 반드시 대조.\n"
        "  · 활성: 전주 목록에 이미 있던 품목. 새 이벤트/에스컬레이션이 발생해도 전주에 있었으면 반드시 활성.\n"
        "  · 신규활성: 전주 목록에 없던 품목이 이번 주 처음 등장할 때만 사용.\n"
        "  · 비활성: 전주에 있었으나 이번 주 기사 근거가 없어진 경로.\n"
        "⚠ 기사 범위 원칙: 이번 주 [주요 기사 목록]에 등장한 사건/초크포인트 범위 안에서만 서술하라.\n"
        "⚠ Part A 경로 분리: KG 초크포인트(호르무즈·파나마 등)와 비KG 뉴스 이벤트(러시아 수출금지 등)를 같은 path 안에 '→'로 이어 연결하지 말 것. 각각 별개의 route 항목으로 분리할 것. 잘못된 예: '호르무즈 해협 → 러시아 수출금지 → 유가 급등'.\n"
        "⚠ Part A KG 경로 준수: 각 commodity의 path에는 해당 commodity의 kg_basis에 명시된 초크포인트만 포함하라. KG에 연결되지 않은 초크포인트를 path에 임의로 추가하지 말 것. 잘못된 예: CF_EuroContainer(수에즈·바브엘만데브·말라카 경유)의 path에 파나마 운하를 대체항로로 서술. 파나마 교란이 다른 commodity에 간접 영향을 미치더라도 CF_EuroContainer의 path에 직접 포함해서는 안 됨.\n"
        "- situation_summary: 1-2문장. 날짜 언급 불필요. 핵심 사건에 [N] 기사 인용을 달 것.\n"
        "- part_a: 핵심 경로 1-2개\n"
        "- part_b: 빈 배열\n"
                "- part_d: 주요 산업 2-3개 (산업별 영향 방향, 심각도, 전파경로 포함)\n"
        "- part_e: vulnerabilities 1개 (이번 주 기사에 실제로 등장한 사건과 직접 연관된 것만. "
        "현재 주 신호에 없는 초크포인트를 취약점에 포함하지 마라), "
        "monitoring_recommendations 1-2개. "
        "[시장 지표]의 이상 신호(⚠)가 있으면 해당 수치를 근거로 사용할 것\n"
        "⚠ 비축유 주의: 한국 정부 비축유 약 200일 분량 보유. "
        "수 주 이내 단기 봉쇄 시나리오에서 '비축유 부족'은 취약점이 아님.\n"
        "⚠ 지표 해석 주의: [시장 지표]의 초크포인트 통과선박 수([글로벌 통계])는 "
        "글로벌 통과량이며 한국 향 선박 수가 아님. 이 수치로 한국 직접 영향을 단정 짓거나 "
        "봉쇄·회피 원인을 임의로 서술하지 말 것.\n"
        "- watchpoints: 1-2개"
    ),
    3: (
        "Tier 3(경계): 표준 깊이로 작성.\n"
        "⚠ 전주 Tier 확인: [전주 시나리오]의 전주 Tier가 현재보다 낮으면(예: T1→T3), "
        "'이번 주에도' '지속' 등 연속 표현 금지. 신규 발생으로 서술.\n"
        "⚠ Part A status 연속성 규칙: [전주 시나리오]의 활성 경로 목록과 반드시 대조.\n"
        "  · 활성: 전주 목록에 이미 있던 품목. 새 이벤트/에스컬레이션이 발생해도 전주에 있었으면 반드시 활성.\n"
        "  · 신규활성: 전주 목록에 없던 품목이 이번 주 처음 등장할 때만 사용.\n"
        "  · 비활성: 전주에 있었으나 이번 주 기사 근거가 없어진 경로.\n"
        "⚠ 기사 범위 원칙: 이번 주 [주요 기사 목록]에 실제로 등장한 사건/초크포인트 범위 안에서만 서술하라. "
        "기사에 없는 초크포인트나 사건을 임의로 포함하지 마라.\n"
        "⚠ Part A 경로 분리: KG 초크포인트(호르무즈·파나마 등)와 비KG 뉴스 이벤트(러시아 수출금지, 인도 쌀 수출금지 등)를 "
        "같은 path 안에 '→'로 이어 연결하지 말 것. "
        "잘못된 예: '호르무즈 해협 → 러시아 수출금지 → 유가 급등'. "
        "KG 초크포인트 원인과 뉴스 이벤트 원인은 반드시 별개의 route 항목으로 분리할 것.\n"
        "⚠ Part A KG 경로 준수: 각 commodity의 path에는 해당 commodity의 kg_basis에 명시된 초크포인트만 포함하라. KG에 연결되지 않은 초크포인트를 path에 임의로 추가하지 말 것. 잘못된 예: CF_EuroContainer(수에즈·바브엘만데브·말라카 경유)의 path에 파나마 운하를 대체항로로 서술. 파나마 교란이 다른 commodity에 간접 영향을 미치더라도 CF_EuroContainer의 path에 직접 포함해서는 안 됨.\n"
        "⚠ 기사 신선도 원칙: [위기 사건 현황]의 주요 위기 사건 '이번 주 신규'가 0건이면, "
        "해당 위기 사건의 과거 기사를 다시 열거하거나 현재형으로 재서술하지 말 것. "
        "대신 '○○ 위기가 지속되고 있으나 이번 주 추가 보도 없음' 수준으로 1문장만 언급하라.\n"
        "- situation_summary: 3-4문단. "
        "위의 [주요 기사 목록]에 있는 날짜(MM월 DD일)를 활용하여 서술하고, 핵심 사건·수치에 [N] 기사 인용을 달 것.\n"
        "단락 구성 원칙: 동일 초크포인트·사건에 관한 내용은 한 단락에 집약하라. "
        "서로 다른 초크포인트(예: 대만해협 vs 파나마 운하)는 별개 단락으로 분리하라. "
        "단락 내에서는 시간순을 유지하되, 단락 간 교차 서술은 금지한다. "
        "예시: [단락1] 대만해협 관련 사건을 날짜순으로 서술. "
        "[단락2] 파나마 운하 관련 사건을 날짜순으로 서술.\n"
        "⚠ situation_summary 주의: 서로 인과적으로 연결할 수 없는 이질적 사건들(예: 남아공 전력난, 방글라데시 발전소, 대만해협 훈련)을 "
        "'동시에' '중첩되면서' 등으로 억지 연결하지 마라. 각 사건이 독립적이라면 별개로 서술하라.\n"
        "- part_a: 활성 경로 전체 (3-4개)\n"
        "- part_b: 주요 산업간 전파 1-2개\n"
        "- part_d: 전체 산업 6-7개 (산업별 영향 방향, 초기/중기/장기 심각도, 전파경로, 전주 대비 변화를 상세 서술)\n"
        "  ⚠ 식량/식품(KS_FoodAgri) 서술 시: 에너지 비용 상승이 식품 생산단계(냉장물류, 수산가공, 양식장 운영비)에 미치는 영향을 반드시 포함하라. 수산물 수출입 기업은 벙커유·운임 상승의 직접 영향권이므로, 해운/물류 비용 증가가 수산업 경쟁력에 미치는 경로도 pathway에 반영할 것.\n"
        '- part_e: vulnerabilities 2개 (반드시 이번 주 [주요 기사 목록]에 등장한 사건과 직접 연관된 것만. '
        '현재 주 신호에 없는 초크포인트는 취약점에 포함하지 말 것), '
        'monitoring_recommendations 2-3개. [시장 지표]의 BDI·운임·유가 등 공급망 수치와 변화율을 근거로 활용하라 (예: "BDI +XX%, WTI +XX%")\n'
        "  ⚠ 수산업 취약점: 해운 할증료·벙커유 급등 시 수산물 수출입 기업의 물류비 부담 증가를 vulnerabilities 또는 monitoring_recommendations에서 다룰 것 (KG: KC_Dongwon, KC_Sajo 등 수산기업이 KS_FoodAgri 소속).\n"
        "⚠ 주가 직접 인용 금지: situation_summary·changes_from_prev 등 서술 텍스트에서 "
        "개별 기업 주가·매출 수치(예: '에너지A 주가 -5.2%', '물류B 매출 감소' 등 구체 수치)를 직접 인용하지 말 것. "
        "주가는 [시장 지표] 패널에 별도 표시되므로 서술에서는 '주가 압박', '시장 심리 악화' 등 정성적 표현으로 대체할 것.\n"
        "⚠ 개별 기업명 사용 금지: situation_summary, part_d(pathway), part_e 등 모든 서술에서 개별 기업명(예: 'S-Oil', 'HMM', '롯데케미칼', 'LG화학', 'GS건설', 'CJ제일제당', '농심' 등)을 직접 언급하지 말 것. 대신 '주요 정유사', '국내 해운사', '석유화학 업체', '건설사', '식품업체' 등 업종 일반명으로 대체하라. ETF명(예: '건설ETF')도 마찬가지로 '건설 관련 지수' 등으로 대체.\n"
        "⚠ 비축유 주의: 한국 정부 비축유 약 200일 분량 보유. "
        "수 주 이내 단기 봉쇄 시나리오에서 '비축유 부족'은 취약점이 아님. "
        "비축유 소진 리스크는 장기(90일+) 봉쇄 시나리오에서만 언급 가능.\n"
        "⚠ 지표 해석 주의: [시장 지표]의 초크포인트 통과선박 수([글로벌 통계])는 "
        "글로벌 통과량이며 한국 향 선박 수가 아님. 이 수치로 한국 직접 영향을 단정 짓거나 "
        "봉쇄·회피 원인을 임의로 서술하지 말 것.\n"
        "- watchpoints: 2-3개"
    ),
    4: (
        "Tier 4(위기): 완전한 깊이로 작성.\n"
        "⚠ 전주 Tier 확인: [전주 시나리오]의 전주 Tier가 현재보다 낮으면(예: T2→T4), "
        "'이번 주에도' '지속' 등 연속 표현 금지. 신규 발생으로 서술.\n"
        "⚠ Part A status 연속성 규칙: [전주 시나리오]의 활성 경로 목록과 반드시 대조.\n"
        "  · 활성: 전주 목록에 이미 있던 품목. 새 이벤트/에스컬레이션이 발생해도 전주에 있었으면 반드시 활성.\n"
        "  · 신규활성: 전주 목록에 없던 품목이 이번 주 처음 등장할 때만 사용.\n"
        "  · 비활성: 전주에 있었으나 이번 주 기사 근거가 없어진 경로.\n"
        "⚠ 기사 범위 원칙: 이번 주 [주요 기사 목록]에 실제로 등장한 사건/초크포인트 범위 안에서만 서술하라. "
        "기사에 없는 초크포인트나 사건을 임의로 포함하지 마라.\n"
        "⚠ Part A 경로 분리: KG 초크포인트(호르무즈·파나마 등)와 비KG 뉴스 이벤트(러시아 수출금지, 인도 쌀 수출금지 등)를 "
        "같은 path 안에 '→'로 이어 연결하지 말 것. "
        "잘못된 예: '호르무즈 해협 → 러시아 수출금지 → 유가 급등'. "
        "KG 초크포인트 원인과 뉴스 이벤트 원인은 반드시 별개의 route 항목으로 분리할 것.\n"
        "⚠ Part A KG 경로 준수: 각 commodity의 path에는 해당 commodity의 kg_basis에 명시된 초크포인트만 포함하라. KG에 연결되지 않은 초크포인트를 path에 임의로 추가하지 말 것. 잘못된 예: CF_EuroContainer(수에즈·바브엘만데브·말라카 경유)의 path에 파나마 운하를 대체항로로 서술. 파나마 교란이 다른 commodity에 간접 영향을 미치더라도 CF_EuroContainer의 path에 직접 포함해서는 안 됨.\n"
        "⚠ 기사 신선도 원칙: [위기 사건 현황]의 주요 위기 사건 '이번 주 신규'가 0건이면, "
        "해당 위기 사건의 과거 기사를 다시 열거하거나 현재형으로 재서술하지 말 것. "
        "대신 '○○ 위기가 지속되고 있으나 이번 주 추가 보도 없음' 수준으로 1문장만 언급하라.\n"
        "- situation_summary: 3-4문단. "
        "위의 [주요 기사 목록]에 있는 날짜(MM월 DD일)를 반드시 활용하여 서술하고, 핵심 사건·수치·정책 변화에 [N] 기사 인용을 달 것.\n"
        "단락 구성 원칙: 동일 초크포인트·사건에 관한 내용은 한 단락에 집약하라. "
        "서로 다른 초크포인트(예: 대만해협 vs 파나마 운하)는 별개 단락으로 분리하라. "
        "단락 내에서는 날짜(MM월 DD일)를 활용해 시간순을 유지하되, 단락 간 교차 서술은 금지한다. "
        "예시: [단락1] '8월 8일 ...부터 8월 22일 ...까지' (대만해협). "
        "[단락2] '8월 14일 ...부터 9월 3일 ...까지' (파나마 운하).\n"
        "⚠ situation_summary 주의: 서로 인과적으로 연결할 수 없는 이질적 사건들을 "
        "'동시에' '중첩되면서' 등으로 억지 연결하지 마라. 각 사건이 독립적이라면 별개로 서술하라.\n"
        "- part_a: 활성/신규활성/비활성 경로 전체\n"
        "- part_b: 모든 주요 산업간 전파 (2-3개)\n"
        "- part_d: 전체 산업 6-7개 (산업별 영향 방향, 초기/중기/장기 심각도, 전파경로, 전주 대비 변화를 상세 서술)\n"
        "  ⚠ 식량/식품(KS_FoodAgri) 서술 시: 에너지 비용 상승이 식품 생산단계(냉장물류, 수산가공, 양식장 운영비)에 미치는 영향을 반드시 포함하라. 수산물 수출입 기업은 벙커유·운임 상승의 직접 영향권이므로, 해운/물류 비용 증가가 수산업 경쟁력에 미치는 경로도 pathway에 반영할 것.\n"
        "- part_e: vulnerabilities 2-3개 (반드시 이번 주 [주요 기사 목록]에 등장한 사건과 직접 연관된 것만. "
        "현재 주 신호에 없는 초크포인트는 취약점에 포함하지 말 것), "
        "monitoring_recommendations 3-5개. [시장 지표]의 BDI·운임·유가 등 공급망 수치를 반드시 근거로 활용하라\n"
        "  ⚠ 수산업 취약점: 해운 할증료·벙커유 급등 시 수산물 수출입 기업의 물류비 부담 증가를 vulnerabilities 또는 monitoring_recommendations에서 다룰 것 (KG: KC_Dongwon, KC_Sajo 등 수산기업이 KS_FoodAgri 소속).\n"
        "⚠ 주가 직접 인용 금지: situation_summary·changes_from_prev 등 서술 텍스트에서 "
        "개별 기업 주가·매출 수치(예: '에너지A 주가 -5.2%', '물류B 매출 감소' 등 구체 수치)를 직접 인용하지 말 것. "
        "주가는 [시장 지표] 패널에 별도 표시되므로 서술에서는 '주가 압박', '시장 심리 악화' 등 정성적 표현으로 대체할 것.\n"
        "⚠ 비축유 주의: 한국 정부 비축유 약 200일 분량 보유. "
        "⚠ 개별 기업명 사용 금지: situation_summary, part_d(pathway), part_e 등 모든 서술에서 개별 기업명(예: 'S-Oil', 'HMM', '롯데케미칼', 'LG화학', 'GS건설', 'CJ제일제당', '농심' 등)을 직접 언급하지 말 것. 대신 '주요 정유사', '국내 해운사', '석유화학 업체', '건설사', '식품업체' 등 업종 일반명으로 대체하라. ETF명(예: '건설ETF')도 마찬가지로 '건설 관련 지수' 등으로 대체.\n"
        "수 주 이내 단기 봉쇄 시나리오에서 '비축유 부족'은 취약점이 아님. "
        "비축유 소진 리스크는 장기(90일+) 봉쇄 시나리오에서만 언급 가능.\n"
        "⚠ 지표 해석 주의: [시장 지표]의 초크포인트 통과선박 수([글로벌 통계])는 "
        "글로벌 통과량이며 한국 향 선박 수가 아님. 이 수치로 한국 직접 영향을 단정 짓거나 "
        "봉쇄·회피 원인을 임의로 서술하지 말 것.\n"
        "- watchpoints: 3-4개 (초기/중기/장기 각 1개 이상)"
    ),
}



def _build_indicator_meta(kg_raw):
    """지표 메타데이터(이름/단위/그룹/방향성) 정의 + KG korea_sector ETF 동적 추가.
    (scenario_generator_v11.ipynb Cell 3 'INDICATOR_META 정의' 기반)
    """
    INDICATOR_META = {
        'SCFI':       {'name':'SCFI',      'full':'상하이 컨테이너 운임지수','unit':'pt',       'group':'글로벌 해운',    'dir':'up_bad',  'freq':'weekly'},
        'BDI':        {'name':'BDI',       'full':'발틱건화물지수',          'unit':'pt',       'group':'글로벌 해운',    'dir':'up_bad',  'freq':'daily'},
        'Harpex':     {'name':'Harpex',    'full':'컨테이너 용선료 지수',    'unit':'pt',       'group':'글로벌 해운',    'dir':'up_bad',  'freq':'weekly'},
        'GSCSI':      {'name':'GSCSI',     'full':'글로벌 해상 컨테이너 물동량','unit':'M TEU','group':'글로벌 해운',    'dir':'neutral', 'freq':'monthly'},
        'RWI_ISL_CTI':{'name':'RWI/ISL CTI','full':'컨테이너 처리량 지수',  'unit':'pt',       'group':'글로벌 해운',    'dir':'neutral', 'freq':'monthly'},
        'GSCPI':      {'name':'GSCPI',     'full':'글로벌 공급망 압력지수',  'unit':'σ',        'group':'공급망 스트레스','dir':'up_bad',  'freq':'monthly'},
        'NAPMSDI':    {'name':'NAPMSDI',   'full':'ISM 납기지연지수',        'unit':'pt',       'group':'공급망 스트레스','dir':'up_bad',  'freq':'monthly'},
        'GPR':        {'name':'GPR',       'full':'지정학적 위험지수',       'unit':'pt',       'group':'공급망 스트레스','dir':'up_bad',  'freq':'monthly'},
        'CP_Hormuz':     {'name':'호르무즈',    'full':'호르무즈 선박통과 (주간)','unit':'척','group':'초크포인트','dir':'down_bad','freq':'weekly'},
        'CP_Suez':       {'name':'수에즈',      'full':'수에즈 선박통과 (주간)',  'unit':'척','group':'초크포인트','dir':'down_bad','freq':'weekly'},
        'CP_BabElMandeb':{'name':'바브엘만데브','full':'바브엘만데브 선박통과',    'unit':'척','group':'초크포인트','dir':'down_bad','freq':'weekly'},
        'CP_Malacca':    {'name':'말라카',      'full':'말라카 선박통과 (주간)',  'unit':'척','group':'초크포인트','dir':'down_bad','freq':'weekly'},
        'CP_Taiwan':     {'name':'대만해협',    'full':'대만해협 선박통과 (주간)','unit':'척','group':'초크포인트','dir':'down_bad','freq':'weekly'},
        'CP_Korea':      {'name':'한국해협',    'full':'한국해협 선박통과 (주간)','unit':'척','group':'초크포인트','dir':'down_bad','freq':'weekly'},
        'Brent':      {'name':'브렌트유',  'full':'브렌트유 선물',           'unit':'USD/bbl', 'group':'에너지','dir':'up_bad',  'freq':'daily'},
        'WTI':        {'name':'WTI',      'full':'WTI 원유 선물',           'unit':'USD/bbl', 'group':'에너지','dir':'up_bad',  'freq':'daily'},
        'NatGas':     {'name':'천연가스',  'full':'천연가스 선물',           'unit':'USD/MMBtu','group':'에너지','dir':'up_bad', 'freq':'daily'},
        'Gold':       {'name':'금',        'full':'금 현물 (GLD ETF)',        'unit':'USD',     'group':'거시경제','dir':'neutral','freq':'daily',
                      'sector_ids':[],'sector_keywords':['금','귀금속','안전자산']},
        'KOSPI':      {'name':'KOSPI',    'full':'KOSPI 지수',              'unit':'pt',      'group':'거시경제','dir':'down_bad','freq':'daily'},
        'KRWUSD':     {'name':'원/달러',   'full':'원달러 환율',             'unit':'KRW',     'group':'거시경제','dir':'up_bad',  'freq':'daily'},
        'USD_Index':  {'name':'달러지수',  'full':'달러 인덱스 (UUP ETF)',    'unit':'USD',     'group':'거시경제','dir':'up_bad',  'freq':'daily',
                      'sector_ids':['KS_Energy','KS_Material','KS_FoodAgri','KS_Manufacture'],
                      'sector_keywords':['달러','환율','원자재']},
        'KR_ExportVol':{'name':'수출량',  'full':'한국 수출량 지수',         'unit':'USD',      'group':'거시경제','dir':'down_bad','freq':'monthly'},
        'VIX':        {'name':'VIX',      'full':'공포지수',                 'unit':'pt',      'group':'거시경제','dir':'up_bad',  'freq':'daily'},
        'SK이노베이션':{'name':'SK이노',   'full':'SK이노베이션','unit':'KRW','group':'한국 정유/화학','dir':'neutral',
                      'sector_ids':['KS_Energy'],'sector_keywords':['정유','석유화학']},
        'S_Oil':      {'name':'S-Oil',    'full':'S-Oil',       'unit':'KRW','group':'한국 정유/화학','dir':'neutral',
                      'sector_ids':['KS_Energy'],'sector_keywords':['정유']},
        '롯데케미칼':  {'name':'롯데케미칼','full':'롯데케미칼',  'unit':'KRW','group':'한국 정유/화학','dir':'neutral',
                      'sector_ids':['KS_Material'],'sector_keywords':['석유화학','납사']},
        'LG화학':     {'name':'LG화학',   'full':'LG화학',      'unit':'KRW','group':'한국 정유/화학','dir':'neutral',
                      'sector_ids':['KS_Material'],'sector_keywords':['석유화학','LG']},
        '한화솔루션':  {'name':'한화솔루션','full':'한화솔루션',  'unit':'KRW','group':'한국 정유/화학','dir':'neutral',
                      'sector_ids':['KS_Material'],'sector_keywords':['석유화학','태양광']},
        'HMM':        {'name':'HMM',      'full':'HMM (컨테이너)','unit':'KRW','group':'한국 해운','dir':'neutral',
                      'sector_ids':['KS_Shipping'],'sector_keywords':['해운','컨테이너']},
        '팬오션':     {'name':'팬오션',   'full':'팬오션 (벌크)', 'unit':'KRW','group':'한국 해운','dir':'neutral',
                      'sector_ids':['KS_Shipping'],'sector_keywords':['해운','벌크']},
        '한국가스공사':{'name':'가스공사', 'full':'한국가스공사',  'unit':'KRW','group':'한국 에너지/식품','dir':'neutral',
                      'sector_ids':['KS_Energy'],'sector_keywords':['가스','LNG','천연가스']},
        '대한항공':   {'name':'대한항공',  'full':'대한항공',     'unit':'KRW','group':'한국 에너지/식품','dir':'neutral',
                      'sector_ids':['KS_Shipping'],'sector_keywords':['항공','운송']},
        'CJ제일제당': {'name':'CJ제일제당','full':'CJ제일제당',   'unit':'KRW','group':'한국 에너지/식품','dir':'neutral',
                      'sector_ids':['KS_FoodAgri'],'sector_keywords':['식품','음식료','밀가루','대두']},
        '농심':       {'name':'농심',      'full':'농심',         'unit':'KRW','group':'한국 에너지/식품','dir':'neutral',
                      'sector_ids':['KS_FoodAgri'],'sector_keywords':['식품','음식료','밀가루']},
    }

    _ALWAYS_REPORT = {'BDI','SCFI','Brent','WTI','KRWUSD','USD_Index','KOSPI','VIX','GPR'}
    for _k in _ALWAYS_REPORT:
        if _k in INDICATOR_META:
            INDICATOR_META[_k]['always_report'] = True

    for _nid, _nd in kg_raw['nodes'].items():
        if _nd.get('node_type') == 'korea_sector':
            _kws = _nd.get('sectorKeywords', [])
            for _etf_name, _ticker in _nd.get('etfTickers', {}).items():
                _is_kr = _ticker.endswith('.KS')
                INDICATOR_META[_etf_name] = {
                    'name': _etf_name, 'full': f'{_etf_name} ({_ticker})',
                    'unit': 'KRW' if _is_kr else 'USD',
                    'group': '한국 산업 ETF', 'dir': 'neutral',
                    'sector_ids': [_nid], 'sector_keywords': _kws,
                }
    return INDICATOR_META


def get_kg_context_brief(top_commodities, top_sectors, tier, G, nodes, max_nodes=40):
    """KG 컨텍스트 구성: 관련 노드 + 구조적 전파 엣지 텍스트."""
    ctx_nodes = set()
    for nid in top_commodities + top_sectors:
        if nid in G:
            ctx_nodes.add(nid)
            for nb_node in list(G.successors(nid)) + list(G.predecessors(nid)):
                if G.nodes[nb_node].get('node_type') != 'RiskEvent':
                    ctx_nodes.add(nb_node)
    for nid, d in nodes.items():
        if d.get('node_type') == 'chokepoint':
            ctx_nodes.add(nid)
    max_nodes_by_tier = {1: 0, 2: 15, 3: 30, 4: max_nodes}
    limit = max_nodes_by_tier.get(tier, max_nodes)
    if len(ctx_nodes) > limit:
        ctx_nodes = set(list(ctx_nodes)[:limit])
    lines = ['=== KG 노드 ===']
    for nid in sorted(ctx_nodes):
        if nid not in G: continue
        d = G.nodes[nid]
        ntype = d.get('node_type', '?')
        name  = d.get('name', nid)
        attrs = []
        _FIELD_LABELS = {
            'koreaImportDependency': '해외수입의존도(전체,CP무관)',
            'exposureRate': 'CP경유율(주요)',
            'hormuzTotalBypassPct': '우회가능율',
        }
        for k in ['koreaImportDependency', 'exposureRate', 'hormuzTotalBypassPct',
                   'lagMinDays', 'lagMaxDays']:
            if k in d:
                attrs.append(f"{_FIELD_LABELS.get(k, k)}={d[k]}")
        if 'cpExposure' in d:
            cp_str = ', '.join(f"{cp}:{v}%" for cp, v in d['cpExposure'].items())
            attrs.append(f"CP별경유율=({cp_str})")
        lines.append(f"[{ntype}] {name} ({', '.join(attrs)})" if attrs else f"[{ntype}] {name}")
    lines.append('\n=== 공급망 전파 경로 (KG 구조) ===')
    _STRUCTURAL_RELATIONS = {'restrictsFlowOf', 'feedsInto', 'dependsOn',
                              'affectsChokepoint', 'suppliesTo', 'importsFrom'}
    max_struct = {1: 0, 2: 10, 3: 20, 4: 30}
    cnt = 0
    for u, v, d in G.edges(data=True):
        rel = d.get('relation', '')
        if rel in _STRUCTURAL_RELATIONS and cnt < max_struct.get(tier, 30):
            un = G.nodes[u].get('name', u) if u in G else u
            vn = G.nodes[v].get('name', v) if v in G else v
            weight = d.get('weight', '')
            w_str = f" (w={weight})" if weight else ""
            lines.append(f"  {un} --[{rel}]--> {vn}{w_str}")
            cnt += 1
    return '\n'.join(lines)


def _build_title_url_lookup():
    """일일 모니터링 JSON → title→URL 룩업 (주간 [N] 인용용)."""
    lookup = {}
    mon_dir = BASE_DIR / 'monitoring'
    if mon_dir.is_dir():
        for dname in sorted(os.listdir(mon_dir)):
            jpath = mon_dir / dname / f'daily_report_llm_{dname}.json'
            if jpath.exists():
                try:
                    with open(jpath, encoding='utf-8') as f:
                        dj = json.load(f)
                    for _cat, _arts in dj.get('sources', {}).items():
                        if not isinstance(_arts, list):
                            continue
                        for _a in _arts:
                            _t = _a.get('title', '').strip()
                            _u = _a.get('url', '')
                            if _t and _u:
                                lookup[_t] = _u
                except Exception:
                    pass
    return lookup


ALERT_PRIORITY = {'Crisis': 4, 'Warning': 3, 'Caution': 2, 'Normal': 1}


def get_key_articles(df, ref_date, window_weeks, tier, max_articles, dominant_cluster,
                      extract_cluster_entities, title_url_lookup):
    """주요 기사 목록 추출 ([N] 인용용 ref_map 포함)."""
    win_end   = pd.Timestamp(ref_date)
    win_start = win_end - pd.Timedelta(weeks=window_weeks)
    sub = df[(df['date'] >= win_start) & (df['date'] < win_end)].copy()
    if len(sub) == 0:
        return '', {}

    if dominant_cluster:
        _has_rel = 'relevance' in sub.columns
        sub_f = sub[sub['relevance'] == 'HIGH'] if _has_rel else sub
        keep_idx = []
        for _idx, _row in sub_f.iterrows():
            _ents = extract_cluster_entities(_row)
            _alert = _row.get('alert_level_1st', 'Normal')
            _is_dom = any(cid == dominant_cluster for cid, _ in _ents)
            _age_days = (win_end - pd.Timestamp(_row['date'])).days
            if _is_dom and _alert in ('Crisis', 'Warning'):
                if _age_days <= 14:
                    keep_idx.append(_idx)
            elif not _is_dom and _alert == 'Crisis':
                _days = (win_end - pd.Timestamp(_row['date'])).days
                if _days <= 7:
                    keep_idx.append(_idx)
        sub = sub.loc[keep_idx].copy() if keep_idx else sub.head(0).copy()
        if len(sub) == 0:
            return '', {}

    sub['_priority'] = sub['alert_level_1st'].map(ALERT_PRIORITY).fillna(0)
    # V12: (1) 컷오프를 리포트가 다루는 1주로 맞춤 (2주면 도미넌트 14일 필터와
    #          경계가 겹쳐 _older 가 경계일 하루로 붕괴)
    #      (2) 선택 정렬을 최신순으로 — 오름차순이면 윈도우 앞쪽(가장 오래된)
    #          기사가 할당량을 채워 이번 주 기사가 0건이 되는 문제가 있었음
    #      표시 순서는 아래 top.sort_values('date') 로 종전대로 날짜 오름차순
    recent_cutoff = win_end - pd.Timedelta(weeks=1)
    _recent = sub[sub['date'] >= recent_cutoff].sort_values(
        ['_priority', 'date'], ascending=[False, False]).head(max_articles // 2)
    _older  = sub[sub['date'] <  recent_cutoff].sort_values(
        ['_priority', 'date'], ascending=[False, False]).head(max_articles - len(_recent))
    top = pd.concat([_older, _recent]).drop_duplicates().sort_values('date')
    lines = ['=== 주요 기사 목록 (situation_summary 인용 참고용) ===']
    lines.append(f'기간: {win_start.strftime("%Y-%m-%d")} ~ {win_end.strftime("%Y-%m-%d")}')
    lines.append('⚠ 각 기사에 [N] 번호가 부여되어 있습니다. situation_summary 서술 시 해당 기사를 근거로 쓸 때 [N] 형태로 인용하세요.')
    lines.append('⚠ 각 기사의 날짜는 \'보도일\'(기사가 보도된 날)이며 사건이 발생한 날이 아닙니다. 대괄호 안 첫 태그는 event_status 로, THREAT 는 위협·예고, DISRUPTION 은 실제 발생한 교란입니다.')
    lines.append('')
    ref_map = {}
    ref_num = 1
    for _, row in top.sort_values('date').iterrows():
        date_str  = pd.Timestamp(row['date']).strftime('%m월 %d일')
        level     = row.get('alert_level_1st', '?')
        title     = str(row.get('title', '')).strip()[:120]
        days_old  = (win_end - pd.Timestamp(row['date'])).days
        freshness = '(이번 주)' if days_old <= 7 else f'({days_old}일 전)'
        ev_st = row.get('event_status', '')
        d_type = row.get('disruption_type', '')
        trig  = row.get('trigger_location', '')
        v5_tag = f'[{ev_st}'
        if d_type and str(d_type) not in ('', 'nan', 'None', 'null'):
            v5_tag += f'/{d_type}'
        v5_tag += ']'
        trig_tag = f' @{trig}' if trig and str(trig) not in ('', 'nan', 'None', 'null') else ''
        if dominant_cluster:
            _row_ents = extract_cluster_entities(row)
            _is_dom = any(cid == dominant_cluster for cid, _ in _row_ents)
            _ctag = f'[dominant]' if _is_dom else '[기타]'
        else:
            _ctag = ''
        _url = title_url_lookup.get(title.strip(), '')
        ref_map[str(ref_num)] = {'title': title, 'url': _url}
        lines.append(f'[{ref_num}] {_ctag} {v5_tag} [{level}] 보도일 {date_str} {freshness}{trig_tag}  {title}')
        summary = str(row.get('event_summary', '')).strip()
        if summary and summary not in ('', 'nan', 'None'):
            lines.append(f'  → {summary[:120]}')
        ref_num += 1
    return '\n'.join(lines), ref_map


def summarize_prev_scenario(prev):
    if prev is None:
        return '(이전 주 시나리오 없음 - 첫 번째 주)'
    tier   = prev.get('tier', 1)
    label  = prev.get('tier_label', '정상')
    period = prev.get('period', '?')
    lines  = [f'[전주: {period}, Tier {tier} {label}]']
    routes = prev.get('part_a', {}).get('routes', [])
    if routes:
        lines.append('활성 경로:')
        for r in routes:
            lines.append(f"  - {r.get('commodity','?')} [{r.get('status','')}]")
    matrix = prev.get('part_d', {}).get('matrix', [])
    if matrix:
        lines.append('산업별 심각도 (초기/중기/장기):')
        for row in matrix:
            lines.append(f"  - {row.get('sector','?')}: {row.get('direction','-')} | "
                         f"{row.get('initial','-')}/{row.get('mid','-')}/{row.get('long','-')}")
    lines.append(f'전반: 심각도={prev.get("overall_severity","-")}, 위기수준={prev.get("alert_level","-")}')
    return '\n'.join(lines)


def build_tier1_scenario(period, week_label, signal, prev_scenario):
    prev_alert = None
    if prev_scenario:
        prev_alert = prev_scenario.get('alert_level')
    changes = []
    if prev_alert and prev_alert != 'Normal':
        changes.append({'item': '위기수준', 'change': '↓',
                        'from': prev_alert, 'to': 'Normal', 'detail': '신호 감소'})
    return {
        'period': period, 'week_label': week_label,
        'tier': 1, 'tier_label': '정상',
        'header': {
            'period': period,
            'crisis_level': 'Normal',
            'situation_summary': '주요 해상 초크포인트 이상 없음. 공급망 정상 운영 중.',
            'changes_from_prev': changes,
            'watchpoints': [{'horizon': '지속', 'point': '정기 모니터링 유지. 이상 신호 발생 시 즉시 Tier 재평가.'}]
        },
        'part_a': {'routes': []}, 'part_b': {'cascades': []},
        'part_d': {'matrix': []},
        'part_e': {'vulnerabilities': [],
                   'monitoring_recommendations': ['정기 모니터링 지속']},
        'overall_severity': '미약', 'overall_direction': '안정', 'alert_level': 'Normal',
        'signal': {
            'n_articles':         signal['n_articles'] if signal else 0,
            'warning_crisis_pct': signal['warning_crisis_pct'] if signal else 0,
            'crisis_pct':         signal['crisis_pct'] if signal else 0,
        }
    }


def fmt_val_plain(v, unit):
    """지표 값을 LLM 프롬프트용 텍스트로 포맷 (HTML 없음)"""
    if v is None:
        return 'N/A'
    try:
        fv = float(v)
        if fv != fv:
            return 'N/A'
        if unit in ('pt', 'USD/bbl', 'KRW/USD', 'USD'):
            return f'{fv:,.1f} {unit}'
        elif unit == 'KRW':
            return f'{fv:,.0f} {unit}'
        elif unit == '%':
            return f'{fv:.2f}%'
        else:
            return f'{fv:,.2f} {unit}'
    except Exception:
        return str(v)


def format_indicators_for_llm(snapshot, top_sectors, indicator_meta, G, threshold=5.0, confirmed_cluster_ids=None):
    """
    임계값(|chg|≥threshold%) 초과 지표 + top_sectors 관련 지표 → LLM 프롬프트용 텍스트.
    CP_* 초크포인트 지표는 confirmed_cluster_ids에 있는 클러스터만 이상 신호 포함
    (확정 클러스터 없는 초크포인트 지표 → 근거 없는 LLM 해석 방지).
    """
    if confirmed_cluster_ids is None:
        confirmed_cluster_ids = set()
    if not snapshot:
        return ''

    alert_items = []
    sector_items = {}
    seen_in_alert = set()

    for kid, val in snapshot.items():
        meta = indicator_meta.get(kid, {})
        chg  = val.get('chg_pct')
        v_str = fmt_val_plain(val.get('value'), val.get('unit', ''))
        chg_str = f'{chg:+.1f}%' if chg is not None else ''
        ddir = meta.get('dir', 'neutral')

        is_cp = (meta.get('group') == '초크포인트')
        cp_allowed = (not is_cp) or (kid in confirmed_cluster_ids)
        if chg is not None and abs(chg) >= threshold and cp_allowed:
            sign    = '▲' if chg > 0 else '▼'
            danger  = (chg > 0 and ddir == 'up_bad') or (chg < 0 and ddir == 'down_bad')
            warn    = ' ⚠' if danger else ''
            scope   = ' [글로벌 통계]' if is_cp else ''
            alert_items.append(f'  {val.get("name", kid)}: {v_str} ({chg_str} {sign}){warn}{scope}')
            seen_in_alert.add(kid)

        for sid in meta.get('sector_ids', []):
            if sid in top_sectors:
                entry = f'    {val.get("name", kid)}: {v_str}' + (f' ({chg_str})' if chg_str else '')
                sector_items.setdefault(sid, []).append((kid, entry))

    core_items = []
    for kid, val in snapshot.items():
        meta = indicator_meta.get(kid, {})
        if not meta.get('always_report'):
            continue
        chg  = val.get('chg_pct')
        v_str = fmt_val_plain(val.get('value'), val.get('unit', ''))
        chg_str = f'{chg:+.1f}%' if chg is not None else ''
        ddir = meta.get('dir', 'neutral')
        danger = chg is not None and ((chg > 0 and ddir == 'up_bad') or (chg < 0 and ddir == 'down_bad'))
        warn = ' ⚠' if danger else ''
        sign = (' ▲' if chg > 0 else ' ▼') if chg is not None else ''
        core_items.append(f'  {val.get("name", kid)}: {v_str}' + (f' ({chg_str}{sign}){warn}' if chg_str else ''))

    lines = ['=== 시장 지표 ===']
    if core_items:
        lines.append('[핵심 지표 현황]')
        lines.extend(core_items)
    if alert_items:
        lines.append(f'[이상 신호 — 주간 변화율 ≥ ±{threshold}%]')
        lines.extend(alert_items)

    if sector_items:
        if alert_items:
            lines.append('')
        lines.append('[전파경로 관련 섹터 지표]')
        for sid, items in sector_items.items():
            sname = G.nodes[sid].get('name', sid) if sid in G else sid
            lines.append(f'  {sname} ({sid}):')
            seen_name = set()
            for kid, entry in items:
                name_key = snapshot.get(kid, {}).get('name', kid)
                if name_key not in seen_name:
                    seen_name.add(name_key)
                    if kid in seen_in_alert:
                        lines.append(entry + ' ★이상신호')
                    else:
                        lines.append(entry)

    return '\n'.join(lines) if len(lines) > 1 else ''


_IND_CHG_THRESHOLD = 1.5  # % 변화 기본 임계값
_IND_CHG_THRESHOLD_OVERRIDE = {
    'KRWUSD': 0.3,
    'USD_Index': 0.5,
}
_INDIVIDUAL_STOCK_GROUPS = {'한국 정유/화학', '한국 해운', '한국 에너지/식품'}


def compute_ind_changes(snap, threshold=_IND_CHG_THRESHOLD):
    """|chg_pct| >= threshold인 지표만 추출 → from/to 실값 계산.
    개별기업 주식(_INDIVIDUAL_STOCK_GROUPS)은 해석 첨부 방지를 위해 제외."""
    result = {}
    for key, meta in snap.items():
        if meta.get('group') in _INDIVIDUAL_STOCK_GROUPS:
            continue
        chg = meta.get('chg_pct')
        val = meta.get('value')
        _thr = _IND_CHG_THRESHOLD_OVERRIDE.get(key, threshold)
        if chg is None or val is None or abs(chg) < _thr:
            continue
        unit = meta.get('unit', '')
        try:
            prev_val = meta.get('prev_value')
            if prev_val is None:
                prev_val = val / (1 + chg / 100)
        except ZeroDivisionError:
            continue

        def _fmt(v, u):
            # 소수 1자리로 줄이면 from/to 로부터 chg_pct 를 재현할 수 없어
            # "표기값으로 계산하면 다른 값이 나온다"는 지적이 반복되었다.
            # (2026-08-24 김태한 박사 WTI 82.2→86.1 = +4.7% vs 표기 +4.9%)
            # chg_pct 와 동일하게 소수 2자리로 맞춘다.
            if u in ('pt', 'USD/bbl', 'KRW/USD', 'USD'):
                return f'{v:,.2f}'
            elif u == 'KRW':
                return f'{v:,.0f}'
            elif u == '%':
                return f'{v:.2f}%'
            elif u == '척':
                return f'{v:,.0f}'
            else:
                return f'{v:,.2f}'

        result[key] = {
            'name':       meta.get('name', key),
            'unit':       unit,
            'from_val':   round(prev_val, 2),
            'to_val':     round(val, 2),
            'from_str':   _fmt(prev_val, unit),
            'to_str':     _fmt(val, unit),
            'chg_pct':    chg,
            'chg_dir':    meta.get('chg_dir', 'flat'),
            'change_sym': '↑' if chg > 0 else '↓',
        }
    return dict(sorted(result.items(), key=lambda x: abs(x[1]['chg_pct']), reverse=True))


def _build_ind_changes_section(ind_changes_dict):
    if not ind_changes_dict:
        return '[주요 지표 변화] 없음 (임계값 미달)'
    lines = [
        '[주요 지표 변화] (실제 데이터 — key/from/to 확정값. '
        'changes_from_prev key는 반드시 이 목록에서 선택)'
    ]
    for key, d in list(ind_changes_dict.items())[:15]:
        unit_str = f' {d["unit"]}' if d['unit'] else ''
        lines.append(
            f'  {key}: {d["name"]}  '
            f'{d["from_str"]} → {d["to_str"]}{unit_str}  '
            f'({d["chg_pct"]:+.1f}%)'
        )
    return '\n'.join(lines)


def get_daily_context(ref_date, monitor_dir=None):
    """이번 주 일별 모니터링 보고서에서 executive_summary + changes 추출."""
    monitor_dir = monitor_dir or str(BASE_DIR / 'monitoring')

    def _strip_daily_refs(text):
        return re.sub(r'\[\d+\]', '', text).strip() if text else text

    prev_sunday = ref_date - pd.Timedelta(days=1)
    week_start  = prev_sunday - pd.Timedelta(days=6)

    day_blocks = []
    for d in pd.date_range(week_start, prev_sunday, freq='D'):
        date_tag = d.strftime('%Y%m%d')
        fpath = os.path.join(monitor_dir, date_tag, f'daily_report_llm_{date_tag}.json')
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath, encoding='utf-8') as fh:
                rpt = json.load(fh)
        except Exception:
            continue

        llm = rpt.get('llm_result', {})
        summary = _strip_daily_refs(llm.get('executive_summary', '').strip())
        changes = llm.get('changes', {})
        new_items = [_strip_daily_refs(x.get('issue', x) if isinstance(x, dict) else str(x))
                     for x in changes.get('new', [])[:3]]
        esc_items = [_strip_daily_refs(x.get('issue', x) if isinstance(x, dict) else str(x))
                     for x in changes.get('escalated', [])[:3]]

        day_lines = [f'▶ {d.strftime("%m/%d")}({["월","화","수","목","금","토","일"][d.weekday()]})']
        if summary:
            first_sent = summary.split('.')[0].strip()
            if first_sent:
                day_lines.append(f'  요약: {first_sent}.')
        if new_items:
            day_lines.append(f'  신규: {", ".join(new_items)}')
        if esc_items:
            day_lines.append(f'  악화: {", ".join(esc_items)}')
        if len(day_lines) > 1:
            day_blocks.append('\n'.join(day_lines))

    if not day_blocks:
        return '(해당 주 일별 모니터링 데이터 없음 — 과거 기간)'
    return '\n'.join(day_blocks)


def get_indicator_snapshot(ref_date, indicator_df, indicator_meta, prev_indicators=None):
    """ref_date 기준 가장 가까운 주의 지표값 + 전주 대비 등락률 반환.
    prev_indicators가 주어지면 직전 주차 리포트 값 기준으로 chg_pct를 계산한다."""
    try:
        ref = pd.Timestamp(ref_date)
        avail = indicator_df.index[indicator_df.index <= ref]
        if len(avail) == 0:
            return {}
        cur_date = avail[-1]
        prev_avail = indicator_df.index[indicator_df.index < cur_date]
        prev_date  = prev_avail[-1] if len(prev_avail) > 0 else None

        _cum_lookup = {}
        _cum_path = MONITORING_DIR / 'indicators' / 'indicators_cumulative.csv'
        try:
            _cum_df = pd.read_csv(_cum_path)
            for _, _row in _cum_df.sort_values('collect_date').iterrows():
                _ind = _row['indicator']
                _sd  = str(_row.get('source_date', ''))
                _sf  = str(_row.get('frequency', ''))
                if _sd and _sd not in ('nan', 'None', ''):
                    _cum_lookup[_ind] = {'source_date': _sd[:10], 'frequency': _sf}
        except Exception:
            pass

        _df_dates = None
        try:
            _df_dates = pd.read_csv(DATES_CSV, index_col='week_date', parse_dates=True)
        except Exception:
            pass

        snapshot = {}
        for col, meta in indicator_meta.items():
            if col not in indicator_df.columns:
                continue
            cur_val = indicator_df.loc[cur_date, col]
            if pd.isna(cur_val):
                continue
            _cum_info = _cum_lookup.get(col, {})
            _freq = _cum_info.get('frequency', '') or meta.get('freq', 'weekly')
            _data_date = ''
            if _df_dates is not None and col in _df_dates.columns and cur_date in _df_dates.index:
                _dd = _df_dates.loc[cur_date, col]
                if pd.notna(_dd):
                    _data_date = str(pd.Timestamp(_dd).date())
            if not _data_date:
                _data_date = _cum_info.get('source_date', '')
            entry = {
                'name':      meta['name'],
                'full':      meta['full'],
                'unit':      meta['unit'],
                'group':     meta['group'],
                'dir':       meta['dir'],
                'value':     round(float(cur_val), 2),
                'chg_pct':   None,
                'chg_dir':   'flat',
                'freq':      _freq,
                'data_date': _data_date,
            }
            _prev_val = None
            if prev_indicators and col in prev_indicators:
                _pv = prev_indicators[col].get('value')
                if _pv is not None:
                    _prev_val = float(_pv)
            elif prev_date is not None:
                _prev_val = indicator_df.loc[prev_date, col]
                if pd.isna(_prev_val):
                    _prev_val = None
            # ⚠ 변동률은 반드시 '표시되는 값'끼리 계산한다.
            #    이전에는 원시 cur_val 과 전주 스냅샷의 반올림 value(소수 2자리)를 섞어 계산해,
            #    실제로는 변동이 없는 지표가 변동한 것처럼 표기되었다.
            #    예) GSCPI 원시 0.8047 vs 전주 저장값 0.80 → +0.6% (실제 변동 0%)
            #    (2026-08-24 김한나 박사 지적 — 2주 연속 동일 오류)
            if _prev_val is not None and _prev_val != 0:
                _cur_disp  = round(float(cur_val), 2)
                _prev_disp = round(float(_prev_val), 2)
                if _prev_disp != 0:
                    chg = (_cur_disp - _prev_disp) / abs(_prev_disp) * 100
                    entry['chg_pct'] = round(chg, 1)
                    entry['chg_dir'] = 'up' if chg > 0.5 else ('down' if chg < -0.5 else 'flat')
                    entry['prev_value'] = _prev_disp
            snapshot[col] = entry
        return snapshot
    except Exception:
        return {}


def generate_weekly_scenario(period, week_label, tier, signal, prev_scenario, df, ref_date,
                              kg_data, cluster_ctx, indicator_df, indicator_meta):
    """LLM(Claude Sonnet) 호출로 주간 시나리오 생성. Tier 1도 LLM 경유(기사 요약 포함),
    실패 시 build_tier1_scenario() fallback."""
    G     = kg_data['G']
    nodes = kg_data['nodes']

    kg_ctx       = get_kg_context_brief(signal.get('top_commodities', []),
                                        signal.get('top_sectors', []), tier, G, nodes)
    prev_summary = summarize_prev_scenario(prev_scenario)

    ind_snap_prompt = get_indicator_snapshot(ref_date, indicator_df, indicator_meta) if ref_date is not None else {}
    top_sectors_set = set(signal.get('top_sectors', []))
    confirmed_cluster_ids_set = set(signal.get('confirmed_clusters', {}).keys())
    indicator_section = format_indicators_for_llm(ind_snap_prompt, top_sectors_set, indicator_meta, G,
                                                   confirmed_cluster_ids=confirmed_cluster_ids_set)
    ind_changes_dict    = compute_ind_changes(ind_snap_prompt)
    ind_changes_section = _build_ind_changes_section(ind_changes_dict)

    articles_section = ''
    _scenario_ref_map = {}
    if df is not None and ref_date is not None:
        max_arts = {1: 10, 2: 15, 3: 20, 4: 25}.get(tier, 15)
        title_url_lookup = _build_title_url_lookup()
        _arts_result = get_key_articles(df, ref_date, WINDOW_WEEKS, tier, max_arts,
                                          signal.get('dominant_cluster'),
                                          cluster_ctx['extract_cluster_entities'],
                                          title_url_lookup)
        if _arts_result:
            articles_section, _scenario_ref_map = _arts_result

    daily_context_section = get_daily_context(ref_date) if ref_date is not None else ''

    max_tokens_by_tier = {1: 4096, 2: 16384, 3: 16384, 4: 16384}

    confirmed_cls  = signal.get('confirmed_clusters', {})
    candidate_cls  = signal.get('candidate_clusters', {})
    dominant_cid   = signal.get('dominant_cluster')
    dominant_disp  = signal.get('dominant_display')
    dom_crisis_pct = signal.get('dominant_crisis_pct', 0.0)
    dom_wc_pct     = signal.get('dominant_wc_pct', 0.0)

    _seed_ids = set(cluster_ctx['CANONICAL_ENTITY_NAMES'].keys())
    _confirmed_seed     = {k: v for k, v in confirmed_cls.items()
                           if k in _seed_ids and k != dominant_cid}
    _confirmed_non_seed = {k: v for k, v in confirmed_cls.items()
                           if k not in _seed_ids and k != dominant_cid}

    if confirmed_cls or candidate_cls:
        _cls_lines = ['[위기 사건 현황]']
        if dominant_cid:
            _dom_new = signal.get('n_new_dominant', 0)
            _dom_new_str = f'이번 주 신규 {_dom_new}건' if _dom_new > 0 else '이번 주 신규 0건 — 이전 위기 지속'
            _cls_lines.append(
                f'⚑ 주요 위기 사건: {dominant_disp} '
                f'(crisis={dom_crisis_pct:.0f}%, wc={dom_wc_pct:.0f}%, {_dom_new_str})')
            _cls_lines.append(
                '  → situation_summary·취약점·Part A 경로는 이 위기 사건 중심으로 서술할 것')
        if _confirmed_seed:
            _cls_lines.append(
                f'동시 활성 초크포인트(KG 등록): {", ".join(_confirmed_seed.values())}')
        if _confirmed_non_seed:
            _cls_lines.append(
                f'[동시 발생 외부 이벤트 — KG 외 위기 사건]: '
                f'{", ".join(_confirmed_non_seed.values())}')
            _cls_lines.append(
                '  ※ 위 이벤트는 주요 초크포인트와 인과적으로 연결하지 말 것. '
                '병렬 발생 사실을 1문장 이내로만 언급. Part A 경로에 포함 금지.')
        if candidate_cls:
            _cls_lines.append(
                f'잠재 위기 사건(모니터링 중): {", ".join(candidate_cls.values())}')
        cluster_section = '\n'.join(_cls_lines)
    else:
        cluster_section = '[위기 사건 현황] 감지된 위기 사건 없음 — 신호 분산 또는 초기 단계'

    _es = signal.get('event_status_dist', {})
    _es_str = ', '.join(f'{k}:{v}건' for k, v in sorted(_es.items(), key=lambda x: -x[1])) or '(없음)'
    _dt = signal.get('disruption_type_dist', {})
    _dt_str = ', '.join(f'{k}:{v}건' for k, v in sorted(_dt.items(), key=lambda x: -x[1])) or '(없음)'
    _trig = signal.get('top_triggers', [])
    _trig_str = ', '.join(_trig[:5]) or '(없음)'

    prompt = PROMPT_TEMPLATE.format(
        period            = period,
        tier              = tier,
        tier_label        = TIER_LABELS[tier],
        crisis_pct        = signal['crisis_pct'],
        warning_pct       = signal['warning_pct'],
        wc_pct            = signal['warning_crisis_pct'],
        n                 = signal['n_articles'],
        trend             = signal.get('trend', 'stable'),
        window_start      = signal.get('window_start', ''),
        window_end        = signal.get('window_end', ''),
        commodities       = ', '.join(signal.get('top_commodities', ['(없음)'])[:4]),
        sectors           = ', '.join(signal.get('top_sectors', ['(없음)'])[:4]),
        cluster_section   = cluster_section,
        prev_summary      = prev_summary,
        articles_section  = articles_section,
        indicator_section = indicator_section,
        ind_changes_section = ind_changes_section,
        kg_ctx            = kg_ctx,
        tier_guidance     = TIER_GUIDANCE[tier],
        n_new_articles_this_week = signal.get('n_new_articles_this_week', 0),
        event_status_summary    = _es_str,
        disruption_type_summary = _dt_str,
        top_triggers_summary    = _trig_str,
        daily_context_section   = daily_context_section,
    )

    # 시나리오 생성은 Sonnet 사용 (call_llm_json은 Haiku 고정 — Part 4 분류용과 분리)
    result = call_llm_sonnet(prompt, system=SCENARIO_SYSTEM, max_tokens=max_tokens_by_tier[tier])

    if result is None:
        if tier == 1:
            return build_tier1_scenario(period, week_label, signal, prev_scenario)
        return {'period': period, 'week_label': week_label,
                'tier': tier, 'tier_label': TIER_LABELS[tier],
                'error': 'LLM 호출 실패', 'signal': signal}

    if 'header' in result:
        _raw = result['header'].get('changes_from_prev', [])
        _filled = []
        for _c in _raw:
            _key = _c.get('key', '').strip()
            _detail = _c.get('detail', '')
            if _key in ind_changes_dict:
                _d = ind_changes_dict[_key]
                _u = f' {_d["unit"]}' if _d['unit'] else ''
                _actual_pct = f'{_d["chg_pct"]:+.1f}%'
                _detail_fixed = re.sub(r'[+-]\d+\.\d+%', _actual_pct, _detail)
                _filled.append({
                    'item':   _d['name'],
                    'change': _d['change_sym'],
                    'from':   f"{_d['from_str']}{_u}",
                    'to':     f"{_d['to_str']}{_u}",
                    'detail': _detail_fixed,
                })
            elif _key:
                _filled.append({'item': _key, 'change': '−',
                                'from': '', 'to': '', 'detail': _detail})
        if _filled:
            result['header']['changes_from_prev'] = _filled

    result['ref_map'] = _scenario_ref_map
    result['period']     = period
    result['week_label'] = week_label
    result['tier']       = tier
    result['tier_label'] = TIER_LABELS[tier]
    result['signal'] = {
        'n_articles':         signal['n_articles'],
        'crisis_pct':         signal['crisis_pct'],
        'warning_pct':        signal['warning_pct'],
        'warning_crisis_pct': signal['warning_crisis_pct'],
        'trend':              signal.get('trend', 'stable'),
        'dominant_cluster':   signal.get('dominant_cluster'),
        'dominant_display':   signal.get('dominant_display'),
        'dominant_crisis_pct':signal.get('dominant_crisis_pct', 0.0),
        'confirmed_clusters': list(signal.get('confirmed_clusters', {}).keys()),
        'candidate_clusters': list(signal.get('candidate_clusters', {}).keys()),
    }
    return result


def _has_fresher_indicators(old_scenario, new_ind_snap):
    """두 snapshot의 data_date 최대값을 지표별로 비교.
    new_ind_snap에 한 지표라도 더 최신 data_date가 있으면 True."""
    def _dates_map(snap):
        out = {}
        if not isinstance(snap, dict):
            return out
        for k, v in snap.items():
            if isinstance(v, dict):
                if 'data_date' in v:
                    dd = v.get('data_date', '')
                    if dd:
                        try:
                            out[k] = pd.Timestamp(dd)
                        except Exception:
                            pass
                else:
                    for k2, v2 in v.items():
                        if isinstance(v2, dict) and 'data_date' in v2:
                            dd = v2.get('data_date', '')
                            if dd:
                                try:
                                    out[k2] = pd.Timestamp(dd)
                                except Exception:
                                    pass
        return out

    old_map = _dates_map(old_scenario.get('indicators', {}))
    new_map = _dates_map(new_ind_snap)
    if not new_map:
        return False
    if not old_map:
        return True
    for col, new_d in new_map.items():
        old_d = old_map.get(col)
        if old_d is None or new_d > old_d:
            return True
    return False


def _has_fresher_articles(old_scenario, ref_date, week_tag):
    """기존 시나리오 생성 후 새 phaseA 기사가 추가되었는지 확인.
    old_scenario._phaseA_week_tag가 현재 week_tag와 다르고, 새 phaseA 날짜가
    해당 주 윈도우에 포함되면 True."""
    old_tag = old_scenario.get('_phaseA_week_tag', '')
    if not old_tag:
        print(f"  ⚠ _phaseA_week_tag 없음 → 기사 변경 여부 확인 불가 → 재생성")
        return True
    if old_tag == week_tag:
        return False
    try:
        new_tag_date = pd.Timestamp(week_tag)
        win_start = pd.Timestamp(ref_date) - pd.Timedelta(weeks=4)
        if new_tag_date >= win_start:
            print(f"  ⚠ phaseA 갱신됨 ({old_tag}→{week_tag}) → 기사 재생성")
            return True
    except Exception:
        return True
    return False


def step7_generate_scenario(phaseA_df, indicator_weekly_df, tier_info, kg_data, week_tag):
    """LLM(Claude Sonnet) 기반 주간 시나리오 생성 + scenario_results.json 갱신 저장.

    tier_info: step6_signal_aggregation()의 반환값 (ref_date/tier/signal/phase_a/cluster_ctx 등)

    Returns:
        list: scenario_results.json에 누적 저장된 전체 시나리오 리스트 (최신순 정렬 X, week 오름차순)
    """
    print(f"\n{'='*60}")
    print("  STEP 7: LLM 시나리오 생성 (Claude Sonnet)")
    print(f"{'='*60}")

    INDICATOR_META = _build_indicator_meta(kg_data['kg_raw'])
    cluster_ctx = tier_info['cluster_ctx']
    phase_a     = tier_info['phase_a']
    ref_date    = tier_info['ref_date']
    week_label  = tier_info['week_label']
    period      = tier_info['period']
    tier        = tier_info['tier']
    sig         = tier_info['signal']

    RESULT_FILE = BASE_DIR / 'scenario_results.json'
    existing = {}
    if RESULT_FILE.exists():
        with open(RESULT_FILE, encoding='utf-8') as f:
            for s in json.load(f):
                existing[s.get('week', s.get('week_label', s.get('period', '?')))] = s

    prev_sunday   = ref_date - pd.Timedelta(days=8)   # 직전 주 일요일
    prev_week_key = prev_sunday.strftime('%G-W%V')
    prev_scenario = existing.get(prev_week_key)
    if prev_scenario:
        print(f"  prev_scenario 로드: {prev_week_key}")
    else:
        print(f"  prev_scenario 없음: {prev_week_key} (최초 주 또는 미실행)")

    _prev_ind = prev_scenario.get('indicators') if prev_scenario else None
    ind_snap = get_indicator_snapshot(ref_date, indicator_weekly_df, INDICATOR_META, prev_indicators=_prev_ind)

    if week_label in existing and \
            not _has_fresher_indicators(existing[week_label], ind_snap) and \
            not _has_fresher_articles(existing[week_label], ref_date, week_tag):
        print(f"  {week_label} → 지표·기사 변경 없음 → 기존 유지 (LLM 스킵)")
        result = existing[week_label]
        result['indicators'] = ind_snap
    else:
        t0 = time.time()
        result = generate_weekly_scenario(
            period, week_label, tier, sig, prev_scenario,
            df=phase_a, ref_date=ref_date, kg_data=kg_data, cluster_ctx=cluster_ctx,
            indicator_df=indicator_weekly_df, indicator_meta=INDICATOR_META,
        )
        elapsed = time.time() - t0
        sev = result.get('header', {}).get('crisis_level', '?')
        al  = result.get('alert_level', '?')
        print(f"  {week_label} → {sev}/{al} ({elapsed:.1f}s)")

    result['week']       = week_label
    result['indicators'] = ind_snap
    result['_phaseA_week_tag'] = week_tag

    existing[week_label] = result
    merged = sorted(existing.values(), key=lambda x: x.get('week', ''))
    with open(RESULT_FILE, 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=2, default=str)
    print(f"  → 저장 완료: {RESULT_FILE} (누적 {len(merged)}주)")

    # 요약 CSV
    summary_rows = []
    for s in merged:
        header = s.get('header', {})
        part_a = s.get('part_a', {})
        part_d = s.get('part_d', {})
        summary_rows.append({
            'week': s.get('week_label', s.get('period', '?')),
            'tier': s.get('tier', '?'),
            'label': s.get('tier_label', '?'),
            'crisis_level': header.get('crisis_level', '?'),
            'alert_level': s.get('alert_level', '?'),
            'n_routes': len(part_a.get('routes', [])),
            'n_matrix': len(part_d.get('matrix', [])),
            'n_changes': len(header.get('changes_from_prev', [])),
            'skipped': s.get('skipped', False),
        })
    pd.DataFrame(summary_rows).to_csv(BASE_DIR / 'scenario_summary.csv', index=False, encoding='utf-8-sig')
    print(f"  → scenario_summary.csv 저장 ({len(summary_rows)}주)")

    return merged


# ══════════════════════════════════════════════════════════════
# STEP 8: HTML 리포트 생성 (docs/weekly_report.html)
# (scenario_generator_v11.ipynb Part 5 / Cell 11 기반)
# ══════════════════════════════════════════════════════════════

def step8_generate_html(scenario_json, week_tag, kg_data):
    """scenario_results.json(누적) → docs/weekly_report.html 생성.
    사이드바 탭(주차별), 시나리오 블록(헤더/지도/상황요약/전파경로/산업매트릭스/취약점),
    지표 패널, 일별 기사 인용 링크를 모두 포함.

    Returns:
        str: 생성된 HTML 파일 경로 (scenario_json이 비어있으면 None)
    """
    print(f"\n{'='*60}")
    print("  STEP 8: HTML 리포트 생성")
    print(f"{'='*60}")

    # ============================================================
    # Cell 11: HTML 뷰어 생성 (weekly_report.html) — v7: Part C 제거, 헤더 통일
    # ============================================================
    import json, os, re
    from pathlib import Path
    # ── indicator_sources.csv → verify_url 매핑 ──
    import csv as _csv
    _IND_URL_MAP = {}
    if os.path.exists('indicator_sources.csv'):
        with open('indicator_sources.csv', encoding='utf-8-sig') as _f:
            for _row in _csv.DictReader(_f):
                _ind_raw = _row.get('indicator', '')
                _kid = _ind_raw.split('(')[0].strip().replace(' ', '_').replace('/', '_')
                # CP_ 계열은 원래 키 유지: 'CP_Hormuz (호르무즈해협)' → 'CP_Hormuz'
                if _ind_raw.startswith('CP_'):
                    _kid = _ind_raw.split(' ')[0].strip()
                _url = _row.get('verify_url', '').strip()
                if _kid and _url:
                    _IND_URL_MAP[_kid] = _url
        print(f'지표 URL 매핑: {len(_IND_URL_MAP)}개 로드')
    else:
        print('⚠ indicator_sources.csv 없음 — 지표 링크 없이 진행')
    # ── 일일 보고서 기사 로딩 (주간 기사 인용 링크용) ──
    from datetime import datetime as _dt, timedelta as _td
    _DAILY_SOURCES = {}   # { 'YYYYMMDD': { cat: [articles] } }
    _MON_DIR = 'monitoring'
    if os.path.isdir(_MON_DIR):
        for _dname in sorted(os.listdir(_MON_DIR)):
            _jpath = os.path.join(_MON_DIR, _dname, f'daily_report_llm_{_dname}.json')
            if os.path.exists(_jpath):
                try:
                    with open(_jpath, encoding='utf-8') as _f:
                        _dj = json.load(_f)
                    _dsrc = _dj.get('sources', {})
                    if _dsrc:
                        _DAILY_SOURCES[_dname] = _dsrc
                except Exception:
                    pass
        print(f'일일 기사 로드: {len(_DAILY_SOURCES)}일분')
    else:
        print('⚠ monitoring/ 디렉토리 없음 — 주간 기사 링크 생략')
    # ── 입력: scenario_json 파라미터 사용 (원본은 scenario_results.json 직접 로드했으나
    #          step7_generate_scenario()가 반환한 merged 리스트를 그대로 받는다) ──
    OUTPUT_DOCS_DIR = BASE_DIR / "docs"
    os.makedirs(OUTPUT_DOCS_DIR, exist_ok=True)
    OUTPUT_HTML = str(OUTPUT_DOCS_DIR / "weekly_report.html")

    _all = {}
    for s in scenario_json:
        _key = s.get('week', s.get('week_label', s.get('period', '?')))
        _all[_key] = s
    print(f"입력: scenario_json {len(scenario_json)}건")

    if not _all:
        print("⚠ scenario_json 비어있음 — step7_generate_scenario 먼저 실행하세요.")
    else:
        scenarios = sorted(_all.values(), key=lambda x: x.get('week', x.get('week_label', x.get('period', ''))))
        print(f"→ 합산: {len(scenarios)}주 ({scenarios[0].get('week','?')} ~ {scenarios[-1].get('week','?')})")
        SEV_COLOR = {
            '심각': '#c0392b', '중요': '#e67e22',
            '보통': '#2980b9', '미약': '#7f8c8d', '?': '#bdc3c7', '-': '#bdc3c7'
        }
        DIR_ICON = {'네거티브': '▼', '포지티브': '▲', '혼합': '◆', '안정': '●', '?': '?'}
        TIER_BG  = {1: '#27ae60', 2: '#2980b9', 3: '#e67e22', 4: '#c0392b'}
        CHG_SYM  = {'↑': '🔺', '↓': '🔻', '☆': '⭐', '−': '—',
                    '↑↑': '🔺🔺', '↓↓': '🔻🔻'}
        GROUP_ORDER = [
            '글로벌 해운', '초크포인트', '공급망 스트레스', '에너지',
            '거시경제', '한국 해운', '한국 에너지', '한국 철강/소재',
            '한국 자동차', '한국 화학', '한국 반도체', '한국 에너지/식품',
            '한국 정유/화학', '한국 산업 ETF'
        ]
        def sev_badge(s):
            c = SEV_COLOR.get(s, '#bdc3c7')
            return f'<span style="background:{c};color:#fff;padding:2px 7px;border-radius:4px;font-size:.85em">{s}</span>'
        def chg_sym(s):
            return CHG_SYM.get(s, s or '—')
        def esc(s):
            return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        def clean_kg(text):
            """KG 내부 표기 → 사람이 읽기 좋은 형태로 변환"""
            if not text: return ''
            t = str(text)
            KG_NAME_MAP = {
                'CF_CrudeOil':'원유', 'CF_LNG':'LNG', 'CF_Naphtha':'나프타',
                'CF_Corn':'옥수수', 'CF_Wheat':'밀', 'CF_Coal':'석탄',
                'CF_EuroContainer':'유럽 컨테이너', 'CF_RareEarth':'희토류',
                'CF_Meat':'육류', 'CF_Petrochemicals':'석유화학',
                'KS_Energy':'에너지', 'KS_Material':'소재/화학',
                'KS_Manufacture':'제조업', 'KS_Shipping':'해운/물류',
                'KS_FoodAgri':'식품/농산물', 'KS_Construction':'건설/인프라',
                'KS_Finance':'금융', 'KS_Macro':'거시경제', 'KS_Consumer':'소비자',
                'CP_Hormuz':'호르무즈 해협', 'CP_Suez':'수에즈 운하',
                'CP_BabElMandeb':'바브엘만데브 해협', 'CP_Malacca':'말라카 해협',
                'CP_Panama':'파나마 운하', 'CP_Taiwan':'대만 해협',
                'CP_BlackSea':'흑해', 'CP_Korea':'한국 항구',
                'CP_Lombok':'롬복 해협', 'CP_Kaohsiung':'가오슝',
                'CP_RussiaFuelExport':'러시아 에너지 수출',
            }
            for kg_id, kor in KG_NAME_MAP.items():
                t = t.replace(kg_id, kor)
            # 한국어로 번역된 KG ID가 괄호 안에 남아 있으면 제거
            for _kor in KG_NAME_MAP.values():
                t = t.replace(f'({_kor})', '')
            t = re.sub(r'--\[.*?\]-->', '→', t)
            t = re.sub(r'^KG:\s*', '', t)
            t = re.sub(r'\bDOMESTIC_IMPACT\s*기사', '국내 기사', t)
            t = re.sub(r'\b(THREAT|DISRUPTION)[/_A-Z]*\s*기사', '기사', t)
            t = re.sub(r'\bDOMESTIC_IMPACT\b', '', t)
            t = re.sub(r'\bTHREAT/SOURCE\b', '', t)
            t = re.sub(r'\bDISRUPTION/LOGISTICS\b', '', t)
            t = re.sub(r'\b(SOURCE|ROUTE|CP|CHOKEPOINT|LOGISTICS)형?\s*교란\s*[:：]?', '', t)
            t = re.sub(r'\bSOURCE/ROUTE\s*교란\s*[:：]?', '', t)
            t = re.sub(r'기사\s*\([^)]*(?:THREAT|DISRUPTION|ROUTE|SOURCE|LOGISTICS)[^)]*\)', '기사', t)
            t = re.sub(r'\([^)]*\b(?:THREAT|DISRUPTION|SOURCE|ROUTE|LOGISTICS|CP|CHOKEPOINT)\b[^)]*\)', '', t)
            t = re.sub(r'^(?:SOURCE|ROUTE|THREAT|DISRUPTION|LOGISTICS)[/_\s]*', '', t)
            t = re.sub(r'@[A-Za-z_\s]+(?=;|$|\b)', '', t)
            t = re.sub(r'\(\s*\)', '', t)
            t = re.sub(r'\s*cascade\b[^;,]*', '', t)
            t = t.replace('CP별경유율', '초크포인트별 경유율')
            t = re.sub(r'\bKG\s+(?:등록|비구조|등록\s*초크포인트)[^;,\.]*', '', t)
            t = re.sub(r'\blagMin(?:Days)?=\d+[~\-]?\d*', '', t)
            t = re.sub(r'\blagMax(?:Days)?=\d+[~\-]?\d*', '', t)
            t = re.sub(r'\bev=[\d.]+', '', t)
            t = re.sub(r'\bKG\s*노드\s*:[^;\n]+', '', t)
            t = re.sub(r'\bKG\s*초크포인트[^;\n]*', '', t)
            t = re.sub(r'\bKG\s*엣지[^;\n]*', '', t)
            t = re.sub(r'\bCF_([A-Za-z가-힣0-9]+)', r'\1', t)
            t = re.sub(r'\bKS_([A-Za-z가-힣0-9/]+)', r'\1', t)
            t = re.sub(r'\bCP_([A-Za-z가-힣0-9]+)', r'\1', t)
            t = re.sub(r'[\s;]*\(?w=[\d.]+\)?', '', t)
            t = re.sub(r'[\s;]*\(?crisis=\d+%\)?', '', t)
            t = t.replace('CP경유율', '초크포인트 경유율')
            t = re.sub(r';\s*;', ';', t)
            t = re.sub(r',\s*,+', ',', t)
            t = re.sub(r'^[;\s]+|[;\s]+$', '', t)
            t = re.sub(r',\s*$', '', t)
            # 독립 영문 초크포인트명 → 한국어
            for _e, _k in [('Hormuz','호르무즈 해협'),('Malacca','말라카 해협'),('BabElMandeb','바브엘만데브 해협'),('Suez','수에즈 운하'),('Panama','파나마 운하')]:
                t = re.sub(r'\b' + _e + r'\b', _k, t)
            # 영문 Tier 레벨 → 한국어 (상황요약 본문에서 노출 방지)
            for _e, _k in [('Crisis','위기'),('Warning','경고'),('Caution','주의'),('Normal','정상')]:
                t = re.sub(r'\b' + _e + r'\b', _k, t)
            t = re.sub(r'\s+', ' ', t).strip()
            return esc(t)
        def _linkify_refs(text, ref_map):
            if not text or not ref_map:
                return text
            import re as _re_link
            def _repl(m):
                num = m.group(1)
                ref = ref_map.get(num, {})
                url = ref.get("url", "")
                title = ref.get("title", "").replace('"', '&quot;')[:120] if ref.get("title") else f"기사 {num}"
                if url:
                    return f'<a href="{url}" target="_blank" rel="noopener" class="ref-link" title="{title}"><sup>[{num}]</sup></a>'
                else:
                    return f'<span class="ref-link" title="{title}"><sup>[{num}]</sup></span>'
            return _re_link.sub(r'\[(\d+)\]', _repl, text)
        def clean_path(text):
            """전파경로 path/pathway 필드에서 내부 KG 표기 제거"""
            if not text: return ''
            t = str(text)
            KG_NAME_MAP = {
                'CF_CrudeOil':'원유', 'CF_LNG':'LNG', 'CF_Naphtha':'나프타',
                'CF_Corn':'옥수수', 'CF_Wheat':'밀', 'CF_Coal':'석탄',
                'CF_EuroContainer':'유럽 컨테이너', 'CF_RareEarth':'희토류',
                'CF_Meat':'육류', 'CF_Petrochemicals':'석유화학',
                'KS_Energy':'에너지', 'KS_Material':'소재/화학',
                'KS_Manufacture':'제조업', 'KS_Shipping':'해운/물류',
                'KS_FoodAgri':'식품/농산물', 'KS_Construction':'건설/인프라',
                'KS_Finance':'금융', 'KS_Macro':'거시경제', 'KS_Consumer':'소비자',
            }
            for kg_id, kor in KG_NAME_MAP.items():
                t = t.replace(kg_id, kor)
            t = re.sub(r'\(\d{2}\.\d{2}\s+SOURCE\s+교란\)', '', t)
            t = re.sub(r'\bSOURCE\s+교란', '', t)
            t = re.sub(r'\((?:SOURCE|ROUTE|THREAT|DISRUPTION|LOGISTICS|CHOKEPOINT|CP)\)', '', t)
            t = re.sub(r'\bCF_([A-Za-z가-힣0-9]+)', r'\1', t)
            t = re.sub(r'\bKS_([A-Za-z가-힣0-9/]+)', r'\1', t)
            t = re.sub(r'\bCP_([A-Za-z가-힣0-9]+)', r'\1', t)
            t = re.sub(r'[\s;]*\(?w=[\d.]+\)?', '', t)
            t = re.sub(r'[\s;]*\(?crisis=\d+%\)?', '', t)
            t = re.sub(r';\s*;', ';', t)
            t = re.sub(r'\s+', ' ', t).strip()
            return esc(t)
        def clean_node(text):
            """에너지(KS_Energy) → 에너지"""
            if not text: return ''
            return esc(re.sub(r'\([A-Za-z_][A-Za-z0-9_]*\)', '', str(text)).strip())
        def clean_sector(text):
            """Part B from/to 필드: KS_/CF_/CP_ 접두사와 괄호 처리 — 세 가지 패턴 모두 대응
            KS_Energy(에너지) → 에너지
            에너지(KS_Energy) → 에너지
            KS_Energy (bare) → KS_MAP 또는 접두사 제거
            """
            if not text: return ''
            t = str(text).strip()
            KS_MAP = {
                'KS_Energy': '에너지', 'KS_Material': '소재/화학',
                'KS_Manufacture': '제조업', 'KS_Shipping': '해운/물류',
                'KS_FoodAgri': '식품/농산물', 'KS_Construction': '건설/인프라',
                'KS_Finance': '금융', 'KS_Macro': '거시경제',
            }
            # Pattern 1: KOREAN(KG_CODE) → KOREAN  e.g. "에너지(KS_Energy)"
            m = re.match(r'^([^()]+)\([A-Za-z_][A-Za-z0-9_]*\)$', t)
            if m: return esc(m.group(1).strip())
            # Pattern 2: KG_CODE(KOREAN_DESC) → KOREAN_DESC  e.g. "KS_Energy(에너지 섹터)"
            m = re.match(r'^(?:KS_|CF_|CP_|KI_)[A-Za-z가-힣0-9]+\(([^)]+)\)$', t)
            if m: return esc(m.group(1).strip())
            # Pattern 3: Bare KG_CODE → KS_MAP 또는 접두사 제거
            if t in KS_MAP: return esc(KS_MAP[t])
            t = re.sub(r'\bKS_([A-Za-z가-힣0-9/]+)', r'\1', t)
            t = re.sub(r'\bCF_([A-Za-z가-힣0-9]+)', r'\1', t)
            t = re.sub(r'\bCP_([A-Za-z가-힣0-9]+)', r'\1', t)
            t = re.sub(r'\bKI_([A-Za-z가-힣0-9]+)', r'\1', t)
            return esc(t.strip())
        def fmt_val(v, unit):
            if v is None:
                return 'N/A'
            try:
                fv = float(v)
                if fv != fv:
                    return 'N/A'
                if unit in ('pt', 'USD/bbl', 'KRW/USD', 'USD'):
                    return f'{fv:,.1f}'
                elif unit == 'KRW':
                    return f'{fv:,.0f}'
                elif unit == '%':
                    return f'{fv:.2f}'
                elif unit == 'σ':
                    return f'{fv:.2f}'
                elif unit == '척':
                    return f'{int(fv):,}'
                else:
                    return f'{fv:,.2f}'
            except Exception:
                return str(v)
        def chg_color_for(cdir, ddir):
            """방향 반영 색상 반환"""
            if cdir == 'up':
                return '#c0392b' if ddir == 'up_bad' else ('#27ae60' if ddir == 'down_bad' else '#e67e22')
            elif cdir == 'down':
                return '#c0392b' if ddir == 'down_bad' else ('#27ae60' if ddir == 'up_bad' else '#2980b9')
            return '#7f8c8d'
        def chg_arrow(cdir):
            return {'up': '▲', 'down': '▼'}.get(cdir, '—')
        _CAT_KR = {
            '1_Security': '안보/지정학', '2_Energy': '에너지',
            '3_Freight': '해상운임', '4_PortCargo': '항만/물류',
            '5_EconFinance': '경제/금융', '6_Seafood': '수산',
            '7_Shipping': '해운', '8_Logistics': '물류',
            '9_PortCongestion': '항만혼잡', '10_OtherIndustry': '기타산업',
        }
        _WEEKDAY_KR = ['월','화','수','목','금','토','일']
        _MAX_PER_CAT = 10   # 카테고리당 최대 표시 기사 수
        def _get_week_sources(period_str):
            """주간 period 문자열에서 해당 주 일일 기사 수집"""
            m = re.search(r'(\d{4}-\d{2}-\d{2})', period_str)
            if not m:
                return {}
            mon_date = _dt.strptime(m.group(1), '%Y-%m-%d')
            agg = {}
            for i in range(7, 0, -1):
                d = (mon_date - _td(days=i)).strftime('%Y%m%d')
                if d in _DAILY_SOURCES:
                    agg[d] = _DAILY_SOURCES[d]
            return agg
        def _render_weekly_sources(agg):
            """주간 기사 접이식 HTML 생성"""
            if not agg:
                return ''
            total = sum(len(a) for ds in agg.values() for a in ds.values() if isinstance(a, list))
            html = f'<details class="weekly-sources"><summary class="ws-summary">📰 주간 참조 기사 ({total:,}건 · {len(agg)}일)</summary>'
            for dstr in sorted(agg.keys()):
                day_src = agg[dstr]
                day_n = sum(len(v) for v in day_src.values() if isinstance(v, list))
                try:
                    dd = _dt.strptime(dstr, '%Y%m%d')
                    dl = f"{dd.month}/{dd.day}({_WEEKDAY_KR[dd.weekday()]})"
                except Exception:
                    dl = dstr
                html += f'<details class="ws-day"><summary class="ws-day-sum">{dl} — {day_n:,}건</summary><div class="ws-day-body">'
                for ck in sorted(day_src.keys()):
                    arts = day_src[ck]
                    if not isinstance(arts, list) or not arts:
                        continue
                    cn = _CAT_KR.get(ck, ck)
                    intl = [a for a in arts if a.get('type') == 'intl']
                    dom  = [a for a in arts if a.get('type') == 'dom']
                    for tag, items in [('해외', intl), ('국내', dom)]:
                        if not items:
                            continue
                        html += f'<div class="ws-cat">{cn} · {tag} ({len(items)}건)</div><ul class="ws-arts">'
                        for a in items[:_MAX_PER_CAT]:
                            t = a.get('title','').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
                            u = a.get('url','')
                            html += f'<li><a href="{u}" target="_blank" rel="noopener">{t}</a></li>' if u else f'<li>{t}</li>'
                        if len(items) > _MAX_PER_CAT:
                            html += f'<li class="ws-more">… 외 {len(items)-_MAX_PER_CAT}건</li>'
                        html += '</ul>'
                html += '</div></details>'
            html += '</details>'
            return html
        # ── 지표 패널 (전체 그룹별) ──────────────────────────────
        def render_indicators(indicators):
            if not indicators:
                return ''
            groups = {}
            for kid, meta in indicators.items():
                g = meta.get('group', '기타')
                groups.setdefault(g, []).append((kid, meta))
            ordered = [g for g in GROUP_ORDER if g in groups]
            ordered += [g for g in groups if g not in ordered]
            html = ['<div class="ind-panel"><div class="ind-title">📈 주간 공급망 모니터링 지표 <span style="font-size:0.7em;color:#999;font-weight:normal">(지표명 클릭 시 최신 정보 확인)</span></div><div class="ind-groups">']
            for g in ordered:
                html.append(f'<div class="ind-group"><div class="ind-group-title">{esc(g)}</div><div class="ind-items">')
                for kid, meta in groups[g]:
                    name  = meta.get('name', kid)
                    full  = meta.get('full', name)
                    value = meta.get('value')
                    unit  = meta.get('unit', '')
                    chg   = meta.get('chg_pct')
                    cdir  = meta.get('chg_dir', 'flat')
                    ddir  = meta.get('dir', 'neutral')
                    cc    = chg_color_for(cdir, ddir)
                    arrow = chg_arrow(cdir)
                    val_str = fmt_val(value, unit)
                    chg_str = f'{chg:+.1f}%' if (chg is not None and chg == chg) else ''
                    unit_span = f'<span class="ind-unit">{esc(unit)}</span>' if unit else ''
                    # 날짜 라벨 생성
                    _freq = meta.get('freq', '')
                    _data_date = meta.get('data_date', '')
                    _date_label = ''
                    if _data_date and str(_data_date) not in ('', 'nan', 'None'):
                        try:
                            _parts = str(_data_date)[:10].split('-')  # YYYY-MM-DD
                            if _freq == 'monthly':
                                _date_label = f"{int(_parts[1])}월"
                            else:  # daily or weekly
                                _date_label = f"{int(_parts[1])}/{int(_parts[2])}"
                        except Exception:
                            pass
                    date_span = f'<span class="ind-date">{_date_label}</span>' if _date_label else ''
                    html.append(
                        f'<div class="ind-item" title="{esc(full)}">'
                        f'<div class="ind-name">{f"""<a href="{_IND_URL_MAP[kid]}" target="_blank" rel="noopener">{esc(name)}</a>""" if kid in _IND_URL_MAP else esc(name)}{date_span}</div>'
                        f'<div class="ind-val">{esc(val_str)}{unit_span}</div>'
                        f'<div class="ind-chg" style="color:{cc}">{arrow} {esc(chg_str)}</div>'
                        f'</div>'
                    )
                html.append('</div></div>')
            html.append('</div></div>')
            return '\n'.join(html)
        # ── 시나리오별 HTML 블록 ───────────────────────────────────
        # ── 지도 렌더링 함수 ──
        # 좌표 테이블 (지리 정보 — KG 노드 ID → [lat, lng])
        _GEO = {
            # 초크포인트
            'CP_Hormuz': [26.56,56.25], 'CP_Suez': [30.46,32.34],
            'CP_BabElMandeb': [12.58,43.33], 'CP_Malacca': [2.50,101.20],
            'CP_Panama': [9.08,-79.68], 'CP_Taiwan': [24.50,120.50],
            'CP_BlackSea': [41.20,29.10], 'CP_Lombok': [-8.40,115.70],
            # 해외 항구
            'FP_RasTanura': [26.64,50.15], 'FP_MinaAhmadi': [29.05,48.15],
            'FP_Fujairah': [25.12,56.33], 'FP_RasLaffan': [25.93,51.53],
            'FP_PortHedland': [-20.31,118.58], 'FP_Gladstone': [-23.85,151.26],
            'FP_Newcastle': [-32.93,151.78], 'FP_USGulf': [29.30,-94.80],
            'FP_Vancouver': [49.29,-123.11], 'FP_Bintulu': [3.17,113.04],
            'FP_Indonesian': [-6.10,106.88], 'FP_Kaohsiung': [22.61,120.27],
            'FP_Yokohama': [35.44,139.64], 'FP_Rotterdam': [51.91,4.48],
            'FP_Tubarao': [-20.28,-40.24], 'FP_Lianyungang': [34.74,119.45],
            'FP_Qingdao': [36.07,120.38], 'FP_Primorsk': [60.35,28.63],
            'FP_Novorossiysk': [44.72,37.79], 'FP_Vladivostok': [43.11,131.87],
            'FP_Sabetta': [71.28,72.05],
            # 한국 항구
            'KP_Ulsan': [35.50,129.38], 'KP_Yeosu': [34.74,127.76],
            'KP_Daesan': [36.99,126.35], 'KP_Pyeongtaek': [36.97,126.83],
            'KP_Incheon_LNG': [37.45,126.60], 'KP_Tongyeong': [34.83,128.43],
            'KP_Gwangyang': [34.93,127.70], 'KP_Pohang': [36.03,129.37],
            'KP_Incheon': [37.45,126.60], 'KP_Gunsan': [35.99,126.71],
            'KP_Busan': [35.10,129.03], 'KP_Boryeong': [36.33,126.59],
            'KP_Dangjin': [36.89,126.63],
        }
        # KG 로드 (항로·초크포인트·우회 정보)
        _KG = kg_data['kg_raw']
        print(f'KG (재사용): {len(_KG["nodes"])} nodes, {len(_KG["edges"])} edges')
        _KG_NODES = _KG['nodes']
        _KG_EDGES = _KG['edges']
        _SHIPS_TO = [e for e in _KG_EDGES if e.get('relation') == 'shipsTo']
        _BYPASSES = [e for e in _KG_EDGES if e.get('relation') == 'bypasses']
        _BP_NODES = {nid: n for nid, n in _KG_NODES.items() if nid.startswith('BP_')}
        _CP_NAMES = {nid: n.get('name','') for nid, n in _KG_NODES.items() if nid.startswith('CP_')}
        # v11: CP 키워드 (한국어 이름 첫 단어) — path 텍스트 매칭용
        _CP_KEYWORDS = {}
        for _cpid, _cpnm in _CP_NAMES.items():
            _parts = _cpnm.split()
            if _parts:
                _CP_KEYWORDS[_cpid] = _parts[0]  # e.g. "호르무즈", "수에즈", "바브엘만데브"
        # v11: KG linkedTo 양방향 전파 — 같은 수로의 두 관문은 어느 쪽이 막혀도 전체 항로 차단
        # e.g. 수에즈↔바브엘만데브: 유럽-아시아 항로에서 둘 다 통과해야 하므로 양방향
        _CP_LINKED = {}
        for _cpid, _cpnode in _KG_NODES.items():
            if _cpid.startswith('CP_') and 'linkedTo' in _cpnode:
                _linked = _cpnode['linkedTo']
                _CP_LINKED.setdefault(_cpid, set()).add(_linked)
                _CP_LINKED.setdefault(_linked, set()).add(_cpid)
        # ── 주요 해상 구간별 중간 경유점 (실제 항로대 반영) ──
        # ── searoute 라이브러리 기반 실제 해상 항로 웨이포인트 ──
        _MALACCA_STRAIT_EXIT = [[2, 102], [1.1, 103.6], [1.34, 104.48]]
        _SCS_TO_KR = [[7.61, 107.32], [12.2, 110.1], [16.38, 111.81], [16.95, 112.05], [16.96, 112.05], [21.7, 114.1], [23, 117], [23.77, 118.1], [24.54, 118.94], [25.09, 119.76], [25.7, 120], [27.8, 121.3], [28.73, 122.21], [30.18, 122.88], [30.83, 122.68], [31.3, 122.9], [33.55, 126.54], [34.2, 127.6]]
        _MALACCA_TO_KR = _MALACCA_STRAIT_EXIT + _SCS_TO_KR
        _INDIAN_OCEAN = [[6.2, 85.95], [6.47, 90], [6.7, 94], [7, 97]]  # (레거시 호환)
        _SEA_SEGMENTS = {
            ('CP_Hormuz', 'CP_Malacca'): [[26.51, 56.55], [26.42, 56.76], [25.96, 56.93], [25.5, 57.1], [24, 59], [22.7, 60.4], [21.44, 62.38], [20.08, 64.5], [19.43, 64.95], [17.53, 66.26], [16.9, 66.69], [15.62, 67.57], [14.68, 68.8], [13.77, 70.0], [12.65, 71.46], [9.7, 75.3], [8, 77], [5.8, 80.1], [5.9, 81.9], [6.2, 85.95], [6.47, 90], [6.7, 94], [7, 97]],
            'Malacca_to_KP': _MALACCA_TO_KR,
            ('CP_Suez', 'CP_BabElMandeb'): [[29.7, 32.6], [27.9, 33.75], [27, 34.5], [23.6, 37], [20.75, 38.9], [16.3, 41.2], [15, 42]],
            ('CP_BabElMandeb', 'CP_Malacca'): [[12, 45], [12.58, 53.06], [11.43, 58.4], [10.87, 60.83], [10.03, 64.3], [9.86, 64.99], [8.88, 68.86], [8.67, 69.67], [8.37, 70.82], [7.8, 72.94], [6.81, 76.53], [5.9, 81.9], [6.47, 90], [7, 97]],
            ('FP_Rotterdam', 'CP_Suez'): [[52, 3.9], [50.99, 1.81], [50.5, 0.39], [49.95, -1.3], [49.62, -2.39], [48.8, -5.06], [43.92, -9.02], [40.78, -9.98], [36.32, -7.27], [36.16, -3.68], [36.47, -1.62], [37.49, 10.37], [37.21, 12.1], [35.85, 17.9], [33.75, 26.31], [32.32, 30.41]],
            ('FP_Tubarao', 'CP_Malacca'): [[-24.89, -34.61], [-27.34, -22.82], [-30, -10], [-32.14, 1.97], [-35, 18], [-34.37, 30.52], [-33.61, 39.47], [-32.99, 43.87], [-31.84, 50.0], [-30.26, 56.63], [-29.33, 59.85], [-25.78, 64.47], [-17.54, 72.59], [-12.54, 77.58], [-7.52, 82.53], [-2.51, 87.52], [-0.0, 90.0], [7, 97]],
            ('FP_PortHedland', 'CP_Lombok'): [[-17.85, 118.1], [-16.49, 117.71], [-16.44, 117.7]],
            ('CP_Lombok', 'KP'): [[-7.25, 116.2], [-5.4, 116.95], [-3.19, 118.45], [-2.15, 118.23], [1.1, 119.5], [1.1, 119.5], [3.81, 119.7], [4.08, 119.72], [4.75, 119.77], [8.4, 120.05], [14.4, 119.9], [17, 119.4], [19, 120], [22.1, 120], [23.01, 120], [23.91, 120], [24.36, 120], [24.81, 120], [25.7, 120], [27.8, 121.3], [28.73, 122.21], [30.18, 122.88], [30.83, 122.68], [31.3, 122.9], [33.55, 126.54], [34.2, 127.6]],
            ('FP_Gladstone', 'KP'): [[-18.49, 153.02], [-16.23, 152.88], [-13.62, 152.72], [-11.5, 152.59], [-10, 152.5], [-7, 149], [-4.8, 147], [-0.0, 144.73], [3.24, 143.2], [10, 140], [15.0, 140.0], [17.66, 140.0], [17.71, 140.0], [20, 140], [21.63, 138.64], [22.61, 137.82], [25.45, 135.44], [25.59, 135.33], [26.91, 134.25], [27.95, 133.4], [31, 130.9], [32.4, 132.4], [33.75, 131.9], [33.8, 131.21], [33.94, 131.07], [33.99, 130.69], [34.3, 130.1]],
            ('FP_Newcastle', 'KP'): [[-27.15, 153.8], [-24.8, 153.4], [-16.23, 152.88], [-11.5, 152.59], [-7, 149], [-0.0, 144.73], [10, 140], [17.66, 140.0], [20, 140], [22.61, 137.82], [25.59, 135.33], [27.95, 133.4], [32.4, 132.4], [33.8, 131.21], [33.99, 130.69]],
            ('FP_Indonesian', 'KP'): [[-5.2, 106.8], [-1.5, 107.3], [3.7, 109.8], [7.55, 109.93], [9.8, 111.91], [13.36, 115.04], [16.91, 118.16], [19, 120], [22.1, 120], [23.01, 120], [23.91, 120], [24.36, 120], [24.81, 120], [25.7, 120], [27.8, 121.3], [28.73, 122.21], [30.18, 122.88], [30.83, 122.68], [31.3, 122.9], [33.55, 126.54], [34.2, 127.6]],
            ('FP_Bintulu', 'KP'): [[3.7, 109.8], [7.55, 109.93], [9.8, 111.91], [13.36, 115.04], [16.91, 118.16], [19, 120], [22.1, 120], [23.01, 120], [23.91, 120], [24.36, 120], [24.81, 120], [25.7, 120], [27.8, 121.3], [28.73, 122.21], [30.18, 122.88], [30.83, 122.68], [31.3, 122.9], [33.55, 126.54], [34.2, 127.6]],
            ('CP_Taiwan', 'KP'): [[22.88, 120.06], [23.01, 120], [23.91, 120], [24.36, 120], [24.81, 120], [25.7, 120], [27.8, 121.3], [28.73, 122.21], [30.18, 122.88], [30.83, 122.68], [31.3, 122.9], [33.55, 126.54], [34.2, 127.6]],
            ('FP_Lianyungang', 'KP'): [[35.9, 121], [35.17, 123.25], [34.4, 125.6], [34.2, 127.6]],
            ('FP_Qingdao', 'KP'): [[35.17, 123.25], [34.4, 125.6], [34.2, 127.6]],
            ('FP_Yokohama', 'KP'): [[34.66, 139.19], [34.41, 138.79], [34.45, 138.6], [34.49, 138.42], [34.44, 138.14], [34.39, 137.86], [34.33, 137.2], [33.85, 136.6], [33.2, 135.1], [34.1, 134.86], [34.39, 134.39], [34.42, 134.01], [34.33, 133.63], [34.07, 133.09], [34.15, 132.96], [34.1, 132.66], [33.82, 132.46], [33.75, 131.9], [33.8, 131.21], [33.94, 131.07], [33.99, 130.69], [34.3, 130.1]],
            ('FP_Vancouver', 'KP'): [[48.66, -122.73], [48.41, -122.79], [48.21, -123.11], [48.16, -123.43], [48.49, -124.73], [50, -135.06], [50.7, -144.05], [51.5, -152.84], [51.83, -160.09], [51.59, -165.17], [51.1, -168.21], [50, -180], [50.48, -187.65], [48.48, -198.56], [47.44, -202.07], [43.2, -214], [41.36, -219.65]],
            ('FP_USGulf', 'KP'): [[25.38, -87.73], [21.79, -86.07], [9.21, -79.9], [8.99, -79.59], [10.04, -88.0], [15.63, -99.91], [24.93, -113.45], [32.73, -124.58], [35.61, -130.0], [40.3, -140.28], [44.96, -154.65], [47.06, -167.34], [50.69, -185.86], [48.11, -199.81], [41.68, -218.72]],
            ('FP_Fujairah', 'CP_Malacca'): [[25.5, 57.1], [24, 59], [22.7, 60.4], [21.44, 62.38], [20.08, 64.5], [19.43, 64.95], [17.53, 66.26], [16.9, 66.69], [15.62, 67.57], [14.68, 68.8], [13.77, 70.0], [12.65, 71.46], [9.7, 75.3], [8, 77], [5.8, 80.1], [5.9, 81.9], [6.2, 85.95], [6.47, 90], [6.7, 94], [7, 97]],
        }
        # ── searoute 사전 캐시 로드 (searoute_cache.json) ──
        _SR_FILE_CACHE = {}
        for _try_dir in ['.', os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else '.', os.getcwd()]:
            _sr_path = os.path.join(_try_dir, 'searoute_cache.json')
            if os.path.exists(_sr_path):
                try:
                    with open(_sr_path, 'r') as _f:
                        _SR_FILE_CACHE = json.load(_f)
                    print(f'  searoute cache: {len(_SR_FILE_CACHE)} routes from {_sr_path}')
                except Exception:
                    pass
                break
        # ── 지도 항로 폴백 경고 (재발 조기 발견용) ──
        #    경유점을 못 찾으면 지도는 '조용히' 직선을 그린다. 그 순간을 로그에 남긴다.
        _MAP_WARNS = set()
        def _map_warn(msg):
            if msg in _MAP_WARNS:
                return
            _MAP_WARNS.add(msg)
            print(f"  \u26a0 [MAP] {msg}")
            if os.environ.get('GITHUB_ACTIONS') == 'true':
                print(f"::warning title=\uc9c0\ub3c4 \ud56d\ub85c \ud3f4\ubc31::{msg}")

        if not _SR_FILE_CACHE:
            _map_warn("searoute_cache.json \uc744 \ucc3e\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4 \u2014 "
                      "\uc6b0\ud68c \ud56d\ub85c\uac00 \uc9c1\uc120\uc73c\ub85c \uadf8\ub824\uc9c8 \uc218 \uc788\uc2b5\ub2c8\ub2e4")

        _SR_CACHE = {}  # 동적 호출 캐시
        def _searoute_waypoints_by_id(from_id, to_id):
            """사전 캐시 → searoute 동적 호출 → 빈 리스트 순으로 해상 항로 반환"""
            # 1) 파일 캐시에서 조회
            cache_key = f"{from_id}|{to_id}"
            if cache_key in _SR_FILE_CACHE:
                return _SR_FILE_CACHE[cache_key]
            # 2) searoute 동적 호출
            if from_id not in _GEO or to_id not in _GEO:
                return []
            coord_from, coord_to = _GEO[from_id], _GEO[to_id]
            rt_key = (round(coord_from[0],1), round(coord_from[1],1),
                      round(coord_to[0],1), round(coord_to[1],1))
            if rt_key in _SR_CACHE:
                return _SR_CACHE[rt_key]
            try:
                import searoute as sr
                origin = [coord_from[1], coord_from[0]]
                dest = [coord_to[1], coord_to[0]]
                route = sr.searoute(origin, dest)
                coords = route['geometry']['coordinates']
                mid = coords[1:-1]
                if len(mid) > 20:
                    step = max(1, len(mid) // 20)
                    mid = mid[::step]
                    if coords[-2] not in mid:
                        mid.append(coords[-2])
                leaflet = [[round(c[1], 2), round(c[0], 2)] for c in mid]
                _SR_CACHE[rt_key] = leaflet
                return leaflet
            except Exception as _e:
                _map_warn(f"{from_id}\u2192{to_id}: \uce90\uc2dc\uc5d0 \uc5c6\uace0 searoute \ud638\ucd9c\ub3c4 "
                          f"\uc2e4\ud328 ({type(_e).__name__}) \u2014 \uacbd\uc720\uc810 \uc5c6\uc74c")
                _SR_CACHE[rt_key] = []
                return []
        def _sea_waypoints(from_id, to_id):
            """두 노드 사이의 해상 경유점 — 정확매칭 → 캐시/searoute → generic 순"""
            # 1) 정확한 (from, to) 매칭
            key = (from_id, to_id)
            if key in _SEA_SEGMENTS:
                return _SEA_SEGMENTS[key]
            # 2) searoute 캐시 / 동적 호출 (항구별 정밀 경로)
            sr = _searoute_waypoints_by_id(from_id, to_id)
            if sr:
                return sr
            # 3) generic KP 폴백 (캐시에도 없을 때만)
            if to_id.startswith('KP_'):
                generic = (from_id, 'KP')
                if generic in _SEA_SEGMENTS:
                    return _SEA_SEGMENTS[generic]
                if from_id == 'CP_Malacca':
                    return _SEA_SEGMENTS.get('Malacca_to_KP', [])
                if from_id == 'CP_Lombok':
                    return _SEA_SEGMENTS.get(('CP_Lombok', 'KP'), [])
            _map_warn(f"{from_id}\u2192{to_id}: \uacbd\uc720\uc810 \uc5c6\uc74c \u2014 "
                      f"\uc9c1\uc120\uc73c\ub85c \uadf8\ub824\uc9d1\ub2c8\ub2e4(\uc721\uc9c0 \uad00\ud1b5 \uac00\ub2a5)")
            return []
        def _render_map(s, map_id):
            """KG 기반 Leaflet 지도: 초크포인트·항로·우회 경로를 KG에서 읽어 렌더링"""
            part_a = s.get('part_a', {})
            routes = part_a.get('routes', []) if isinstance(part_a, dict) else []
            tier = s.get('tier', 1)
            sig = s.get('signal', {})
            # part_a routes → 교란 중인 CP 추출
            # v11: 2-layer 접근 (v10 kg_basis 단독 → 말라카 등 거짓양성 발생 해결)
            #   Layer 1: path 텍스트에서 CP 키워드(이름 첫 단어) 매칭
            #            — LLM 자유 텍스트이지만, CP명 첫 단어는 거의 항상 포함
            #   Layer 2: KG linkedTo 단방향 전파
            #            — 바브엘만데브 교란 → 수에즈도 영향 (지리적 연쇄)
            #   안전장치: dominant_cluster는 항상 포함
            _active_cps = set()
            # Layer 1: path 키워드 매칭 (CP명 첫 단어 vs 전체 route path 텍스트)
            _all_paths = ' '.join(r.get('path', '') for r in routes)
            for cp_id, kw in _CP_KEYWORDS.items():
                if kw in _all_paths:
                    _active_cps.add(cp_id)
            # Layer 2: KG linkedTo 양방향 전파 (같은 수로 관문 — 어느 쪽 교란이든 전체 항로 차단)
            _propagated = set()
            for cp_id in list(_active_cps):
                for linked in _CP_LINKED.get(cp_id, set()):
                    if linked not in _active_cps:
                        _propagated.add(linked)
            _active_cps |= _propagated
            _dominant = sig.get('dominant_cluster', '')
            # dominant CP는 항상 active에 포함 (안전장치)
            if _dominant and _dominant.startswith('CP_') and _dominant in _CP_NAMES:
                _active_cps.add(_dominant)
            _secondary_cps = _active_cps - {_dominant}
            if _active_cps:
                print(f'  MAP {s.get("week","?")}: active={sorted(_active_cps)}, dominant={_dominant}, secondary={sorted(_secondary_cps)}, propagated={sorted(_propagated)}')
            if tier <= 1 and not _active_cps:
                print(f'  MAP skip: {s.get("week","?")}')
                return ''
            # ── 교란 품목 목록 ──
            active_commodities = set()
            for r in routes:
                active_commodities.add(r.get('commodity',''))
            # ── KG shipsTo 중 교란 CP를 경유하는 항로 추출 ──
            affected_routes = []
            for e in _SHIPS_TO:
                edge_cps = set(e.get('chokepoints', []))
                if edge_cps & _active_cps:
                    affected_routes.append(e)
            # ── 마커 JS 생성 ──
            markers_js = []
            # 1) 초크포인트 마커
            for cp_id in _GEO:
                if not cp_id.startswith('CP_'):
                    continue
                coord = _GEO[cp_id]
                name = _CP_NAMES.get(cp_id, cp_id)
                is_dominant = (cp_id == _dominant)
                is_secondary = (cp_id in _secondary_cps)
                if is_dominant:
                    color, radius, opacity, status = '#c0392b', 10, 1.0, '주요 교란'
                elif is_secondary:
                    color, radius, opacity, status = '#e67e22', 8, 0.9, '교란'
                else:
                    color, radius, opacity, status = '#2980b9', 5, 0.4, '정상'
                popup = f"<b>{name}</b><br>상태: {status}" if (is_dominant or is_secondary) else f"<b>{name}</b>"
                # CP_Panama는 한국 오른쪽(+360°)에 기본 표시
                lng = coord[1] + 360 if cp_id == 'CP_Panama' else coord[1]
                markers_js.append(
                    f'L.circleMarker([{coord[0]},{lng}],{{radius:{radius},fillColor:"{color}",'
                    f'color:"#fff",weight:2,fillOpacity:{opacity}}}'
                    f').addTo(m).bindPopup("{popup}");'
                )
            # 2) 한국 마커
            markers_js.append(
                'L.marker([36.5,127.0],{icon:L.divIcon({html:"\U0001f1f0\U0001f1f7",className:"kr-icon",iconSize:[24,24],iconAnchor:[12,12]})}).addTo(m);'
            )
            routes_js = []
            # 3) KG shipsTo 항로 — 교란 CP 경유 항로만 표시
            _drawn = set()  # 중복 방지 (동일 FP→KP 그룹)
            for e in affected_routes:
                fp = e.get('from','')
                kp = e.get('to','')
                cf = e.get('commodity','')
                edge_cps = e.get('chokepoints', [])
                key = f"{fp}_{kp}"
                if key in _drawn or fp not in _GEO or kp not in _GEO:
                    continue
                _drawn.add(key)
                # 경유지 좌표 연결: 해상 경유점 삽입으로 실제 항로 반영
                node_seq = [fp] + [cp for cp in edge_cps if cp in _GEO] + [kp]
                is_panama_route = 'CP_Panama' in edge_cps
                waypoints = []
                for idx_n in range(len(node_seq)):
                    nid = node_seq[idx_n]
                    coord = list(_GEO[nid])
                    # 파나마 경유: 미주 측 좌표를 +360° 이동 (지도 오른쪽에서 한국으로 연속 표시)
                    if is_panama_route and not nid.startswith('KP_') and coord[1] < 0:
                        coord = [coord[0], coord[1] + 360]
                    if idx_n == 0:
                        waypoints.append(coord)
                    else:
                        prev_id = node_seq[idx_n - 1]
                        # 파나마 경유 태평양 횡단 전용 경유점 (양수 경도: 오른쪽→한국)
                        if prev_id == 'CP_Panama' and nid.startswith('KP_'):
                            waypoints.extend([
                                [12, 240], [22, 200], [28, 165],
                                [26, 140], [30, 128], [33, 126],
                            ])
                        else:
                            intermediates = _sea_waypoints(prev_id, nid)
                            if is_panama_route:
                                intermediates = [[p[0], p[1] + 360] if p[1] < 0 else p for p in intermediates]
                            waypoints.extend(intermediates)
                        waypoints.append(coord)
                fp_name = _KG_NODES.get(fp, {}).get('name', fp)
                kp_name = _KG_NODES.get(kp, {}).get('name', kp)
                cf_name = _KG_NODES.get(cf, {}).get('name', cf)
                popup = f"{fp_name} → {kp_name} ({cf_name})"
                routes_js.append(
                    f'L.polyline({json.dumps(waypoints)},{{color:"#2980b9",weight:2,opacity:0.6}}).addTo(m).bindPopup("{popup}");'
                )
            # 4) 우회 경로 — 교란 CP에 대한 bypass
            for bp in _BYPASSES:
                bp_id = bp.get('from','')
                bypassed_cp = bp.get('to','')
                if bypassed_cp not in _active_cps:
                    continue
                bp_node = _BP_NODES.get(bp_id, {})
                bp_name = bp_node.get('name', bp_id)
                add_days = bp_node.get('additionalDays', '?')
                cap_pct = bp.get('bypassCapacityPct', '?')
                popup = f"우회: {bp_name}<br>추가 일수: {add_days}일 | 용량: {cap_pct}%"
                # 우회 경로 시각화: BP_CapeRoute는 희망봉 루트
                if bp_id == 'BP_CapeRoute':
                    # 사전 캐시에서 희망봉 경유 항로 로드
                    if 'CAPE_ROUTE' in _SR_FILE_CACHE:
                        cape_route = _SR_FILE_CACHE['CAPE_ROUTE']
                    else:
                        # searoute 동적 생성 (Rotterdam→Cape→Busan)
                        _rot = _GEO.get('FP_Rotterdam', [51.9, 4.5])
                        _bus = _GEO.get('KP_Busan', [35.1, 129.0])
                        try:
                            import searoute as sr
                            _r1 = sr.searoute([_rot[1], _rot[0]], [18.5, -34.5])
                            _r2 = sr.searoute([18.5, -34.5], [_bus[1], _bus[0]])
                            _combined = _r1['geometry']['coordinates'] + _r2['geometry']['coordinates'][1:]
                            _step = max(1, len(_combined) // 30)
                            _simp = _combined[::_step]
                            if _combined[-1] not in _simp:
                                _simp.append(_combined[-1])
                            cape_route = [[round(c[1],2), round(c[0],2)] for c in _simp]
                        except Exception:
                            _map_warn("CAPE_ROUTE \uce90\uc2dc \uc5c6\uc74c + searoute \ubbf8\uc124\uce58 \u2014 "
                                      "\ud76c\ub9dd\ubd09 \uc6b0\ud68c\ub85c\uac00 3\uc810 \uc9c1\uc120\uc73c\ub85c \uadf8\ub824\uc9d1\ub2c8\ub2e4")
                            cape_route = [_rot, [-34.5, 18.5], _bus]
                    routes_js.append(
                        f'L.polyline({json.dumps(cape_route)},{{color:"#c0392b",weight:2,opacity:0.7,dashArray:"8,5"}}).addTo(m).bindPopup("{popup}");'
                    )
                elif bp_id == 'BP_SaudiEastWest':
                    # 사우디 동서 파이프라인: 페르시아만→홍해(얀부)
                    pipe_route = [_GEO.get('FP_RasTanura',[26.6,50.2]),[24.5,44.0],[24.0,38.2]]
                    routes_js.append(
                        f'L.polyline({json.dumps(pipe_route)},{{color:"#e67e22",weight:3,opacity:0.7,dashArray:"4,4"}}).addTo(m).bindPopup("{popup}");'
                    )
                elif bp_id == 'BP_FujairahPipeline':
                    # Habshan→Fujairah 파이프라인
                    pipe_route = [[24.2,54.6],_GEO.get('FP_Fujairah',[25.1,56.3])]
                    routes_js.append(
                        f'L.polyline({json.dumps(pipe_route)},{{color:"#e67e22",weight:3,opacity:0.7,dashArray:"4,4"}}).addTo(m).bindPopup("{popup}");'
                    )
                elif bp_id == 'BP_MagellanRoute':
                    # 마젤란 해협/케이프혼 우회: USGulf → 남대서양 → 케이프혼 → 남태평양 → 한국
                    # +360° 좌표계 사용 (파나마 경유 항로와 동일하게 지도 오른쪽에서 표시)
                    _usg = _GEO.get('FP_USGulf', [29.3, -94.8])
                    _bus = _GEO.get('KP_Busan', [35.1, 129.0])
                    magellan_route = [
                        [_usg[0], _usg[1] + 360],  # USGulf (+360)
                        [25, 295],    # 카리브해 남부
                        [10, 310],    # 베네수엘라 앞바다
                        [0, 320],     # 적도 브라질 앞바다
                        [-15, 330],   # 브라질 남부
                        [-35, 310],   # 아르헨티나 앞바다
                        [-52, 295],   # 마젤란 해협 입구
                        [-56, 290],   # 케이프혼
                        [-55, 270],   # 드레이크 해협 서쪽
                        [-50, 250],   # 남태평양 진입
                        [-40, 220],   # 남태평양
                        [-25, 195],   # 태평양 중부
                        [-10, 175],   # 적도 부근
                        [10, 155],    # 서태평양
                        [25, 135],    # 필리핀해
                        [_bus[0], _bus[1]],  # 부산
                    ]
                    routes_js.append(
                        f'L.polyline({json.dumps(magellan_route)},{{color:"#c0392b",weight:2,opacity:0.7,dashArray:"8,5"}}).addTo(m).bindPopup("{popup}");'
                    )
            all_js = ' '.join(markers_js + routes_js)
            # ── 범례 ──
            dominant_names = [_CP_NAMES.get(c,'') for c in [_dominant] if c in _CP_NAMES and c in _active_cps]
            secondary_names = [_CP_NAMES.get(c,'') for c in _secondary_cps if c in _CP_NAMES]
            legend_parts = []
            if dominant_names:
                legend_parts.append(f'<span style="color:#c0392b">\u25cf 주요:</span> {", ".join(dominant_names)}')
            if secondary_names:
                legend_parts.append(f'<span style="color:#e67e22">\u25cf 부차:</span> {", ".join(secondary_names)}')
            legend_html = ' | '.join(legend_parts) if legend_parts else '현재 교란 없음'
            commodity_list = ', '.join(active_commodities) if active_commodities else '-'
            # 우회 경로 범례
            active_bp_names = []
            for bp in _BYPASSES:
                if bp.get('to','') in _active_cps:
                    bp_node = _BP_NODES.get(bp.get('from',''), {})
                    active_bp_names.append(bp_node.get('name', bp.get('from','')))
            bp_legend = f' | 우회: {", ".join(set(active_bp_names))}' if active_bp_names else ''
            return (
                f'<div class="map-section">'
                f'<div class="map-title">\U0001f5fa 해상 공급망 위기 지도</div>'
                f'<div id="{map_id}" style="width:100%;height:340px;border-radius:8px;border:1px solid #ddd;"></div>'
                f'<div class="map-legend">{legend_html}</div>'
                f'<div class="map-legend" style="margin-top:2px;">'
                f'<span style="color:#2980b9">\u2500\u2500 정상 항로</span> | '
                f'<span style="color:#c0392b">- - - 우회 항로</span> | '
                f'<span style="color:#e67e22">- - 파이프라인</span> | '
                f'교란 품목: {commodity_list}{bp_legend}</div>'
                f'<script>'
                f'window.__mapInit=window.__mapInit||{{}};'
                f'window.__mapInit["{map_id}"]=function(){{'
                f'if(window.__maps&&window.__maps["{map_id}"])return;'
                f'var m=L.map("{map_id}",{{scrollWheelZoom:false,zoomControl:true}}).setView([15,60],2);'
                f'L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}",{{maxZoom:16}}).addTo(m);'
                f'L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{{z}}/{{y}}/{{x}}",{{attribution:"Esri, HERE, Garmin, &copy; OSM",maxZoom:16}}).addTo(m);'
                f'{all_js}'
                f'window.__maps=window.__maps||{{}};window.__maps["{map_id}"]=m;'
                f'}};'
                f'</script>'
                f'</div>'
            )
        def render_scenario(s):
            if s.get('skipped'):
                return (f'<div class="skipped">⏭ 스킵 — Tier {s.get("tier","?")} {s.get("tier_label","")}</div>')
            tier   = s.get('tier', 1)
            label  = s.get('tier_label', '정상')
            period = s.get('period', '?')
            header = s.get('header', {})
            sig    = s.get('signal', {})
            indicators = s.get('indicators', {})
            html   = []
            # 헤더 블록
            # crisis_level은 Tier에서 자동 결정 (LLM 자유 판단 제거 — Tier가 이미 신호 정규화 완료)
            _TIER_CRISIS = {1: 'Normal', 2: 'Caution', 3: 'Warning', 4: 'Crisis'}
            crisis_level = _TIER_CRISIS.get(tier, '?')
            cl_color = {'Crisis':'#c0392b','Warning':'#e67e22','Caution':'#2980b9','Normal':'#27ae60'}.get(crisis_level,'#7f8c8d')
            tier_bg  = TIER_BG.get(tier, '#7f8c8d')
            html.append(
                f'<div class="header-block">'
                f'<div class="header-top">'
                f'<span class="tier-badge" style="background:{tier_bg}">Tier {tier} {esc(label)}</span>'
                f'<span class="period">{format_period_display(s)}</span>'
                f'<span class="crisis-level" style="color:{cl_color}">● {esc(crisis_level)}</span>'
                f'</div>'
                f'<div class="signal-bar">'
                f'신호: Crisis <b>{sig.get("crisis_pct","?")}%</b> | '
                f'Warning <b>{sig.get("warning_pct","?")}%</b> | '
                f'합산 <b>{sig.get("warning_crisis_pct","?")}%</b> | '
                f'총 <b>{sig.get("n_articles","?")}건</b> | '
                f'추세: <b>{sig.get("trend","-")}</b>'
                f'</div>'
            )
            # ── 지도 삽입 ──
            _map_id = f"map_{s.get('week','x').replace('-','_')}"
            html.append(_render_map(s, _map_id))
            # ── 상황 요약: 개조식 (위기 클러스터별 텍스트 재구조화) ──
            summary = header.get('situation_summary', '')
            _ref_map = s.get('ref_map', {})
            if summary:
                html.append('<div class="situation structured">')
                html.append('<b>■ 상황 요약</b>')
                # 클러스터 키워드 매핑 (우선순위 순)
                _CL_KW = [
                    ('홍해/바브엘만데브', ['후티', '예멘', '바브엘만데브', '홍해']),
                    ('호르무즈 해협', ['호르무즈', '이란', '걸프', '통행료', '봉쇄']),
                    ('수에즈 운하', ['수에즈']),
                    ('파나마 운하', ['파나마']),
                    ('대만 해협', ['대만해협', '대만 해협']),
                    ('흑해', ['흑해', '우크라이나', '러시아']),
                    ('에너지/유가', ['브렌트유', 'WTI', '유가', 'IEA', 'OPEC', 'LNG', '카타르', '에너지 안보', '에너지 대란']),
                    ('한국 영향', ['국내', '한국', '소비자', '가스요금', '나프타', '수출 금지', '수출금지', '원/달러', '환율']),
                    ('해운/물류', ['SCFI', 'BDI', '운임', '컨테이너', '선사']),
                ]
                # 텍스트를 문장 단위로 분할 (한국어 문장종결어미 기준)
                _marked = re.sub(r'([다음임함짐됨](?:\[\d+\])*\.)(\s+)(?=[가-힣0-9"\'\'])', r'\1§SPLIT§', summary)
                # re.split with capturing group produces interleaved whitespace — filter
                _segs = [s.strip() for s in _marked.split('§SPLIT§') if s.strip() and len(s.strip()) > 5]
                # 각 세그먼트를 클러스터에 배정
                _cl_map = {}  # {cluster: [segments]}
                _unmatched = []
                for _seg in _segs:
                    _assigned = False
                    for _cname, _kws in _CL_KW:
                        if any(_kw in _seg for _kw in _kws):
                            _cl_map.setdefault(_cname, []).append(_seg)
                            _assigned = True
                            break
                    if not _assigned:
                        _unmatched.append(_seg)
                # 클러스터별 렌더링
                if _cl_map:
                    for _cname, _items in _cl_map.items():
                        html.append(f'<div class="cluster-group">')
                        html.append(f'<div class="cluster-title">{esc(_cname)}</div>')
                        html.append('<ul class="cluster-bullets">')
                        for _item in _items:
                            html.append(f'<li>{_linkify_refs(clean_kg(_item), _ref_map)}</li>')
                        html.append('</ul></div>')
                    if _unmatched:
                        html.append(f'<div class="cluster-group">')
                        html.append(f'<div class="cluster-title">종합</div>')
                        html.append('<ul class="cluster-bullets">')
                        for _item in _unmatched:
                            html.append(f'<li>{_linkify_refs(clean_kg(_item), _ref_map)}</li>')
                        html.append('</ul></div>')
                else:
                    # 클러스터 매핑 실패: 단순 bullet 리스트
                    html.append('<ul class="cluster-bullets">')
                    for _seg in _segs:
                        html.append(f'<li>{_linkify_refs(clean_kg(_seg), _ref_map)}</li>')
                    html.append('</ul>')
                # 접이식 원문
                html.append(f'<details class="original-narrative"><summary>원문 보기</summary><p>{_linkify_refs(clean_kg(summary), _ref_map)}</p></details>')
                html.append('</div>')
                # ── 주간 참조 기사 목록 ──
                _wsrc = _get_week_sources(period)
                if _wsrc:
                    html.append(_render_weekly_sources(_wsrc))
            # 기존 저장 JSON에 개별기업주가 포함된 경우를 위해 렌더링 단계에서도 필터
            _IND_STOCK_UNITS = {'KRW'}  # 개별기업주는 KRW + 집계지표가 아닌 경우
            _IND_STOCK_NAMES = {
                'SK이노', 'S-Oil', '롯데케미칼', 'LG화학', '한화솔루션',
                'HMM', '팬오션', '가스공사', '대한항공', 'CJ제일제당', '농심',
                'SK이노베이션',
            }
            changes = [
                c for c in header.get('changes_from_prev', [])
                if c.get('item', '') not in _IND_STOCK_NAMES
            ]
            if changes:
                html.append('<div class="changes"><b>■ 전주 대비 주요 변화</b><ul>')
                for c in changes:
                    sym  = chg_sym(c.get('change',''))
                    item = esc(c.get('item',''))
                    frm  = esc(c.get('from',''))
                    to_  = esc(c.get('to',''))
                    det  = clean_kg(c.get('detail',''))
                    html.append(f'<li>{sym} <b>{item}</b>  {frm} → {to_}  <span class="detail">{det}</span></li>')
                html.append('</ul></div>')
            wps = header.get('watchpoints', [])
            if wps:
                html.append('<div class="watchpoints"><b>■ 향후 주시 포인트</b><ul>')
                for w in wps:
                    html.append(f'<li><span class="horizon">{esc(w.get("horizon",""))}</span> {clean_kg(w.get("point",""))}</li>')
                html.append('</ul></div>')
            html.append('</div>')  # header-block
            # 지표 패널
            html.append(render_indicators(indicators))
            # Part A
            routes = s.get('part_a', {}).get('routes', [])
            if routes:
                html.append('<div class="part"><div class="part-title">■ 국제 → 한국 전파경로</div>')
                for r in routes:
                    status   = r.get('status', '')
                    is_new   = r.get('is_new', False)
                    d_type   = r.get('disruption_type', '')
                    _dt_map  = {'ROUTE':'⚓ 경로위기','SOURCE':'🏭 공급원위기','LOGISTICS':'📦 물류위기'}
                    dt_tag   = f'<span class="dt-tag dt-{d_type.lower()}">{_dt_map.get(d_type, d_type)}</span>' if d_type else ''
                    new_tag  = '<span class="new-tag">☆ 신규</span>' if (is_new or status == '신규활성') else ''
                    sta_col  = {'활성':'#27ae60','신규활성':'#e67e22','비활성':'#7f8c8d'}.get(status,'#bdc3c7')
                    html.append(
                        f'<div class="route-item">'
                        f'<span class="route-status" style="border-color:{sta_col};color:{sta_col}">{esc(status)}</span>'
                        f'<b>{clean_kg(r.get("commodity","?"))}</b> {dt_tag}{new_tag}'
                        f'<div class="route-path">⟶ {clean_path(r.get("path",""))}</div>'
                        f'</div>'
                    )
                html.append('</div>')
            # Part B
            cascades = s.get('part_b', {}).get('cascades', [])
            if cascades:
                html.append('<div class="part"><div class="part-title">■ 국내 산업간 전파경로</div>')
                for cas in cascades:
                    new_tag = '<span class="new-tag">☆ 신규</span>' if cas.get('is_new') else ''
                    html.append(f'<div class="cascade-item"><b>{clean_kg(cas.get("name",""))} {new_tag}</b><div class="cascade-steps">')
                    for step in cas.get('steps', []):
                        html.append(
                            f'<span class="step">{clean_sector(step.get("from",""))} → {clean_sector(step.get("to",""))}</span>'
                            f'<span class="mech"> ({clean_kg(step.get("mechanism",""))}, {esc(step.get("lag",""))})</span> '
                        )
                    html.append('</div></div>')
                html.append('</div>')
            # Part D
            matrix = s.get('part_d', {}).get('matrix', [])
            if matrix:
                html.append('<div class="part"><div class="part-title">■ 산업별 영향 및 전파경로</div>')
                html.append(
                    '<table class="matrix-table">'
                    '<thead><tr><th>산업</th><th>방향</th>'
                    '<th>초기(0-4주)</th><th>중기(4-12주)</th><th>장기(12주+)</th>'
                    '<th>영향 및 전파경로</th><th>변화</th></tr></thead><tbody>'
                )
                for row in matrix:
                    dir_ = row.get('direction', '?')
                    _pw = row.get('pathway', '')
                    _pw_sents = [p.strip() for p in re.split(r'(?<=[\.])\s+|;\s*', _pw) if p.strip()] if _pw else []
                    _path_sents = [p for p in _pw_sents if '→' in p]
                    _impact_sents = [p for p in _pw_sents if '→' not in p]
                    _pw_html = ''
                    if _path_sents and _impact_sents:
                        _pw_html = '<span class="pw-label">▸ 영향:</span> ' + clean_path('. '.join(_impact_sents))
                        _pw_html += '<br><span class="pw-label">▸ 전파경로:</span> ' + clean_path('. '.join(_path_sents))
                    elif _path_sents:
                        _pw_html = '<span class="pw-label">▸ 전파경로:</span> ' + clean_path('. '.join(_path_sents))
                    elif _impact_sents:
                        _pw_html = '<span class="pw-label">▸ 영향:</span> ' + clean_path('. '.join(_impact_sents))
                    else:
                        _pw_html = clean_path(_pw) if _pw else ''
                    html.append(
                        '<tr>'
                        f'<td><b>{clean_sector(row.get("sector","?"))}</b></td>'
                        f'<td>{DIR_ICON.get(dir_,"?")} {esc(dir_)}</td>'
                        f'<td>{sev_badge(row.get("initial","?"))}</td>'
                        f'<td>{sev_badge(row.get("mid","?"))}</td>'
                        f'<td>{sev_badge(row.get("long","?"))}</td>'
                        f'<td class="pathway">{_pw_html}</td>'
                        f'<td class="change-col">{chg_sym(row.get("change",""))}</td>'
                        '</tr>'
                    )
                html.append('</tbody></table></div>')
            # Part E
            part_e = s.get('part_e', {})
            vulns  = part_e.get('vulnerabilities', [])
            recs   = part_e.get('monitoring_recommendations', [])
            if vulns or recs:
                html.append('<div class="part part-e"><div class="part-title">■ 공급망 취약점 진단 및 모니터링 권고</div>')
                if vulns:
                    html.append('<b>공급망 취약점 진단</b><ul>')
                    for v in vulns:
                        html.append(f'<li>{esc(v)}</li>')
                    html.append('</ul>')
                if recs:
                    html.append('<b>모니터링 권고</b><ul>')
                    for r in recs:
                        html.append(f'<li>{esc(r)}</li>')
                    html.append('</ul>')
                html.append('</div>')
            return '\n'.join(html)
        def tier_dot(t):
            colors = {1:'#27ae60',2:'#2980b9',3:'#e67e22',4:'#c0392b'}
            return f'<span style="color:{colors.get(t,"#bdc3c7")}">●</span>'
        def format_period_plain(s):
            """사이드바 탭용 — period 문자열의 ISO week 번호 표시 (ex: 2026.03.30 (Week 13))"""
            import re as _re
            period_str = s.get('period', '') or s.get('week_label', '') or ''
            m_date = _re.search(r'(\d{4})-(\d{2})-(\d{2})', period_str)
            m_week = _re.search(r'W(\d{1,2})', period_str)
            if m_date and m_week:
                y, mo, d = m_date.groups()
                w = int(m_week.group(1))
                return f"{y}.{mo}.{d} (Week {w:02d})"
            return period_str
        def format_period_display(s):
            """
            period="2026-W01 (2026-01-05)" → "2026.01.05 (Week 01)"
            + 기사수집기간 서브타이틀 (월요일 기준 직전 7일)
            week_label="2026-W01"가 fallback
            """
            import re as _re
            from datetime import date as _date, timedelta as _td
            period_str = s.get('period', '') or s.get('week_label', '') or ''
            # 날짜 추출: YYYY-MM-DD 또는 마감 YYYY-MM-DD 패턴
            m_date = _re.search(r'(\d{4})-(\d{2})-(\d{2})', period_str)
            # 주차 추출: W\d{1,2}
            m_week = _re.search(r'W(\d{1,2})', period_str)
            if m_date and m_week:
                y, mo, d = m_date.groups()
                w = int(m_week.group(1))
                main = f"{y}.{mo}.{d} (Week {w:02d})"
                # 기사수집기간: 해당 월요일 기준 직전 7일 (월~일)
                try:
                    ref_mon  = _date(int(y), int(mo), int(d))
                    wk_end   = ref_mon - _td(days=1)   # 직전 일요일
                    wk_start = ref_mon - _td(days=7)   # 직전 월요일
                    sub = (f'<br><small style="font-size:.9em;font-weight:normal;color:#888">' 
                           f'기사수집기간: {wk_start.strftime("%Y.%m.%d")}~{wk_end.strftime("%Y.%m.%d")}' 
                           f'</small>')
                except Exception:
                    sub = ''
                return main + sub
            elif m_date:
                y, mo, d = m_date.groups()
                return f"{y}.{mo}.{d}"
            elif m_week:
                w = int(m_week.group(1))
                year = _re.match(r'(\d{4})', period_str)
                yr = year.group(1) if year else ''
                return f"{yr} Week {w:02d}"
            return period_str
        display_scenarios = list(reversed(scenarios))  # 최신 주가 위에 오도록 내림차순
        sidebar_items  = []
        content_blocks = []
        for i, s in enumerate(display_scenarios):
            period  = s.get('period','?')
            tier    = s.get('tier', 1)
            label   = s.get('tier_label','?')
            skipped = s.get('skipped', False)
            sid     = f"sc_{i}"
            # PDF 파일 링크용 날짜
            import re as _re_pdf
            _m_pdf = _re_pdf.search(r'(\d{4})-(\d{2})-(\d{2})', s.get('period',''))
            _pdf_date = f'{_m_pdf.group(1)}.{_m_pdf.group(2)}.{_m_pdf.group(3)}' if _m_pdf else ''
            _pdf_url = f'pdf/KMI_Global_SC_AI_Weekly_Report({_pdf_date}).pdf' if _pdf_date >= '2026.04.20' else ''
            dot     = tier_dot(tier)
            skip_cls = ' skipped-menu' if skipped else ''
            active_cls = " active" if i == 0 else ""
            sidebar_items.append(
                f'<label class="menu-item{skip_cls}" for="tab_{sid}">'
                f'{dot} {esc(format_period_plain(s))}</label>'
            )
            content_blocks.append(
                f'<div class="scenario-block" id="{sid}" data-pdf="{_pdf_url}">{render_scenario(s)}</div>'
            )
        # ── radio input 생성 (CSS-only 탭 전환용) ──
        radio_inputs = []
        tab_css_rules = []
        for i, s in enumerate(display_scenarios):
            sid = f'sc_{i}'
            checked = ' checked' if i == 0 else ''
            radio_inputs.append(f'<input type="radio" name="sc" id="tab_{sid}" hidden{checked}>')
            tab_css_rules.append(f'#tab_{sid}:checked ~ .sidebar label[for="tab_{sid}"] {{ background:#3498db; }}')
            tab_css_rules.append(f'#tab_{sid}:checked ~ .main #{sid} {{ display:block; }}')
        TAB_CSS = '\n'.join(tab_css_rules)
        CSS = """
    * { box-sizing:border-box; margin:0; padding:0; }
    body { font-family:'Noto Sans KR',sans-serif; font-size:16px; background:#f5f6fa; color:#2c3e50; -webkit-user-select:none; user-select:none; }
    .container { display:flex; min-height:100vh; }
    .sidebar { width:180px; min-width:180px; background:#2c3e50; color:#ecf0f1; overflow-y:auto; padding:10px 0; position:sticky; top:0; height:100vh; align-self:flex-start; }
    .sidebar-title { padding:12px 14px; font-size:13px; font-weight:bold; border-bottom:1px solid #34495e; color:#bdc3c7; }
    .nav-link { display:flex; align-items:center; justify-content:space-between; padding:9px 12px; font-size:12px; font-weight:600; color:#ecf0f1; text-decoration:none; border-radius:6px; background:rgba(52,152,219,0.15); border:1px solid rgba(52,152,219,0.3); margin-bottom:8px; } .nav-link:hover { background:#3498db; border-color:#3498db; color:#fff; }
    .menu-item { padding:8px 10px; cursor:pointer; font-size:12px; line-height:1.5; border-bottom:1px solid #34495e; transition:.15s; }
    .menu-item:hover { background:#34495e; }
    .menu-item.active { background:#3498db; }
    .menu-item.skipped-menu { opacity:.5; }
    .main { flex:1; overflow-y:visible; padding:20px 28px; min-width:0; }
    .scenario-block { display:none; }
    #sc_welcome { display:none; }
    /* radio+label 탭 전환: 동적 CSS(TAB_CSS)가 checked 상태에 따라 표시 제어 */
    label.menu-item { display:block; -webkit-user-select:none; user-select:none; }
    .header-block { background:#fff; border-radius:8px; padding:18px 22px; margin-bottom:14px; box-shadow:0 1px 4px rgba(0,0,0,.08); }
    .header-top { display:flex; align-items:center; gap:12px; margin-bottom:10px; }
    .tier-badge { color:#fff; padding:4px 12px; border-radius:5px; font-weight:bold; font-size:15px; }
    .period { font-size:16px; font-weight:bold; flex:1; }
    .crisis-level { font-weight:bold; font-size:17px; }
    .signal-bar { font-size:14px; color:#7f8c8d; padding:6px 0 10px; border-bottom:1px solid #ecf0f1; margin-bottom:10px; }
    .situation p { font-size:16px; line-height:1.75; margin-top:8px; }
    .situation.structured { margin-top:10px; }
    .cluster-group { margin:10px 0 6px; padding-left:4px; border-left:3px solid #3498db; }
    .cluster-title { font-size:15px; font-weight:bold; color:#2c3e50; padding:4px 8px; background:#eaf2ff; margin-bottom:2px; }
    .cluster-bullets { margin:4px 0 8px 18px; padding:0; }
    .cluster-bullets li { font-size:15px; line-height:1.7; margin-bottom:5px; }
    .cluster-bullets li b { color:#2c3e50; }
    .original-narrative { margin-top:10px; font-size:14px; color:#7f8c8d; }
    .original-narrative summary { cursor:pointer; font-size:13px; color:#95a5a6; }
    .original-narrative p { font-size:14px; line-height:1.65; color:#555; margin-top:5px; }
    .changes, .watchpoints { margin-top:10px; }
    .changes ul, .watchpoints ul { margin-top:5px; padding-left:18px; }
    .changes li, .watchpoints li { font-size:16px; line-height:1.75; margin-bottom:6px; }
    .horizon { background:#eaf2ff; color:#2980b9; padding:1px 6px; border-radius:3px; font-size:11px; margin-right:6px; }
    .detail { color:#7f8c8d; font-size:13px; }
    .new-tag { background:#f39c12; color:#fff; padding:1px 6px; border-radius:3px; font-size:11px; }
    /* 전체 지표 패널 */
    .ind-panel { background:#fff; border-radius:8px; padding:14px 18px; margin-bottom:14px; box-shadow:0 1px 4px rgba(0,0,0,.08); border-left:4px solid #1abc9c; }
    .ind-title { font-weight:bold; font-size:13px; color:#1abc9c; margin-bottom:10px; }
    .ind-groups { display:flex; flex-wrap:wrap; gap:14px; }
    .ind-group { min-width:160px; flex:1; }
    .ind-group-title { font-size:11px; font-weight:bold; color:#7f8c8d; letter-spacing:.04em; margin-bottom:6px; padding-bottom:3px; border-bottom:1px solid #ecf0f1; }
    .ind-items { display:flex; flex-wrap:wrap; gap:6px; }
    .ind-item { background:#f8f9fa; border-radius:6px; padding:6px 10px; min-width:88px; cursor:default; transition:.15s; }
    .ind-item:hover { background:#eaf6f3; }
    .ind-name { font-size:11px; font-weight:bold; color:#555; margin-bottom:2px; }
    .ind-name a { color:#555; text-decoration:none; }
    .ind-name a:hover { color:#2980b9; text-decoration:underline; }
    .ind-val { font-size:13px; font-weight:bold; color:#2c3e50; }
    .ind-unit { font-size:10px; color:#95a5a6; margin-left:2px; font-weight:normal; }
    .ind-chg { font-size:11px; font-weight:bold; margin-top:2px; }
    .ind-date { font-size:10px; color:#aaa; margin-left:4px; font-weight:normal; vertical-align:middle; }
    /* Part 공통 */
    .part { background:#fff; border-radius:8px; padding:20px 24px; margin-bottom:14px; box-shadow:0 1px 4px rgba(0,0,0,.08); }
    .part-title { font-weight:bold; font-size:16px; border-bottom:2px solid #3498db; padding-bottom:6px; margin-bottom:12px; }
    .part-e { border-left:4px solid #9b59b6; }
    .part-e .part-title { border-bottom-color:#9b59b6; }
    .part-e ul { padding-left:18px; margin-top:6px; }
    .part-e li { font-size:15px; line-height:1.7; margin-bottom:5px; }
    .part-e b { font-size:15px; }
    .route-item { border-left:3px solid #3498db; padding:8px 12px; margin-bottom:8px; background:#f8f9fa; border-radius:0 4px 4px 0; }
    .route-status { border:1px solid; padding:1px 7px; border-radius:4px; font-size:11px; margin-right:8px; }
    .dt-tag { display:inline-block; font-size:11px; font-weight:600; padding:1px 6px; border-radius:3px; margin-left:6px; vertical-align:middle; }
    .dt-route { background:#e8f0fe; color:#1a56db; }
    .dt-source { background:#f3e8ff; color:#7c3aed; }
    .dt-logistics { background:#e0f7f4; color:#0d9488; }
    .route-path { margin-top:4px; color:#555; font-size:16px; line-height:1.75; }
    .cascade-item { border-left:3px solid #e67e22; padding:8px 12px; margin-bottom:8px; background:#fef9f0; border-radius:0 4px 4px 0; }
    .cascade-steps { margin-top:5px; font-size:16px; line-height:1.75; }
    .step { font-weight:bold; }
    .mech { color:#7f8c8d; }
    .sector-block { margin-bottom:16px; padding-bottom:12px; border-bottom:1px solid #f0f0f0; }
    .sector-block:last-child { border-bottom:none; margin-bottom:0; padding-bottom:0; }
    .sector-name { font-weight:bold; font-size:14px; margin-bottom:6px; }
    .sector-id { font-weight:normal; color:#7f8c8d; font-size:13px; }
    table { width:100%; border-collapse:collapse; font-size:15px; }
    th { background:#f0f3f7; padding:7px 10px; text-align:center; border:1px solid #e0e3e8; font-size:13px; }
    td { padding:8px 10px; border:1px solid #e0e3e8; vertical-align:middle; font-size:15px; line-height:1.6; }
    .impact-table td { text-align:center; }
    .impact-table td:first-child { text-align:left; }
    .matrix-table td { text-align:center; }
    .matrix-table td:first-child, .matrix-table td:nth-child(6) { text-align:left; }
    .change-col { font-weight:bold; color:#c0392b; }
    .pathway { color:#555; font-size:16px; line-height:1.75; }
    .pw-label { font-weight:600; color:#2980b9; font-size:13px; }
    .policy-table th, .policy-table td { text-align:left; }
    .basis { color:#7f8c8d; font-size:13px; }
    .skipped { padding:20px; color:#7f8c8d; font-style:italic; }
    /* ── 리포트 헤더 ── */
    .report-header { background:#2c3e50; color:#ecf0f1; padding:20px 28px; display:flex; align-items:center; gap:12px; flex-wrap:nowrap; }
    .kmi-logo { height:34px; margin-right:0; flex-shrink:0; }
    .rh-text-group { display:flex; flex-direction:column; gap:2px; }
    .report-header .rh-title { font-size:20px; font-weight:700; white-space:nowrap; }
    .report-header .rh-sub { font-size:13px; color:#bdc3c7; }
    @media (max-width: 768px) {
      .report-header { padding:8px 12px; flex-wrap:nowrap; }
      .report-header .rh-title { font-size:16px; }
      .kmi-logo { height:27px; margin-right:0; }
      .report-header .rh-sub { font-size:12px; }
    }
    /* ── 모바일 반응형 ── */
    @media (max-width: 768px) {
      body { font-size:15px; }
      .container { flex-direction:column; height:auto; min-height:auto; overflow:visible; }
      /* 탭바: 상단 고정, 가로 스크롤 */
      .sidebar { width:100%; min-width:0; height:auto;
        position:sticky; top:0; z-index:100; background:#2c3e50;
        display:flex; flex-wrap:nowrap; overflow-x:auto; overflow-y:hidden;
        padding:6px 8px; gap:4px; -webkit-overflow-scrolling:touch; }
      .sidebar-title { display:none; }
      .menu-item { flex:0 0 auto; border-radius:4px; padding:4px 10px;
        border-bottom:none; border-right:1px solid #34495e; font-size:11px; white-space:nowrap; }
      .nav-link { flex:0 0 auto; border-radius:4px; padding:4px 10px; border-bottom:none; white-space:nowrap; font-size:11px; margin-bottom:0; }
      label.menu-item { display:block; }
      .main { overflow:visible; padding:12px 14px; }
      .scenario-block { max-width:100%; }
      .header-top { flex-wrap:wrap; gap:8px; }
      .ind-groups { flex-direction:column; gap:8px; }
      .ind-group { min-width:0; }
      .ind-items { gap:4px; }
      table { font-size:11px; }
      th, td { padding:3px 5px; }
      .part { padding:12px 14px; }
      .header-block { padding:14px 16px; }
    }
    /* 주간 참조 기사 */
    /* 기사 인용 링크 */
    .ref-link { font-size:0.75em; vertical-align:super; }
    .ref-link a, .ref-link { color:#2980b9; text-decoration:none; cursor:pointer; }
    .ref-link a:hover { color:#e74c3c; text-decoration:underline; }
    .weekly-sources { margin-top:12px; }
    .ws-summary { cursor:pointer; font-size:14px; font-weight:600; color:#2980b9; padding:6px 10px; background:#eaf2ff; border-radius:5px; }
    .ws-summary:hover { background:#d5e6fa; }
    .ws-day { margin:4px 0 2px 8px; }
    .ws-day-sum { cursor:pointer; font-size:13px; color:#555; padding:3px 6px; }
    .ws-day-sum:hover { color:#2980b9; }
    .ws-day-body { padding-left:10px; }
    .ws-cat { font-size:12px; font-weight:600; color:#7f8c8d; margin:6px 0 2px; }
    .ws-arts { margin:0 0 4px 16px; padding:0; }
    .ws-arts li { font-size:12px; line-height:1.6; list-style:disc; }
    .ws-arts a { color:#555; text-decoration:none; }
    .ws-arts a:hover { color:#2980b9; text-decoration:underline; }
    .ws-more { color:#999; font-style:italic; list-style:none; }
    /* PDF 다운로드 링크 */
    .pdf-link-btn { display:inline-block; background:#fff; color:#2c3e50; text-decoration:none; padding:5px 12px; border-radius:4px; font-size:11px; margin-left:auto; transition:.2s; font-weight:600; white-space:nowrap; }
    .pdf-link-btn:hover { background:#ecf0f1; color:#2c3e50; }
    """
        # radio+label CSS가 탭 전환 담당 — JS 최소화
        JS = ""
        MAP_CSS = """
    .map-section { margin: 10px 14px 16px; }
    .map-title { font-size: 15px; font-weight: 700; margin-bottom: 6px; color: #2c3e50; }
    .map-legend { font-size: 11px; color: #555; margin-top: 4px; line-height: 1.6; }
    .kr-icon { font-size: 20px !important; background: none !important; border: none !important; }
    .leaflet-container { font-family: inherit; touch-action: none; }
    .map-section { touch-action: none; }
    @media (max-width: 768px) {
      .map-section { margin: 8px 0 12px; }
      .map-section > div[id^=map_] { height: 260px !important; }
      .map-title { font-size: 13px; }
      .map-legend { font-size: 10px; }
    }
    """
        MAP_JS = (
            "function _tryInitMap(md){"
            "if(!md||!md.id)return;"
            "if(typeof L==='undefined'){md.innerHTML='<div style=\"padding:20px;color:#888\">지도를 불러오는 중…</div>';return;}"
            "if(window.__mapInit&&window.__mapInit[md.id]){"
            "try{"
            "md.style.touchAction='none';"
            "md.parentElement.style.touchAction='none';"
            "window.__mapInit[md.id]();"
            "[300,800,1500].forEach(function(d){"
            "setTimeout(function(){if(window.__maps&&window.__maps[md.id])window.__maps[md.id].invalidateSize();},d);"
            "});"
            "}catch(e){"
            "md.innerHTML='<div style=\"padding:20px;color:red\">Map error: '+e.message+'</div>';"
            "}"
            "}else if(window.__maps&&window.__maps[md.id]){"
            "window.__maps[md.id].invalidateSize();"
            "}"
            "}"
            "function _initVisibleMap(){"
            "var ch=document.querySelector('input[name=sc]:checked');"
            "if(!ch)return;"
            "var bl=document.getElementById(ch.id.replace('tab_',''));"
            "if(!bl)return;"
            "var md=bl.querySelector('[id^=map_]');"
            "_tryInitMap(md);"
            "}"
            "(function(){"
            "if('IntersectionObserver' in window){"
            "var obs=new IntersectionObserver(function(entries){"
            "entries.forEach(function(e){"
            "if(e.isIntersecting&&e.intersectionRatio>0){"
            "_tryInitMap(e.target);"
            "}"
            "});"
            "},{threshold:0.1});"
            "document.querySelectorAll('[id^=map_]').forEach(function(el){obs.observe(el);});"
            "}else{"
            "window.addEventListener('load',function(){_initVisibleMap();});"
            "}"
            "})();"
            "window.addEventListener('resize',function(){var k;if(window.__maps){for(k in window.__maps){window.__maps[k].invalidateSize();}}});"
            "document.querySelectorAll('input[type=radio][hidden]').forEach(function(r){"
            "r.addEventListener('change',function(){setTimeout(_initVisibleMap,300);});"
            "});"
            "window.addEventListener('load',function(){"
            "setTimeout(_initVisibleMap,500);"
            "setTimeout(_initVisibleMap,2000);"
            "});"
        )
        # ── Leaflet CSS/JS 인라인 임베드 (모바일 웹뷰 CDN 차단 대응) ──
        # KMI 로고 (base64 임베딩)
        _logo_path = str(BASE_DIR / 'assets' / 'kmi_logo_white.png')
        import base64 as _b64
        _KMI_LOGO_B64 = ''
        if os.path.exists(_logo_path):
            with open(_logo_path, 'rb') as _f:
                _KMI_LOGO_B64 = _b64.b64encode(_f.read()).decode()
        LEAFLET_CSS = ''
        LEAFLET_JS = ''
        for _try_dir in ['.', os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else '.', os.getcwd()]:
            _leaf_css_path = os.path.join(_try_dir, 'leaflet_inline.css')
            _leaf_js_path  = os.path.join(_try_dir, 'leaflet_inline.js')
            if os.path.exists(_leaf_css_path) and os.path.exists(_leaf_js_path):
                with open(_leaf_css_path, 'r', encoding='utf-8') as _f:
                    LEAFLET_CSS = _f.read()
                with open(_leaf_js_path, 'r', encoding='utf-8') as _f:
                    LEAFLET_JS = _f.read()
                print(f'Leaflet 인라인 임베드: CSS {len(LEAFLET_CSS)//1024}KB + JS {len(LEAFLET_JS)//1024}KB (from {_try_dir})')
                break
        if not LEAFLET_JS:
            print('⚠ leaflet_inline.css/js 없음 — CDN fallback 사용')
        n = len(scenarios)
        _PDF_JS = """(function(){var rs=document.querySelectorAll('input[name="sc"]');var slot=document.getElementById('pdf-header-slot');function up(){var c=document.querySelector('input[name="sc"]:checked');if(!c||!slot)return;var d=document.getElementById(c.id.replace('tab_',''));var u=d?d.getAttribute('data-pdf'):'';slot.innerHTML=u?'<a class="pdf-link-btn" href="'+u+'" download>PDF</a>':'';}rs.forEach(function(r){r.addEventListener('change',up);});up();})();"""
        overall_html = (
            '<!DOCTYPE html>\n<html lang="ko">\n<head>\n'
            '<meta charset="UTF-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
            '<!-- Google Analytics (GA4) -->\n'
            '<script async src="https://www.googletagmanager.com/gtag/js?id=G-JEDV505PLS"></script>\n'
            '<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag("js",new Date());gtag("config","G-JEDV505PLS");</script>\n'
            '<title>글로벌 공급망 AI 주간 모니터링 | KMI</title>\n'
            f'<style>{CSS}\n{TAB_CSS}\n{MAP_CSS}\n{LEAFLET_CSS}</style>\n'
            + (f'<script>{LEAFLET_JS}</script>\n' if LEAFLET_JS else
               '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>\n'
               '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>\n')
            + '</head>\n<body>\n'
            '<div class="container">\n'
            + '\n'.join(radio_inputs) + '\n'
            '  <div class="sidebar">\n'
            + '<a class="nav-link" href="https://scm-briefing.kmi.re.kr/">Go To Daily <span style="font-size:16px;line-height:1">›</span></a>\n'
            + '\n'.join(sidebar_items) +
            '\n  </div>\n'
            '  <div class="main">\n'
            '  <div class="report-header">' + (f'<img class="kmi-logo" src="data:image/png;base64,{_KMI_LOGO_B64}" alt="KMI">' if _KMI_LOGO_B64 else '') + '<div class="rh-text-group"><span class="rh-title"> 글로벌 공급망 AI 주간 모니터링</span><span class="rh-sub">한국해양수산개발원(KMI) 해양수산 AX 지원단 · hmjeon@kmi.re.kr</span></div><span id="pdf-header-slot"></span></div>\n'
            '  <div style="background:#f0f4f8;border-left:4px solid #2980b9;padding:8px 14px;font-size:11px;color:#555;margin:10px 14px 0 14px">\n'
            '  본 리포트는 온톨로지 기반 전문가 지식 그래프와 주간 국내외 기사를 기반으로 생성형 AI가 작성한 것으로 KMI의 공식 의견이 아님을 밝힙니다.</div>\n'
            + '\n'.join(content_blocks) +
            '\n    <div id="sc_welcome" style="text-align:center;padding:60px;color:#7f8c8d">← 왼쪽에서 주를 선택하세요</div>\n'
            '  </div>\n'
            '</div>\n'
            f'<script>{JS}\n{MAP_JS}</script>\n'
            "<script>(function(){var a=document.querySelector('.main');if(!a)return;a.style.touchAction='pan-y';var startX=0,startValid=false,multiTouch=false;a.addEventListener('touchstart',function(e){startValid=false;var t=e.target;if(t.closest&&(t.closest('.map-section')||t.closest('.leaflet-control')))return;if(e.touches.length>1){multiTouch=true;return;}multiTouch=false;startX=e.changedTouches[0].clientX;startValid=true;},{passive:true});a.addEventListener('touchend',function(e){if(!startValid||multiTouch){multiTouch=false;return;}var t=e.target;if(t.closest&&(t.closest('.map-section')||t.closest('.leaflet-control'))){startValid=false;return;}if(e.touches.length>0)return;var dx=startX-e.changedTouches[0].clientX;if(Math.abs(dx)<50)return;startValid=false;var cur=document.querySelector('input[type=radio][hidden]:checked');if(!cur)return;var all=Array.from(document.querySelectorAll('input[type=radio][hidden][name=\"'+cur.name+'\"]'));var idx=all.indexOf(cur);var nxt=dx>0?idx+1:idx-1;if(nxt>=0&&nxt<all.length){var lbl=document.querySelector('label[for=\"'+all[nxt].id+'\"]');if(lbl){lbl.click();window.scrollTo(0,0);}}},{passive:true});}());</script>\n"
            "<script>document.addEventListener('contextmenu',function(e){e.preventDefault();});document.addEventListener('keydown',function(e){if((e.ctrlKey||e.metaKey)&&(e.key==='s'||e.key==='u')){e.preventDefault();}});</script>\n"
            f'<script>{_PDF_JS}</script>\n'
            '</body>\n</html>'
        )
        with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
            f.write(overall_html)
        size_kb = os.path.getsize(OUTPUT_HTML) // 1024
        print(f"✅ {OUTPUT_HTML} 생성 완료 ({size_kb} KB)")
        return OUTPUT_HTML

    print("⚠ step8_generate_html: scenario_json 비어있어 HTML 생성 스킵")
    return None


# ══════════════════════════════════════════════════════════════
# STEP 9: 이메일 첨부용 스테이징
# (실제 발송은 send_weekly_email.py 담당 — 이 함수는 파일 준비만 수행)
# ══════════════════════════════════════════════════════════════

def step9_prepare_email(html_path, json_path, week_tag):
    """생성된 HTML/JSON 리포트를 이메일 첨부용 스테이징 디렉토리로 복사.

    실제 이메일 발송(SMTP 등)은 scripts/send_weekly_email.py가 담당하며,
    이 함수는 발송 스크립트가 참조할 고정 경로에 최신 산출물을 준비해두는
    역할만 수행한다.

    Returns:
        dict: {"html": 복사된 html 경로, "json": 복사된 json 경로,
               "staging_dir": 스테이징 디렉토리 경로} 또는
              html_path/json_path가 없으면 None
    """
    import shutil

    print(f"\n{'='*60}")
    print("  STEP 9: 이메일 첨부용 스테이징")
    print(f"{'='*60}")

    if not html_path or not os.path.exists(html_path):
        print(f"  ⚠ HTML 파일 없음 ({html_path}) → 이메일 스테이징 스킵")
        return None
    if not json_path or not os.path.exists(json_path):
        print(f"  ⚠ JSON 파일 없음 ({json_path}) → 이메일 스테이징 스킵")
        return None

    staging_dir = BASE_DIR / "email_staging" / week_tag
    os.makedirs(staging_dir, exist_ok=True)

    html_dst = staging_dir / "weekly_report.html"
    json_dst = staging_dir / "scenario_results.json"
    shutil.copy2(html_path, html_dst)
    shutil.copy2(json_path, json_dst)

    print(f"  ✓ HTML → {html_dst}")
    print(f"  ✓ JSON → {json_dst}")

    return {
        "html": str(html_dst),
        "json": str(json_dst),
        "staging_dir": str(staging_dir),
    }


if __name__ == '__main__':
    _week_tag_arg = sys.argv[1] if len(sys.argv) > 1 else None
    main(_week_tag_arg)

