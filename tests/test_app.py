import io
import os
import re
import sqlite3

os.environ["SANTIYE_TEST"] = "1"

import pytest
from openpyxl import Workbook, load_workbook
from PIL import Image
from werkzeug.security import generate_password_hash

from app import create_app


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db"),
                      "UPLOAD_FOLDER": str(tmp_path / "foto"), "SECRET_KEY": "test"})
    db = sqlite3.connect(app.config["DATABASE"])
    for kadi, ad, rol in [("levdur", "Levdur", "yonetici"), ("ahmet", "Ahmet Usta", "personel"),
                          ("mehmet", "Mehmet", "personel"), ("ofis", "Ayşe Planlama", "ofis")]:
        db.execute("INSERT INTO kullanici (kullanici_adi, ad, sifre_hash, rol, olusturma) VALUES (?,?,?,?,?)",
                   (kadi, ad, generate_password_hash("sifre1234"), rol, "2026-10-01 08:00"))
    db.executemany("INSERT INTO butce_kalemi (kod, tanim, birim, butce_miktar, birim_fiyat, aktivite_kodu) "
                   "VALUES (?,?,?,?,?,?)",
                   [("02.01", "Temel kazısı", "m³", 1000, 100, "A100"),
                    ("03.01", "C30 beton, temel", "m³", 100, 3000, "A200"),
                    ("03.05", "Nervürlü donatı", "ton", 20, None, "A210")])
    db.commit()
    db.close()
    return app


def csrf(c, yol="/giris"):
    return re.search(r'name="csrf" value="([^"]+)"', c.get(yol).get_data(as_text=True)).group(1)


def giris(c, kadi, sifre="sifre1234"):
    return c.post("/giris", data={"csrf": csrf(c), "kullanici_adi": kadi, "sifre": sifre}, follow_redirects=True)


def istemci(app, kadi):
    c = app.test_client()
    giris(c, kadi)
    return c


def sinif_id(app, ad):
    return sqlite3.connect(app.config["DATABASE"]).execute(
        "SELECT id FROM iscilik_sinifi WHERE ad = ?", (ad,)).fetchone()[0]


def rapor_gir(c, tarih="2026-10-26", satirlar=(("02.01", "120", "B blok", ""),), iscilik=None,
              notlar="", fotolar=(), yol="/yeni"):
    veri = {"csrf": csrf(c, "/"), "tarih": tarih, "hava": "Güneşli", "sicaklik": "14", "makineler": "",
            "notlar": notlar, "kalem_kod": [s[0] for s in satirlar], "miktar": [s[1] for s in satirlar],
            "lokasyon": [s[2] for s in satirlar], "aciklama": [s[3] for s in satirlar]}
    for k, v in (iscilik or {}).items():
        veri[f"iscilik_{k}"] = str(v)
    if fotolar:
        veri["foto"] = list(fotolar)
    return c.post(yol, data=veri, content_type="multipart/form-data", follow_redirects=True)


def onayla(app, kayit_id):
    y = istemci(app, "levdur")
    y.post(f"/kayit/{kayit_id}/onay", data={"csrf": csrf(y, "/")})


# ---------------- Giriş ve yetki ----------------
def test_giris_yapmadan_erisilemez(app):
    c = app.test_client()
    for yol in ["/", "/ilerleme", "/kalemler", "/disa-aktar"]:
        assert c.get(yol).status_code == 302


def test_bes_hatali_denemede_kilit(app):
    c = app.test_client()
    for _ in range(5):
        giris(c, "ahmet", "yanlis")
    assert "10 dakika" in giris(c, "ahmet").get_data(as_text=True)


def test_csrf_olmadan_form_reddedilir(app):
    c = istemci(app, "ahmet")
    assert c.post("/yeni", data={"tarih": "2026-10-26"}).status_code == 400


def test_rol_yetkileri(app):
    p, o = istemci(app, "ahmet"), istemci(app, "ofis")
    for yol in ["/ilerleme", "/kalemler", "/rapor", "/disa-aktar", "/kullanicilar"]:
        assert p.get(yol).status_code == 403
    for yol in ["/ilerleme", "/kalemler", "/rapor", "/disa-aktar"]:
        assert o.get(yol).status_code == 200
    assert o.get("/yeni").status_code == 403          # ofis rapor girmez
    assert o.get("/kullanicilar").status_code == 403


# ---------------- Günlük rapor ----------------
def test_imalat_ve_iscilik_kaydedilir(app):
    c = istemci(app, "ahmet")
    metin = rapor_gir(c, satirlar=[("02.01", "120,5", "B blok", ""), ("03.01", "18", "B blok temel", "grobeton")],
                      iscilik={sinif_id(app, "Kalıpçı"): 6, sinif_id(app, "Düz işçi"): 4}).get_data(as_text=True)
    assert "raporu kaydedildi" in metin
    assert "120,5&nbsp;m³" in metin and "Temel kazısı" in metin
    assert "Kalıpçı 6" in metin and "toplam 10 kişi" in metin


def test_listede_olmayan_kod_reddedilir(app):
    c = istemci(app, "ahmet")
    metin = rapor_gir(c, satirlar=[("99.99", "5", "", "")]).get_data(as_text=True)
    assert "listede olan bir bütçe kodu değil" in metin
    assert 'value="99.99"' in metin            # girilen değer formda korunur


def test_gecersiz_miktar_reddedilir(app):
    c = istemci(app, "ahmet")
    for miktar in ["0", "-3", "abc", ""]:
        metin = rapor_gir(c, satirlar=[("02.01", miktar, "", "")]).get_data(as_text=True)
        assert "sıfırdan büyük bir sayı" in metin


def test_imalatsiz_gun_not_ister(app):
    c = istemci(app, "ahmet")
    assert "nedenini yazın" in rapor_gir(c, satirlar=[]).get_data(as_text=True)
    assert "raporu kaydedildi" in rapor_gir(c, satirlar=[], notlar="Yağmur, çalışılmadı").get_data(as_text=True)


def test_duzenleme_satirlari_degistirir_ve_kimin_duzenledigini_tutar(app):
    a = istemci(app, "ahmet")
    rapor_gir(a)
    m = istemci(app, "mehmet")
    assert "zaten var" in rapor_gir(m).get_data(as_text=True)
    metin = rapor_gir(m, satirlar=[("03.01", "1234.5", "", "")], yol="/kayit/1/duzenle").get_data(as_text=True)
    assert "son düzenleme Mehmet" in metin
    db = sqlite3.connect(app.config["DATABASE"])
    assert db.execute("SELECT COUNT(*), SUM(miktar) FROM imalat_satiri").fetchone() == (1, 1234.5)
    # düzenleme formu miktarı binlik ayraçsız gösterir; tekrar kaydedince değer bozulmaz
    form = m.get("/kayit/1/duzenle").get_data(as_text=True)
    assert 'value="1234.5"' in form


def test_onaylanan_rapor_kilitlenir(app):
    a = istemci(app, "ahmet")
    rapor_gir(a)
    onayla(app, 1)
    assert "değiştirilemez" in a.get("/kayit/1/duzenle", follow_redirects=True).get_data(as_text=True)


def test_fotograf_kucultulur_ve_konum_silinir(app):
    def jpeg():
        b = io.BytesIO()
        Image.new("RGB", (4000, 3000), (120, 130, 140)).save(b, "JPEG")
        b.seek(0)
        return b
    c = istemci(app, "ahmet")
    rapor_gir(c, fotolar=[(jpeg(), "a.jpg")])
    (dosya,) = os.listdir(app.config["UPLOAD_FOLDER"])
    img = Image.open(os.path.join(app.config["UPLOAD_FOLDER"], dosya))
    assert max(img.size) <= 1600 and not img.getexif()


# ---------------- Bütçe kalemi içe aktarma ----------------
def _xlsx(satirlar):
    wb = Workbook()
    for s in satirlar:
        wb.active.append(s)
    b = io.BytesIO()
    wb.save(b)
    b.seek(0)
    return b


def yukle(c, dosya, ad, goturu_kapali=True):
    veri = {"csrf": csrf(c, "/kalemler"), "dosya": (dosya, ad)}
    if goturu_kapali:
        veri["goturu_kapali"] = "1"
    return c.post("/kalemler", data=veri,
                  content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)


def test_excel_ice_aktarma_baslik_satirini_bulur(app):
    o = istemci(app, "ofis")
    dosya = _xlsx([["ABC Projesi Bütçesi"], [], ["Poz No", "İş Kalemi", "Birim", "Keşif Miktarı", "Birim Fiyat"],
                   ["04.01", "Kalıp, temel", "m²", 450, "185,50"], ["", "Ara toplam", "", "", ""],
                   ["04.02", "Kalıp, perde", "m²", "1.250,75", None], ["04.03", "", "m²", 1, 1]])
    metin = yukle(o, dosya, "butce.xlsx")
    assert "2 yeni kalem" in metin
    assert "04.03" in metin and "tanım veya birim boş" in metin
    db = sqlite3.connect(app.config["DATABASE"])
    assert db.execute("SELECT butce_miktar, birim_fiyat FROM butce_kalemi WHERE kod='04.01'").fetchone() == (450, 185.5)
    assert db.execute("SELECT butce_miktar FROM butce_kalemi WHERE kod='04.02'").fetchone() == (1250.75,)


def test_csv_noktali_virgul_ve_guncelleme(app):
    o = istemci(app, "ofis")
    csv = "Kod;Tanım;Birim;Bütçe Miktarı;Aktivite Kodu\n02.01;Temel kazısı (revize);m³;1200;A100\n05.01;Duvar;m²;300;A300\n"
    metin = yukle(o, io.BytesIO(csv.encode("utf-8-sig")), "kalemler.csv")
    assert "1 yeni kalem, 1 güncellenen" in metin
    db = sqlite3.connect(app.config["DATABASE"])
    assert db.execute("SELECT tanim, butce_miktar FROM butce_kalemi WHERE kod='02.01'").fetchone() == \
        ("Temel kazısı (revize)", 1200)


def test_kullanilan_kalemin_birimi_degismez(app):
    rapor_gir(istemci(app, "ahmet"))
    o = istemci(app, "ofis")
    metin = yukle(o, _xlsx([["Kod", "Tanım", "Birim"], ["02.01", "Temel kazısı", "ton"]]), "b.xlsx")
    assert "birimi değiştirilemez" in metin
    db = sqlite3.connect(app.config["DATABASE"])
    assert db.execute("SELECT birim FROM butce_kalemi WHERE kod='02.01'").fetchone() == ("m³",)


def test_basliksiz_dosya_anlasilir_hata_verir(app):
    o = istemci(app, "ofis")
    assert "Başlık satırı bulunamadı" in yukle(o, _xlsx([["a", "b"], [1, 2]]), "x.xlsx")


def test_pasif_kalem_sahada_kullanilamaz(app):
    o = istemci(app, "ofis")
    o.post("/kalemler/1/durum", data={"csrf": csrf(o, "/kalemler")})
    c = istemci(app, "ahmet")
    assert "listede olan bir bütçe kodu değil" in rapor_gir(c).get_data(as_text=True)


# ---------------- İlerleme ve dışa aktarma ----------------
def _veri(app):
    a = istemci(app, "ahmet")
    rapor_gir(a, "2026-10-12", [("02.01", "400", "", ""), ("03.01", "60", "", "")])     # kayıt 1
    rapor_gir(a, "2026-10-26", [("02.01", "300", "", ""), ("03.01", "50", "", "")],
              iscilik={sinif_id(app, "Betoncu"): 5})                                   # kayıt 2
    rapor_gir(a, "2026-10-27", [("03.05", "4,5", "", "")])                             # kayıt 3 (onaysız kalacak)
    onayla(app, 1)
    onayla(app, 2)


def test_ilerleme_yalnizca_onayli_veriyi_sayar(app):
    _veri(app)
    o = istemci(app, "ofis")
    metin = o.get("/ilerleme?bas=2026-10-26&bit=2026-11-01").get_data(as_text=True)
    # 02.01: dönem 300, kümülatif 700 / 1000 = %70
    assert "%70,0" in metin
    # 03.01: 110 / 100 -> bütçe aşımı
    assert "Bütçe miktarı aşıldı" in metin
    # 03.05 onaysız olduğu için görünmez
    assert "Nervürlü donatı" not in metin
    dahil = o.get("/ilerleme?bas=2026-10-26&bit=2026-11-01&onaysiz=1").get_data(as_text=True)
    assert "Nervürlü donatı" in dahil and "%22,5" in dahil


def test_kazanilmis_deger(app):
    _veri(app)
    metin = istemci(app, "ofis").get("/ilerleme?bas=2026-10-26&bit=2026-11-01").get_data(as_text=True)
    # bütçe tutarı: 1000*100 + 100*3000 = 400.000; kazanılan: 700*100 + 110*3000 = 400.000
    assert metin.count("400.000") >= 2
    assert "%100,0" in metin


def test_excel_disa_aktarma(app):
    _veri(app)
    yanit = istemci(app, "ofis").get("/disa-aktar?bas=2026-10-01&bit=2026-11-01")
    assert yanit.status_code == 200
    wb = load_workbook(io.BytesIO(yanit.data))
    assert wb.sheetnames == ["Bilgi", "Imalat", "Iscilik", "Ilerleme", "IsProgrami", "Gunluk"]
    imalat = list(wb["Imalat"].iter_rows(min_row=2, values_only=True))
    assert len(imalat) == 4                       # onaysız 03.05 satırı hariç
    assert imalat[0][:5] == ("2026-10-12", "02.01", "Temel kazısı", "m³", 400)
    assert imalat[0][7] == "A100" and imalat[0][9] == 40000
    assert list(wb["Iscilik"].iter_rows(min_row=2, values_only=True)) == [("2026-10-26", "Betoncu", 5)]
    ilerleme = {r[0]: r for r in wb["Ilerleme"].iter_rows(min_row=2, values_only=True)}
    assert ilerleme["02.01"][6] == 700 and ilerleme["02.01"][7] == 0.7

    ip = {r[0]: r for r in wb["IsProgrami"].iter_rows(min_row=2, values_only=True)}
    assert ip["A100"][5] == 0.7 and ip["A100"][6] == "2026-10-12" and ip["A100"][7] == "2026-10-26"
    assert ip["A200"][8] == "Bütçe miktarına ulaştı"


def test_iki_dilli_cok_sayfali_butce_dosyasi(app):
    """Kapak sayfası, iki dilli başlıklar, sayı olarak poz no, grup satırları ve götürü kalemler."""
    wb = Workbook()
    wb.active.title = "00_KAPAK"
    wb.active.append(["Proje kapağı"])
    ws = wb.create_sheet("02_KESIF_BUTCE")
    ws.append(["KEŞİF VE BÜTÇE"])
    ws.append([None, None, None, None, None, None, "BİRİM MALİYET"])
    ws.append(["Grup", "Poz No", "Tanım", "Description", "Birim\nUnit", "Miktar\nQty", "Birim Fiyat\nUnit Rate"])
    ws.append([None, None, "GENEL TOPLAM", None, None, None, None])
    ws.append([None, 1, "Mühendislik", "Engineering", None, None, None])
    ws.append([1, 1.01, "Mimari Proje", "Architectural Design", "gtr", 1, 850000])
    ws.append([None, 4, "İnşaat İşleri", "Civil Works", None, None, None])
    ws.append([4, 4.01, "Kazı ve Dolgu", "Excavation", "m³", 60000, 5.7])
    ws.append([4, 4.1, "Kaba sıva", "Plaster", "m²", 5000, 7])
    ws.append([None, 10, "Genel Giderler", "Overheads", None, None, None])
    ws.append([10, 10.01, "Personel", "Staff", "ay", 24, 1000])
    b = io.BytesIO()
    wb.save(b)
    b.seek(0)
    o = istemci(app, "ofis")
    metin = yukle(o, b, "butce.xlsm")
    assert "4 yeni kalem" in metin and "02_KESIF_BUTCE" in metin and "3 grup başlığı" in metin
    assert "aktivite kodu = bütçe kodu" in metin and "2 götürü/aylık kalem" in metin
    db = sqlite3.connect(app.config["DATABASE"])
    kodlar = dict(db.execute("SELECT kod, aktif FROM butce_kalemi WHERE kod NOT LIKE '0%'"))
    assert kodlar == {"1.01": 0, "4.01": 1, "4.10": 1, "10.01": 0}
    assert db.execute("SELECT aktivite_kodu, birim_fiyat FROM butce_kalemi WHERE kod='4.01'").fetchone() == ("4.01", 5.7)
    # doğal sıralama: 4.01 < 4.10 < 10.01
    sayfa = o.get("/kalemler").get_data(as_text=True)
    assert sayfa.index(">4.01<") < sayfa.index(">4.10<") < sayfa.index(">10.01<")


def test_devir_miktari_kumulatife_eklenir_ve_sadece_dosyada_varsa_guncellenir(app):
    o = istemci(app, "ofis")
    yukle(o, _xlsx([["Kod", "Tanım", "Birim", "Bütçe miktarı", "Devir miktarı"],
                    ["02.01", "Temel kazısı", "m³", 1000, 250]]), "d.xlsx")
    _veri(app)
    metin = o.get("/ilerleme?bas=2026-10-26&bit=2026-11-01").get_data(as_text=True)
    assert "%95,0" in metin          # (250 devir + 700 saha) / 1000
    # devir sütunu olmayan dosya devri silmez
    yukle(o, _xlsx([["Kod", "Tanım", "Birim"], ["02.01", "Temel kazısı", "m³"]]), "e.xlsx")
    db = sqlite3.connect(app.config["DATABASE"])
    assert db.execute("SELECT devir_miktar FROM butce_kalemi WHERE kod='02.01'").fetchone() == (250,)
