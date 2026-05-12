import streamlit as st
from playwright.sync_api import sync_playwright
from pypdf import PdfWriter
import time
import io
import zipfile

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="TEV Odeme Sorgulama", page_icon="")
st.title(" TEV Odeme Sorgulama Portali")

st.markdown("""
Tescil numaralarini Excel'den kopyalayip yapistirin.
Sistem her birini sorgulayacak, hem tek tek hem de **birlestirilmis** olarak sunacaktir.
""")

# --- SESSION STATE ---
for key in ["zip_bytes", "merged_pdf_bytes", "query_results"]:
    if key not in st.session_state:
        st.session_state[key] = None

raw_data = st.text_area("Tescil Numaralari", height=200, placeholder="20230000...")


def extract_tev_result(page):
    try:
        page_text = page.inner_text("body").lower()
        if "odeme yoktur" in page_text or "\u00f6deme yoktur" in page_text:
            return "Odeme Yoktur", False

        rows = page.query_selector_all("tr")
        for row in rows:
            cells = row.query_selector_all("th, td")
            for i, cell in enumerate(cells):
                cell_text = cell.inner_text().strip()
                if "telafi edici vergi" in cell_text.lower():
                    if i + 1 < len(cells):
                        value = cells[i + 1].inner_text().strip()
                        return value, True

        return "Deger okunamadi", None

    except Exception:
        return "Hata", None


if st.button("Sorgulamayi Baslat", type="primary"):
    tescil_list = [t.strip() for t in raw_data.split('\n') if t.strip()]

    if not tescil_list:
        st.error("Lutfen tescil numarasi girin!")
    else:
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

                        tev_value, has_payment = extract_tev_result(page)
                        query_results.append({
                            "tescil": tescil_no,
                            "deger": tev_value,
                            "odeme_var": has_payment
                        })

                        page.emulate_media(media="print")
                        pdf_content = page.pdf(format="A4")
                        pdf_results[f"{tescil_no}.pdf"] = pdf_content
                        pdf_list_for_merge.append(pdf_content)

                    except Exception as e:
                        st.error(f"{tescil_no} hatasi: {str(e)}")
                        query_results.append({"tescil": tescil_no, "deger": "Hata", "odeme_var": None})

                    progress_bar.progress((index + 1) / len(tescil_list))

                browser.close()

            st.session_state.query_results = query_results

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

                st.success("Tum islemler tamamlandi!")
            else:
                st.warning("Sonuc alinamadi.")

        except Exception as main_e:
            st.error(f"Sistem Hatasi: {str(main_e)}")


# --- SORGU SONUCLARI ---
if st.session_state.query_results:
    st.markdown("---")
    st.markdown("### Sorgu Sonuclari")

    result_lines = []
    for r in st.session_state.query_results:
        if r["odeme_var"] is False:
            icon = "ODEME YOKTUR"
            label = "Odeme Yoktur"
        elif r["odeme_var"] is True:
            icon = "ODEME VAR"
            label = f"Telafi Edici Vergi: {r['deger']}"
        else:
            icon = "HATA"
            label = r["deger"]

        st.markdown(f"**{r['tescil']}** → {icon} | {label}")
        result_lines.append(f"{r['tescil']}\t{label}")

    copy_text = "\n".join(result_lines)
    st.markdown("**Sonuclari Kopyala:**")
    st.code(copy_text, language=None)


# --- INDIRME SECENEKLERI ---
if st.session_state.zip_bytes or st.session_state.merged_pdf_bytes:
    st.markdown("### Sonuclari Indir")
    col1, col2 = st.columns(2)
    with col1:
        if st.session_state.merged_pdf_bytes:
            st.download_button(
                label="Birlestirilmis Tek PDF Indir",
                data=st.session_state.merged_pdf_bytes,
                file_name="Tev_Tum_Sorgular_Birlestirilmis.pdf",
                mime="application/pdf",
                use_container_width=True
            )
    with col2:
        if st.session_state.zip_bytes:
            st.download_button(
                label="PDF'leri Ayri Ayri Indir (ZIP)",
                data=st.session_state.zip_bytes,
                file_name="Tev_Sorgu_Arsivi.zip",
                mime="application/zip",
                use_container_width=True
            )
