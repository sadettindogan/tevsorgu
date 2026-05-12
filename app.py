import streamlit as st
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import os
import time
import glob

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="TEV Ödeme Sorgulama", layout="wide")
st.title("📄 TEV Ödeme Sorgulama Sistemi")
st.info("Tescil numaralarını alta yapıştırın ve işlemi başlatın.")

# --- SIDEBAR / AYARLAR ---
with st.sidebar:
    st.header("⚙️ Ayarlar")
    pdf_folder = st.text_input("PDF Kayıt Klasörü", value=os.path.join(os.path.expanduser("~"), "Desktop", "TEV_Sorgu_Sonuclari"))
    wait_time = st.slider("Bekleme Süresi (Saniye)", 3, 15, 6)

# --- VERİ GİRİŞİ ---
raw_data = st.text_area("Tescil Numaraları (Her satıra bir tane gelecek şekilde yapıştırın)", height=200)

if st.button("Sorgulamayı Başlat"):
    # Girdiyi temizle ve listeye çevir
    tescil_list = [t.strip() for t in raw_data.split('\n') if t.strip()]
    
    if not tescil_list:
        st.error("Lütfen en az bir tescil numarası girin!")
    else:
        if not os.path.exists(pdf_folder):
            os.makedirs(pdf_folder, exist_ok=True)

        progress_bar = st.progress(0)
        status_text = st.empty()
        log_area = st.expander("İşlem Günlüğü", expanded=True)

        try:
            # --- CHROME AYARLARI ---
            chrome_options = Options()
            chrome_options.add_argument("--start-maximized")
            
            # PDF yazdırma ayarları
            prefs = {
                "printing.print_preview_sticky_settings.appState": f'{{"recentDestinations": [{{"id": "Save as PDF", "origin": "local"}}], "selectedDestinationId": "Save as PDF", "version": 2}}',
                "savefile.default_directory": pdf_folder
            }
            chrome_options.add_experimental_option("prefs", prefs)
            chrome_options.add_argument("--kiosk-printing")

            driver = webdriver.Chrome(options=chrome_options)
            wait = WebDriverWait(driver, 20)
            url = "https://uygulama.gtb.gov.tr/TEV/"

            for index, tescil_no in enumerate(tescil_list):
                try:
                    status_text.text(f"Şu an sorgulanıyor: {tescil_no} ({index+1}/{len(tescil_list)})")
                    
                    before_pdfs = set(glob.glob(os.path.join(pdf_folder, "*.pdf")))
                    
                    driver.get(url)
                    
                    # Input kutusu
                    input_box = wait.until(EC.presence_of_element_located((By.ID, "TextBox_Beyanname")))
                    input_box.clear()
                    input_box.send_keys(tescil_no)

                    # Ara butonu
                    ara_btn = wait.until(EC.element_to_be_clickable((By.ID, "Btn_Ara")))
                    ara_btn.click()

                    # Sonuç için bekleme
                    time.sleep(wait_time)

                    # PDF Yazdır
                    driver.execute_script("window.print();")
                    time.sleep(3)

                    # Dosya isimlendirme
                    after_pdfs = set(glob.glob(os.path.join(pdf_folder, "*.pdf")))
                    new_pdfs = list(after_pdfs - before_pdfs)

                    if new_pdfs:
                        latest_pdf = max(new_pdfs, key=os.path.getctime)
                        new_pdf_path = os.path.join(pdf_folder, f"{tescil_no}.pdf")
                        
                        # Eğer dosya zaten varsa ismini değiştir
                        if os.path.exists(new_pdf_path):
                            new_pdf_path = os.path.join(pdf_folder, f"{tescil_no}_{int(time.time())}.pdf")
                        
                        os.rename(latest_pdf, new_pdf_path)
                        log_area.write(f"✅ Başarılı: {tescil_no}")
                    else:
                        log_area.write(f"⚠️ PDF oluşturulamadı: {tescil_no}")

                except Exception as e:
                    log_area.write(f"❌ Hata ({tescil_no}): {str(e)}")
                
                # Progress güncelle
                progress_bar.progress((index + 1) / len(tescil_list))

            driver.quit()
            st.success("Tüm işlemler tamamlandı!")
            st.balloons()

        except Exception as main_e:
            st.error(f"Genel bir hata oluştu: {main_e}")
