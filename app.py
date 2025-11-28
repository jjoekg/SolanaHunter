import streamlit as st
import requests
import pandas as pd
import networkx as nx
from pyvis.network import Network
import streamlit.components.v1 as components
import time

# ==========================================
# 1. 頁面設定與狀態初始化
# ==========================================
st.set_page_config(page_title="Solana 狙擊指揮中心 (穩定版)", layout="wide", page_icon="⚓")

# 初始化 Session State (這是防止跳頁的關鍵！)
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None
if 'current_token' not in st.session_state:
    st.session_state.current_token = ""

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
# 2. 核心功能
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
    """執行分析並回傳結果"""
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
    
    # 這裡我們使用 st.progress 需要小心，因為這是在函數內
    # 為了簡化，這裡直接跑完
    
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
# 3. 顯示功能 (Rendering)
# ==========================================
def render_analysis_results(token_addr, G, risk):
    """將分析結果畫在畫面上 (包含圖表、RugCheck、交易按鈕)"""
    
    st.markdown("---")
    st.subheader(f"📊 分析報告: `{token_addr}`")

    # 1. 風險提示
    if risk > 0:
        st.error(f"🚨 發現老鼠倉集團！風險指數: {risk}")
    else:
        st.success("✅ 籌碼結構健康 (無明顯關聯)")
    
    # 2. 關係圖
    net = Network(height="450px", width="100%", bgcolor="#222222", font_color="white", directed=True, cdn_resources='in_line')
    net.from_nx(G)
    net.save_graph("graph.html")
    with open("graph.html", "r", encoding="utf-8") as f:
        components.html(f.read(), height=470)
    
    # 3. RugCheck
    st.subheader("🛡️ 合約安全")
    try:
        url = f"https://api.rugcheck.xyz/v1/tokens/{token_addr}/report"
        res = requests.get(url, timeout=5).json()
        score = res.get('score', 0)
        if score < 1000: st.success(f"評分: {score} (安全)")
        else: st.error(f"評分: {score} (危險)")
    except: st.warning("RugCheck 連線失敗")

    # 4. Jupiter 交易 (這裡的輸入框不會再導致跳頁了)
    st.subheader("🔫 快速狙擊")
    col1, col2 = st.columns([1, 2])
    with col1:
        # 這裡的 key 很重要，確保每次輸入都是獨立的
        amount = st.number_input("買入 SOL 數量", min_value=0.1, value=0.5, step=0.1, key=f"amt_{token_addr}")
    with col2:
        st.write("")
        st.write("")
        jup_url = f"https://jup.ag/swap/SOL-{token_addr}?inAmount={amount}"
        st.markdown(f"""
        <a href="{jup_url}" target="_blank" style="text-decoration:none;">
            <button style="background-color:#4CAF50;color:white;padding:10px 20px;border:none;border-radius:10px;cursor:pointer;width:100%;font-size:16px;">
            🚀 買入 {amount} SOL
            </button>
        </a>
        """, unsafe_allow_html=True)

# ==========================================
# 4. 主介面邏輯
# ==========================================
st.title("🚀 Solana 狙擊指揮中心 (穩定版)")

if not HELIUS_KEY:
    st.warning("⚠️ 請先在左側欄位輸入 Helius API Key！")

# 輸入框
target = st.text_input("輸入代幣地址", "2zMMhcVQhZkJeb4h5Rpp47aZPaej4XMs75c8V4Jkpump")

# 按鈕邏輯：按下去後，把結果存進 session_state
if st.button("開始分析", key="btn_manual"):
    with st.spinner("🕵️‍♂️ 正在分析中... (請稍候)"):
        G, risk = analyze_token(target)
        if G:
            # 存檔！這樣刷新也不會不見
            st.session_state.analyzed_data = {'G': G, 'risk': risk, 'addr': target}
        else:
            st.error(f"分析失敗: {risk}")

# 渲染邏輯：只要 session_state 裡面有資料，就畫出來
# 這樣不管你下面怎麼調金額，這裡都會持續顯示
if st.session_state.analyzed_data:
    data = st.session_state.analyzed_data
    # 只有當目前輸入框的地址跟分析結果一樣時才顯示 (避免誤會)
    if data['addr'] == target:
        render_analysis_results(data['addr'], data['G'], data['risk'])
