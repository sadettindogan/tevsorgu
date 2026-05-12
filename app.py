import streamlit as st
from playwright.sync_api import sync_playwright
import os
import time
import io
import zipfile

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="TEV Ödeme Sorgulama", page_icon="📄")
st.title("📄 TEV Ödeme Sorgulama Portalı")

st.markdown("""
Tescil numaralarını Excel'den kopyalayıp aşağıdaki kutuya yapıştırın. 
Sistem her birini sorgulayıp PDF olarak hazırlayacaktır.
""")

# --- SESSION STATE ---
if "zip_bytes" not in st.session_state:
    st.session_state.zip_bytes = None

# --- VERİ GİRİŞİ ---
raw_data = st.text_area("Tescil Numaraları (Her satıra bir tane)", height=200, placeholder="20230000... \n20240000...")

if st.button("🚀 Sorgulamayı Başlat", type="primary"):
    tescil_list = [t.strip() for t in raw_data.split('\n') if t.strip()]
    
    if not tescil_list:
        st.error("⚠️ Lütfen en az bir tescil numarası girin!")
    else:
        progress_bar = st.progress(0)
        status_text = st.empty()
        pdf_results = {} # Dosya adı: Byte içeriği
        
        try:
            with sync_playwright() as p:
                # Tarayıcı başlatma (Paylaştığın çalışan kodun ayarlarıyla aynı)
                browser = p.chromium.launch(
                    headless=True,
                    executable_path="/usr/bin/chromium",
                    args=["--no-sandbox", "--disable-dev-shm-usage"]
                )
                
                # PDF yazdırma desteği için context
                context = browser.new_context()
                page = context.new_page()
                url = "https://uygulama.gtb.gov.tr/TEV/"

                for index, tescil_no in enumerate(tescil_list):
                    try:
                        status_text.text(f"Sorgulanıyor: {tescil_no} ({index+1}/{len(tescil_list)})")
                        
                        # Sayfaya git
                        page.goto(url)
                        
                        # Input ve Ara
                        page.fill("#TextBox_Beyanname", tescil_no)
                        page.click("#Btn_Ara")
                        
                        # Sonucun yüklenmesi için kısa bir bekleme
                        time.sleep(5) 
                        
                        # Sayfayı PDF olarak kaydet
                        # Emulate media 'print' yaparak Selenium'daki window.print() etkisini yaratıyoruz
                        page.emulate_media(media="print")
                        pdf_content = page.pdf(format="A4")
                        
                        pdf_results[f"{tescil_no}.pdf"] = pdf_content
                        st.write(f"✅ {tescil_no} hazır.")
                        
                    except Exception as e:
                        st.error(f"❌ {tescil_no} sorgulanırken hata oluştu: {str(e)}")
                    
                    progress_bar.progress((index + 1) / len(tescil_list))

                browser.close()

            # ZIP Dosyası Oluşturma
            if pdf_results:
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                    for filename, content in pdf_results.items():
                        zf.writestr(filename, content)
                
                st.session_state.zip_bytes = zip_buffer.getvalue()
                st.success("Tüm sorgulamalar tamamlandı!")
            else:
                st.warning("Hiç PDF oluşturulamadı.")

        except Exception as main_e:
            st.error(f"Sistem Hatası: {str(main_e)}")

# --- İNDİRME BUTONU ---
if st.session_state.zip_bytes:
    st.download_button(
        label="📥 Tüm PDF'leri İndir (ZIP)",
        data=st.session_state.zip_bytes,
        file_name="TEV_Sorgu_Sonuclari.zip",
        mime="application/zip"
    )
