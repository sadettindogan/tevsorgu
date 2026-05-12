import streamlit as st
from playwright.sync_api import sync_playwright
from pypdf import PdfWriter
import time
import io
import zipfile
import pandas as pd
import re

# --- SAYFA AYARLARI ---
# Layout'u tekrar 'centered' (varsayılan) yaptık, sadece tabloya odaklandık.
st.set_page_config(page_title="TEV Odeme Sorgulama", page_icon="", layout="centered")
st.title("TEV Ödeme Sorgulama Portali")

# --- SESSION STATE ---
for key in ["zip_bytes", "merged_pdf_bytes", "query_results"]:
    if key not in st.session_state:
        st.session_state[key] = None

raw_data = st.text_area("Tescil Numaraları", height=150, placeholder="20230000...")

# --- SORGU MODU ---
col_btn1, col_btn2 = st.columns(2)
with col_btn1:
    btn_sadece_sonuc = st.button("🔍 Sorgula (Sadece Sonuç)", use_container_width=True, type="primary")
with col_btn2:
    btn_pdf_al = st.button("📄 Sorgula (Sonuç + PDF)", use_container_width=True)

pdf_mode = btn_pdf_al
start_query = btn_sadece_sonuc or btn_pdf_al

# ... (extract_tev_result ve wait_for_result fonksiyonları aynı kalıyor) ...

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

        gonderen = get_by_id("Lab_ver_gonderen")
        vergino = get_by_id("Lab_ver_vergino")
        tev_value = get_by_id("Lab_ver_telafi")
        tahsilat_yeri = get_by_id("Lab_ver_tahsilat")

        has_payment = None
        if tev_value and tev_value != "-":
            if re.match(r"^[\d.,\s]+$", tev_value):
                has_payment = True
            else:
                nums = re.findall(r"\d[\d.,]*", tev_value)
                if nums:
                    tev_value = nums[0]
                    has_payment = True
        return gonderen, vergino, tev_value, tahsilat_yeri, has_payment
    except:
        return "-", "-", "Hata", "-", None

def wait_for_result(page, prev_tescil=None):
    timeout, interval, elapsed = 10, 0.3, 0
    while elapsed < timeout:
        try:
            body = page.inner_text("body")
            if prev_tescil and prev_tescil in body:
                time.sleep(interval); elapsed += interval; continue
            if any(kw in body.lower() for kw in ["telafi edici vergi", "ödeme yoktur", "kayıt bulunamadı"]): return
        except: pass
        time.sleep(interval); elapsed += interval

if start_query:
    tescil_list = [t.strip() for t in raw_data.split('\n') if t.strip()]
    if tescil_list:
        st.session_state.query_results = []
        progress_bar = st.progress(0)
        pdf_results, pdf_list_for_merge, query_results = {}, [], []
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            url = "https://uygulama.gtb.gov.tr/TEV/"
            page.goto(url)
            prev_tescil = None

            for i, tno in enumerate(tescil_list):
                page.fill("#TextBox_Beyanname", tno)
                page.click("#Btn_Ara")
                wait_for_result(page, prev_tescil)
                
                res = extract_tev_result(page)
                query_results.append({
                    "İhracat Beyannamesi": tno, "Gönderen": res[0], "Vergino": res[1],
                    "Telafi Edici Vergi": res[2], "Tahsilat Yeri": res[3], "odeme_var": res[4]
                })

                if pdf_mode and res[2] != "Kayıt Bulunamadı":
                    page.emulate_media(media="print")
                    pdf_c = page.pdf(format="A4")
                    pdf_results[f"{tno}.pdf"] = pdf_c
                    pdf_list_for_merge.append(pdf_c)
                
                prev_tescil = tno
                progress_bar.progress((i + 1) / len(tescil_list))
            browser.close()

        st.session_state.query_results = query_results
        if pdf_mode:
            z_buf = io.BytesIO()
            with zipfile.ZipFile(z_buf, "w") as zf:
                for f, c in pdf_results.items(): zf.writestr(f, c)
            st.session_state.zip_bytes = z_buf.getvalue()
            
            merger = PdfWriter()
            for p_data in pdf_list_for_merge: merger.append(io.BytesIO(p_data))
            m_buf = io.BytesIO()
            merger.write(m_buf)
            st.session_state.merged_pdf_bytes = m_buf.getvalue()

# --- SONUÇLARI GÖSTER ---
if st.session_state.query_results:
    st.markdown("---")
    if st.session_state.merged_pdf_bytes or st.session_state.zip_bytes:
        c1, c2 = st.columns(2)
        if st.session_state.merged_pdf_bytes: c1.download_button("📄 Tek PDF", st.session_state.merged_pdf_bytes, "Tev_Birlestirilmis.pdf", use_container_width=True)
        if st.session_state.zip_bytes: c2.download_button("📦 ZIP", st.session_state.zip_bytes, "Tev_Arsiv.zip", use_container_width=True)

    st.markdown("### 🔍 Sonuç Detay")

    df = pd.DataFrame(st.session_state.query_results)

    # TABLO İÇİ YAZI BOYUTUNU KÜÇÜLTME (Kritik Bölüm)
    def style_table(row):
        # Fontu 10px yaptık, satır yüksekliğini daralttık
        style = 'font-size: 10px; line-height: 1.1; vertical-align: middle;'
        tev = row["Telafi Edici Vergi"]
        
        if tev == "Kayıt Bulunamadı": bg = "background-color: #f8f9fa;"
        elif tev == "Ödeme Yoktur": bg = "background-color: #f0fff4; color: #1a7f37; font-weight: bold;"
        elif row["odeme_var"] is True: bg = "background-color: #fff5f5; color: #d73a49; font-weight: bold;"
        else: bg = ""
        
        return [f"{style} {bg}"] * len(row)

    styled_df = df.style.apply(style_table, axis=1)

    # Sütun genişliklerini manuel ayarlayarak taşmayı önlüyoruz
    st.dataframe(
        styled_df,
        use_container_width=True,
        hide_index=True,
        column_order=("İhracat Beyannamesi", "Gönderen", "Vergino", "Telafi Edici Vergi", "Tahsilat Yeri"),
        column_config={
            "Gönderen": st.column_config.TextColumn("Gönderen", width="medium"),
            "Telafi Edici Vergi": st.column_config.TextColumn("TEV Tutarı", width="small"),
            "Vergino": st.column_config.TextColumn("Vergi No", width="small")
        }
    )
