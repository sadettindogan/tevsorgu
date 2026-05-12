import streamlit as st
from playwright.sync_api import sync_playwright
from pypdf import PdfWriter
import time
import io
import zipfile
import pandas as pd
import re

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="TEV Odeme Sorgulama", page_icon="")
st.title("TEV Ödeme Sorgulama Portali")

st.markdown("Tescil numaralarını Excel'den kopyalayıp yapıştırın.")

# --- SESSION STATE ---
for key in ["zip_bytes", "merged_pdf_bytes", "query_results"]:
    if key not in st.session_state:
        st.session_state[key] = None

raw_data = st.text_area("Tescil Numaraları", height=200, placeholder="20230000...")

# --- SORGU MODU SEÇİMİ ---
col_btn1, col_btn2 = st.columns(2)
with col_btn1:
    btn_sadece_sonuc = st.button("🔍 Sorgula (Sadece Sonuç Gösterir)", use_container_width=True, type="primary")
with col_btn2:
    btn_pdf_al = st.button("📄 Sorgula (Sonuç + PDF Alır)", use_container_width=True)

pdf_mode = btn_pdf_al
start_query = btn_sadece_sonuc or btn_pdf_al


def extract_tev_result(page):
    """
    Sayfadan TEV alanlarını okur.
    Sayfa yapısı: label ve değer ayrı satırlarda (tablo/grid).
      Gönderen
      STARCOOL ...
      Vergino
      7810163646
      Telafi Edici Vergi
      1908,61
      Tahsilat Yeri
      Banka

    Döner: (gonderen, vergino, tev_degeri, tahsilat_yeri, has_payment)
    """
    try:
        page_text = page.inner_text("body")

        # Kayıt bulunamadı kontrolü
        if "kayıt bulunamadı" in page_text.lower() or "kayit bulunamadi" in page_text.lower():
            return "-", "-", "Kayıt Bulunamadı", "-", None

        # Ödeme yoktur kontrolü
        if "ödeme yoktur" in page_text.lower() or "odeme yoktur" in page_text.lower():
            return "-", "-", "Ödeme Yoktur", "-", False

        lines = [l.strip() for l in page_text.splitlines() if l.strip()]

        def find_value(label_keywords):
            """
            Verilen keyword'lerden birini içeren satırı bulur,
            sonraki anlamlı satırı değer olarak döner.
            Aynı satırda da değer arayabilir (fallback).
            """
            for i, line in enumerate(lines):
                line_lower = line.lower()
                for kw in label_keywords:
                    if kw in line_lower:
                        # Önce aynı satırda label'dan sonrası
                        after = line[line_lower.index(kw) + len(kw):].strip()
                        if after:
                            return after
                        # Sonraki satır
                        if i + 1 < len(lines):
                            return lines[i + 1].strip()
            return "-"

        gonderen      = find_value(["gönderen", "gonderen"])
        vergino       = find_value(["vergino", "vergi no", "vergi numarası"])
        tev_raw       = find_value(["telafi edici vergi"])
        tahsilat_yeri = find_value(["tahsilat yeri"])

        # TEV değerini temizle ve sayısallığını kontrol et
        has_payment = None
        tev_value = tev_raw.strip() if tev_raw else "-"

        if tev_value and tev_value != "-":
            # Sadece rakam/nokta/virgülden oluşuyorsa direkt kabul
            if re.match(r"^[\d.,\s]+$", tev_value):
                has_payment = True
            else:
                # İçinden sayı çekmeye çalış
                nums = re.findall(r"\d[\d.,]*", tev_value)
                if nums:
                    tev_value = nums[0]
                    has_payment = True

        # --- Fallback: HTML tablo araması ---
        if tev_value == "-":
            rows = page.query_selector_all("tr")
            for row in rows:
                cells = row.query_selector_all("th, td")
                for i, cell in enumerate(cells):
                    if "telafi edici vergi" in cell.inner_text().lower() and i + 1 < len(cells):
                        val = cells[i + 1].inner_text().strip()
                        if val:
                            tev_value = val
                            has_payment = True if re.search(r"\d", val) else None

        # --- Fallback 2: span/div araması ---
        if tev_value == "-":
            all_els = page.query_selector_all("span, td, div, label, p, th")
            for i, el in enumerate(all_els):
                if "telafi edici vergi" in el.inner_text().lower():
                    for offset in range(1, 4):
                        if i + offset < len(all_els):
                            candidate = all_els[i + offset].inner_text().strip()
                            if re.match(r"^[\d.,]+$", candidate):
                                tev_value = candidate
                                has_payment = True
                                break

        return gonderen, vergino, tev_value, tahsilat_yeri, has_payment

    except Exception as ex:
        return "-", "-", f"Hata: {str(ex)}", "-", None


def wait_for_result(page, previous_tescil=None):
    """
    Yeni sonucun yüklendiğini akıllıca bekler. Maks 10 saniye.
    """
    timeout = 10
    interval = 0.3
    elapsed = 0

    while elapsed < timeout:
        try:
            body_text = page.inner_text("body")
            if previous_tescil and previous_tescil in body_text:
                time.sleep(interval)
                elapsed += interval
                continue
            if any(kw in body_text.lower() for kw in [
                "telafi edici vergi", "ödeme yoktur", "odeme yoktur",
                "kayıt bulunamadı", "kayit bulunamadi"
            ]):
                return
        except Exception:
            pass
        time.sleep(interval)
        elapsed += interval


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
        timer_text = st.empty()
        pdf_results = {}
        pdf_list_for_merge = []
        query_results = []

        toplam_baslangic = time.time()

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    executable_path="/usr/bin/chromium",
                    args=["--no-sandbox", "--disable-dev-shm-usage"]
                )
                context = browser.new_context()
                page = context.new_page()
                url = "https://uygulama.gtb.gov.tr/TEV/"

                page.goto(url)
                page.wait_for_load_state("networkidle")
                previous_tescil = None

                for index, tescil_no in enumerate(tescil_list):
                    sorgu_baslangic = time.time()
                    try:
                        status_text.text(f"⏳ Sorgulanıyor: {tescil_no} ({index+1}/{len(tescil_list)})")

                        page.fill("#TextBox_Beyanname", "")
                        page.fill("#TextBox_Beyanname", tescil_no)
                        page.click("#Btn_Ara")

                        wait_for_result(page, previous_tescil)

                        sorgu_sure = time.time() - sorgu_baslangic
                        toplam_sure = time.time() - toplam_baslangic
                        timer_text.text(
                            f"⏱ Son sorgu: {sorgu_sure:.1f}s  |  Toplam: {toplam_sure:.1f}s"
                        )

                        gonderen, vergino, tev_value, tahsilat_yeri, has_payment = extract_tev_result(page)

                        query_results.append({
                            "İhracat Beyannamesi": tescil_no,
                            "Gönderen": gonderen,
                            "Vergino": vergino,
                            "Telafi Edici Vergi": tev_value,
                            "Tahsilat Yeri": tahsilat_yeri,
                            "odeme_var": has_payment
                        })

                        if pdf_mode and tev_value != "Kayıt Bulunamadı":
                            page.emulate_media(media="print")
                            pdf_content = page.pdf(format="A4")
                            pdf_results[f"{tescil_no}.pdf"] = pdf_content
                            pdf_list_for_merge.append(pdf_content)
                            page.emulate_media(media="screen")

                        previous_tescil = tescil_no

                    except Exception as e:
                        st.error(f"{tescil_no} hatası: {str(e)}")
                        query_results.append({
                            "İhracat Beyannamesi": tescil_no,
                            "Gönderen": "-",
                            "Vergino": "-",
                            "Telafi Edici Vergi": "Hata",
                            "Tahsilat Yeri": "-",
                            "odeme_var": None
                        })
                        try:
                            page.goto(url)
                            page.wait_for_load_state("networkidle")
                            previous_tescil = None
                        except Exception:
                            pass

                    progress_bar.progress((index + 1) / len(tescil_list))

                browser.close()

            toplam_sure = time.time() - toplam_baslangic
            st.session_state.query_results = query_results

            if pdf_mode and pdf_results:
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

            status_text.text(f"✅ Tamamlandı! ({len(tescil_list)} sorgu)")
            timer_text.text(
                f"⏱ Toplam süre: {toplam_sure:.1f}s  |  Ortalama: {toplam_sure/len(tescil_list):.1f}s/sorgu"
            )

        except Exception as main_e:
            st.error(f"Sistem Hatası: {str(main_e)}")


# --- SORGU SONUÇLARI TABLO ---
if st.session_state.query_results:
    st.markdown("---")
    st.markdown("### 📊 Sorgu Sonuçları")

    display_rows = []
    for r in st.session_state.query_results:
        tev = r["Telafi Edici Vergi"]
        if tev == "Kayıt Bulunamadı":
            durum = "⚪ Kayıt Bulunamadı"
        elif tev == "Ödeme Yoktur":
            durum = "✅ Ödeme Yoktur"
        elif r["odeme_var"] is True:
            durum = "🔴 Ödeme Var"
        elif tev == "Hata":
            durum = "❌ Hata"
        else:
            durum = "❓ Belirsiz"

        display_rows.append({
            "İhracat Beyannamesi": r["İhracat Beyannamesi"],
            "Gönderen": r["Gönderen"],
            "Vergino": r["Vergino"],
            "Telafi Edici Vergi": tev,
            "Tahsilat Yeri": r["Tahsilat Yeri"],
            "Durum": durum,
        })

    df = pd.DataFrame(display_rows)

    def highlight_row(row):
        tev = row["Telafi Edici Vergi"]
        if tev == "Kayıt Bulunamadı":
            return ["background-color: #f0f0f0; color: #888"] * len(row)
        elif tev == "Ödeme Yoktur":
            return ["background-color: #e6f4ea; color: #2e7d32"] * len(row)
        elif row["Durum"].startswith("🔴"):
            return ["background-color: #fdecea; color: #c62828"] * len(row)
        elif tev == "Hata":
            return ["background-color: #fff8e1; color: #f57f17"] * len(row)
        return [""] * len(row)

    styled_df = df.style.apply(highlight_row, axis=1)
    st.dataframe(styled_df, use_container_width=True, hide_index=True)

    # Sadece TEV değerleri — Excel'e yapıştırılabilir
    st.markdown("**Telafi Edici Vergi Değerleri (Excel'e yapıştırılabilir):**")
    tev_lines = [r["Telafi Edici Vergi"] for r in display_rows]
    st.code("\n".join(tev_lines), language=None)


# --- İNDİRME SEÇENEKLERİ ---
if st.session_state.zip_bytes or st.session_state.merged_pdf_bytes:
    st.markdown("### 📥 PDF İndir")
    col1, col2 = st.columns(2)
    with col1:
        if st.session_state.merged_pdf_bytes:
            st.download_button(
                label="Birleştirilmiş Tek PDF İndir",
                data=st.session_state.merged_pdf_bytes,
                file_name="Tev_Tum_Sorgular_Birlestirilmis.pdf",
                mime="application/pdf",
                use_container_width=True
            )
    with col2:
        if st.session_state.zip_bytes:
            st.download_button(
                label="PDF'leri Ayrı Ayrı İndir (ZIP)",
                data=st.session_state.zip_bytes,
                file_name="Tev_Sorgu_Arsivi.zip",
                mime="application/zip",
                use_container_width=True
            )
