import streamlit as st
import requests
import pandas as pd
import networkx as nx
from pyvis.network import Network
import streamlit.components.v1 as components
import time

# ==========================================
# 1. 頁面設定與狀態記憶
# ==========================================
st.set_page_config(page_title="Solana 狙擊指揮中心 (量產版)", layout="wide", page_icon="🚀")

# 初始化 Session State
if 'manual_result' not in st.session_state:
    st.session_state.manual_result = None
if 'auto_results' not in st.session_state:
    st.session_state.auto_results = []

st.sidebar.title("⚙️ 設定中心")
HELIUS_KEY = st.sidebar.text_input("Helius API Key", type="password")
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

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
# 3. 掃描新幣策略 (支援自訂數量)
# ==========================================
def scan_new_pairs(target_count=5):
    """
    Args:
        target_count: 用戶想抓幾個幣
    """
    keywords = ["pump", "meme", "cat", "dog", "pepe", "moon"]
    BLACKLIST_ADDR = ["So11111111111111111111111111111111111111112", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"]
    
    all_candidates = []
    # 為了確保過濾後還有足夠的幣，我們抓取目標數量的 3 倍
    fetch_limit = target_count * 3
    
    try:
        for kw in keywords:
            res = requests.get(f"https://api.dexscreener.com/latest/dex/search?q={kw}", timeout=5).json()
            pairs = res.get('pairs', [])
            for p in pairs:
                if p.get('chainId') != 'solana': continue
                if p.get('baseToken', {}).get('address') in BLACKLIST_ADDR: continue
                name = p.get('baseToken', {}).get('name', '').lower()
                if name == 'solana' or name == 'wrapped sol': continue
                
                all_candidates.append(p)
            
            # 如果已經抓夠多了，就停
            if len(all_candidates) > fetch_limit: break
        
        all_candidates.sort(key=lambda x: x.get('pairCreatedAt', 0), reverse=True)
        
        # 去重
        seen = set()
        final = []
        for p in all_candidates:
            addr = p.get('baseToken', {}).get('address', '')
            if addr not in seen:
                seen.add(addr)
                final.append(p)
        
        # 回傳用戶指定的數量
        return final[:target_count]
    except: return []

# ==========================================
# 4. 渲染功能
# ==========================================
def render_token_card(token_addr, token_name, price, G, risk):
    st.markdown(f"### {token_name}")
    st.caption(f"📍 `{token_addr}` | 💰 ${price}")

    if risk > 0: st.error(f"🚨 發現老鼠倉集團！風險指數: {risk}")
    else: st.success("✅ 籌碼結構健康")
    
    net = Network(height="400px", width="100%", bgcolor="#222222", font_color="white", directed=True, cdn_resources='in_line')
    net.from_nx(G)
    html_data = net.generate_html()
    components.html(html_data, height=420)
    
    try:
        r_res = requests.get(f"https://api.rugcheck.xyz/v1/tokens/{token_addr}/report", timeout=3).json()
        score = r_res.get('score', 9999)
        if score < 1000: st.info(f"🛡️ RugCheck 評分: {score}")
        else: st.warning(f"🛡️ RugCheck 評分: {score}")
    except: pass

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
# 5. 主介面
# ==========================================
st.title("🚀 Solana 狙擊指揮中心")

if not HELIUS_KEY:
    st.warning("⚠️ 請先在左側欄位輸入 Helius API Key！")

tab1, tab2 = st.tabs(["🔍 手動查幣", "🤖 自動掃描市場"])

# TAB 1
with tab1:
    target = st.text_input("輸入代幣地址", "2zMMhcVQhZkJeb4h5Rpp47aZPaej4XMs75c8V4Jkpump")
    if st.button("開始分析", key="btn_manual"):
        with st.spinner("🕵️‍♂️ 正在分析中..."):
            G, risk = analyze_token(target)
            if G:
                st.session_state.manual_result = {'G': G, 'risk': risk, 'addr': target, 'name': 'Target Token', 'price': '-'}
            else:
                st.error(f"分析失敗: {risk}")

    if st.session_state.manual_result and st.session_state.manual_result['addr'] == target:
        res = st.session_state.manual_result
        render_token_card(res['addr'], res['name'], res['price'], res['G'], res['risk'])

# TAB 2: 增加滑桿
with tab2:
    col_a, col_b = st.columns([3, 1])
    with col_a:
        st.write("自動抓取市場上最新的熱門新幣。")
    with col_b:
        # 🔥 新增功能：讓用戶自己選數量
        scan_count = st.slider("掃描數量", min_value=5, max_value=20, value=5, step=5)
    
    if st.button(f"🛡️ 掃描 {scan_count} 個新幣", key="btn_auto"):
        if not HELIUS_KEY:
             st.error("無 Key")
        else:
            with st.spinner(f"🛰️ 正在掃描 {scan_count} 個新幣，這可能需要幾分鐘 (取決於 API 速度)..."):
                pairs = scan_new_pairs(scan_count)
                results_buffer = []
                
                if not pairs:
                    st.warning("暫無新幣數據")
                else:
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    for i, pair in enumerate(pairs):
                        name = pair.get('baseToken', {}).get('name', 'Unknown')
                        addr = pair.get('baseToken', {}).get('address', '')
                        price = pair.get('priceUsd', '0')
                        
                        status_text.text(f"正在深度分析第 {i+1}/{len(pairs)} 個：{name}...")
                        
                        G, risk = analyze_token(addr)
                        if G:
                            results_buffer.append({'addr': addr, 'name': name, 'price': price, 'G': G, 'risk': risk})
                        
                        progress_bar.progress((i + 1) / len(pairs))
                    
                    st.session_state.auto_results = results_buffer
                    status_text.empty()
                    progress_bar.empty()

    if st.session_state.auto_results:
        st.success(f"✅ 掃描完成！共顯示 {len(st.session_state.auto_results)} 個代幣")
        for res in st.session_state.auto_results:
            render_token_card(res['addr'], res['name'], res['price'], res['G'], res['risk'])
