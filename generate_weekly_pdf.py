"""
Cell 12: 주간 보고서 PDF 생성 (v10)
scenario_results.json → docs/pdf/KMI_Global_SC_AI_Weekly_Report(YYYY.MM.DD).pdf

HormuzTracker SitRep 스타일의 깔끔한 보고서 문서.
html2canvas 스크린샷이 아니라 Python으로 직접 레이아웃을 구성합니다.
"""

import json, os, re, sys
from pathlib import Path
from datetime import datetime

# ── fpdf2 설치 확인 ──
try:
    from fpdf import FPDF
except ImportError:
    print("fpdf2 설치 중...")
    os.system(f"{sys.executable} -m pip install fpdf2 -q")
    from fpdf import FPDF

# ══════════════════════════════════════════════════════════════
# 0. 설정
# ══════════════════════════════════════════════════════════════
RESULT_FILE = 'scenario_results.json'
OUTPUT_DIR  = 'docs/pdf'
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 생성할 주 선택
GENERATE_LAST_N = None   # 예: 5 → 최신 5주만, None → GENERATE_FROM 이후 전체
GENERATE_FROM = '2026-04-20'  # 이 날짜 이후 주만 생성 (4/20 공개 시작)

# ══════════════════════════════════════════════════════════════
# 1. 한국어 폰트 자동 탐색
# ══════════════════════════════════════════════════════════════
def find_korean_font():
    """시스템에서 한국어 TTF 폰트를 찾아 (regular, bold) 경로 반환.

    주의: .ttc (TrueType Collection) 파일은 Adobe에서는 정상이지만
    브라우저 PDF 뷰어에서 글자가 깨지므로, .ttf 파일을 우선 사용합니다.
    프로젝트 assets/fonts/ 에 번들된 NotoSansKR을 최우선으로 사용합니다.
    """
    import platform

    # ── 0. 프로젝트 번들 폰트 (최우선 — .ttf, 브라우저 호환 보장) ──
    script_dir = os.path.dirname(os.path.abspath(__file__))
    bundled_reg = os.path.join(script_dir, 'assets', 'fonts', 'NotoSansKR-Regular.ttf')
    bundled_bold = os.path.join(script_dir, 'assets', 'fonts', 'NotoSansKR-Bold.ttf')
    if os.path.exists(bundled_reg):
        bold_path = bundled_bold if os.path.exists(bundled_bold) else bundled_reg
        return bundled_reg, bold_path

    # ── 1. 시스템 TTF 폰트 (TTC 제외 — 브라우저 호환성) ──
    candidates_ttf = []
    sys_name = platform.system()

    if sys_name == 'Darwin':  # macOS
        candidates_ttf = [
            ('/Library/Fonts/NotoSansKR-Regular.ttf', '/Library/Fonts/NotoSansKR-Bold.ttf'),
            (os.path.expanduser('~/Library/Fonts/NotoSansKR-Regular.ttf'),
             os.path.expanduser('~/Library/Fonts/NotoSansKR-Bold.ttf')),
            ('/Library/Fonts/NanumGothic.ttf', '/Library/Fonts/NanumGothicBold.ttf'),
            (os.path.expanduser('~/Library/Fonts/NanumGothic.ttf'),
             os.path.expanduser('~/Library/Fonts/NanumGothicBold.ttf')),
            ('/Library/Fonts/malgun.ttf', '/Library/Fonts/malgunbd.ttf'),
        ]
    elif sys_name == 'Windows':
        windir = os.environ.get('WINDIR', 'C:\\Windows')
        candidates_ttf = [
            (f'{windir}\\Fonts\\malgun.ttf', f'{windir}\\Fonts\\malgunbd.ttf'),
            (f'{windir}\\Fonts\\NotoSansKR-Regular.ttf', f'{windir}\\Fonts\\NotoSansKR-Bold.ttf'),
            (f'{windir}\\Fonts\\NanumGothic.ttf', f'{windir}\\Fonts\\NanumGothicBold.ttf'),
        ]
    else:  # Linux
        candidates_ttf = [
            ('/usr/share/fonts/truetype/nanum/NanumGothic.ttf', '/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf'),
        ]

    # matplotlib 폰트 경로도 시도 (TTF만)
    try:
        import matplotlib.font_manager as fm
        for f in fm.findSystemFonts():
            if not f.lower().endswith('.ttf'):
                continue  # .ttc 제외
            fname = os.path.basename(f).lower()
            if any(k in fname for k in ['notosanskr', 'nanum', 'malgun']):
                if 'bold' not in fname:
                    bold = f.replace('Regular', 'Bold').replace('regular', 'bold')
                    candidates_ttf.append((f, bold if os.path.exists(bold) else None))
    except Exception:
        pass

    for reg, bold in candidates_ttf:
        if os.path.exists(reg):
            bold_path = bold if (bold and os.path.exists(bold)) else reg
            return reg, bold_path

    # ── 2. Fallback: TTC 허용 (Adobe에서만 정상, 브라우저 깨짐 경고) ──
    candidates_ttc = []
    if sys_name == 'Darwin':
        candidates_ttc = [
            ('/System/Library/Fonts/Supplemental/AppleSDGothicNeo.ttc', None),
            ('/System/Library/Fonts/AppleSDGothicNeo.ttc', None),
        ]
    elif sys_name == 'Linux':
        candidates_ttc = [
            ('/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc'),
            ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'),
        ]

    for reg, bold in candidates_ttc:
        if os.path.exists(reg):
            print("⚠ TTC 폰트 사용 중 — 브라우저에서 글자가 깨질 수 있습니다.")
            print("  assets/fonts/에 NotoSansKR-Regular.ttf를 추가하세요.")
            bold_path = bold if (bold and os.path.exists(bold)) else reg
            return reg, bold_path

    return None, None


FONT_REG, FONT_BOLD = find_korean_font()
if FONT_REG:
    print(f"✅ 한국어 폰트: {os.path.basename(FONT_REG)}")
else:
    print("⚠ 한국어 폰트를 찾을 수 없습니다. NotoSansKR을 설치해 주세요.")
    print("  macOS: brew install font-noto-sans-cjk-kr")
    print("  또는: https://fonts.google.com/noto/specimen/Noto+Sans+KR 에서 다운로드")

# ══════════════════════════════════════════════════════════════
# 2. KG 표기 정리 함수 (Cell 11과 동일)
# ══════════════════════════════════════════════════════════════
_KG_NAME_MAP = {
    'CF_CrudeOil':'원유', 'CF_LNG':'LNG', 'CF_Naphtha':'나프타',
    'CF_Corn':'옥수수', 'CF_Wheat':'밀', 'CF_Coal':'석탄',
    'CF_EuroContainer':'유럽 컨테이너', 'CF_RareEarth':'희토류',
    'CF_Meat':'육류', 'CF_Petrochemicals':'석유화학',
    'KS_Energy':'에너지', 'KS_Material':'소재/화학',
    'KS_Manufacture':'제조업', 'KS_Shipping':'해운/물류',
    'KS_FoodAgri':'식품/농산물', 'KS_Construction':'건설/인프라',
    'KS_Finance':'금융', 'KS_Macro':'거시경제', 'KS_Consumer':'소비자',
}

def clean_text(text):
    if not text:
        return ''
    t = str(text)
    for kg_id, kor in _KG_NAME_MAP.items():
        t = t.replace(kg_id, kor)
    t = re.sub(r'--\[.*?\]-->', ' → ', t)
    t = re.sub(r'\([A-Za-z_][A-Za-z0-9_]*\)', '', t)
    t = re.sub(r'\bCF_([A-Za-z가-힣0-9]+)', r'\1', t)
    t = re.sub(r'\bKS_([A-Za-z가-힣0-9/]+)', r'\1', t)
    t = re.sub(r'\bCP_([A-Za-z가-힣0-9]+)', r'\1', t)
    t = re.sub(r'\bKI_([A-Za-z가-힣0-9]+)', r'\1', t)
    t = re.sub(r'[\s;]*\(?w=[\d.]+\)?', '', t)
    t = re.sub(r'\[(\d+)\]', '', t)  # 인용 번호 제거
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def clean_sector(text):
    if not text:
        return ''
    t = str(text).strip()
    m = re.match(r'^([^()]+)\([A-Za-z_][A-Za-z0-9_]*\)$', t)
    if m:
        return m.group(1).strip()
    m = re.match(r'^(?:KS_|CF_|CP_|KI_)[A-Za-z가-힣0-9]+\(([^)]+)\)$', t)
    if m:
        return m.group(1).strip()
    if t in _KG_NAME_MAP:
        return _KG_NAME_MAP[t]
    for prefix in ['KS_', 'CF_', 'CP_', 'KI_']:
        t = re.sub(r'\b' + prefix + r'([A-Za-z가-힣0-9/]+)', r'\1', t)
    return t.strip()

def extract_date(period_str):
    """period 문자열에서 YYYY.MM.DD 추출"""
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', period_str or '')
    if m:
        return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
    return period_str or '?'

def extract_week(period_str):
    m = re.search(r'W(\d{1,2})', period_str or '')
    return int(m.group(1)) if m else 0

# ══════════════════════════════════════════════════════════════
# 3. PDF 보고서 클래스
# ══════════════════════════════════════════════════════════════
_TIER_CRISIS = {1: 'Normal', 2: 'Caution', 3: 'Warning', 4: 'Crisis'}
_TIER_KR = {1: '정상', 2: '주의', 3: '경고', 4: '위기'}
_SEV_MAP = {'심각': 4, '중요': 3, '보통': 2, '미약': 1}
_DIR_MAP = {'네거티브': '▼', '포지티브': '▲', '혼합': '◆', '안정': '●'}
_CHG_MAP = {'↑': '▲', '↓': '▼', '↑↑': '▲▲', '↓↓': '▼▼', '☆': '★', '−': '—'}

# 지표 그룹 순서 및 표시할 지표 필터
_DISPLAY_GROUPS = {
    '글로벌 해운': ['SCFI', 'BDI', 'Harpex'],
    '초크포인트': [],  # CP_ 로 시작하는 것 자동
    '공급망 스트레스': ['GSCSI', 'RWI_ISL_CTI', 'GSCPI', 'NAPMSDI', 'GPR'],
    '에너지': ['Brent', 'WTI', 'NatGas', 'Gold'],
    '거시경제': ['KOSPI', 'KRWUSD', 'USD_Index', 'VIX', 'KR_ExportVol'],
    '한국 산업 ETF': [],  # ETF로 끝나는 것 자동
}

# 주식종목 제외 목록
_STOCK_NAMES = {
    'SK이노베이션', 'S_Oil', '롯데케미칼', 'LG화학', '한화솔루션',
    'HMM', '팬오션', '한국가스공사', '대한항공', 'CJ제일제당', '농심',
}


class WeeklyReportPDF(FPDF):
    """주간 보고서 PDF — 전문 보고서 스타일"""

    # ── 디자인 컬러 팔레트 ──
    C_NAVY      = (26, 42, 58)       # 타이틀, 주요 강조
    C_DARK_BLUE = (44, 62, 80)       # 섹션 제목 텍스트
    C_ACCENT    = (41, 128, 185)     # 액센트 바, 링크 컬러
    C_LIGHT_BG  = (236, 240, 241)    # 섹션 헤더 배경
    C_TABLE_HDR = (44, 62, 80)       # 테이블 헤더 배경 (네이비)
    C_TABLE_ALT = (245, 247, 250)    # 테이블 교대행 배경
    C_BODY_TEXT = (51, 51, 51)       # 본문 텍스트
    C_SUB_TEXT  = (100, 100, 100)    # 부제, 날짜 등
    C_BORDER    = (189, 195, 199)    # 테이블 보더
    C_WHITE     = (255, 255, 255)

    def __init__(self, font_reg, font_bold):
        super().__init__(orientation='P', unit='mm', format='A4')
        self.set_auto_page_break(auto=True, margin=22)

        # 폰트 등록
        self.add_font('KR', '', font_reg)
        self.add_font('KR', 'B', font_bold)
        self._font_reg = font_reg
        self._font_bold = font_bold
        self._is_first_page = True

    def header(self):
        if not self._is_first_page:
            # 2페이지 이후: 상단에 얇은 네이비 바 + 보고서명
            self.set_fill_color(*self.C_NAVY)
            self.rect(0, 0, self.w, 10, 'F')
            self.set_font('KR', 'B', 8)
            self.set_text_color(*self.C_WHITE)
            self.set_y(2.5)
            self.cell(0, 5, '글로벌 공급망 AI 주간 모니터링', align='C')
            self.set_y(12)

    def footer(self):
        self.set_y(-18)
        # 구분선
        self.set_draw_color(*self.C_BORDER)
        self.set_line_width(0.3)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)
        self.set_font('KR', '', 6.5)
        self.set_text_color(140, 140, 140)
        self.cell(0, 4,
            'Source: KMI (scm-briefing.kmi.re.kr) | 본 리포트는 온톨로지 기반 전문가 지식 그래프와 AI가 작성한 것으로 KMI의 공식 의견이 아닙니다.',
            align='L')
        self.set_font('KR', '', 7)
        self.set_text_color(160, 160, 160)
        self.cell(0, 4, f'Page {self.page_no()}/{{nb}}', align='R', new_x='LMARGIN', new_y='NEXT')

    def _draw_title_block(self, date_str, week_num):
        """문서 헤더: 진한 네이비 배경 타이틀 바 + KMI 로고"""
        # 네이비 배경 바
        bar_h = 28
        self.set_fill_color(*self.C_NAVY)
        self.rect(0, 0, self.w, bar_h, 'F')

        # KMI 로고 (타이틀 왼쪽, 작게)
        logo_path = os.path.join(os.path.dirname(os.path.abspath('__file__')), 'assets', 'kmi_logo_white.png')
        logo_w_mm = 0
        if os.path.exists(logo_path):
            logo_h = 9  # mm (타이틀 글자 높이와 맞춤)
            logo_w_mm = logo_h * 938 / 709  # 원본 비율 유지
            logo_x = self.l_margin + 2
            logo_y = 7.5  # 타이틀 글자 상단과 맞춤
            self.image(logo_path, x=logo_x, y=logo_y, w=logo_w_mm, h=logo_h)

        # 타이틀 텍스트 (흰색, 로고 오른쪽)
        title_indent = logo_w_mm + 5 if logo_w_mm else 2
        self.set_y(6)
        self.set_font('KR', 'B', 20)
        self.set_text_color(*self.C_WHITE)
        self.set_x(self.l_margin + title_indent)
        self.cell(0, 9, '글로벌 공급망 AI 주간 모니터링', new_x='LMARGIN', new_y='NEXT')

        self.set_font('KR', '', 9)
        self.set_text_color(180, 200, 220)
        self.set_x(self.l_margin + title_indent)
        self.cell(0, 5, f'{date_str}  (Week {week_num:02d})', new_x='LMARGIN', new_y='NEXT')

        # 네이비 바 아래 액센트 라인
        self.set_fill_color(*self.C_ACCENT)
        self.rect(0, bar_h, self.w, 1.2, 'F')
        self.set_y(bar_h + 4)

        # 기관 정보 (네이비 바 아래)
        self.set_font('KR', '', 8)
        self.set_text_color(*self.C_SUB_TEXT)
        self.cell(0, 4, 'https://scm-briefing.kmi.re.kr  |  한국해양수산개발원(KMI) 해양수산 AX 지원단  |  hmjeon@kmi.re.kr',
                  new_x='LMARGIN', new_y='NEXT')
        self.ln(3)
        self._is_first_page = False

    def _section_title(self, num, title):
        """번호 붙은 섹션 제목 — 좌측 액센트 바 + 배경"""
        # 페이지 넘김 체크
        if self.get_y() + 12 > self.h - 25:
            self.add_page()

        y_top = self.get_y()
        h = 8

        # 밝은 배경
        self.set_fill_color(*self.C_LIGHT_BG)
        self.rect(self.l_margin, y_top, self.w - self.l_margin - self.r_margin, h, 'F')

        # 좌측 액센트 바 (파란색, 두꺼운 세로줄)
        self.set_fill_color(*self.C_ACCENT)
        self.rect(self.l_margin, y_top, 1.5, h, 'F')

        # 텍스트
        self.set_font('KR', 'B', 12)
        self.set_text_color(*self.C_DARK_BLUE)
        self.set_xy(self.l_margin + 4, y_top + 1)
        self.cell(0, 6, f'{num}. {title}')
        self.set_y(y_top + h + 3)

    def _body_text(self, text, size=9):
        self.set_font('KR', '', size)
        self.set_text_color(*self.C_BODY_TEXT)
        self.multi_cell(0, 4.8, text)
        self.ln(1.5)

    def _bullet(self, text, size=9, indent=5):
        self.set_font('KR', '', size)
        self.set_text_color(*self.C_BODY_TEXT)
        x = self.get_x()
        self.set_x(x + indent)
        self.set_text_color(*self.C_ACCENT)
        self.cell(4, 4.8, '•')
        self.set_text_color(*self.C_BODY_TEXT)
        self.multi_cell(0, 4.8, text)
        self.ln(0.8)

    def _sub_heading(self, text):
        """서브 헤딩 (그룹명 등)"""
        self.set_font('KR', 'B', 9)
        self.set_text_color(*self.C_ACCENT)
        self.cell(3, 5, '')
        self.set_fill_color(*self.C_ACCENT)
        y_mid = self.get_y() + 2.5
        # 작은 사각형 불릿
        self.rect(self.l_margin + 1, y_mid - 1, 2, 2, 'F')
        self.set_x(self.l_margin + 5)
        self.set_text_color(*self.C_DARK_BLUE)
        self.cell(0, 5, text, new_x='LMARGIN', new_y='NEXT')
        self.ln(0.5)

    def _mini_table(self, headers, rows, col_widths=None, header_bg=None):
        """디자인 테이블 — 네이비 헤더 + zebra 행"""
        if header_bg is None:
            header_bg = self.C_TABLE_HDR

        if not col_widths:
            usable = self.w - self.l_margin - self.r_margin
            col_widths = [usable / len(headers)] * len(headers)

        row_h = 5.5
        hdr_h = 6.5

        # 페이지 넘김 체크 — 헤더 + 최소 2행
        needed = hdr_h + row_h * min(3, len(rows))
        if self.get_y() + needed > self.h - 25:
            self.add_page()

        def _draw_header():
            self.set_font('KR', 'B', 8)
            self.set_fill_color(*header_bg)
            self.set_text_color(*self.C_WHITE)
            self.set_draw_color(*header_bg)
            for i, h in enumerate(headers):
                self.cell(col_widths[i], hdr_h, h, border=0, fill=True, align='C')
            self.ln()

        _draw_header()

        # Rows — zebra striping
        self.set_font('KR', '', 8)
        for row_idx, row in enumerate(rows):
            # 페이지 넘김 체크
            if self.get_y() + row_h > self.h - 25:
                self.add_page()
                _draw_header()
                self.set_font('KR', '', 8)

            # 교대 배경
            if row_idx % 2 == 1:
                self.set_fill_color(*self.C_TABLE_ALT)
                fill = True
            else:
                self.set_fill_color(*self.C_WHITE)
                fill = True

            self.set_text_color(*self.C_BODY_TEXT)
            self.set_draw_color(*self.C_BORDER)

            for i, val in enumerate(row):
                align = 'L' if i == 0 else 'C'
                txt = str(val)[:60] if len(str(val)) > 60 else str(val)
                # 하단 보더만
                x_before = self.get_x()
                self.cell(col_widths[i], row_h, txt, border='B', align=align, fill=fill)
            self.ln()
        self.ln(3)


def generate_pdf(scenario, font_reg, font_bold):
    """단일 주의 PDF 보고서 생성"""

    period = scenario.get('period', '?')
    date_str = extract_date(period)
    week_num = extract_week(period)
    tier = scenario.get('tier', 1)
    tier_label = scenario.get('tier_label', '?')
    header = scenario.get('header', {})
    sig = scenario.get('signal', {})
    indicators = scenario.get('indicators', {})
    skipped = scenario.get('skipped', False)

    if skipped:
        return None

    pdf = WeeklyReportPDF(font_reg, font_bold)
    pdf.alias_nb_pages()
    pdf.add_page()

    # ── 문서 헤더 (네이비 타이틀 바) ──
    pdf._draw_title_block(date_str, week_num)

    # ══════════════════════════════════════════════════
    # 1. 상황 요약
    # ══════════════════════════════════════════════════
    pdf._section_title(1, '상황 요약')

    # Tier 배지 (컬러 박스)
    crisis = _TIER_CRISIS.get(tier, '?')
    crisis_kr = _TIER_KR.get(tier, '?')
    tier_colors = {1: (39,174,96), 2: (41,128,185), 3: (230,126,34), 4: (192,57,43)}
    tc = tier_colors.get(tier, (100,100,100))

    # 배지 배경 박스
    badge_text = f' Tier {tier} — {crisis} ({crisis_kr}) '
    pdf.set_font('KR', 'B', 11)
    badge_w = pdf.get_string_width(badge_text) + 8
    badge_h = 7.5
    y_badge = pdf.get_y()
    pdf.set_fill_color(*tc)
    pdf.set_draw_color(*tc)
    # 둥근 느낌의 배지 (rect로 대체)
    pdf.rect(pdf.l_margin, y_badge, badge_w, badge_h, 'F')
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(pdf.l_margin + 1, y_badge + 0.5)
    pdf.cell(badge_w - 2, badge_h - 1, badge_text, align='C')

    # Tier 라벨 (배지 옆)
    pdf.set_xy(pdf.l_margin + badge_w + 3, y_badge + 1)
    pdf.set_font('KR', 'B', 10)
    pdf.set_text_color(*tc)
    pdf.cell(0, 5, tier_label)
    pdf.set_y(y_badge + badge_h + 3)

    # Signal 바 (컬러 프로그레스 스타일)
    crisis_pct = sig.get('crisis_pct', 0)
    warning_pct = sig.get('warning_pct', 0)
    try:
        crisis_pct_val = float(crisis_pct)
        warning_pct_val = float(warning_pct)
    except (ValueError, TypeError):
        crisis_pct_val = 0
        warning_pct_val = 0

    bar_total_w = 80
    bar_h = 4
    y_bar = pdf.get_y()
    x_bar = pdf.l_margin

    # 배경 (회색)
    pdf.set_fill_color(230, 230, 230)
    pdf.rect(x_bar, y_bar, bar_total_w, bar_h, 'F')
    # Warning (주황)
    if warning_pct_val > 0:
        pdf.set_fill_color(230, 126, 34)
        pdf.rect(x_bar, y_bar, bar_total_w * min((warning_pct_val + crisis_pct_val) / 100, 1), bar_h, 'F')
    # Crisis (빨강)
    if crisis_pct_val > 0:
        pdf.set_fill_color(192, 57, 43)
        pdf.rect(x_bar, y_bar, bar_total_w * min(crisis_pct_val / 100, 1), bar_h, 'F')

    # 시그널 텍스트 (바 옆)
    pdf.set_xy(x_bar + bar_total_w + 3, y_bar - 0.5)
    pdf.set_font('KR', '', 8)
    pdf.set_text_color(80, 80, 80)
    sig_text = (
        f"Crisis {crisis_pct}% | Warning {warning_pct}% | "
        f"합산 {sig.get('warning_crisis_pct','?')}% | "
        f"기사 {sig.get('n_articles','?')}건 | {sig.get('trend','-')}"
    )
    pdf.cell(0, 5, sig_text)
    pdf.set_y(y_bar + bar_h + 3)

    # Summary text
    summary = header.get('situation_summary', '')
    if summary:
        summary_clean = clean_text(summary)
        # 문장 분리
        sentences = re.split(r'(?<=[다음임함짐됨]\.)(\s+)', summary_clean)
        sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 5]
        for sent in sentences:
            pdf._bullet(sent, size=9)
    pdf.ln(2)

    # ══════════════════════════════════════════════════
    # 2. 전주 대비 주요 변화
    # ══════════════════════════════════════════════════
    changes = [c for c in header.get('changes_from_prev', [])
               if c.get('item', '') not in _STOCK_NAMES]
    if changes:
        pdf._section_title(2, '전주 대비 주요 변화')
        # 테이블: 항목/변화/이전/현재만 (상세는 아래 본문으로)
        headers_chg = ['항목', '변화', '이전', '현재']
        widths_chg = [35, 15, 45, 45]
        rows_chg = []
        for c in changes:
            chg_sym = _CHG_MAP.get(c.get('change', ''), c.get('change', ''))
            rows_chg.append([
                c.get('item', ''),
                chg_sym,
                c.get('from', ''),
                c.get('to', ''),
            ])
        pdf._mini_table(headers_chg, rows_chg, widths_chg)

        # 상세 내역은 본문 형태로
        for c in changes:
            detail = clean_text(c.get('detail', ''))
            if detail:
                pdf.set_font('KR', 'B', 8)
                pdf.set_text_color(44, 62, 80)
                item_name = c.get('item', '')
                chg_sym = _CHG_MAP.get(c.get('change', ''), '')
                pdf.cell(30, 4, f'{chg_sym} {item_name}:', new_x='END')
                pdf.set_font('KR', '', 8)
                pdf.set_text_color(80, 80, 80)
                pdf.multi_cell(0, 4, detail)
                pdf.ln(0.5)

    # ══════════════════════════════════════════════════
    # 3. 주요 지표
    # ══════════════════════════════════════════════════
    pdf._section_title(3, '주요 지표')

    # 지표를 그룹별로 분류
    ind_by_group = {}
    for ind_key, ind_data in indicators.items():
        if isinstance(ind_data, dict):
            grp = ind_data.get('group', '기타')
            name = ind_data.get('name', ind_key)
            if name in _STOCK_NAMES:
                continue
            ind_by_group.setdefault(grp, []).append(ind_data)

    group_order = ['글로벌 해운', '초크포인트', '공급망 스트레스', '에너지',
                   '거시경제', '한국 산업 ETF']

    for grp in group_order:
        items = ind_by_group.get(grp, [])
        if not items:
            # 다른 이름으로 매칭 시도
            for k, v in ind_by_group.items():
                if grp in k:
                    items = v
                    break
        if not items:
            continue

        pdf._sub_heading(grp)

        headers_ind = ['지표', '값', '전주비(%)', '방향', '기준일']
        widths_ind = [35, 25, 20, 15, 25]
        rows_ind = []
        for it in items:
            val = it.get('value', 'N/A')
            if isinstance(val, (int, float)):
                if val != val:  # NaN
                    val = 'N/A'
                elif abs(val) >= 1000:
                    val = f'{val:,.1f}'
                else:
                    val = f'{val:.2f}'
            chg = it.get('chg_pct', '')
            if isinstance(chg, (int, float)) and chg == chg:
                chg_str = f'{chg:+.1f}'
            else:
                chg_str = str(chg) if chg else '—'
            direction = '▲' if it.get('chg_dir') == 'up' else ('▼' if it.get('chg_dir') == 'down' else '—')
            data_date = it.get('data_date', '')
            rows_ind.append([
                it.get('full', it.get('name', '?'))[:25],
                str(val),
                chg_str,
                direction,
                str(data_date)[:10]
            ])
        pdf._mini_table(headers_ind, rows_ind, widths_ind)

    # ══════════════════════════════════════════════════
    # 4. 국제→한국 전파경로
    # ══════════════════════════════════════════════════
    routes = scenario.get('part_a', {}).get('routes', [])
    if routes:
        pdf._section_title(4, '국제 → 한국 전파경로')
        for r in routes:
            status = r.get('status', '')
            commodity = clean_text(r.get('commodity', ''))
            path = clean_text(r.get('path', ''))
            is_new = r.get('is_new', False)
            d_type = r.get('disruption_type', '')
            dt_map = {'ROUTE': '경로위기', 'SOURCE': '공급원위기', 'LOGISTICS': '물류위기'}
            dt_label = dt_map.get(d_type, d_type)

            new_mark = ' [신규]' if (is_new or status == '신규활성') else ''

            # 상태 배지 (작은 컬러 박스)
            sc = {'활성': (39,174,96), '신규활성': (230,126,34), '비활성': (127,140,141)}
            status_color = sc.get(status, (100,100,100))
            badge_y = pdf.get_y()
            badge_x = pdf.l_margin

            # 상태 배지 배경
            pdf.set_font('KR', 'B', 7)
            status_w = pdf.get_string_width(status) + 4
            pdf.set_fill_color(*status_color)
            pdf.rect(badge_x, badge_y + 0.5, status_w, 4.5, 'F')
            pdf.set_text_color(255, 255, 255)
            pdf.set_xy(badge_x, badge_y + 0.3)
            pdf.cell(status_w, 5, f' {status} ', align='C')

            # commodity + type 텍스트
            pdf.set_xy(badge_x + status_w + 2, badge_y)
            pdf.set_font('KR', 'B', 9)
            pdf.set_text_color(*pdf.C_DARK_BLUE)
            pdf.cell(0, 5, f'{commodity} — {dt_label}{new_mark}', new_x='LMARGIN', new_y='NEXT')

            pdf.set_font('KR', '', 8)
            pdf.set_text_color(100, 100, 100)
            if path:
                pdf.set_x(pdf.l_margin + 3)
                pdf.multi_cell(0, 4, f'  {path}')
            pdf.ln(1.5)

    # ══════════════════════════════════════════════════
    # 5. 산업별 영향 매트릭스
    # ══════════════════════════════════════════════════
    matrix = scenario.get('part_d', {}).get('matrix', [])
    if matrix:
        pdf._section_title(5, '산업별 영향 매트릭스')
        headers_m = ['산업', '방향', '초기', '중기', '장기', '변화']
        widths_m = [30, 18, 18, 18, 18, 12]
        rows_m = []
        for row in matrix:
            dir_sym = _DIR_MAP.get(row.get('direction', ''), '?')
            chg_sym = _CHG_MAP.get(row.get('change', ''), row.get('change', '—'))
            rows_m.append([
                clean_sector(row.get('sector', '?'))[:20],
                f"{dir_sym} {row.get('direction', '')}",
                row.get('initial', '?'),
                row.get('mid', '?'),
                row.get('long', '?'),
                chg_sym
            ])
        pdf._mini_table(headers_m, rows_m, widths_m)

        # 전파경로 상세 (매트릭스 아래)
        pdf._sub_heading('산업별 전파경로 상세')
        for row in matrix:
            pathway = clean_text(row.get('pathway', ''))
            if pathway:
                sector = clean_sector(row.get('sector', '?'))
                pdf.set_font('KR', 'B', 8)
                pdf.set_text_color(*pdf.C_ACCENT)
                pdf.cell(0, 4, f'{sector}:', new_x='LMARGIN', new_y='NEXT')
                pdf.set_font('KR', '', 8)
                pdf.set_text_color(80, 80, 80)
                pdf.set_x(pdf.l_margin + 3)
                pdf.multi_cell(0, 3.8, pathway)
                pdf.ln(1)

    # ══════════════════════════════════════════════════
    # 6. 취약점 진단 및 모니터링 권고
    # ══════════════════════════════════════════════════
    part_e = scenario.get('part_e', {})
    vulns = part_e.get('vulnerabilities', [])
    recs = part_e.get('monitoring_recommendations', [])
    wps = header.get('watchpoints', [])

    if vulns or recs or wps:
        pdf._section_title(6, '취약점 진단 및 모니터링 권고')

        if wps:
            pdf._sub_heading('향후 주시 포인트')
            for w in wps:
                horizon = w.get('horizon', '')
                point = clean_text(w.get('point', ''))
                pdf._bullet(f'[{horizon}] {point}', size=8, indent=5)
            pdf.ln(2)

        if vulns:
            pdf._sub_heading('공급망 취약점')
            for v in vulns:
                pdf._bullet(clean_text(v), size=8, indent=5)
            pdf.ln(2)

        if recs:
            pdf._sub_heading('모니터링 권고')
            for r in recs:
                pdf._bullet(clean_text(r), size=8, indent=5)

    # ── 파일 저장 ──
    fname = f'KMI_Global_SC_AI_Weekly_Report({date_str}).pdf'
    fpath = os.path.join(OUTPUT_DIR, fname)
    pdf.output(fpath)
    return fpath


# ══════════════════════════════════════════════════════════════
# 4. 실행
# ══════════════════════════════════════════════════════════════
if not FONT_REG:
    print("❌ 한국어 폰트 없이는 PDF를 생성할 수 없습니다.")
else:
    with open(RESULT_FILE, encoding='utf-8') as f:
        all_scenarios = json.load(f)

    # 스킵된 주 제외
    valid = [s for s in all_scenarios if not s.get('skipped', False)]
    print(f"로드: {len(valid)}주 (스킵 제외)")

    # 날짜 필터 적용
    if GENERATE_FROM:
        valid = [s for s in valid
                 if re.search(r'\d{4}-\d{2}-\d{2}', s.get('period', ''))
                 and re.search(r'\d{4}-\d{2}-\d{2}', s.get('period', '')).group() >= GENERATE_FROM]
        print(f"→ {GENERATE_FROM} 이후: {len(valid)}주")

    if GENERATE_LAST_N:
        targets = valid[-GENERATE_LAST_N:]
        print(f"→ 최신 {GENERATE_LAST_N}주만 생성")
    else:
        targets = valid
        print(f"→ 대상: {len(targets)}주")

    success = 0
    errors = []
    for s in targets:
        period = s.get('period', '?')
        try:
            fpath = generate_pdf(s, FONT_REG, FONT_BOLD)
            if fpath:
                success += 1
                print(f"  ✅ {os.path.basename(fpath)}")
        except Exception as e:
            errors.append((period, str(e)))
            print(f"  ❌ {period}: {e}")

    print(f"\n{'='*50}")
    print(f"완료: {success}/{len(targets)}주 PDF 생성")
    if errors:
        print(f"오류: {len(errors)}건")
        for p, e in errors:
            print(f"  {p}: {e}")
    print(f"출력 폴더: {OUTPUT_DIR}/")
