import streamlit as st
from playwright.sync_api import sync_playwright
from pypdf import PdfWriter
import time
import io
import zipfile
import pandas as pd

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="TEV Odeme Sorgulama", page_icon="")
st.title("TEV Ödeme Sorgulama Portali")

st.markdown("""
Tescil numaralarını Excel'den kopyalayıp yapıştırın.
""")

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
    Döner: (gonderen, vergino, tev_degeri, tahsilat_yeri, has_payment)
    """
    try:
        import re
        page_text = page.inner_text("body")

        # Kayıt bulunamadı kontrolü
        if "kayıt bulunamadı" in page_text.lower() or "kayit bulunamadi" in page_text.lower():
            return "-", "-", "Kayıt Bulunamadı", "-", None

        # Ödeme yoktur kontrolü
        if "odeme yoktur" in page_text.lower() or "ödeme yoktur" in page_text.lower():
            return "-", "-", "Ödeme Yoktur", "-", False

        lines = [l.strip() for l in page_text.splitlines() if l.strip()]

        def get_value_after_label(label):
            for i, line in enumerate(lines):
                if label.lower() in line.lower():
                    after = line[line.lower().index(label.lower()) + len(label):].strip()
                    if after and after not in [":", ""]:
                        return after.lstrip(":").strip()
                    if i + 1 < len(lines):
                        return lines[i + 1].strip()
            return "-"

        gonderen = get_value_after_label("Gönderen")
        vergino = get_value_after_label("Vergino") or get_value_after_label("Vergi No")
        tahsilat_yeri = get_value_after_label("Tahsilat Yeri")

        # TEV değeri
        tev_value = "-"
        has_payment = None
        for i, line in enumerate(lines):
            if "telafi edici vergi" in line.lower():
                same_line = re.search(r"[\d.,]+", line[line.lower().index("telafi edici vergi") + len("telafi edici vergi"):])
                if same_line:
                    tev_value = same_line.group()
                    has_payment = True
                    break
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if re.match(r"^[\d.,]+$", next_line):
                        tev_value = next_line
                        has_payment = True
                        break
                    nums = re.findall(r"[\d]{1,}[.,][\d]+", next_line)
                    if nums:
                        tev_value = nums[0]
                        has_payment = True
                        break

        # Tablo araması
        if tev_value == "-":
            rows = page.query_selector_all("tr")
            for row in rows:
                cells = row.query_selector_all("th, td")
                for i, cell in enumerate(cells):
                    txt = cell.inner_text().lower()
                    if "telafi edici vergi" in txt and i + 1 < len(cells):
                        tev_value = cells[i + 1].inner_text().strip()
                        has_payment = True if tev_value and tev_value != "-" else None

        return gonderen, vergino, tev_value, tahsilat_yeri, has_payment

    except Exception as ex:
        return "-", "-", f"Hata: {str(ex)}", "-", None


if start_query:
    tescil_list = [t.strip() for t in raw_data.split('\n') if t.strip()]

    if not tescil_list:
        st.error("Lütfen tescil numarası girin!")
    else:
        # Reset
        st.session_state.zip_bytes = None
        st.session_state.merged_pdf_bytes = None
        st.session_state.query_results = None

        progress_bar = st.progress(0)
        status_text = st.empty()
        pdf_results = {}
        pdf_list_for_merge = []
        query_results = []

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

                for index, tescil_no in enumerate(tescil_list):
                    try:
                        status_text.text(f"Sorgulanıyor: {tescil_no} ({index+1}/{len(tescil_list)})")
                        page.goto(url)
                        page.fill("#TextBox_Beyanname", tescil_no)
                        page.click("#Btn_Ara")
                        time.sleep(5)

                        gonderen, vergino, tev_value, tahsilat_yeri, has_payment = extract_tev_result(page)

                        query_results.append({
                            "İhracat Beyannamesi": tescil_no,
                            "Gönderen": gonderen,
                            "Vergino": vergino,
                            "Telafi Edici Vergi": tev_value,
                            "Tahsilat Yeri": tahsilat_yeri,
                            "odeme_var": has_payment
                        })

                        # PDF modu ve kayıt bulunanlar için PDF al
                        if pdf_mode and tev_value != "Kayıt Bulunamadı":
                            page.emulate_media(media="print")
                            pdf_content = page.pdf(format="A4")
                            pdf_results[f"{tescil_no}.pdf"] = pdf_content
                            pdf_list_for_merge.append(pdf_content)

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

                    progress_bar.progress((index + 1) / len(tescil_list))

                browser.close()

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

            status_text.text("✅ Tüm işlemler tamamlandı!")
            st.success("Sorgulama tamamlandı!")

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

    # Excel'e kopyalanabilir metin
    copy_lines = ["İhracat Beyannamesi\tGönderen\tVergino\tTelafi Edici Vergi\tTahsilat Yeri"]
    for r in display_rows:
        copy_lines.append(
            f"{r['İhracat Beyannamesi']}\t{r['Gönderen']}\t{r['Vergino']}\t{r['Telafi Edici Vergi']}\t{r['Tahsilat Yeri']}"
        )
    st.markdown("**Sonuçları Kopyala (Excel'e yapıştırılabilir):**")
    st.code("\n".join(copy_lines), language=None)


# --- İNDİRME SEÇENEKLERİ (sadece PDF modunda) ---
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
