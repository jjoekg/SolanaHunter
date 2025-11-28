import streamlit as st
import requests
import pandas as pd
import networkx as nx
from pyvis.network import Network
import streamlit.components.v1 as components
import time

# ==========================================
# 1. 頁面設定與狀態記憶 (防閃退核心)
# ==========================================
st.set_page_config(page_title="Solana 狙擊指揮中心 (完全體)", layout="wide", page_icon="🚀")

# 初始化 Session State
if 'manual_result' not in st.session_state:
    st.session_state.manual_result = None # 存手動查詢的結果
if 'auto_results' not in st.session_state:
    st.session_state.auto_results = []    # 存自動掃描的結果列表

st.sidebar.title("⚙️ 設定中心")
HELIUS_KEY = st.sidebar.text_input("Helius API Key", type="password")
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

# 交易所標籤
CEX_LABELS = {
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1": "Binance 1",
    "2AQdpHJ2JpcEgPiATUXjQxA8QmafFegfBKkTY8CJ92pA": "Binance 2",
    "AC5RDfQFmDS1deWZosYb21bfU9aMCjVZk4JipjbA71gh": "Coinbase 1",
    "H8sMJSCQxfKiFTCf97_wnBo8PH48Atn36JcZggs8ZKx": "Coinbase 2",
    "315iCQx9t9NCQF457223M6e37kG9PTc1": "Wintermute",
}

# ==========================================
# 2. 核心功能：API 請求與分析
# ==========================================
def send_rpc(method, params):
    try:
        return requests.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":method,"params":params}, timeout=10).json()
    except: return {}

def trace_funder(wallet):
    time.sleep(0.1) 
    data = send_rpc("getSignaturesForAddress", [wallet, {"limit": 20}])
    sigs = [tx['signature'] for tx in data.get('result', [])]
    check_list = sigs[-5:] + sigs[:5] if len(sigs) > 10 else sigs
    
    for sig in check_list:
        tx_res = send_rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        try:
            instrs = tx_res['result']['transaction']['message']['instructions']
            for i in instrs:
                if i.get('program') == 'system' and i.get('parsed', {}).get('type') == 'transfer':
                    info = i['parsed']['info']
                    if info['destination'] == wallet and info['lamports'] > 10000000:
                        return info['source']
        except: continue
    return None

def analyze_token(token_address):
    """分析單一代幣，回傳 (Graph, RiskScore)"""
    if not HELIUS_KEY: return None, "請輸入 API Key"
    if token_address.startswith("0x"): return None, "不支援以太坊"

    res = send_rpc("getTokenLargestAccounts", [token_address])
    if 'result' not in res: return None, "查無數據"
    
    accounts = res['result']['value'][:10]
    whales = []
    
    for acc in accounts:
        info = send_rpc("getAccountInfo", [acc['address'], {"encoding": "jsonParsed"}])
        try:
            owner = info['result']['value']['data']['parsed']['info']['owner']
            whales.append(owner)
        except: continue
    
    unique_whales = list(set(whales))
    
    G = nx.DiGraph()
    short_token = token_address[:4] + "..."
    G.add_node(token_address, label=f"Token\n{short_token}", color="#ffd700", size=25, shape="star")
    
    risk_score = 0
    funder_map = {}
    
    # 這裡不顯示進度條，以免自動掃描時洗版
    for whale in unique_whales:
        G.add_node(whale, label=f"Holder\n{whale[:4]}...", color="#97c2fc", size=15)
        G.add_edge(whale, token_address, color="#cccccc")
        
        funder = trace_funder(whale)
        if funder:
            if funder in CEX_LABELS:
                f_color, f_label = "#00ff00", f"🏦 {CEX_LABELS[funder]}"
            else:
                f_color, f_label = "#ff4b4b", f"🚨 SOURCE\n{funder[:4]}..."
                funder_map[funder] = funder_map.get(funder, 0) + 1
                if funder_map[funder] > 1: risk_score += 10

            if funder not in G:
                G.add_node(funder, label=f_label, color=f_color, size=25, shape="box")
            G.add_edge(funder, whale, color=f_color)

    return G, risk_score

# ==========================================
# 3. 掃描新幣策略 (Fail-Safe)
# ==========================================
def scan_new_pairs():
    keywords = ["pump", "meme", "cat", "dog", "pepe"]
    BLACKLIST_ADDR = ["So11111111111111111111111111111111111111112", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"]
    
    all_candidates = []
    try:
        # 多關鍵字輪詢
        for kw in keywords:
            res = requests.get(f"https://api.dexscreener.com/latest/dex/search?q={kw}", timeout=5).json()
            pairs = res.get('pairs', [])
            for p in pairs:
                if p.get('chainId') != 'solana': continue
                if p.get('baseToken', {}).get('address') in BLACKLIST_ADDR: continue
                name = p.get('baseToken', {}).get('name', '').lower()
                if name == 'solana' or name == 'wrapped sol': continue
                all_candidates.append(p)
            if len(all_candidates) > 15: break
        
        # 按時間排序
        all_candidates.sort(key=lambda x: x.get('pairCreatedAt', 0), reverse=True)
        
        # 去重
        seen = set()
        final = []
        for p in all_candidates:
            addr = p.get('baseToken', {}).get('address', '')
            if addr not in seen:
                seen.add(addr)
                final.append(p)
        return final[:5] # 只回傳前5個
    except: return []

# ==========================================
# 4. 共用渲染組件 (畫圖+按鈕)
# ==========================================
def render_token_card(token_addr, token_name, price, G, risk):
    """將單個代幣的分析結果畫出來"""
    st.markdown(f"### {token_name}")
    st.caption(f"📍 `{token_addr}` | 💰 ${price}")

    # 風險提示
    if risk > 0:
        st.error(f"🚨 發現老鼠倉集團！風險指數: {risk}")
    else:
        st.success("✅ 籌碼結構健康 (無明顯關聯)")
    
    # 畫圖
    net = Network(height="400px", width="100%", bgcolor="#222222", font_color="white", directed=True, cdn_resources='in_line')
    net.from_nx(G)
    # 這裡我們用一個隨機檔名避免快取衝突，或直接用 HTML string
    # 為了簡單，這裡用 unique key
    html_data = net.generate_html()
    components.html(html_data, height=420)
    
    # RugCheck (簡單版)
    try:
        r_res = requests.get(f"https://api.rugcheck.xyz/v1/tokens/{token_addr}/report", timeout=3).json()
        score = r_res.get('score', 9999)
        if score < 1000: st.info(f"🛡️ RugCheck 評分: {score} (安全)")
        else: st.warning(f"🛡️ RugCheck 評分: {score} (注意)")
    except: pass

    # 交易按鈕 (使用 Unique Key 防止衝突)
    col1, col2 = st.columns([1, 2])
    with col1:
        amt = st.number_input("買入 SOL", min_value=0.1, value=0.5, step=0.1, key=f"buy_{token_addr}")
    with col2:
        st.write("")
        st.write("")
        jup_url = f"https://jup.ag/swap/SOL-{token_addr}?inAmount={amt}"
        st.markdown(f"""<a href="{jup_url}" target="_blank"><button style="background-color:#4CAF50;color:white;padding:8px 16px;border:none;border-radius:8px;cursor:pointer;">🚀 買入 {amt} SOL</button></a>""", unsafe_allow_html=True)
    
    st.divider()

# ==========================================
# 5. 主介面邏輯 (雙分頁)
# ==========================================
st.title("🚀 Solana 狙擊指揮中心")

if not HELIUS_KEY:
    st.warning("⚠️ 請先在左側欄位輸入 Helius API Key！")

tab1, tab2 = st.tabs(["🔍 手動查幣", "🤖 自動掃描市場"])

# --- TAB 1: 手動查詢 ---
with tab1:
    target = st.text_input("輸入代幣地址", "2zMMhcVQhZkJeb4h5Rpp47aZPaej4XMs75c8V4Jkpump")
    
    # 按鈕觸發分析，並存入 Session
    if st.button("開始分析", key="btn_manual"):
        with st.spinner("🕵️‍♂️ 正在分析中..."):
            G, risk = analyze_token(target)
            if G:
                st.session_state.manual_result = {'G': G, 'risk': risk, 'addr': target, 'name': 'Target Token', 'price': '-'}
            else:
                st.error(f"分析失敗: {risk}")

    # 渲染手動結果 (如果有存檔)
    if st.session_state.manual_result:
        # 確保顯示的是當前輸入框的幣
        res = st.session_state.manual_result
        if res['addr'] == target:
            render_token_card(res['addr'], res['name'], res['price'], res['G'], res['risk'])

# --- TAB 2: 自動掃描 ---
with tab2:
    st.write("點擊按鈕，自動抓取市場上最新的 5 個熱門新幣並進行老鼠倉檢測。")
    
    # 按鈕觸發掃描，並存入 Session
    if st.button("🛡️ 啟動自動掃描", key="btn_auto"):
        if not HELIUS_KEY:
             st.error("無 Key")
        else:
            with st.spinner("🛰️ 正在掃描 DexScreener 並分析大戶數據 (需約 30 秒)..."):
                pairs = scan_new_pairs()
                results_buffer = []
                
                if not pairs:
                    st.warning("暫無新幣數據")
                else:
                    progress_bar = st.progress(0)
                    for i, pair in enumerate(pairs):
                        name = pair.get('baseToken', {}).get('name', 'Unknown')
                        addr = pair.get('baseToken', {}).get('address', '')
                        price = pair.get('priceUsd', '0')
                        
                        # 分析
                        G, risk = analyze_token(addr)
                        if G:
                            results_buffer.append({
                                'addr': addr, 'name': name, 'price': price, 'G': G, 'risk': risk
                            })
                        
                        progress_bar.progress((i + 1) / len(pairs))
                    
                    # 存入 Session
                    st.session_state.auto_results = results_buffer
                    progress_bar.empty()

    # 渲染自動掃描結果 (如果有存檔)
    if st.session_state.auto_results:
        st.success(f"✅ 掃描完成！共找到 {len(st.session_state.auto_results)} 個有效代幣")
        for res in st.session_state.auto_results:
            render_token_card(res['addr'], res['name'], res['price'], res['G'], res['risk'])
