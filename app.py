import streamlit as st
import requests
import pandas as pd
import networkx as nx
from pyvis.network import Network
import streamlit.components.v1 as components
import time
from datetime import datetime

# ==========================================
# 1. 頁面設定
# ==========================================
st.set_page_config(page_title="Solana 狙擊指揮中心 (行為分析版)", layout="wide", page_icon="🕵️")

if 'manual_result' not in st.session_state: st.session_state.manual_result = None
if 'auto_results' not in st.session_state: st.session_state.auto_results = []

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

def get_token_info(token_address):
    """從 DexScreener 獲取價格與創建時間"""
    try:
        res = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}", timeout=5).json()
        pairs = res.get('pairs', [])
        if pairs:
            price = float(pairs[0].get('priceUsd', 0))
            created_at = pairs[0].get('pairCreatedAt', 0) / 1000 # 轉成秒
            return price, created_at
    except: pass
    return 0.0, 0

def check_wallet_behavior(wallet, token_create_time):
    """
    🕵️‍♂️ 行為分析：
    1. 是否為新錢包 (Fresh)
    2. 是否為狙擊手 (Sniper)
    """
    time.sleep(0.1)
    
    # 抓最後 50 筆交易 (或者全部)
    data = send_rpc("getSignaturesForAddress", [wallet, {"limit": 50}])
    sigs = data.get('result', [])
    
    if not sigs: return "Unknown", False, False
    
    # 1. 判斷新錢包
    # 如果交易總數 < 50 筆，且最早的一筆交易時間距離現在 < 3 天 -> 新錢包
    is_fresh = False
    first_tx_time = sigs[-1].get('blockTime', 0)
    current_time = time.time()
    
    if len(sigs) < 50 and (current_time - first_tx_time) < (3 * 24 * 3600):
        is_fresh = True
        
    # 2. 判斷狙擊手 (Sniper)
    # 檢查他在這個代幣上的第一筆交易時間
    is_sniper = False
    if token_create_time > 0:
        # 這裡簡化邏輯：如果錢包最早的交易時間 非常接近 代幣創建時間 (< 10分鐘)
        # 注意：這只是近似判斷，精準判斷需要過濾特定 token 的 tx
        if abs(first_tx_time - token_create_time) < 600: 
            is_sniper = True
            
    return sigs, is_fresh, is_sniper

def trace_funder_from_sigs(wallet, sigs):
    """從已有的簽名中找資金來源"""
    # 只查最早的 5 筆
    check_list = sigs[-5:] 
    
    for tx_info in check_list:
        sig = tx_info['signature']
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
    if not HELIUS_KEY: return None, "無 Key"
    
    # 1. 獲取基礎資訊
    price, create_time = get_token_info(token_address)
    
    # 2. 抓股東
    res = send_rpc("getTokenLargestAccounts", [token_address])
    if 'result' not in res: return None, "查無數據"
    
    accounts = res['result']['value'][:10]
    whales = []
    
    for acc in accounts:
        raw = float(acc.get('amount', 0))
        amt = raw / (10 ** acc.get('decimals', 6))
        val_usd = amt * price
        
        info = send_rpc("getAccountInfo", [acc['address'], {"encoding": "jsonParsed"}])
        try:
            owner = info['result']['value']['data']['parsed']['info']['owner']
            whales.append((owner, val_usd))
        except: continue
    
    unique_whales = {}
    for w, val in whales:
        if w in unique_whales: unique_whales[w] += val
        else: unique_whales[w] = val

    # 3. 畫圖 & 分析
    G = nx.DiGraph()
    short_token = token_address[:4] + "..."
    G.add_node(token_address, label=f"Token\n{short_token}", color="#ffd700", size=30, shape="star")
    
    risk_score = 0
    funder_map = {}
    
    status_text = st.empty()
    bar = st.progress(0)
    
    for i, (whale, val_usd) in enumerate(unique_whales.items()):
        status_text.text(f"🔍 行為分析中 {i+1}/{len(unique_whales)}: {whale[:4]}...")
        bar.progress((i+1)/len(unique_whales))
        
        # A. 行為檢測
        sigs, is_fresh, is_sniper = check_wallet_behavior(whale, create_time)
        
        # B. 決定顏色與標籤
        node_color = "#97c2fc" # 預設藍 (老散戶)
        tags = []
        
        if is_fresh:
            tags.append("👶新號")
            node_color = "#FFFF00" # 黃色警告
            risk_score += 5
            
        if is_sniper:
            tags.append("⚡狙擊")
            node_color = "#DA70D6" # 紫色警告
            risk_score += 10
            
        val_str = f"${val_usd/1000:.1f}k" if val_usd > 1000 else f"${val_usd:.0f}"
        label = f"Holder\n{whale[:4]}...\n💰{val_str}\n{' '.join(tags)}"
        
        G.add_node(whale, label=label, color=node_color, size=20)
        G.add_edge(whale, token_address, color="#cccccc")
        
        # C. 查金主 (利用剛剛抓到的 sigs 加速)
        funder = trace_funder_from_sigs(whale, sigs)
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
    bar.empty()
    return G, risk_score, price

# ==========================================
# 3. 掃描
# ==========================================
def scan_new_pairs(target_count=5):
    keywords = ["pump", "meme", "pepe", "cat"]
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
# 4. 渲染
# ==========================================
def render_token_card(token_addr, token_name, price, G, risk):
    st.markdown(f"### {token_name}")
    st.caption(f"📍 `{token_addr}` | 💰 ${price}")

    c1, c2 = st.columns(2)
    with c1:
        if risk >= 20: st.error(f"🚨 極高風險: {risk} (集團/狙擊)")
        elif risk >= 10: st.warning(f"⚠️ 中度風險: {risk} (可疑行為)")
        else: st.success("✅ 相對安全")
    
    with c2:
        try:
            r = requests.get(f"https://api.rugcheck.xyz/v1/tokens/{token_addr}/report", timeout=3).json()
            score = r.get('score', 9999)
            if score < 1000: st.info(f"🛡️ 合約: {score}")
            else: st.warning(f"🛡️ 合約: {score}")
        except: pass

    net = Network(height="450px", width="100%", bgcolor="#222222", font_color="white", directed=True, cdn_resources='in_line')
    net.from_nx(G)
    components.html(net.generate_html(), height=470)

    # 交易按鈕
    c1, c2 = st.columns([1, 2])
    with c1:
        amt = st.number_input("SOL", 0.1, 10.0, 0.5, key=f"b_{token_addr}")
    with c2:
        st.write(""); st.write("")
        url = f"https://jup.ag/swap/SOL-{token_addr}?inAmount={amt}"
        st.markdown(f"""<a href="{url}" target="_blank"><button style="background-color:#4CAF50;color:white;padding:8px;border-radius:5px;width:100%;">🚀 買入</button></a>""", unsafe_allow_html=True)
    st.divider()

# ==========================================
# 5. 主程式
# ==========================================
st.title("🚀 Solana 狙擊指揮中心 (行為分析版)")

if not HELIUS_KEY: st.warning("⚠️ 請輸入 API Key")

tab1, tab2 = st.tabs(["🔍 手動", "🤖 自動"])

with tab1:
    target = st.text_input("代幣地址", "")
    if st.button("分析", key="m"):
        with st.spinner("🕵️‍♂️ 進行行為特徵分析 (新號/狙擊)..."):
            G, risk, price = analyze_token(target)
            if G: st.session_state.manual_result = {'G':G, 'risk':risk, 'addr':target, 'name':'Target', 'price':price}
            else: st.error("失敗")

    if st.session_state.manual_result and st.session_state.manual_result['addr'] == target:
        r = st.session_state.manual_result
        render_token_card(r['addr'], r['name'], r['price'], r['G'], r['risk'])

with tab2:
    cnt = st.slider("數量", 5, 20, 5)
    if st.button("掃描", key="a"):
        if not HELIUS_KEY: st.error("No Key")
        else:
            with st.spinner("掃描中..."):
                pairs = scan_new_pairs(cnt)
                buf = []
                bar = st.progress(0)
                for i, p in enumerate(pairs):
                    addr = p['baseToken']['address']
                    G, risk, price = analyze_token(addr) # price 會在內部抓
                    if G: buf.append({'addr':addr, 'name':p['baseToken']['name'], 'price':price, 'G':G, 'risk':risk})
                    bar.progress((i+1)/len(pairs))
                st.session_state.auto_results = buf
                bar.empty()

    if st.session_state.auto_results:
        for r in st.session_state.auto_results:
            render_token_card(r['addr'], r['name'], r['price'], r['G'], r['risk'])
