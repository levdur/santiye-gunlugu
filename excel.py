"""Bütçe kalemlerini Excel/CSV'den okuma ve rapor verilerini Excel'e yazma."""
import csv
import io

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# Kullanıcının dosyasındaki başlıklar -> sistemdeki alanlar.
# Kendi bütçe dosyanızdaki başlık farklıysa buraya ekleyin.
SUTUN_ESLEME = {
    "kod": ["kod", "butce kodu", "maliyet kodu", "poz", "poz no", "is kalemi kodu", "wbs", "cost code"],
    "tanim": ["tanim", "aciklama", "is kalemi", "is tanimi", "imalat", "description"],
    "birim": ["birim", "unit"],
    "butce_miktar": ["butce miktari", "kesif miktari", "miktar", "budget qty", "quantity", "qty"],
    "birim_fiyat": ["birim fiyat", "birim fiyati", "unit rate", "rate"],
    "devir_miktar": ["devir miktari", "devir", "onceki miktar", "onceki kumulatif", "acilis miktari",
                     "gerceklesen miktar", "opening qty", "previous qty"],
    "aktivite_kodu": ["aktivite kodu", "aktivite", "activity id", "activity", "is programi kodu"],
}
ZORUNLU = ["kod", "tanim", "birim"]

_TR = str.maketrans("çğıöşüâîûÇĞİÖŞÜ", "cgiosuaiuCGIOSU")


def _normal(metin):
    return " ".join(str(metin or "").translate(_TR).lower().replace("_", " ").split())


SAHAYA_KAPALI_BIRIMLER = {"gtr", "goturu", "ay", "ay.", "ls", "lump sum", "month"}


def kod_metni(deger):
    """Excel'de sayı olarak duran poz numaralarını metne çevirir: 4.1 -> '4.10', 4.01 -> '4.01', 'A-12' -> 'A-12'."""
    if deger is None:
        return ""
    if isinstance(deger, bool):
        return str(deger)
    if isinstance(deger, int):
        return str(deger)
    if isinstance(deger, float):
        if deger.is_integer():
            return str(int(deger))
        return f"{deger:.2f}"
    return str(deger).strip()


def kod_sirasi(kod):
    """Doğal sıralama anahtarı: '4.01' < '4.10' < '10.01'."""
    import re
    return [(0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"[.\-_ /]", str(kod)) if p != ""]


def sayi(deger):
    """'1.234,5', '1234.5', '12,5' gibi değerleri sayıya çevirir. Boşsa None."""
    if deger is None:
        return None
    if isinstance(deger, (int, float)):
        return float(deger)
    s = str(deger).strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    return float(s)


def _sayfalar(dosya_adi, veri):
    """[(sayfa_adi, satirlar)] döndürür."""
    ad = dosya_adi.lower()
    if ad.endswith((".xlsx", ".xlsm")):
        wb = load_workbook(io.BytesIO(veri), read_only=True, data_only=True)
        return [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
    if ad.endswith(".csv"):
        metin = veri.decode("utf-8-sig", errors="replace")
        ilk = metin.split("\n", 1)[0]
        ayrac = ";" if ilk.count(";") > ilk.count(",") else ","
        return [("CSV", list(csv.reader(io.StringIO(metin), delimiter=ayrac)))]
    raise ValueError("Yalnızca .xlsx, .xlsm veya .csv dosyası yükleyin.")


def _alan_bul(hucre):
    """Bir başlık hücresinin hangi alana karşılık geldiğini bulur. İki dilli başlıklarda ('Birim\\nUnit') her satırı dener."""
    adaylar = [_normal(hucre)] + [_normal(p) for p in str(hucre or "").splitlines()]
    for aday in adaylar:
        for alan, adlar in SUTUN_ESLEME.items():
            if aday in adlar:
                return alan
    return None


def _baslik_bul(satirlar):
    """İlk 15 satırda başlık satırını arar. (satir_no, eslesme) veya (None, {}) döndürür."""
    for i, satir in enumerate(satirlar[:15]):
        bulunan = {}
        for j, hucre in enumerate(satir):
            alan = _alan_bul(hucre)
            if alan and alan not in bulunan:
                bulunan[alan] = j
        if all(a in bulunan for a in ZORUNLU):
            return i, bulunan
    return None, {}


def kalemleri_oku(dosya_adi, veri):
    """Dosyayı okur; Kod/Tanım/Birim başlıklarını içeren sayfayı kendisi bulur.
    (kalemler, hatalar, bilgi) döndürür."""
    secilen = None
    for ad, satirlar in _sayfalar(dosya_adi, veri):
        no, eslesme = _baslik_bul(satirlar)
        if no is not None and (secilen is None or len(eslesme) > len(secilen[3])):
            secilen = (ad, satirlar, no, eslesme)
    if secilen is None:
        return [], ["Başlık satırı bulunamadı. Dosyada en az 'Kod' (veya 'Poz No'), 'Tanım' ve 'Birim' sütunları olmalı."], {}
    sayfa, satirlar, baslik_satiri, eslesme = secilen
    aktivite_var = "aktivite_kodu" in eslesme

    kalemler, hatalar, gorulen, grup_sayisi = [], [], set(), 0
    for no, satir in enumerate(satirlar[baslik_satiri + 1:], start=baslik_satiri + 2):
        def al(alan):
            j = eslesme.get(alan)
            return satir[j] if j is not None and j < len(satir) else None
        kod = kod_metni(al("kod"))
        if not kod:
            continue                                   # boş, toplam veya ara başlık satırı
        tanim, birim = str(al("tanim") or "").strip(), str(al("birim") or "").strip()
        if tanim and not birim and al("butce_miktar") in (None, ""):
            grup_sayisi += 1                           # birimsiz ve miktarsız: grup başlığı
            continue
        if not tanim or not birim:
            hatalar.append(f"Satır {no} ({kod}): tanım veya birim boş.")
            continue
        if kod in gorulen:
            hatalar.append(f"Satır {no}: {kod} kodu dosyada birden fazla geçiyor; ilki alındı.")
            continue
        try:
            miktar, fiyat = sayi(al("butce_miktar")), sayi(al("birim_fiyat"))
            devir = sayi(al("devir_miktar")) or 0.0
        except ValueError:
            hatalar.append(f"Satır {no} ({kod}): miktar veya birim fiyat sayı değil.")
            continue
        gorulen.add(kod)
        aktivite = kod_metni(al("aktivite_kodu")) if aktivite_var else kod
        kalemler.append({"kod": kod, "tanim": tanim, "birim": birim, "butce_miktar": miktar,
                         "birim_fiyat": fiyat, "aktivite_kodu": aktivite or None, "devir_miktar": devir})
    bilgi = {"devir_var": "devir_miktar" in eslesme, "sayfa": sayfa, "grup": grup_sayisi, "aktivite_varsayilan": not aktivite_var}
    return kalemler, hatalar, bilgi


# ---------------- Excel'e yazma ----------------
_KALIN = Font(name="Arial", bold=True)
_NORMAL = Font(name="Arial")
_BASLIK_DOLGU = PatternFill("solid", fgColor="DDE3E8")


def _sayfa(wb, ad, basliklar, satirlar, bicimler=None, ilk=False):
    ws = wb.active if ilk else wb.create_sheet()
    ws.title = ad
    ws.append(basliklar)
    for h in ws[1]:
        h.font, h.fill = _KALIN, _BASLIK_DOLGU
        h.alignment = Alignment(vertical="top", wrap_text=True)
    for s in satirlar:
        ws.append(list(s))
    bicimler = bicimler or {}
    for j, baslik in enumerate(basliklar, start=1):
        harf = get_column_letter(j)
        en = max([len(str(baslik))] + [len(str(s[j - 1])) for s in satirlar if s[j - 1] is not None][:500])
        ws.column_dimensions[harf].width = min(max(en + 2, 9), 60)
        for hucre in ws[harf][1:]:
            hucre.font = _NORMAL
            if j in bicimler:
                hucre.number_format = bicimler[j]
    ws.freeze_panes = "A2"
    if satirlar:
        ws.auto_filter.ref = ws.dimensions
    return ws


def rapor_excel(bilgi, gunluk, imalat, iscilik, ilerleme, is_programi):
    wb = Workbook()
    ws = wb.active
    ws.title = "Bilgi"
    for anahtar, deger in bilgi:
        ws.append([anahtar, deger])
    for h in ws["A"]:
        h.font = _KALIN
    for h in ws["B"]:
        h.font = _NORMAL
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 70

    _sayfa(wb, "Imalat",
           ["Tarih", "Bütçe kodu", "Tanım", "Birim", "Miktar", "Lokasyon", "Açıklama", "Aktivite kodu",
            "Birim fiyat", "Tutar", "Giren", "Onay"], imalat, {5: "#,##0.00", 9: "#,##0.00", 10: "#,##0.00"})
    _sayfa(wb, "Iscilik", ["Tarih", "Sınıf", "Kişi"], iscilik, {3: "0"})
    _sayfa(wb, "Ilerleme",
           ["Bütçe kodu", "Tanım", "Birim", "Aktivite kodu", "Bütçe miktarı", "Dönem miktarı",
            "Kümülatif miktar (devir dahil)", "Tamamlanma", "Kalan miktar", "Birim fiyat", "Bütçe tutarı",
            "Kazanılmış değer (kümülatif)"], ilerleme,
           {5: "#,##0.00", 6: "#,##0.00", 7: "#,##0.00", 8: "0.0%", 9: "#,##0.00", 10: "#,##0.00",
            11: "#,##0.00", 12: "#,##0.00"})
    _sayfa(wb, "IsProgrami",
           ["Poz No / Aktivite", "Tanım", "Birim", "Bütçe miktarı", "Kümülatif miktar", "Fiziksel %",
            "İlk imalat tarihi", "Son imalat tarihi", "Durum"], is_programi,
           {4: "#,##0.00", 5: "#,##0.00", 6: "0.0%"})
    _sayfa(wb, "Gunluk", ["Tarih", "Hava", "Sıcaklık (°C)", "Toplam kişi", "Makineler", "Notlar", "Giren", "Onay"],
           gunluk, {3: "0", 4: "0"})
    cikti = io.BytesIO()
    wb.save(cikti)
    cikti.seek(0)
    return cikti


def kalem_sablonu():
    """Ofisin dolduracağı içe aktarma şablonu."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Kalemler"
    ws.append(["Kod", "Tanım", "Birim", "Bütçe miktarı", "Birim fiyat", "Aktivite kodu", "Devir miktarı"])
    for h in ws[1]:
        h.font, h.fill = _KALIN, _BASLIK_DOLGU
    ornek = ["4.02", "Betonarme betonu", "m³", 18000, 109, "4.02", 7600]
    ws.append(ornek)
    for h in ws[2]:
        h.font = Font(name="Arial", color="0000FF")
    for harf, en in zip("ABCDEFG", [14, 42, 8, 15, 13, 15, 15]):
        ws.column_dimensions[harf].width = en
    ws["I1"] = "Nasıl doldurulur"
    ws["I1"].font = _KALIN
    aciklamalar = [
        "Her satır bir bütçe kalemi. 2. satır örnektir; silip kendi kalemlerinizi yazın.",
        "Kod, Tanım ve Birim zorunlu. Diğer sütunlar boş bırakılabilir.",
        "Birim fiyat girilirse kazanılmış değer hesaplanır.",
        "Aktivite kodu: iş programındaki aktivitenin kodu. Boş sütun yoksa bütçe kodu kullanılır.",
        "Devir miktarı: sistem kullanılmaya başlamadan önce yapılmış kümülatif miktar (devam eden projeler için).",
        "Aynı kodla tekrar yüklerseniz mevcut kalem güncellenir; dosyada olmayan kalemler silinmez.",
    ]
    for i, a in enumerate(aciklamalar, start=2):
        ws[f"I{i}"] = a
        ws[f"I{i}"].font = _NORMAL
    ws.column_dimensions["I"].width = 95
    cikti = io.BytesIO()
    wb.save(cikti)
    cikti.seek(0)
    return cikti
