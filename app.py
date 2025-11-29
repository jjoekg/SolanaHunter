import streamlit as st
import requests
import pandas as pd
import networkx as nx
from pyvis.network import Network
import streamlit.components.v1 as components
import time

# ==========================================
# 1. 頁面設定 & 狀態記憶 (防閃退)
# ==========================================
st.set_page_config(page_title="Solana 狙擊指揮中心 (上帝模式)", layout="wide", page_icon="⚡")

if 'manual_result' not in st.session_state: st.session_state.manual_result = None
if 'auto_results' not in st.session_state: st.session_state.auto_results = []

st.sidebar.title("⚙️ 設定中心")
HELIUS_KEY = st.sidebar.text_input("Helius API Key", type="password")
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

# 知名交易所標籤 (避免誤判)
CEX_LABELS = {
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1": "Binance 1",
    "2AQdpHJ2JpcEgPiATUXjQxA8QmafFegfBKkTY8CJ92pA": "Binance 2",
    "AC5RDfQFmDS1deWZosYb21bfU9aMCjVZk4JipjbA71gh": "Coinbase 1",
    "H8sMJSCQxfKiFTCf97_wnBo8PH48Atn36JcZggs8ZKx": "Coinbase 2",
    "315iCQx9t9NCQF457223M6e37kG9PTc1": "Wintermute",
}

# ==========================================
# 2. 核心功能模組
# ==========================================
def send_rpc(method, params):
    try:
        return requests.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":method,"params":params}, timeout=10).json()
    except: return {}

def get_token_price(token_address):
    """抓取代幣現價 (用於計算持倉價值)"""
    try:
        res = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}", timeout=5).json()
        pairs = res.get('pairs', [])
        if pairs: return float(pairs[0].get('priceUsd', 0))
    except: pass
    return 0.0

def trace_funder(wallet):
    """
    🕵️‍♂️ 深層資金溯源：
    追查最近 30 筆交易，找出是誰轉錢給這個錢包的
    """
    time.sleep(0.1) # 溫柔一點
    # 擴大搜索範圍
    data = send_rpc("getSignaturesForAddress", [wallet, {"limit": 30}])
    sigs = [tx['signature'] for tx in data.get('result', [])]
    
    # 策略：查最早的 5 筆 (通常是剛創錢包時) 和 最近 5 筆
    check_list = sigs[-5:] + sigs[:5] if len(sigs) > 10 else sigs
    
    for sig in check_list:
        tx_res = send_rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        try:
            instrs = tx_res['result']['transaction']['message']['instructions']
            for i in instrs:
                if i.get('program') == 'system' and i.get('parsed', {}).get('type') == 'transfer':
                    info = i['parsed']['info']
                    # 如果有錢轉進來，且金額 > 0.1 SOL
                    if info['destination'] == wallet and info['lamports'] > 100000000:
                        return info['source']
        except: continue
    return None

def analyze_token(token_address, current_price=0.0):
    """
    全能分析函數：
    1. 抓大戶
    2. 算價值
    3. 查老鼠倉
    4. 畫圖
    """
    if not HELIUS_KEY: return None, "請輸入 API Key"
    if token_address.startswith("0x"): return None, "不支援以太坊"

    # 如果沒傳價格，自己查
    if current_price == 0.0:
        current_price = get_token_price(token_address)

    # 1. 抓前 10 大持倉
    res = send_rpc("getTokenLargestAccounts", [token_address])
    if 'result' not in res: return None, "查無數據"
    
    accounts = res['result']['value'][:10]
    whales = []
    
    for acc in accounts:
        # 計算持倉價值
        raw_amt = float(acc.get('amount', 0))
        decimals = acc.get('decimals', 6) # 預設6
        amount = raw_amt / (10 ** decimals)
        value_usd = amount * current_price
        
        info = send_rpc("getAccountInfo", [acc['address'], {"encoding": "jsonParsed"}])
        try:
            owner = info['result']['value']['data']['parsed']['info']['owner']
            whales.append((owner, value_usd))
        except: continue
    
    # 去重並保留最大價值
    unique_whales = {}
    for w, val in whales:
        if w in unique_whales: unique_whales[w] += val
        else: unique_whales[w] = val

    # 2. 開始畫圖 & 偵測
    G = nx.DiGraph()
    short_token = token_address[:4] + "..."
    # 中心代幣節點
    G.add_node(token_address, label=f"Token\n{short_token}\nPrice: ${current_price}", color="#ffd700", size=30, shape="star")
    
    risk_score = 0
    funder_map = {}
    
    for whale, val_usd in unique_whales.items():
        # 顯示價值
        val_str = f"${val_usd/1000:.1f}k" if val_usd > 1000 else f"${val_usd:.0f}"
        
        # 節點大小隨價值變化
        size = 15 + (val_usd / 5000)
        if size > 40: size = 40 # 上限
        
        G.add_node(whale, label=f"Holder\n{whale[:4]}...\n💰{val_str}", color="#97c2fc", size=size)
        G.add_edge(whale, token_address, color="#cccccc")
        
        # 3. 追查金主
        funder = trace_funder(whale)
        if funder:
            # 判斷是否為交易所
            if funder in CEX_LABELS:
                f_color, f_label = "#00ff00", f"🏦 {CEX_LABELS[funder]}"
            else:
                f_color, f_label = "#ff4b4b", f"🚨 SOURCE\n{funder[:4]}..."
                # 累計風險
                funder_map[funder] = funder_map.get(funder, 0) + 1
                if funder_map[funder] > 1: risk_score += 10

            if funder not in G:
                G.add_node(funder, label=f_label, color=f_color, size=25, shape="box")
            G.add_edge(funder, whale, color=f_color)

    return G, risk_score

def check_rug_safety(token_address):
    """RugCheck 合約檢測"""
    try:
        res = requests.get(f"https://api.rugcheck.xyz/v1/tokens/{token_address}/report", timeout=3).json()
        score = res.get('score', 9999)
        return score
    except: return 9999

# ==========================================
# 3. 掃描策略
# ==========================================
def scan_new_pairs(target_count=5):
    keywords = ["pump", "meme", "pepe", "dog", "moon"]
    BLACKLIST = ["So11111111111111111111111111111111111111112", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"]
    all_c = []
    
    try:
        for kw in keywords:
            res = requests.get(f"https://api.dexscreener.com/latest/dex/search?q={kw}", timeout=5).json()
            for p in res.get('pairs', []):
                if p.get('chainId') != 'solana': continue
                if p.get('baseToken', {}).get('address') in BLACKLIST: continue
                all_c.append(p)
            if len(all_c) > target_count * 3: break
        
        all_c.sort(key=lambda x: x.get('pairCreatedAt', 0), reverse=True)
        
        seen, final = set(), []
        for p in all_c:
            addr = p.get('baseToken', {}).get('address', '')
            if addr not in seen:
                seen.add(addr)
                final.append(p)
        return final[:target_count]
    except: return []

# ==========================================
# 4. UI 渲染組件
# ==========================================
def render_token_card(token_addr, token_name, price, G, risk):
    st.markdown(f"### {token_name}")
    st.caption(f"📍 `{token_addr}` | 💰 ${price}")

    # 風險與 RugCheck 並排顯示
    col_a, col_b = st.columns(2)
    with col_a:
        if risk > 0: st.error(f"🚨 老鼠倉風險: {risk}")
        else: st.success("✅ 籌碼結構健康")
    with col_b:
        score = check_rug_safety(token_addr)
        if score < 1000: st.info(f"🛡️ 合約評分: {score} (Safe)")
        else: st.warning(f"🛡️ 合約評分: {score} (Risky)")

    # 畫圖
    net = Network(height="450px", width="100%", bgcolor="#222222", font_color="white", directed=True, cdn_resources='in_line')
    net.from_nx(G)
    html = net.generate_html()
    components.html(html, height=470)

    # 交易按鈕
    c1, c2 = st.columns([1, 2])
    with c1:
        amt = st.number_input("SOL", 0.1, 10.0, 0.5, 0.1, key=f"b_{token_addr}")
    with c2:
        st.write(""); st.write("")
        url = f"https://jup.ag/swap/SOL-{token_addr}?inAmount={amt}"
        st.markdown(f"""<a href="{url}" target="_blank"><button style="background-color:#4CAF50;color:white;padding:8px;border:none;border-radius:5px;width:100%;">🚀 買入 {amt} SOL</button></a>""", unsafe_allow_html=True)
    st.divider()

# ==========================================
# 5. 主程式入口
# ==========================================
st.title("🚀 Solana 狙擊指揮中心 (God Mode)")

if not HELIUS_KEY: st.warning("⚠️ 請輸入 API Key")

tab1, tab2 = st.tabs(["🔍 手動查詢", "🤖 自動掃描"])

with tab1:
    target = st.text_input("代幣地址", "")
    if st.button("分析", key="m_btn") and target:
        with st.spinner("🕵️‍♂️ 正在進行全方位分析 (資金+價值+合約)..."):
            price = get_token_price(target)
            G, risk = analyze_token(target, price)
            if G: st.session_state.manual_result = {'G':G, 'risk':risk, 'addr':target, 'name':'Target', 'price':price}
            else: st.error("分析失敗")

    if st.session_state.manual_result and st.session_state.manual_result['addr'] == target:
        r = st.session_state.manual_result
        render_token_card(r['addr'], r['name'], r['price'], r['G'], r['risk'])

with tab2:
    cnt = st.slider("掃描數量", 5, 20, 5)
    if st.button(f"🛡️ 掃描市場", key="a_btn"):
        if not HELIUS_KEY: st.error("No Key")
        else:
            with st.spinner("🛰️ 正在掃描市場並分析數據..."):
                pairs = scan_new_pairs(cnt)
                buf = []
                bar = st.progress(0)
                status = st.empty()
                
                for i, p in enumerate(pairs):
                    addr = p['baseToken']['address']
                    name = p['baseToken']['name']
                    price = float(p.get('priceUsd', 0))
                    status.text(f"分析中: {name}...")
                    
                    G, risk = analyze_token(addr, price)
                    if G: buf.append({'addr':addr, 'name':name, 'price':price, 'G':G, 'risk':risk})
                    bar.progress((i+1)/len(pairs))
                
                st.session_state.auto_results = buf
                status.empty(); bar.empty()

    if st.session_state.auto_results:
        st.success(f"✅ 掃描完成: {len(st.session_state.auto_results)} 個代幣")
        for r in st.session_state.auto_results:
            render_token_card(r['addr'], r['name'], r['price'], r['G'], r['risk'])
