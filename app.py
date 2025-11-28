import streamlit as st
import requests
import pandas as pd
import networkx as nx
from pyvis.network import Network
import streamlit.components.v1 as components
import time

# ==========================================
# 1. 頁面設定
# ==========================================
st.set_page_config(page_title="Solana 狙擊指揮中心 (完全體)", layout="wide", page_icon="🚀")

st.sidebar.title("⚙️ 設定中心")
HELIUS_KEY = st.sidebar.text_input("Helius API Key", type="password")
TG_TOKEN = st.sidebar.text_input("Telegram Bot Token (選填)", type="password")
TG_CHAT_ID = st.sidebar.text_input("Telegram Chat ID (選填)")

RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

# 知名交易所標籤
CEX_LABELS = {
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1": "Binance 1",
    "2AQdpHJ2JpcEgPiATUXjQxA8QmafFegfBKkTY8CJ92pA": "Binance 2",
    "AC5RDfQFmDS1deWZosYb21bfU9aMCjVZk4JipjbA71gh": "Coinbase 1",
    "H8sMJSCQxfKiFTCf97_wnBo8PH48Atn36JcZggs8ZKx": "Coinbase 2",
    "315iCQx9t9NCQF457223M6e37kG9PTc1": "Wintermute",
}

# ==========================================
# 2. 核心功能：老鼠倉偵測
# ==========================================
def send_rpc(method, params):
    try:
        return requests.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":method,"params":params}, timeout=10).json()
    except: return {}

def trace_funder(wallet):
    time.sleep(0.1) 
    data = send_rpc("getSignaturesForAddress", [wallet, {"limit": 20}]) # 查最近20筆
    sigs = [tx['signature'] for tx in data.get('result', [])]
    
    check_list = sigs[-5:] + sigs[:5] if len(sigs) > 10 else sigs
    
    for sig in check_list:
        tx_res = send_rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        try:
            instrs = tx_res['result']['transaction']['message']['instructions']
            for i in instrs:
                if i.get('program') == 'system' and i.get('parsed', {}).get('type') == 'transfer':
                    info = i['parsed']['info']
                    if info['destination'] == wallet and info['lamports'] > 10000000: # >0.01 SOL
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
    
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    for i, whale in enumerate(unique_whales):
        status_text.text(f"🔍 深度掃描大戶 {i+1}/{len(unique_whales)}: {whale[:4]}...")
        progress_bar.progress((i + 1) / len(unique_whales))
        
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

    status_text.empty()
    progress_bar.empty()
    return G, risk_score

# ==========================================
# 3. 新功能：RugCheck 安全檢測
# ==========================================
def check_rug_safety(token_address):
    st.markdown("---")
    st.subheader("🛡️ 合約安全檢測 (RugCheck)")
    try:
        url = f"https://api.rugcheck.xyz/v1/tokens/{token_address}/report"
        res = requests.get(url, timeout=5).json()
        score = res.get('score', 0)
        risks = res.get('risks', [])
        
        col1, col2 = st.columns(2)
        with col1:
            if score < 1000:
                st.success(f"✅ 安全評分: {score} (越低越好)")
            else:
                st.error(f"❌ 危險評分: {score} (高風險)")
        
        with col2:
            if not risks:
                st.info("👍 未發現明顯合約漏洞")
            else:
                with st.expander(f"⚠️ 發現 {len(risks)} 個潛在風險"):
                    for r in risks:
                        st.write(f"🔴 **{r.get('name')}**: {r.get('description')}")
        return score
    except:
        st.warning("RugCheck 暫時無法連線")
        return 9999

# ==========================================
# 4. 新功能：Jupiter 一鍵交易
# ==========================================
def render_trade_button(token_address):
    st.markdown("---")
    st.subheader("🔫 快速狙擊 (Jupiter)")
    
    col1, col2 = st.columns([1, 2])
    with col1:
        amount = st.number_input("買入 SOL 數量", min_value=0.1, value=0.5, step=0.1)
    with col2:
        st.write("") # Spacer
        st.write("")
        # Jupiter Deep Link
        jup_url = f"https://jup.ag/swap/SOL-{token_address}?inAmount={amount}"
        st.markdown(f"""
        <a href="{jup_url}" target="_blank" style="text-decoration:none;">
            <button style="background-color:#4CAF50;color:white;padding:10px 20px;border:none;border-radius:10px;cursor:pointer;width:100%;font-size:16px;font-weight:bold;">
            🚀 立即買入 (Phantom)
            </button>
        </a>
        """, unsafe_allow_html=True)

# ==========================================
# 5. 掃描策略
# ==========================================
def scan_new_pairs():
    BLACKLIST_ADDR = ["So11111111111111111111111111111111111111112", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"]
    try:
        # 1. 抓 Pump 新幣
        res = requests.get("https://api.dexscreener.com/latest/dex/search?q=pump", timeout=5).json()
        raw = res.get('pairs', [])
        valid = []
        for p in raw:
            if p.get('chainId') != 'solana': continue
            if p.get('baseToken', {}).get('address') in BLACKLIST_ADDR: continue
            valid.append(p)
        
        # 2. 如果沒東西，抓熱門 SOL
        if not valid:
            res = requests.get("https://api.dexscreener.com/latest/dex/search?q=solana", timeout=5).json()
            raw = res.get('pairs', [])
            for p in raw:
                if p.get('chainId') == 'solana' and p.get('baseToken', {}).get('address') not in BLACKLIST_ADDR:
                    valid.append(p)
                    
        valid.sort(key=lambda x: x.get('pairCreatedAt', 0), reverse=True)
        return valid[:5]
    except: return []

# ==========================================
# 6. 主介面
# ==========================================
st.title("🚀 Solana 狙擊指揮中心 (完全體)")

if not HELIUS_KEY:
    st.warning("⚠️ 請先在左側輸入 Helius API Key！")

tab1, tab2 = st.tabs(["🔍 深度分析 & 交易", "🛡️ 自動掃描市場"])

# TAB 1: 單幣分析
with tab1:
    target = st.text_input("輸入代幣地址", "2zMMhcVQhZkJeb4h5Rpp47aZPaej4XMs75c8V4Jkpump")
    if st.button("開始分析", key="btn_manual"):
        with st.spinner("🕵️‍♂️ 全面掃描中 (資金網 + 合約 + 交易)..."):
            # 1. 畫老鼠倉圖
            G, risk = analyze_token(target)
            
            if G is None:
                st.error(f"分析失敗: {risk}")
            else:
                # 顯示圖表
                if risk > 0:
                    st.error(f"🚨 發現老鼠倉集團！風險指數: {risk}")
                else:
                    st.success("✅ 籌碼結構健康 (無明顯關聯)")
                
                net = Network(height="450px", width="100%", bgcolor="#222222", font_color="white", directed=True, cdn_resources='in_line')
                net.from_nx(G)
                net.save_graph("graph.html")
                with open("graph.html", "r", encoding="utf-8") as f:
                    components.html(f.read(), height=470)
                
                # 2. RugCheck 檢測
                rug_score = check_rug_safety(target)
                
                # 3. 交易按鈕 (只有風險低才建議買)
                render_trade_button(target)

# TAB 2: 自動掃描
with tab2:
    if st.button("📡 掃描新幣"):
        if not HELIUS_KEY: st.error("無 Key")
        else:
            pairs = scan_new_pairs()
            if not pairs: st.warning("無數據")
            else:
                for pair in pairs:
                    name = pair.get('baseToken', {}).get('name', 'Unknown')
                    addr = pair.get('baseToken', {}).get('address', '')
                    price = pair.get('priceUsd', '0')
                    
                    st.markdown(f"**{name}** (`{addr}`)")
                    st.write(f"💰 ${price}")
                    
                    # 快速分析
                    G, risk = analyze_token(addr)
                    if G:
                        if risk > 0: st.error(f"❌ 風險: {risk}")
                        else: 
                            st.success("✅ 籌碼分散")
                            # 安全的新幣直接顯示買入按鈕
                            render_trade_button(addr)
                    
                    st.divider()
