import streamlit as st
from playwright.sync_api import sync_playwright
from pypdf import PdfWriter
import time
import io
import zipfile
import pandas as pd
import re

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="TEV Odeme Sorgulama", page_icon="", layout="centered")

# CSS ile tüm sayfayı %80 ölçeklendirme (Uzaklaştırma etkisi)
st.markdown(
    """
    <style>
        .main .block-container {
            transform: scale(0.8);
            transform-origin: top center;
        }
        .stDataFrame div[data-testid="stTable"] {
            font-size: 10px !important;
        }
    </style>
    """,
    unsafe_allow_html=True
)

st.title("TEV Ödeme Sorgulama Portali")

# --- SESSION STATE ---
if "query_results" not in st.session_state: st.session_state.query_results = None
if "zip_bytes" not in st.session_state: st.session_state.zip_bytes = None
if "merged_pdf_bytes" not in st.session_state: st.session_state.merged_pdf_bytes = None

raw_data = st.text_area("Tescil Numaraları", height=150, placeholder="20230000...")

col_btn1, col_btn2 = st.columns(2)
with col_btn1:
    btn_sadece_sonuc = st.button("🔍 Sorgula (Sadece Sonuç Gösterir)", use_container_width=True, type="primary")
with col_btn2:
    btn_pdf_al = st.button("📄 Sorgula (Sonuç + PDF Alır)", use_container_width=True)

pdf_mode = btn_pdf_al
start_query = btn_sadece_sonuc or btn_pdf_al

def extract_tev_result(page):
    try:
        page_text = page.inner_text("body")
        if "kayıt bulunamadı" in page_text.lower() or "kayit bulunamadi" in page_text.lower():
            return "-", "-", "Kayıt Bulunamadı", "-", None
        if "ödeme yoktur" in page_text.lower() or "odeme yoktur" in page_text.lower():
            return "-", "-", "Ödeme Yoktur", "-", False

        def get_by_id(element_id):
            el = page.query_selector(f"#{element_id}")
            return el.inner_text().strip() if el else "-"

        return get_by_id("Lab_ver_gonderen"), get_by_id("Lab_ver_vergino"), get_by_id("Lab_ver_telafi"), get_by_id("Lab_ver_tahsilat"), True
    except:
        return "-", "-", "Hata", "-", None

def wait_for_result(page, prev_t):
    timeout, interval, elapsed = 10, 0.3, 0
    while elapsed < timeout:
        try:
            body = page.inner_text("body")
            if prev_t and prev_t in body:
                time.sleep(interval); elapsed += interval; continue
            if any(kw in body.lower() for kw in ["telafi edici vergi", "ödeme yoktur", "kayıt bulunamadı"]): return
        except: pass
        time.sleep(interval); elapsed += interval

if start_query:
    tescil_list = [t.strip() for t in raw_data.split('\n') if t.strip()]
    if tescil_list:
        progress_bar = st.progress(0)
        status_text = st.empty()
        pdf_results, pdf_list_for_merge, results = {}, [], []
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
                page = browser.new_page()
                page.goto("https://uygulama.gtb.gov.tr/TEV/")
                prev_t = None

                for i, tno in enumerate(tescil_list):
                    status_text.text(f"⏳ Sorgulanıyor: {tno} ({i+1}/{len(tescil_list)})")
                    page.fill("#TextBox_Beyanname", tno)
                    page.click("#Btn_Ara")
                    wait_for_result(page, prev_t)
                    
                    res = extract_tev_result(page)
                    # Sütun isimlerini burada sabitliyoruz
                    results.append({
                        "Beyanname": tno,
                        "Gönderen": res[0],
                        "VergiNo": res[1],
                        "Tutar": res[2],
                        "Tahsilat": res[3],
                        "odeme_var": res[4]
                    })

                    if pdf_mode and res[2] not in ["Kayıt Bulunamadı", "Hata"]:
                        page.emulate_media(media="print")
                        pdf_c = page.pdf(format="A4")
                        pdf_results[f"{tno}.pdf"] = pdf_c
                        pdf_list_for_merge.append(pdf_c)
                    
                    prev_t = tno
                    progress_bar.progress((i + 1) / len(tescil_list))
                browser.close()

            st.session_state.query_results = results
            if pdf_mode and pdf_results:
                z_buf = io.BytesIO()
                with zipfile.ZipFile(z_buf, "w") as zf:
                    for f, c in pdf_results.items(): zf.writestr(f, c)
                st.session_state.zip_bytes = z_buf.getvalue()

                merger = PdfWriter()
                for p_data in pdf_list_for_merge: merger.append(io.BytesIO(p_data))
                m_buf = io.BytesIO()
                merger.write(m_buf)
                st.session_state.merged_pdf_bytes = m_buf.getvalue()
            status_text.text("✅ Tamamlandı!")
        except Exception as e:
            st.error(f"Hata: {e}")

if st.session_state.query_results:
    st.markdown("---")
    if st.session_state.zip_bytes or st.session_state.merged_pdf_bytes:
        c1, c2 = st.columns(2)
        if st.session_state.merged_pdf_bytes: c1.download_button("📄 Tek PDF", st.session_state.merged_pdf_bytes, "Tev.pdf", use_container_width=True)
        if st.session_state.zip_bytes: c2.download_button("📦 ZIP", st.session_state.zip_bytes, "Tev.zip", use_container_width=True)

    st.markdown("### 🔍 Sonuç Detay")
    df = pd.DataFrame(st.session_state.query_results)

    def style_table(row):
        # Metin kaydırmayı ve fontu zorla
        style = 'font-size: 10px; white-space: normal; word-wrap: break-word;'
        tev = row["Tutar"]
        if tev == "Kayıt Bulunamadı": bg = "background-color: #f8f9fa;"
        elif tev == "Ödeme Yoktur": bg = "background-color: #f0fff4; color: #1a7f37; font-weight: bold;"
        elif row["odeme_var"] is True and tev not in ["-", "Hata"]: bg = "background-color: #fff5f5; color: #d73a49; font-weight: bold;"
        else: bg = ""
        return [f"{style} {bg}"] * len(row)

    styled_df = df.style.apply(style_table, axis=1)

    # KeyError riskini ortadan kaldırmak için doğrudan DataFrame sütunlarını kullanıyoruz
    st.dataframe(
        styled_df,
        use_container_width=True,
        hide_index=True,
        column_order=("Beyanname", "Gönderen", "VergiNo", "Tutar", "Tahsilat"),
        column_config={
            "Gönderen": st.column_config.TextColumn("Gönderen Ünvanı", width="large"),
            "Beyanname": st.column_config.TextColumn("Beyanname No", width="medium"),
            "Tutar": st.column_config.TextColumn("TEV Tutarı", width="small")
        }
    )
