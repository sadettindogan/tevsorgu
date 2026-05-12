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
        /* Ana konteyneri %80 oranında küçült ve ortala */
        .main .block-container {
            transform: scale(0.8);
            transform-origin: top center;
        }
        /* Tablo içindeki yazıların alt satıra geçmesini ve küçük fontu zorla */
        .stDataFrame div[data-testid="stTable"] {
            font-size: 10px !important;
        }
        /* Gereksiz boşlukları daralt */
        .block-container {
            padding-top: 2rem;
        }
    </style>
    """,
    unsafe_allow_html=True
)

st.title("TEV Ödeme Sorgulama Portali")
st.markdown("Tescil numaralarını Excel'den kopyalayıp yapıştırın.")

# --- SESSION STATE ---
for key in ["zip_bytes", "merged_pdf_bytes", "query_results"]:
    if key not in st.session_state:
        st.session_state[key] = None

raw_data = st.text_area("Tescil Numaraları", height=150, placeholder="20230000...")

# --- SORGU MODU SEÇİMİ ---
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

        gonderen      = get_by_id("Lab_ver_gonderen")
        vergino       = get_by_id("Lab_ver_vergino")
        tev_value     = get_by_id("Lab_ver_telafi")
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

def wait_for_result(page, previous_tescil=None):
    timeout, interval, elapsed = 10, 0.3, 0
    while elapsed < timeout:
        try:
            body_text = page.inner_text("body")
            if previous_tescil and previous_tescil in body_text:
                time.sleep(interval); elapsed += interval; continue
            if any(kw in body_text.lower() for kw in ["telafi edici vergi", "ödeme yoktur", "kayıt bulunamadı"]):
                return
        except: pass
        time.sleep(interval); elapsed += interval

if start_query:
    tescil_list = [t.strip() for t in raw_data.split('\n') if t.strip()]
    if not tescil_list:
        st.error("Lütfen tescil numarası girin!")
    else:
        st.session_state.zip_bytes = None
        st.session_state.merged_pdf_bytes = None
        st.session_state.query_results = None

        progress_bar = st.progress(0)
        status_text = st.empty()
        pdf_results, pdf_list_for_merge, query_results = {}, [], []
        toplam_baslangic = time.time()

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
                page = browser.new_page()
                url = "https://uygulama.gtb.gov.tr/TEV/"
                page.goto(url)
                previous_tescil = None

                for index, tescil_no in enumerate(tescil_list):
                    status_text.text(f"⏳ Sorgulanıyor: {tescil_no} ({index+1}/{len(tescil_list)})")
                    page.fill("#TextBox_Beyanname", tescil_no)
                    page.click("#Btn_Ara")
                    wait_for_result(page, previous_tescil)

                    res = extract_tev_result(page)
                    query_results.append({
                        "İhracat Beyannamesi": tescil_no,
                        "Gönderen": res[0],
                        "Vergi No": res[1],
                        "TEV Tutarı": res[2],
                        "Tahsilat Yeri": res[3],
                        "odeme_var": res[4]
                    })

                    if pdf_mode and res[2] != "Kayıt Bulunamadı":
                        page.emulate_media(media="print")
                        pdf_content = page.pdf(format="A4")
                        pdf_results[f"{tescil_no}.pdf"] = pdf_content
                        pdf_list_for_merge.append(pdf_content)
                    
                    previous_tescil = tescil_no
                    progress_bar.progress((index + 1) / len(tescil_list))
                browser.close()

            st.session_state.query_results = query_results
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

            status_text.text(f"✅ Tamamlandı! Toplam Süre: {time.time()-toplam_baslangic:.1f}s")
        except Exception as e:
            st.error(f"Sistem Hatası: {str(e)}")

# --- SONUÇLARI GÖSTER ---
if st.session_state.query_results:
    st.markdown("---")
    
    if st.session_state.zip_bytes or st.session_state.merged_pdf_bytes:
        st.markdown("### 📥 PDF İndir")
        c1, c2 = st.columns(2)
        with c1:
            if st.session_state.merged_pdf_bytes:
                st.download_button("📄 Tek PDF İndir", st.session_state.merged_pdf_bytes, "Tev_Sonuclar.pdf", use_container_width=True)
        with c2:
            if st.session_state.zip_bytes:
                st.download_button("📦 ZIP İndir", st.session_state.zip_bytes, "Tev_Arsiv.zip", use_container_width=True)

    st.markdown("### 🔍 Sonuç Detay")
    df = pd.DataFrame(st.session_state.query_results)

    def style_table(row):
        # Yazı boyutunu 10px yapıp alt satıra geçmeyi (wrap) aktif ettik
        style = 'font-size: 10px; white-space: normal; word-wrap: break-word; line-height: 1.2;'
        tev = row["TEV Tutarı"]
        if tev == "Kayıt Bulunamadı": bg = "background-color: #f8f9fa;"
        elif tev == "Ödeme Yoktur": bg = "background-color: #f0fff4; color: #1a7f37; font-weight: bold;"
        elif row["odeme_var"] is True: bg = "background-color: #fff5f5; color: #d73a49; font-weight: bold;"
        else: bg = ""
        return [f"{style} {bg}"] * len(row)

    styled_df = df.style.apply(style_table, axis=1)

    st.dataframe(
        styled_df,
        use_container_width=True,
        hide_index=True,
        column_order=("İhracat Beyannamesi", "Gönderen", "Vergi No", "TEV Tutarı", "Tahsilat Yeri"),
        column_config={
            "Gönderen": st.column_config.TextColumn("Gönderen Ünvanı", width="large"),
            "İhracat Beyannamesi": st.column_config.TextColumn("Beyanname No", width="medium"),
            "TEV Tutarı": st.column_config.TextColumn("Tutar", width="small")
        }
    )
