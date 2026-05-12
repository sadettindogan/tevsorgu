import streamlit as st
from playwright.sync_api import sync_playwright
from pypdf import PdfWriter
import os
import time
import io
import zipfile

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="TEV Ödeme Sorgulama", page_icon="📄")
st.title("📄 TEV Ödeme Sorgulama Portalı")

st.markdown("""
Tescil numaralarını Excel'den kopyalayıp yapıştırın. 
Sistem veriyi okuyacak, ekrana sonucu basacak ve PDF'leri hazırlayacaktır.
""")

# --- SESSION STATE ---
if "zip_bytes" not in st.session_state:
    st.session_state.zip_bytes = None
if "merged_pdf_bytes" not in st.session_state:
    st.session_state.merged_pdf_bytes = None
if "results_text" not in st.session_state:
    st.session_state.results_text = ""

# --- VERİ GİRİŞİ ---
raw_data = st.text_area("Tescil Numaraları", height=200, placeholder="20230000...")

if st.button("🚀 Sorgulamayı Başlat", type="primary"):
    tescil_list = [t.strip() for t in raw_data.split('\n') if t.strip()]
    
    if not tescil_list:
        st.error("⚠️ Lütfen tescil numarası girin!")
    else:
        progress_bar = st.progress(0)
        status_text = st.empty()
        pdf_results = {}
        pdf_list_for_merge = []
        extracted_data = [] # Kopyalanacak metinler için

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"]
                )
                context = browser.new_context()
                page = context.new_page()
                url = "https://uygulama.gtb.gov.tr/TEV/"

                for index, tescil_no in enumerate(tescil_list):
                    try:
                        status_text.text(f"Sorgulanıyor: {tescil_no} ({index+1}/{len(tescil_list)})")
                        page.goto(url)
                        page.fill("#TextBox_Beyanname", tescil_no)
                        page.click("#Btn_Ara")
                        
                        # Sayfanın yüklenmesi ve verinin gelmesi için bekleme
                        time.sleep(3) 

                        # --- VERİ OKUMA MANTIĞI ---
                        # Sayfada 'Telafi Edici Vergi' yazan hücrenin yanındaki değeri bulmaya çalışır
                        try:
                            # Tablo yapısına göre vergi değerini çeker
                            val_element = page.query_selector("td:has-text('Telafi Edici Vergi') + td")
                            if val_element:
                                tev_degeri = val_element.inner_text().strip()
                                result_msg = f"{tescil_no}: {tev_degeri}"
                            else:
                                result_msg = f"{tescil_no}: Ödeme Yoktur"
                        except:
                            result_msg = f"{tescil_no}: Ödeme Yoktur"
                        
                        extracted_data.append(result_msg)
                        
                        # PDF Oluşturma
                        page.emulate_media(media="print")
                        pdf_content = page.pdf(format="A4")
                        pdf_results[f"{tescil_no}.pdf"] = pdf_content
                        pdf_list_for_merge.append(pdf_content)
                        
                        st.write(f"✅ {result_msg}")
                        
                    except Exception as e:
                        st.error(f"❌ {tescil_no} hatası: {str(e)}")
                    
                    progress_bar.progress((index + 1) / len(tescil_list))

                browser.close()

            # Sonuçları toplu metin haline getir
            st.session_state.results_text = "\n".join(extracted_data)

            # ZIP ve PDF Birleştirme İşlemleri
            if pdf_results:
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                    for filename, content in pdf_results.items():
                        zf.writestr(filename, content)
                st.session_state.zip_bytes = zip_buffer.getvalue()

                merger = PdfWriter()
                for pdf_data in pdf_list_for_merge:
                    merger.append(io.BytesIO(pdf_data))
                
                merged_buffer = io.BytesIO()
                merger.write(merged_buffer)
                st.session_state.merged_pdf_bytes = merged_buffer.getvalue()
                merger.close()

                st.success("Tüm işlemler tamamlandı!")

        except Exception as main_e:
            st.error(f"Sistem Hatası: {str(main_e)}")

# --- SONUÇLARI GÖSTER VE KOPYALA ---
if st.session_state.results_text:
    st.markdown("---")
    st.subheader("📋 Sorgulama Sonuçları")
    # st.code bileşeni sağ üstte otomatik "copy" butonu sunar
    st.code(st.session_state.results_text, language="text")
    st.caption("Yukarıdaki sonuçları sağ üstteki butona basarak kopyalayabilirsiniz.")

# --- İNDİRME SEÇENEKLERİ ---
if st.session_state.zip_bytes or st.session_state.merged_pdf_bytes:
    st.markdown("### 📥 PDF Dosyalarını İndir")
    col1, col2 = st.columns(2)
    
    with col1:
        if st.session_state.merged_pdf_bytes:
            st.download_button(
                label="📄 Birleştirilmiş Tek PDF İndir",
                data=st.session_state.merged_pdf_bytes,
                file_name="Tev_Tum_Sorgular_Birlestirilmis.pdf",
                mime="application/pdf",
                use_container_width=True
            )
            
    with col2:
        if st.session_state.zip_bytes:
            st.download_button(
                label="📦 PDF'leri Ayrı Ayrı İndir (ZIP)",
                data=st.session_state.zip_bytes,
                file_name="Tev_Sorgu_Arsivi.zip",
                mime="application/zip",
                use_container_width=True
            )
