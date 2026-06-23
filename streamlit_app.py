"""
国家统计局新版数据接口查询平台 — Streamlit 版
==========================================
基于公开 API V2.0 (2026.03.27) 构建 | 无需鉴权，公开访问
原始 HTML 版本作者: Charles | Streamlit 移植版

API 基址: https://data.stats.gov.cn/dg/website/publicrelease/web/external
"""

import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# ================================================================
# 配置
# ================================================================
BASE_URL = "https://data.stats.gov.cn/dg/website/publicrelease/web/external"
ROOT_ID_MONTHLY = "fc982599aa684be7969d7b90b1bd0e84"

# 分类代码映射
CATEGORY_MAP = {
    "月度数据": "1",
    "季度数据": "2",
    "年度数据": "3",
    "分省季度": "5",
    "分省年度": "6",
    "其他/普查": "7",
}

# 省级行政区划代码（12位编码）
PROVINCE_CODES = {
    "110000000000": "北京", "120000000000": "天津", "130000000000": "河北",
    "140000000000": "山西", "150000000000": "内蒙古", "210000000000": "辽宁",
    "220000000000": "吉林", "230000000000": "黑龙江", "310000000000": "上海",
    "320000000000": "江苏", "330000000000": "浙江", "340000000000": "安徽",
    "350000000000": "福建", "360000000000": "江西", "370000000000": "山东",
    "410000000000": "河南", "420000000000": "湖北", "430000000000": "湖南",
    "440000000000": "广东", "450000000000": "广西", "460000000000": "海南",
    "500000000000": "重庆", "510000000000": "四川", "520000000000": "贵州",
    "530000000000": "云南", "540000000000": "西藏", "610000000000": "陕西",
    "620000000000": "甘肃", "630000000000": "青海", "640000000000": "宁夏",
    "650000000000": "新疆",
}

PROVINCE_CATEGORY_CODES = {"5", "6"}


# ================================================================
# API 调用函数（带 requests 缓存 / 重试）
# ================================================================

# 统一的浏览器模拟请求头
_BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://data.stats.gov.cn",
    "Referer": "https://data.stats.gov.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _api_get(url: str, params: dict = None) -> dict:
    """统一的 GET 请求封装"""
    resp = requests.get(url, params=params, headers=_BROWSER_HEADERS, timeout=30)
    try:
        return resp.json()
    except ValueError:
        snippet = resp.text[:200].replace("\n", " ").strip()
        raise RuntimeError(
            f"API 返回格式异常（状态码 {resp.status_code}），"
            f"请检查网络连接后重试。\n响应内容：{snippet}"
        )


@st.cache_data(ttl=86400, show_spinner="正在加载分类树...")
def query_tree(pid: str = "", code: str = "1") -> list:
    """查询指标分类树"""
    url = f"{BASE_URL}/new/queryIndexTreeAsync"
    params = {"pid": pid, "code": code}
    for attempt in range(2):
        try:
            result = _api_get(url, params)
            return result.get("data", [])
        except (requests.Timeout, requests.ConnectionError, RuntimeError) as e:
            if attempt == 0:
                time.sleep(2)
                continue
            raise


@st.cache_data(ttl=3600, show_spinner="正在获取指标列表...")
def query_indicators(cid: str) -> list:
    """根据数据集 ID 查询指标列表"""
    url = f"{BASE_URL}/new/queryIndicatorsByCid"
    params = {"cid": cid}
    for attempt in range(2):
        try:
            result = _api_get(url, params)
            return result.get("data", {}).get("list", [])
        except (requests.Timeout, requests.ConnectionError, RuntimeError) as e:
            if attempt == 0:
                time.sleep(2)
                continue
            raise


def _post_data(payload: dict) -> list:
    """通用 POST 请求封装（含重试、错误处理）"""
    headers = {**_BROWSER_HEADERS, "Content-Type": "application/json"}
    url = f"{BASE_URL}/stream/esData"

    for attempt in range(2):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp.status_code == 404:
                raise RuntimeError(
                    "数据查询接口返回 404，可能原因：\n"
                    "1. 当前网络环境无法访问该接口（WAF/代理拦截）\n"
                    "2. API 端点已变更\n"
                    "3. 请尝试使用国内网络环境访问"
                )
            resp.raise_for_status()
            result = resp.json()
            if not result.get("success"):
                raise RuntimeError(result.get("message", "查询失败"))
            return result.get("data", [])
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 500:
                raise RuntimeError(
                    "服务器内部错误（500），通常是因为请求参数格式不正确：\n"
                    "1. 时间格式须为 YYYYMM + MM 后缀，如 202601MM\n"
                    "2. 所选指标可能与数据集不匹配\n"
                    "3. 请检查参数后重试"
                )
            raise
        except (requests.Timeout, requests.ConnectionError) as e:
            if attempt == 0:
                time.sleep(2)
                continue
            raise


def query_data(
    cid: str,
    indicator_ids: List[str],
    start_time: str,
    end_time: str,
    region: str = "000000000000",
    show_type: str = "1",
) -> list:
    """查询时间序列数据（单地区）"""
    payload = {
        "cid": cid,
        "indicatorIds": indicator_ids,
        "das": [{"text": "全国", "value": region}],
        "dts": [f"{start_time}-{end_time}"],
        "showType": show_type,
        "rootId": ROOT_ID_MONTHLY,
    }
    return _post_data(payload)


def query_data_all_provinces(
    cid: str,
    indicator_ids: List[str],
    start_time: str,
    end_time: str,
    show_type: str = "1",
) -> list:
    """分省数据查询：并发查询 31 省，合并结果并标注省份。"""
    regions = sorted(PROVINCE_CODES.items(), key=lambda x: x[1])
    all_results = []
    total = len(regions)

    progress_bar = st.progress(0.0, text="准备查询...")
    completed = 0

    def _query_one(value: str, text: str) -> list:
        """查询单个省份并标注地区"""
        payload = {
            "cid": cid,
            "indicatorIds": indicator_ids,
            "das": [{"text": text, "value": value}],
            "dts": [f"{start_time}-{end_time}"],
            "showType": show_type,
            "rootId": ROOT_ID_MONTHLY,
        }
        try:
            data = _post_data(payload)
            for item in data:
                item["reg"] = value
                item["reg_name"] = text
            return data
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(_query_one, value, text): (value, text)
            for value, text in regions
        }
        for future in as_completed(futures):
            completed += 1
            pct = completed / total
            value, text = futures[future]
            progress_bar.progress(pct, text=f"📡 查询中 {completed}/{total}：{text}")
            try:
                result = future.result()
                all_results.extend(result)
            except Exception:
                pass

    progress_bar.empty()
    return all_results


# ================================================================
# 辅助函数
# ================================================================

# 分类代码 → 时间后缀映射
TIME_SUFFIX_MAP = {
    "1": "MM",   # 月度: YYYYMM + MM
    "2": "SS",   # 季度: YYYYMM(季度编码) + SS
    "3": "YY",   # 年度: YYYY + YY
    "5": "SS",   # 分省季度
    "6": "YY",   # 分省年度
    "7": "",     # 其他/普查
}

# 时间单位显示名称
TIME_LABEL_MAP = {
    "1": "月度", "2": "季度", "3": "年度",
    "5": "季度", "6": "年度", "7": "",
}


def get_time_suffix() -> str:
    """根据当前分类获取 API 时间后缀"""
    code = st.session_state.current_category_code
    return TIME_SUFFIX_MAP.get(code, "MM")


def date_to_api_format(d: datetime.date, suffix: str = "") -> str:
    """
    将 date/datetime 转换为 API 所需的时间格式

    月度:  202602MM   (YYYYMM + MM)
    季度:  202504SS   (YYYY + 季度编码(01-04) + SS)  例: Q4=04
    年度:  2025YY     (YYYY + YY)
    """
    if not suffix:
        suffix = get_time_suffix()

    if suffix == "MM":
        return d.strftime("%Y%m") + "MM"
    elif suffix == "SS":
        # 根据月份推算季度(1-4)并编码为两位: 01,02,03,04
        q = (d.month - 1) // 3 + 1
        return f"{d.year}{q:02d}SS"
    elif suffix == "YY":
        return f"{d.year}YY"
    else:
        return d.strftime("%Y%m")


def flatten_tree_nodes(nodes: list) -> List[Dict[str, Any]]:
    """将树节点列表平铺（当前层级），供 selectbox 使用"""
    seen = set()
    result = []
    for n in nodes:
        nid = n.get("_id", "")
        if nid in seen:
            continue
        seen.add(nid)
        result.append({
            "id": nid,
            "name": n.get("name", "未知"),
            "is_leaf": n.get("isLeaf", False),
            "sdate": n.get("sdate", ""),
            "edate": n.get("edate", ""),
        })
    return result


def parse_query_data_to_df(data: list, indicators_map: dict) -> pd.DataFrame:
    """将 API 返回数据解析为 DataFrame。支持分省数据（reg_name 列）。"""
    if not data:
        return pd.DataFrame()

    # 判断是否为 32 位 hash 编码
    _is_hash = lambda s: bool(s) and len(s) >= 20 and all(c in '0123456789abcdefABCDEF' for c in s)

    has_region = "reg_name" in data[0]
    records = []

    for item in data:
        rec = {"时间": item.get("name", ""), "code": item.get("code", "")}
        if has_region:
            rec["省份"] = item.get("reg_name", "")

        for v in item.get("values", []):
            vid = v.get("_id", "")
            info = indicators_map.get(vid, {})
            col = info.get("i_showname", vid[:8])
            rec[col] = v.get("value")
            du = info.get("du", "")
            if du and not _is_hash(du):
                rec[f"{col}_unit"] = du
        records.append(rec)

    df = pd.DataFrame(records)
    if "code" in df.columns:
        df = df.sort_values("code").reset_index(drop=True)
    return df


# ================================================================
# Streamlit 页面配置
# ================================================================

st.set_page_config(
    page_title="国家统计局数据查询平台",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 自定义 CSS 样式
# st.markdown("""
# <style>
#     .stApp { background-color: #F8FAFC; }
#     .block-container { padding-top: 1rem; }
#     h1, h2, h3 { font-family: 'Fira Sans', sans-serif; }
#     .stButton > button { font-weight: 500; }
#     .highlight { background-color: #EFF6FF; border-left: 3px solid #3B82F6; }
#     .metric-card { background: white; border-radius: 0.75rem; padding: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
#     div[data-testid="stSidebar"] { background-color: white; border-right: 1px solid #E2E8F0; }
# </style>
# """, unsafe_allow_html=True)

# ================================================================
# 初始化 Session State
# ================================================================

if "selected_cid" not in st.session_state:
    st.session_state.selected_cid = None
if "selected_cid_name" not in st.session_state:
    st.session_state.selected_cid_name = ""
if "selected_indicators" not in st.session_state:
    st.session_state.selected_indicators = {}  # id -> info dict
if "current_tree" not in st.session_state:
    st.session_state.current_tree = []
if "query_df" not in st.session_state:
    st.session_state.query_df = None
if "tree_loaded" not in st.session_state:
    st.session_state.tree_loaded = False
if "is_province_data" not in st.session_state:
    st.session_state.is_province_data = False
if "tree_history" not in st.session_state:
    st.session_state.tree_history = []  # stack of (level_name, nodes) for breadcrumb
if "tree_path_names" not in st.session_state:
    st.session_state.tree_path_names = []  # breadcrumb path names
if "current_category_code" not in st.session_state:
    st.session_state.current_category_code = "1"

# ================================================================
# 标题区
# ================================================================

col_title, col_status = st.columns([3, 1])
with col_title:
    st.markdown("""
    <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem;">
        <span style="font-size: 2rem;">📊</span>
        <div>
            <h1 style="margin: 0; font-size: 1.5rem; font-weight: 700; color: #1E293B;">
                国家统计局数据查询平台
            </h1>
        </div>
    </div>
    """, unsafe_allow_html=True)

with col_status:
    st.caption(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if st.session_state.selected_cid and st.session_state.selected_cid_name:
        st.markdown(f"<span style='font-size:0.75rem;color:#3B82F6;'>📁 {st.session_state.selected_cid_name}</span>", unsafe_allow_html=True)

# ================================================================
# 侧边栏
# ================================================================

with st.sidebar:
    st.markdown("## 📂 数据导航")

    # --- 当前选中的数据集 ---
    if st.session_state.selected_cid and st.session_state.selected_cid_name:
        sel_container = st.container(border=True)
        with sel_container:
            col_a, col_b = st.columns([3, 1])
            with col_a:
                st.markdown(f"**📌 已选：** {st.session_state.selected_cid_name}")
            with col_b:
                if st.button("✕ 清除", key="clear_selection", use_container_width=True):
                    st.session_state.selected_cid = None
                    st.session_state.selected_cid_name = ""
                    st.session_state.selected_indicators = {}
                    st.rerun()
    else:
        st.info("👆 通过下方分类导航选择一个数据集")

    # --- 分类导航 ---
    with st.expander("📂 分类导航", expanded=True):
        category = st.selectbox(
            "选择分类",
            options=list(CATEGORY_MAP.keys()),
            index=0,
            label_visibility="collapsed",
            key="category_selector",
        )

        # 分类切换时自动重载根节点
        code = CATEGORY_MAP[category]
        if st.session_state.current_category_code != code:
            st.session_state.current_category_code = code
            st.session_state.tree_loaded = False
            st.session_state.current_tree = []
            st.session_state.tree_history = []
            st.session_state.tree_path_names = []
            st.rerun()

        # 自动加载根节点（首次或切换分类时）
        if not st.session_state.tree_loaded or not st.session_state.current_tree:
            with st.spinner("加载数据目录..."):
                try:
                    nodes = query_tree("", code)
                    st.session_state.current_tree = nodes
                    st.session_state.tree_loaded = True
                    st.rerun()
                except Exception as e:
                    err_msg = str(e)
                    if "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
                        st.error("⏱️ 加载超时，请稍后重试")
                    else:
                        st.error(f"加载失败：{e}")

        # 展示面包屑导航
        if st.session_state.tree_path_names:
            breadcrumb = " > ".join(st.session_state.tree_path_names)
            st.caption(f"📍 当前位置：{breadcrumb}")
        if st.session_state.selected_cid and st.session_state.selected_cid_name:
            st.caption(f"✅ 已选：{st.session_state.selected_cid_name}")

        # 展示当前层级的节点
        if st.session_state.current_tree:
            flat_options = flatten_tree_nodes(st.session_state.current_tree)
            option_labels = []
            for r in flat_options:
                icon = "📄" if r["is_leaf"] else "📁"
                timerange = f"({r['sdate']}-{r['edate']})" if r["sdate"] or r["edate"] else ""
                label = f"{icon} {r['name']} {timerange}".strip()
                option_labels.append(label)

            option_ids = [r["id"] for r in flat_options]
            option_is_leaf = [r["is_leaf"] for r in flat_options]

            # 标记已选中的节点
            selected_cid_ss = st.session_state.selected_cid
            option_labels_marked = []
            for i, r in enumerate(flat_options):
                lbl = option_labels[i]
                if option_is_leaf[i] and r["id"] == selected_cid_ss:
                    lbl += " ✅"
                option_labels_marked.append(lbl)

            selected_idx = st.selectbox(
                "选择数据目录",
                options=range(len(flat_options)),
                format_func=lambda i: option_labels_marked[i],
                label_visibility="collapsed",
                placeholder="请选择...",
                key="tree_selectbox",
            )

            # 展示操作按钮
            col_a, col_b = st.columns([3, 1])
            with col_a:
                if option_is_leaf[selected_idx]:
                    # 数据集 → 选它
                    leaf = flat_options[selected_idx]
                    already = (st.session_state.selected_cid == leaf["id"])
                    if already:
                        st.button("✅ 已选中", key="select_leaf", use_container_width=True, disabled=True)
                    else:
                        if st.button("📄 选此数据集", key="select_leaf", use_container_width=True):
                            st.session_state.selected_cid = leaf["id"]
                            st.session_state.selected_cid_name = leaf["name"]
                            st.session_state.selected_indicators = {}
                            st.rerun()
                else:
                    # 文件夹 → 一键进入
                    folder = flat_options[selected_idx]
                    if st.button(f"📂 打开「{folder['name']}」", key="enter_folder", use_container_width=True):
                        try:
                            children = query_tree(folder["id"], code)
                            if children:
                                st.session_state.tree_history.append(
                                    (st.session_state.current_tree, st.session_state.tree_path_names.copy())
                                )
                                st.session_state.tree_path_names.append(folder["name"])
                                st.session_state.current_tree = children
                                st.rerun()
                            else:
                                st.warning("该目录无子节点")
                        except Exception as e:
                            st.error(f"加载失败：{e}")

            with col_b:
                if st.session_state.tree_history:
                    if st.button("← 返回", key="back_tree", use_container_width=True):
                        prev_tree, prev_path = st.session_state.tree_history.pop()
                        st.session_state.current_tree = prev_tree
                        st.session_state.tree_path_names = prev_path
                        st.rerun()
                else:
                    if st.button("🏠 首页", key="home_tree", use_container_width=True):
                        st.session_state.tree_history = []
                        st.session_state.tree_path_names = []
                        st.session_state.current_tree = []
                        st.session_state.tree_loaded = False
                        st.rerun()
        else:
            st.caption("暂无数据")

    # --- 指标选择（自动全选，仅展示信息） ---
    with st.expander("📋 指标信息", expanded=True):
        if st.session_state.selected_cid:
            try:
                indicators = query_indicators(st.session_state.selected_cid)
                if not indicators:
                    st.caption("该数据集无可用指标")
                    st.session_state.selected_indicators = {}
                else:
                    # 自动全选所有指标
                    st.session_state.selected_indicators = {ind.get("_id"): ind for ind in indicators}

                    st.success(f"✅ 已自动加载 {len(indicators)} 个指标（全选）")

                    # 展示指标概要
                    with st.container(border=True):
                        for ind in indicators[:15]:  # 最多展示15个
                            name = ind.get("i_showname", "未知")
                            st.markdown(f"- {name}")
                        if len(indicators) > 15:
                            st.caption(f"... 还有 {len(indicators) - 15} 个指标未显示")

                    # 显示第一个指标的口径说明（如果有）
                    first_ind = indicators[0]
                    mark = first_ind.get("i_mark", "")
                    if mark:
                        with st.container(border=True):
                            st.markdown("**📌 统计口径说明（首指标）：**")
                            st.caption(mark)
            except Exception as e:
                err_msg = str(e)
                if "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
                    st.error("⏱️ 获取指标超时，可能原因：\n"
                             "1. 网络连接较慢（已自动重试一次）\n"
                             "2. 国家统计局服务器响应慢\n"
                             "👉 请稍后重试，或尝试切换网络")
                else:
                    st.error(f"获取指标失败：{e}")
        else:
            st.caption("请先选择一个数据集")

    # --- 缓存状态 ---
    st.divider()
    st.caption("💾 数据缓存 24h 有效，自动管理")

# ================================================================
# 主面板 — 查询参数 & 可视化
# ================================================================

main_col = st.columns([1, 1])

# --- 查询参数配置 ---
with st.container(border=True):
    st.markdown("### ⚙️ 查询参数配置")

    params_col1, params_col2 = st.columns(2)

    @st.cache_data(ttl=3600)
    def _default_dates():
        end = datetime.now()
        return end, end - timedelta(days=365)

    with params_col1:
        default_end, default_start = _default_dates()
        unit = TIME_LABEL_MAP.get(st.session_state.current_category_code, "")
        start_date_picker = st.date_input(
            f"开始{unit}" if unit else "开始时间",
            value=default_start, min_value=datetime(2000, 1, 1), max_value=default_end,
        )
    with params_col2:
        end_date_picker = st.date_input(
            f"结束{unit}" if unit else "结束时间",
            value=default_end, min_value=datetime(2000, 1, 1), max_value=default_end,
        )
    # 查询按钮
    query_btn_col1, query_btn_col2 = st.columns([3, 1])
    with query_btn_col2:
        selected_count = len(st.session_state.selected_indicators)
        st.markdown(f"**已选指标：** {selected_count} 个")

    with query_btn_col1:
        query_btn = st.button(
            "🚀 查询数据",
            type="primary",
            use_container_width=True,
            disabled=(
                st.session_state.selected_cid is None
                or len(st.session_state.selected_indicators) == 0
            ),
        )

    if st.session_state.selected_cid is None:
        st.warning("⚠️ 请先在左侧边栏中选择一个数据集（通过搜索或分类导航）")
    elif len(st.session_state.selected_indicators) == 0:
        st.warning("⏳ 正在加载指标列表，请稍候...")

# --- 执行查询 ---
if query_btn:
    if not st.session_state.selected_cid:
        st.error("请先选择数据集")
    elif not st.session_state.selected_indicators:
        st.error("请至少选择一个指标")
    elif not start_date_picker or not end_date_picker:
        st.error("请填写时间范围")
    else:
        # 根据分类选择合适的时间格式
        time_suffix = get_time_suffix()
        period_unit = TIME_LABEL_MAP.get(st.session_state.current_category_code, "")
        start_api = date_to_api_format(start_date_picker, time_suffix)
        end_api = date_to_api_format(end_date_picker, time_suffix)

        # 分省分类 → 逐个查询所有省份
        is_province = st.session_state.current_category_code in PROVINCE_CATEGORY_CODES

        with st.spinner("正在查询数据..."):
            try:
                if is_province:
                    raw_data = query_data_all_provinces(
                        cid=st.session_state.selected_cid,
                        indicator_ids=list(st.session_state.selected_indicators.keys()),
                        start_time=start_api,
                        end_time=end_api,
                    )
                else:
                    raw_data = query_data(
                        cid=st.session_state.selected_cid,
                        indicator_ids=list(st.session_state.selected_indicators.keys()),
                        start_time=start_api,
                        end_time=end_api,
                    )
                if not raw_data:
                    st.warning("未查询到数据，请尝试调整时间范围")
                else:
                    st.session_state.is_province_data = is_province
                    df = parse_query_data_to_df(raw_data, st.session_state.selected_indicators)
                    st.session_state.query_df = df
                    total = len(raw_data)
                    msg = f"✅ 查询成功！共 {total} 条记录"
                    if is_province:
                        msg += f"（31 省份）"
                    st.success(msg)
            except Exception as e:
                st.error(f"查询失败：{e}")

# ================================================================
# 数据可视化
# ================================================================

if st.session_state.query_df is not None and not st.session_state.query_df.empty:
    df = st.session_state.query_df

    # 提取可绘制的数值列（排除非数值列）
    exclude_cols = {"时间", "code", "省份"}
    value_cols = [c for c in df.columns if c not in exclude_cols and not c.endswith("_unit")]
    # 进一步筛选：只保留能转为数值的列
    plot_cols = []
    for c in value_cols:
        try:
            pd.to_numeric(df[c], errors="coerce")
            plot_cols.append(c)
        except:
            pass

    # ===== 分省数据的省份选择器（仅图表用，表格显示全部） =====
    chart_df = df
    if st.session_state.is_province_data and "省份" in df.columns:
        provinces = sorted(df["省份"].unique())
        # 默认选中北京
        default_province = "北京" if "北京" in provinces else provinces[0]
        selected_province = st.selectbox("📍 图表展示省份", provinces, index=provinces.index(default_province))
        chart_df = df[df["省份"] == selected_province].copy()

    # ===== 图表区 =====
    with st.container(border=True):
        viz_col1, viz_col2 = st.columns([4, 1])
        with viz_col1:
            st.markdown("### 📈 数据可视化")
        with viz_col2:
            chart_type = st.selectbox("图表类型", ["折线图", "柱状图", "面积图"], label_visibility="collapsed")

        if plot_cols:
            fig = go.Figure()

            for col in plot_cols:
                numeric_vals = pd.to_numeric(chart_df[col], errors="coerce")
                name_display = col
                hover_suffix = ""

                if chart_type == "折线图":
                    fig.add_trace(go.Scatter(
                        x=chart_df["时间"],
                        y=numeric_vals,
                        mode="lines+markers",
                        name=name_display,
                        line=dict(width=2),
                        marker=dict(size=6),
                        hovertemplate="%{x}<br>%{y}" + hover_suffix + "<extra></extra>",
                    ))
                elif chart_type == "柱状图":
                    fig.add_trace(go.Bar(
                        x=chart_df["时间"],
                        y=numeric_vals,
                        name=name_display,
                        hovertemplate="%{x}<br>%{y}" + hover_suffix + "<extra></extra>",
                    ))
                elif chart_type == "面积图":
                    fig.add_trace(go.Scatter(
                        x=chart_df["时间"],
                        y=numeric_vals,
                        mode="lines",
                        name=name_display,
                        stackgroup="one",
                        line=dict(width=1),
                        hovertemplate="%{x}<br>%{y}" + hover_suffix + "<extra></extra>",
                    ))

            period_label = TIME_LABEL_MAP.get(st.session_state.current_category_code, "")
            xaxis_title = f"时间（{period_label}）" if period_label else "时间"
            fig.update_layout(
                template="plotly_white",
                hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=20, r=20, t=40, b=20),
                height=450,
                xaxis=dict(title=xaxis_title),
                yaxis=dict(title="数值"),
                dragmode="zoom",
            )

            # 不需要范围滑块，默认全部显示

            st.plotly_chart(fig, config={"responsive": True})
        else:
            st.info("当前选中的指标无适合绘图的数值数据")

    # ===== 数据表格 =====
    with st.container(border=True):
        st.markdown("### 📋 数据明细")

        # 准备展示用 DataFrame（去掉编码列和不需要的列）
        display_df = df.drop(columns=[c for c in df.columns if c.endswith("_unit") or c == "code"], errors="ignore")

        st.dataframe(display_df, use_container_width=True, hide_index=True)

        # CSV 导出
        csv_data = display_df.to_csv(index=False, encoding="utf-8-sig")
        dataset_tag = st.session_state.selected_cid_name.replace(" ", "_") if st.session_state.selected_cid_name else "数据"
        time_tag = f"{start_date_picker.strftime('%Y%m')}-{end_date_picker.strftime('%Y%m')}"
        st.download_button(
            label="📥 下载 CSV",
            data=csv_data,
            file_name=f"国家统计局_{dataset_tag}_{time_tag}.csv",
            mime="text/csv",
            use_container_width=True,
        )

# ================================================================
# 页脚
# ================================================================

st.divider()
st.markdown(
    """
    <div style="text-align: center; font-size: 0.75rem; color: #64748B;">
        <p>数据来源：<a href="https://data.stats.gov.cn" target="_blank">国家统计局公开 API</a> |
        技术文档：<a href="https://blog.csdn.net/loo_Charles_ool/article/details/159548826" target="_blank">CSDN</a></p>
        <p>基于新版 API V2.0 构建 | 无需鉴权，公开访问</p>
    </div>
    """,
    unsafe_allow_html=True,
)
