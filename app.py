import streamlit as st
from playwright.sync_api import sync_playwright
from pypdf import PdfWriter
import time
import io
import zipfile
import pandas as pd

# --- EN SADE VE STABİL VERSİYON ---
st.set_page_config(page_title="TEV Sorgu Sistemi", layout="wide")
st.title("TEV Ödeme Sorgulama Portali")

if "results" not in st.session_state: st.session_state.results = None
if "zip" not in st.session_state: st.session_state.zip = None
if "pdf" not in st.session_state: st.session_state.pdf = None

raw_data = st.text_area("Tescil Numaraları", height=150)
col1, col2 = st.columns(2)
with col1: btn_sorgu = st.button("🔍 Sadece Sorgula", use_container_width=True, type="primary")
with col2: btn_pdf = st.button("📄 Sorgula + PDF", use_container_width=True)

if (btn_sorgu or btn_pdf) and raw_data:
    t_list = [t.strip() for t in raw_data.split('\n') if t.strip()]
    results, pdf_files = [], []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.goto("https://uygulama.gtb.gov.tr/TEV/")
        
        prog = st.progress(0)
        for i, tno in enumerate(t_list):
            try:
                page.fill("#TextBox_Beyanname", tno)
                page.click("#Btn_Ara")
                time.sleep(2) # En güvenli bekleme süresi
                
                def get_t(id):
                    el = page.query_selector(f"#{id}")
                    return el.inner_text().strip() if el else "-"
                
                res_data = {
                    "Beyanname": tno,
                    "Gonderen": get_t("Lab_ver_gonderen"),
                    "VergiNo": get_t("Lab_ver_vergino"),
                    "Tutar": get_t("Lab_ver_telafi"),
                    "Tahsilat": get_t("Lab_ver_tahsilat")
                }
                results.append(res_data)

                if btn_pdf and res_data["Tutar"] not in ["-", "Kayıt Bulunamadı"]:
                    page.emulate_media(media="print")
                    pdf_files.append((tno, page.pdf(format="A4")))
            except:
                results.append({"Beyanname": tno, "Gonderen": "HATA", "VergiNo": "-", "Tutar": "-", "Tahsilat": "-"})
            prog.progress((i + 1) / len(t_list))
        browser.close()

    st.session_state.results = results
    if pdf_files:
        # ZIP
        z_io = io.BytesIO()
        with zipfile.ZipFile(z_io, "w") as zf:
            for n, c in pdf_files: zf.writestr(f"{n}.pdf", c)
        st.session_state.zip = z_io.getvalue()
        # MERGE
        merger = PdfWriter()
        for _, c in pdf_files: merger.append(io.BytesIO(c))
        m_io = io.BytesIO()
        merger.write(m_io)
        st.session_state.pdf = m_io.getvalue()

if st.session_state.results:
    if st.session_state.pdf:
        st.download_button("📄 PDF İndir", st.session_state.pdf, "Sonuclar.pdf")
    st.dataframe(pd.DataFrame(st.session_state.results), use_container_width=True, hide_index=True)
