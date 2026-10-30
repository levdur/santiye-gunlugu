"""Şantiye Günlüğü: sahadan bütçe kodlu günlük imalat verisi, ofiste ilerleme ve maliyet takibi."""
import os
import secrets
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

import click
from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash

import excel

HAVA_SECENEKLERI = ["Güneşli", "Parçalı bulutlu", "Bulutlu", "Yağmurlu", "Karlı", "Rüzgârlı"]
VARSAYILAN_SINIFLAR = ["Formen", "Kalıpçı", "Demirci", "Betoncu", "Duvarcı", "Sıvacı", "Elektrikçi",
                       "Tesisatçı", "Operatör", "Şoför", "Düz işçi"]
ROLLER = {"yonetici": "Yönetici", "ofis": "Ofis (bütçe/planlama)", "personel": "Saha personeli"}
KAYIT_BASINA_EN_FAZLA_FOTO = 10
FOTO_EN_BUYUK_KENAR = 1600
FOTO_KALITE = 78
HATALI_GIRIS_SINIRI = 5
KILIT_SURESI = timedelta(minutes=10)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def create_app(test_config=None):
    app = Flask(__name__, instance_path=os.path.join(BASE_DIR, "instance"))
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.update(
        DATABASE=os.path.join(app.instance_path, "santiye.db"),
        UPLOAD_FOLDER=os.path.join(app.instance_path, "fotograflar"),
        SAAT_DILIMI=os.environ.get("SAAT_DILIMI", "Europe/Istanbul"),
        PROJE_ADI=os.environ.get("PROJE_ADI", "Şantiye Günlüğü"),
        MAX_CONTENT_LENGTH=60 * 1024 * 1024,
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("GUVENLI_CEREZ", "0") == "1",
        SECRET_KEY=os.environ.get("SECRET_KEY") or _gizli_anahtar(app.instance_path),
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # ================= Zaman =================
    def simdi():
        return datetime.now(ZoneInfo(app.config["SAAT_DILIMI"]))

    def bugun():
        return simdi().date()

    def zaman_damgasi():
        return simdi().strftime("%Y-%m-%d %H:%M")

    # ================= Veritabanı =================
    def get_db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    with app.app_context():
        db = get_db()
        with open(os.path.join(BASE_DIR, "schema.sql"), encoding="utf-8") as f:
            db.executescript(f.read())
        sutunlar = {r["name"] for r in db.execute("PRAGMA table_info(butce_kalemi)")}
        if "devir_miktar" not in sutunlar:
            db.execute("ALTER TABLE butce_kalemi ADD COLUMN devir_miktar REAL NOT NULL DEFAULT 0")
            db.commit()
        if db.execute("SELECT COUNT(*) FROM iscilik_sinifi").fetchone()[0] == 0:
            db.executemany("INSERT INTO iscilik_sinifi (ad, sira) VALUES (?, ?)",
                           [(ad, i) for i, ad in enumerate(VARSAYILAN_SINIFLAR)])
            db.commit()

    # ================= Komut satırı =================
    @app.cli.command("kullanici-ekle")
    @click.option("--yonetici", is_flag=True, help="Yönetici hesabı oluştur")
    def kullanici_ekle_komutu(yonetici):
        """Yeni kullanıcı oluşturur (ilk yönetici hesabı için)."""
        kadi = click.prompt("Kullanıcı adı").strip().lower()
        ad = click.prompt("Ad soyad").strip()
        sifre = click.prompt("Şifre (en az 8 karakter)", hide_input=True, confirmation_prompt=True)
        hata = kullanici_olustur(kadi, ad, sifre, "yonetici" if yonetici else "personel")
        if hata:
            raise click.ClickException(hata)
        click.echo(f"{kadi} oluşturuldu.")

    def kullanici_olustur(kadi, ad, sifre, rol):
        if not kadi or not kadi.isascii() or not kadi.replace(".", "").replace("_", "").isalnum():
            return "Kullanıcı adı yalnızca İngilizce harf, rakam, nokta ve alt çizgi içerebilir."
        if not ad:
            return "Ad soyad boş olamaz."
        if len(sifre) < 8:
            return "Şifre en az 8 karakter olmalı."
        if rol not in ROLLER:
            return "Geçersiz rol."
        try:
            get_db().execute(
                "INSERT INTO kullanici (kullanici_adi, ad, sifre_hash, rol, olusturma) VALUES (?, ?, ?, ?, ?)",
                (kadi, ad, generate_password_hash(sifre), rol, zaman_damgasi()))
            get_db().commit()
        except sqlite3.IntegrityError:
            return "Bu kullanıcı adı zaten var."
        return None

    # ================= Oturum, yetki, güvenlik =================
    @app.before_request
    def oturumu_yukle():
        g.kullanici = None
        kid = session.get("kullanici_id")
        if kid is not None:
            k = get_db().execute("SELECT * FROM kullanici WHERE id = ? AND aktif = 1", (kid,)).fetchone()
            if k is None:
                session.clear()
            g.kullanici = k

    @app.before_request
    def csrf_kontrol():
        if request.method == "POST":
            beklenen, gelen = session.get("csrf"), request.form.get("csrf")
            if not beklenen or not gelen or not secrets.compare_digest(beklenen, gelen):
                abort(400, "Oturum süresi doldu. Sayfayı yenileyip tekrar deneyin.")

    def rol(*izinli):
        return g.kullanici is not None and g.kullanici["rol"] in izinli

    @app.context_processor
    def sablon_yardimcilari():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return {"csrf": session["csrf"], "kullanici": g.get("kullanici"), "bugun": bugun().isoformat(),
                "rol": rol, "ROLLER": ROLLER, "proje_adi": app.config["PROJE_ADI"], "sayi_bicim": sayi_bicim, "yuzde": yuzde}

    def giris_gerekli(fn):
        @wraps(fn)
        def sarmal(*a, **kw):
            if g.kullanici is None:
                return redirect(url_for("giris", sonra=request.path))
            return fn(*a, **kw)
        return sarmal

    def roller_gerekli(*izinli):
        def dekorator(fn):
            @wraps(fn)
            @giris_gerekli
            def sarmal(*a, **kw):
                if not rol(*izinli):
                    abort(403)
                return fn(*a, **kw)
            return sarmal
        return dekorator

    def guvenli_yonlendirme(hedef, varsayilan):
        return hedef if hedef and hedef.startswith("/") and not hedef.startswith("//") else varsayilan

    @app.route("/giris", methods=["GET", "POST"])
    def giris():
        if request.method == "POST":
            kadi = request.form.get("kullanici_adi", "").strip().lower()
            sifre = request.form.get("sifre", "")
            db = get_db()
            k = db.execute("SELECT * FROM kullanici WHERE kullanici_adi = ?", (kadi,)).fetchone()
            if k and k["kilit_bitis"] and k["kilit_bitis"] > zaman_damgasi():
                flash("Çok fazla hatalı deneme. 10 dakika sonra tekrar deneyin.", "hata")
            elif k and k["aktif"] and check_password_hash(k["sifre_hash"], sifre):
                db.execute("UPDATE kullanici SET hatali_giris = 0, kilit_bitis = NULL WHERE id = ?", (k["id"],))
                db.commit()
                csrf = session.get("csrf")
                session.clear()
                session["csrf"] = csrf or secrets.token_urlsafe(32)
                session["kullanici_id"] = k["id"]
                session.permanent = True
                return redirect(guvenli_yonlendirme(request.args.get("sonra"), url_for("liste")))
            else:
                if k:
                    sayi_ = k["hatali_giris"] + 1
                    kilit = None
                    if sayi_ >= HATALI_GIRIS_SINIRI:
                        kilit = (simdi() + KILIT_SURESI).strftime("%Y-%m-%d %H:%M")
                        sayi_ = 0
                    db.execute("UPDATE kullanici SET hatali_giris = ?, kilit_bitis = ? WHERE id = ?",
                               (sayi_, kilit, k["id"]))
                    db.commit()
                flash("Kullanıcı adı veya şifre hatalı.", "hata")
        return render_template("giris.html")

    @app.route("/cikis", methods=["POST"])
    def cikis():
        session.clear()
        flash("Çıkış yaptınız.")
        return redirect(url_for("giris"))

    @app.route("/sifre", methods=["GET", "POST"])
    @giris_gerekli
    def sifre_degistir():
        if request.method == "POST":
            if not check_password_hash(g.kullanici["sifre_hash"], request.form.get("eski", "")):
                flash("Mevcut şifre hatalı.", "hata")
            elif len(request.form.get("yeni", "")) < 8:
                flash("Yeni şifre en az 8 karakter olmalı.", "hata")
            elif request.form.get("yeni") != request.form.get("yeni2"):
                flash("Yeni şifreler birbirini tutmuyor.", "hata")
            else:
                get_db().execute("UPDATE kullanici SET sifre_hash = ? WHERE id = ?",
                                 (generate_password_hash(request.form["yeni"]), g.kullanici["id"]))
                get_db().commit()
                flash("Şifreniz değiştirildi.")
                return redirect(url_for("liste"))
        return render_template("sifre.html")

    # ================= Ortak sorgular =================
    def kaydi_getir(kayit_id):
        k = get_db().execute("SELECT * FROM kayit WHERE id = ?", (kayit_id,)).fetchone()
        if k is None:
            abort(404)
        return k

    def kullanici_adlari():
        return {r["id"]: r["ad"] for r in get_db().execute("SELECT id, ad FROM kullanici")}

    def _gruplu(sorgu, kayit_idleri):
        sonuc = defaultdict(list)
        if kayit_idleri:
            isaret = ",".join("?" * len(kayit_idleri))
            for r in get_db().execute(sorgu.format(isaret=isaret), list(kayit_idleri)):
                sonuc[r["kayit_id"]].append(r)
        return sonuc

    def fotograflar(ids):
        return _gruplu("SELECT * FROM fotograf WHERE kayit_id IN ({isaret}) ORDER BY id", ids)

    def imalatlar(ids):
        return _gruplu("""SELECT s.*, k.kod, k.tanim, k.birim, k.aktivite_kodu, k.birim_fiyat
                          FROM imalat_satiri s JOIN butce_kalemi k ON k.id = s.kalem_id
                          WHERE s.kayit_id IN ({isaret}) ORDER BY s.id""", ids)

    def iscilikler(ids):
        return _gruplu("""SELECT s.*, c.ad FROM iscilik_satiri s JOIN iscilik_sinifi c ON c.id = s.sinif_id
                          WHERE s.kayit_id IN ({isaret}) ORDER BY c.sira, c.ad""", ids)

    def aktif_kalemler():
        return sorted(get_db().execute("SELECT * FROM butce_kalemi WHERE aktif = 1").fetchall(),
                      key=lambda k: excel.kod_sirasi(k["kod"]))

    def aktif_siniflar():
        return get_db().execute("SELECT * FROM iscilik_sinifi WHERE aktif = 1 ORDER BY sira, ad").fetchall()

    # ================= Günlük rapor formu =================
    def formu_oku():
        """Formu okur ve doğrular. (baslik, imalat_satirlari, iscilik, hatalar) döndürür."""
        f = request.form
        hatalar = []
        tarih = f.get("tarih", "").strip()
        try:
            if date.fromisoformat(tarih) > bugun():
                hatalar.append("İleri bir tarihe kayıt girilemez.")
        except ValueError:
            hatalar.append("Geçerli bir tarih girin.")
        hava = f.get("hava", "")
        if hava not in HAVA_SECENEKLERI:
            hatalar.append("Hava durumunu listeden seçin.")
        sicaklik = f.get("sicaklik", "").strip()
        try:
            sicaklik = int(sicaklik) if sicaklik else None
        except ValueError:
            hatalar.append("Sıcaklık bir tam sayı olmalı.")
            sicaklik = None
        baslik = {"tarih": tarih, "hava": hava, "sicaklik": sicaklik,
                  "makineler": f.get("makineler", "").strip(), "notlar": f.get("notlar", "").strip()}

        kalem_kodlari = {k["kod"]: k for k in aktif_kalemler()}
        satirlar = []
        for i, (kod, miktar, lok, acik) in enumerate(zip(f.getlist("kalem_kod"), f.getlist("miktar"),
                                                         f.getlist("lokasyon"), f.getlist("aciklama")), start=1):
            kod, miktar, lok, acik = kod.strip(), miktar.strip(), lok.strip(), acik.strip()
            if not (kod or miktar or lok or acik):
                continue
            satir = {"kod": kod, "miktar": miktar, "lokasyon": lok, "aciklama": acik}
            satirlar.append(satir)
            kalem = kalem_kodlari.get(kod)
            if kalem is None:
                hatalar.append(f"İmalat satırı {i}: '{kod or 'boş'}' listede olan bir bütçe kodu değil.")
                continue
            try:
                deger = excel.sayi(miktar)
                if deger is None or deger <= 0:
                    raise ValueError
            except ValueError:
                hatalar.append(f"İmalat satırı {i} ({kod}): miktar sıfırdan büyük bir sayı olmalı.")
                continue
            satir["kalem_id"], satir["deger"] = kalem["id"], deger

        iscilik = {}
        for s in aktif_siniflar():
            deger = f.get(f"iscilik_{s['id']}", "").strip()
            if not deger:
                continue
            try:
                kisi = int(deger)
                if kisi < 0:
                    raise ValueError
            except ValueError:
                hatalar.append(f"{s['ad']}: kişi sayısı sıfır veya pozitif tam sayı olmalı.")
                continue
            if kisi:
                iscilik[s["id"]] = kisi

        if not satirlar and not baslik["notlar"]:
            hatalar.append("İmalat girilmediyse notlara nedenini yazın (ör. yağmur nedeniyle çalışılmadı).")
        return baslik, satirlar, iscilik, hatalar

    def satirlari_kaydet(kayit_id, satirlar, iscilik):
        db = get_db()
        db.execute("DELETE FROM imalat_satiri WHERE kayit_id = ?", (kayit_id,))
        db.execute("DELETE FROM iscilik_satiri WHERE kayit_id = ?", (kayit_id,))
        db.executemany("INSERT INTO imalat_satiri (kayit_id, kalem_id, miktar, lokasyon, aciklama) VALUES (?,?,?,?,?)",
                       [(kayit_id, s["kalem_id"], s["deger"], s["lokasyon"] or None, s["aciklama"] or None)
                        for s in satirlar])
        db.executemany("INSERT INTO iscilik_satiri (kayit_id, sinif_id, kisi) VALUES (?,?,?)",
                       [(kayit_id, sid, kisi) for sid, kisi in iscilik.items()])

    def formu_goster(baslik, kayit, satirlar, iscilik, fotolar):
        return render_template("form.html", baslik_metni=baslik, kayit=kayit, satirlar=satirlar,
                               iscilik=iscilik, fotolar=fotolar, kalemler=aktif_kalemler(),
                               siniflar=aktif_siniflar(), hava_secenekleri=HAVA_SECENEKLERI)

    def fotograflari_kaydet(kayit_id):
        uyarilar = []
        dosyalar = [d for d in request.files.getlist("foto") if d and d.filename]
        mevcut = get_db().execute("SELECT COUNT(*) FROM fotograf WHERE kayit_id = ?", (kayit_id,)).fetchone()[0]
        bos_yer = KAYIT_BASINA_EN_FAZLA_FOTO - mevcut
        if len(dosyalar) > bos_yer:
            uyarilar.append(f"Bir kayıtta en fazla {KAYIT_BASINA_EN_FAZLA_FOTO} fotoğraf olabilir; fazlası eklenmedi.")
            dosyalar = dosyalar[:max(bos_yer, 0)]
        for d in dosyalar:
            try:
                img = ImageOps.exif_transpose(Image.open(d.stream)).convert("RGB")
                img.thumbnail((FOTO_EN_BUYUK_KENAR, FOTO_EN_BUYUK_KENAR))
                ad = f"{uuid.uuid4().hex}.jpg"
                img.save(os.path.join(app.config["UPLOAD_FOLDER"], ad), "JPEG", quality=FOTO_KALITE, optimize=True)
            except (UnidentifiedImageError, OSError):
                uyarilar.append(f"{d.filename} okunamadı. JPG veya PNG fotoğraf yükleyin.")
                continue
            get_db().execute("INSERT INTO fotograf (kayit_id, dosya, yukleyen_id, olusturma) VALUES (?,?,?,?)",
                             (kayit_id, ad, g.kullanici["id"], zaman_damgasi()))
        get_db().commit()
        return uyarilar

    def foto_dosyasini_sil(dosya):
        yol = os.path.join(app.config["UPLOAD_FOLDER"], dosya)
        if os.path.exists(yol):
            os.remove(yol)

    # ================= Kayıtlar =================
    @app.route("/")
    @giris_gerekli
    def liste():
        db = get_db()
        kayitlar = db.execute("SELECT * FROM kayit ORDER BY tarih DESC LIMIT 60").fetchall()
        ids = [k["id"] for k in kayitlar]
        return render_template(
            "liste.html", kayitlar=kayitlar, adlar=kullanici_adlari(), fotolar=fotograflar(ids),
            imalat=imalatlar(ids), iscilik=iscilikler(ids),
            bugunku=db.execute("SELECT id FROM kayit WHERE tarih = ?", (bugun().isoformat(),)).fetchone(),
            onay_bekleyen=db.execute("SELECT COUNT(*) FROM kayit WHERE onayli = 0").fetchone()[0],
            kalem_sayisi=db.execute("SELECT COUNT(*) FROM butce_kalemi WHERE aktif = 1").fetchone()[0])

    @app.route("/yeni", methods=["GET", "POST"])
    @roller_gerekli("yonetici", "personel")
    def yeni():
        if request.method == "POST":
            baslik, satirlar, iscilik, hatalar = formu_oku()
            if not hatalar:
                mevcut = get_db().execute("SELECT id FROM kayit WHERE tarih = ?", (baslik["tarih"],)).fetchone()
                if mevcut:
                    flash(f"{baslik['tarih']} için kayıt zaten var. Bu kaydı düzenleyebilirsiniz.", "hata")
                    return redirect(url_for("duzenle", kayit_id=mevcut["id"]))
                cur = get_db().execute(
                    """INSERT INTO kayit (tarih, hava, sicaklik, makineler, notlar, olusturan_id, olusturma)
                       VALUES (:tarih, :hava, :sicaklik, :makineler, :notlar, :uid, :zaman)""",
                    {**baslik, "uid": g.kullanici["id"], "zaman": zaman_damgasi()})
                satirlari_kaydet(cur.lastrowid, satirlar, iscilik)
                get_db().commit()
                for u in fotograflari_kaydet(cur.lastrowid):
                    flash(u, "hata")
                flash(f"{baslik['tarih']} raporu kaydedildi.")
                return redirect(url_for("liste"))
            for h in hatalar:
                flash(h, "hata")
            return formu_goster("Günlük rapor", baslik, satirlar, iscilik, [])
        mevcut = get_db().execute("SELECT id, onayli FROM kayit WHERE tarih = ?", (bugun().isoformat(),)).fetchone()
        if mevcut and not mevcut["onayli"]:
            return redirect(url_for("duzenle", kayit_id=mevcut["id"]))
        bos = {"tarih": bugun().isoformat(), "hava": "", "sicaklik": None, "makineler": "", "notlar": ""}
        return formu_goster("Günlük rapor", bos, [], {}, [])

    @app.route("/kayit/<int:kayit_id>/duzenle", methods=["GET", "POST"])
    @roller_gerekli("yonetici", "personel")
    def duzenle(kayit_id):
        kayit = kaydi_getir(kayit_id)
        if kayit["onayli"]:
            flash("Bu kayıt onaylandığı için değiştirilemez.", "hata")
            return redirect(url_for("liste"))
        mevcut_fotolar = fotograflar([kayit_id]).get(kayit_id, [])
        if request.method == "POST":
            baslik, satirlar, iscilik, hatalar = formu_oku()
            if not hatalar and get_db().execute("SELECT id FROM kayit WHERE tarih = ? AND id != ?",
                                                (baslik["tarih"], kayit_id)).fetchone():
                hatalar.append("Bu tarih için başka bir kayıt var.")
            if not hatalar:
                get_db().execute(
                    """UPDATE kayit SET tarih=:tarih, hava=:hava, sicaklik=:sicaklik, makineler=:makineler,
                       notlar=:notlar, guncelleyen_id=:uid, guncelleme=:zaman WHERE id=:id""",
                    {**baslik, "uid": g.kullanici["id"], "zaman": zaman_damgasi(), "id": kayit_id})
                satirlari_kaydet(kayit_id, satirlar, iscilik)
                get_db().commit()
                for u in fotograflari_kaydet(kayit_id):
                    flash(u, "hata")
                flash(f"{baslik['tarih']} raporu güncellendi.")
                return redirect(url_for("liste"))
            for h in hatalar:
                flash(h, "hata")
            return formu_goster("Raporu düzenle", {**baslik, "id": kayit_id}, satirlar, iscilik, mevcut_fotolar)
        satirlar = [{"kod": s["kod"], "miktar": f"{s['miktar']:.3f}".rstrip("0").rstrip("."), "lokasyon": s["lokasyon"] or "",
                     "aciklama": s["aciklama"] or ""} for s in imalatlar([kayit_id]).get(kayit_id, [])]
        iscilik = {s["sinif_id"]: s["kisi"] for s in iscilikler([kayit_id]).get(kayit_id, [])}
        return formu_goster("Raporu düzenle", kayit, satirlar, iscilik, mevcut_fotolar)

    @app.route("/kayit/<int:kayit_id>/onay", methods=["POST"])
    @roller_gerekli("yonetici")
    def onay(kayit_id):
        kayit = kaydi_getir(kayit_id)
        if kayit["onayli"]:
            get_db().execute("UPDATE kayit SET onayli=0, onaylayan_id=NULL, onay_zamani=NULL WHERE id=?", (kayit_id,))
            flash(f"{kayit['tarih']} raporunun onayı kaldırıldı; artık düzenlenebilir.")
        else:
            get_db().execute("UPDATE kayit SET onayli=1, onaylayan_id=?, onay_zamani=? WHERE id=?",
                             (g.kullanici["id"], zaman_damgasi(), kayit_id))
            flash(f"{kayit['tarih']} raporu onaylandı.")
        get_db().commit()
        return redirect(guvenli_yonlendirme(request.form.get("sonra"), url_for("liste")))

    @app.route("/kayit/<int:kayit_id>/sil", methods=["POST"])
    @roller_gerekli("yonetici")
    def sil(kayit_id):
        kayit = kaydi_getir(kayit_id)
        for f in fotograflar([kayit_id]).get(kayit_id, []):
            foto_dosyasini_sil(f["dosya"])
        get_db().execute("DELETE FROM kayit WHERE id = ?", (kayit_id,))
        get_db().commit()
        flash(f"{kayit['tarih']} raporu silindi.")
        return redirect(url_for("liste"))

    # ================= Fotoğraflar =================
    @app.route("/foto/<int:foto_id>")
    @giris_gerekli
    def foto(foto_id):
        f = get_db().execute("SELECT dosya FROM fotograf WHERE id = ?", (foto_id,)).fetchone()
        if f is None:
            abort(404)
        return send_from_directory(app.config["UPLOAD_FOLDER"], f["dosya"], max_age=86400)

    @app.route("/foto/<int:foto_id>/sil", methods=["POST"])
    @roller_gerekli("yonetici", "personel")
    def foto_sil(foto_id):
        f = get_db().execute("""SELECT fotograf.*, kayit.onayli FROM fotograf
                                JOIN kayit ON kayit.id = fotograf.kayit_id WHERE fotograf.id = ?""",
                             (foto_id,)).fetchone()
        if f is None:
            abort(404)
        if f["onayli"] or not (rol("yonetici") or f["yukleyen_id"] == g.kullanici["id"]):
            abort(403)
        foto_dosyasini_sil(f["dosya"])
        get_db().execute("DELETE FROM fotograf WHERE id = ?", (foto_id,))
        get_db().commit()
        flash("Fotoğraf silindi.")
        return redirect(url_for("duzenle", kayit_id=f["kayit_id"]))

    # ================= Tarih aralığı yardımcıları =================
    def aralik():
        pazartesi = bugun() - timedelta(days=bugun().weekday())
        bas = request.args.get("bas") or pazartesi.isoformat()
        bit = request.args.get("bit") or (pazartesi + timedelta(days=6)).isoformat()
        try:
            if date.fromisoformat(bas) > date.fromisoformat(bit):
                bas, bit = bit, bas
        except ValueError:
            abort(400, "Tarih biçimi YYYY-AA-GG olmalı.")
        return bas, bit, request.args.get("onaysiz") == "1"

    def ilerleme_hesapla(bas, bit, onaysiz):
        db = get_db()
        hareket = {r["kalem_id"]: r for r in db.execute(
            """SELECT s.kalem_id,
                      SUM(CASE WHEN r.tarih >= :bas THEN s.miktar ELSE 0 END) AS donem,
                      SUM(s.miktar) AS kumulatif
               FROM imalat_satiri s JOIN kayit r ON r.id = s.kayit_id
               WHERE r.tarih <= :bit AND (r.onayli = 1 OR :onaysiz)
               GROUP BY s.kalem_id""", {"bas": bas, "bit": bit, "onaysiz": int(onaysiz)})}
        satirlar, toplam = [], {"butce_tutar": 0.0, "kazanilan": 0.0, "donem_tutar": 0.0, "fiyatli": False}
        for k in sorted(db.execute("SELECT * FROM butce_kalemi").fetchall(), key=lambda r: excel.kod_sirasi(r["kod"])):
            h = hareket.get(k["id"])
            if not k["aktif"] and not h and not (k["devir_miktar"] or 0):
                continue
            donem, kum = (h["donem"], h["kumulatif"]) if h else (0.0, 0.0)
            kum += k["devir_miktar"] or 0
            butce = k["butce_miktar"]
            oran = kum / butce if butce else None
            fiyat = k["birim_fiyat"]
            satir = {"k": k, "donem": donem, "kumulatif": kum, "oran": oran,
                     "kalan": (butce - kum) if butce is not None else None,
                     "asim": bool(butce) and kum > butce,
                     "butce_tutar": butce * fiyat if butce is not None and fiyat is not None else None,
                     "kazanilan": kum * fiyat if fiyat is not None else None}
            if satir["butce_tutar"] is not None:
                toplam["butce_tutar"] += satir["butce_tutar"]
                toplam["fiyatli"] = True
            if fiyat is not None:
                toplam["kazanilan"] += kum * fiyat
                toplam["donem_tutar"] += donem * fiyat
            satirlar.append(satir)
        toplam["oran"] = toplam["kazanilan"] / toplam["butce_tutar"] if toplam["butce_tutar"] else None
        return satirlar, toplam

    # ================= Rapor ve ilerleme =================
    @app.route("/rapor")
    @roller_gerekli("yonetici", "ofis")
    def rapor():
        bas, bit, onaysiz = aralik()
        kayitlar = get_db().execute("SELECT * FROM kayit WHERE tarih BETWEEN ? AND ? ORDER BY tarih",
                                    (bas, bit)).fetchall()
        ids = [k["id"] for k in kayitlar]
        isc = iscilikler(ids)
        ozet = {
            "gun": len(kayitlar),
            "adam_gun": sum(s["kisi"] for v in isc.values() for s in v),
            "onaysiz": sum(1 for k in kayitlar if not k["onayli"]),
            "hava": Counter(k["hava"] for k in kayitlar).most_common(),
        }
        sinif_toplam = Counter()
        for v in isc.values():
            for s in v:
                sinif_toplam[s["ad"]] += s["kisi"]
        return render_template("rapor.html", kayitlar=kayitlar, bas=bas, bit=bit, ozet=ozet,
                               adlar=kullanici_adlari(), fotolar=fotograflar(ids), imalat=imalatlar(ids),
                               iscilik=isc, sinif_toplam=sinif_toplam.most_common(),
                               foto_goster=request.args.get("foto") == "1")

    @app.route("/ilerleme")
    @roller_gerekli("yonetici", "ofis")
    def ilerleme():
        bas, bit, onaysiz = aralik()
        satirlar, toplam = ilerleme_hesapla(bas, bit, onaysiz)
        sadece_hareketli = request.args.get("hepsi") != "1"
        if sadece_hareketli:
            satirlar = [s for s in satirlar if s["kumulatif"]]
        return render_template("ilerleme.html", satirlar=satirlar, toplam=toplam, bas=bas, bit=bit,
                               onaysiz=onaysiz, sadece_hareketli=sadece_hareketli)

    @app.route("/disa-aktar")
    @roller_gerekli("yonetici", "ofis")
    def disa_aktar():
        bas, bit, onaysiz = aralik()
        db = get_db()
        adlar = kullanici_adlari()
        filtre = "r.tarih BETWEEN ? AND ? AND (r.onayli = 1 OR ?)"
        prm = (bas, bit, int(onaysiz))
        imalat = [(r["tarih"], r["kod"], r["tanim"], r["birim"], r["miktar"], r["lokasyon"], r["aciklama"],
                   r["aktivite_kodu"], r["birim_fiyat"],
                   r["miktar"] * r["birim_fiyat"] if r["birim_fiyat"] is not None else None,
                   adlar.get(r["olusturan_id"]), "Onaylı" if r["onayli"] else "Onaysız")
                  for r in db.execute(f"""SELECT r.tarih, r.onayli, r.olusturan_id, s.miktar, s.lokasyon, s.aciklama,
                                                 k.kod, k.tanim, k.birim, k.aktivite_kodu, k.birim_fiyat
                                          FROM imalat_satiri s JOIN kayit r ON r.id = s.kayit_id
                                          JOIN butce_kalemi k ON k.id = s.kalem_id
                                          WHERE {filtre} ORDER BY r.tarih, k.kod""", prm)]
        iscilik = [(r["tarih"], r["ad"], r["kisi"]) for r in db.execute(
            f"""SELECT r.tarih, c.ad, s.kisi FROM iscilik_satiri s JOIN kayit r ON r.id = s.kayit_id
                JOIN iscilik_sinifi c ON c.id = s.sinif_id WHERE {filtre} ORDER BY r.tarih, c.sira""", prm)]
        gunluk = [(r["tarih"], r["hava"], r["sicaklik"], r["toplam"], r["makineler"], r["notlar"],
                   adlar.get(r["olusturan_id"]), "Onaylı" if r["onayli"] else "Onaysız")
                  for r in db.execute(f"""SELECT r.*, (SELECT COALESCE(SUM(kisi),0) FROM iscilik_satiri
                                                       WHERE kayit_id = r.id) AS toplam
                                          FROM kayit r WHERE {filtre} ORDER BY r.tarih""", prm)]
        satirlar, toplam = ilerleme_hesapla(bas, bit, onaysiz)
        ilerleme_ = [(s["k"]["kod"], s["k"]["tanim"], s["k"]["birim"], s["k"]["aktivite_kodu"],
                      s["k"]["butce_miktar"], s["donem"], s["kumulatif"], s["oran"], s["kalan"],
                      s["k"]["birim_fiyat"], s["butce_tutar"], s["kazanilan"]) for s in satirlar]
        bilgi = [("Proje", app.config["PROJE_ADI"]), ("Dönem", f"{bas} – {bit}"),
                 ("Onaysız kayıtlar", "Dahil" if onaysiz else "Hariç (yalnızca onaylı veri)"),
                 ("Kümülatif", f"Projenin başından {bit} tarihine kadar"),
                 ("Oluşturan", g.kullanici["ad"]), ("Oluşturma zamanı", zaman_damgasi()),
                 ("Sayfalar", "Imalat: her imalat satırı · Iscilik: sınıf bazında kişi · "
                              "Ilerleme: bütçe kodu bazında özet · IsProgrami: aktivite bazında fiziksel % · "
                              "Gunluk: rapor başlıkları")]
        tarihler = {r["kalem_id"]: r for r in db.execute(
            """SELECT s.kalem_id, MIN(r.tarih) AS ilk, MAX(r.tarih) AS son FROM imalat_satiri s
               JOIN kayit r ON r.id = s.kayit_id WHERE r.tarih <= ? AND (r.onayli = 1 OR ?)
               GROUP BY s.kalem_id""", (bit, int(onaysiz)))}
        is_programi = []
        for k in sorted(db.execute("SELECT * FROM butce_kalemi").fetchall(), key=lambda r: excel.kod_sirasi(r["kod"])):
            s_ = next((x for x in satirlar if x["k"]["id"] == k["id"]), None)
            kum = s_["kumulatif"] if s_ else (k["devir_miktar"] or 0)
            t = tarihler.get(k["id"])
            if not k["aktif"] and not t and not (k["devir_miktar"] or 0):
                durum, oran = "Sahadan izlenmiyor", None
            elif not t and not (k["devir_miktar"] or 0):
                durum, oran = "Başlamadı", 0.0 if k["butce_miktar"] else None
            else:
                t = t or {"ilk": None, "son": None}
                oran = kum / k["butce_miktar"] if k["butce_miktar"] else None
                durum = "Bütçe miktarına ulaştı" if oran is not None and oran >= 1 else "Devam ediyor"
            is_programi.append((k["aktivite_kodu"] or k["kod"], k["tanim"], k["birim"], k["butce_miktar"], kum, oran,
                                t["ilk"] if t else None, t["son"] if t else None, durum))
        bilgi.append(("IsProgrami sayfası", "Fiziksel % sütunu 06B_IS_PROGRAMI'deki 'Fiziksel %' sütununa poz sırasıyla "
                                            "yapıştırılabilir. İlk imalat tarihi, gerçek başlangıç için öneridir."))
        dosya = excel.rapor_excel(bilgi, gunluk, imalat, iscilik, ilerleme_, is_programi)
        return send_file(dosya, as_attachment=True, download_name=f"santiye_{bas}_{bit}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ================= Bütçe kalemleri ve işçilik sınıfları =================
    @app.route("/kalemler", methods=["GET", "POST"])
    @roller_gerekli("yonetici", "ofis")
    def kalemler():
        if request.method == "POST":
            dosya = request.files.get("dosya")
            if not dosya or not dosya.filename:
                flash("Bir dosya seçin.", "hata")
                return redirect(url_for("kalemler"))
            try:
                okunan, hatalar, bilgi = excel.kalemleri_oku(dosya.filename, dosya.read())
            except Exception as e:   # bozuk veya desteklenmeyen dosya
                flash(f"Dosya okunamadı: {e}", "hata")
                return redirect(url_for("kalemler"))
            goturu_kapali = request.form.get("goturu_kapali") == "1"
            db = get_db()
            eklenen = guncellenen = kapali = 0
            for k in okunan:
                mevcut = db.execute("SELECT id, birim FROM butce_kalemi WHERE kod = ?", (k["kod"],)).fetchone()
                if mevcut is None:
                    aktif = 0 if goturu_kapali and excel._normal(k["birim"]) in excel.SAHAYA_KAPALI_BIRIMLER else 1
                    kapali += 1 - aktif
                    db.execute("""INSERT INTO butce_kalemi (kod, tanim, birim, butce_miktar, birim_fiyat, aktivite_kodu,
                                                            devir_miktar, aktif)
                                  VALUES (:kod, :tanim, :birim, :butce_miktar, :birim_fiyat, :aktivite_kodu,
                                          :devir_miktar, :aktif)""",
                               {**k, "aktif": aktif})
                    eklenen += 1
                    continue
                kullanimda = db.execute("SELECT 1 FROM imalat_satiri WHERE kalem_id = ? LIMIT 1",
                                        (mevcut["id"],)).fetchone()
                if kullanimda and mevcut["birim"] != k["birim"]:
                    hatalar.append(f"{k['kod']}: sahadan veri girilmiş bir kalemin birimi değiştirilemez "
                                   f"({mevcut['birim']} → {k['birim']}). Birim korunarak diğer alanlar güncellendi.")
                    k = {**k, "birim": mevcut["birim"]}
                devir_sql = ", devir_miktar=:devir_miktar" if bilgi.get("devir_var") else ""
                db.execute(f"""UPDATE butce_kalemi SET tanim=:tanim, birim=:birim, butce_miktar=:butce_miktar,
                               birim_fiyat=:birim_fiyat, aktivite_kodu=:aktivite_kodu{devir_sql} WHERE kod=:kod""", k)
                guncellenen += 1
            db.commit()
            if bilgi:
                ek = f" Okunan sayfa: {bilgi['sayfa']}; {bilgi['grup']} grup başlığı atlandı."
                if bilgi["aktivite_varsayilan"]:
                    ek += " Dosyada aktivite kodu sütunu olmadığı için aktivite kodu = bütçe kodu alındı."
                if kapali:
                    ek += f" {kapali} götürü/aylık kalem saha listesine kapalı eklendi."
                flash(ek.strip())
            flash(f"İçe aktarma tamamlandı: {eklenen} yeni kalem, {guncellenen} güncellenen kalem.")
            for h in hatalar[:30]:
                flash(h, "hata")
            if len(hatalar) > 30:
                flash(f"… ve {len(hatalar) - 30} uyarı daha.", "hata")
            return redirect(url_for("kalemler"))
        liste_ = sorted(get_db().execute("""SELECT k.*, (SELECT COUNT(*) FROM imalat_satiri s WHERE s.kalem_id = k.id)
                                            AS kullanim FROM butce_kalemi k""").fetchall(),
                        key=lambda k: excel.kod_sirasi(k["kod"]))
        siniflar = get_db().execute("SELECT * FROM iscilik_sinifi ORDER BY aktif DESC, sira, ad").fetchall()
        return render_template("kalemler.html", kalemler=liste_, siniflar=siniflar)

    @app.route("/kalemler/sablon.xlsx")
    @roller_gerekli("yonetici", "ofis")
    def kalem_sablonu():
        return send_file(excel.kalem_sablonu(), as_attachment=True, download_name="butce_kalemleri_sablon.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.route("/kalemler/<int:kid>/durum", methods=["POST"])
    @roller_gerekli("yonetici", "ofis")
    def kalem_durum(kid):
        get_db().execute("UPDATE butce_kalemi SET aktif = 1 - aktif WHERE id = ?", (kid,))
        get_db().commit()
        flash("Kalemin durumu değiştirildi. Pasif kalemler sahadaki listede görünmez; geçmiş verisi korunur.")
        return redirect(url_for("kalemler"))

    @app.route("/siniflar", methods=["POST"])
    @roller_gerekli("yonetici", "ofis")
    def sinif_ekle():
        ad = request.form.get("ad", "").strip()
        if not ad:
            flash("Sınıf adı boş olamaz.", "hata")
        else:
            try:
                sira = get_db().execute("SELECT COALESCE(MAX(sira), 0) + 1 FROM iscilik_sinifi").fetchone()[0]
                get_db().execute("INSERT INTO iscilik_sinifi (ad, sira) VALUES (?, ?)", (ad, sira))
                get_db().commit()
                flash(f"{ad} sınıfı eklendi.")
            except sqlite3.IntegrityError:
                flash("Bu sınıf zaten var.", "hata")
        return redirect(url_for("kalemler") + "#siniflar")

    @app.route("/siniflar/<int:sid>/durum", methods=["POST"])
    @roller_gerekli("yonetici", "ofis")
    def sinif_durum(sid):
        get_db().execute("UPDATE iscilik_sinifi SET aktif = 1 - aktif WHERE id = ?", (sid,))
        get_db().commit()
        flash("Sınıfın durumu değiştirildi.")
        return redirect(url_for("kalemler") + "#siniflar")

    # ================= Kullanıcı yönetimi =================
    @app.route("/kullanicilar", methods=["GET", "POST"])
    @roller_gerekli("yonetici")
    def kullanicilar():
        if request.method == "POST":
            hata = kullanici_olustur(request.form.get("kullanici_adi", "").strip().lower(),
                                     request.form.get("ad", "").strip(), request.form.get("sifre", ""),
                                     request.form.get("rol", "personel"))
            flash(hata, "hata") if hata else flash("Kullanıcı oluşturuldu. Kullanıcı adını ve şifreyi kişiye iletin.")
            return redirect(url_for("kullanicilar"))
        liste_ = get_db().execute("SELECT * FROM kullanici ORDER BY aktif DESC, ad").fetchall()
        return render_template("kullanicilar.html", kullanicilar=liste_)

    @app.route("/kullanicilar/<int:kid>/durum", methods=["POST"])
    @roller_gerekli("yonetici")
    def kullanici_durum(kid):
        if kid == g.kullanici["id"]:
            flash("Kendi hesabınızı pasifleştiremezsiniz.", "hata")
        else:
            get_db().execute("UPDATE kullanici SET aktif = 1 - aktif WHERE id = ?", (kid,))
            get_db().commit()
            flash("Kullanıcının durumu değiştirildi.")
        return redirect(url_for("kullanicilar"))

    @app.route("/kullanicilar/<int:kid>/sifre", methods=["POST"])
    @roller_gerekli("yonetici")
    def kullanici_sifre_sifirla(kid):
        yeni = request.form.get("sifre", "")
        if len(yeni) < 8:
            flash("Yeni şifre en az 8 karakter olmalı.", "hata")
        else:
            get_db().execute("UPDATE kullanici SET sifre_hash = ?, hatali_giris = 0, kilit_bitis = NULL WHERE id = ?",
                             (generate_password_hash(yeni), kid))
            get_db().commit()
            flash("Şifre sıfırlandı. Yeni şifreyi kişiye iletin.")
        return redirect(url_for("kullanicilar"))

    @app.errorhandler(413)
    def cok_buyuk(_e):
        flash("Yüklenen dosyalar çok büyük. Daha az fotoğrafla tekrar deneyin.", "hata")
        return redirect(request.referrer or url_for("liste"))

    return app


def sayi_bicim(deger, ondalik=2):
    """1234.5 -> '1.234,5' (Türkçe biçim, gereksiz sıfırlar atılır)."""
    if deger is None:
        return ""
    metin = f"{deger:,.{ondalik}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if "," in metin:
        metin = metin.rstrip("0").rstrip(",")
    return metin


def yuzde(oran):
    """0.584 -> '%58,4'"""
    if oran is None:
        return "–"
    return "%" + f"{oran * 100:.1f}".replace(".", ",")


def _gizli_anahtar(klasor):
    yol = os.path.join(klasor, "gizli_anahtar")
    if not os.path.exists(yol):
        with open(yol, "w") as f:
            f.write(secrets.token_hex(32))
    with open(yol) as f:
        return f.read().strip()


app = create_app() if os.environ.get("SANTIYE_TEST") != "1" else None

if __name__ == "__main__":
    app.run(debug=True, port=5050)
